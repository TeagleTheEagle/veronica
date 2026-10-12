"""Where VERONICA may work. These rules are code, not just words in the prompt: every file tool goes through them.

- A project folder must not be, contain, or sit inside JARVIS's folders (the workspace, the installed JARVIS, the
  release and test folders), and no folder with "jarvis" in its path is ever opened. VERONICA does not write JARVIS.
- Drive roots, the home folder and Windows' own folders are refused.
- Inside a project, a path must stay inside it; secret files (.env, keys, credentials) are never read or written;
  .git, virtual environments and node_modules are never written.
"""
import os
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parent  # C:\Users\jonny\JARVIS-claude-workspace: JARVIS, FRIDAY, governance, reference, VERONICA

BLOCKED_FOLDERS = [
    WORKSPACE,
    Path(r'D:\JARVIS-release-work'),
    Path(r'D:\JARVIS-S6-FRESHVM'),
    Path(r'D:\JARVIS-stage7'),
]
if os.environ.get('LOCALAPPDATA'):
    BLOCKED_FOLDERS.append(Path(os.environ['LOCALAPPDATA']) / 'JARVIS')

SYSTEM_FOLDERS = [Path(os.environ.get(name, default)) for name, default in (
    ('SystemRoot', r'C:\Windows'),
    ('ProgramFiles', r'C:\Program Files'),
    ('ProgramFiles(x86)', r'C:\Program Files (x86)'),
    ('ProgramData', r'C:\ProgramData'),
)]

SKIP_DIRS = {'.git', '.hg', '.svn', '.venv', 'venv', 'env', 'node_modules', '__pycache__', '.pytest_cache',
             '.mypy_cache', '.tox', '.idea', '.vs'}
NEVER_WRITE_DIRS = {'.git', '.hg', '.svn', '.venv', 'venv', 'node_modules'}
SECRET_NAMES = {'.npmrc', '.pypirc', '.netrc', '.git-credentials', 'id_rsa', 'id_ed25519', 'id_ecdsa', 'id_dsa'}
SECRET_SUFFIXES = {'.pem', '.key', '.pfx', '.p12', '.kdbx', '.keystore', '.jks'}
SAFE_ENV_SUFFIXES = ('.example', '.sample', '.template')


MARKERS = re.compile(r'\[(\s*)(veronica|owner)', re.I)


def untrusted(text):
    """Text written by other people (emails, calendar entries, web pages). VERONICA's program marks its own facts with
    [VERONICA] and [Owner]; inside such text those markers are defused to (VERONICA and (Owner, so the text cannot pose
    as her program or the owner."""
    return MARKERS.sub(lambda m: '(' + m.group(1) + m.group(2), str(text))


def one_line(text, limit=300):
    """Other people's text as one defused line, cut at `limit` characters."""
    return untrusted(' '.join(str(text).split()))[:limit]


class SafetyError(ValueError):
    """Said to the owner (or to the model) as it is."""


def _norm(path):
    return os.path.normcase(os.path.realpath(str(path)))


def inside(child, parent):
    """True when child is parent or lies under it (case-insensitive on Windows, links resolved)."""
    child, parent = _norm(child), _norm(parent)
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:  # different drives
        return False


def check_project(folder):
    """The resolved project folder, or SafetyError saying why VERONICA may not work there."""
    path = Path(os.path.realpath(os.path.expanduser(str(folder).strip().strip('"'))))
    if not path.is_dir():
        raise SafetyError(f'There is no folder at {path}.')
    if any('jarvis' in part.lower() for part in path.parts):
        raise SafetyError('That folder belongs to JARVIS. VERONICA does not work on JARVIS.')
    for blocked in BLOCKED_FOLDERS:
        if inside(path, blocked):
            raise SafetyError('That folder is part of the JARVIS workspace. VERONICA does not work there.')
        if inside(blocked, path):
            raise SafetyError('That folder contains JARVIS. Choose the project folder itself, not a folder above it.')
    if path.parent == path:
        raise SafetyError('A whole drive is too wide. Choose the project folder itself.')
    if _norm(path) == _norm(Path.home()) or inside(Path.home(), path):
        raise SafetyError('Your whole home folder is too wide. Choose the project folder itself.')
    for system in SYSTEM_FOLDERS:
        if inside(path, system) or inside(system, path):
            raise SafetyError("That is one of Windows' own folders. VERONICA does not work there.")
    return path


def is_secret(name):
    lower = name.lower()
    if lower == '.env' or (lower.startswith('.env.') and not lower.endswith(SAFE_ENV_SUFFIXES)):
        return True
    if lower in SECRET_NAMES or Path(lower).suffix in SECRET_SUFFIXES:
        return True
    return 'secret' in lower or 'credential' in lower


def resolve(project, relative, writing=False):
    """The full path of `relative` inside `project`, or SafetyError."""
    if not isinstance(relative, str) or not relative.strip():
        raise SafetyError('A file path is needed.')
    candidate = Path(relative.strip())
    full = Path(os.path.realpath(candidate if candidate.is_absolute() else Path(project) / candidate))
    if not inside(full, project):
        raise SafetyError(f'{relative} is outside the open project. VERONICA only works inside it.')
    rel = os.path.relpath(_norm(full), _norm(project))
    parts = () if rel == '.' else Path(rel).parts
    if any(is_secret(part) for part in parts):
        raise SafetyError(f'{relative} looks like a secret file (keys, passwords). VERONICA never opens those.')
    if writing:
        if not parts:
            raise SafetyError('That is the project folder itself, not a file.')
        if any(part.lower() in NEVER_WRITE_DIRS for part in parts[:-1]):
            raise SafetyError(f'{relative} is inside .git, a virtual environment or node_modules. '
                              'VERONICA does not change those.')
    return full
