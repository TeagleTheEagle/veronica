"""VERONICA as an installed app: where her program and her data live (paths.py), and the install step (installer.py)."""
import json
import sqlite3

import installer
import paths


def make_release(home, release='1.0.0-abc', version='1.0.0'):
    app = home / 'releases' / release / 'app'
    app.mkdir(parents=True)
    (app / paths.RELEASE_MARK).write_text(json.dumps({'version': version, 'release_id': release}), encoding='utf-8')
    python = home / 'releases' / release / 'python'
    python.mkdir()
    (python / 'python.exe').write_bytes(b'')
    return app


def test_the_coding_folder_keeps_program_and_data_together(tmp_path):
    layout = paths.layout(tmp_path, environ={})
    assert layout['installed'] is False and layout['home'] == tmp_path and layout['program'] == tmp_path
    assert layout["python"] == tmp_path / '.venv' / 'Scripts' / 'python.exe' and layout['version'] == 'development'


def test_an_installed_release_keeps_her_data_in_its_home(tmp_path):
    home = tmp_path / 'VERONICA'
    app = make_release(home)
    layout = paths.layout(app, environ={})
    assert layout['installed'] and layout['home'] == home and layout['version'] == '1.0.0'
    assert layout['python'] == home / 'releases' / '1.0.0-abc' / 'python' / 'python.exe'
    assert layout['models'] == home / 'releases' / '1.0.0-abc' / 'models'
    assert paths.layout(app, environ={'VERONICA_HOME': str(tmp_path / 'elsewhere')})['home'] == tmp_path / 'elsewhere'


def old_folder(tmp_path):
    old = tmp_path / 'old-veronica'
    (old / 'data' / 'checkpoints').mkdir(parents=True)
    (old / '.env').write_text('OPENROUTER_API_KEY=test\n', encoding='utf-8')
    (old / 'settings.json').write_text('{"listen_for_name": false}', encoding='utf-8')
    (old / 'reports').mkdir()
    (old / 'reports' / 'r.md').write_text('# report', encoding='utf-8')
    (old / 'data' / 'checkpoints' / 'c.json').write_text('{}', encoding='utf-8')
    db = sqlite3.connect(str(old / 'data' / 'veronica.db'))
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE memories (content TEXT)')
    db.execute("INSERT INTO memories VALUES ('the owner likes tea')")
    db.commit()                                     # left open: the copy must still hold the row (WAL)
    return old, db


def test_the_first_install_copies_her_data_and_points_at_the_release(tmp_path, monkeypatch):
    old, open_db = old_folder(tmp_path)
    home = tmp_path / 'VERONICA'
    make_release(home)
    result = installer.install(home, '1.0.0-abc', migrate_from=old)
    open_db.close()
    assert result['ok'] and result['previous'] is None
    assert sorted(result['migrated']) == ['.env', 'data\\', 'reports\\', 'settings.json']
    assert (home / '.env').read_text(encoding='utf-8') == 'OPENROUTER_API_KEY=test\n'
    copied = sqlite3.connect(str(home / 'data' / 'veronica.db'))
    assert copied.execute('SELECT content FROM memories').fetchall() == [('the owner likes tea',)]
    copied.close()
    assert (old / '.env').is_file()                 # copied, never moved
    assert json.loads((home / 'current.json').read_text(encoding='utf-8'))['release'] == '1.0.0-abc'


def test_an_update_keeps_her_data_and_one_way_back(tmp_path, monkeypatch):
    old, open_db = old_folder(tmp_path)
    open_db.close()
    home = tmp_path / 'VERONICA'
    for release in ('1.0.0-a', '1.0.1-b'):
        make_release(home, release)
        installer.install(home, release, migrate_from=old)
    (home / '.env').write_text('OPENROUTER_API_KEY=changed-in-the-app\n', encoding='utf-8')
    make_release(home, '1.0.2-c')
    result = installer.install(home, '1.0.2-c', migrate_from=old)
    assert result['ok'] and result['previous'] == '1.0.1-b' and result['removed'] == ['1.0.0-a']
    assert result['migrated'] == [] and 'changed-in-the-app' in (home / '.env').read_text(encoding='utf-8')
    assert sorted(p.name for p in (home / 'releases').iterdir()) == ['1.0.1-b', '1.0.2-c']
    again = installer.install(home, '1.0.2-c', migrate_from=old)      # a reinstall keeps the way back
    assert again['previous'] == '1.0.1-b'


def test_without_being_asked_the_install_copies_nothing(tmp_path):
    home = tmp_path / 'VERONICA'
    make_release(home)
    result = installer.install(home, '1.0.0-abc')
    assert result['ok'] and result['migrated'] == [] and not (home / '.env').exists() and not (home / 'data').exists()


def test_an_incomplete_release_is_refused(tmp_path):
    home = tmp_path / 'VERONICA'
    (home / 'releases' / '1.0.0-x' / 'app').mkdir(parents=True)
    result = installer.install(home, '1.0.0-x')
    assert result['ok'] is False and 'not complete' in result['error'] and not (home / 'current.json').exists()


def test_no_key_or_personal_file_is_packed():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('build_release', Path(__file__).parent / 'tools' / 'build_release.py')
    if spec is None or not Path(spec.origin).is_file():
        return                                      # the test copy has no tools folder
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    assert {'.env', 'projects.json', 'settings.json', 'veronica.log'} <= set(build.NOT_PACKED)
