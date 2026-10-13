"""Build VERONICA-Setup-<version>.exe: VERONICA as an installed Windows app (owner 2026-10-14: "a real installed app:
installer, Start Menu, uninstall, her own folder in AppData, updates like JARVIS"; "a separate VERONICA app until the
merge").

    .venv\\Scripts\\python.exe tools\\build_release.py --version 1.0.0 [--out D:\\VERONICA-release-work]

What goes in (a release is bound to one commit; nothing is downloaded):
  - app\\       her code from the HEAD commit (git archive), never uncommitted edits; plus installed-release.json
  - python\\    CPython 3.12.10 embeddable, the same file and SHA-256 JARVIS's build pins (its build cache), with the
               packages of her .venv (the ones her tests run against; same Python 3.12.10) and the C++ runtime DLLs
  - models\\    the speech model (base.en) from models\\
  - VERONICA.exe  the launcher (launcher\\VERONICA-installed.cs, compiled by the C# compiler that comes with Windows)
Out: <out>\\VERONICA-<version>-<commit>\\ (the staged release), VERONICA-Setup-<version>.exe (Inno Setup 6, which is
installed on this PC) and VERONICA-<version>-<commit>.release.json (hashes and sizes). No keys are ever packed: her
.env stays in %LOCALAPPDATA%\\VERONICA and is copied there once from the coding folder by installer.py.
"""
import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = Path(r'D:\VERONICA-release-work')
PYTHON_EMBED = {'file': 'python-3.12.10-embed-amd64.zip',
                'sha256': '4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3',
                'caches': [Path(r'D:\JARVIS-release-work\src-1.0.20\build\cache'),
                           Path(r'D:\JARVIS-release-work\src-merge\build\cache'), ROOT / 'build' / 'cache']}
CRT = ('msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll', 'concrt140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll')
CSC = Path(r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe')
ISCC = [Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'Inno Setup 6' / 'ISCC.exe',
        Path(r'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'), Path(r'C:\Program Files\Inno Setup 6\ISCC.exe')]
NOT_PACKED = ('.env', '.env.', 'projects.json', 'settings.json', 'veronica.log', 'VERONICA.exe')
SKIP_PACKAGES = ('pip', 'pip-', '__pycache__')


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def git(*args):
    return subprocess.run(['git', *args], cwd=str(ROOT), capture_output=True, check=True).stdout


def refuse(message):
    sys.exit(f'REFUSED: {message}')


def version_quad(version):
    parts = version.split('.')
    if not 1 <= len(parts) <= 4 or not all(p.isdigit() for p in parts):
        refuse(f'the version {version!r} must be numbers like 1.0.0')
    return '.'.join((parts + ['0'] * 4)[:4])


def build(version, out=DEFAULT_OUT, allow_dirty=False):
    quad = version_quad(version)
    dirty = [line for line in git('status', '--porcelain', '--untracked-files=no').decode().splitlines() if line]
    if dirty and not allow_dirty:
        refuse('commit her changes first: a release is built from the HEAD commit only. Changed: ' + ', '.join(dirty))
    commit = git('rev-parse', 'HEAD').decode().strip()
    release_id = f'{version}-{commit[:12]}'
    stage = Path(out) / f'VERONICA-{release_id}'
    if stage.exists():
        shutil.rmtree(stage)
    release = stage / 'releases' / release_id
    started = time.time()

    # 1. her code, from the commit
    app = release / 'app'
    app.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(git('archive', '--format=tar', commit))) as archive:
        for member in archive.getmembers():
            name = member.name
            if name.startswith(('.git', 'tools/', 'build_app.py')) or name.rsplit('/', 1)[-1].startswith(NOT_PACKED):
                continue
            archive.extract(member, app, filter='data')
    (app / 'installed-release.json').write_text(json.dumps({'version': version, 'release_id': release_id,
                                                            'commit': commit, 'built_at': time.time()}, indent=2),
                                                encoding='utf-8')

    # 2. her Python: the pinned embeddable, her packages, the C++ runtime
    embed = next((c / PYTHON_EMBED['file'] for c in PYTHON_EMBED['caches'] if (c / PYTHON_EMBED['file']).is_file()), None)
    if embed is None or sha256(embed) != PYTHON_EMBED['sha256']:
        refuse(f'{PYTHON_EMBED["file"]} with SHA-256 {PYTHON_EMBED["sha256"]} not found in the build caches')
    python = release / 'python'
    with zipfile.ZipFile(embed) as z:
        z.extractall(python)
    (python / 'python312._pth').write_text('python312.zip\n.\nLib\\site-packages\n..\\app\nimport site\n',
                                           encoding='ascii')
    venv_cfg = (ROOT / '.venv' / 'pyvenv.cfg').read_text(encoding='utf-8')
    if 'version = 3.12.10' not in venv_cfg:
        refuse('her .venv is not Python 3.12.10, so its packages do not fit the embeddable Python')
    site = ROOT / '.venv' / 'Lib' / 'site-packages'
    target = python / 'Lib' / 'site-packages'
    target.mkdir(parents=True)
    for item in site.iterdir():
        if item.name.startswith(SKIP_PACKAGES) or item.name == 'distutils-precedence.pth':
            continue
        if item.is_dir():
            shutil.copytree(item, target / item.name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        else:
            shutil.copy2(item, target / item.name)
    for dll in CRT:
        source = Path(r'C:\Windows\System32') / dll
        if not source.is_file():
            refuse(f'{dll} is missing on this PC')
        shutil.copyfile(source, python / dll)

    # 3. the speech model
    model = ROOT / 'models' / 'base.en'
    if not (model / 'model.bin').is_file():
        refuse('the speech model models\\base.en is missing')
    shutil.copytree(model, release / 'models' / 'base.en')

    # 4. the launcher
    source = (ROOT / 'launcher' / 'VERONICA-installed.cs').read_text(encoding='utf-8').replace('@VERSION_QUAD@', quad)
    cs = Path(out) / 'VERONICA-installed.cs'
    cs.write_text(source, encoding='utf-8')
    icon = ROOT / 'launcher' / 'veronica.ico'
    done = subprocess.run([str(CSC), '/nologo', '/target:winexe', '/optimize+', '/reference:System.Windows.Forms.dll',
                           '/reference:System.Core.dll', f'/win32icon:{icon}', f'/out:{stage / "VERONICA.exe"}',
                           str(cs)], capture_output=True, text=True)
    if done.returncode:
        refuse('the launcher did not compile:\n' + done.stdout + done.stderr)

    # 5. the check: her Python imports every module of hers from the release, with no venv and no repository
    env = {k: v for k, v in os.environ.items() if not k.startswith(('PYTHON', 'VIRTUAL_ENV'))}
    env['VERONICA_HOME'] = str(Path(out) / 'import-check-home')
    modules = ['agent', 'app', 'goals', 'health', 'installer', 'models', 'paths', 'research', 'store', 'tasks', 'voice',
               'web', 'webview', 'faster_whisper', 'langgraph', 'deepagents', 'clr']
    check = subprocess.run([str(python / 'python.exe'), '-B', '-c',
                            'import importlib, json, paths\n'
                            + ''.join(f'importlib.import_module({m!r})\n' for m in modules)
                            + 'print(json.dumps({"installed": paths.INSTALLED, "version": paths.VERSION}))'],
                           cwd=str(Path(out)), env=env, capture_output=True, text=True, timeout=300)
    shutil.rmtree(Path(out) / 'import-check-home', ignore_errors=True)
    if check.returncode or '"installed": true' not in check.stdout:
        refuse('her Python in the release cannot load her code:\n' + (check.stderr or check.stdout)[-2000:])

    # 6. the Setup
    iscc = next((p for p in ISCC if p.is_file()), None)
    if iscc is None:
        refuse('Inno Setup 6 (ISCC.exe) was not found')
    name = f'VERONICA-Setup-{version}'
    script = (ROOT / 'tools' / 'VERONICA-Setup.iss.in').read_text(encoding='utf-8')
    for key, value in {'VERSION': version, 'VERSION_QUAD': quad, 'RELEASE_ID': release_id, 'STAGE': str(stage),
                       'OUTPUT_DIR': str(Path(out)), 'OUTPUT_NAME': name, 'ICON': str(icon)}.items():
        script = script.replace(f'@{key}@', value)
    iss = Path(out) / f'{name}.iss'
    iss.write_text(script, encoding='utf-8-sig')
    done = subprocess.run([str(iscc), '/Q', str(iss)], capture_output=True, text=True)
    setup = Path(out) / f'{name}.exe'
    if done.returncode or not setup.is_file():
        refuse('Inno Setup did not build the Setup:\n' + done.stdout[-2000:] + done.stderr[-2000:])

    record = {'version': version, 'release_id': release_id, 'commit': commit, 'built_utc':
              time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'seconds': round(time.time() - started),
              'setup': {'file': setup.name, 'sha256': sha256(setup), 'bytes': setup.stat().st_size},
              'python_embeddable_sha256': PYTHON_EMBED['sha256'], 'import_check': json.loads(check.stdout),
              'release_bytes': sum(f.stat().st_size for f in release.rglob('*') if f.is_file())}
    (Path(out) / f'VERONICA-{release_id}.release.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description='Build VERONICA-Setup-<version>.exe')
    parser.add_argument('--version', required=True)
    parser.add_argument('--out', default=str(DEFAULT_OUT))
    parser.add_argument('--allow-dirty', action='store_true', help='tests only: build from HEAD while files changed')
    args = parser.parse_args(argv)
    Path(args.out).mkdir(parents=True, exist_ok=True)
    print(json.dumps(build(args.version, Path(args.out), args.allow_dirty), indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
