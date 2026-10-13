"""VERONICA's goals, proposals and heartbeat (stage 3; owner 2026-10-13: she proposes edits, improvements, fixes and
goals and works on them by herself, and every one needs his approval first).

- Goals: VERONICA proposes them (propose_goal); the owner adds his own ("new goal: ..."). Hers wait as 'proposed'
  until he approves; only approved ('active') goals are worked on.
- Proposals: a fix, an improvement or a task she suggests (propose_change). Each waits as 'pending'; once he approves
  it, the heartbeat works on it until she finishes it (finish_proposal) or MAX_PROPOSAL_BEATS heartbeats pass.
- The owner decides in the window (the Goals panel), or by typing or saying "approve proposal 3", "decline goal 2",
  "pause goal 2", "resume goal 2", "drop goal 2", "goal 2 is done". Her program reads these words before the model
  sees them: she has no tool that approves anything, so nothing she reads (an email, a web page) can approve for him.
- The heartbeat (while her window is open, every `minutes`; or `python goals.py beat`): her health check (a failing
  check becomes a proposed fix, never a repair she starts unasked), one step of approved work, and once a day a short
  review in which she may propose up to REFLECT_PROPOSALS new things. Each run is a fresh conversation with its own
  step budget (WORK_ROUNDS), apart from the owner's chat.

    python goals.py                      goals, proposals and the last heartbeats
    python goals.py beat                 one heartbeat now
    python goals.py approve proposal 3   (also decline / pause / resume / drop / done; goal or proposal)
    python goals.py add "Learn the new calendar API"
"""
import json
import re
import sys
import threading
import time

import paths
import safety

HERE = safety.HERE
CONFIG = paths.DATA / 'heartbeat.json'
DEFAULT_MINUTES = 60
MIN_MINUTES = 15
WORK_ROUNDS = 40          # model steps one heartbeat run may take (the owner's chat has no limit; this runs unattended)
REVIEW_ROUNDS = 15
REFLECT_HOURS = 24
REFLECT_PROPOSALS = 2
MAX_WAITING = 6           # no daily review while this many items already wait for the owner
MAX_PROPOSAL_BEATS = 3
DECLINED_QUIET_DAYS = 7   # a health fix the owner declined is not proposed again for a week

NUMBERS = {w: i for i, w in enumerate('zero one two three four five six seven eight nine ten eleven twelve thirteen '
                                      'fourteen fifteen sixteen seventeen eighteen nineteen twenty'.split())}
NUMBERS.update({'won': 1, 'to': 2, 'too': 2, 'for': 4, 'ate': 8})   # what Whisper sometimes hears
VERBS = {'approve': 'approve', 'accept': 'approve', 'decline': 'decline', 'reject': 'decline', 'refuse': 'decline',
         'deny': 'decline', 'pause': 'pause', 'resume': 'resume', 'unpause': 'resume', 'continue': 'resume',
         'drop': 'drop', 'cancel': 'drop', 'abandon': 'drop', 'remove': 'drop', 'delete': 'drop',
         'finish': 'done', 'complete': 'done', 'close': 'done'}
MOVES = {   # (kind, action) -> (new status, from which statuses)
    ('goal', 'approve'): ('active', ('proposed',)),
    ('goal', 'decline'): ('declined', ('proposed',)),
    ('goal', 'pause'): ('paused', ('active',)),
    ('goal', 'resume'): ('active', ('paused',)),
    ('goal', 'drop'): ('dropped', ('proposed', 'active', 'paused')),
    ('goal', 'done'): ('done', ('active', 'paused')),
    ('proposal', 'approve'): ('approved', ('pending',)),
    ('proposal', 'decline'): ('declined', ('pending',)),
    ('proposal', 'drop'): ('declined', ('pending', 'approved')),
}
SAID = {('goal', 'approve'): 'Approved goal {id}: {title}. I will work towards it at my heartbeats.',
        ('goal', 'decline'): 'Declined goal {id}: {title}. I will not work on it.',
        ('goal', 'pause'): 'Paused goal {id}: {title}.',
        ('goal', 'resume'): 'Goal {id} is active again: {title}.',
        ('goal', 'drop'): 'Dropped goal {id}: {title}.',
        ('goal', 'done'): 'Goal {id} is marked done: {title}.',
        ('proposal', 'approve'): 'Approved proposal {id}: {title}. I will work on it at my next heartbeat.',
        ('proposal', 'decline'): 'Declined proposal {id}: {title}. It will not be done.',
        ('proposal', 'drop'): 'Cancelled proposal {id}: {title}. It will not be done.'}
LEADING = ('hey veronica', 'veronica', 'please', 'okay', 'ok')
DECISION = re.compile(r'^(?P<verb>\w+) (?:the )?(?P<kind>goal|proposal|idea|suggestion) (?:number |no )?(?P<n>\w+)$')
DONE_FORM = re.compile(r'^(?:the )?goal (?:number |no )?(?P<n>\w+) is (?:done|finished|complete|completed)$')
# "New goal: ...", "Add a goal - ...": the separator is required, so "add a goal to my calendar" is not a goal
NEW_GOAL = re.compile(r"^\s*(?:hey\s+)?(?:veronica[,.!\s]+)?(?:please\s+)?(?:(?:add|set|create|make)\s+(?:a\s+(?:new\s+)?|"
                      r"new\s+)goal|new\s+goal)\s*[:,\-\u2013]\s*(?P<title>.+?)\s*$", re.I | re.S)


def _words(text):
    line = ' '.join(re.sub(r"[^\w\s]", ' ', str(text or '').lower()).split())
    changed = True
    while changed:
        changed = False
        for lead in LEADING:
            if line.startswith(lead + ' '):
                line, changed = line[len(lead) + 1:], True
        if line.endswith(' please'):
            line, changed = line[:-7], True
    return line


def _number(word):
    if word.isdigit():
        return int(word)
    return NUMBERS.get(word)


def parse_decision(text):
    """The owner's own words as (kind, id, action), e.g. "Approve proposal 3." -> ('proposal', 3, 'approve').
    None when the words are not a decision."""
    line = _words(text)
    match = DONE_FORM.match(line)
    if match and _number(match.group('n')) is not None:
        return 'goal', _number(match.group('n')), 'done'
    match = DECISION.match(line)
    if not match or match.group('verb') not in VERBS or _number(match.group('n')) is None:
        return None
    kind = 'goal' if match.group('kind') == 'goal' else 'proposal'
    return kind, _number(match.group('n')), VERBS[match.group('verb')]


def parse_new_goal(text):
    """The title from "New goal: ...", "Add a goal: ..." or "Set a new goal ...", else None."""
    match = NEW_GOAL.match(str(text or ''))
    if not match:
        return None
    title = match.group('title').strip().rstrip('.')
    return title if len(title) >= 3 else None


def decide(db, kind, item_id, action):
    """The owner's decision on a goal or proposal. (done, words for him). Only the owner's own actions call this."""
    if (kind, action) not in MOVES:
        return False, f"I can't {'mark done' if action == 'done' else action} a {kind}."
    item = db.goal(item_id) if kind == 'goal' else db.proposal(item_id)
    if item is None:
        return False, f'There is no {kind} {item_id}.'
    status, allowed = MOVES[(kind, action)]
    setter = db.set_goal_status if kind == 'goal' else db.set_proposal_status
    if setter(item_id, status, outcome='by the owner' if action in ('decline', 'drop') else None,
              allowed_from=allowed) is None:
        return False, f'{kind.capitalize()} {item_id} is {item["status"]}, so it cannot be {status} now.'
    return True, SAID[(kind, action)].format(id=item_id, title=item['title'])


def add_owner_goal(db, title, detail=''):
    goal, new = db.add_goal(title, detail, origin='owner')
    if not new:
        return goal, f'Goal {goal["id"]} ({goal["status"]}) is already that: {goal["title"]}.'
    return goal, f'New goal {goal["id"]}: {goal["title"]}. I will work towards it at my heartbeats.'


def waiting(db):
    """What waits for the owner's decision: goals she proposed and pending proposals."""
    return ([dict(g, type='goal') for g in db.goals(('proposed',))] +
            [dict(p, type='proposal') for p in db.proposals(('pending',))])


def describe_all(db):
    """Goals and proposals as text (for list_goals and the console)."""
    lines = []
    for goal in db.goals():
        if goal['status'] in ('declined', 'dropped'):
            continue
        lines.append(f'goal {goal["id"]}  {goal["status"].upper():<9} {goal["title"]}'
                     + (f'  (by the owner)' if goal['origin'] == 'owner' else ''))
        for note in (goal.get('notes') or [])[-3:]:
            lines.append(f'      {time.strftime("%d %b %H:%M", time.localtime(note["at"]))}  {note["note"][:200]}')
        if goal.get('outcome') and goal['status'] == 'done':
            lines.append(f'      outcome: {goal["outcome"][:200]}')
    for p in db.proposals():
        if p['status'] == 'declined':
            continue
        line = f'proposal {p["id"]}  {p["status"].upper():<9} {p["kind"]}: {p["title"]}'
        if p.get('goal_id'):
            line += f'  (for goal {p["goal_id"]})'
        lines.append(line)
        if p.get('outcome') and p['status'] in ('done', 'failed'):
            lines.append(f'      outcome: {p["outcome"][:200]}')
    return '\n'.join(lines) if lines else 'No goals or proposals yet.'


def panel(db, heartbeat=None):
    """Everything the window's Goals panel shows."""
    def goal_row(g):
        return {'type': 'goal', 'id': g['id'], 'title': g['title'], 'detail': g['detail'], 'status': g['status'],
                'origin': g['origin'], 'beats': g['beats'], 'outcome': g.get('outcome'),
                'notes': [{'at': n['at'], 'note': n['note']} for n in (g.get('notes') or [])[-3:]]}

    def proposal_row(p):
        return {'type': 'proposal', 'id': p['id'], 'kind': p['kind'], 'title': p['title'], 'detail': p['detail'],
                'status': p['status'], 'goal_id': p.get('goal_id'), 'beats': p['beats'], 'outcome': p.get('outcome')}

    finished = ([goal_row(g) for g in db.goals(('done', 'declined', 'dropped'))] +
                [proposal_row(p) for p in db.proposals(('done', 'failed', 'declined'))])
    finished.sort(key=lambda r: -r['id'])
    return {'waiting': [goal_row(g) for g in db.goals(('proposed',))] +
                       [proposal_row(p) for p in db.proposals(('pending',))],
            'goals': [goal_row(g) for g in db.goals(('active', 'paused'))],
            'work': [proposal_row(p) for p in db.proposals(('approved',))],
            'finished': finished[:12],
            'heartbeat': heartbeat.state() if heartbeat else None}


class Heartbeat:
    """VERONICA's own working time. `make_agent()` gives a fresh Veronica (agent.py) on the same store."""

    def __init__(self, db, make_agent, config_file=CONFIG, health_check=None, clock=time.time):
        self.db, self.make_agent, self.config_file, self.clock = db, make_agent, config_file, clock
        self.health_check = health_check
        self._lock = threading.Lock()
        self.running = False

    # ----- settings (data\heartbeat.json) ------------------------------------------------------------------------

    def config(self):
        data = {}
        try:
            data = json.loads(self.config_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        minutes = data.get('minutes') if isinstance(data, dict) else None
        return {'on': bool(data.get('on', True)) if isinstance(data, dict) else True,
                'review': bool(data.get('review', True)) if isinstance(data, dict) else True,
                'hands': bool(data.get('hands', True)) if isinstance(data, dict) else True,
                'minutes': max(MIN_MINUTES, int(minutes)) if isinstance(minutes, (int, float)) else DEFAULT_MINUTES}

    def set_config(self, on=None, minutes=None, review=None, hands=None):
        current = self.config()
        if on is not None:
            current['on'] = bool(on)
        if review is not None:
            current['review'] = bool(review)
        if hands is not None:
            current['hands'] = bool(hands)
        if minutes is not None:
            current['minutes'] = max(MIN_MINUTES, int(minutes))
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.config_file.write_text(json.dumps(current, indent=2), encoding='utf-8')
        return current

    # ----- when ----------------------------------------------------------------------------------------------

    def last(self, kind='beat'):
        rows = self.db.heartbeats(limit=1, kind=kind)
        return rows[0] if rows else None

    def next_at(self):
        config = self.config()
        if not config['on']:
            return None
        last = self.last()
        return (last['at'] + config['minutes'] * 60) if last else self.clock()

    def due(self):
        at = self.next_at()
        return at is not None and self.clock() >= at and not self.running

    def state(self):
        config, last = self.config(), self.last()
        return dict(config, running=self.running, next_at=self.next_at(),
                    last={'at': last['at'], 'summary': last['summary'], 'ok': bool(last['ok'])} if last else None,
                    recent=[{'at': r['at'], 'kind': r['kind'], 'item': r['item'], 'summary': r['summary'],
                             'ok': bool(r['ok'])} for r in self.db.heartbeats(limit=12) if r['kind'] != 'beat'])

    # ----- one heartbeat -------------------------------------------------------------------------------------

    def beat(self):
        """One heartbeat: health check, one step of approved work, the daily review when it is due.
        {'ok', 'summary', 'did', 'notify'}."""
        if not self._lock.acquire(blocking=False):
            return {'ok': False, 'summary': 'A heartbeat is already running.', 'did': [], 'notify': False}
        self.running = True
        try:
            did, problems, notify = [], [], False
            open_repair = False
            report = None
            if self.health_check is not None:
                try:
                    report = self.health_check()
                except Exception as error:
                    problems.append(f'the health check itself failed: {type(error).__name__}: {error}')
            if report is not None:
                self.db.add_heartbeat('health', None, report.summary(), report.ok)
                for check in report.checks:
                    if check.name == 'repairs' and check.ok is None:
                        open_repair = True
                    if check.ok is not False:
                        continue
                    if check.name == 'keys':    # only the owner can put a key in .env
                        problems.append(f'{check.name}: {check.detail}')
                        continue
                    proposed = self._propose_health_fix(check)
                    if proposed:
                        did.append(proposed)
                    problems.append(f'{check.name}: {check.detail}')
                notify = notify or bool(problems)
            if open_repair:
                did.append('a repair of my own code is left open, so I did no other work (finish or roll it back)')
            else:
                work = self.db.next_work()
                if work is not None:
                    did.append(self._work(*work))
                    notify = True
            if self._review_due():
                said = self._review()
                if said:
                    did.append(said)
                    notify = notify or 'proposed' in said
            summary = '; '.join(did) or 'Nothing approved to work on.'
            if problems:
                summary = 'Problems: ' + '; '.join(problems) + '. ' + summary
            self.db.add_heartbeat('beat', None, summary, not problems)
            return {'ok': not problems, 'summary': summary, 'did': did, 'notify': notify}
        finally:
            self.running = False
            self._lock.release()

    def _propose_health_fix(self, check):
        title = f'Fix my health check: {check.name}'
        quiet_since = self.clock() - DECLINED_QUIET_DAYS * 86400
        if any(p['title'].lower() == title.lower() and (p['decided_at'] or 0) > quiet_since
               for p in self.db.proposals(('declined',))):
            return None
        proposal, new = self.db.add_proposal('fix', title, f'My heartbeat health check failed: {check.name}: '
                                                           f'{check.detail}. I would find the cause and repair it '
                                                           'with my self-repair tools.')
        return f'proposed fix {proposal["id"]} for my {check.name} check' if new else None

    def _agent(self, rounds, proposals_left=None):
        agent = self.make_agent()
        agent.owner_present = False          # the owner's decision words are not read from a heartbeat run
        agent.max_rounds = rounds
        agent.proposals_left = proposals_left
        agent.checkpoint_name = 'heartbeat'
        agent.pc_autonomous = self.config()['hands']
        return agent

    def _work(self, kind, item):
        label = f'{kind} {item["id"]} ({item["title"][:80]})'
        if kind == 'proposal' and item['beats'] >= MAX_PROPOSAL_BEATS:
            self.db.set_proposal_status(item['id'], 'failed', f'Not finished after {MAX_PROPOSAL_BEATS} heartbeats.',
                                        allowed_from=('approved',))
            self.db.add_heartbeat('work', f'proposal:{item["id"]}', 'Stopped: not finished after '
                                  f'{MAX_PROPOSAL_BEATS} heartbeats.', False)
            return f'stopped {label}: not finished after {MAX_PROPOSAL_BEATS} heartbeats'
        self.db.worked_on(kind, item['id'])
        notes_before = len((self.db.goal(item['id']) or {}).get('notes') or []) if kind == 'goal' else 0
        try:
            reply = self._agent(WORK_ROUNDS).respond(work_prompt(kind, item))
            words, ok = reply.say, True
        except Exception as error:
            words, ok = f'{type(error).__name__}: {error}', False
        words = ' '.join(str(words).split())
        if kind == 'goal' and ok:
            goal = self.db.goal(item['id']) or {}
            if len(goal.get('notes') or []) == notes_before:     # she did not record the step herself
                self.db.note_goal(item['id'], words[:600])
        self.db.add_heartbeat('work', f'{kind}:{item["id"]}', words, ok)
        return f'worked on {label}: {words[:240]}' if ok else f'could not work on {label}: {words[:240]}'

    def _review_due(self):
        config = self.config()
        if not config['review'] or len(waiting(self.db)) >= MAX_WAITING:
            return False
        last = self.last('review')
        return last is None or self.clock() - last['at'] >= REFLECT_HOURS * 3600

    def _review(self):
        before = len(waiting(self.db))
        try:
            reply = self._agent(REVIEW_ROUNDS, proposals_left=REFLECT_PROPOSALS).respond(REVIEW_PROMPT)
            words, ok = ' '.join(reply.say.split()), True
        except Exception as error:
            words, ok = f'{type(error).__name__}: {error}', False
        new = len(waiting(self.db)) - before
        self.db.add_heartbeat('review', None, words, ok)
        if not ok:
            return f'the daily review failed: {words[:200]}'
        return f'daily review: proposed {new} new idea(s) for you to approve' if new > 0 else 'daily review: no new ideas'


def work_prompt(kind, item):
    if kind == 'proposal':
        return (f'[Heartbeat] The owner approved this {item["kind"]} (proposal {item["id"]}): {item["title"]}\n'
                f'Details: {item["detail"] or "(none)"}\n\n'
                'Work on it now, on your own. If it changes your own code, call start_self_repair first and '
                'finish_self_repair after. Prove each step with real output. When it is finished, call '
                f'finish_proposal(proposal_id={item["id"]}, succeeded=true, outcome=...); if it cannot be done, call '
                'finish_proposal with succeeded=false and say why. '
                f'This is heartbeat {item["beats"] + 1} of at most {MAX_PROPOSAL_BEATS} for it. '
                'End with one short paragraph for the owner: what you did and what is left.')
    notes = '\n'.join(f'- {n["note"]}' for n in (item.get('notes') or [])[-5:]) or '- (nothing yet)'
    return (f'[Heartbeat] Work towards your active goal {item["id"]}: {item["title"]}\n'
            f'Details: {item["detail"] or "(none)"}\nProgress so far:\n{notes}\n\n'
            'Take the next useful step, one you can prove with real output, and record it with '
            f'goal_progress(goal_id={item["id"]}, note=...). When the goal is fully reached, call goal_progress with '
            'done=true. A change to your own code, to JARVIS, or anything that spends money or writes to someone new '
            'is not done for a goal: propose it with propose_change and the owner decides. '
            'End with one short paragraph for the owner.')


REVIEW_PROMPT = (f'[Heartbeat, daily review] Look at how you are doing and what would help the owner: your health '
                 '(self_check), your goals and proposals (list_goals) and your recent repairs (list_self_repairs). '
                 f'You may suggest up to {REFLECT_PROPOSALS} new things: a goal (propose_goal) or a fix, improvement '
                 'or task (propose_change). Suggest only what is concrete and useful and not already listed. Do not '
                 'start any of it, send email or change files in this review: the owner approves first. '
                 'End with one short sentence for the owner.')


# ----- the console ---------------------------------------------------------------------------------------------------

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    import veronica
    veronica.load_env()
    import store as store_mod
    db = store_mod.Store()
    try:
        if not argv or argv[0] == 'list':
            print(describe_all(db))
            for row in db.heartbeats(limit=5):
                print(f'{time.strftime("%d %b %H:%M", time.localtime(row["at"]))}  {row["kind"]:<7} '
                      f'{"" if row["ok"] else "FAILED "}{row["summary"][:160]}')
            return 0
        if argv[0] == 'add' and len(argv) > 1:
            print(add_owner_goal(db, ' '.join(argv[1:]))[1])
            return 0
        if argv[0] == 'beat':
            import health
            from agent import Veronica
            beat = Heartbeat(db, lambda: Veronica(store=db, log=lambda line: print(line, flush=True)),
                             health_check=lambda: health.check(store=db))
            print(beat.beat()['summary'])
            return 0
        decision = parse_decision(' '.join(argv))
        if decision:
            print(decide(db, *decision)[1])
            return 0
        print(__doc__)
        return 2
    finally:
        db.close()


if __name__ == '__main__':
    sys.exit(main())
