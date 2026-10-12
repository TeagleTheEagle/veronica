"""VERONICA, the owner's coding assistant. Type to VERONICA, or talk.

    .venv\\Scripts\\python.exe veronica.py                     type to VERONICA
    .venv\\Scripts\\python.exe veronica.py --voice             talk: "Veronica, fix the failing test"
    .venv\\Scripts\\python.exe veronica.py --project NAME      open a project first (a saved name or a folder)
"""
import argparse
import os
import sys
from pathlib import Path

# PyInstaller bundles assets into a temporary folder stored in sys._MEIPASS
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    BASE_DIR = Path(sys._MEIPASS)
else:
    BASE_DIR = Path(__file__).resolve().parent

HERE = BASE_DIR
EXIT_WORDS = {'exit', 'quit', 'goodbye', 'bye'}


def load_env(path=HERE / '.env'):
    """KEY=VALUE lines from VERONICA's own .env into the environment. Variables already set win; values are never
    printed."""
    if not path.is_file():
        return
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        name, value = line.split('=', 1)
        name = name.strip().removeprefix('export ').strip()
        if name and name not in os.environ:
            os.environ[name] = value.strip().strip('"').strip("'")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description='VERONICA, your coding assistant.')
    parser.add_argument('--voice', action='store_true', help='talk to VERONICA instead of typing')
    parser.add_argument('--project', help='open this project first: a name from projects.json, or a folder path')
    args = parser.parse_args(argv)
    load_env()

    import safety
    from agent import ToolError, Veronica, VeronicaError
    veronica = Veronica(spoken=args.voice, log=print)
    if args.project:
        try:
            print(veronica.open(args.project))
        except (safety.SafetyError, ToolError) as problem:
            print(f'VERONICA: {problem}')
            return 1
        veronica.notes.append(f'[VERONICA] The owner opened {veronica.project}. '
                              f'Test command: {veronica.test_command or "none found"}.')

    if args.voice:
        import voice
        try:
            voice.run(veronica)
        except voice.VoiceError as problem:
            print(f'VERONICA: {problem}')
            return 1
        return 0

    print('VERONICA here. Type what you want. Say "exit" to stop.')
    while True:
        try:
            text = input('\nYou: ')
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if text.strip().lower().strip('.!') in EXIT_WORDS:
            print('VERONICA: Goodbye.')
            return 0
        try:
            reply = veronica.respond(text)
        except VeronicaError as problem:
            print(f'VERONICA: {problem}')
            continue
        if reply.show:
            print(reply.show)
        print(f'VERONICA: {reply.say}')


if __name__ == '__main__':
    sys.exit(main())
