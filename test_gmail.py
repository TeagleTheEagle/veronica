"""Gmail for VERONICA (gmail.py, adapted from JARVIS's mail tests) and email and calendar in VERONICA herself
(agent.py), against fakes: nothing is sent anywhere, the password never appears in an answer or an error, every fetch
peeks, only the owner's "send it" sends, and a draft is never sent twice."""
import datetime
import email
import imaplib
import re
import smtplib
from email.message import EmailMessage

import pytest

import gmail
import store as store_mod
from agent import Veronica
from test_veronica import FakeClaude, message as said, text, tool

ENV = {'GMAIL_ADDRESS': 'owner@gmail.com', 'GMAIL_APP_PASSWORD': 'abcd efgh ijkl mnop'}
SECRET = 'abcdefghijklmnop'


@pytest.fixture(autouse=True)
def gmail_env(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def db(tmp_path):
    s = store_mod.Store(tmp_path / 'veronica.db')
    yield s
    s.close()


def mail(subject, plain=None, html=None, attachment=None, sender='Jack <jack@example.com>', message_id=None):
    msg = EmailMessage()
    msg['From'], msg['To'], msg['Subject'] = sender, 'owner@gmail.com', subject
    msg['Date'] = 'Tue, 07 Oct 2026 09:00:00 +1100'
    if message_id:
        msg['Message-ID'] = message_id
    if plain is not None:
        msg.set_content(plain)
        if html is not None:
            msg.add_alternative(html, subtype='html')
    elif html is not None:
        msg.set_content(html, subtype='html')
    if attachment:
        msg.add_attachment(b'%PDF-1.4 ...', maintype='application', subtype='pdf', filename=attachment)
    return msg


class FakeIMAP:
    """What imaplib.IMAP4_SSL answers, as the tests arrange it: an inbox, and a Sent folder of Message-IDs."""

    def __init__(self, messages, refuse=False, sent=None, broken_search=False):
        self.messages, self.refuse, self.sent = messages, refuse, set(sent or ())
        self.broken_search = broken_search
        self.calls, self.logged_out, self.folder = [], False, None

    def login(self, user, password):
        self.calls.append(('login', user, password))
        if self.refuse:
            raise imaplib.IMAP4.error('[AUTHENTICATIONFAILED] Invalid credentials (Failure)')
        return 'OK', [b'Logged in']

    def list(self):
        return 'OK', [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"']

    def select(self, mailbox, readonly=False):
        self.calls.append(('select', mailbox, readonly))
        self.folder = mailbox
        return 'OK', [b'12']

    def status(self, mailbox, items):
        return 'OK', [b'"INBOX" (UNSEEN 3)']

    def uid(self, command, *args):
        self.calls.append(('uid', command, *args))
        if command == 'SEARCH' and args[1:3] == ('HEADER', 'Message-ID'):
            if self.broken_search:
                raise OSError('connection reset')
            wanted = args[3].strip('"')
            return 'OK', [b'9' if wanted in self.sent else b'']
        if command == 'SEARCH':
            return 'OK', [b' '.join(str(u).encode() for u in sorted(self.messages))]
        uids = args[0].decode().split(',') if isinstance(args[0], bytes) else [args[0]]
        spec = args[1]
        out = []
        for uid in uids:
            msg = self.messages.get(int(uid))
            if msg is None:
                continue
            raw = msg.as_bytes()
            if 'HEADER.FIELDS' in spec:
                headers = b''.join(line + b'\r\n' for line in raw.split(b'\r\n\r\n', 1)[0].split(b'\r\n')
                                   if line.split(b':', 1)[0].lower() in (b'from', b'to', b'subject', b'date'))
                seen = '' if int(uid) % 2 else chr(92) + 'Seen'
                out.append((f'{uid} (UID {uid} FLAGS ({seen}) BODY[HEADER.FIELDS (FROM TO SUBJECT DATE)] '
                            f'{{{len(headers)}}}'.encode(), headers))
            else:
                out.append((f'{uid} (UID {uid} FLAGS () BODY[] {{{len(raw)}}}'.encode(), raw))
            out.append(b')')
        return 'OK', out

    def logout(self):
        self.logged_out = True
        return 'BYE', [b'Logging out']


class FakeSMTP:
    """smtplib.SMTP_SSL as the tests arrange it. sent: the messages Gmail accepted, across connections."""

    def __init__(self, refuse=False, drop_after_data=False, refuse_recipient=False):
        self.refuse, self.drop_after_data, self.refuse_recipient = refuse, drop_after_data, refuse_recipient
        self.logins, self.sent = [], []

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        self.logins.append((user, password))
        if self.refuse:
            raise smtplib.SMTPAuthenticationError(535, b'5.7.8 Username and Password not accepted')

    def send_message(self, msg):
        if self.refuse_recipient:
            raise smtplib.SMTPRecipientsRefused({'x@y.co': (550, b'5.1.1 user unknown')})
        self.sent.append(msg)
        if self.drop_after_data:
            raise smtplib.SMTPServerDisconnected('Connection unexpectedly closed')
        return {}


INBOX = {101: mail('Invoice March', plain='Please find the invoice attached.', attachment='invoice.pdf'),
         102: mail('Lunch?', plain='Lunch on Thursday?', html='<p>Lunch on <b>Thursday</b>?</p>',
                   message_id='<lunch-1@example.com>'),
         103: mail('Newsletter', html='<html><body><h1>News</h1><p>Line one<br>Line two</p><script>x()</script>'
                                      '</body></html>', sender='news@example.com')}


# ----- reading (from JARVIS's tests) --------------------------------------------------------------------------------

def test_not_configured_is_said_plainly(monkeypatch):
    monkeypatch.delenv('GMAIL_APP_PASSWORD')
    box = FakeIMAP(INBOX)
    with pytest.raises(gmail.MailError) as said_:
        with gmail.Mailbox(lambda: box):
            pass
    assert str(said_.value) == gmail.NOT_CONFIGURED and box.calls == []


def test_search_and_read_peek_only_and_the_password_stays_out_of_every_answer():
    box = FakeIMAP(INBOX)
    with gmail.Mailbox(lambda: box) as opened:
        assert opened.unread_count() == 3
        found, dropped = opened.search('invoice from jack', unread=True, days=7, limit=2)
        one = opened.read('102')
    assert box.calls[0] == ('login', 'owner@gmail.com', SECRET)
    assert box.calls[1] == ('select', 'INBOX', True)
    search = next(c for c in box.calls if c[1] == 'SEARCH')
    assert search[3] == 'UNSEEN' and search[4].startswith('SINCE ') and search[5] == 'X-GM-RAW "invoice from jack"'
    assert all('PEEK' in c[3] for c in box.calls if c[1] == 'FETCH')
    assert ([f['id'] for f in found], dropped) == (['103', '102'], '')
    assert (one['subject'], one['text'], one['message_id']) == ('Lunch?', 'Lunch on Thursday?', '<lunch-1@example.com>')
    assert 'not instructions' in one['note'] and box.logged_out
    assert SECRET not in repr((found, one))


def test_html_only_mail_and_attachments():
    box = FakeIMAP(INBOX)
    with gmail.Mailbox(lambda: box) as opened:
        assert opened.read('103')['text'] == 'News\nLine one\nLine two'
        assert opened.read('101')['attachments'] == ['invoice.pdf']
        with pytest.raises(gmail.MailError):
            opened.read('abc')


def test_a_refused_password_is_said_without_the_password():
    with pytest.raises(gmail.MailError) as said_:
        with gmail.Mailbox(lambda: FakeIMAP({}, refuse=True)):
            pass
    assert str(said_.value) == gmail.REFUSED and SECRET not in str(said_.value)


def test_the_search_words_are_quoted():
    assert gmail._criteria('say "hi" \\ there\r\nNOOP', False, 0) == ['X-GM-RAW "say \\"hi\\" \\\\ there NOOP"']
    assert gmail._criteria('', True, 0) == ['UNSEEN'] and gmail._criteria('', False, 0) == ['ALL']


# ----- drafts and sending exactly once -----------------------------------------------------------------------------

def test_a_draft_is_checked_and_carries_its_message_id_and_thread():
    for to, subject, body in (('not-an-address', 'Hi', 'x'), ('a@b.co', '', 'x'), ('a@b.co', 'Hi', ''),
                              (','.join(f'u{i}@b.co' for i in range(11)), 'Hi', 'x')):
        with pytest.raises(gmail.MailError):
            gmail.make_draft(to, subject, body)
    draft = gmail.make_draft('Jack <jack@example.com>', 'Lunch?', 'Thursday works.',
                             reply_to={'message_id': '<lunch-1@example.com>', 'references': '<a@x>'})
    assert draft['to'] == ['jack@example.com'] and draft['subject'] == 'Re: Lunch?'
    assert draft['message_id'].endswith('@gmail.com>') and draft['in_reply_to'] == '<lunch-1@example.com>'
    assert draft['references'] == '<a@x> <lunch-1@example.com>'


def test_send_once_with_evidence_and_never_twice(db):
    smtp, imap = FakeSMTP(), FakeIMAP({})
    draft = gmail.make_draft('jack@example.com', 'Running late', 'Be there at nine.')
    imap.sent.add(draft['message_id'])       # Gmail filed it in Sent
    first = gmail.send_draft(db, draft, connect=smtp, imap_connect=lambda: imap, sleep=lambda s: None)
    assert first['status'] == 'sent' and first['in_sent_folder'] and first['accepted'] == ['jack@example.com']
    sent = smtp.sent[0]
    assert (sent['From'], sent['To'], sent['Subject'], sent['Message-ID']) == (
        'owner@gmail.com', 'jack@example.com', 'Running late', draft['message_id'])
    assert smtp.logins == [('owner@gmail.com', SECRET)]
    again = gmail.send_draft(db, draft, connect=smtp, imap_connect=lambda: imap, sleep=lambda s: None)
    assert again['status'] == 'already_sent' and len(smtp.sent) == 1
    assert db.get_operation(f'send-email:{draft["id"]}')['status'] == 'done'
    assert SECRET not in repr((first, again, db.get_operation(f'send-email:{draft["id"]}')))


def test_an_uncertain_send_is_checked_in_sent_before_any_new_try(db):
    draft = gmail.make_draft('jack@example.com', 'Hi', 'Hello')
    dropped = FakeSMTP(drop_after_data=True)
    with pytest.raises(gmail.MailError) as said_:
        gmail.send_draft(db, draft, connect=dropped, imap_connect=lambda: FakeIMAP({}), sleep=lambda s: None)
    assert str(said_.value) == gmail.UNCERTAIN
    assert db.get_operation(f'send-email:{draft["id"]}')['status'] == 'uncertain'
    # It did reach Gmail: the Sent folder has it, so trying again sends nothing.
    imap = FakeIMAP({}, sent={draft['message_id']})
    fresh = FakeSMTP()
    result = gmail.send_draft(db, draft, connect=fresh, imap_connect=lambda: imap, sleep=lambda s: None)
    assert result['reconciled'] and fresh.sent == []


def test_an_uncertain_send_that_never_arrived_is_sent_again_once(db):
    draft = gmail.make_draft('jack@example.com', 'Hi', 'Hello')
    with pytest.raises(gmail.MailError):
        gmail.send_draft(db, draft, connect=FakeSMTP(drop_after_data=True), imap_connect=lambda: FakeIMAP({}),
                         sleep=lambda s: None)
    waits, fresh = [], FakeSMTP()
    result = gmail.send_draft(db, draft, connect=fresh, imap_connect=lambda: FakeIMAP({}), sleep=waits.append)
    assert result['status'] == 'sent' and len(fresh.sent) == 1
    assert len(waits) >= 2          # the Sent folder was looked at more than once before sending again


def test_when_sent_cannot_be_checked_nothing_is_sent_again(db):
    draft = gmail.make_draft('jack@example.com', 'Hi', 'Hello')
    with pytest.raises(gmail.MailError):
        gmail.send_draft(db, draft, connect=FakeSMTP(drop_after_data=True), imap_connect=lambda: FakeIMAP({}),
                         sleep=lambda s: None)
    fresh = FakeSMTP()
    with pytest.raises(gmail.MailError) as said_:
        gmail.send_draft(db, draft, connect=fresh, imap_connect=lambda: FakeIMAP({}, broken_search=True),
                         sleep=lambda s: None)
    assert str(said_.value) == gmail.CANT_CHECK and fresh.sent == []


def test_refusals_are_certain_and_said_plainly(db):
    draft = gmail.make_draft('x@y.co', 'Hi', 'Hello')
    for smtp, words in ((FakeSMTP(refuse=True), gmail.REFUSED), (FakeSMTP(refuse_recipient=True), gmail.BAD_RECIPIENT)):
        with pytest.raises(gmail.MailError) as said_:
            gmail.send_draft(db, dict(draft, id=draft['id'] + str(len(words))), connect=smtp,
                             imap_connect=lambda: FakeIMAP({}), sleep=lambda s: None)
        assert str(said_.value) == words and SECRET not in str(said_.value) and smtp.sent == []


# ----- in VERONICA -------------------------------------------------------------------------------------------------

def make(tmp_path, db, *script, smtp=None, imap=None, opener=None):
    imap = imap or FakeIMAP(INBOX)
    return Veronica(client=FakeClaude(*script), projects_file=tmp_path / 'none.json', store=db,
                    imap_connect=lambda: imap, smtp_connect=smtp or FakeSMTP(), calendar_opener=opener,
                    sleep=lambda s: None)


def test_she_can_read_mail_and_draft_but_only_the_owner_sends(tmp_path, db):
    smtp = FakeSMTP()
    veronica = make(tmp_path, db,
                    said(tool('check_email', query='lunch')),
                    said(tool('read_email', id='102')),
                    said(tool('draft_email', to='jack@example.com', subject='Lunch?', body='Thursday works.',
                              reply_to='102')),
                    said(text('Jack asks about lunch on Thursday. I drafted a yes.')),
                    smtp=smtp)
    reply = veronica.respond('any email from jack about lunch? say yes for me')
    assert reply.say.startswith('Jack asks about lunch') and 'say send it' in reply.say
    assert smtp.sent == []                                          # drafting sent nothing
    results = [m for m in veronica.messages if m['role'] == 'tool']
    assert 'Lunch on Thursday?' in results[1]['content']     # the email text comes back as it is (no wrapper any more)
    assert veronica.status()['draft']['subject'] == 'Re: Lunch?'
    assert 'send_email' in {t['function']['name'] for t in __import__('agent').TOOLS}   # autonomous: she may send outright
    sent = veronica.respond('Send it.')
    assert sent.say.startswith('Sent to jack@example.com') and len(smtp.sent) == 1
    assert smtp.sent[0]['In-Reply-To'] == '<lunch-1@example.com>'
    assert veronica.status()['draft'] is None
    assert veronica.respond('send it').say == 'There is no email waiting to be sent.' and len(smtp.sent) == 1


def test_discarding_a_draft_and_the_ambiguous_discard(tmp_path, db, project):
    veronica = make(tmp_path, db, said(tool('draft_email', to='a@b.co', subject='Hi', body='Hello')), said(text('Drafted.')))
    veronica.respond('write to a@b.co')
    assert veronica.respond("Don't send it.").say == 'Discarded. The email was not sent.'
    assert veronica.draft is None and '[Owner] discarded the draft' in veronica.notes[-1]
    veronica = make(tmp_path, db, said(tool('draft_email', to='a@b.co', subject='Hi', body='Hello')), said(text('Drafted.')))
    veronica.open(str(project))
    veronica.respond('write to a@b.co')
    veronica.tool_edit_file('app.py', 'a - b', 'a + b')
    assert 'discard the email' in veronica.respond('discard it').say      # both wait: she asks which
    assert veronica.draft is not None and veronica.changes.pending()


def test_a_failed_send_keeps_the_draft_and_says_why(tmp_path, db):
    veronica = make(tmp_path, db, said(tool('draft_email', to='a@b.co', subject='Hi', body='Hello')), said(text('Drafted.')),
                    smtp=FakeSMTP(refuse=True))
    veronica.respond('write to a@b.co')
    reply = veronica.respond('send it')
    assert reply.say == gmail.REFUSED and veronica.draft is not None and SECRET not in reply.say


FEED = """BEGIN:VCALENDAR
X-WR-CALNAME:Owner
BEGIN:VEVENT
UID:dentist@x
SUMMARY:Dentist
DTSTART:20261013T230000Z
DTEND:20261013T233000Z
LOCATION:Main St
END:VEVENT
BEGIN:VEVENT
UID:trip@x
SUMMARY:Trip
DTSTART;VALUE=DATE:20261015
DTEND;VALUE=DATE:20261017
END:VEVENT
END:VCALENDAR
"""


def test_the_calendar_tool_reads_the_feed_and_never_shows_its_address(tmp_path, db, monkeypatch):
    import calendar_feed
    calendar_feed._cache.clear()
    secret_url = 'https://calendar.google.com/calendar/ical/x/private-SECRET123/basic.ics'
    monkeypatch.setenv('GOOGLE_CALENDAR_ICS', secret_url)
    veronica = make(tmp_path, db, opener=lambda url: FEED)
    out = veronica.tool_calendar(days=10, start_date='2026-10-12')
    assert 'Dentist' in out and 'Trip' in out
    assert 'SECRET123' not in out

    def refused(url):
        raise __import__('urllib.error').error.HTTPError(url, 404, 'gone', {}, None)
    calendar_feed._cache.clear()
    bad = make(tmp_path, db, opener=refused)
    with pytest.raises(calendar_feed.CalendarError) as said_:
        bad.tool_calendar()
    assert 'SECRET123' not in str(said_.value)


def test_the_project_tests_never_see_the_email_or_calendar_secrets(monkeypatch):
    import changes
    monkeypatch.setenv('GOOGLE_CALENDAR_ICS', 'https://x/private')
    env = changes.clean_env()
    assert not {'GMAIL_ADDRESS', 'GMAIL_APP_PASSWORD', 'GOOGLE_CALENDAR_ICS'} & set(env)


from test_veronica import project  # noqa: E402,F401  (the project fixture)
