"""VERONICA's changes wait for the owner. Before VERONICA first writes a file, its original is kept here in memory, so
"undo" puts every file back as it was (and removes files and folders VERONICA created), and "keep" just forgets the
originals. Nothing here runs git or touches anything VERONICA did not write.

The project's tests are the check: run with the project's own test command, from the project folder, with keys and
tokens taken out of the environment."""
import difflib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import safety

SECRET_ENV = re.compile(r'KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|ANTHROPIC|FISH|GMAIL|CALENDAR|ICS|BRAVE|MAPS', re.I)
TEST_TIMEOUT = 600
OUTPUT_LIMIT = 6000


class ChangeSet:
    def __init__(self):
        self.originals = {}       # Path -> original bytes, or None when VERONICA created the file
        self.created_dirs = []    # folders VERONICA created, outermost first

    def before_write(self, path, root):
        path = Path(path)
        if path not in self.originals:
            self.originals[path] = path.read_bytes() if path.is_file() else None
        missing, parent = [], path.parent
        while not parent.exists() and safety.inside(parent, root):
            missing.append(parent)
            parent = parent.parent
        self.created_dirs.extend(reversed(missing))

    def touched(self):
        """Files whose content now differs from before VERONICA wrote them."""
        changed = []
        for path, original in self.originals.items():
            now = path.read_bytes() if path.is_file() else None
            if now != original:
                changed.append(path)
        return changed

    def pending(self):
        return bool(self.touched())

    def fingerprint(self):
        return tuple((str(p), p.read_bytes() if p.is_file() else None) for p in sorted(self.touched()))

    def diff(self, root, max_lines=200):
        lines = []
        for path in sorted(self.touched()):
            name = os.path.relpath(path, root)
            before = _text(self.originals[path])
            after = _text(path.read_bytes() if path.is_file() else None)
            lines.extend(difflib.unified_diff(before, after, fromfile=f'a/{name}', tofile=f'b/{name}', lineterm=''))
        if len(lines) > max_lines:
            lines = lines[:max_lines] + [f'... ({len(lines) - max_lines} more lines of changes)']
        return '\n'.join(lines)

    def undo(self):
        for path, original in self.originals.items():
            if original is None:
                if path.is_file():
                    path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(original)
        for folder in reversed(self.created_dirs):
            try:
                folder.rmdir()  # only if empty
            except OSError:
                pass
        self.keep()

    def keep(self):
        self.originals.clear()
        self.created_dirs.clear()


def _text(data):
    if data is None:
        return []
    return data.decode('utf-8', errors='replace').splitlines()


def clean_env():
    """The environment for the project's tests: no API keys, tokens or passwords."""
    return {k: v for k, v in os.environ.items() if not SECRET_ENV.search(k)}


def hide_secrets(text):
    """Test output with every key, password or secret address VERONICA holds replaced by [hidden]. The tests cannot
    see them in their environment, but code under test runs as the owner and could read them from disk."""
    values = set()
    for name, value in os.environ.items():
        if SECRET_ENV.search(name) and value and len(value.strip()) >= 6:
            values.add(value.strip())
            values.add(value.replace(' ', '').strip())
            values.update(part.strip() for part in value.split(',') if len(part.strip()) >= 6)
    for value in sorted(values, key=len, reverse=True):
        text = text.replace(value, '[hidden]')
    return text


def detect_test_command(root):
    """The project's test command when it is plain to see, else None."""
    root = Path(root)
    package = root / 'package.json'
    if package.is_file():
        try:
            script = json.loads(package.read_text(encoding='utf-8')).get('scripts', {}).get('test', '')
        except (ValueError, OSError, AttributeError):
            script = ''
        if script and 'no test specified' not in script:
            return 'npm test'
    if _has_python_tests(root):
        local = root / '.venv' / 'Scripts' / 'python.exe'
        if local.is_file():
            python = f'"{local}"'
        elif _is_her_own(root):
            import paths                   # her own code without a .venv (the installed app): the Python she runs on,
            python = f'"{paths.PYTHON}"'   # which has her packages; a bare "python" found the system one (proposal 2)
        else:
            python = 'python' if sys.platform == 'win32' else 'python3'
        return f'{python} -m pytest -q'
    return None


def _is_her_own(root):
    """True when the folder is her own program folder."""
    try:
        import paths
        return Path(root).resolve() == Path(paths.PROGRAM).resolve()
    except Exception:
        return False


def _has_python_tests(root, depth=3):
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in safety.SKIP_DIRS]
        if len(Path(current).relative_to(root).parts) >= depth:
            dirs[:] = []
        if any(f.endswith('.py') and (f.startswith('test_') or f.endswith('_test.py')) for f in files):
            return True
    return False


def run_tests(root, command, timeout=TEST_TIMEOUT):
    """(passed, output) for the project's tests. The command is the owner's, never the model's."""
    try:
        result = subprocess.run(command, shell=True, cwd=root, env=clean_env(), capture_output=True,
                                text=True, encoding='utf-8', errors='replace', timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f'The tests took longer than {timeout // 60} minutes and were stopped.'
    output = hide_secrets((result.stdout or '') + (result.stderr or ''))
    if len(output) > OUTPUT_LIMIT:
        output = '...\n' + output[-OUTPUT_LIMIT:]
    return result.returncode == 0, output.strip()
