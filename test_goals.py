"""Stage 3: goals, proposals and the heartbeat (goals.py, the store, her chat tools, the window). The model is a
scripted fake; the store is a temporary database."""
import time

import pytest

import agent
import app
import goals
import health
import store as store_mod
from agent import Veronica
from test_app import FakeUI
from test_veronica import FakeClaude, message, text, tool


@pytest.fixture
def db(tmp_path):
    s = store_mod.Store(tmp_path / 'v.db')
    yield s
    s.close()


def make(db, tmp_path, *script):
    return Veronica(client=FakeClaude(*script), projects_file=tmp_path / 'none.json', store=db)


def results(veronica):
    return [m['content'] for m in veronica.messages if m['role'] == 'tool']


# ----- the owner's words ------------------------------------------------------------------------------------------

@pytest.mark.parametrize('said, decision', [
    ('Approve proposal 3', ('proposal', 3, 'approve')),
    ('Veronica, approve proposal three.', ('proposal', 3, 'approve')),
    ('please decline goal 2', ('goal', 2, 'decline')),
    ('reject the idea number 4', ('proposal', 4, 'decline')),
    ('Pause goal 1.', ('goal', 1, 'pause')),
    ('resume goal one please', ('goal', 1, 'resume')),
    ('Drop goal 5', ('goal', 5, 'drop')),
    ('Goal 2 is done.', ('goal', 2, 'done')),
    ('cancel proposal 7', ('proposal', 7, 'drop')),
])
def test_the_owners_decisions_are_understood(said, decision):
    assert goals.parse_decision(said) == decision


@pytest.mark.parametrize('said', ['approve it', 'what is proposal 3 about?', 'approve proposal', 'approve proposal x',
                                  'I approve of proposal 3 in principle', 'send it', 'approve the email'])
def test_other_words_are_not_decisions(said):
    assert goals.parse_decision(said) is None


def test_a_new_goal_needs_its_separator():
    assert goals.parse_new_goal('New goal: learn my weekly routine') == 'learn my weekly routine'
    assert goals.parse_new_goal('Veronica, add a goal - keep my inbox under 50') == 'keep my inbox under 50'
    assert goals.parse_new_goal('add a goal to my calendar for Friday') is None
    assert goals.parse_new_goal('new goal: hi') is None


# ----- the store --------------------------------------------------------------------------------------------------

def test_her_goals_wait_and_the_owners_start_active(db):
    hers, new = db.add_goal('Keep my tests green', 'one a day')
    assert new and hers['status'] == 'proposed' and hers['origin'] == 'veronica'
    again, new = db.add_goal('keep my tests GREEN')
    assert not new and again['id'] == hers['id']
    his, _ = db.add_goal('Learn my routine', origin='owner')
    assert his['status'] == 'active' and his['decided_at']
    assert db.next_work() == ('goal', db.goal(his['id']))       # her proposed goal is not worked on


def test_approved_proposals_are_worked_on_before_goals(db):
    db.add_goal('Learn my routine', origin='owner')
    p, _ = db.add_proposal('fix', 'Fix the worker check', 'it fails')
    assert db.next_work()[0] == 'goal'                          # pending: not yet
    with pytest.raises(ValueError):
        db.add_proposal('rewrite', 'Something', '')
    goals.decide(db, 'proposal', p['id'], 'approve')
    kind, row = db.next_work()
    assert kind == 'proposal' and row['id'] == p['id']


def test_decisions_only_move_items_the_right_way(db):
    g, _ = db.add_goal('Tidy my notes')
    assert goals.decide(db, 'goal', g['id'], 'pause') == (False, f'Goal {g["id"]} is proposed, so it cannot be paused now.')
    assert goals.decide(db, 'goal', 99, 'approve') == (False, 'There is no goal 99.')
    assert goals.decide(db, 'proposal', 1, 'pause')[1] == "I can't pause a proposal."
    ok, said = goals.decide(db, 'goal', g['id'], 'approve')
    assert ok and said.startswith('Approved goal') and db.goal(g['id'])['status'] == 'active'
    assert goals.decide(db, 'goal', g['id'], 'approve')[0] is False
    assert goals.decide(db, 'goal', g['id'], 'done')[0] and db.goal(g['id'])['status'] == 'done'


# ----- in the chat ------------------------------------------------------------------------------------------------

def test_she_proposes_and_cannot_approve_or_work_on_her_own_proposal(db, tmp_path):
    veronica = make(db, tmp_path,
                    message(tool('propose_change', kind='improvement', title='Cache calendar lookups',
                                 detail='Calendar is fetched on every question.')),
                    message(tool('propose_goal', title='Keep my tests green', why='Fewer surprises.')),
                    message(tool('goal_progress', goal_id=1, note='started')),
                    message(tool('finish_proposal', proposal_id=1, succeeded=True, outcome='done')),
                    message(text('I have two ideas waiting for you.')))
    assert veronica.respond('any ideas?').say == 'I have two ideas waiting for you.'
    said = results(veronica)
    assert said[0].startswith('Proposed improvement 1') and 'approve proposal 1' in said[0]
    assert said[1].startswith('Proposed goal 1')
    assert 'only goals the owner approved' in said[2] and 'only approved proposals' in said[3]
    assert db.proposal(1)['status'] == 'pending' and db.goal(1)['status'] == 'proposed'
    assert not any(name.startswith(('approve', 'decide', 'decline')) for name in agent.TOOL_SPECS)


def test_the_owner_approves_in_his_own_words_without_the_model(db, tmp_path):
    db.add_proposal('fix', 'Fix the worker check', 'it fails')
    veronica = make(db, tmp_path)                                 # no scripted model replies: none are asked for
    reply = veronica.respond('Approve proposal 1.')
    assert reply.say.startswith('Approved proposal 1') and db.proposal(1)['status'] == 'approved'
    assert veronica.client.requests == [] and veronica.notes == [f'[Owner] {reply.say}']
    assert veronica.respond('New goal: learn my routine').say.startswith('New goal 1')
    assert db.goal(1)['status'] == 'active' and db.goal(1)['origin'] == 'owner'


def test_words_read_by_a_heartbeat_run_never_decide(db, tmp_path):
    db.add_proposal('fix', 'Fix the worker check', 'it fails')
    veronica = make(db, tmp_path, message(text('I cannot approve that myself.')))
    veronica.owner_present = False
    assert veronica.respond('approve proposal 1').say == 'I cannot approve that myself.'
    assert db.proposal(1)['status'] == 'pending'


def test_she_records_goal_progress_and_finishes_approved_work(db, tmp_path):
    db.add_goal('Learn my routine', origin='owner')
    p, _ = db.add_proposal('task', 'Sort the downloads folder', '')
    goals.decide(db, 'proposal', p['id'], 'approve')
    veronica = make(db, tmp_path,
                    message(tool('goal_progress', goal_id=1, note='read the calendar: gym Mon/Wed/Fri')),
                    message(tool('finish_proposal', proposal_id=p['id'], succeeded=True, outcome='sorted 41 files')),
                    message(tool('goal_progress', goal_id=1, note='routine written down', done=True)),
                    message(text('Done.')))
    veronica.respond('work')
    assert db.proposal(p['id'])['status'] == 'done' and db.proposal(p['id'])['outcome'] == 'sorted 41 files'
    goal = db.goal(1)
    assert goal['status'] == 'done' and [n['note'] for n in goal['notes']][0].startswith('read the calendar')


# ----- the heartbeat ----------------------------------------------------------------------------------------------

class Clock:
    """Starts at the real time (the store stamps rows with it) and is moved on by hand."""
    def __init__(self):
        self.now = time.time()

    def __call__(self):
        return self.now


def healthy():
    return health.Report([health.Check('code', True, 'every module imports')])


def beat_with(db, tmp_path, *script, health_check=healthy, clock=None, **kwargs):
    made, client = [], FakeClaude(*script)        # one scripted model, shared by every heartbeat run

    def make_agent():
        agent = Veronica(client=client, projects_file=tmp_path / 'none.json', store=db)
        made.append(agent)
        return agent
    beat = goals.Heartbeat(db, make_agent, config_file=tmp_path / 'heartbeat.json', health_check=health_check,
                           clock=clock or Clock(), **kwargs)
    return beat, made


def test_with_nothing_approved_a_heartbeat_does_no_work(db, tmp_path):
    db.add_goal('Her idea, not approved')
    beat, made = beat_with(db, tmp_path, message(text('Nothing to suggest today.')))
    result = beat.beat()
    assert result['ok'] and 'daily review: no new ideas' in result['summary'] and not result['notify']
    assert len(made) == 1 and made[0].owner_present is False and made[0].max_rounds == goals.REVIEW_ROUNDS
    assert db.goal(1)['beats'] == 0 and db.goal(1)['notes'] == []


def test_a_heartbeat_works_on_an_approved_goal_and_records_the_step(db, tmp_path):
    db.add_goal('Learn my routine', origin='owner')
    beat, made = beat_with(db, tmp_path,
                           message(tool('goal_progress', goal_id=1, note='Read this week: gym Mon/Wed/Fri 6am.')),
                           message(text('I read your calendar for the week.')))
    beat.set_config(review=False)
    result = beat.beat()
    assert result['notify'] and 'worked on goal 1' in result['summary']
    goal = db.goal(1)
    assert goal['beats'] == 1 and goal['notes'][-1]['note'] == 'Read this week: gym Mon/Wed/Fri 6am.'
    assert made[0].max_rounds == goals.WORK_ROUNDS and made[0].checkpoint_name == 'heartbeat'
    assert '[Heartbeat] Work towards your active goal 1' in made[0].messages[0]['content']
    assert db.heartbeats(kind='work')[0]['item'] == 'goal:1'


def test_her_words_are_kept_when_she_records_no_step_herself(db, tmp_path):
    db.add_goal('Learn my routine', origin='owner')
    beat, _ = beat_with(db, tmp_path, message(text('Looked at the calendar; nothing new.')))
    beat.set_config(review=False)
    beat.beat()
    assert db.goal(1)['notes'][-1]['note'] == 'Looked at the calendar; nothing new.'


def test_an_approved_proposal_gets_three_heartbeats_then_stops(db, tmp_path):
    p, _ = db.add_proposal('task', 'Sort the downloads folder', '')
    goals.decide(db, 'proposal', p['id'], 'approve')
    beat, _ = beat_with(db, tmp_path, *[message(text('Still sorting.'))] * goals.MAX_PROPOSAL_BEATS)
    beat.set_config(review=False)
    for _ in range(goals.MAX_PROPOSAL_BEATS):
        beat.beat()
    assert db.proposal(p['id'])['status'] == 'approved'
    assert 'not finished after' in beat.beat()['summary']
    assert db.proposal(p['id'])['status'] == 'failed'


def test_a_failing_health_check_becomes_a_proposed_fix_not_a_repair(db, tmp_path):
    broken = lambda: health.Report([health.Check('worker', False, '2 task(s) wait and no worker could start'),
                                    health.Check('keys', False, 'missing OPENROUTER_API_KEY')])
    beat, made = beat_with(db, tmp_path, health_check=broken)
    beat.set_config(review=False)
    result = beat.beat()
    assert not result['ok'] and result['notify'] and 'proposed fix 1 for my worker check' in result['summary']
    assert [p['title'] for p in db.proposals()] == ['Fix my health check: worker']   # no fix proposed for a key
    assert db.proposal(1)['status'] == 'pending' and made == []
    beat.beat()
    assert len(db.proposals()) == 1                               # not proposed twice
    goals.decide(db, 'proposal', 1, 'decline')
    beat.beat()
    assert len(db.proposals()) == 1                               # declined: quiet for a week


def test_an_open_repair_stops_other_work(db, tmp_path):
    db.add_goal('Learn my routine', origin='owner')
    open_repair = lambda: health.Report([health.Check('repairs', None, '1 left open: 20261014-010000')])
    beat, made = beat_with(db, tmp_path, health_check=open_repair)
    beat.set_config(review=False)
    assert 'left open' in beat.beat()['summary'] and made == [] and db.goal(1)['beats'] == 0


def test_the_daily_review_may_propose_only_two_and_runs_once_a_day(db, tmp_path):
    clock = Clock()
    beat, made = beat_with(db, tmp_path,
                           message(tool('propose_change', kind='fix', title='Idea one', detail='a')),
                           message(tool('propose_change', kind='task', title='Idea two', detail='b')),
                           message(tool('propose_goal', title='Idea three', why='c')),
                           message(text('Two ideas for you.')),
                           clock=clock)
    result = beat.beat()
    assert 'proposed 2 new idea(s)' in result['summary'] and result['notify']
    assert 'enough new ideas' in results(made[0])[2] and db.goals() == []
    clock.now += 3600
    assert 'review' not in beat.beat()['summary'] and len(made) == 1
    clock.now += 24 * 3600
    assert beat._review_due()


def test_the_heartbeat_is_due_by_its_settings(db, tmp_path):
    clock = Clock()
    beat, _ = beat_with(db, tmp_path, clock=clock)
    assert beat.config() == {'on': True, 'review': True, 'hands': True, 'minutes': 60} and beat.due()
    beat.set_config(review=False)
    beat.beat()
    assert not beat.due()
    clock.now += 61 * 60
    assert beat.due()
    beat.set_config(minutes=5)
    assert beat.config()['minutes'] == goals.MIN_MINUTES
    beat.set_config(on=False)
    assert not beat.due() and beat.next_at() is None


def test_a_heartbeat_that_errors_is_recorded_and_the_next_still_runs(db, tmp_path, monkeypatch):
    monkeypatch.setattr(agent.time, 'sleep', lambda seconds: None)     # her three provider retries, without waiting
    db.add_goal('Learn my routine', origin='owner')
    beat, _ = beat_with(db, tmp_path, *[RuntimeError('provider down')] * 3, message(text('Second time lucky.')))
    beat.set_config(review=False)
    assert 'could not work on goal 1' in beat.beat()['summary']
    assert db.heartbeats(kind='work')[0]['ok'] == 0
    assert 'worked on goal 1' in beat.beat()['summary']


# ----- the window -------------------------------------------------------------------------------------------------

def test_the_window_shows_and_decides(db, tmp_path):
    ui = FakeUI()
    veronica = make(db, tmp_path)
    session = app.Session(veronica, ui, voice=object(), transcriber=object(), microphone=object(),
                          settings_file=tmp_path / 's.json')
    session.heartbeat = goals.Heartbeat(db, lambda: None, config_file=tmp_path / 'hb.json')
    db.add_proposal('improvement', 'Cache calendar lookups', 'faster')
    bridge = app.Bridge(session)
    panel = bridge.goals()
    assert [r['title'] for r in panel['waiting']] == ['Cache calendar lookups'] and panel['heartbeat']['on']
    assert bridge.decide('proposal', '1', 'approve') is True and db.proposal(1)['status'] == 'approved'
    assert session._beat_now.is_set()                              # approved work starts soon
    assert bridge.decide('memory', 1, 'approve') is False and bridge.decide('goal', 'x', 'approve') is False
    assert bridge.add_goal('Learn my routine') == 1 and db.goal(1)['status'] == 'active'
    assert bridge.heartbeat_settings(None, 180, None)['minutes'] == 180
    notices = [args[0] for args in ui.named('notice')]
    assert notices[0].startswith('Approved proposal 1') and notices[-1].startswith('New goal 1')
