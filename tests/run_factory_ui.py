"""Run the real DOM checks against an isolated local fake LINE/AI fixture."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


def main():
    root = Path(__file__).resolve().parent
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
    raise SystemExit(main())
