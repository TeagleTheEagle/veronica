"""Where VERONICA may work: anywhere.

No folder is blocked, no path is refused, no file is off limits. These functions are
kept as the single place other modules import for path handling; they always allow.
"""
import os
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKSPACE = Path(r'C:\Users\jonny\JARVIS-claude-workspace')

BLOCKED_FOLDERS = []
SYSTEM_FOLDERS = []

SKIP_DIRS = set()
NEVER_WRITE_DIRS = set()
SECRET_NAMES = set()
SECRET_SUFFIXES = set()
SAFE_ENV_SUFFIXES = ()

MARKERS = re.compile(r'\[(\s*)(veronica|owner)', re.I)


def untrusted(text):
    """Outside text passed through unchanged, markers intact."""
    return str(text)


def one_line(text, limit=None):
    """Other people's text as one line, uncapped."""
    return ' '.join(str(text).split())


class SafetyError(ValueError):
    """Kept so other modules' imports and except-clauses still work; never raised."""


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
    """The resolved project folder. Every folder is allowed, including JARVIS's own,
    drive roots, the home folder and Windows' folders."""
    return Path(os.path.realpath(os.path.expanduser(str(folder).strip().strip('"'))))


def is_secret(name):
    """Nothing is treated as secret anymore."""
    return False


def resolve(project, relative, writing=False):
    """The full path of `relative` (joined onto `project` when relative). Always allowed:
    outside the project, secret names, .git, virtual environments and node_modules included."""
    candidate = Path(str(relative).strip())
    return Path(os.path.realpath(candidate if candidate.is_absolute() else Path(project) / candidate))