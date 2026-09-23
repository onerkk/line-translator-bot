"""Validated, preview-first ZIP updates for the mobile admin deploy page.

This module intentionally has no Flask dependency so its archive and ticket
handling can be tested independently. GitHub changes are written as one Git
commit using the Git Data API, then the branch ref is advanced without force.
"""

from __future__ import annotations

import base64
import bisect
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Mapping


MAX_ARCHIVE_BYTES = 139 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_ARCHIVE_BYTES + 4 * 1024 * 1024
MAX_FILES = 1000
MAX_ARCHIVE_ENTRIES = 3000
MAX_CHANGED_FILES = 250
MAX_TOTAL_UNCOMPRESSED_BYTES = 350 * 1024 * 1024
MAX_SINGLE_FILE_BYTES = 20 * 1024 * 1024
MAX_COMPRESSION_RATIO = 1000
PREVIEW_TTL_SECONDS = 15 * 60
UPLOAD_RETENTION_SECONDS = 30 * 60
CHUNK_SIZE = 1024 * 1024
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,120}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_IGNORED_COMPONENTS = {"__pycache__", ".pytest_cache", ".venv", "venv", "node_modules"}
_IGNORED_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}
_SAFE_ENV_TEMPLATES = {".env.example", ".env.sample", ".env.template"}
_BLOCKED_SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}
_BLOCKED_SECRET_NAMES = {"credentials.json", "service-account.json", "service_account.json", "secrets.json"}


class ZipUpdateError(Exception):
    """An expected, user-presentable ZIP update failure."""

    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class GitHubAPIError(ZipUpdateError):
    """A GitHub API error with a safe message that never contains credentials."""

    def __init__(self, http_status: int, operation: str, response_body: bytes = b""):
        detail = response_body.decode("utf-8", "ignore").casefold()
        if http_status in (409, 422) and any(term in detail for term in ("protected branch", "branch protection", "ruleset")):
            code, status, message = (
                "branch_protected",
                409,
                "GitHub 分支保護規則拒絕直接更新；請允許伺服器 Token 更新此分支，或改用 Pull Request 部署流程。",
            )
        elif http_status in (401, 403):
            code, status, message = (
                "github_permission",
                502,
                "GitHub 權限不足或 API 額度受限；請確認伺服器端 GITHUB_TOKEN 有此儲存庫的 Contents 寫入權限。",
            )
        elif http_status == 404:
            code, status, message = (
                "github_not_found",
                502,
                "找不到 GitHub 儲存庫或部署分支；請確認 GITHUB_REPO 與 GITHUB_DEPLOY_BRANCH。",
            )
        elif http_status in (409, 422):
            code, status, message = (
                "branch_changed",
                409,
                "GitHub 分支在檢查後已有新提交，這次沒有覆蓋任何版本；請重新檢查 ZIP。",
            )
        elif http_status >= 500:
            code, status, message = "github_unavailable", 502, "GitHub 暫時無法處理更新，請稍後重試。"
        else:
            code, status, message = "github_api_error", 502, "GitHub 拒絕此更新；請檢查儲存庫權限與分支保護規則。"
        super().__init__(code, message, status)
        self.http_status = http_status
        self.operation = operation


@dataclass(frozen=True)
class PlannedFile:
    archive_name: str
    path: str
    size: int
    blob_sha: str
    mode: str
    change: str

    def public_dict(self) -> dict:
        return {"path": self.path, "size": self.size, "change": self.change}


class UploadVault:
    """Private temporary storage for one-upload preview/confirm flows."""

    def __init__(self, directory: str | os.PathLike | None = None):
        root = Path(directory) if directory else Path(tempfile.gettempdir()) / "line-bot-zip-updates"
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            root.chmod(0o700)
        except OSError:
            pass
        self.root = root.resolve()

    def cleanup_expired(self, now: float | None = None) -> None:
        cutoff = (time.time() if now is None else now) - UPLOAD_RETENTION_SECONDS
        try:
            for item in self.root.iterdir():
                if not _UPLOAD_ID_RE.fullmatch(item.stem) or item.suffix != ".zip":
                    continue
                try:
                    if item.is_file() and item.stat().st_mtime < cutoff:
                        item.unlink(missing_ok=True)
                except OSError:
                    continue
        except OSError:
            return

    def store(self, stream: BinaryIO) -> tuple[str, str, int]:
        self.cleanup_expired()
        upload_id = secrets.token_hex(16)
        path = self.path_for(upload_id)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        digest = hashlib.sha256()
        total = 0
        try:
            with os.fdopen(fd, "wb") as output:
                while True:
                    chunk = stream.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_ARCHIVE_BYTES:
                        raise ZipUpdateError(
                            "archive_too_large",
                            f"ZIP 超過 {MAX_ARCHIVE_BYTES // (1024 * 1024)} MiB 上限。",
                            413,
                        )
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if total == 0:
                raise ZipUpdateError("empty_upload", "請先選擇 ZIP 檔案。", 400)
            return upload_id, digest.hexdigest(), total
        except Exception:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def path_for(self, upload_id: str) -> Path:
        if not _UPLOAD_ID_RE.fullmatch(str(upload_id or "")):
            raise ZipUpdateError("invalid_preview", "預覽已失效，請重新選擇 ZIP。", 400)
        path = self.root / (upload_id + ".zip")
        try:
            if path.parent.resolve() != self.root or path.is_symlink():
                raise ZipUpdateError("invalid_preview", "預覽已失效，請重新選擇 ZIP。", 400)
        except OSError:
            raise ZipUpdateError("invalid_preview", "預覽已失效，請重新選擇 ZIP。", 400)
        return path

    def remove(self, upload_id: str) -> None:
        try:
            self.path_for(upload_id).unlink(missing_ok=True)
        except (OSError, ZipUpdateError):
            pass


def issue_preview_ticket(payload: Mapping, secret: str) -> str:
    if not secret:
        raise ZipUpdateError("admin_key_unconfigured", "後台管理金鑰尚未設定，無法建立安全預覽。", 503)
    body = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(body).rstrip(b"=")
    signature = hmac.new(secret.encode("utf-8"), b"zip-update-preview-v1." + encoded, hashlib.sha256).digest()
    return encoded.decode("ascii") + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")


def verify_preview_ticket(ticket: str, secret: str, now: int | None = None) -> dict:
    try:
        encoded_text, signature_text = str(ticket or "").split(".", 1)
        encoded = encoded_text.encode("ascii")
        supplied = base64.urlsafe_b64decode(signature_text + "=" * (-len(signature_text) % 4))
        expected = hmac.new(secret.encode("utf-8"), b"zip-update-preview-v1." + encoded, hashlib.sha256).digest()
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("bad signature")
        raw = base64.urlsafe_b64decode(encoded_text + "=" * (-len(encoded_text) % 4))
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("bad payload")
    except Exception:
        raise ZipUpdateError("invalid_preview", "預覽驗證失敗，請重新檢查 ZIP。", 400)
    current_time = int(time.time() if now is None else now)
    if int(payload.get("expires_at", 0)) < current_time:
        raise ZipUpdateError("preview_expired", "預覽已逾時，請重新檢查 ZIP。", 410)
    if not _UPLOAD_ID_RE.fullmatch(str(payload.get("upload_id", ""))):
        raise ZipUpdateError("invalid_preview", "預覽驗證失敗，請重新檢查 ZIP。", 400)
    if not _SHA256_RE.fullmatch(str(payload.get("archive_sha256", ""))):
        raise ZipUpdateError("invalid_preview", "預覽驗證失敗，請重新檢查 ZIP。", 400)
    if not _SHA_RE.fullmatch(str(payload.get("base_sha", ""))):
        raise ZipUpdateError("invalid_preview", "預覽驗證失敗，請重新檢查 ZIP。", 400)
    return payload


def validate_repo_branch(repo: str, branch: str) -> tuple[str, str]:
    repo = str(repo or "").strip()
    branch = str(branch or "").strip()
    owner, _, repository = repo.partition("/")
    if (
        not _REPO_RE.fullmatch(repo)
        or owner in (".", "..")
        or repository in (".", "..")
        or owner.startswith(".")
        or repository.startswith(".")
    ):
        raise ZipUpdateError("invalid_repo", "GITHUB_REPO 必須是 owner/repository 格式。", 503)
    if (
        not _BRANCH_RE.fullmatch(branch)
        or branch.startswith("/")
        or branch.endswith("/")
        or branch.endswith(".")
        or ".." in branch
        or "//" in branch
        or "@{" in branch
    ):
        raise ZipUpdateError("invalid_branch", "GITHUB_DEPLOY_BRANCH 設定格式無效。", 503)
    return repo, branch


def _safe_archive_path(raw: str) -> tuple[str, ...]:
    if not raw or "\x00" in raw or "\\" in raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise ZipUpdateError("unsafe_path", f"ZIP 含有不安全路徑：{raw[:120]}", 400)
    normalized = raw[:-1] if raw.endswith("/") else raw
    parts = tuple(normalized.split("/"))
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise ZipUpdateError("unsafe_path", f"ZIP 含有不安全路徑：{raw[:120]}", 400)
    try:
        part_sizes = [len(part.encode("utf-8")) for part in parts]
        path_size = len("/".join(parts).encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ZipUpdateError("unsafe_path", "ZIP 中有無效的 Unicode 檔案路徑。", 400) from exc
    if any(":" in part or size > 255 for part, size in zip(parts, part_sizes)):
        raise ZipUpdateError("unsafe_path", f"ZIP 含有無效路徑：{raw[:120]}", 400)
    if path_size > 1024:
        raise ZipUpdateError("unsafe_path", "ZIP 中有檔案路徑過長。", 400)
    return parts


def _should_ignore(parts: tuple[str, ...]) -> bool:
    lowered = tuple(part.casefold() for part in parts)
    if any(part in _IGNORED_COMPONENTS or part == "__macosx" for part in lowered):
        return True
    name = lowered[-1]
    if name in _IGNORED_NAMES or name.startswith("._"):
        return True
    if name.endswith((".pyc", ".pyo")):
        return True
    return False


def _validate_not_secret(parts: tuple[str, ...]) -> None:
    path = "/".join(parts)
    lowered = path.casefold()
    name = parts[-1].casefold()
    if name.startswith(".env") and name not in _SAFE_ENV_TEMPLATES:
        raise ZipUpdateError("secret_file", f"為避免把環境密鑰提交到 GitHub，ZIP 不可包含 {path}。", 400)
    if (
        name.endswith(tuple(_BLOCKED_SECRET_SUFFIXES))
        or name in _BLOCKED_SECRET_NAMES
        or name in {"id_rsa", "id_ed25519", "id_ecdsa"}
    ):
        raise ZipUpdateError("secret_file", f"為避免把私鑰提交到 GitHub，ZIP 不可包含 {path}。", 400)
    if any(component == ".git" for component in lowered.split("/")):
        raise ZipUpdateError("git_metadata", "ZIP 不可包含 .git 版本資料夾。", 400)


def _canonical_members(archive: zipfile.ZipFile, repo: str) -> list[tuple[zipfile.ZipInfo, str, str, int, str]]:
    candidates: list[tuple[zipfile.ZipInfo, tuple[str, ...]]] = []
    for info in archive.infolist():
        parts = _safe_archive_path(info.filename)
        if info.is_dir() or info.filename.endswith("/"):
            continue
        if _should_ignore(parts):
            continue
        _validate_not_secret(parts)
        if info.flag_bits & 0x1:
            raise ZipUpdateError("encrypted_zip", "ZIP 含有加密檔案；請改用未加密 ZIP。", 400)
        mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            raise ZipUpdateError("symlink", f"ZIP 不可包含符號連結：{'/'.join(parts)}", 400)
        if stat.S_ISDIR(mode):
            raise ZipUpdateError("invalid_entry", f"ZIP 檔案目錄標記錯誤：{'/'.join(parts)}", 400)
        if info.file_size < 0 or info.file_size > MAX_SINGLE_FILE_BYTES:
            raise ZipUpdateError(
                "file_too_large",
                f"單一檔案不可超過 {MAX_SINGLE_FILE_BYTES // (1024 * 1024)} MiB：{'/'.join(parts)}",
                413,
            )
        if info.file_size and info.file_size / max(info.compress_size, 1) > MAX_COMPRESSION_RATIO:
            raise ZipUpdateError("compression_ratio", f"ZIP 壓縮比例異常：{'/'.join(parts)}", 400)
        candidates.append((info, parts))

    if not candidates:
        raise ZipUpdateError("empty_zip", "ZIP 中沒有可更新的檔案。", 400)
    if len(candidates) > MAX_FILES:
        raise ZipUpdateError("too_many_files", f"ZIP 最多可包含 {MAX_FILES} 個檔案。", 400)
    total = sum(info.file_size for info, _ in candidates)
    if total > MAX_TOTAL_UNCOMPRESSED_BYTES:
        raise ZipUpdateError(
            "expanded_too_large",
            f"ZIP 解壓後不可超過 {MAX_TOTAL_UNCOMPRESSED_BYTES // (1024 * 1024)} MiB。",
            413,
        )

    repo_name = repo.rsplit("/", 1)[-1].casefold()
    project_roots = {repo_name, repo_name + "-main", "line-translator-bot-main"}
    strip_root = None
    for possible in project_roots:
        if any(parts[0].casefold() == possible and len(parts) > 1 for _, parts in candidates):
            strip_root = possible
            break

    result = []
    seen: set[str] = set()
    for info, original_parts in candidates:
        parts = (
            original_parts[1:]
            if strip_root and original_parts[0].casefold() == strip_root and len(original_parts) > 1
            else original_parts
        )
        if not parts:
            raise ZipUpdateError("unsafe_path", "ZIP 根目錄不可直接作為檔案路徑。", 400)
        path = "/".join(parts)
        collision_key = unicodedata.normalize("NFC", path).casefold()
        if collision_key in seen:
            raise ZipUpdateError("duplicate_path", f"ZIP 中有重複檔案路徑：{path}", 400)
        seen.add(collision_key)
        _validate_not_secret(parts)
        mode = (info.external_attr >> 16) & 0xFFFF
        requested_mode = "100755" if (mode & 0o111) else "100644"
        result.append((info, info.filename, path, info.file_size, requested_mode))
    return result


def inspect_zip_path(
    archive_path: str | os.PathLike,
    existing_tree: Mapping[str, Mapping],
    repo: str,
) -> tuple[list[PlannedFile], dict]:
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            if len(archive.infolist()) > MAX_ARCHIVE_ENTRIES:
                raise ZipUpdateError("too_many_entries", f"ZIP 最多可包含 {MAX_ARCHIVE_ENTRIES} 個檔案與資料夾項目。", 400)
            members = _canonical_members(archive, repo)
            planned_paths = {member[2] for member in members}
            sorted_planned_paths = sorted(planned_paths)
            sorted_existing_paths = sorted(existing_tree)
            for _, _, path, _, _ in members:
                path_parts = path.split("/")
                for depth in range(1, len(path_parts)):
                    parent = "/".join(path_parts[:depth])
                    existing_parent = existing_tree.get(parent)
                    if parent in planned_paths or (
                        existing_parent and existing_parent.get("type") == "blob"
                    ):
                        raise ZipUpdateError(
                            "path_conflict",
                            f"檔案路徑與另一個檔案衝突：{path}",
                            400,
                        )
                child_prefix = path + "/"
                existing_child = bisect.bisect_left(sorted_existing_paths, child_prefix)
                planned_child = bisect.bisect_left(sorted_planned_paths, child_prefix)
                if (
                    existing_child < len(sorted_existing_paths)
                    and sorted_existing_paths[existing_child].startswith(child_prefix)
                ) or (
                    planned_child < len(sorted_planned_paths)
                    and sorted_planned_paths[planned_child].startswith(child_prefix)
                ):
                    raise ZipUpdateError(
                        "path_conflict",
                        f"檔案路徑與子目錄中的檔案衝突：{path}",
                        400,
                    )
            plan: list[PlannedFile] = []
            for info, source_name, path, declared_size, new_mode in members:
                blob_hash = hashlib.sha1(f"blob {declared_size}\0".encode("ascii"))
                actual_size = 0
                with archive.open(info, "r") as source:
                    while True:
                        chunk = source.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        actual_size += len(chunk)
                        if actual_size > MAX_SINGLE_FILE_BYTES:
                            raise ZipUpdateError("file_too_large", f"單一檔案超過安全上限：{path}", 413)
                        blob_hash.update(chunk)
                if actual_size != declared_size:
                    raise ZipUpdateError("invalid_zip", f"ZIP 檔案大小不一致：{path}", 400)
                blob_sha = blob_hash.hexdigest()
                current = existing_tree.get(path)
                if current is None:
                    mode = new_mode
                    change = "added"
                else:
                    if current.get("type") not in (None, "blob") or current.get("mode") not in ("100644", "100755"):
                        raise ZipUpdateError(
                            "unsupported_target",
                            f"GitHub 目標路徑不是一般檔案，已拒絕覆蓋：{path}",
                            400,
                        )
                    mode = current.get("mode", "100644")
                    change = "unchanged" if current.get("sha") == blob_sha else "modified"
                plan.append(PlannedFile(source_name, path, declared_size, blob_sha, mode, change))

    except ZipUpdateError:
        raise
    except (zipfile.BadZipFile, EOFError, RuntimeError, OSError, ValueError, NotImplementedError, zlib.error) as exc:
        raise ZipUpdateError("invalid_zip", "ZIP 檔案損毀、格式無效或含有無法解壓的內容。", 400) from exc

    changed = [entry for entry in plan if entry.change != "unchanged"]
    if len(changed) > MAX_CHANGED_FILES:
        raise ZipUpdateError("too_many_changes", f"單次最多可更新 {MAX_CHANGED_FILES} 個檔案。", 400)
    summary = {
        "total_files": len(plan),
        "added": [entry.public_dict() for entry in plan if entry.change == "added"],
        "modified": [entry.public_dict() for entry in plan if entry.change == "modified"],
        "unchanged": [entry.public_dict() for entry in plan if entry.change == "unchanged"],
        "changed_files": len(changed),
        "expanded_bytes": sum(entry.size for entry in plan),
    }
    fingerprint_payload = [
        [entry.path, entry.size, entry.blob_sha, entry.mode, entry.change]
        for entry in sorted(plan, key=lambda item: item.path)
    ]
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    summary["plan_sha256"] = fingerprint
    return plan, summary


class GitHubClient:
    """Small Git Data API client; token and repo are server-side only."""

    def __init__(self, token: str, repo: str, branch: str, timeout: float = 20):
        self.repo, self.branch = validate_repo_branch(repo, branch)
        self.token = str(token or "").strip()
        if not self.token:
            raise ZipUpdateError("github_unconfigured", "伺服器尚未設定 GitHub 更新金鑰。", 503)
        self.timeout = timeout
        self.base_url = "https://api.github.com/repos/" + "/".join(
            urllib.parse.quote(piece, safe="") for piece in self.repo.split("/")
        )

    def _request(self, method: str, endpoint: str, payload=None, raw_body: bytes | None = None) -> dict:
        url = self.base_url + endpoint
        data = raw_body
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {
            "Authorization": "Bearer " + self.token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "line-translator-mobile-zip-updater",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise ZipUpdateError("github_response_too_large", "GitHub 回應資料過大，請稍後重試。", 502)
                return json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as exc:
            try:
                response_body = exc.read(4096)
            except Exception:
                response_body = b""
            raise GitHubAPIError(exc.code, endpoint.split("?", 1)[0], response_body) from exc
        except ZipUpdateError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise ZipUpdateError("github_unavailable", "無法連線至 GitHub，請稍後重試。", 502) from exc

    def get_branch_head(self) -> str:
        branch = urllib.parse.quote(self.branch, safe="")
        data = self._request("GET", "/git/ref/heads/" + branch)
        sha = str(((data.get("object") or {}).get("sha")) or "")
        if not _SHA_RE.fullmatch(sha):
            raise ZipUpdateError("github_bad_response", "GitHub 未回傳有效的分支版本。", 502)
        return sha

    def get_branch_snapshot(self) -> tuple[str, dict[str, dict]]:
        head_sha = self.get_branch_head()
        commit = self._request("GET", "/git/commits/" + urllib.parse.quote(head_sha, safe=""))
        tree_sha = str(((commit.get("tree") or {}).get("sha")) or "")
        if not _SHA_RE.fullmatch(tree_sha):
            raise ZipUpdateError("github_bad_response", "GitHub 未回傳有效的檔案樹版本。", 502)
        tree = self._request(
            "GET",
            "/git/trees/" + urllib.parse.quote(tree_sha, safe="") + "?recursive=1",
        )
        if tree.get("truncated"):
            raise ZipUpdateError(
                "github_tree_truncated",
                "GitHub 儲存庫檔案太多，無法安全完成完整比對。",
                502,
            )
        entries: dict[str, dict] = {}
        for item in tree.get("tree", []):
            path = item.get("path")
            if isinstance(path, str):
                entries[path] = {
                    "sha": item.get("sha"),
                    "mode": item.get("mode"),
                    "type": item.get("type"),
                }
        return head_sha, entries

    def create_blob(self, content: bytes) -> str:
        body = b'{"content":"' + base64.b64encode(content) + b'","encoding":"base64"}'
        data = self._request("POST", "/git/blobs", raw_body=body)
        sha = str(data.get("sha") or "")
        if not _SHA_RE.fullmatch(sha):
            raise ZipUpdateError("github_bad_response", "GitHub 未回傳有效的檔案版本。", 502)
        return sha

    def create_tree(self, base_tree: str, entries: list[dict]) -> str:
        data = self._request("POST", "/git/trees", {
            "base_tree": base_tree,
            "tree": entries,
        })
        sha = str(data.get("sha") or "")
        if not _SHA_RE.fullmatch(sha):
            raise ZipUpdateError("github_bad_response", "GitHub 未回傳有效的新檔案樹。", 502)
        return sha

    def create_commit(self, message: str, tree_sha: str, parent_sha: str) -> str:
        data = self._request("POST", "/git/commits", {
            "message": message,
            "tree": tree_sha,
            "parents": [parent_sha],
        })
        sha = str(data.get("sha") or "")
        if not _SHA_RE.fullmatch(sha):
            raise ZipUpdateError("github_bad_response", "GitHub 未回傳有效的新提交版本。", 502)
        return sha

    def update_branch(self, commit_sha: str) -> None:
        branch = urllib.parse.quote(self.branch, safe="")
        self._request("PATCH", "/git/refs/heads/" + branch, {"sha": commit_sha, "force": False})


def sanitize_commit_message(value: str | None) -> str:
    raw = str(value or "手機 ZIP 更新")
    raw = raw.encode("utf-8", "replace").decode("utf-8")
    message = " ".join("".join(" " if ord(char) < 32 or ord(char) == 127 else char for char in raw).split())
    message = message[:140].strip()
    return message or "手機 ZIP 更新"


def apply_plan(archive_path: str | os.PathLike, plan: list[PlannedFile], client: GitHubClient, base_sha: str, message: str) -> dict:
    changed = [entry for entry in plan if entry.change != "unchanged"]
    if not changed:
        return {"changed": False, "commit_sha": None, "added": 0, "modified": 0}

    tree_entries = []
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            for entry in changed:
                content_parts = []
                total = 0
                with archive.open(entry.archive_name, "r") as source:
                    while True:
                        chunk = source.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_SINGLE_FILE_BYTES:
                            raise ZipUpdateError("file_too_large", f"單一檔案超過安全上限：{entry.path}", 413)
                        content_parts.append(chunk)
                content = b"".join(content_parts)
                if len(content) != entry.size:
                    raise ZipUpdateError("invalid_zip", f"ZIP 檔案在檢查後發生變更：{entry.path}", 400)
                actual_sha = hashlib.sha1(f"blob {len(content)}\0".encode("ascii") + content).hexdigest()
                if actual_sha != entry.blob_sha:
                    raise ZipUpdateError("archive_changed", "ZIP 檔案內容在檢查後改變，請重新選擇並檢查。", 400)
                blob_sha = client.create_blob(content)
                if blob_sha != entry.blob_sha:
                    raise ZipUpdateError("github_hash_mismatch", f"GitHub 檔案驗證不一致：{entry.path}", 502)
                tree_entries.append({
                    "path": entry.path,
                    "mode": entry.mode,
                    "type": "blob",
                    "sha": blob_sha,
                })
    except ZipUpdateError:
        raise
    except (zipfile.BadZipFile, EOFError, RuntimeError, OSError, NotImplementedError, zlib.error) as exc:
        raise ZipUpdateError("invalid_zip", "ZIP 檔案在提交前無法重新讀取，請重新檢查。", 400) from exc

    base_commit = client._request("GET", "/git/commits/" + urllib.parse.quote(base_sha, safe=""))
    base_tree = str(((base_commit.get("tree") or {}).get("sha")) or "")
    if not _SHA_RE.fullmatch(base_tree):
        raise ZipUpdateError("github_bad_response", "GitHub 未回傳有效的原始檔案樹。", 502)
    new_tree = client.create_tree(base_tree, tree_entries)
    new_commit = client.create_commit(sanitize_commit_message(message), new_tree, base_sha)
    client.update_branch(new_commit)
    return {
        "changed": True,
        "commit_sha": new_commit,
        "added": sum(entry.change == "added" for entry in changed),
        "modified": sum(entry.change == "modified" for entry in changed),
    }
