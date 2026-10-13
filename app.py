"""VERONICA's window: one page in the layout the owner chose (splash, home, voice mode, tools, memory, profile and the
side menu), drawn by ui/index.html in a WebView2 window (pywebview). This file joins the page to VERONICA (agent.py)
and to the microphone and voice (voice.py).

- Home: type to VERONICA. With "Listen for 'Veronica'" on (Profile), saying "Veronica, ..." while the window is open
  opens voice mode and is answered, as with JARVIS.
- Voice mode works like ChatGPT's: no name needed. The orb swells with your voice, swirls while VERONICA thinks and
  pulses with VERONICA's voice; tap it to stop VERONICA speaking, or to pause listening.

Nothing listens once the window is closed. The microphone is read in memory only (voice.py). One worker thread does
all the listening and talking to VERONICA, so the page never waits on Claude.

    .venv\\Scripts\\pythonw.exe app.py      (what VERONICA.exe runs)
"""
import base64
import ctypes
import json
import logging
import os
import queue
import sys
import threading
import time
from pathlib import Path

import models
import paths
import safety
import voice as speech
from agent import Reply, ToolError, Veronica, VeronicaError

INFO_EVERY = 15            # seconds between refreshes of the task list on the page
MAIL_EVERY = 600           # seconds between unread-mail and today's-calendar counts (network)
BEAT_FIRST = 120           # seconds after the window opens before the first heartbeat may run
BEAT_POLL = 60             # seconds between "is a heartbeat due?" checks

HERE = Path(__file__).resolve().parent
PAGE = HERE / 'ui' / 'index.html'
ICON = HERE / 'launcher' / 'veronica.ico'
SETTINGS = paths.SETTINGS
LOG = paths.LOG
SPEAK_LIMIT = 180          # seconds to wait for the page to finish playing one reply
NOISE = {'', 'you', 'thank you', 'thanks', 'thanks for watching', 'bye', 'hmm', 'um', 'uh', 'mm', 'oh'}

log = logging.getLogger('veronica')


def is_noise(text):
    """Words Whisper often 'hears' in background sound; ignored when no name was said."""
    words = ' '.join(''.join(c for c in text.lower() if c.isalnum() or c.isspace()).split())
    return len(words) < 2 or words in NOISE


class Session:
    """Listening, VERONICA's answers and her speech, on one worker thread. `ui` is the page (WindowUI)."""

    def __init__(self, veronica, ui, voice=None, transcriber=None, microphone=None, settings_file=SETTINGS,
                 hearing=True):
        self.veronica, self.ui, self.voice = veronica, ui, voice
        self.hearing = hearing  # False: no speech model, no microphone (the smoke check)
        self.transcriber, self.microphone = transcriber, microphone
        self.settings_file = Path(settings_file)
        self.settings = {'listen_for_name': True}
        self.settings.update(self._load_settings())
        self.jobs = queue.Queue()
        self.spoken = threading.Event()
        self.stopping = threading.Event()
        self.voice_mode = False
        self.paused = False
        self.segmenter = speech.Segmenter()
        self._listening_now = None
        self._shown_state = None
        self._thread = None
        self._info_thread = None
        self._info_now = threading.Event()
        self._beat_thread = None
        self._beat_now = threading.Event()
        self.heartbeat = None      # goals.Heartbeat, made in start()
        self._shown_draft = None   # the id of the email draft the page shows now (what a spoken "send it" means)

    # ----- from the page (any thread) --------------------------------------------------------------------------

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name='veronica-worker', daemon=True)
            self._thread.start()
        if self._info_thread is None and self.hearing:
            self._info_thread = threading.Thread(target=self._info_loop, name='veronica-info', daemon=True)
            self._info_thread.start()
        if self._beat_thread is None and self.hearing:
            self._beat_thread = threading.Thread(target=self._beat_loop, name='veronica-heartbeat', daemon=True)
            self._beat_thread.start()
        import calendar_feed
        import gmail
        return {'listen_for_name': self.settings['listen_for_name'], 'model': models.label(models.chat_model()),
                'connections': {'gmail': gmail.configured(), 'calendar': calendar_feed.configured(),
                                'search': bool(os.environ.get('BRAVE_API_KEY', '').strip())}}

    def stop(self, *args):
        self.stopping.set()
        self.spoken.set()
        self._info_now.set()
        self._beat_now.set()

    # ----- goals, proposals and the heartbeat (goals.py) --------------------------------------------------------

    def _heartbeat(self):
        if self.heartbeat is None:
            import goals
            import health
            veronica = self.veronica
            self.heartbeat = goals.Heartbeat(
                veronica.store,
                lambda: Veronica(store=veronica.store, home=veronica.home, projects_file=veronica.projects_file,
                                 log=lambda line: log.info('heartbeat:%s', line)),
                health_check=lambda: health.check(store=veronica.store, root=veronica.home))
        return self.heartbeat

    def goals_panel(self):
        import goals
        return goals.panel(self.veronica.store, self._heartbeat())

    def decide(self, kind, item_id, action):
        """The owner pressed Approve / Decline / Pause / Resume / Drop / Done in the Goals panel."""
        import goals
        done, said = goals.decide(self.veronica.store, kind, int(item_id), action)
        if done:
            self.veronica.notes.append(f'[Owner] {said}')
            if action == 'approve':
                self._beat_now.set()        # approved work starts at the next heartbeat check, not in an hour
        self.ui.notice(said)
        self._info_now.set()
        return done

    def add_goal(self, title):
        import goals
        try:
            goal, said = goals.add_owner_goal(self.veronica.store, title)
        except ValueError as problem:
            self.ui.notice(str(problem))
            return None
        self.veronica.notes.append(f'[Owner] added goal {goal["id"]}: {goal["title"]}.')
        self.ui.notice(said)
        self._info_now.set()
        return goal['id']

    def heartbeat_settings(self, on=None, minutes=None, review=None):
        return self._heartbeat().set_config(on=on, minutes=minutes, review=review)

    def beat_now(self):
        self._beat_now.set()

    def _beat_loop(self):
        """VERONICA's own working time while the window is open: a heartbeat whenever one is due (goals.py)."""
        self._beat_now.wait(BEAT_FIRST)
        while not self.stopping.is_set():
            asked = self._beat_now.is_set()
            self._beat_now.clear()
            try:
                beat = self._heartbeat()
                if asked or beat.due():
                    self._beat()
            except Exception:
                log.exception('heartbeat')
            self._beat_now.wait(BEAT_POLL)

    def _beat(self):
        beat = self._heartbeat()
        self._info_now.set()
        result = beat.beat()
        log.info('heartbeat: %s', result['summary'])
        if result['notify']:
            self.ui.notice('Heartbeat: ' + result['summary'][:300])
            self.veronica.notes.append(f'[VERONICA heartbeat] {result["summary"][:1500]}')
        self._info_now.set()
        return result

    # ----- research tasks, memory and suggestions (any thread; the store is thread-safe) -----------------------

    def task_list(self):
        return [_task_row(t) for t in self.veronica.store.list_tasks(limit=15) if t['kind'] == 'research']

    def task_detail(self, task_id):
        import tasks
        task = self.veronica.store.get_task(str(task_id))
        if task is None:
            return None
        row = _task_row(task)
        row['events'] = [{'at': e['at'], 'message': e['message']} for e in self.veronica.store.events(task['id'])[-30:]]
        row['text'] = tasks.describe(task)
        return row

    def start_research(self, topic, request_id=None):
        import tasks
        try:
            task = tasks.start_research(self.veronica.store, topic, request_id=request_id or None)
        except ValueError as problem:
            self.ui.notice(str(problem))
            return None
        try:
            tasks.ensure_worker(self.veronica.store)
        except OSError:
            log.exception('worker not started')
            self.ui.notice('The research worker could not be started; the task waits in the queue.')
        self._info_now.set()
        return task['id']

    def cancel_task(self, task_id):
        task = self.veronica.store.request_cancel(str(task_id))
        self._info_now.set()
        return task['status'] if task else None

    def open_report(self, task_id):
        """Open a saved report in the PC's default app, only if it is in VERONICA's reports folder."""
        import citations
        task = self.veronica.store.get_task(str(task_id))
        path = ((task or {}).get('result') or {}).get('report')
        if not path or not safety.inside(path, citations.REPORTS) or not Path(path).is_file():
            self.ui.notice('That report is not available.')
            return False
        os.startfile(path)  # noqa: S606 (a file VERONICA wrote, in her own reports folder)
        return True

    def memory_list(self):
        return [{'id': m['id'], 'kind': m['kind'], 'content': m['content'], 'provenance': m['provenance'],
                 'updated_at': m['updated_at']} for m in self.veronica.store.memories()]

    def forget_memory(self, memory_id):
        return self.veronica.store.forget(int(memory_id))

    def edit_memory(self, memory_id, content):
        content = ' '.join(str(content or '').split())[:500]
        if len(content) < 3:
            return False
        return self.veronica.store.update_memory(int(memory_id), content, 'the owner, edited in the window on '
                                                 + time.strftime('%Y-%m-%d %H:%M')) is not None

    def _info_loop(self):
        """Keep the page's task list and suggestions fresh: tasks often, mail and calendar counts now and then."""
        mail_at = 0.0
        self.startup_check()
        while not self.stopping.is_set():
            info = {'tasks': [], 'pending': self.veronica.status()}
            try:
                info['tasks'] = self.task_list()
                info['memories'] = len(self.veronica.store.memories())
                import goals
                info['waiting'] = len(goals.waiting(self.veronica.store))
                info['beating'] = bool(self.heartbeat and self.heartbeat.running)
            except Exception:
                log.exception('task list')
            if time.time() - mail_at > MAIL_EVERY:
                mail_at = time.time()
                info.update(self._counts())
            self.ui.info(info)
            self._info_now.wait(INFO_EVERY)
            self._info_now.clear()

    def startup_check(self):
        """Her health check when the window opens (health.py): it starts a worker for waiting tasks, and the page is
        told only when something is wrong. Returns the report, or None when the check itself failed."""
        import health
        try:
            report = health.check(store=self.veronica.store, root=self.veronica.home)
        except Exception:
            log.exception('health check')
            return None
        log.info('health check: %s\n%s', report.summary(), report.text())
        bad = [c for c in report.checks if c.ok is False]
        open_repairs = [c for c in report.checks if c.name == 'repairs' and c.ok is None]
        if bad:
            self.ui.notice('Health check: ' + '; '.join(f'{c.name}: {c.detail}' for c in bad))
        if open_repairs:
            self.ui.notice(f'A repair of my own code was left open ({open_repairs[0].detail}). Ask me to finish it or '
                           'roll it back.')
        return report

    def _counts(self):
        import datetime
        import calendar_feed
        import gmail
        counts = {}
        if gmail.configured():
            try:
                with gmail.Mailbox(self.veronica.imap_connect) as box:
                    counts['unread'] = box.unread_count()
            except gmail.MailError:
                counts['unread'] = None
        if calendar_feed.configured():
            try:
                now = datetime.datetime.now().astimezone()
                end = now.replace(hour=23, minute=59, second=59)
                rows, _ = calendar_feed.events_between(now, end, opener=self.veronica.calendar_opener)
                counts['events_today'] = len(rows)
            except calendar_feed.CalendarError:
                counts['events_today'] = None
        return counts

    def set_voice_mode(self, on):
        self.voice_mode, self.paused = bool(on), False

    def set_paused(self, paused):
        self.paused = bool(paused)

    def set_setting(self, name, value):
        if name in self.settings:
            self.settings[name] = value
            try:
                self.settings_file.write_text(json.dumps(self.settings, indent=2), encoding='utf-8')
            except OSError:
                log.exception('settings not saved')

    def saved_projects(self):
        try:
            saved = self.veronica._projects()
        except ToolError:
            return []
        return [{'name': name, 'folder': entry['folder']} for name, entry in sorted(saved.items())]

    # ----- the worker ------------------------------------------------------------------------------------------

    def _run(self):
        try:
            if self.voice is None:
                self.voice = speech.make_voice(notice=self.ui.notice)
            self.ui.voice_name(self.voice.name)
            if self.transcriber is None and self.hearing:
                try:
                    self.transcriber = speech.Transcriber()
                except Exception as problem:  # model missing, or the speech library failed to load
                    log.exception('speech model')
                    self.ui.notice(f'Voice is off: {problem}')
            if self.microphone is None and self.transcriber is not None:
                try:
                    self.microphone = speech.Microphone()
                    self.microphone.pause()
                except speech.VoiceError as problem:
                    self.ui.notice(str(problem))
            self.ui.hearing_ready(self.transcriber is not None and self.microphone is not None)
            while not self.stopping.is_set():
                self.step()
        except Exception:
            log.exception('worker stopped')
            self.ui.notice('Something went wrong inside VERONICA. The details are in veronica.log.')
        finally:
            if self.microphone is not None:
                try:
                    self.microphone.close()
                except Exception:
                    pass

    def step(self):
        """One turn of the worker: a job from the page, or 100 ms of microphone."""
        try:
            kind, value = self.jobs.get_nowait()
        except queue.Empty:
            kind = None
        if kind is not None:
            if self.microphone is not None:   # nothing heard while VERONICA works is acted on afterwards
                self.microphone.pause()
                self._listening_now = False
            self._do(kind, value)
            self._sync()
            return
        if not self._sync():
            time.sleep(0.05)
            return
        frame = self.microphone.read(timeout=0.1)
        if frame is None:
            return
        self.ui.level(min(1.0, float((frame.astype('float32') ** 2).mean() ** 0.5) / 3000.0))
        utterance = self.segmenter.feed(frame)
        if utterance is None:
            return
        self.microphone.pause()
        self._listening_now = False
        try:
            if self.voice_mode:
                self._state('thinking')
            self.heard(self.transcriber.transcribe(utterance))
        finally:
            self.segmenter.reset()
            self._sync()

    def _wanted(self):
        if self.microphone is None or self.transcriber is None:
            return False
        if self.voice_mode:
            return not self.paused
        return bool(self.settings['listen_for_name'])

    def _sync(self):
        """Open or close the microphone to match the page, and tell the page. True while listening."""
        wanted = self._wanted()
        if wanted != self._listening_now:
            if self.microphone is not None:
                (self.microphone.resume if wanted else self.microphone.pause)()
            self.segmenter.reset()
            self._listening_now = wanted
        self._state(('listening' if wanted else 'paused') if self.voice_mode else ('idle' if wanted else 'off'))
        return wanted

    def _state(self, name):
        if name != self._shown_state:
            self._shown_state = name
            self.ui.state(name)

    def heard(self, text):
        """What the microphone heard, as text."""
        text = (text or '').strip()
        if not text:
            return
        woke, command = speech.parse_wake(text)
        if woke:
            if not self.voice_mode:
                self.voice_mode, self.paused = True, False
                self.ui.voice_mode(True)
            if not command:
                self._say('Yes?')
                return
            self._answer(command, shown=self._shown_draft)
        elif self.voice_mode and not is_noise(text):
            self._answer(text, shown=self._shown_draft)

    def _do(self, kind, value):
        if kind == 'text':
            text, shown = (value.get('text', ''), value.get('shown')) if isinstance(value, dict) else (value, None)
            self._answer(text, typed=True, shown=shown)
        elif kind in ('send_email', 'discard_email'):
            act = self.veronica.send if kind == 'send_email' else self.veronica.discard
            self._reply(act(value or None))
        elif kind == 'new_chat':
            self.veronica.new_chat()
            self.ui.status(self.veronica.status())
        elif kind == 'open':
            try:
                said = self.veronica.open(value)
            except (safety.SafetyError, ToolError) as problem:
                self.ui.notice(str(problem))
                return
            self.veronica.notes.append(f'[VERONICA] The owner opened {self.veronica.project}. '
                                       f'Test command: {self.veronica.test_command or "none found"}.')
            self.ui.message('veronica', f'Opened {self.veronica.project.name}. What should I do in it?', said)
            self.ui.status(self.veronica.status())

    def _answer(self, text, typed=False, shown=None):
        if not typed and text.lower().strip(' .!?') in speech.GOODBYE:
            self._say('Goodbye.')
            self.voice_mode = False
            self.ui.voice_mode(False)
            return
        if not typed:
            self.ui.message('you', text)
        self._state('thinking')
        try:
            reply = self.veronica.respond(text, shown_draft=shown)
        except VeronicaError as problem:
            reply = Reply(str(problem))
        except Exception:
            log.exception('respond')
            reply = Reply('Something went wrong inside me. The details are in veronica.log.')
        self._reply(reply)

    def _reply(self, reply):
        status = self.veronica.status()
        self.ui.message('veronica', reply.say, reply.show)
        self.ui.status(status)
        self._shown_draft = (status.get('draft') or {}).get('id')
        if self.voice_mode:
            self._say(reply.say)

    def _say(self, text):
        words = speech.speech_text(text)
        if not words or self.voice is None:
            return
        if self.microphone is not None:
            self.microphone.pause()
            self._listening_now = False
        try:
            self._state('thinking')
            audio, mime = speech.speech_audio(self.voice, words, notice=self.ui.notice)
            self.spoken.clear()
            self.ui.speak(base64.b64encode(audio).decode('ascii'), mime)
            self.spoken.wait(timeout=SPEAK_LIMIT)
        except speech.VoiceError as problem:
            self.ui.notice(str(problem))
        finally:
            self.segmenter.reset()

    def _load_settings(self):
        try:
            data = json.loads(self.settings_file.read_text(encoding='utf-8'))
            return {k: v for k, v in data.items() if k in ('listen_for_name',)} if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}


class WindowUI:
    """Python -> page: each call runs one function of window.veronica in the page."""

    def __init__(self, window):
        self.window = window

    def _js(self, name, *args):
        try:
            self.window.run_js(f'window.veronica && veronica.{name}({",".join(json.dumps(a) for a in args)})')
        except Exception:
            log.debug('page call %s failed', name, exc_info=True)

    def state(self, name):
        self._js('state', name)

    def level(self, value):
        self._js('level', round(value, 3))

    def message(self, who, text, show=''):
        self._js('message', who, text, show)

    def status(self, status):
        self._js('status', status)

    def activity(self, line):
        self._js('activity', line.strip())

    def speak(self, b64, mime):
        self._js('speak', b64, mime)

    def notice(self, text):
        self._js('notice', text)

    def voice_mode(self, on):
        self._js('voiceMode', on)

    def hearing_ready(self, ready):
        self._js('hearingReady', ready)

    def info(self, info):
        self._js('info', info)

    def voice_name(self, name):
        self._js('voiceName', name)


class Bridge:
    """Page -> Python (window.pywebview.api). Only these methods are reachable from the page."""

    def __init__(self, session):
        self._session = session
        self._window = None

    def start(self):
        return self._session.start()

    def send(self, text, shown_draft=None):
        text = str(text or '').strip()[:4000]
        if text:
            self._session.jobs.put(('text', {'text': text, 'shown': str(shown_draft) if shown_draft else None}))

    def keep(self):
        self._session.jobs.put(('text', 'keep'))

    def undo(self):
        self._session.jobs.put(('text', 'undo'))

    def send_email(self, draft_id=None):
        """The Send button: sends only the draft whose id the page showed (None: refused, nothing is sent)."""
        self._session.jobs.put(('send_email', str(draft_id or 'none')))

    def discard_email(self, draft_id=None):
        self._session.jobs.put(('discard_email', str(draft_id or 'none')))

    def new_chat(self):
        self._session.jobs.put(('new_chat', None))

    def open_project(self, name):
        name = str(name or '').strip()
        if name:
            self._session.jobs.put(('open', name))

    def projects(self):
        return self._session.saved_projects()

    def voice_mode(self, on):
        self._session.set_voice_mode(on)

    def pause(self, paused):
        self._session.set_paused(paused)

    def speech_done(self):
        self._session.spoken.set()

    def listen_for_name(self, on):
        self._session.set_setting('listen_for_name', bool(on))

    def tasks(self):
        return self._session.task_list()

    def task(self, task_id):
        return self._session.task_detail(task_id)

    def start_research(self, topic, request_id=None):
        return self._session.start_research(str(topic or ''), str(request_id or '')[:64])

    def cancel_task(self, task_id):
        return self._session.cancel_task(task_id)

    def open_report(self, task_id):
        return self._session.open_report(task_id)

    def memories(self):
        return self._session.memory_list()

    def forget_memory(self, memory_id):
        return self._session.forget_memory(memory_id)

    def edit_memory(self, memory_id, content):
        return self._session.edit_memory(memory_id, content)

    def refresh(self):
        self._session._info_now.set()

    def goals(self):
        return self._session.goals_panel()

    def decide(self, kind, item_id, action):
        kind, action = str(kind or ''), str(action or '')
        if kind not in ('goal', 'proposal'):
            return False
        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            return False
        return self._session.decide(kind, item_id, action)

    def add_goal(self, title):
        return self._session.add_goal(str(title or '').strip()[:200])

    def heartbeat_settings(self, on=None, minutes=None, review=None):
        return self._session.heartbeat_settings(on=None if on is None else bool(on),
                                                minutes=None if minutes is None else int(minutes),
                                                review=None if review is None else bool(review))

    def beat_now(self):
        self._session.beat_now()

    def about(self):
        return {'folder': str(paths.HOME), 'version': paths.VERSION, 'model': models.label(models.chat_model()),
                'voice': getattr(self._session.voice, 'name', '')}

    def quit(self):
        self._session.stop()
        if self._window is not None:
            self._window.destroy()


def _task_row(task):
    progress = task.get('progress') or {}
    todos = progress.get('todos') or []
    result = task.get('result') or {}
    return {'id': task['id'], 'title': task['title'], 'status': task['status'], 'created_at': task['created_at'],
            'steps': progress.get('model_calls', 0), 'cost': round(task.get('cost_usd') or 0, 2),
            'now': progress.get('last_step') if task['status'] == 'running' else None,
            'plan_done': sum(1 for x in todos if x.get('status') == 'completed'), 'plan_total': len(todos),
            'report': bool(result.get('report')), 'error': task.get('error'),
            'quotes_checked': result.get('quotes_checked'), 'sources': len(result.get('sources_cited') or [])}


def _set_icon(window):
    """The window and taskbar show VERONICA's icon, not Python's."""
    try:
        import clr  # noqa: F401  (pythonnet, installed with pywebview)
        from System import Action
        from System.Drawing import Icon
        form = window.native
        form.Invoke(Action(lambda: setattr(form, 'Icon', Icon(str(ICON)))))
    except Exception:
        log.debug('icon not set', exc_info=True)


def main(argv=None):
    smoke = '--smoke' in (sys.argv[1:] if argv is None else argv)
    if LOG.is_file() and LOG.stat().st_size > 1_000_000:
        LOG.write_text('', encoding='utf-8')
    logging.basicConfig(filename=LOG, level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    for noisy in ('faster_whisper', 'httpx', 'httpx2', 'anthropic'):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    import veronica as start_script
    start_script.load_env()
    os.environ.setdefault('WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS', '--autoplay-policy=no-user-gesture-required')
    if sys.platform == 'win32':
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('VERONICA.Agent')
        except Exception:
            pass
    import webview

    ui = WindowUI(None)
    agent = Veronica(spoken=True, log=lambda line: ui.activity(line))
    session = Session(agent, ui, voice=speech.WindowsVoice() if smoke else None, hearing=not smoke)
    bridge = Bridge(session)
    window = webview.create_window('Veronica', html=PAGE.read_text(encoding='utf-8'), js_api=bridge,
                                   width=1320, height=860, min_size=(380, 640), background_color='#05060A',
                                   hidden=smoke)
    ui.window = bridge._window = window
    window.events.closed += session.stop
    window.events.shown += lambda: _set_icon(window)
    if smoke:
        window.events.loaded += lambda: threading.Thread(target=_smoke, args=(window, session), daemon=True).start()
    log.info('window opened')
    webview.start(private_mode=True)
    session.stop()


def _smoke(window, session):
    """--smoke: the page loads, reaches Python, and shows Home. No microphone, no speech model, no Claude."""
    time.sleep(4)
    try:
        found = window.evaluate_js("JSON.stringify({home: document.querySelector('#home').classList.contains('active'),"
                                   " api: !!(window.pywebview && window.pywebview.api && window.pywebview.api.start),"
                                   " veronica: typeof window.veronica.message})")
        print('page:', found, '| python worker started:', session._thread is not None, flush=True)
    except Exception as problem:
        print('smoke failed:', problem, flush=True)
    finally:
        window.destroy()


if __name__ == '__main__':
    main()
