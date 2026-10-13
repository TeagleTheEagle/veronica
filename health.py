"""VERONICA looking after herself: a health check, and repairs to her own code that can always be undone.

Health check (check): her Python is there, her key is there, her code imports, her database opens, a worker is
running when tasks are waiting (one is started if not: that is the one repair the check makes by itself), the
speech model is in place, and no repair was left half done. Run at the window's start (app.py), from the chat
(self_check), or `python health.py`.

Self-repair (start_repair, finish_repair, rollback): before she edits her own code, a snapshot of every file of hers
goes to data\\self-repair\\<stamp>\\files, the tests that already fail are recorded, and a git branch
self-repair/<stamp> is made (no checkout: the owner's working tree and branch are never switched). After the edit,
her tests run again on a throwaway copy and the health check runs; a new failing test, a module that no longer
imports, or a failed check puts every file back from the snapshot (rollback). A repair that holds is committed to the
branch (the branch only; master and the working tree are left as they are) and the snapshot is kept, so the owner can
still say "roll back" afterwards. The snapshot and its manifest are on disk: a rollback survives a crash or restart.

    python health.py                 the health check
    python health.py tests           her own tests on a throwaway copy, and which fail
    python health.py selftest        the same, failing only on tests that passed when the open repair started
    python health.py repairs         the repairs so far
    python health.py rollback [ID]   put her files back from the latest (or the given) snapshot
"""
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import paths
import safety

HERE = paths.PROGRAM
PYTHON = paths.PYTHON
REPAIRS = HERE / 'data' / 'self-repair'
OWN_PATTERNS = ('*.py', 'pytest.ini', 'requirements.txt', '*.md', '.env.example', 'ui/*', 'launcher/*')
MODULES = ('agent', 'app', 'changes', 'calendar_feed', 'citations', 'context_state', 'gmail', 'goals', 'hands', 'health',
           'models', 'pc', 'research', 'store', 'subagents', 'tasks', 'voice', 'web')
REQUIRED_KEYS = ('OPENROUTER_API_KEY',)
OPTIONAL_KEYS = ('BRAVE_API_KEY', 'FISH_API_KEY', 'GMAIL_ADDRESS', 'GMAIL_APP_PASSWORD', 'GOOGLE_CALENDAR_ICS',
                 'ASI_ONE_API_KEY')
TEST_TIMEOUT = 1200
RESULT_LINE = re.compile(r'^(FAILED|ERROR) (\S+)')
SUMMARY_LINE = re.compile(r'^=* ?(\d+ (?:passed|failed|error).*?) ?=*$')


class RepairError(RuntimeError):
    pass


# ----- the health check --------------------------------------------------------------------------------------------

@dataclass
class Check:
    name: str
    ok: object        # True, False, or None for a warning
    detail: str = ''

    def line(self):
        mark = {True: 'ok', False: 'FAIL', None: 'warn'}[self.ok]
        return f'{mark:>4}  {self.name}: {self.detail}'


@dataclass
class Report:
    checks: list = field(default_factory=list)

    @property
    def ok(self):
        return all(c.ok is not False for c in self.checks)

    def problems(self):
        return [c for c in self.checks if c.ok is not True]

    def text(self):
        head = 'VERONICA is healthy.' if self.ok else 'VERONICA has a problem.'
        return head + '\n' + '\n'.join(c.line() for c in self.checks)

    def summary(self):
        bad = [c.name for c in self.checks if c.ok is False]
        return 'healthy' if not bad else 'problems: ' + ', '.join(bad)


def _clean_env(keys=True):
    """The environment for her subprocesses: no PYTHONPATH from whoever started her; without keys when keys=False
    (her tests never see her keys, as with changes.run_tests)."""
    import changes
    env = dict(os.environ) if keys else changes.clean_env()
    env.pop('PYTHONPATH', None)
    env['PYTHONIOENCODING'] = 'utf-8'
    return env


def imports_ok(root=HERE, python=None, modules=MODULES, timeout=120):
    """(True, '') when every module of hers imports in a fresh interpreter, else (False, the error)."""
    python = str(python or (PYTHON if PYTHON.is_file() else sys.executable))
    code = 'import importlib, sys\n' + '\n'.join(f'importlib.import_module({m!r})' for m in modules) + '\nprint("ok")'
    try:
        result = subprocess.run([python, '-c', code], cwd=str(root), env=_clean_env(), capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, f'could not run her Python: {error}'
    if result.returncode == 0 and 'ok' in result.stdout:
        return True, ''
    lines = [line for line in (result.stderr or result.stdout).strip().splitlines() if line.strip()]
    return False, (lines[-1] if lines else f'exit code {result.returncode}')[:300]


def check(store=None, root=HERE, python=None, imports=True, modules=MODULES, start_worker=True):
    """The health report. `store` is her database (opened if None); the worker is started when tasks wait."""
    report = Report()
    root = Path(root)                  # callers may give the folder as text
    python = Path(python) if python else (PYTHON if PYTHON.is_file() else Path(sys.executable))
    report.checks.append(Check('python', python.is_file(), str(python)))

    missing = [k for k in REQUIRED_KEYS if not os.environ.get(k, '').strip()]
    absent = [k for k in OPTIONAL_KEYS if not os.environ.get(k, '').strip()]
    report.checks.append(Check('keys', not missing, ('missing ' + ', '.join(missing)) if missing else
                               ('all set' if not absent else 'set; not set: ' + ', '.join(absent))))

    if imports:
        ok, error = imports_ok(root, python, modules)
        report.checks.append(Check('code', ok, 'every module imports' if ok else error))

    try:
        if store is None:
            import store as store_mod
            store = store_mod.Store()
        tasks_ = store.list_tasks(limit=50)
        waiting = [t for t in tasks_ if t['status'] in ('queued', 'running')]
        report.checks.append(Check('database', True, f'{len(store.memories())} memories, {len(tasks_)} recent tasks'))
        if not waiting:
            report.checks.append(Check('worker', True, 'nothing waiting'))
        elif store.live_workers():
            report.checks.append(Check('worker', True, f'running, {len(waiting)} task(s) in hand'))
        elif start_worker:
            import tasks
            try:
                started = tasks.ensure_worker(store)
                report.checks.append(Check('worker', True, f'started one for {len(waiting)} waiting task(s)' if started
                                           else 'running'))
            except OSError as error:
                report.checks.append(Check('worker', False, f'{len(waiting)} task(s) wait and no worker could start: {error}'))
        else:
            report.checks.append(Check('worker', False, f'{len(waiting)} task(s) wait and no worker is running'))
    except Exception as error:
        report.checks.append(Check('database', False, f'{type(error).__name__}: {str(error)[:200]}'))

    model = paths.MODELS if root == paths.PROGRAM else root / 'models'
    has_model = any(model.rglob('model.bin')) if model.is_dir() else False
    report.checks.append(Check('speech model', True if has_model else None,
                               'in place' if has_model else 'not found (she will type, not hear)'))

    open_repairs = [r for r in repairs(root) if r.get('status') == 'open']
    report.checks.append(Check('repairs', None if open_repairs else True,
                               f'{len(open_repairs)} left open: ' + ', '.join(r['stamp'] for r in open_repairs)
                               if open_repairs else 'none open'))
    return report


# ----- her own tests ------------------------------------------------------------------------------------------------

def own_files(root=HERE):
    """Her own files, relative to root, as the snapshot and the git commit see them."""
    root = Path(root)
    found = []
    for pattern in OWN_PATTERNS:
        if '/' in pattern:
            folder, name = pattern.split('/', 1)
            base = root / folder
            if base.is_dir():
                found.extend(p.relative_to(root).as_posix() for p in base.rglob('*') if p.is_file()
                             and fnmatch.fnmatch(p.name, name))
        else:
            found.extend(p.name for p in root.iterdir() if p.is_file() and fnmatch.fnmatch(p.name, pattern))
    return sorted(set(found))


def failing_tests(root=HERE, python=None, timeout=TEST_TIMEOUT):
    """(the set of failing tests, pytest's summary line) from her tests on a throwaway copy of her files.
    The set is None when the tests could not be run at all."""
    python = str(python or (PYTHON if PYTHON.is_file() else sys.executable))
    copy = Path(tempfile.mkdtemp(prefix='veronica-selftest-'))
    try:
        for name in own_files(root):
            target = copy / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(root) / name, target)
        try:
            result = subprocess.run([python, '-m', 'pytest', '-q', '-rfE', '-p', 'no:cacheprovider', '--no-header'],
                                    cwd=str(copy), env=_clean_env(keys=False), capture_output=True, text=True, encoding='utf-8',
                                    errors='replace', timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as error:
            return None, f'the tests could not be run: {error}'
        output = (result.stdout or '') + (result.stderr or '')
        failed = {m.group(2) for m in (RESULT_LINE.match(line) for line in output.splitlines()) if m}
        summary = next((m.group(1) for m in (SUMMARY_LINE.match(line.strip()) for line in output.splitlines()) if m),
                       f'exit code {result.returncode}')
        if result.returncode >= 2 and not failed:     # pytest itself broke: collection error, bad usage
            tail = '\n'.join(output.strip().splitlines()[-8:])
            return None, f'the tests could not be run ({summary}):\n{tail}'
        return failed, summary
    finally:
        shutil.rmtree(copy, ignore_errors=True)


# ----- repairs: snapshot, branch, finish, rollback --------------------------------------------------------------------

def _git(root, *args, check=True):
    result = subprocess.run(['git', *args], cwd=str(root), capture_output=True, text=True, encoding='utf-8',
                            errors='replace', env=_clean_env())
    if check and result.returncode != 0:
        raise RepairError(f'git {args[0]} failed: {(result.stderr or result.stdout).strip()[:300]}')
    return result.stdout.strip()


def _has_git(root):
    try:
        return _git(root, 'rev-parse', '--is-inside-work-tree', check=False) == 'true'
    except OSError:
        return False


def repairs(root=HERE):
    folder = Path(root) / 'data' / 'self-repair'
    found = []
    if folder.is_dir():
        for manifest in sorted(folder.glob('*/manifest.json')):
            try:
                found.append(json.loads(manifest.read_text(encoding='utf-8')))
            except (OSError, ValueError):
                continue
    return found


def _repair_dir(root, stamp):
    return Path(root) / 'data' / 'self-repair' / stamp


def _save(root, repair):
    folder = _repair_dir(root, repair['stamp'])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'manifest.json').write_text(json.dumps(repair, indent=2), encoding='utf-8')


def start_repair(reason, root=HERE, python=None, tests=failing_tests):
    """Snapshot her files, record the tests that already fail, make the branch. Returns the repair (a dict)."""
    root = Path(root)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    while _repair_dir(root, stamp).exists():
        stamp += 'x'
    files = own_files(root)
    folder = _repair_dir(root, stamp) / 'files'
    for name in files:
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, target)
    baseline, summary = tests(root, python) if tests else (set(), 'not run')
    branch = None
    if _has_git(root):
        branch = f'self-repair/{stamp}'
        _git(root, 'branch', branch)
    repair = {'stamp': stamp, 'reason': ' '.join(str(reason).split())[:500], 'started': time.time(), 'files': files,
              'baseline_failures': sorted(baseline) if baseline is not None else None, 'baseline_summary': summary,
              'branch': branch, 'status': 'open'}
    _save(root, repair)
    return repair


def changed_files(repair, root=HERE):
    """Files that differ from the snapshot now, plus files of hers that did not exist then."""
    root = Path(root)
    folder = _repair_dir(root, repair['stamp']) / 'files'
    changed = []
    for name in repair['files']:
        now, then = root / name, folder / name
        if not now.is_file() or not then.is_file() or now.read_bytes() != then.read_bytes():
            changed.append(name)
    changed.extend(name for name in own_files(root) if name not in repair['files'])
    return sorted(set(changed))


def rollback(repair, root=HERE):
    """The files this repair changed put back as they were in its snapshot; files it created are removed. Only the
    repair's own files are touched (a kept repair: the files it recorded as changed), so a later rollback does not
    undo other work done since. Returns the files put back."""
    root = Path(root)
    folder = _repair_dir(root, repair['stamp']) / 'files'
    if not folder.is_dir():
        raise RepairError(f'The snapshot {repair["stamp"]} is missing; nothing was changed.')
    names = repair.get('changed') if repair.get('status') == 'kept' else changed_files(repair, root)
    restored = []
    for name in names or []:
        target = root / name
        if name in repair['files'] and (folder / name).is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(folder / name, target)
            restored.append(name)
        elif target.is_file():
            target.unlink()
            restored.append(name)
    repair['status'] = 'rolled back'
    repair['finished'] = time.time()
    _save(root, repair)
    return restored


def commit_to_branch(root, branch, files, message):
    """Commit the working-tree versions of `files` onto `branch` without checking it out: a temporary index is
    filled from the branch, the files are added to it, and the branch moved to the new commit. HEAD, the owner's
    branch and the working tree are not touched."""
    root = Path(root)
    with tempfile.NamedTemporaryFile(prefix='veronica-index-', delete=False) as handle:
        index = handle.name
    os.unlink(index)
    env = {**_clean_env(), 'GIT_INDEX_FILE': index}
    try:
        def git(*args):
            result = subprocess.run(['git', *args], cwd=str(root), env=env, capture_output=True, text=True,
                                    encoding='utf-8', errors='replace')
            if result.returncode != 0:
                raise RepairError(f'git {args[0]} failed: {(result.stderr or result.stdout).strip()[:300]}')
            return result.stdout.strip()
        git('read-tree', branch)
        present = [f for f in files if (root / f).is_file()]
        gone = [f for f in files if not (root / f).is_file()]
        if present:
            git('add', '--', *present)
        if gone:
            git('rm', '--cached', '--ignore-unmatch', '-q', '--', *gone)
        tree = git('write-tree')
        commit = git('commit-tree', tree, '-p', branch, '-m', message)
        git('update-ref', f'refs/heads/{branch}', commit)
        return commit
    finally:
        try:
            os.unlink(index)
        except OSError:
            pass


def finish_repair(repair, summary='', root=HERE, python=None, tests=failing_tests, health=None):
    """Check the repair and keep it (committed to its branch) or roll it back. Returns (kept, words for the owner)."""
    root = Path(root)
    changed = changed_files(repair, root)
    if not changed:
        repair['status'] = 'kept'
        repair['finished'] = time.time()
        _save(root, repair)
        return True, 'Nothing changed, so there is nothing to check.'
    reasons = []
    failures, test_summary = tests(root, python) if tests else (set(), 'not run')
    if failures is None:
        reasons.append(test_summary)
    else:
        baseline = set(repair.get('baseline_failures') or [])
        new = sorted(failures - baseline)
        fixed = sorted(baseline - failures)
        if new:
            reasons.append(f'{len(new)} test(s) fail that passed before: ' + ', '.join(new[:8]))
    report = (health or check)(root=root, python=python)
    bad = [c for c in report.checks if c.ok is False]
    if bad:
        reasons.append('the health check fails: ' + '; '.join(f'{c.name}: {c.detail}' for c in bad))
    if reasons:
        rollback(repair, root)
        repair['outcome'] = '; '.join(reasons)
        _save(root, repair)
        return False, (f'Rolled back ({len(changed)} file(s) put back): ' + '; '.join(reasons) + f'. Tests: {test_summary}.')
    repair['status'] = 'kept'
    repair['finished'] = time.time()
    repair['changed'] = changed
    repair['summary'] = ' '.join(str(summary).split())[:500]
    repair['tests'] = test_summary
    if failures is not None and fixed:
        repair['fixed_tests'] = fixed
    if repair.get('branch'):
        try:
            repair['commit'] = commit_to_branch(root, repair['branch'], changed,
                                                f'VERONICA self-repair {repair["stamp"]}: {repair["summary"] or repair["reason"]}')
        except RepairError as error:
            repair['commit_error'] = str(error)
    _save(root, repair)
    words = f'Kept: {len(changed)} file(s) changed ({", ".join(changed[:8])}). Tests: {test_summary}'
    if failures is not None and fixed:
        words += f'; {len(fixed)} test(s) that failed before now pass'
    words += '.' + (f' Committed to branch {repair["branch"]}.' if repair.get('commit') else '')
    words += ' The running VERONICA still uses the old code until she is restarted.'
    return True, words


def latest_repair(root=HERE, statuses=('open', 'kept')):
    found = [r for r in repairs(root) if r.get('status') in statuses]
    return found[-1] if found else None


def describe(repair):
    line = f'{repair["stamp"]}  {repair["status"].upper():<12} {repair["reason"]}'
    if repair.get('changed'):
        line += f'\n    changed: {", ".join(repair["changed"])}'
    if repair.get('outcome'):
        line += f'\n    why: {repair["outcome"]}'
    if repair.get('commit'):
        line += f'\n    commit {repair["commit"][:10]} on {repair["branch"]}'
    return line


# ----- the console -------------------------------------------------------------------------------------------------

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    import veronica
    veronica.load_env()
    command = argv[0] if argv else 'check'
    if command == 'check':
        report = check()
        print(report.text())
        return 0 if report.ok else 1
    if command == 'tests':
        failed, summary = failing_tests()
        print(summary)
        for name in sorted(failed or []):
            print('  ' + name)
        return 0
    if command == 'selftest':
        # the test command while a repair is open: fail only on tests that passed when the repair started
        repair = latest_repair(statuses=('open',))
        failed, summary = failing_tests()
        if failed is None:
            print(summary)
            return 1
        baseline = set((repair or {}).get('baseline_failures') or [])
        new = sorted(failed - baseline)
        print(f'{summary}; {len(new)} new failure(s) since the repair started' + (':' if new else '.'))
        for name in new:
            print('  ' + name)
        return 1 if new else 0
    if command == 'repairs':
        rows = repairs()
        print('\n'.join(describe(r) for r in rows) if rows else 'No repairs yet.')
        return 0
    if command == 'rollback':
        repair = next((r for r in repairs() if r['stamp'] == argv[1]), None) if len(argv) > 1 else latest_repair()
        if repair is None:
            print('No repair to roll back.')
            return 1
        restored = rollback(repair)
        print(f'Rolled back {repair["stamp"]}: ' + (', '.join(restored) if restored else 'nothing had changed'))
        return 0
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main())
