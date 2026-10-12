"""Tests for the window's worker (app.py): when VERONICA listens, what she answers, and when she speaks. The page,
the microphone, the speech model, the voice and Claude are fakes."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

import app
import voice
from agent import Veronica
from test_veronica import FakeClaude, message, project, text  # noqa: F401  (project is a fixture)


class FakeUI:
    def __init__(self):
        self.calls = []
        self.session = None

    def __getattr__(self, name):
        def record(*args):
            self.calls.append((name, args))
            if name == 'speak' and self.session is not None:
                self.session.spoken.set()  # the page finished playing at once
        return record

    def named(self, name):
        return [args for called, args in self.calls if called == name]


class FakeVoice:
    name = 'Fake voice'

    def __init__(self):
        self.said = []

    def audio(self, words):
        self.said.append(words)
        return b'RIFF' + b'\x00' * 200


class FakeMicrophone:
    def __init__(self, frames=()):
        self.frames, self.paused, self.closed = list(frames), True, False

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def read(self, timeout=0.1):
        return self.frames.pop(0) if self.frames and not self.paused else None

    def close(self):
        self.closed = True


def make_session(tmp_path, *script, **kwargs):
    ui = FakeUI()
    veronica = Veronica(client=FakeClaude(*script), projects_file=tmp_path / 'none.json', spoken=True)
    session = app.Session(veronica, ui, voice=FakeVoice(), transcriber=SimpleNamespace(transcribe=lambda a: ''),
                          microphone=FakeMicrophone(), settings_file=tmp_path / 'settings.json', **kwargs)
    ui.session = session
    return session, ui


def test_at_home_only_her_name_wakes_her(tmp_path):
    session, ui = make_session(tmp_path, message(text('It is ten past ten.')))
    session.heard('what time is it')
    assert not ui.named('message') and not session.voice.said
    session.heard('Veronica, what time is it?')
    assert session.voice_mode and ui.named('voice_mode') == [(True,)]
    assert ui.named('message')[0] == ('you', 'what time is it?')
    assert ui.named('message')[1][:2] == ('veronica', 'It is ten past ten.')
    assert session.voice.said == ['It is ten past ten.']


def test_her_name_alone_gets_yes(tmp_path):
    session, ui = make_session(tmp_path)
    session.heard('Hey Veronica.')
    assert session.voice.said == ['Yes?'] and not ui.named('message')


def test_in_voice_mode_no_name_is_needed_but_noise_is_ignored(tmp_path):
    session, ui = make_session(tmp_path, message(text('Done.')))
    session.set_voice_mode(True)
    session.heard('Thank you.')
    session.heard('you')
    assert not ui.named('message')
    session.heard('open the calculator project')
    assert ui.named('message')[0] == ('you', 'open the calculator project')


def test_goodbye_leaves_voice_mode(tmp_path):
    session, ui = make_session(tmp_path)
    session.set_voice_mode(True)
    session.heard('goodbye')
    assert not session.voice_mode and ui.named('voice_mode') == [(False,)] and session.voice.said == ['Goodbye.']


def test_typed_messages_are_answered_in_text_only(tmp_path):
    session, ui = make_session(tmp_path, message(text('Hello.')))
    app.Bridge(session).send('  hi there  ')
    session.step()
    assert ui.named('message') == [('veronica', 'Hello.', '')]  # the page shows the typed words itself
    assert not session.voice.said


def test_the_microphone_follows_the_page(tmp_path):
    session, ui = make_session(tmp_path)
    session.step()
    assert not session.microphone.paused and ui.named('state')[-1] == ('idle',)
    session.set_setting('listen_for_name', False)
    session.step()
    assert session.microphone.paused and ui.named('state')[-1] == ('off',)
    session.set_voice_mode(True)
    session.step()
    assert not session.microphone.paused and ui.named('state')[-1] == ('listening',)
    session.set_paused(True)
    session.step()
    assert session.microphone.paused and ui.named('state')[-1] == ('paused',)
    assert json.loads((tmp_path / 'settings.json').read_text(encoding='utf-8')) == {'listen_for_name': False}


def test_a_heard_utterance_goes_through_speech_recognition(tmp_path):
    loud = (np.sin(np.arange(voice.FRAME)) * 6000).astype(np.int16)
    quiet = np.full(voice.FRAME, 10, dtype=np.int16)
    frames = [quiet] * 10 + [loud] * 8 + [quiet] * voice.END_SILENCE
    session, ui = make_session(tmp_path, message(text('Sure.')))
    session.microphone.frames = frames
    session.transcriber = SimpleNamespace(transcribe=lambda audio: 'Veronica, run the tests')
    for _ in range(len(frames) + 2):
        session.step()
    assert ('you', 'run the tests') in ui.named('message')
    assert session.voice.said == ['Sure.']


def test_settings_are_remembered(tmp_path):
    (tmp_path / 'settings.json').write_text(json.dumps({'listen_for_name': False, 'other': 1}), encoding='utf-8')
    session, _ = make_session(tmp_path)
    assert session.settings == {'listen_for_name': False}


def test_when_fish_audio_fails_windows_voice_makes_the_sound(monkeypatch):
    def refused(request, timeout):
        raise voice.urllib.error.HTTPError(request.full_url, 503, 'Busy', {}, None)

    monkeypatch.setattr(voice.urllib.request, 'urlopen', refused)
    notices = []
    fish = voice.FishVoice('test-key', fallback=FakeVoice())
    audio, mime = voice.speech_audio(fish, 'Hello.', notice=notices.append)
    assert mime == 'audio/wav' and audio.startswith(b'RIFF')
    assert '503' in notices[0] and 'test-key' not in notices[0]


def test_noise_words():
    assert app.is_noise('Thank you.') and app.is_noise('Um') and app.is_noise('')
    assert not app.is_noise('Thank you, now run the tests')


def test_quit_closes_the_window(tmp_path):
    session, _ = make_session(tmp_path)
    bridge = app.Bridge(session)
    closed = []
    bridge._window = SimpleNamespace(destroy=lambda: closed.append(True))
    bridge.quit()
    assert closed == [True] and session.stopping.is_set()


def test_the_page_can_only_reach_the_bridge_methods():
    public = sorted(name for name in dir(app.Bridge) if not name.startswith('_'))
    assert public == ['about', 'cancel_task', 'discard_email', 'edit_memory', 'forget_memory', 'keep',
                      'listen_for_name', 'memories', 'new_chat', 'open_project', 'open_report', 'pause', 'projects',
                      'quit', 'refresh', 'send', 'send_email', 'speech_done', 'start', 'start_research', 'task',
                      'tasks', 'undo', 'voice_mode']


def test_the_page_starts_research_once_per_click_and_opens_only_veronicas_reports(tmp_path, monkeypatch):
    import store as store_mod
    import tasks
    session, ui = make_session(tmp_path)
    session.veronica._store = store_mod.Store(tmp_path / 'veronica.db')
    monkeypatch.setattr(tasks, 'ensure_worker', lambda db: False)
    bridge = app.Bridge(session)
    first = bridge.start_research('heat pumps in cold climates', 'click-1')
    again = bridge.start_research('heat pumps in cold climates', 'click-1')
    assert first == again and len(bridge.tasks()) == 1
    assert bridge.task(first)['status'] == 'queued'
    assert bridge.cancel_task(first) == 'cancelled'
    opened = []
    monkeypatch.setattr(app.os, 'startfile', opened.append, raising=False)
    with session.veronica.store._tx() as conn:
        conn.execute("UPDATE tasks SET result = ? WHERE id = ?", ('{"report": "C:/Windows/win.ini"}', first))
    assert bridge.open_report(first) is False and opened == []
    memory = session.veronica.store.remember('preference', 'Short answers.', 'test')
    assert bridge.edit_memory(memory['id'], 'Very short answers.') and bridge.memories()[0]['content'] == 'Very short answers.'
    assert bridge.forget_memory(memory['id']) and bridge.memories() == []
