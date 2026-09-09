"""Run the real DOM checks against an isolated local fake LINE/AI fixture."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


# The shipped CI96 frontend already implements this lifecycle API. CI98 only
# added two version comments to that same JS body. Keep the verified release
# compatible; the real DOM behavior checks below still run for every release.
_COMPATIBLE_UNMARKED_ADMIN_ASSETS = {
    "2f3a2a3f61997c1bfcbcefe427bcf40a018fc469727ac0bdcc102231be8feae6": "ci96-lifecycle-verified",
}
_COMPATIBLE_UNMARKED_MEMBER_ASSETS = {
    "0b718a77956eb26f778c85f03699dc7a456e8d060a42ee005c2c6dfae98d3d08": "ui104-lifecycle-verified",
}
_COMPATIBLE_UNMARKED_FORM_ASSETS = {
    "ef317649cbe49d5a181e67acfdf15e3784f55e944859275ceb98d43164c7d8f8": "ui104-lifecycle-verified",
}
_ASSETS = (
    ("admin", "static/admin_factory.js", "FACTORY_ADMIN", _COMPATIBLE_UNMARKED_ADMIN_ASSETS),
    ("member", "static/line_factory.js", "FACTORY_MEMBER", _COMPATIBLE_UNMARKED_MEMBER_ASSETS),
    ("form", "static/liff_forms.js", "FACTORY_FORM", _COMPATIBLE_UNMARKED_FORM_ASSETS),
)


def _check_asset(repo, label, relative_path, prefix, compatible):
    path = repo / relative_path
    raw = path.read_bytes() if path.is_file() else b""
    normalized = raw.replace(b"\r\n", b"\n")
    source = normalized.decode("utf-8", errors="replace")
    build = re.search(r"^// " + prefix + r"_BUILD: (.+)$", source, re.M)
    api = re.search(r"^// " + prefix + r"_LIFECYCLE_API: (\d+)$", source, re.M)
    digest = hashlib.sha256(raw).hexdigest()
    # A Git checkout can convert line endings without changing JS behavior.
    content_digest = hashlib.sha256(normalized).hexdigest()
    compatible_build = compatible.get(content_digest)
    version = build.group(1).strip() if build else compatible_build or "unmarked/missing"
    supported = api.group(1) == "1" if api else bool(compatible_build)
    print(f"CHECK {label} asset: {path.resolve()} build={version} sha256={digest}", flush=True)
    if not supported:
        misplaced = repo / path.name
        location_hint = (
            f" A file named {path.name} exists at repository root; it must be under static/."
            if misplaced.is_file() else ""
        )
        raise RuntimeError(
            f"{relative_path}: missing, old, or incompatible lifecycle API; expected API 1 "
            "or the verified compatible frontend."
            + location_hint +
            " Apply the runtime files and tests from the same update, or run "
            "python apply_ci106_update.py --repo . from the repository root. "
            "This check does not modify source files or skip the DOM behavior tests."
        )


def check_admin_asset(repo):
    """Retain the previous entry point for callers checking only admin."""
    return _check_asset(repo, *_ASSETS[0])


def check_frontend_assets(repo):
    """Report every incompatible runtime asset before launching Node or Flask."""
    failures = []
    for asset in _ASSETS:
        try:
            _check_asset(repo, *asset)
        except RuntimeError as exc:
            failures.append(str(exc))
    if failures:
        raise RuntimeError("Factory frontend update is incomplete:\n- " + "\n- ".join(failures))


def main():
    root = Path(__file__).resolve().parent
    check_frontend_assets(root.parent)
    env = dict(os.environ)
    if not env.get("FACTORY_UI_PORT"):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            env["FACTORY_UI_PORT"] = str(sock.getsockname()[1])
    env["FACTORY_UI_URL"] = "http://127.0.0.1:" + env["FACTORY_UI_PORT"]
    env["FACTORY_ACK_WORKER_ENABLED"] = "0"
    with tempfile.TemporaryFile() as log:
        server = subprocess.Popen([sys.executable, str(root / "factory_ui_server.py")],
                                  stdout=log, stderr=log, env=env)
        try:
            for _ in range(150):
                if server.poll() is not None:
                    raise RuntimeError("UI fixture exited before becoming ready")
                try:
                    with urllib.request.urlopen(env["FACTORY_UI_URL"] + "/preview-fixture-health", timeout=.5) as response:
                        health = json.load(response)
                    assert health["build"] == health["reminder_build"]
                    print("CHECK fixture modules: " + health["build"], flush=True)
                    break
                except OSError:
                    time.sleep(.03)
            else:
                raise RuntimeError("UI fixture did not start")
            for filename in ("factory_ui_lifecycle.cjs", "member_ui_lifecycle.cjs", "factory_ui_smoke.cjs", "unified_menu_ui_smoke.cjs"):
                result = subprocess.run(["node", str(root / filename)], timeout=45, env=env)
                if result.returncode:
                    return result.returncode
            return 0
        except (RuntimeError, AssertionError, subprocess.TimeoutExpired):
            log.seek(0)
            print(log.read().decode(errors="replace")[-12000:], file=sys.stderr, flush=True)
            raise
        finally:
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-assets", action="store_true", help="check the matched frontend before installing test dependencies")
    args = parser.parse_args()
    if args.check_assets:
        check_frontend_assets(Path(__file__).resolve().parents[1])
        raise SystemExit(0)
    raise SystemExit(main())
