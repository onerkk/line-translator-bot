from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import app


APP_PATH = Path(__file__).with_name("app.py")
APP_SOURCE = APP_PATH.read_text(encoding="utf-8")
APP_TREE = ast.parse(APP_SOURCE)
SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.IGNORECASE | re.DOTALL)


def _javascript_containers():
    containers = []
    for node in ast.walk(APP_TREE):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        value = node.value
        if "<script" in value.lower() or "self.addEventListener" in value:
            containers.append((node, value))
    return sorted(containers, key=lambda item: item[0].lineno)


def _embedded_javascript_assets():
    assets = []
    for node, value in _javascript_containers():
        if "<script" not in value.lower():
            assets.append((f"app.py:{node.lineno}:service-worker", value))
            continue
        for index, match in enumerate(SCRIPT_RE.finditer(value), start=1):
            attributes, body = match.groups()
            if re.search(r"\bsrc\s*=", attributes, re.IGNORECASE) or not body.strip():
                continue
            assets.append((f"app.py:{node.lineno}:script-{index}", body))
    return assets


def test_all_python_embedded_javascript_uses_raw_string_containers():
    containers = _javascript_containers()
    assert len(containers) >= 6

    for node, _value in containers:
        source = ast.get_source_segment(APP_SOURCE, node).lstrip()
        assert source.startswith(("r'''", 'r"""', "R'''", 'R"""')), (
            f"embedded JavaScript at app.py:{node.lineno} must use a raw Python "
            "string so JavaScript escapes cannot be expanded before delivery"
        )


def test_reported_validation_issue_line_keeps_javascript_newline_escapes():
    expected = r"(data.validation_issues?'\n'+data.validation_issues.join('\n'):'')"
    assert expected in app.ADMIN_HTML
    assert "data.validation_issues?'" + "\n" not in app.ADMIN_HTML


def test_every_embedded_javascript_asset_parses_as_delivered():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable; raw-string and exact regression guards still run")

    assets = _embedded_javascript_assets()
    assert len(assets) >= 9
    for label, javascript in assets:
        result = subprocess.run(
            [node, "--check", "-"],
            input=javascript,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        assert result.returncode == 0, f"{label}\n{result.stderr}"


def test_admin_bootstrap_updates_worker_before_large_application_script():
    scripts = [
        match.group(2)
        for match in SCRIPT_RE.finditer(app.ADMIN_HTML)
        if not re.search(r"\bsrc\s*=", match.group(1), re.IGNORECASE)
        and match.group(2).strip()
    ]
    worker_index = next(i for i, script in enumerate(scripts) if "serviceWorker.register" in script)
    application_index = next(i for i, script in enumerate(scripts) if "var KEY=" in script)

    assert worker_index < application_index
    assert "/sw.js?v=62" in scripts[worker_index]


def test_admin_html_and_worker_cannot_restore_a_stale_cached_console():
    client = app.app.test_client()
    admin_response = client.get("/admin")
    worker_response = client.get("/sw.js")

    assert admin_response.status_code == 200
    assert worker_response.status_code == 200
    assert "no-store" in admin_response.headers["Cache-Control"]
    assert "no-store" in worker_response.headers["Cache-Control"]
    assert "caches.open" not in app.SW_JS
    assert "caches.match" not in app.SW_JS
    assert "addEventListener('fetch'" not in app.SW_JS
    assert "startsWith(CACHE_PREFIX)" in app.SW_JS


def test_runtime_ids_are_json_encoded_before_javascript_embedding(monkeypatch):
    hostile_id = "client';\n</script><script>throw new Error('injected')//"
    encoded = (
        json.dumps(hostile_id, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )
    monkeypatch.setattr(app, "GOOGLE_CLIENT_ID", hostile_id)
    monkeypatch.setattr(app, "LIFF_ID", hostile_id)

    client = app.app.test_client()
    pages = {
        "/admin": client.get("/admin").get_data(as_text=True),
        "/google-test": client.get("/google-test").get_data(as_text=True),
        "/liff/form": client.get("/liff/form").get_data(as_text=True),
    }
    for route, html in pages.items():
        assert encoded in html, route
        assert "</script><script>throw new Error" not in html
        assert "__GOOGLE_CLIENT_ID_JSON__" not in html
        assert "__LIFF_ID_JSON__" not in html

    node = shutil.which("node")
    if not node:
        return
    for route, html in pages.items():
        for index, match in enumerate(SCRIPT_RE.finditer(html), start=1):
            if re.search(r"\bsrc\s*=", match.group(1), re.IGNORECASE):
                continue
            result = subprocess.run(
                [node, "--check", "-"],
                input=match.group(2),
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
            assert result.returncode == 0, f"{route}:script-{index}\n{result.stderr}"
