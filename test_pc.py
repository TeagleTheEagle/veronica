"""Stage 4: VERONICA's hands on the PC (pc.py). The real PC is never touched: every test runs against pc.Fake."""
import pytest

import app
import goals
import pc
import store as store_mod
from agent import Veronica
from test_app import FakeUI
from test_veronica import FakeClaude, message, text, tool

SHOT = b'\x89PNG\r\n\x1a\n' + b'0' * 40


@pytest.fixture
def db(tmp_path):
    s = store_mod.Store(tmp_path / 'v.db')
    yield s
    s.close()


def make(db, tmp_path, *script, windows=()):
    veronica = Veronica(client=FakeClaude(*script), projects_file=tmp_path / 'none.json', store=db)
    veronica.pc_backend = pc.Fake(windows=[{'handle': 7, 'title': 'Notepad - shopping list'}], shot=SHOT)
    return veronica


def results(veronica):
    return [m['content'] for m in veronica.messages if m['role'] == 'tool']


# ----- the actions themselves ------------------------------------------------------------------------------------

def test_every_action_runs_on_the_backend():
    fake = pc.Fake(windows=[{'handle': 7, 'title': 'Notepad - shopping list'}])
    assert pc.run('click', {'x': 10, 'y': 20}, fake) == 'Clicked left at (10, 20).'
    assert pc.run('click', {'x': 1, 'y': 2, 'button': 'right'}, fake).startswith('Clicked right')
    assert pc.run('type_text', {'text': 'hello'}, fake) == 'Typed 5 characters.'
    assert pc.run('press_keys', {'keys': 'ctrl+s'}, fake) == 'Pressed ctrl+s.'
    assert pc.run('scroll', {'direction': 'up'}, fake) == 'Scrolled up 3 notch(es).'
    assert pc.run('activate_window', {'title': 'notepad'}, fake).endswith('Notepad - shopping list.')
    assert pc.run('minimize_window', {'title': 'shop'}, fake).startswith('Minimized')
    assert pc.run('close_window', {'title': 'shop'}, fake).startswith('Asked to close')
    assert pc.run('open_thing', {'path_or_url': 'https://example.com'}, fake) == 'Opened https://example.com.'
    assert pc.run('set_volume', {'action': 'mute'}, fake) == 'Volume: mute.'
    assert pc.run('media_key', {'action': 'play_pause'}, fake) == 'Media: play_pause.'
    assert pc.run('lock_screen', {}, fake) == 'The PC is locked.'
    assert pc.run('notify_owner', {'text': 'tea is ready'}, fake) == 'Shown to the owner.'
    assert 'Notepad' in pc.run('list_windows', {}, fake)
    kinds = [c[0] for c in fake.calls]
    assert kinds == ['click', 'click', 'type', 'press', 'scroll', 'activate', 'minimize', 'close', 'open', 'key',
                     'key', 'lock', 'notify']


def test_bad_actions_are_refused_before_touching_anything():
    fake = pc.Fake(windows=[{'handle': 7, 'title': 'Notepad'}])
    for name, given in [('click', {'x': 1, 'y': 2, 'button': 'middle'}), ('press_keys', {'keys': 'ctrl+nope'}),
                        ('scroll', {'direction': 'sideways'}), ('activate_window', {'title': 'missing'}),
                        ('set_volume', {'action': 'louder'}), ('media_key', {'action': 'rewind'}),
                        ('open_thing', {'path_or_url': 'a\nb'}), ('nope', {})]:
        with pytest.raises(pc.HandsError):
            pc.run(name, given, fake)
    assert fake.calls == []


# ----- the owner's yes before anything changes -------------------------------------------------------------------

def test_the_owner_is_asked_before_anything_changes(db, tmp_path):
    veronica = make(db, tmp_path, message(tool('click', x=10, y=20)), message(text('Shall I click it?')))
    assert veronica.respond('click the save button').say == 'Shall I click it?'
    assert results(veronica)[0].startswith('Waiting for your approval: click left at (10, 20)')
    assert veronica.pc_backend.calls == [] and veronica.pending_pc['name'] == 'click'
    reply = veronica.respond('yes')
    assert reply.say == 'Done: Clicked left at (10, 20).' and veronica.pc_backend.calls == [('click', 10, 20, 'left')]
    assert veronica.pending_pc is None and veronica.store.heartbeats(kind='pc')[0]['item'] == 'click'


def test_no_means_not_done(db, tmp_path):
    veronica = make(db, tmp_path, message(tool('type_text', text='rm -rf /')), message(text('Shall I type it?')))
    veronica.respond('type that')
    assert veronica.respond('no').say.startswith('Not done') and veronica.pc_backend.calls == []
    assert veronica.pending_pc is None


def test_looking_needs_no_yes(db, tmp_path):
    veronica = make(db, tmp_path, message(tool('list_windows')), message(tool('look_at_screen', question='what is open?')),
                    message(text('Notepad with a shopping list.')), message(text('Notepad is open.')))
    reply = veronica.respond('what is on my screen?')
    assert reply.say == 'Notepad is open.'
    assert results(veronica)[0].startswith('7  Notepad')
    vision = [r for r in veronica.client.requests if isinstance(r['messages'][0]['content'], list)][0]
    picture = vision['messages'][0]['content'][1]
    assert picture['image_url']['url'].startswith('data:image/png;base64,') and veronica.pending_pc is None


def test_looking_and_notifying_need_no_yes(db, tmp_path):
    veronica = make(db, tmp_path, message(tool('notify_owner', text='tea is ready')), message(text('Told you.')))
    assert veronica.respond('tell me about the tea').say == 'Told you.'
    assert veronica.pc_backend.calls == [('notify', 'tea is ready')] and veronica.pending_pc is None


def test_hands_off_stops_her_until_she_is_allowed_again(db, tmp_path):
    veronica = make(db, tmp_path, message(tool('click', x=1, y=2)), message(text('Shall I?')),
                    message(tool('click', x=1, y=2)), message(text('Shall I?')))
    assert veronica.respond('stop her hands').say.startswith('My hands are off')
    veronica.respond('click there')
    assert 'hands are off' in results(veronica)[0] and veronica.pc_backend.calls == []
    assert veronica.respond('hands on').say.startswith('My hands are on again')
    veronica.respond('click there')
    assert veronica.pending_pc is not None


# ----- while the owner is away -----------------------------------------------------------------------------------

def test_a_heartbeat_uses_the_hands_for_approved_work_and_records_it(db, tmp_path):
    veronica = make(db, tmp_path, message(tool('click', x=5, y=6)), message(text('Clicked it.')))
    veronica.owner_present, veronica.pc_autonomous = False, True
    assert veronica.respond('click it').say == 'Clicked it.'
    assert veronica.pc_backend.calls == [('click', 5, 6, 'left')]
    assert veronica.store.heartbeats(kind='pc')[0]['summary'] == 'click left at (5, 6)'


def test_the_hands_switch_for_work_while_away(db, tmp_path):
    veronica = make(db, tmp_path, message(tool('click', x=5, y=6)), message(text('Clicked it.')))
    veronica.owner_present, veronica.pc_autonomous = False, False
    veronica.respond('click it')
    assert 'switched my hands off for work while he is away' in results(veronica)[0]
    assert veronica.pc_backend.calls == []


def test_the_heartbeat_switch_reaches_the_agent(db, tmp_path):
    beat = goals.Heartbeat(db, lambda: None, config_file=tmp_path / 'hb.json')
    assert beat.config()['hands'] is True
    beat.set_config(hands=False)
    agent = Veronica(client=FakeClaude(), projects_file=tmp_path / 'none.json', store=db)
    beat.make_agent = lambda: agent
    beat._agent(goals.WORK_ROUNDS)
    assert agent.pc_autonomous is False and agent.owner_present is False


# ----- the window ------------------------------------------------------------------------------------------------

def test_the_window_shows_the_waiting_action_and_allows_or_denies(db, tmp_path):
    ui = FakeUI()
    veronica = make(db, tmp_path, message(tool('click', x=10, y=20)), message(text('Shall I?')),
                    message(tool('click', x=1, y=1)), message(text('Shall I again?')))
    session = app.Session(veronica, ui, voice=object(), transcriber=object(), microphone=object(),
                          settings_file=tmp_path / 's.json')
    bridge = app.Bridge(session)
    veronica.respond('click it')
    status = veronica.status()
    assert status['pending_pc']['words'] == 'click left at (10, 20)' and status['hands_on'] is True
    assert bridge.hands(False) is False and veronica.status()['hands_on'] is False
    bridge.hands(True)
    assert bridge.allow_pc() is True and veronica.pc_backend.calls == [('click', 10, 20, 'left')]
    assert ui.named('message')[-1][1] == 'Done: Clicked left at (10, 20).'
    veronica.respond('click the other one')
    assert bridge.deny_pc() is True and len(veronica.pc_backend.calls) == 1
    assert ui.named('message')[-1][1].startswith('Not done')
