"""Run the repository regressions in temporary state with external I/O blocked.

python run_translation_offline_checks.py
python run_translation_offline_checks.py test_translation_speed_economy.py

The existing SDK connection-reuse regression needs a local loopback server;
only 127.0.0.1/::1 are allowed. No provider or LINE connection is allowed.
"""
import os
from pathlib import Path
import socket
import sys
import tempfile
from unittest.mock import patch


def main():
    repo = Path(__file__).resolve().parent
    os.chdir(repo)
    sys.path.insert(0, str(repo))
    from benchmark_translation_cp import _offline_environment

    with tempfile.TemporaryDirectory(prefix="translation-offline-checks-") as state:
        _offline_environment(state)
        os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        connect, connect_ex = socket.socket.connect, socket.socket.connect_ex

        def guarded(original):
            def run(sock, address, *args, **kwargs):
                if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
                    return original(sock, address, *args, **kwargs)
                raise RuntimeError("External network disabled in offline regression")
            return run

        with patch.object(socket.socket, "connect", guarded(connect)), \
                patch.object(socket.socket, "connect_ex", guarded(connect_ex)):
            import ai_provider
            ai_provider.PROVIDER_CONFIG_PATH = str(Path(state) / "providers.json")
            import pytest
            tests = sys.argv[1:] or sorted(path.name for path in repo.glob("test_*.py"))
            return pytest.main(["-q", "-p", "pytest_subtests", "--tb=short", *tests])


if __name__ == "__main__":
    raise SystemExit(main())
