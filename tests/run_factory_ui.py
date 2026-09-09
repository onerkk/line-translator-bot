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


def check_admin_asset(repo):
    path = repo / "static" / "admin_factory.js"
    raw = path.read_bytes() if path.is_file() else b""
    source = raw.decode("utf-8", errors="replace")
    build = re.search(r"^// FACTORY_ADMIN_BUILD: (.+)$", source, re.M)
    api = re.search(r"^// FACTORY_ADMIN_LIFECYCLE_API: (\d+)$", source, re.M)
    digest = hashlib.sha256(raw).hexdigest()
    print(f"CHECK admin asset: {path.resolve()} build={build.group(1) if build else 'legacy/missing'} sha256={digest}", flush=True)
    if not api or api.group(1) != "1":
        raise RuntimeError(
            "Factory UI tests require the page-lifecycle update in static/admin_factory.js. "
            "The file loaded above is old, missing, or incompatible. "
            "Apply static/admin_factory.js and tests/ from the same update ZIP at their repository paths."
        )


def main():
    root = Path(__file__).resolve().parent
    check_admin_asset(root.parent)
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
            for filename in ("factory_ui_lifecycle.cjs", "factory_ui_smoke.cjs", "unified_menu_ui_smoke.cjs"):
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
        check_admin_asset(Path(__file__).resolve().parents[1])
        raise SystemExit(0)
    raise SystemExit(main())
