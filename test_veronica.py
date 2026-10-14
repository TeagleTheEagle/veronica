"""VERONICA's tests. No test touches the network, the microphone, the speakers or a real API key: Claude, the
project's tests and Fish Audio are replaced by fakes."""
import json
import os
import shutil
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import agent
import changes
import models
import safety
import voice
from agent import Veronica, normalize, validate


# Test folders live outside the JARVIS workspace (VERONICA refuses folders inside it, and anything named JARVIS).
@pytest.fixture
def project():
    base = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Temp' / 'veronica-tests' / uuid.uuid4().hex[:12]
    base.mkdir(parents=True)
    (base / 'app.py').write_text('def add(a, b):\n    return a - b\n', encoding='utf-8')
    (base / 'test_app.py').write_text('from app import add\n\ndef test_add():\n    assert add(2, 2) == 4\n',
                                      encoding='utf-8')
    yield base
    shutil.rmtree(base, ignore_errors=True)


# ----- fakes -----------------------------------------------------------------------------------------------------

def text(words):
    return SimpleNamespace(type='text', text=words)


def tool(name, **given):
    return SimpleNamespace(type='tool_use', id='toolu_' + uuid.uuid4().hex[:8], name=name, input=given)


def message(*blocks, stop=None):
    stop = stop or ('tool_use' if any(b.type == 'tool_use' for b in blocks) else 'end_turn')
    return SimpleNamespace(stop_reason=stop, content=list(blocks))


class FakeClaude:
    """Answers each request with the next scripted message and keeps the requests."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **params):
        """The same script, answered the way OpenRouter (OpenAI-style chat) answers. A scripted reply that is already
        in that shape (it has choices) is given as it is."""
        self.requests.append({**params, 'messages': list(params['messages'])})
        reply = self.script.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if hasattr(reply, 'choices'):
            return reply
        words = ' '.join(b.text for b in reply.content if b.type == 'text') or None
        calls = [SimpleNamespace(id=b.id, type='function',
                                 function=SimpleNamespace(name=b.name, arguments=json.dumps(b.input)))
                 for b in reply.content if b.type == 'tool_use'] or None
        finish = {'max_tokens': 'length', 'tool_use': 'tool_calls'}.get(reply.stop_reason, 'stop')
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish,
                                                        message=SimpleNamespace(content=words, tool_calls=calls))])

    def _stream(self, **params):
        self.requests.append({**params, 'messages': list(params['messages'])})
        reply = self.script.pop(0)

        class Stream:
            def __enter__(self):
                if isinstance(reply, Exception):
                    raise reply
                return self

            def __exit__(self, *exc):
                return False

            def get_final_message(self):
                return reply
        return Stream()


def passing(root, command):
    return True, '1 passed'


def failing(root, command):
    return False, 'FAILED test_app.py::test_add - assert 0 == 4'


def make(project, *script, runner=passing, **kwargs):
    veronica = Veronica(client=FakeClaude(*script), test_runner=runner,
                        projects_file=project / 'no-projects.json', **kwargs)
    veronica.open(str(project))
    return veronica


# ----- safety ----------------------------------------------------------------------------------------------------

def test_every_folder_is_allowed_as_a_project(project):
    # Owner's decision (2026-10-14): no folder is blocked.
    for folder in (safety.WORKSPACE, safety.HERE, safety.WORKSPACE.parent, Path(safety.WORKSPACE.anchor), Path.home()):
        assert safety.check_project(folder) == Path(os.path.realpath(folder))


def test_a_normal_project_folder_is_allowed(project):
    assert safety.check_project(project) == Path(os.path.realpath(project))


def test_paths_are_not_restricted(project):
    # Owner's decision (2026-10-14): outside paths, secret files, .git, venvs and node_modules are allowed.
    for path in ('../outside.py', '.env', 'id_rsa', '.git/config', '.venv/lib/x.py', 'node_modules/a/index.js'):
        assert safety.resolve(project, path, writing=True) == Path(os.path.realpath(project / path))
    assert not safety.is_secret('.env')


# ----- changes ---------------------------------------------------------------------------------------------------

def test_undo_puts_files_back_and_removes_what_was_created(project):
    box = changes.ChangeSet()
    app, new = project / 'app.py', project / 'pkg' / 'deep' / 'new.py'
    box.before_write(app, project)
    app.write_text('changed\n', encoding='utf-8')
    box.before_write(new, project)
    new.parent.mkdir(parents=True)
    new.write_text('x = 1\n', encoding='utf-8')
    assert sorted(box.touched()) == sorted([app, new])
    assert '-    return a - b' in box.diff(project) and '+changed' in box.diff(project)
    box.undo()
    assert app.read_text(encoding='utf-8') == 'def add(a, b):\n    return a - b\n'
    assert not (project / 'pkg').exists()
    assert not box.pending()


def test_keep_leaves_the_changes_in_place(project):
    box = changes.ChangeSet()
    box.before_write(project / 'app.py', project)
    (project / 'app.py').write_text('kept\n', encoding='utf-8')
    box.keep()
    assert (project / 'app.py').read_text(encoding='utf-8') == 'kept\n' and not box.pending()


def test_the_tests_run_without_keys_in_the_environment(monkeypatch):
    for name in ('ANTHROPIC_API_KEY', 'FISH_API_KEY', 'GITHUB_TOKEN', 'DB_PASSWORD'):
        monkeypatch.setenv(name, 'not-a-real-value')
    monkeypatch.setenv('PATH_EXTRA', 'kept')
    env = changes.clean_env()
    assert not {'ANTHROPIC_API_KEY', 'FISH_API_KEY', 'GITHUB_TOKEN', 'DB_PASSWORD'} & set(env)
    assert env['PATH_EXTRA'] == 'kept'


def test_the_test_command_is_found(project):
    assert 'pytest' in changes.detect_test_command(project)
    (project / 'package.json').write_text(json.dumps({'scripts': {'test': 'jest'}}), encoding='utf-8')
    assert changes.detect_test_command(project) == 'npm test'
    empty = project / 'empty'
    empty.mkdir()
    assert changes.detect_test_command(empty) is None


def test_run_tests_reports_pass_and_fail(project):
    assert changes.run_tests(project, f'"{sys.executable}" -c "print(1)"') == (True, '1')
    passed, output = changes.run_tests(project, f'"{sys.executable}" -c "import sys; print(2); sys.exit(3)"')
    assert passed is False and output == '2'


# ----- the owner's words -----------------------------------------------------------------------------------------

def test_keep_and_undo_are_understood_however_they_are_said():
    for said in ('keep', 'Keep it.', 'Veronica, keep the changes please', 'OK keep it, thanks'):
        assert normalize(said) in agent.KEEP
    for said in ('Undo it!', 'undo the changes', 'roll it back please'):
        assert normalize(said) in agent.UNDO
    assert normalize('Discard them.') in agent.DISCARD_ANY
    assert normalize("Don't send it.") in agent.DISCARD_EMAIL and normalize('Yes, send it!') in agent.SEND
    assert normalize('keep the tests passing') not in agent.KEEP


def test_tool_input_is_checked_before_a_tool_runs():
    assert validate('read_file', {'path': 'a.py', 'start_line': 3}) == {'path': 'a.py', 'start_line': 3}
    for name, given in (('read_file', {}), ('read_file', {'path': 3}), ('read_file', {'path': 'a', 'x': 1}),
                        ('read_file', {'path': 'a', 'start_line': True}), ('no_such_tool', {}),
                        ('read_file', 'not json')):
        with pytest.raises(agent.ToolError):
            validate(name, given)


def test_every_tool_has_a_groq_function_schema():
    assert {t['function']['name'] for t in agent.TOOLS} == set(agent.TOOL_SPECS)
    for definition in agent.TOOLS:
        assert definition['type'] == 'function'
        assert definition['function']['parameters']['type'] == 'object'
    for name in agent.TOOL_SPECS:
        assert hasattr(Veronica, 'tool_' + name)


# ----- tools -----------------------------------------------------------------------------------------------------

def test_file_tools(project):
    veronica = make(project)
    (project / '.env').write_text('KEY=x', encoding='utf-8')
    (project / 'node_modules').mkdir()
    (project / 'node_modules' / 'lib.js').write_text('x', encoding='utf-8')
    listing = veronica.tool_list_files()
    assert 'app.py' in listing
    assert veronica.tool_read_file('app.py').splitlines()[1] == '    2      return a - b'
    assert veronica.tool_search('return a') == 'app.py:2: return a - b'
    with pytest.raises(agent.ToolError, match='not found'):
        veronica.tool_edit_file('app.py', 'nothing like this', 'x')
    (project / 'twice.py').write_text('x = 1\nx = 1\n', encoding='utf-8')
    with pytest.raises(agent.ToolError, match='2 times'):
        veronica.tool_edit_file('twice.py', 'x = 1', 'x = 2')
    assert veronica.tool_edit_file('twice.py', 'x = 1', 'x = 2', replace_all=True).startswith('Edited')
    veronica.tool_write_file('src/new.py', 'print("hi")\n')
    assert (project / 'src' / 'new.py').is_file()
    assert sorted(veronica._names()) == ['src\\new.py' if os.sep == '\\' else 'src/new.py', 'twice.py']


def test_edits_keep_windows_line_endings(project):
    (project / 'crlf.py').write_bytes(b'a = 1\r\nb = 2\r\n')
    veronica = make(project)
    veronica.tool_edit_file('crlf.py', 'a = 1\nb = 2', 'a = 10\nb = 20')
    assert (project / 'crlf.py').read_bytes() == b'a = 10\r\nb = 20\r\n'


def test_tools_need_an_open_project_and_refuse_bad_paths(project):
    veronica = Veronica(client=FakeClaude(), projects_file=project / 'none.json')
    with pytest.raises(agent.ToolError, match='No project'):
        veronica.tool_read_file('app.py')
    veronica.open(str(project))


def test_saved_projects_open_by_name_with_their_test_command(project):
    saved = project / 'projects.json'
    saved.write_text(json.dumps({'Calculator': {'folder': str(project), 'test': 'my-test-command'}}), 'utf-8')
    veronica = Veronica(client=FakeClaude(), projects_file=saved)
    assert 'calculator' in veronica.tool_list_projects()
    assert 'my-test-command' in veronica.open('calculator')
    assert veronica.test_command == 'my-test-command'


def test_another_project_opens_freely_because_writes_are_already_permanent(project):
    veronica = make(project)
    veronica.tool_edit_file('app.py', 'a - b', 'a + b')
    other = project / 'other'
    other.mkdir()
    assert veronica.open(str(other)).startswith('Opened')
    assert (project / 'app.py').read_text(encoding='utf-8').endswith('a + b\n')


# ----- the conversation ------------------------------------------------------------------------------------------

def test_a_fix_that_passes_the_tests_is_applied_and_says_so(project):
    veronica = make(project,
                    message(text('Looking.'), tool('read_file', path='app.py')),
                    message(tool('edit_file', path='app.py', old_text='a - b', new_text='a + b')),
                    message(text('Fixed: add was subtracting.')))
    reply = veronica.respond('fix the add function')
    assert reply.say.startswith('Fixed: add was subtracting.')
    assert 'The tests pass and changes have been applied.' in reply.say
    assert '+    return a + b' in reply.show
    assert (project / 'app.py').read_text(encoding='utf-8').endswith('a + b\n')
    request = veronica.client.requests[0]
    assert request['model'] == agent.chat_model() and request['tool_choice'] == 'auto'
    assert request['max_tokens'] == agent.REPLY_TOKENS
    assert request['extra_body'] == dict(agent.THINKING, provider=models.provider_preferences(agent.chat_model()))
    assert request['messages'][0] == {'role': 'system', 'content': veronica.system_prompt}

    # Direct action: the write is already permanent, so nothing is left waiting for the owner.
    assert not veronica.changes.pending()
    assert veronica.respond('Keep it.').say == 'There is nothing waiting to be kept.'
    assert (project / 'app.py').read_text(encoding='utf-8').endswith('a + b\n')


def test_the_model_is_read_when_she_asks_so_the_env_file_counts(project, monkeypatch):
    # app.py imports agent.py before it reads .env, so OPENROUTER_MODEL must be read at each request, not at import.
    # One model per job from models.py (owner's guide, 2026-10-13); a .env line may only pick another listed model.
    monkeypatch.delenv('OPENROUTER_MODEL', raising=False)
    monkeypatch.setenv('VERONICA_MODEL', 'openai/gpt-oss-120b')
    assert agent.chat_model() == agent.DEFAULT_MODEL == 'deepseek/deepseek-v4-pro'   # owner, 2026-10-14
    monkeypatch.setenv('OPENROUTER_MODEL', 'qwen/qwen3.8-max-0902')       # not on the list: ignored
    assert agent.chat_model() == 'deepseek/deepseek-v4-pro'
    monkeypatch.setenv('OPENROUTER_MODEL', 'anthropic/claude-haiku-5.5')
    veronica = make(project, message(text('Hello.')))
    assert veronica.respond('hello').say == 'Hello.'
    assert veronica.client.requests[0]['model'] == 'anthropic/claude-haiku-5.5'


def test_she_talks_to_openrouter_with_its_own_key_only(project, monkeypatch):
    for name in ('OPENROUTER_API_KEY', 'GROQ_API_KEY', 'OPENAI_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('OPENAI_API_KEY', 'not-for-openrouter')
    veronica = Veronica(projects_file=project / 'no-projects.json')
    with pytest.raises(agent.VeronicaError, match='OPENROUTER_API_KEY'):
        veronica.client
    monkeypatch.setenv('OPENROUTER_API_KEY', 'or-test-key')
    veronica = Veronica(projects_file=project / 'no-projects.json')
    client = veronica.client   # made here; nothing is sent until she asks
    assert str(client.base_url).rstrip('/') == 'https://openrouter.ai/api/v1' and client.api_key == 'or-test-key'


def test_undo_is_gone_because_writes_are_permanent(project):
    veronica = make(project,
                    message(tool('edit_file', path='app.py', old_text='a - b', new_text='a * b')),
                    message(text('Changed it.')))
    veronica.respond('make add multiply')
    assert (project / 'app.py').read_text(encoding='utf-8').endswith('a * b\n')
    assert veronica.respond('undo').say == 'There is nothing to undo.'
    assert (project / 'app.py').read_text(encoding='utf-8').endswith('a * b\n')


def test_failing_tests_go_back_to_her_and_are_reported_not_hidden(project, monkeypatch):
    monkeypatch.setattr(agent, 'FIX_TRIES', 2)   # the real limit is unbounded; two tries keep the scripted test finite
    edit = message(tool('edit_file', path='app.py', old_text='a - b', new_text='a * b'))
    veronica = make(project, edit, message(text('Done.')),
                    message(text('Still trying.')), message(text('I could not fix it.')),
                    runner=failing)
    reply = veronica.respond('fix add')
    assert 'The tests still fail, so I have left the files as they are for your review.' in reply.say
    assert 'FAILED test_app.py::test_add' in reply.show
    # Direct action leaves the files as they are rather than reverting them behind the owner's back.
    assert (project / 'app.py').read_text(encoding='utf-8').endswith('a * b\n')
    sent = [m for m in veronica.client.requests[-1]['messages'] if m['role'] == 'user']
    assert sum('[VERONICA check] The tests fail' in str(m['content']) for m in sent) == agent.FIX_TRIES


def test_tool_errors_go_back_to_the_model_not_the_owner(project):
    veronica = make(project,
                    message(tool('read_file', path='missing.py')),
                    message(text('That file does not exist.')))
    assert veronica.respond('read missing.py').say == 'That file does not exist.'
    result = [m for m in veronica.messages if m['role'] == 'tool'][0]
    assert result['content'] and 'no file' in result['content'].lower()


def test_api_problems_are_said_plainly(project, monkeypatch):
    import anthropic
    slept = []
    monkeypatch.setattr(agent.time, 'sleep', slept.append)
    veronica = make(project, *(anthropic.APIConnectionError(request=None) for _ in range(3)))
    with pytest.raises(agent.VeronicaError, match='failed after 3 attempts'):
        veronica.respond('hello')
    assert slept == [2, 4, 8]          # the exponential backoff, without actually waiting for it


def test_an_unreadable_tool_request_goes_back_to_her(project):
    # Groq gives a tool call's input as JSON text: text that is not JSON goes back to her as an error, not a crash.
    call = SimpleNamespace(id='call_1', type='function',
                           function=SimpleNamespace(name='read_file', arguments='{not json'))
    unreadable = SimpleNamespace(choices=[SimpleNamespace(finish_reason='tool_calls',
                                                          message=SimpleNamespace(content=None, tool_calls=[call]))])
    veronica = make(project, unreadable, message(text('Let me try that again.')))
    assert veronica.respond('read app.py').say == 'Let me try that again.'
    result, = [m for m in veronica.client.requests[1]['messages'] if m['role'] == 'tool']
    assert result['tool_call_id'] == 'call_1' and 'read_file needs path' in result['content']


def test_without_tests_she_says_they_could_not_be_checked(project):
    (project / 'test_app.py').unlink()
    veronica = make(project,
                    message(tool('edit_file', path='app.py', old_text='a - b', new_text='a + b')),
                    message(text('Fixed.')))
    assert veronica.test_command is None
    assert 'no tests I can run' in veronica.respond('fix add').say


# ----- voice -----------------------------------------------------------------------------------------------------

def test_she_only_answers_to_her_name():
    assert voice.parse_wake('Hey Veronica, fix the failing test.') == (True, 'fix the failing test.')
    assert voice.parse_wake('Veronica.') == (True, '')
    assert voice.parse_wake('veronika open the website') == (True, 'open the website')
    assert voice.parse_wake('I told Veronica about it') == (False, '')
    assert voice.parse_wake('Hey Jarvis, what time is it') == (False, '')


def test_speech_text_drops_code_and_markdown():
    said = voice.speech_text('**Fixed.** Changed `add`:\n```python\nreturn a + b\n```\n- tests pass')
    assert said == 'Fixed. Changed add: (the details are on screen) tests pass'
    assert voice.speech_text('word ' * 300).endswith('The rest is on screen.')


def test_the_segmenter_cuts_one_utterance():
    seg = voice.Segmenter(calibration_frames=5)
    quiet = np.full(voice.FRAME, 20, dtype=np.int16)
    loud = (np.sin(np.arange(voice.FRAME)) * 5000).astype(np.int16)
    out = [seg.feed(f) for f in [quiet] * 5 + [quiet] * 3 + [loud] * 10 + [quiet] * voice.END_SILENCE]
    done = [o for o in out if o is not None]
    assert len(done) == 1
    assert len(done[0]) == (voice.PRE_ROLL + 10 + voice.END_SILENCE) * voice.FRAME
    assert seg.feed(quiet) is None


def test_a_cough_is_not_an_utterance():
    seg = voice.Segmenter(calibration_frames=2)
    quiet = np.full(voice.FRAME, 20, dtype=np.int16)
    loud = np.full(voice.FRAME, 8000, dtype=np.int16)
    out = [seg.feed(f) for f in [quiet, quiet, loud, loud] + [quiet] * voice.END_SILENCE]
    assert all(o is None for o in out)


def test_fish_audio_request(monkeypatch):
    sent = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'ID3' + b'\x00' * 200

    def fake_urlopen(request, timeout):
        sent.update(url=request.full_url, headers=dict(request.header_items()), body=json.loads(request.data))
        return Response()

    monkeypatch.setattr(voice.urllib.request, 'urlopen', fake_urlopen)
    fish = voice.FishVoice('test-key', 'voice-123', model='s2.1-pro')
    assert fish.audio('Hello.').startswith(b'ID3')
    assert sent['url'] == 'https://api.fish.audio/v1/tts'
    assert sent['headers']['Authorization'] == 'Bearer test-key' and sent['headers']['Model'] == 's2.1-pro'
    assert sent['body'] == {'text': 'Hello.', 'format': 'mp3', 'reference_id': 'voice-123'}


def test_when_fish_audio_fails_windows_voice_says_it(monkeypatch):
    def refused(request, timeout):
        raise voice.urllib.error.HTTPError(request.full_url, 402, 'Payment Required', {}, None)

    monkeypatch.setattr(voice.urllib.request, 'urlopen', refused)
    said, notices = [], []
    fallback = SimpleNamespace(say=said.append)
    voice.FishVoice('test-key', fallback=fallback, notice=notices.append).say('Tests pass.')
    assert said == ['Tests pass.']
    assert '402' in notices[0] and 'test-key' not in notices[0]


def test_the_voice_is_fish_audio_only_with_a_key(monkeypatch):
    monkeypatch.delenv('FISH_API_KEY', raising=False)
    assert isinstance(voice.make_voice(), voice.WindowsVoice)
    monkeypatch.setenv('FISH_API_KEY', 'test-key')
    assert isinstance(voice.make_voice(), voice.FishVoice)


def test_the_speech_model_is_in_place():
    assert (voice.MODEL_DIR / 'model.bin').is_file()
