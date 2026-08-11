import os
import socket
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("RUN_BROWSER_E2E") != "true", reason="opt-in Playwright suite")


def _port():
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); value = sock.getsockname()[1]; sock.close(); return value


def test_complete_learner_browser_journey(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    database_url = f"sqlite:///{tmp_path / 'browser.db'}"
    environment = {**os.environ, "DATABASE_URL": database_url, "SECRET_KEY": "browser-session-secret",
                   "DATA_ENCRYPTION_KEY": "browser-data-secret", "COOKIE_SECURE": "false",
                   "LEARNER_TRAINING_ENABLED": "true", "VERIFICATION_INTERVAL_SECONDS": "3600",
                   "PYTHONPATH": os.getcwd()}
    subprocess.run([sys.executable, "tests/e2e_seed.py"], env=environment, check=True)
    port = _port()
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1",
                               "--port", str(port), "--log-level", "warning"], env=environment)
    try:
        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=.2): break
            except OSError: time.sleep(.1)
        else: raise AssertionError("test server did not start")

        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch()
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/")
            page.get_by_label("Username").fill("browser-learner")
            page.get_by_label("Password").fill("browser-password")
            page.get_by_role("button", name="Login").click()
            page.wait_for_url("**/dashboard")
            page.get_by_role("heading", name="Browser acceptance event").wait_for()

            page.get_by_role("button", name="Show team access").click()
            page.get_by_text("BROWSER-SUDO-PASSWORD", exact=True).wait_for()
            page.get_by_role("button", name="Close").click()
            page.get_by_role("button", name="Reveal hint 1").click()
            page.get_by_text("Check the SSH server configuration", exact=False).wait_for()

            # Backend state/scoring is covered independently; this route
            # interception proves the browser reaction and refresh contract.
            page.route("**/api/vms/*/modules/*/verify", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body='{"result":"pass","summary":"Remediation verified.","status":"completed","score":{"total":200}}'))
            subprocess.run([sys.executable, "-c",
                "from sqlalchemy import create_engine,text; import os; e=create_engine(os.environ['DATABASE_URL']); "
                "c=e.connect(); c.execute(text(\"UPDATE vm_modules SET status='completed', completed=1, first_completed_at=CURRENT_TIMESTAMP\")); c.commit()"],
                env=environment, check=True)
            page.get_by_role("button", name="Verify fix").click()
            page.get_by_text("Verified", exact=True).wait_for()
            page.wait_for_timeout(1100)
            page.get_by_text("200", exact=True).first.wait_for()

            subprocess.run([sys.executable, "-c",
                "from sqlalchemy import create_engine,text; import os; e=create_engine(os.environ['DATABASE_URL']); "
                "c=e.connect(); c.execute(text(\"UPDATE vm_modules SET status='regressed', completed=0\")); c.commit()"],
                env=environment, check=True)
            page.reload()
            page.get_by_text("A completed task has regressed", exact=False).wait_for()
            page.get_by_role("link", name="Scoreboard").click()
            page.get_by_role("heading", name="Scoreboard").wait_for()
            page.get_by_text("Browser Team", exact=True).wait_for()
            browser.close()
    finally:
        server.terminate()
        try: server.wait(timeout=5)
        except subprocess.TimeoutExpired: server.kill()
