"""A lifecycle-compatible old admin must not pass the recipient-scope gate."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('ci133_ui_runner', ROOT / 'tests/run_factory_ui.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def checkout(tmp_path):
    paths = {asset[1] for asset in runner._ASSETS} | {asset[0] for asset in runner._QUICK_REPLY_ASSETS}
    paths.add('tests/run_factory_ui.py')
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    return tmp_path


@pytest.mark.parametrize('state', ['old', 'missing', 'unsupported', 'misplaced'])
def test_wrong_admin_is_reported_with_correct_upload_path_without_writing(tmp_path, state):
    root = checkout(tmp_path)
    path = root / 'static/admin_factory.js'
    if state == 'old':
        path.write_text('// FACTORY_ADMIN_BUILD: 2026-09-09.ack119-pending-mentions\n'
                        '// FACTORY_ADMIN_LIFECYCLE_API: 1\n')
        runner.check_frontend_assets(root)  # This was the gap in CI #133.
    elif state == 'missing':
        path.unlink()
    elif state == 'misplaced':
        shutil.copyfile(path, root / 'admin_factory.js')
        path.write_text('// FACTORY_ADMIN_BUILD: 2026-09-09.ack119-pending-mentions\n'
                        '// FACTORY_ADMIN_LIFECYCLE_API: 1\n')
    else:
        path.write_text(path.read_text().replace('RECIPIENT_SCOPE_API: 1', 'RECIPIENT_SCOPE_API: 2'))
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(RuntimeError) as failure:
        runner.check_recipient_scope_asset(root)
    message = str(failure.value)
    assert 'static/admin_factory.js' in message and 'open the static folder' in message
    assert 'DOM test remains required' in message
    if state == 'misplaced':
        assert 'repository root; the page does not load that file' in message
    assert before == {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('version', ['ack122', 'ci133'])
@pytest.mark.parametrize('endings', ['lf', 'crlf'])
def test_verified_releases_support_both_checkout_line_endings(tmp_path, version, endings):
    root = checkout(tmp_path)
    path = root / 'static/admin_factory.js'
    data = path.read_bytes().replace(b'\r\n', b'\n')
    if version == 'ack122':
        data = data.replace(b'2026-09-09.ci133-recipient-scope', b'2026-09-09.ack122-recipient-scope')
        data = data.replace(b'// FACTORY_ADMIN_RECIPIENT_SCOPE_API: 1\n', b'')
    if endings == 'crlf':
        data = data.replace(b'\n', b'\r\n')
    path.write_bytes(data)
    runner.check_recipient_scope_asset(root)


@pytest.mark.parametrize('preflight', [True, False])
def test_cli_rejects_old_asset_before_starting_flask_or_node(tmp_path, preflight):
    root = checkout(tmp_path)
    (root / 'static/admin_factory.js').write_text(
        '// FACTORY_ADMIN_BUILD: 2026-09-09.ack119-pending-mentions\n'
        '// FACTORY_ADMIN_LIFECYCLE_API: 1\n')
    # No fixture or Node scripts are copied: reaching startup would produce an
    # unrelated failure. Both CLI entry points must identify the actual asset.
    result = subprocess.run([sys.executable, str(root / 'tests/run_factory_ui.py'),
                             *(['--check-assets'] if preflight else [])],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert 'Recipient-scope update is incomplete: static/admin_factory.js' in output
    assert 'ack119-pending-mentions' in output
    assert 'fixture exited' not in output and 'Timeout:' not in output
