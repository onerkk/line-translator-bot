"""Catch partial frontend uploads before running Node; verify safe restoration."""
import importlib.util
from pathlib import Path
import shutil

import pytest

import apply_ci106_update as update


ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ci106_ui_runner", ROOT / "tests/run_factory_ui.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def copy_assets(tmp_path):
    (tmp_path / "static").mkdir(exist_ok=True)
    for _, relative, _, _ in runner._ASSETS:
        shutil.copy2(ROOT / relative, tmp_path / relative)
    return tmp_path


@pytest.mark.parametrize("relative", ["static/line_factory.js", "static/liff_forms.js"])
@pytest.mark.parametrize("state", ["missing", "legacy", "unsupported"])
def test_incomplete_member_or_form_update_is_reported_before_node(tmp_path, relative, state):
    repo = copy_assets(tmp_path)
    target = repo / relative
    if state == "missing":
        target.unlink()
    elif state == "legacy":
        target.write_text("const $=id=>document.getElementById(id);\n")
    else:
        target.write_text(target.read_text().replace("_LIFECYCLE_API: 1", "_LIFECYCLE_API: 2"))
    with pytest.raises(RuntimeError, match=relative):
        runner.check_frontend_assets(repo)


def test_reports_both_missing_files_and_misplaced_root_file(tmp_path):
    repo = copy_assets(tmp_path)
    (repo / "static/line_factory.js").rename(repo / "line_factory.js")
    (repo / "static/liff_forms.js").unlink()
    with pytest.raises(RuntimeError) as result:
        runner.check_frontend_assets(repo)
    message = str(result.value)
    assert "static/line_factory.js" in message and "static/liff_forms.js" in message
    assert "exists at repository root" in message
    assert "python apply_ci106_update.py" in message


@pytest.mark.parametrize("endings", ["lf", "crlf"])
def test_matching_release_is_accepted_with_git_line_endings(tmp_path, endings):
    repo = copy_assets(tmp_path)
    if endings == "crlf":
        for path in (repo / "static").iterdir():
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    runner.check_frontend_assets(repo)


def test_installer_restores_real_payload_and_keeps_unrelated_data(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("# old application\n")
    (root / "settings.json").write_text('{"keep":true}')
    files = update.payload_files()
    changed, backup = update.apply_update(root, files)
    assert changed and backup
    assert (backup / "app.py").read_text() == "# old application\n"
    assert (root / "settings.json").read_text() == '{"keep":true}'
    for name, data in files.items():
        assert (root / name).read_bytes() == data
    runner.check_frontend_assets(root)
    assert update.apply_update(root, files) == ([], None)


def test_installer_check_is_read_only(tmp_path):
    (tmp_path / "app.py").write_bytes(b"old")
    changed, backup = update.apply_update(tmp_path, {"app.py": b"new"}, check=True)
    assert changed == ["app.py"] and backup is None
    assert (tmp_path / "app.py").read_bytes() == b"old"


def test_installer_validates_entire_payload_before_any_write(tmp_path):
    (tmp_path / "app.py").write_bytes(b"old")
    with pytest.raises(ValueError):
        update.apply_update(tmp_path, {"app.py": b"new", "../outside.txt": b"bad"})
    assert (tmp_path / "app.py").read_bytes() == b"old"
    assert not (tmp_path.parent / "outside.txt").exists()


def test_installer_rejects_external_parent_symlink(tmp_path):
    root, outside = tmp_path / "repo", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "app.py").write_bytes(b"old")
    (root / "static").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        update.apply_update(root, {"app.py": b"new", "static/line_factory.js": b"new"})
    assert (root / "app.py").read_bytes() == b"old"
    assert not list(outside.iterdir())


def test_installer_rolls_back_when_replacement_fails(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_bytes(b"old")
    original = update.os.replace
    def fail_second(source, destination):
        if Path(destination).name == "member.js":
            raise OSError("simulated filesystem failure")
        return original(source, destination)
    monkeypatch.setattr(update.os, "replace", fail_second)
    with pytest.raises(RuntimeError, match="Previous files restored"):
        update.apply_update(tmp_path, {"app.py": b"new", "static/member.js": b"new"})
    assert (tmp_path / "app.py").read_bytes() == b"old"
    assert not (tmp_path / "static/member.js").exists()
