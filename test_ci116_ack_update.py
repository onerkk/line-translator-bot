"""Reject partial ACK uploads early and restore the pair without reverting app.py."""
import importlib.util
from pathlib import Path
import shutil

import pytest

import apply_ack116_update as update


ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('ci116_runner', ROOT / 'tests/run_factory_ui.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def copy_pair(tmp_path):
    for relative, *_ in runner._QUICK_REPLY_ASSETS:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    return tmp_path


@pytest.mark.parametrize('relative', ['line_quick_reply.py', 'static/admin_quick_reply.js'])
@pytest.mark.parametrize('state', ['missing', 'old', 'unsupported'])
def test_partial_ack_update_identifies_the_actual_file(tmp_path, relative, state, capsys):
    repo = copy_pair(tmp_path)
    target = repo / relative
    if state == 'missing':
        target.unlink()
    elif state == 'old':
        target.write_text('# old backend\n' if target.suffix == '.py' else '// old editor\n')
    else:
        target.write_text(target.read_text().replace('ACK_COMMAND_API = 1', 'ACK_COMMAND_API = 2').replace('QUICK_REPLY_ACK_API: 1', 'QUICK_REPLY_ACK_API: 2'))
    before = {str(p): p.read_bytes() for p in repo.rglob('*') if p.is_file()}
    with pytest.raises(RuntimeError, match=relative):
        runner.check_quick_reply_assets(repo)
    assert str(target.resolve()) in capsys.readouterr().out
    assert before == {str(p): p.read_bytes() for p in repo.rglob('*') if p.is_file()}


def test_misplaced_editor_reports_static_path_and_all_missing_assets(tmp_path):
    repo = copy_pair(tmp_path)
    (repo / 'static/admin_quick_reply.js').rename(repo / 'admin_quick_reply.js')
    (repo / 'line_quick_reply.py').unlink()
    with pytest.raises(RuntimeError) as failure:
        runner.check_quick_reply_assets(repo)
    text = str(failure.value)
    assert 'line_quick_reply.py' in text and 'static/admin_quick_reply.js' in text
    assert 'repository root' in text and 'apply_ack116_update.py' in text


@pytest.mark.parametrize('endings', ['lf', 'crlf'])
def test_matching_ack_files_accept_checkout_line_endings(tmp_path, endings):
    repo = copy_pair(tmp_path)
    if endings == 'crlf':
        for path in repo.rglob('*'):
            if path.is_file():
                path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
    runner.check_quick_reply_assets(repo)


def test_single_file_update_restores_both_paths_without_reverting_translation_or_data(tmp_path):
    (tmp_path / 'app.py').write_bytes(b'# current translation application\n')
    (tmp_path / 'settings.json').write_bytes(b'{"group":"keep"}')
    (tmp_path / 'static').mkdir()
    (tmp_path / 'line_quick_reply.py').write_bytes(b'# old backend\n')
    (tmp_path / 'static/admin_quick_reply.js').write_bytes(b'// old frontend\n')
    files = update.payload_files()
    assert 'app.py' not in files and 'settings.json' not in files
    for relative in ['line_quick_reply.py', 'static/admin_quick_reply.js', 'line_factory_features.py',
                     'tests/run_factory_ui.py', 'tests/unified_menu_ui_smoke.cjs', 'test_ack_configuration.py']:
        # The CI116 installer is a fixed historical release. Later updates may
        # change the checkout; verify restored bytes instead of requiring a
        # new runtime to equal an older installer's embedded snapshot.
        assert relative in files
    changed, backup = update.apply_update(tmp_path, files)
    assert 'line_quick_reply.py' in changed and 'static/admin_quick_reply.js' in changed
    assert (backup / 'line_quick_reply.py').read_bytes() == b'# old backend\n'
    assert (backup / 'static/admin_quick_reply.js').read_bytes() == b'// old frontend\n'
    assert (tmp_path / 'app.py').read_bytes() == b'# current translation application\n'
    assert (tmp_path / 'settings.json').read_bytes() == b'{"group":"keep"}'
    for relative, data in files.items():
        assert (tmp_path / relative).read_bytes() == data
    installed_spec = importlib.util.spec_from_file_location('installed_ci116_runner', tmp_path / 'tests/run_factory_ui.py')
    installed_runner = importlib.util.module_from_spec(installed_spec)
    installed_spec.loader.exec_module(installed_runner)
    installed_runner.check_quick_reply_assets(tmp_path)
    assert update.apply_update(tmp_path, files) == ([], None)


def test_installer_failure_restores_the_entire_previous_pair(tmp_path, monkeypatch):
    (tmp_path / 'app.py').write_bytes(b'# keep\n')
    (tmp_path / 'static').mkdir()
    (tmp_path / 'line_quick_reply.py').write_bytes(b'old backend')
    (tmp_path / 'static/admin_quick_reply.js').write_bytes(b'old frontend')
    original = update.os.replace
    def fail_frontend(source, destination):
        if Path(destination).name == 'admin_quick_reply.js':
            raise OSError('simulated disk failure')
        return original(source, destination)
    monkeypatch.setattr(update.os, 'replace', fail_frontend)
    with pytest.raises(RuntimeError, match='Previous files restored'):
        update.apply_update(tmp_path, {'line_quick_reply.py': b'new backend', 'static/admin_quick_reply.js': b'new frontend'})
    assert (tmp_path / 'line_quick_reply.py').read_bytes() == b'old backend'
    assert (tmp_path / 'static/admin_quick_reply.js').read_bytes() == b'old frontend'
