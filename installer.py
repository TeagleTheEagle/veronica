"""The installed VERONICA's own install step (run by VERONICA-Setup-<version>.exe after it has unpacked a release).

    <home>\\releases\\<id>\\python\\python.exe -B <home>\\releases\\<id>\\app\\installer.py install --home <home> --release <id>

- current.json says which release VERONICA.exe opens; the one before is kept as "previous" (a way back), and any
  older release folders are removed.
- The first install copies (never moves) her data from the coding folder, so she keeps her memories, goals, keys and
  settings: .env, settings.json, projects.json, data\\ (the database copied with SQLite's own backup, so a copy made
  while the old window is open is still whole) and reports\\. Later installs and updates never touch her data, and
  the uninstall leaves it in place.
Prints one JSON line (the Setup keeps it as setup-result.json) and exits 0, or 1 on a refusal.
"""
import argparse
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

OLD_FOLDER = Path(r'C:\Users\jonny\JARVIS-claude-workspace\veronica')


def real_home():
    import os
    return Path(os.environ.get('LOCALAPPDATA') or (Path.home() / 'AppData' / 'Local')) / 'VERONICA'
DATA_FILES = ('.env', 'settings.json', 'projects.json')
DATA_FOLDERS = ('reports',)


def _copy_database(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f'file:{source}?mode=ro', uri=True)
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def migrate(home, old):
    """Copy her data from `old` (the coding folder) into `home` once (when home has no data yet). The names copied."""
    home, old = Path(home), Path(old)
    if (home / 'data').exists() or (home / '.env').exists() or not old.is_dir() or old.resolve() == home.resolve():
        return []
    copied = []
    for name in DATA_FILES:
        if (old / name).is_file():
            shutil.copy2(old / name, home / name)
            copied.append(name)
    for name in DATA_FOLDERS:
        if (old / name).is_dir():
            shutil.copytree(old / name, home / name)
            copied.append(name + '\\')
    data = old / 'data'
    if data.is_dir():
        for item in data.iterdir():
            target = home / 'data' / item.name
            if item.suffix == '.db' and item.is_file():
                _copy_database(item, target)
            elif item.name.endswith(('.db-wal', '.db-shm')):
                continue                     # folded into the copy by the backup above
            elif item.is_dir():
                shutil.copytree(item, target)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
        copied.append('data\\')
    return copied


def install(home, release, migrate_from=None):
    """Point current.json at `release`. Her data is copied from `migrate_from` only when it is given (the Setup's
    run gives the coding folder; nothing else ever reads it, so a test can never copy her real keys)."""
    home = Path(home)
    folder = home / 'releases' / release
    if not (folder / 'app' / 'installed-release.json').is_file() or not (folder / 'python' / 'python.exe').is_file():
        return {'ok': False, 'error': f'Release {release} is not complete in {folder}.'}
    current_file = home / 'current.json'
    previous = None
    try:
        previous = json.loads(current_file.read_text(encoding='utf-8')).get('release')
    except (OSError, ValueError):
        pass
    if previous == release:              # a reinstall of the same release keeps the way back it had
        try:
            previous = json.loads(current_file.read_text(encoding='utf-8')).get('previous')
        except (OSError, ValueError):
            previous = None
    copied = migrate(home, migrate_from) if migrate_from else []
    current_file.write_text(json.dumps({'release': release, 'previous': previous, 'switched_at': time.time()}),
                            encoding='utf-8')
    removed = []
    for other in sorted((home / 'releases').iterdir()):
        if other.is_dir() and other.name not in (release, previous):
            shutil.rmtree(other, ignore_errors=True)
            removed.append(other.name)
    return {'ok': True, 'release': release, 'previous': previous, 'migrated': copied, 'removed': removed,
            'home': str(home)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="VERONICA's install step")
    parser.add_argument('command', choices=['install'])
    parser.add_argument('--home', required=True)
    parser.add_argument('--release', required=True)
    args = parser.parse_args(argv)
    try:
        # her data is copied only into the PC's own install home, never into a rehearsal's sandbox folder
        own = Path(args.home).resolve() == real_home().resolve()
        result = install(args.home, args.release, migrate_from=OLD_FOLDER if own else None)
    except Exception as error:           # the Setup shows this line; nothing else is printed
        result = {'ok': False, 'error': f'{type(error).__name__}: {error}'}
    print(json.dumps(result))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
