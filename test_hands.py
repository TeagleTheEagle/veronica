"""VERONICA's hands (hands.py) and the chat tools over them. The command tests run real processes (python itself);
the web tools are fed fakes, so nothing here touches the network."""
import sys
import time
from pathlib import Path

import pytest

import agent
import hands
import web
from agent import Veronica
from test_veronica import FakeClaude, message, text, tool

PY = f'"{sys.executable}"'


# ----- hands.py ------------------------------------------------------------------------------------------------------

def test_a_command_runs_in_the_folder_asked_for_and_reports_its_exit_code(tmp_path):
    code, out = hands.run_command(f'{PY} -c "import os; print(os.getcwd())"', tmp_path)
    assert code == 0 and Path(out).resolve() == tmp_path.resolve()
    code, out = hands.run_command(f'{PY} -c "import sys; print(\'oops\', file=sys.stderr); sys.exit(7)"', tmp_path)
    assert code == 7 and out == 'oops'


def test_a_command_that_runs_too_long_is_stopped(tmp_path):
    started = time.time()
    code, out = hands.run_command(f'{PY} -c "import time; print(\'going\', flush=True); time.sleep(60)"', tmp_path,
                                  timeout=2)
    assert code is None and 'going' in out and 'stopped after 2 seconds' in out
    assert time.time() - started < 30


def test_bad_commands_and_folders_are_refused_plainly(tmp_path):
    with pytest.raises(hands.HandsError, match='Give a command'):
        hands.run_command('   ', tmp_path)
    with pytest.raises(hands.HandsError, match='no folder'):
        hands.run_command('echo hi', tmp_path / 'missing')


def test_long_output_keeps_both_ends_and_hides_keys(monkeypatch):
    monkeypatch.setenv('BRAVE_API_KEY', 'brave-secret-value')
    text_ = 'start ' + 'x' * 50_000 + ' brave-secret-value end'
    out = hands.clip(text_)
    assert out.startswith('start') and out.endswith('[hidden] end') and 'left out' in out
    assert 'brave-secret-value' not in out and len(out) < 21_000


def test_background_processes_are_followed_and_stopped(tmp_path):
    box = hands.Processes()
    pid = box.start(f'{PY} -c "import time; print(\'serving\', flush=True); time.sleep(60)"', tmp_path)
    for _ in range(50):
        if 'serving' in box.output(pid):
            break
        time.sleep(0.1)
    assert 'still running' in box.output(pid) and 'serving' in box.output(pid)
    assert pid in box.list()
    assert box.stop(pid).startswith('Stopped')
    assert 'stopped by VERONICA' in box.state(pid)
    assert box.stop(pid).startswith(f'Process {pid} had already ended')
    with pytest.raises(hands.HandsError, match='no background process'):
        box.output('nope')


def test_a_finished_background_process_says_so(tmp_path):
    box = hands.Processes()
    pid = box.start(f'{PY} -c "print(\'done\')"', tmp_path)
    for _ in range(50):
        if 'finished' in box.state(pid):
            break
        time.sleep(0.1)
    assert box.state(pid) == 'finished with exit code 0' and 'done' in box.output(pid)


# ----- the chat tools -------------------------------------------------------------------------------------------------

def test_the_new_tools_have_schemas_and_methods():
    for name in ('run_command', 'start_process', 'process_output', 'stop_process', 'list_processes', 'web_search',
                 'fetch_url'):
        assert name in agent.TOOL_SPECS and hasattr(Veronica, 'tool_' + name)
        assert any(t['function']['name'] == name for t in agent.TOOLS)


def test_run_command_from_chat_defaults_to_the_open_project(tmp_path):
    runs = []

    def runner(command, cwd, timeout=None):
        runs.append((command, Path(cwd), timeout))
        return 0, 'ok'

    veronica = Veronica(client=FakeClaude(), projects_file=tmp_path / 'none.json', command_runner=runner)
    assert veronica.tool_run_command('git status') == 'Exit code 0.\nok'
    assert runs[0][1] == Path.home()
    project = tmp_path / 'proj'
    project.mkdir()
    veronica.open(str(project))
    veronica.tool_run_command('git status', timeout=30)
    assert runs[1][1] == Path(project.resolve()) and runs[1][2] == 30
    veronica.tool_run_command('dir', cwd=str(tmp_path))
    assert runs[2][1] == tmp_path

    def stopped(command, cwd, timeout=None):
        return None, 'partial'
    veronica.run_command = stopped
    assert veronica.tool_run_command('sleep') == 'Stopped at the timeout.\npartial'


def test_she_runs_a_command_in_conversation_and_sees_the_result(tmp_path):
    veronica = Veronica(client=FakeClaude(message(tool('run_command', command='git --version')),
                                          message(text('Git 2.50 is installed.'))),
                        projects_file=tmp_path / 'none.json',
                        command_runner=lambda command, cwd, timeout=None: (0, 'git version 2.50.0'))
    assert veronica.respond('which git do I have?').say == 'Git 2.50 is installed.'
    result, = [m for m in veronica.client.requests[1]['messages'] if m['role'] == 'tool']
    assert result['content'] == 'Exit code 0.\ngit version 2.50.0'


def test_web_search_and_fetch_url_from_chat(tmp_path):
    def search(query, count=8):
        assert query == 'heat pumps' and count == 3
        return [{'title': 'Heat pumps', 'url': 'https://example.com/hp', 'description': 'All about them.'}]

    def fetch(url):
        assert url == 'https://example.com/hp'
        return {'url': url, 'title': 'Heat pumps', 'text': 'Body text. ' * 50, 'content_hash': 'abc'}

    veronica = Veronica(client=FakeClaude(), projects_file=tmp_path / 'none.json', search=search, fetch=fetch)
    found = veronica.tool_web_search('heat pumps', count=3)
    assert found == '1. Heat pumps\n   https://example.com/hp\n   All about them.'
    page = veronica.tool_fetch_url('https://example.com/hp', max_chars=200)
    assert page.startswith('Heat pumps\nhttps://example.com/hp\n\nBody text.') and 'more characters' in page
    veronica.search = lambda query, count=8: []
    assert veronica.tool_web_search('nothing') == 'No results.'


def test_web_errors_go_back_to_her_not_to_the_owner(tmp_path):
    def refused(query, count=8):
        raise web.WebError('Web search needs BRAVE_API_KEY in VERONICA\'s .env.')

    veronica = Veronica(client=FakeClaude(message(tool('web_search', query='x')),
                                          message(text('I cannot search: the Brave key is missing.'))),
                        projects_file=tmp_path / 'none.json', search=refused)
    assert 'Brave key' in veronica.respond('search x').say
    result, = [m for m in veronica.client.requests[1]['messages'] if m['role'] == 'tool']
    assert 'BRAVE_API_KEY' in result['content']


def test_the_coder_subagent_uses_the_coding_model_and_the_hands(tmp_path, monkeypatch):
    monkeypatch.delenv('OPENROUTER_CODING_MODEL', raising=False)
    monkeypatch.delenv('OPENROUTER_MODEL', raising=False)
    veronica = Veronica(client=FakeClaude(message(tool('task', description='run the build', subagent_type='coder')),
                                          message(tool('run_command', command='make')),   # the coder's turns
                                          message(text('Built: exit code 0.')),
                                          message(text('The build passed.'))),            # VERONICA's answer
                        projects_file=tmp_path / 'none.json',
                        command_runner=lambda command, cwd, timeout=None: (0, 'built'))
    assert veronica.respond('build it').say == 'The build passed.'
    models = [r['model'] for r in veronica.client.requests]
    assert models == ['deepseek/deepseek-v4-pro'] * 4       # chat and coding both on DeepSeek V4 Pro (owner, 2026-10-14)
    coder_tools = {t['function']['name'] for t in veronica.client.requests[1]['tools']}
    assert {'run_command', 'edit_file', 'run_tests'} <= coder_tools and 'send_email' not in coder_tools
    assert veronica.messages[-2]['role'] == 'tool' and 'Built: exit code 0.' in veronica.messages[-2]['content']


def test_the_helpers_use_the_cheap_model_and_the_secretary_keeps_its_own_tools(tmp_path):
    veronica = Veronica(client=FakeClaude(message(tool('task', description='check mail', subagent_type='secretary')),
                                          message(text('Nothing new.')),
                                          message(text('No new mail.'))),
                        projects_file=tmp_path / 'none.json')
    assert veronica.respond('any mail?').say == 'No new mail.'
    assert veronica.client.requests[1]['model'] == 'xiaomi/mimo-v2.6-flash'
    names = {t['function']['name'] for t in veronica.client.requests[1]['tools']}
    assert 'check_email' in names and 'run_command' not in names
