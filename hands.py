"""VERONICA's hands: a terminal on this PC, and processes she leaves running.

- run_command runs one command line through the shell (cmd.exe on Windows), waits for it, and gives back the exit
  code and the output (stdout and stderr together). It runs as the owner, in the folder asked for, with the owner's
  whole environment: git, pip, python, npm, builds, scripts, anything (owner, 2026-10-13: no gate on commands).
  The only things done to the output are a size cap and changes.hide_secrets, so a key she holds never comes back
  to the model in text.
- A command that outlives its timeout is stopped with its whole process tree, and she is told so.
- Processes keeps the programs she starts in the background (servers, long builds): each writes to its own log
  file, can be read while it runs, and can be stopped.
"""
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import changes

DEFAULT_TIMEOUT = 600
MAX_TIMEOUT = 6 * 3600
OUTPUT_LIMIT = 20_000


class HandsError(ValueError):
    """A command that could not even be started."""


def _popen(command, cwd, stdout):
    """The command started through the shell in its own process group, so kill_tree can stop all of it."""
    if sys.platform == 'win32':
        return subprocess.Popen(command, shell=True, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=stdout,
                                stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    return subprocess.Popen(command, shell=True, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=stdout,
                            stderr=subprocess.STDOUT, start_new_session=True)


def kill_tree(pid):
    """Stop a process and everything it started."""
    if sys.platform == 'win32':
        subprocess.run(['taskkill', '/T', '/F', '/PID', str(pid)], capture_output=True)
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def _decode(data):
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return data.decode('mbcs' if sys.platform == 'win32' else 'utf-8', errors='replace')


def clip(text, limit=OUTPUT_LIMIT):
    """Output for the model: secrets hidden, the middle left out when it is very long."""
    text = changes.hide_secrets(text)
    if len(text) > limit:
        half = limit // 2
        text = text[:half] + f'\n... ({len(text) - limit} characters left out) ...\n' + text[-half:]
    return text.strip()


def _check(command, cwd):
    command = str(command or '').strip()
    if not command:
        raise HandsError('Give a command to run.')
    cwd = Path(cwd)
    if not cwd.is_dir():
        raise HandsError(f'There is no folder {cwd}.')
    return command, cwd


def run_command(command, cwd, timeout=DEFAULT_TIMEOUT):
    """(exit code, output) of a command run to its end; (None, output) when it was stopped at the timeout."""
    command, cwd = _check(command, cwd)
    timeout = max(1, min(MAX_TIMEOUT, int(timeout or DEFAULT_TIMEOUT)))
    process = _popen(command, cwd, subprocess.PIPE)
    try:
        out, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(process.pid)
        out, _ = process.communicate()
        return None, (clip(_decode(out or b'')) + f'\n[stopped after {timeout} seconds; it was still running]').strip()
    return process.returncode, clip(_decode(out or b''))


class Processes:
    """Programs VERONICA started in the background, by id."""

    def __init__(self):
        self._entries = {}

    def start(self, command, cwd):
        command, cwd = _check(command, cwd)
        process_id = uuid.uuid4().hex[:8]
        log = tempfile.NamedTemporaryFile(prefix='veronica-process-', suffix='.log', delete=False)
        try:
            process = _popen(command, cwd, log)
        finally:
            log.close()
        self._entries[process_id] = {'process': process, 'log': log.name, 'command': command, 'cwd': str(cwd),
                                     'started': time.time(), 'stopped': False}
        return process_id

    def _entry(self, process_id):
        entry = self._entries.get(str(process_id).strip())
        if entry is None:
            raise HandsError(f'There is no background process {process_id}.')
        return entry

    def state(self, process_id):
        entry = self._entry(process_id)
        code = entry['process'].poll()
        if code is None:
            return 'still running'
        if entry['stopped']:
            return 'stopped by VERONICA'
        return f'finished with exit code {code}'

    def output(self, process_id, limit=OUTPUT_LIMIT):
        entry = self._entry(process_id)
        try:
            text = _decode(Path(entry['log']).read_bytes())
        except OSError:
            text = ''
        head = f'Process {process_id} ({entry["command"][:80]}): {self.state(process_id)}.'
        return head + ('\n' + clip(text, limit) if text.strip() else '\n(no output yet)')

    def stop(self, process_id):
        entry = self._entry(process_id)
        if entry['process'].poll() is not None:
            return f'Process {process_id} had already ended ({self.state(process_id)}).'
        entry['stopped'] = True
        kill_tree(entry['process'].pid)
        try:
            entry['process'].wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        return f'Stopped process {process_id} ({entry["command"][:80]}).'

    def list(self):
        if not self._entries:
            return 'No background processes.'
        return '\n'.join(f'{pid} | {self.state(pid)} | {e["command"][:80]} | in {e["cwd"]}'
                         for pid, e in self._entries.items())
