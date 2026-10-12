"""Research tasks and memory from VERONICA's chat (agent.py), with a scripted Claude and a temporary store."""
import pytest

import store as store_mod
from agent import Veronica
from test_veronica import FakeClaude, message as said, text, tool


@pytest.fixture
def db(tmp_path):
    s = store_mod.Store(tmp_path / 'veronica.db')
    yield s
    s.close()


def make(tmp_path, db, *script):
    started = []
    veronica = Veronica(client=FakeClaude(*script), projects_file=tmp_path / 'none.json', store=db,
                        on_task_started=lambda store: started.append(True) or True)
    return veronica, started


def test_research_starts_a_background_task_and_reports_its_status(tmp_path, db):
    veronica, started = make(tmp_path, db,
                             said(tool('start_research', topic='heat pumps in cold climates', focus='running costs')),
                             said(text('I have started researching heat pumps; it runs in the background.')),
                             said(tool('research_status')),
                             said(text('It is queued.')))
    reply = veronica.respond('research heat pumps in cold climates properly, mostly running costs')
    assert 'background' in reply.say and started == [True]
    task, = db.list_tasks()
    assert task['kind'] == 'research' and task['input']['focus'] == 'running costs' and task['status'] == 'queued'
    veronica.respond('how is the research going?')
    result = veronica.messages[-2]['content'][0]['content']
    assert task['id'] in result and 'QUEUED' in result


def test_cancel_research_from_chat(tmp_path, db):
    import tasks
    task = tasks.start_research(db, 'solar panels')
    veronica, _ = make(tmp_path, db, said(tool('cancel_research', task_id=task['id'])), said(text('Cancelled.')))
    veronica.respond('stop the solar research')
    assert db.get_task(task['id'])['status'] == 'cancelled'


def test_memory_keeps_the_owners_words_as_provenance_and_comes_back_next_conversation(tmp_path, db):
    veronica, _ = make(tmp_path, db,
                       said(tool('remember', content='The owner prefers British spelling in reports.')),
                       said(text('Noted.')),
                       said(text('You prefer British spelling.')))
    veronica.respond('remember that I like British spelling in reports')
    memory, = db.memories()
    assert memory['kind'] == 'preference'
    assert '"remember that I like British spelling in reports"' in memory['provenance']
    veronica.new_chat()
    veronica.respond('what do you know about me?')
    first_turn = veronica.messages[0]['content']
    assert any('British spelling' in block['text'] for block in first_turn if block['type'] == 'text')
    import tasks
    assert tasks.start_research(db, 'heat pumps')['input']['preferences'] == [
        'The owner prefers British spelling in reports.']


def test_correct_list_and_forget_memories(tmp_path, db):
    memory = db.remember('fact', 'The owner lives in Sydney.', 'test')
    veronica, _ = make(tmp_path, db,
                       said(tool('update_memory', id=memory['id'], content='The owner lives in Melbourne.')),
                       said(tool('list_memories')),
                       said(tool('forget_memory', id=memory['id'])),
                       said(tool('forget_memory', id=memory['id'])),
                       said(text('Done.')))
    veronica.respond('I moved to Melbourne. Then forget where I live.')
    results = [m['content'][0] for m in veronica.messages if m['role'] == 'user' and m['content'][0].get('type') ==
               'tool_result']
    assert 'Melbourne' in results[1]['content'] and 'corrected by the owner' in results[1]['content']
    assert results[2]['content'].startswith('Forgotten') and results[3]['is_error']
    assert db.memories() == []


def test_remember_refuses_junk(tmp_path, db):
    veronica, _ = make(tmp_path, db, said(tool('remember', content='x', kind='secret')), said(text('ok')))
    veronica.respond('remember x')
    assert db.memories() == []
