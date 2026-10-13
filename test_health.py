"""VERONICA looking after herself (health.py) and the chat tools over it. Repairs run on a throwaway folder with its
own git repository, never on her real folder; her test suite is replaced by a fake that reads a marker file."""
import json
import subprocess
from pathlib import Path

import pytest

import health
import store as store_mod
from agent import Veronica
from test_veronica import FakeClaude, message, text, tool


def git(root, *args):
    return subprocess.run(['git', *args], cwd=str(root), capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def home(tmp_path):
    """A small 'VERONICA folder' under git on master, with one clean commit."""
    root = tmp_path / 'veronica'
    root.mkdir()
    (root / 'brain.py').write_text('def answer():\n    return 41\n', encoding='utf-8')
    (root / 'README.md').write_text('# me\n', encoding='utf-8')
    (root / 'ui').mkdir()
    (root / 'ui' / 'index.html').write_text('<p>hi</p>\n', encoding='utf-8')
    (root / 'data').mkdir()
    (root / 'data' / 'private.db').write_text('not mine to snapshot', encoding='utf-8')
    git(root, 'init', '-q', '-b', 'master')
    git(root, 'config', 'user.email', 'test@example.com')
    git(root, 'config', 'user.name', 'Test')
    git(root, 'add', 'brain.py', 'README.md', 'ui/index.html')
    git(root, 'commit', '-q', '-m', 'start')
    return root


def fake_tests(root, python=None):
    """Her tests, faked: brain.py must return 42 for test_answer; 'BROKEN' anywhere breaks test_other too."""
    code = (Path(root) / 'brain.py').read_text(encoding='utf-8') if (Path(root) / 'brain.py').is_file() else ''
    failed = set()
    if 'return 42' not in code:
        failed.add('test_brain.py::test_answer')
    if 'BROKEN' in code:
        failed.add('test_brain.py::test_other')
    return failed, f'{2 - len(failed)} passed, {len(failed)} failed'


def healthy(root, python=None):
    return health.Report([health.Check('code', True, 'every module imports')])


def unhealthy(root, python=None):
    return health.Report([health.Check('code', False, "SyntaxError: invalid syntax (brain.py, line 2)")])


# ----- the health check -------------------------------------------------------------------------------------------------

def test_the_check_reports_keys_database_worker_and_open_repairs(tmp_path, home, monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'or-test')
    monkeypatch.delenv('BRAVE_API_KEY', raising=False)
    db = store_mod.Store(tmp_path / 'v.db')
    try:
        report = health.check(store=db, root=home, imports=False)
        names = {c.name: c for c in report.checks}
        assert report.ok and names['keys'].ok is True and 'BRAVE_API_KEY' in names['keys'].detail
        assert names['database'].ok and names['worker'].detail == 'nothing waiting'
        assert names['speech model'].ok is None and names['repairs'].detail == 'none open'
        monkeypatch.delenv('OPENROUTER_API_KEY')
        report = health.check(store=db, root=home, imports=False)
        assert not report.ok and 'keys' in report.summary() and 'FAIL  keys: missing OPENROUTER_API_KEY' in report.text()
    finally:
        db.close()


def test_waiting_tasks_get_a_worker_started(tmp_path, home, monkeypatch):
    import tasks
    monkeypatch.setenv('OPENROUTER_API_KEY', 'or-test')
    db = store_mod.Store(tmp_path / 'v.db')
    try:
        tasks.start_research(db, 'heat pumps')
        started = []
        monkeypatch.setattr(tasks, 'ensure_worker', lambda store: started.append(True) or True)
        worker = {c.name: c for c in health.check(store=db, root=home, imports=False).checks}['worker']
        assert started == [True] and worker.ok and 'started one for 1 waiting' in worker.detail
        worker = {c.name: c for c in health.check(store=db, root=home, imports=False, start_worker=False).checks}['worker']
        assert worker.ok is False
    finally:
        db.close()


def test_imports_ok_finds_a_broken_module(tmp_path):
    (tmp_path / 'good.py').write_text('X = 1\n', encoding='utf-8')
    (tmp_path / 'bad.py').write_text('def oops(:\n', encoding='utf-8')
    assert health.imports_ok(tmp_path, modules=('good',)) == (True, '')
    ok, error = health.imports_ok(tmp_path, modules=('good', 'bad'))
    assert ok is False and 'SyntaxError' in error


def test_her_own_files_are_code_and_page_not_data(home):
    assert health.own_files(home) == ['README.md', 'brain.py', 'ui/index.html']


# ----- repairs ----------------------------------------------------------------------------------------------------------

def test_a_repair_that_fixes_a_test_is_kept_and_committed_to_its_branch_only(home):
    head_before = git(home, 'rev-parse', 'master')
    repair = health.start_repair('answer is off by one', root=home, tests=fake_tests)
    assert repair['baseline_failures'] == ['test_brain.py::test_answer'] and repair['branch'].startswith('self-repair/')
    assert (home / 'data' / 'self-repair' / repair['stamp'] / 'files' / 'brain.py').is_file()
    (home / 'brain.py').write_text('def answer():\n    return 42\n', encoding='utf-8')
    kept, words = health.finish_repair(repair, 'answer returns 42', root=home, tests=fake_tests, health=healthy)
    assert kept and 'Kept: 1 file(s) changed (brain.py)' in words and '1 test(s) that failed before now pass' in words
    assert 'Committed to branch' in words
    # the branch has the fix; master, HEAD and the owner's checkout are untouched
    assert git(home, 'show', f'{repair["branch"]}:brain.py').endswith('return 42')
    assert git(home, 'rev-parse', 'master') == head_before and git(home, 'branch', '--show-current') == 'master'
    assert (home / 'brain.py').read_text(encoding='utf-8').endswith('return 42\n')    # the working tree keeps it
    saved = json.loads((home / 'data' / 'self-repair' / repair['stamp'] / 'manifest.json').read_text(encoding='utf-8'))
    assert saved['status'] == 'kept' and saved['changed'] == ['brain.py']


def test_a_repair_that_breaks_a_passing_test_is_rolled_back(home):
    repair = health.start_repair('tidy brain', root=home, tests=fake_tests)
    (home / 'brain.py').write_text('def answer():\n    return 41  # BROKEN\n', encoding='utf-8')
    (home / 'new_helper.py').write_text('x = 1\n', encoding='utf-8')
    kept, words = health.finish_repair(repair, 'tidied', root=home, tests=fake_tests, health=healthy)
    assert not kept and words.startswith('Rolled back') and 'test_brain.py::test_other' in words
    assert (home / 'brain.py').read_text(encoding='utf-8') == 'def answer():\n    return 41\n'
    assert not (home / 'new_helper.py').exists()
    assert (home / 'data' / 'private.db').read_text(encoding='utf-8') == 'not mine to snapshot'


def test_a_repair_that_fails_the_health_check_is_rolled_back(home):
    repair = health.start_repair('rewrite', root=home, tests=fake_tests)
    (home / 'brain.py').write_text('def answer(:\n    return 42\n', encoding='utf-8')
    kept, words = health.finish_repair(repair, 'rewrote', root=home, tests=fake_tests, health=unhealthy)
    assert not kept and 'health check fails' in words and 'SyntaxError' in words
    assert (home / 'brain.py').read_text(encoding='utf-8').endswith('return 41\n')


def test_tests_that_cannot_run_count_as_a_failed_repair(home):
    repair = health.start_repair('x', root=home, tests=fake_tests)
    (home / 'brain.py').write_text('def answer():\n    return 42\n', encoding='utf-8')
    kept, words = health.finish_repair(repair, 'y', root=home, tests=lambda root, python=None: (None, 'collection error'),
                                       health=healthy)
    assert not kept and 'collection error' in words


def test_a_kept_repair_can_be_rolled_back_later_without_undoing_other_work(home):
    repair = health.start_repair('fix', root=home, tests=fake_tests)
    (home / 'brain.py').write_text('def answer():\n    return 42\n', encoding='utf-8')
    assert health.finish_repair(repair, 'fixed', root=home, tests=fake_tests, health=healthy)[0]
    (home / 'README.md').write_text('# me, edited by the owner later\n', encoding='utf-8')
    restored = health.rollback(health.latest_repair(home), root=home)
    assert restored == ['brain.py'] and (home / 'brain.py').read_text(encoding='utf-8').endswith('return 41\n')
    assert (home / 'README.md').read_text(encoding='utf-8') == '# me, edited by the owner later\n'
    assert health.repairs(home)[-1]['status'] == 'rolled back'


def test_an_open_repair_shows_in_the_health_check(tmp_path, home, monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'or-test')
    repair = health.start_repair('left open', root=home, tests=fake_tests)
    db = store_mod.Store(tmp_path / 'v.db')
    try:
        check = {c.name: c for c in health.check(store=db, root=home, imports=False).checks}['repairs']
        assert check.ok is None and repair['stamp'] in check.detail
    finally:
        db.close()


def test_without_git_a_repair_still_snapshots_and_rolls_back(tmp_path):
    root = tmp_path / 'plain'
    root.mkdir()
    (root / 'brain.py').write_text('return 41\n', encoding='utf-8')
    repair = health.start_repair('x', root=root, tests=fake_tests)
    assert repair['branch'] is None
    (root / 'brain.py').write_text('BROKEN\n', encoding='utf-8')
    kept, _ = health.finish_repair(repair, 'y', root=root, tests=fake_tests, health=healthy)
    assert not kept and (root / 'brain.py').read_text(encoding='utf-8') == 'return 41\n'


# ----- from the chat ---------------------------------------------------------------------------------------------------

def make(home, tmp_path, *script):
    db = store_mod.Store(tmp_path / 'v.db')
    return Veronica(client=FakeClaude(*script), projects_file=tmp_path / 'none.json', store=db, home=home,
                    self_tests=fake_tests, test_runner=lambda root, command: (True, 'selftest ok'))


def test_she_repairs_herself_in_conversation_and_the_fix_is_kept(home, tmp_path, monkeypatch):
    monkeypatch.setattr(health, 'check', lambda store=None, root=None, **kw: healthy(root))
    veronica = make(home, tmp_path,
                    message(tool('start_self_repair', reason='answer() is off by one')),
                    message(tool('edit_file', path='brain.py', old_text='return 41', new_text='return 42')),
                    message(tool('finish_self_repair', summary='answer() returns 42')),
                    message(text('Fixed myself: answer() returns 42, kept on its branch.')))
    reply = veronica.respond('your answer function is off by one, fix yourself')
    assert reply.say.startswith('Fixed myself')
    results = [m['content'] for m in veronica.messages if m['role'] == 'tool']
    assert results[0].startswith('Repair ') and 'already failing' in results[0] and 'Opened' in results[0]
    assert results[2].startswith('Kept: 1 file(s) changed (brain.py)')
    assert (home / 'brain.py').read_text(encoding='utf-8').endswith('return 42\n')
    assert veronica.repair is None and health.repairs(home)[-1]['status'] == 'kept'
    veronica.store.close()


def test_she_cannot_keep_a_repair_that_breaks_her_and_it_is_undone(home, tmp_path, monkeypatch):
    monkeypatch.setattr(health, 'check', lambda store=None, root=None, **kw: healthy(root))
    veronica = make(home, tmp_path,
                    message(tool('start_self_repair', reason='tidy')),
                    message(tool('edit_file', path='brain.py', old_text='return 41', new_text='return 41  # BROKEN')),
                    message(tool('finish_self_repair', summary='tidied')),
                    message(text('That broke a test, so it was rolled back.')))
    veronica.respond('tidy your brain')
    result = [m['content'] for m in veronica.messages if m['role'] == 'tool'][2]
    assert result.startswith('Rolled back') and (home / 'brain.py').read_text(encoding='utf-8').endswith('return 41\n')
    veronica.store.close()


def test_rollback_and_list_from_chat(home, tmp_path):
    veronica = make(home, tmp_path)
    assert veronica.tool_rollback_self_repair() == 'There is no repair to roll back.'
    assert veronica.tool_list_self_repairs() == 'No repairs yet.'
    repair = health.start_repair('fix', root=home, tests=fake_tests)
    (home / 'brain.py').write_text('def answer():\n    return 42\n', encoding='utf-8')
    health.finish_repair(repair, 'fixed', root=home, tests=fake_tests, health=healthy)
    assert 'KEPT' in veronica.tool_list_self_repairs()
    said = veronica.tool_rollback_self_repair()
    assert said.startswith(f'Rolled back repair {repair["stamp"]}: brain.py') and 'Restart me' in said
    assert veronica.tool_rollback_self_repair(repair['stamp']).endswith('was already rolled back.')
    veronica.store.close()


def test_a_second_repair_waits_for_the_first(home, tmp_path):
    veronica = make(home, tmp_path)
    assert veronica.tool_start_self_repair('one').startswith('Repair ')
    assert 'is already open' in veronica.tool_start_self_repair('two')
    veronica.store.close()


def test_the_check_takes_her_folder_as_text_too(home, tmp_path, monkeypatch):
    monkeypatch.setattr(health, 'imports_ok', lambda *a, **k: (True, ''))
    db = store_mod.Store(tmp_path / 'v.db')
    report = health.check(store=db, root=str(home), start_worker=False)
    assert [c.name for c in report.checks][-2:] == ['speech model', 'repairs']
    db.close()


def test_self_check_from_chat(home, tmp_path, monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'or-test')
    monkeypatch.setattr(health, 'imports_ok', lambda *a, **k: (True, ''))
    veronica = make(home, tmp_path)
    said = veronica.tool_self_check()
    assert said.startswith('VERONICA is healthy.') and 'ok  code: every module imports' in said
    veronica.store.close()


def test_the_window_shows_only_problems_at_start(home, tmp_path, monkeypatch):
    import app
    from test_app import FakeUI
    ui = FakeUI()
    veronica = make(home, tmp_path)
    session = app.Session(veronica, ui, voice=object(), transcriber=object(), microphone=object(),
                          settings_file=tmp_path / 's.json')
    monkeypatch.setattr(health, 'check', lambda store=None, root=None, **kw: healthy(root))
    assert session.startup_check().ok and ui.named('notice') == []
    monkeypatch.setattr(health, 'check', lambda store=None, root=None, **kw: health.Report(
        [health.Check('keys', False, 'missing OPENROUTER_API_KEY'), health.Check('repairs', None, '1 left open: x')]))
    session.startup_check()
    notices = [args[0] for args in ui.named('notice')]
    assert notices[0] == 'Health check: keys: missing OPENROUTER_API_KEY' and 'left open' in notices[1]
    veronica.store.close()
