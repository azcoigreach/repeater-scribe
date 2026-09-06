import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def application(tmp_path_factory):
    directory = tmp_path_factory.mktemp("browser-acceptance")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"https://127.0.0.1:{port}"
    environment = {key: value for key, value in os.environ.items() if not key.startswith("ASLT_")}
    environment.update(
        {
            "PYTHONPATH": str(ROOT / "src"),
            "ASLT_DATABASE_URL": f"sqlite:///{directory / 'browser.db'}",
            "ASLT_DATA_DIR": str(directory),
            "ASLT_TMP_DIR": str(directory / "tmp"),
            "ASLT_ARCHIVE_PATHS": str(directory / "archive"),
            "ASLT_DEPLOYMENT_MODE": "internet",
            "ASLT_AUTH_MODE": "oidc",
            "ASLT_PUBLIC_BASE_URL": origin,
            "ASLT_SESSION_SECRET": "browser-test-only-secret-0123456789abcdef",
            "ASLT_SESSION_SECRET_FILE": "",
            "ASLT_OIDC_ISSUER_URL": "https://identity.example.test",
            "ASLT_OIDC_CLIENT_ID": "browser-test",
            "ASLT_OIDC_CLIENT_SECRET": "test-only",
            "ASLT_OIDC_CLIENT_SECRET_FILE": "",
            "ASLT_ALLOWED_HOSTS": "127.0.0.1",
            "ASLT_AMI_ENABLED": "false",
            "ASLT_AMI_CONTROL_ENABLED": "false",
            "ASLT_FAVORITE_STATS_ENABLED": "false",
            "ASLT_AUTO_PROCESS": "false",
            "ASLT_LIVE_TRANSCRIPTION": "false",
            "ASLT_QRZ_USERNAME": "",
            "ASLT_QRZ_PASSWORD": "",
            "ASLT_QRZ_PASSWORD_FILE": "",
            "ASLT_REQUEST_RATE_PER_MINUTE": "10000",
            "ASLT_RETENTION_DAYS": "0",
            "BROWSER_PORT": str(port),
            "BROWSER_IDS": str(directory / "ids.json"),
            "BROWSER_KEY": str(directory / "key.pem"),
            "BROWSER_CERT": str(directory / "cert.pem"),
        }
    )
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            environment["BROWSER_KEY"],
            "-out",
            environment["BROWSER_CERT"],
            "-days",
            "1",
            "-subj",
            "/CN=127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
    )
    with (directory / "server.log").open("w") as log:
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "browser_tests/server.py")],
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=log,
        )
        try:
            with httpx.Client(verify=False, trust_env=False) as client:
                for _ in range(200):
                    if process.poll() is not None:
                        pytest.fail((directory / "server.log").read_text())
                    try:
                        if client.get(origin + "/api/v1/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.1)
                else:
                    pytest.fail(
                        "Acceptance server did not start: " + (directory / "server.log").read_text()
                    )
            yield origin, json.loads((directory / "ids.json").read_text())
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def page(browser, application):
    origin, _ = application
    context = browser.new_context(
        ignore_https_errors=True, timezone_id="UTC", viewport={"width": 1440, "height": 1000}
    )
    context.add_cookies(
        [
            {
                "name": "__Host-aslt_session",
                "value": "browser-operator",
                "url": origin,
                "secure": True,
                "httpOnly": True,
                "sameSite": "Lax",
            }
        ]
    )
    # All external services are stubbed; fail any accidental internet request.
    context.route(
        "**/*",
        lambda route: (
            route.continue_() if route.request.url.startswith(origin + "/") else route.abort()
        ),
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    yield page
    context.close()
    assert errors == []
