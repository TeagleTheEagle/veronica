"""Where VERONICA's program and her data live (owner 2026-10-14: "a real installed app ... her own folder in
AppData, updates like JARVIS").

Two layouts:
- The coding folder (this repository, run with .venv): program and data are the same folder, as before.
- The installed app (tools/build_release.py, VERONICA-Setup-<version>.exe):
      %LOCALAPPDATA%\\VERONICA\\                 HOME: her data, kept across updates and an uninstall
          .env, settings.json, projects.json, veronica.log, data\\, reports\\
          VERONICA.exe                          the launcher (reads current.json)
          current.json                          {"release": id, "previous": id}
          releases\\<id>\\app\\                   PROGRAM: this file's folder
          releases\\<id>\\python\\                 her own Python (3.12.10 embeddable + her packages)
          releases\\<id>\\models\\                 the speech model
  A release is recognised by app\\installed-release.json, which only the build writes.
VERONICA_HOME overrides HOME (sandbox runs of a built release).
"""
import json
import os
import sys
from pathlib import Path

RELEASE_MARK = 'installed-release.json'


def layout(program, environ=None):
    """The folders for a program folder, as a dict. Pure: the tests call it with made-up folders."""
    environ = os.environ if environ is None else environ
    program = Path(program)
    mark = program / RELEASE_MARK
    installed = mark.is_file()
    release = {}
    if installed:
        try:
            release = json.loads(mark.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            release = {}
    if environ.get('VERONICA_HOME', '').strip():
        home = Path(environ['VERONICA_HOME'].strip())
    elif installed:
        home = program.parents[2]       # <home>\releases\<id>\app: so a sandbox install keeps its data beside it
    else:
        home = program
    base = program.parent if installed else program
    if installed:
        python = base / 'python' / 'python.exe'
        pythonw = base / 'python' / 'pythonw.exe'
    else:
        python = program / '.venv' / 'Scripts' / 'python.exe'
        pythonw = program / '.venv' / 'Scripts' / 'pythonw.exe'
    return {'program': program, 'home': home, 'installed': installed, 'models': base / 'models',
            'python': python, 'pythonw': pythonw, 'version': release.get('version', 'development'),
            'release': release.get('release_id')}


_L = layout(Path(__file__).resolve().parent)
PROGRAM = _L['program']          # her code (what self-repair snapshots and checks)
HOME = _L['home']                # her data
INSTALLED = _L['installed']
MODELS = _L['models']
PYTHON = _L['python'] if _L['python'].is_file() else Path(sys.executable)
PYTHONW = _L['pythonw'] if _L['pythonw'].is_file() else Path(sys.executable)
VERSION = _L['version']
RELEASE = _L['release']

DATA = HOME / 'data'
ENV_FILE = HOME / '.env'
LOG = HOME / 'veronica.log'
SETTINGS = HOME / 'settings.json'
PROJECTS = HOME / 'projects.json'
REPORTS = HOME / 'reports'
