import hashlib
import io
import stat
import zipfile

import pytest

import zip_update_manager as zu


def _make_zip(path, entries):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries:
            if isinstance(name, zipfile.ZipInfo):
                archive.writestr(name, value)
            else:
                archive.writestr(name, value)
    return path


def _git_blob_sha(content):
    return hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()


def test_inspect_strips_known_project_root_and_classifies_files(tmp_path):
    archive_path = _make_zip(tmp_path / "update.zip", [
        ("line-translator-bot-main/app.py", b"new app"),
        ("line-translator-bot-main/templates/page.html", b"same"),
        ("line-translator-bot-main/new_module.py", b"new"),
    ])
    tree = {
        "app.py": {"sha": _git_blob_sha(b"old app"), "mode": "100644", "type": "blob"},
        "templates/page.html": {"sha": _git_blob_sha(b"same"), "mode": "100644", "type": "blob"},
    }

    plan, summary = zu.inspect_zip_path(archive_path, tree, "onerkk/line-translator-bot")

    assert [(item.path, item.change) for item in plan] == [
        ("app.py", "modified"),
        ("templates/page.html", "unchanged"),
        ("new_module.py", "added"),
    ]
    assert summary["changed_files"] == 2
    assert summary["expanded_bytes"] == len(b"new app") + len(b"same") + len(b"new")
    assert len(summary["plan_sha256"]) == 64


@pytest.mark.parametrize("name", [
    "../escape.py",
    "/absolute.py",
    "C:/outside.py",
    "folder/../../escape.py",
])
def test_inspect_rejects_traversal_and_absolute_paths(tmp_path, name):
    archive_path = _make_zip(tmp_path / "unsafe.zip", [(name, b"x")])

    with pytest.raises(zu.ZipUpdateError) as error:
        zu.inspect_zip_path(archive_path, {}, "onerkk/line-translator-bot")

    assert error.value.code == "unsafe_path"


def test_inspect_rejects_git_metadata_env_and_private_keys(tmp_path):
    for filename, code in [
        (".git/config", "git_metadata"),
        (".env", "secret_file"),
        ("certs/deploy.pem", "secret_file"),
    ]:
        archive_path = _make_zip(tmp_path / (filename.split("/")[-1] + ".zip"), [(filename, b"secret")])
        with pytest.raises(zu.ZipUpdateError) as error:
            zu.inspect_zip_path(archive_path, {}, "onerkk/line-translator-bot")
        assert error.value.code == code


def test_inspect_rejects_symlinks_and_case_collisions(tmp_path):
    symlink = zipfile.ZipInfo("link")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    archive_path = _make_zip(tmp_path / "symlink.zip", [(symlink, "target")])
    with pytest.raises(zu.ZipUpdateError) as error:
        zu.inspect_zip_path(archive_path, {}, "onerkk/line-translator-bot")
    assert error.value.code == "symlink"

    archive_path = _make_zip(tmp_path / "duplicate.zip", [("Readme.md", b"a"), ("README.md", b"b")])
    with pytest.raises(zu.ZipUpdateError) as error:
        zu.inspect_zip_path(archive_path, {}, "onerkk/line-translator-bot")
    assert error.value.code == "duplicate_path"


def test_inspect_rejects_file_directory_path_conflicts(tmp_path):
    archive_path = _make_zip(tmp_path / "conflict.zip", [("pkg", b"file"), ("pkg/module.py", b"child")])
    with pytest.raises(zu.ZipUpdateError) as error:
        zu.inspect_zip_path(archive_path, {}, "onerkk/line-translator-bot")
    assert error.value.code == "path_conflict"

    archive_path = _make_zip(tmp_path / "existing-conflict.zip", [("pkg/module.py", b"child")])
    tree = {"pkg": {"sha": _git_blob_sha(b"file"), "mode": "100644", "type": "blob"}}
    with pytest.raises(zu.ZipUpdateError) as error:
        zu.inspect_zip_path(archive_path, tree, "onerkk/line-translator-bot")
    assert error.value.code == "path_conflict"


def test_preview_ticket_is_signed_scoped_and_expires(tmp_path):
    vault = zu.UploadVault(tmp_path / "vault")
    upload_id, digest, size = vault.store(io.BytesIO(b"sample zip bytes"))
    payload = {
        "upload_id": upload_id,
        "archive_sha256": digest,
        "archive_bytes": size,
        "repo": "onerkk/line-translator-bot",
        "branch": "main",
        "base_sha": "a" * 40,
        "expires_at": 500,
    }
    ticket = zu.issue_preview_ticket(payload, "admin-secret")

    assert zu.verify_preview_ticket(ticket, "admin-secret", now=499) == payload
    with pytest.raises(zu.ZipUpdateError) as error:
        zu.verify_preview_ticket(ticket, "wrong-secret", now=499)
    assert error.value.code == "invalid_preview"
    with pytest.raises(zu.ZipUpdateError) as error:
        zu.verify_preview_ticket(ticket, "admin-secret", now=501)
    assert error.value.code == "preview_expired"


class _FakeGitHub:
    def __init__(self):
        self.blobs = []
        self.tree_entries = None
        self.updated_sha = None

    def _request(self, method, path, payload=None, raw_body=None):
        assert method == "GET"
        assert path == "/git/commits/" + "a" * 40
        return {"tree": {"sha": "b" * 40}}

    def create_blob(self, content):
        self.blobs.append(content)
        return _git_blob_sha(content)

    def create_tree(self, base_tree, entries):
        assert base_tree == "b" * 40
        self.tree_entries = entries
        return "c" * 40

    def create_commit(self, message, tree_sha, parent_sha):
        assert tree_sha == "c" * 40
        assert parent_sha == "a" * 40
        assert message == "Mobile update"
        return "d" * 40

    def update_branch(self, sha):
        self.updated_sha = sha


def test_apply_plan_creates_a_single_commit_with_changed_entries_only(tmp_path):
    content = b"new app"
    archive_path = _make_zip(tmp_path / "apply.zip", [("app.py", content)])
    plan, _ = zu.inspect_zip_path(archive_path, {}, "onerkk/line-translator-bot")
    client = _FakeGitHub()

    result = zu.apply_plan(archive_path, plan, client, "a" * 40, "Mobile update")

    assert result == {"changed": True, "commit_sha": "d" * 40, "added": 1, "modified": 0}
    assert client.blobs == [content]
    assert client.tree_entries == [{"path": "app.py", "mode": plan[0].mode, "type": "blob", "sha": _git_blob_sha(content)}]
    assert client.updated_sha == "d" * 40


def test_noop_plan_does_not_create_commit(tmp_path):
    content = b"same"
    archive_path = _make_zip(tmp_path / "noop.zip", [("app.py", content)])
    plan, _ = zu.inspect_zip_path(
        archive_path,
        {"app.py": {"sha": _git_blob_sha(content), "mode": "100644", "type": "blob"}},
        "onerkk/line-translator-bot",
    )

    result = zu.apply_plan(archive_path, plan, object(), "a" * 40, "ignored")

    assert result == {"changed": False, "commit_sha": None, "added": 0, "modified": 0}


def test_repo_branch_and_commit_message_are_sanitized():
    assert zu.validate_repo_branch("onerkk/line-translator-bot", "main") == (
        "onerkk/line-translator-bot",
        "main",
    )
    with pytest.raises(zu.ZipUpdateError):
        zu.validate_repo_branch("https://github.com/onerkk/repo", "main")
    with pytest.raises(zu.ZipUpdateError):
        zu.validate_repo_branch("onerkk/repo", "../main")
    assert zu.sanitize_commit_message("  Mobile\n update\x00 ") == "Mobile update"
    assert len(zu.sanitize_commit_message("x" * 200)) == 140
