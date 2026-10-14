"""The research workflow on Deep Agents, end to end with a scripted model and fake web (no network, no keys):
the whole workflow, restart recovery across a real process crash, tool failure, duplicate-action prevention,
success verification, cancellation and the limits."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

import research
import store as store_mod
import tasks
import web
from research_fakes import BAD, BODY, GOOD, Effects, ScriptedModel, call, script
from langchain_core.messages import AIMessage

HERE = Path(__file__).resolve().parent


@pytest.fixture
def place(tmp_path):
    db = store_mod.Store(tmp_path / 'veronica.db')
    saver = research.checkpointer(tmp_path / 'checkpoints.db')
    yield {'db': db, 'saver': saver, 'reports': tmp_path / 'reports', 'log': tmp_path / 'effects.log', 'tmp': tmp_path}
    db.close()


def run(place, model, effects=None, task=None):
    db = place['db']
    effects = effects or Effects(place['log'])
    task = task or tasks.start_research(db, 'heat pumps in cold climates')
    claimed = db.claim_task('test-worker')
    assert claimed['id'] == task['id']
    return tasks.run_task(db, claimed, 'test-worker', saver=place['saver'], model=model, search=effects.search,
                          fetch=effects.fetch, reports_dir=place['reports']), effects


# ----- the whole workflow ------------------------------------------------------------------------------------------

def test_research_to_a_verified_saved_report(place):
    model = ScriptedModel(script=script(bad_first=True))
    final, effects = run(place, model)
    assert final['status'] == 'succeeded', final['error']
    report = Path(final['result']['report'])
    text = report.read_text(encoding='utf-8')
    assert text.startswith('# Heat pumps') and '## Sources' in text
    assert '[1] Heat pumps explained — https://a.example/heat' in text and 'does not prove that the claims' in text
    assert final['result']['quotes_checked'] == 2 and final['result']['sources_cited'] == [1, 2]
    assert final['result']['summary'].startswith('Heat pumps work well')
    events = [e['message'] for e in place['db'].events(final['id'])]
    assert any('Citation check found' in e for e in events)          # the first report was sent back...
    assert any(e.startswith('Saved the report') for e in events)     # ...fixed, and saved
    progress = final['progress']
    assert progress['model_calls'] == 7 and progress['todos'][0]['content'] == 'Search for sources'
    assert final['cost_usd'] == pytest.approx(7 * (1000 * 4e-6 + 200 * 20e-6))
    assert final['tokens_in'] == 7000 and final['tokens_out'] == 1400


def test_one_agent_only_and_exactly_these_tools(place):
    model = ScriptedModel(script=script(bad_first=False))
    run(place, model)
    offered = set(model.tools_seen[0])
    assert offered == {'write_todos', 'ls', 'read_file', 'write_file', 'web_search', 'fetch_source', 'read_source',
                       'submit_report'}
    assert 'task' not in offered and 'execute' not in offered      # no sub-agents, no shell


# ----- restart recovery --------------------------------------------------------------------------------------------

def test_a_task_resumes_after_the_worker_process_dies(place):
    db, tmp = place['db'], place['tmp']
    task = tasks.start_research(db, 'heat pumps in cold climates')
    crashed = subprocess.run([sys.executable, str(HERE / 'research_fakes.py'), 'crash', str(tmp / 'veronica.db'),
                              str(tmp / 'checkpoints.db'), str(place['reports']), str(place['log'])],
                             cwd=str(HERE), capture_output=True, text=True, timeout=180,
                             env=dict(os.environ, LANGSMITH_TRACING='false'))
    assert crashed.returncode == 3, crashed.stderr[-2000:]
    effects = Effects(place['log'])
    assert effects.lines() == ['search heat pumps cold climate', 'fetch https://a.example/heat',
                               'fetch https://b.example/cold', 'crashed']
    assert db.get_task(task['id'])['status'] == 'running'           # the dead worker still holds its lease...
    with db._tx() as conn:                                            # ...until it runs out
        conn.execute('UPDATE tasks SET lease_until = 0 WHERE id = ?', (task['id'],))

    model = ScriptedModel(script=script())
    ran = tasks.worker(db=db, once=True, saver=research.checkpointer(tmp / 'checkpoints.db'), model=model,
                       search=effects.search, fetch=effects.fetch, reports_dir=place['reports'])
    final = db.get_task(task['id'])
    assert ran == 1 and final['status'] == 'succeeded', final['error']
    assert final['attempts'] == 2
    assert min(model.steps) == 4          # the model calls that finished before the crash were not made again
    lines = effects.lines()
    assert lines.count('search heat pumps cold climate') == 1          # finished tool calls were not repeated
    assert lines.count('fetch https://a.example/heat') == 1
    assert lines.count('fetch https://b.example/cold') == 2            # the one cut off by the crash ran again
    assert any(e['message'] == 'Resuming from where it stopped' for e in db.events(task['id']))
    assert len(list(place['reports'].glob('*.md'))) == 1


# ----- tool failure and replanning ---------------------------------------------------------------------------------

def test_a_failing_page_goes_back_to_the_agent_which_tries_another(place):
    steps = script(bad_first=False)
    steps.insert(2, call('fetch_source', 9, url='https://down.example/page'))
    effects = Effects(place['log'], fail={'https://down.example/page': web.WebError('The server answered 503.')})
    model = ScriptedModel(script=steps)
    final, _ = run(place, model, effects)
    assert final['status'] == 'succeeded', final['error']
    sources = place['db'].sources(final['id'])
    assert [s['url'] for s in sources] == ['https://a.example/heat', 'https://b.example/cold']


def test_an_unexpected_tool_crash_fails_the_task_with_its_reason(place):
    effects = Effects(place['log'], fail={'https://a.example/heat': RuntimeError('parser exploded')})
    final, _ = run(place, ScriptedModel(script=script()), effects)
    assert final['status'] == 'failed' and 'parser exploded' in final['error']


def test_a_transient_model_failure_puts_the_task_back_to_resume(place):
    steps = script(bad_first=False)
    steps[2] = ConnectionError('network down')
    final, effects = run(place, ScriptedModel(script=steps))
    assert final['status'] == 'queued' and 'tried again' in final['error']
    steps[2] = call('fetch_source', 2, url='https://a.example/heat')
    claimed = place['db'].claim_task('test-worker')
    final = tasks.run_task(place['db'], claimed, 'test-worker', saver=place['saver'],
                           model=ScriptedModel(script=steps), search=effects.search, fetch=effects.fetch,
                           reports_dir=place['reports'])
    assert final['status'] == 'succeeded' and final['attempts'] == 2
    assert effects.lines().count('search heat pumps cold climate') == 1


# ----- success is decided from evidence ------------------------------------------------------------------------------

def test_saying_it_is_done_without_a_verified_report_is_a_failure(place):
    steps = script(bad_first=False)
    steps[4:] = [AIMessage(content='Done! I saved a great report.')]   # never submitted
    final, _ = run(place, ScriptedModel(script=steps))
    assert final['status'] == 'failed' and 'without a saved report' in final['error']
    assert final['result']['summary'] == 'Done! I saved a great report.'
    assert not place['reports'].exists() or not list(place['reports'].glob('*.md'))


def test_a_report_that_never_passes_the_check_is_never_saved(place):
    steps = script(bad_first=True)
    steps[5] = call('submit_report', 5, title='Heat pumps', report_markdown=BODY, citations_list=BAD)
    final, _ = run(place, ScriptedModel(script=steps))
    assert final['status'] == 'failed'
    assert place['db'].get_operation(f'save-report:{final["id"]}') is None


def test_a_report_changed_on_disk_after_saving_does_not_count(place):
    final, _ = run(place, ScriptedModel(script=script(bad_first=False)))
    Path(final['result']['report']).write_text('tampered', encoding='utf-8')
    assert research.evidence(place['db'], final) is None


# ----- no duplicate actions ----------------------------------------------------------------------------------------

def test_submitting_twice_saves_once_and_starting_twice_makes_one_task(place):
    steps = script(bad_first=False)
    steps.insert(6, call('submit_report', 6, title='Heat pumps', report_markdown=BODY, citations_list=GOOD))
    final, _ = run(place, ScriptedModel(script=steps))
    assert final['status'] == 'succeeded' and len(list(place['reports'].glob('*.md'))) == 1
    db = place['db']
    first = tasks.start_research(db, 'solar panels', request_id='window-click-7')
    again = tasks.start_research(db, 'solar panels', request_id='window-click-7')
    assert first['id'] == again['id']


# ----- cancel and limits -------------------------------------------------------------------------------------------

def test_cancelling_a_running_task_stops_it_at_its_next_step(place):
    db = place['db']
    task = tasks.start_research(db, 'heat pumps')
    claimed = db.claim_task('test-worker')
    db.request_cancel(task['id'])
    effects = Effects(place['log'])
    final = tasks.run_task(db, claimed, 'test-worker', saver=place['saver'], model=ScriptedModel(script=script()),
                           search=effects.search, fetch=effects.fetch, reports_dir=place['reports'])
    assert final['status'] == 'cancelled' and effects.lines() == []


def test_the_cost_limit_stops_a_task(place, monkeypatch):
    monkeypatch.setattr(research, 'MAX_COST_USD', 0.01)   # each scripted step costs $0.008
    final, effects = run(place, ScriptedModel(script=script()))
    assert final['status'] == 'failed' and 'cost limit' in final['error']
    assert final['progress']['model_calls'] == 2


def test_the_step_limit_stops_a_task(place, monkeypatch):
    monkeypatch.setattr(research, 'MAX_MODEL_CALLS', 3)
    final, _ = run(place, ScriptedModel(script=script()))
    assert final['status'] == 'failed' and 'step limit' in final['error']


def test_the_time_limit_stops_a_task(place, monkeypatch):
    monkeypatch.setattr(research, 'MAX_MINUTES', 0)
    final, effects = run(place, ScriptedModel(script=script()))
    assert final['status'] == 'failed' and 'time limit' in final['error'] and effects.lines() == []


def test_errors_shown_to_the_owner_hide_anything_like_a_key():
    error = RuntimeError('auth failed for sk-ant-api03-abcdefgh and Bearer xyz123')
    assert 'sk-ant' not in tasks._plain(error) and 'xyz123' not in tasks._plain(error)


def test_schedule_intervals():
    assert tasks.parse_every('1d') == 86400 and tasks.parse_every('6h') == 21600
    for bad in ('5m', 'often', ''):
        with pytest.raises(ValueError):
            tasks.parse_every(bad)


def test_research_runs_on_the_research_model_through_openrouter_with_its_own_key_only(monkeypatch):
    # Claude Sonnet 5.5 (models.py, owner's guide 2026-10-13). Without the OpenRouter key the task stops; no other
    # key goes to openrouter.ai.
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    monkeypatch.setenv('OPENAI_API_KEY', 'not-for-openrouter')
    with pytest.raises(RuntimeError, match='OPENROUTER_API_KEY'):
        research.default_model()
    monkeypatch.setenv('OPENROUTER_API_KEY', 'or-test-key')
    model = research.default_model()
    assert research.MODEL == 'deepseek/deepseek-v4-pro' and model.model_name == research.MODEL
    assert model.openai_api_base == 'https://openrouter.ai/api/v1'
    assert model.openai_api_key.get_secret_value() == 'or-test-key'
    assert model.extra_body == {'reasoning': {'effort': research.EFFORT, 'exclude': True}}


def test_a_sonnet_turn_is_counted_at_sonnets_prices_and_an_unknown_model_at_the_dearest():
    usage = {'input_tokens': 1000, 'output_tokens': 100, 'total_tokens': 1100, 'input_token_details': {'cache_read': 400}}
    sonnet = AIMessage(content='x', usage_metadata=usage, response_metadata={'model_name': 'anthropic/claude-sonnet-5.5'})
    assert research.cost_of(sonnet) == pytest.approx(600 * 2e-6 + 400 * 0.2e-6 + 100 * 10e-6)
    haiku = AIMessage(content='x', usage_metadata=usage, response_metadata={'model_name': 'anthropic/claude-haiku-5.5'})
    assert research.cost_of(haiku) == pytest.approx(600 * 0.1e-6 + 400 * 0.01e-6 + 100 * 0.5e-6)
    other = AIMessage(content='x', usage_metadata=usage, response_metadata={'model_name': 'someone/else'})
    assert research.cost_of(other) == pytest.approx(600 * 4e-6 + 400 * 0.2e-6 + 100 * 20e-6)


def test_a_dropped_openrouter_connection_is_retried_later():
    import httpx
    import openai
    request = httpx.Request('POST', 'https://openrouter.ai/api/v1/chat/completions')
    assert tasks._transient(openai.APIConnectionError(request=request))
    assert not tasks._transient(ValueError('bad'))


def test_the_cost_of_a_fallback_served_turn_uses_each_models_prices():
    message = AIMessage(content='x', usage_metadata={'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0},
                        response_metadata={'usage': {'iterations': [
                            {'model': 'claude-opus-5-5', 'input_tokens': 1000, 'output_tokens': 0},
                            {'model': 'claude-opus-5', 'input_tokens': 1000, 'output_tokens': 100}]}})
    assert research.cost_of(message) == pytest.approx(1000 * 4e-6 + 1000 * 5e-6 + 100 * 25e-6)
