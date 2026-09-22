"""Browser coverage for the microphone review and approval checkpoints."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MOCK_SPEECH_SCRIPT = """
window.__recognizers = [];
class MockRecognition {
  start() {
    window.__recognizers.push(this);
    this.onstart?.();
  }
  abort() { this.onend?.(); }
}
window.SpeechRecognition = MockRecognition;
window.__say = (text) => {
  const recognition = window.__recognizers.at(-1);
  if (!recognition) throw new Error('Voice recognition has not started');
  recognition.onresult({results: [{0: {transcript: text}, isFinal: true, length: 1}]});
};
window.SpeechSynthesisUtterance = class {
  constructor(text) { this.text = text; }
};
Object.defineProperty(window, 'speechSynthesis', {
  configurable: true,
  value: {
    cancel() {},
    speak(utterance) { setTimeout(() => utterance.onend?.(), 50); }
  }
});
"""


@pytest.fixture(params=[False, True], ids=["local", "public-demo"])
def voice_ui_server(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[str]:
    """Serve the real app with isolated persistence for a single browser journey."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'voice-ui.db').as_posix()}"
    env["PUBLIC_BASE_URL"] = base_url
    env["VOICE_ADAPTER"] = "simulated"
    env["BOOKING_ADAPTER"] = "simulated"
    env["PUBLIC_DEMO_MODE"] = "true" if request.param else "false"
    if request.param:
        env["DEMO_SESSION_SECRET"] = "scripted-browser-test-session-secret-2026"
    server = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1", "--port", str(port),
        ],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if server.poll() is not None:
                raise AssertionError(f"Jeevan server exited: {server.stderr.read().decode()}")
            try:
                with urlopen(f"{base_url}/health", timeout=0.5) as response:
                    if response.status == 200:
                        break
            except (OSError, URLError):
                time.sleep(0.1)
        else:
            raise AssertionError("Jeevan server did not become healthy within 20 seconds")
        yield base_url
    finally:
        server.terminate()
        try:
            server.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.communicate(timeout=5)


@pytest.mark.browser
@pytest.mark.skipif(os.getenv("RUN_BROWSER_E2E") != "1", reason="set RUN_BROWSER_E2E=1")
def test_spoken_request_waits_for_start_and_above_budget_quote_waits_for_approval(
    voice_ui_server: str,
) -> None:
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        context.add_init_script(MOCK_SPEECH_SCRIPT)
        page = context.new_page()
        browser_errors: list[str] = []
        failed_responses: list[str] = []
        page.on("pageerror", lambda error: browser_errors.append(str(error)))
        page.on(
            "response",
            lambda response: failed_responses.append(response.url)
            if response.status >= 400 else None,
        )
        page.on(
            "console",
            lambda message: (
                browser_errors.append(message.text) if message.type == "error" else None
            ),
        )
        try:
            page.goto(voice_ui_server)
            page.get_by_role("button", name="Start listening").click()
            page.evaluate(
                "window.__say('Book AC servicing this Saturday after 2 PM under Rs 1,000')"
            )

            start_button = page.locator("#voice-start-task")
            expect(start_button).to_be_enabled()
            expect(page.locator("#voice-review")).to_be_visible()
            expect(page.locator("#voice-reply")).to_contain_text("Please review these details")
            assert page.evaluate("fetch('/api/v1/tasks').then(r => r.json())") == []

            page.wait_for_function("window.__recognizers.length >= 2")
            page.evaluate("window.__say('start task')")
            expect(page.locator("#customer-task .approval-card")).to_be_visible()
            task = page.evaluate(
                "fetch('/api/v1/tasks').then(r => r.json()).then(tasks => tasks[0])"
            )
            assert task["status"] == "awaiting_approval"
            assert task["quote_paise"] > task["budget_paise"]
            assert task["confirmation_ref"] is None

            page.wait_for_function("window.__recognizers.length >= 3")
            page.evaluate("window.__say('approve quote')")
            expect(page.locator("#voice-reply")).to_contain_text("booking is complete")
            completed = page.evaluate(
                "fetch('/api/v1/tasks').then(r => r.json()).then(tasks => tasks[0])"
            )
            assert completed["id"] == task["id"]
            assert completed["status"] == "completed"
            assert completed["confirmation_ref"]
            assert len(page.evaluate("fetch('/api/v1/tasks').then(r => r.json())")) == 1
            assert not browser_errors, (browser_errors, failed_responses)
        finally:
            browser.close()


@pytest.mark.browser
@pytest.mark.skipif(os.getenv("RUN_BROWSER_E2E") != "1", reason="set RUN_BROWSER_E2E=1")
def test_spoken_follow_up_completes_missing_details_before_single_booking(
    voice_ui_server: str,
) -> None:
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        context.add_init_script(MOCK_SPEECH_SCRIPT)
        page = context.new_page()
        browser_errors: list[str] = []
        page.on("pageerror", lambda error: browser_errors.append(str(error)))
        try:
            page.goto(voice_ui_server)
            page.get_by_role("button", name="Start listening").click()
            page.evaluate("window.__say('Book AC servicing')")

            expect(page.locator("#voice-reply")).to_contain_text("the date")
            expect(page.locator("#voice-reply")).to_contain_text("a time")
            expect(page.locator("#voice-reply")).to_contain_text("maximum budget")
            expect(page.locator("#voice-start-task")).to_be_disabled()
            assert page.evaluate("fetch('/api/v1/tasks').then(r => r.json())") == []

            page.wait_for_function("window.__recognizers.length >= 2")
            page.evaluate("window.__say('26 september 2026, after 2 pm, budget is 2500')")

            expect(page.locator("#voice-start-task")).to_be_enabled()
            expect(page.locator("#requested-date")).to_have_value("2026-09-26")
            expect(page.locator("#time-window")).to_have_value("After 2:00 PM")
            expect(page.locator("#budget")).to_have_value("2500")
            assert page.evaluate("fetch('/api/v1/tasks').then(r => r.json())") == []

            page.wait_for_function("window.__recognizers.length >= 3")
            page.evaluate("window.__say('start task')")
            expect(page.locator("#customer-task .status-pill")).to_have_text("Completed")
            tasks = page.evaluate("fetch('/api/v1/tasks').then(r => r.json())")
            assert len(tasks) == 1
            task = tasks[0]
            assert task["status"] == "completed"
            assert task["budget_paise"] == 250_000
            assert task["confirmation_ref"]
            assert sum(event["event_type"] == "browser.confirmed" for event in task["events"]) == 1
            assert not browser_errors, browser_errors
        finally:
            browser.close()
