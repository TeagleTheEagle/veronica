"""Tests for the durable store, the citation check and report saving, and the web layer (no network)."""
import hashlib
import json
from pathlib import Path

import pytest

import citations
import store as store_mod
import web


@pytest.fixture
def db(tmp_path):
    s = store_mod.Store(tmp_path / 'veronica.db')
    yield s
    s.close()


# ----- tasks, leases, schedules ------------------------------------------------------------------------------------

def test_a_request_id_never_makes_a_second_task(db):
    first = db.create_task('research', 'Topic', {'topic': 'x'}, request_id='click-1')
    again = db.create_task('research', 'Topic', {'topic': 'x'}, request_id='click-1')
    assert first['id'] == again['id'] and len(db.list_tasks()) == 1


def test_only_one_worker_claims_a_task_and_a_dead_workers_task_is_taken_over(db, monkeypatch):
    task = db.create_task('research', 'T', {'topic': 'x'})
    assert db.claim_task('worker-a', lease_seconds=60)['id'] == task['id']
    assert db.claim_task('worker-b', lease_seconds=60) is None          # still leased to worker-a
    clock = store_mod.now() + 120
    monkeypatch.setattr(store_mod, 'now', lambda: clock)             # worker-a stopped renewing (crashed)
    taken = db.claim_task('worker-b', lease_seconds=60)
    assert taken['id'] == task['id'] and taken['attempts'] == 2 and taken['lease_owner'] == 'worker-b'
    assert not db.finish_task(task['id'], 'succeeded', worker_id='worker-a')  # the old worker may not finish it
    assert db.finish_task(task['id'], 'succeeded', worker_id='worker-b')


def test_cancel_queued_and_running(db):
    queued = db.create_task('research', 'Q', {})
    assert db.request_cancel(queued['id'])['status'] == 'cancelled'
    running = db.create_task('research', 'R', {})
    db.claim_task('w')
    assert db.request_cancel(running['id'])['status'] == 'running' and db.cancel_requested(running['id'])


def test_a_schedule_queues_each_run_once_and_does_not_pile_up_missed_runs(db):
    start = 1_000_000.0
    schedule = db.add_schedule('research', 'Daily', {'topic': 'x'}, every_seconds=3600, first_run_at=start)
    assert len(db.enqueue_due(at=start + 10)) == 1
    assert db.enqueue_due(at=start + 20) == []                       # same run, asked again: nothing new
    assert db.get_schedule(schedule['id'])['next_run_at'] == start + 3600
    assert len(db.enqueue_due(at=start + 5 * 3600 + 5)) == 1          # four runs missed: one run, not four
    assert db.get_schedule(schedule['id'])['next_run_at'] == start + 6 * 3600
    with pytest.raises(ValueError):
        db.add_schedule('research', 'Too often', {}, every_seconds=10)


def test_memories_are_kept_apart_from_tasks(db):
    memory = db.remember('preference', 'Use British spelling in reports.', 'owner, in chat')
    task = db.create_task('research', 'T', {})
    db.finish_task(task['id'], 'cancelled')
    assert db.memory(memory['id'])['provenance'] == 'owner, in chat'
    updated = db.update_memory(memory['id'], 'Use Australian spelling.', 'owner corrected it')
    assert updated['content'] == 'Use Australian spelling.' and updated['updated_at'] >= updated['created_at']
    assert db.forget(memory['id']) and db.memories() == []


def test_sources_are_numbered_per_task_and_a_url_keeps_its_number(db):
    assert db.add_source('t1', 'https://a.example/', 'A', 'text a', 'h1') == (1, True)
    assert db.add_source('t1', 'https://b.example/', 'B', 'text b', 'h2') == (2, True)
    assert db.add_source('t1', 'https://a.example/', 'A', 'text a', 'h1') == (1, False)
    assert db.add_source('t2', 'https://b.example/', 'B', 'text b', 'h2') == (1, True)


# ----- the citation check ------------------------------------------------------------------------------------------

SOURCES = [
    {'n': 1, 'url': 'https://example.org/heat', 'title': 'Heat pumps', 'fetched_at': 0, 'content_hash': 'a' * 64,
     'text': 'Heat pumps move heat rather than making it, so they can deliver three units of heat for each unit of '
             'electricity in mild weather.'},
    {'n': 2, 'url': 'https://example.org/cold', 'title': 'Cold climates', 'fetched_at': 0, 'content_hash': 'b' * 64,
     'text': 'In very cold weather the efficiency of air source heat pumps falls, although modern units still work '
             'well below freezing.'},
]
BODY = ('Heat pumps are efficient because they move heat instead of generating it [1]. ' * 4 +
        'Their efficiency drops in very cold weather, but modern units keep working below freezing [2]. ' * 4)
GOOD = [{'source': 1, 'quote': 'they can deliver three units of heat for each unit of electricity'},
        {'source': 2, 'quote': 'the efficiency of air source heat pumps falls ... still work well below freezing'}]


def test_a_well_cited_report_passes():
    result = citations.verify(BODY, GOOD, SOURCES)
    assert result['ok'], result['problems']
    assert result['cited'] == [1, 2] and result['quotes_checked'] == 2


def test_citation_presence_is_not_enough():
    made_up = [{'source': 1, 'quote': 'heat pumps are ten times better than gas boilers'}, GOOD[1]]
    result = citations.verify(BODY, made_up, SOURCES)
    assert not result['ok'] and any('not found' in p for p in result['problems'])


def test_citing_a_source_that_was_never_retrieved_fails():
    result = citations.verify(BODY + ' Also see [3].', GOOD, SOURCES)
    assert any('[3] is cited but no source 3' in p for p in result['problems'])


def test_a_cited_source_without_a_quote_fails_and_stray_urls_fail():
    result = citations.verify(BODY + ' More at https://unretrieved.example/page', GOOD[:1], SOURCES)
    problems = ' '.join(result['problems'])
    assert '[2] is cited but has no supporting quote' in problems and 'not retrieved' in problems


def test_quotes_tolerate_case_spacing_and_curly_quotes():
    assert citations.quote_found('Heat   pumps “move heat” rather than MAKING it', 'Heat pumps "move heat" rather '
                                 'than making it, so')
    assert not citations.quote_found('making it rather than move heat', 'move heat rather than making it')


def test_cited_numbers_reads_groups_and_ranges():
    assert citations.cited_numbers('a [1] b [2, 3] c [4-6] d [7–8]') == {1, 2, 3, 4, 5, 6, 7, 8}


def test_the_rendered_report_lists_only_retrieved_cited_sources_and_says_what_was_checked():
    out = citations.render('Heat pumps', BODY, GOOD, SOURCES, 'task1')
    assert '[1] Heat pumps — https://example.org/heat' in out and '[2] Cold climates' in out
    assert 'does not prove that the claims are true' in out


# ----- saving once ------------------------------------------------------------------------------------------------

def test_a_report_is_saved_once_with_evidence(db, tmp_path):
    first = citations.save_report(db, 'task1', 'Heat pumps', 'report text', folder=tmp_path / 'reports')
    assert Path(first['path']).read_text(encoding='utf-8') == 'report text'
    assert first['sha256'] == hashlib.sha256(b'report text').hexdigest()
    again = citations.save_report(db, 'task1', 'Heat pumps', 'report text', folder=tmp_path / 'reports')
    assert again['already_saved'] and again['path'] == first['path']
    assert len(list((tmp_path / 'reports').glob('*.md'))) == 1


def test_a_save_interrupted_after_writing_is_reconciled_not_repeated(db, tmp_path):
    folder = tmp_path / 'reports'
    first = citations.save_report(db, 'task2', 'T', 'content', folder=folder)
    # the worker stopped after the file landed but before the database said so
    db.finish_operation('save-report:task2', 'pending')
    stamp = Path(first['path']).stat().st_mtime_ns
    again = citations.save_report(db, 'task2', 'T', 'content', folder=folder)
    assert again['reconciled'] and Path(first['path']).stat().st_mtime_ns == stamp
    assert db.get_operation('save-report:task2')['status'] == 'done'


def test_a_save_that_never_landed_is_done_again(db, tmp_path):
    folder = tmp_path / 'reports'
    first = citations.save_report(db, 'task3', 'T', 'content', folder=folder)
    Path(first['path']).unlink()
    db.finish_operation('save-report:task3', 'uncertain')
    again = citations.save_report(db, 'task3', 'T', 'content', folder=folder)
    assert Path(again['path']).read_text(encoding='utf-8') == 'content'


# ----- the web -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('url', ['http://127.0.0.1/', 'http://localhost:8000/', 'http://192.168.1.1/admin',
                                 'http://10.0.0.5/', 'http://169.254.169.254/latest/', 'file:///C:/Windows/win.ini',
                                 'ftp://example.org/'])
def test_private_and_non_web_addresses_are_refused(url):
    with pytest.raises(web.WebError):
        web.check_url(url)


def test_transient_failures_are_retried_with_backoff_then_give_up(monkeypatch):
    monkeypatch.setattr(web, 'check_url', lambda url: url)
    calls, waits = [], []

    def flaky(url, headers, timeout, max_bytes, opener):
        calls.append(url)
        problem = web.WebError('busy', transient=True, status=503)
        problem.retry_after = None
        raise problem

    monkeypatch.setattr(web, '_get_once', flaky)
    with pytest.raises(web.WebError):
        web.request('https://example.org/', sleep=waits.append, retries=3)
    assert len(calls) == 4 and len(waits) == 3 and waits[0] < waits[1] < waits[2] <= 8.5


def test_permanent_failures_are_not_retried(monkeypatch):
    monkeypatch.setattr(web, 'check_url', lambda url: url)
    calls = []

    def missing(url, headers, timeout, max_bytes, opener):
        calls.append(url)
        raise web.WebError('The server answered 404.', status=404)

    monkeypatch.setattr(web, '_get_once', missing)
    with pytest.raises(web.WebError, match='404'):
        web.request('https://example.org/x', sleep=lambda s: None)
    assert len(calls) == 1


def test_brave_results_are_parsed_and_the_key_stays_in_a_header():
    seen = {}

    def fake(url, headers=None):
        seen.update(url=url, headers=headers)
        return url, 'application/json', json.dumps({'web': {'results': [
            {'title': '<strong>Heat</strong> pumps', 'url': 'https://a.example/', 'description': 'About <b>heat</b>'},
            {'title': 'No url'}]}}).encode()

    results = web.brave_search('heat   pumps', count=5, key='test-key', fetch=fake)
    assert results == [{'title': 'Heat pumps', 'url': 'https://a.example/', 'description': 'About heat'}]
    assert 'test-key' not in seen['url'] and seen['headers']['X-Subscription-Token'] == 'test-key'
    assert 'q=heat+pumps' in seen['url'] and 'count=5' in seen['url']


def test_brave_without_a_key_says_so(monkeypatch):
    monkeypatch.delenv('BRAVE_API_KEY', raising=False)
    with pytest.raises(web.WebError, match='BRAVE_API_KEY'):
        web.brave_search('x')


def test_pages_become_plain_text():
    markup = ('<html><head><title>Heat &amp; cold</title><style>p{}</style></head><body><script>alert(1)</script>'
              '<h1>Heat pumps</h1><p>They move heat rather than making it, which is why they are efficient.</p>'
              '<p>Second paragraph with more words in it.</p></body></html>')
    page = web.fetch_page('https://example.org/', fetch=lambda url: (url, 'text/html; charset=utf-8',
                                                                       markup.encode()))
    assert page['title'] == 'Heat & cold'
    assert page['text'].splitlines()[0] == 'Heat pumps' and 'alert' not in page['text']
    assert page['content_hash'] == hashlib.sha256(page['text'].encode()).hexdigest()


def test_non_text_files_are_not_read():
    with pytest.raises(web.WebError, match='application/pdf'):
        web.fetch_page('https://example.org/a.pdf', fetch=lambda url: (url, 'application/pdf', b'%PDF-1.7'))
