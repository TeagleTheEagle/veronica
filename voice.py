"""Talking to VERONICA the way the owner talks to JARVIS: say "Veronica, ..." and VERONICA answers out loud.

- Listening: the microphone is read in memory (never recorded to disk). A simple loudness check cuts it into
  utterances: speech starts when it gets louder than the room, and ends after 1.3 seconds of quiet (at most 30
  seconds), as in JARVIS.
- Understanding: each utterance is transcribed on this PC by the local Whisper model in models/base.en (the same
  model JARVIS uses; no cloud). Only utterances that start with the name ("Veronica", "Hey Veronica") are acted on;
  "Veronica" alone gets "Yes?" and the next 7 seconds need no name.
- Speaking: Fish Audio's voice when FISH_API_KEY is set (the reply text is sent to Fish Audio to be spoken), else
  Windows' own voice. If Fish Audio fails, the screen says why and Windows' voice says that reply.
"""
import ctypes
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import safety

RATE = 16000
FRAME = 1600                 # 100 ms
END_SILENCE = 13             # frames of quiet that end an utterance (1.3 s)
MAX_FRAMES = 300             # 30 s
MIN_SPEECH = 4               # frames of speech for it to count (0.4 s)
PRE_ROLL = 3                 # frames kept from before speech started
FOLLOW_UP = 7.0              # seconds after "Yes?" in which no name is needed
MODEL_DIR = safety.HERE / 'models' / 'base.en'
FISH_URL = 'https://api.fish.audio/v1/tts'
NAMES = ('veronica', 'veronika', 'verronica', 'veronicka', 'varonica')
GREETINGS = ('hey', 'hi', 'hello', 'ok', 'okay', 'yo')
GOODBYE = {'goodbye', 'good bye', 'bye', 'stop listening', 'shut down', 'go to sleep'}


class VoiceError(RuntimeError):
    """Said as it is."""


# ----- hearing ---------------------------------------------------------------------------------------------------

def parse_wake(text):
    """(woke, command): woke when the words start with the name, optionally after a greeting."""
    words = re.sub(r"[^\w\s']", ' ', text.lower()).split()
    if words and words[0] in GREETINGS:
        words = words[1:]
    if not words or words[0] not in NAMES:
        return False, ''
    name = re.search(r'(?i)\b(?:' + '|'.join(NAMES) + r')\b[\s,.:;!?-]*', text)
    return True, text[name.end():].strip() if name else ''


class Segmenter:
    """Cuts 100 ms frames of 16 kHz int16 audio into utterances by loudness."""

    def __init__(self, calibration_frames=10):
        self.calibration = []
        self.calibration_frames = calibration_frames
        self.threshold = None
        self.reset()

    def reset(self):
        self.recent, self.speech, self.quiet, self.loud = [], [], 0, 0

    def feed(self, frame):
        """Returns a finished utterance (numpy int16 array) or None."""
        import numpy as np
        level = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2))) if len(frame) else 0.0
        if self.threshold is None:
            self.calibration.append(level)
            if len(self.calibration) >= self.calibration_frames:
                self.threshold = max(300.0, 3.0 * float(np.median(self.calibration)))
            return None
        if not self.speech:
            if level > self.threshold:
                self.speech, self.quiet, self.loud, self.recent = self.recent + [frame], 0, 1, []
            else:
                self.recent = (self.recent + [frame])[-PRE_ROLL:]
            return None
        self.speech.append(frame)
        if level > self.threshold:
            self.loud += 1
            self.quiet = 0
        else:
            self.quiet += 1
        if self.quiet >= END_SILENCE or len(self.speech) >= MAX_FRAMES:
            done, loud = self.speech, self.loud
            self.reset()
            if loud >= MIN_SPEECH:
                return np.concatenate(done)
        return None


class Microphone:
    """The default microphone, read in memory. pause() drops what it hears (while VERONICA works or speaks)."""

    def __init__(self):
        import queue
        import sounddevice
        self.frames = queue.Queue()
        self.paused = False
        try:
            self.stream = sounddevice.InputStream(samplerate=RATE, channels=1, dtype='int16', blocksize=FRAME,
                                                  callback=self._heard)
            self.stream.start()
        except Exception as error:  # PortAudio raises its own types
            raise VoiceError(f'The microphone could not be opened: {error}') from None

    def _heard(self, data, frames, when, status):
        if not self.paused:
            self.frames.put(data[:, 0].copy())

    def pause(self):
        self.paused = True

    def resume(self):
        import queue
        while True:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                break
        self.paused = False

    def read(self, timeout=0.5):
        import queue
        try:
            return self.frames.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self):
        self.stream.stop()
        self.stream.close()


class Transcriber:
    def __init__(self, directory=MODEL_DIR):
        if not (Path(directory) / 'model.bin').is_file():
            raise VoiceError(f'The speech model is missing from {directory}. See the README.')
        from faster_whisper import WhisperModel
        self.model = WhisperModel(str(directory), device='cpu', compute_type='int8', cpu_threads=4,
                                  local_files_only=True)

    def transcribe(self, audio):
        import numpy as np
        samples = audio.astype(np.float32) / 32768.0
        segments, _ = self.model.transcribe(samples, language='en', beam_size=5, initial_prompt='Veronica.',
                                            condition_on_previous_text=False, vad_filter=False)
        return ' '.join(segment.text.strip() for segment in segments if not likely_noise(segment)).strip()


def likely_noise(segment):
    """Whisper sometimes 'hears' words in a cough or a door closing: such segments are unsure on both counts."""
    return getattr(segment, 'no_speech_prob', 0) > 0.6 and getattr(segment, 'avg_logprob', 0) < -1.0


# ----- speaking --------------------------------------------------------------------------------------------------

def speech_text(text, limit=600):
    """What is said out loud: no code blocks, no markdown marks, not too long."""
    text = re.sub(r'```.*?(```|$)', ' (the details are on screen) ', text, flags=re.S)
    text = re.sub(r'`([^`]*)`', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'^\s*(#+|[-*+]|\d+\.)\s+', '', text, flags=re.M)
    text = re.sub(r'[*_#>|]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) > limit:
        cut = text[:limit]
        end = max(cut.rfind('. '), cut.rfind('! '), cut.rfind('? '))
        text = (cut[:end + 1] if end > limit // 2 else cut.rsplit(' ', 1)[0] + '.') + ' The rest is on screen.'
    return text


class WindowsVoice:
    """Windows' own speech (System.Speech), offline. The text goes in on stdin and the file name in the environment,
    never on a command line."""
    name = 'Windows voice'

    def __init__(self, hint=None):
        self.hint = hint or os.environ.get('VERONICA_WINDOWS_VOICE', 'Zira')

    def _speak(self, text, wav=None):
        script = ('[Console]::InputEncoding = [Text.Encoding]::UTF8; Add-Type -AssemblyName System.Speech; '
                  '$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; '
                  '$hint = $env:VERONICA_VOICE_HINT; '
                  '$v = $s.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Name -like "*$hint*" } '
                  '| Select-Object -First 1; '
                  'if ($v) { $s.SelectVoice($v.VoiceInfo.Name) }; '
                  'if ($env:VERONICA_WAV) { $s.SetOutputToWaveFile($env:VERONICA_WAV) }; '
                  '$s.Speak([Console]::In.ReadToEnd()); $s.Dispose()')
        env = dict(os.environ, VERONICA_VOICE_HINT=self.hint, VERONICA_WAV=str(wav or ''))
        subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script], input=text,
                       text=True, encoding='utf-8', env=env, capture_output=True, timeout=180,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def say(self, text):
        if text and sys.platform == 'win32':
            self._speak(text)

    def audio(self, text):
        """The speech as WAV bytes (for the window, which plays it itself)."""
        if sys.platform != 'win32':
            raise VoiceError("Windows' voice works on Windows only.")
        with tempfile.TemporaryDirectory(prefix='veronica-voice-') as folder:
            wav = Path(folder) / 'say.wav'
            self._speak(text, wav)
            if not wav.is_file() or wav.stat().st_size < 100:
                raise VoiceError("Windows' voice made no sound.")
            return wav.read_bytes()


class FishVoice:
    """Fish Audio text to speech. The key is read from the environment and never printed."""
    name = 'Fish Audio'

    def __init__(self, key, voice_id=None, model=None, fallback=None, notice=print):
        self.key, self.voice_id = key, voice_id
        self.model = model or os.environ.get('FISH_MODEL') or 's2.1-pro'
        self.fallback = fallback or WindowsVoice()
        self.notice = notice

    def audio(self, text):
        body = {'text': text, 'format': 'mp3'}
        if self.voice_id:
            body['reference_id'] = self.voice_id
        request = urllib.request.Request(FISH_URL, data=json.dumps(body).encode('utf-8'), method='POST', headers={
            'Authorization': f'Bearer {self.key}', 'Content-Type': 'application/json', 'model': self.model})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            raise VoiceError(_fish_problem(error.code, self.model)) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise VoiceError("Fish Audio can't be reached (check the internet connection).") from None

    def say(self, text):
        if not text:
            return
        try:
            play_mp3(self.audio(text))
        except VoiceError as problem:
            self.notice(f'(voice: {problem} Windows\' voice is saying this one.)')
            self.fallback.say(text)


def speech_audio(voice, text, notice=print):
    """(sound bytes, MIME type) of `text` in `voice`. When Fish Audio fails, the notice says why and Windows' voice
    makes the sound."""
    if isinstance(voice, FishVoice):
        try:
            return voice.audio(text), 'audio/mpeg'
        except VoiceError as problem:
            notice(f"Voice: {problem} Windows' voice is saying this one.")
            voice = voice.fallback
    return voice.audio(text), 'audio/wav'


def _fish_problem(code, model):
    if code == 401:
        return 'Fish Audio refused the key (401): check FISH_API_KEY.'
    if code == 402:
        return (f'Fish Audio says payment is needed for the model {model} (402). On the free plan, set '
                'FISH_MODEL=s2.1-pro-free.')
    if code == 404:
        return 'Fish Audio does not know that voice (404): check FISH_VOICE_ID.'
    if code == 429:
        return 'Fish Audio is limiting requests (429).'
    if code >= 500:
        return f'Fish Audio is busy or down ({code}).'
    return f'Fish Audio rejected the request ({code}).'


def play_mp3(audio, limit=180):
    """Play MP3 bytes through Windows' own player (winmm), waiting until it has finished."""
    if sys.platform != 'win32':
        raise VoiceError('Playing sound works on Windows only.')
    if not audio or len(audio) < 100:
        raise VoiceError('Fish Audio sent no sound.')
    winmm = ctypes.windll.winmm
    buffer = ctypes.create_unicode_buffer(256)

    def command(text):
        if winmm.mciSendStringW(text, buffer, 256, 0):
            raise VoiceError('The PC could not play the sound.')
        return buffer.value

    alias = 'veronica' + uuid.uuid4().hex[:10]
    with tempfile.TemporaryDirectory(prefix='veronica-say-') as folder:
        path = Path(folder) / 'say.mp3'
        path.write_bytes(audio)
        command(f'open "{path}" type mpegvideo alias {alias}')
        try:
            command(f'play {alias}')
            deadline = time.monotonic() + limit
            while command(f'status {alias} mode') == 'playing' and time.monotonic() < deadline:
                time.sleep(0.1)
        finally:
            try:
                command(f'close {alias}')
            except VoiceError:
                pass


def make_voice(notice=print):
    key = os.environ.get('FISH_API_KEY', '').strip()
    if key:
        return FishVoice(key, os.environ.get('FISH_VOICE_ID', '').strip() or None, notice=notice)
    return WindowsVoice()


# ----- the conversation ------------------------------------------------------------------------------------------

def run(veronica, show=print):
    """Listen for "Veronica, ..." until Ctrl+C or "Veronica, goodbye"."""
    voice = make_voice(notice=show)
    if isinstance(voice, FishVoice):
        show('Voice: Fish Audio. What VERONICA says is sent to Fish Audio to be spoken.')
    else:
        show("Voice: Windows' own voice (set FISH_API_KEY for Fish Audio; see the README).")
    show('Loading the speech model (the first time can take a minute)...')
    transcriber = Transcriber()
    voice.say('VERONICA here.')  # before the microphone opens: the first second sets how quiet the room is
    microphone = Microphone()
    segmenter = Segmenter()
    show('Listening. Say "Veronica" and what you want. Say "Veronica, goodbye" or press Ctrl+C to stop.')
    awake_until = 0.0
    try:
        while True:
            frame = microphone.read()
            if frame is None:
                continue
            utterance = segmenter.feed(frame)
            if utterance is None:
                continue
            microphone.pause()
            try:
                heard = transcriber.transcribe(utterance)
                woke, command = parse_wake(heard) if heard else (False, '')
                if not woke and heard and time.monotonic() < awake_until:
                    woke, command = True, heard
                if not woke:
                    continue
                if not command:
                    voice.say('Yes?')
                    awake_until = time.monotonic() + FOLLOW_UP
                    continue
                awake_until = 0.0
                show(f'\nYou: {command}')
                if command.lower().strip(' .!?') in GOODBYE:
                    voice.say('Goodbye.')
                    return
                if not quick(command):
                    voice.say('On it.')
                reply = answer(veronica, command)
                if reply.show:
                    show(reply.show)
                show(f'VERONICA: {reply.say}')
                voice.say(speech_text(reply.say))
            finally:
                segmenter.reset()
                microphone.resume()
    except KeyboardInterrupt:
        show('\nStopped listening.')
    finally:
        microphone.close()


def quick(command):
    """True for keep, undo, show, send and discard, which are answered at once (no "On it." first)."""
    import agent
    said = agent.normalize(command)
    return any(said in words for words in (agent.KEEP, agent.UNDO, agent.SHOW, agent.SEND, agent.DISCARD_EMAIL,
                                            agent.DISCARD_ANY))


def answer(veronica, command):
    from agent import Reply, VeronicaError
    try:
        return veronica.respond(command)
    except VeronicaError as problem:
        return Reply(str(problem))
