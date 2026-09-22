"""Record a reproducible, clearly labeled scripted Jeevan walkthrough.

This is a silent screen recording using mocked browser speech input. For an
application video, record your own microphone separately with the same steps.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "portfolio" / "jeevan_scripted_demo.webm"
MOCK_SPEECH = """
window.__recognizers = [];
class MockRecognition {
  start() { window.__recognizers.push(this); this.onstart?.(); }
  abort() { this.onend?.(); }
  stop() { this.onend?.(); }
}
window.SpeechRecognition = MockRecognition;
window.__say = (text) => {
  const recognition = window.__recognizers.at(-1);
  if (!recognition) throw new Error('Speech recognition has not started');
  recognition.onresult({results: [{0: {transcript: text}, isFinal: true, length: 1}]});
};
window.SpeechSynthesisUtterance = class {
  constructor(text) { this.text = text; }
};
Object.defineProperty(window, 'speechSynthesis', {
  configurable: true,
  value: {cancel() {}, speak(utterance) { setTimeout(() => utterance.onend?.(), 150); }}
});
"""


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env.update(
            DATABASE_URL=f"sqlite:///{(Path(tmp) / 'jeevan-demo.db').as_posix()}",
            PUBLIC_BASE_URL=url,
            BOOKING_ADAPTER="simulated",
            VOICE_ADAPTER="simulated",
            PUBLIC_DEMO_MODE="false",
        )
        server = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "app.main:app",
                "--host", "127.0.0.1", "--port", str(port),
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(100):
                if server.poll() is not None:
                    raise RuntimeError("Jeevan demo server exited before recording")
                try:
                    with urlopen(f"{url}/health", timeout=0.2):
                        break
                except (OSError, URLError):
                    time.sleep(0.1)
            else:
                raise RuntimeError("Jeevan demo server did not become healthy")

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(
                    viewport={"width": 1440, "height": 900},
                    record_video_dir=str(OUTPUT.parent),
                    record_video_size={"width": 1440, "height": 900},
                )
                context.add_init_script(MOCK_SPEECH)
                page = context.new_page()
                page.goto(url)
                page.evaluate(
                    """() => {
                      const label = document.createElement('div');
                      label.id = 'recording-label';
                      label.style.cssText = 'position:fixed;z-index:100;left:20px;bottom:20px;'
                        + 'max-width:620px;padding:12px 16px;border-radius:12px;'
                        + 'background:#17312ded;color:white;font:600 15px system-ui;'
                        + 'box-shadow:0 8px 30px #0003';
                      label.textContent = 'SCRIPTED SPEECH INPUT · SIMULATED PROVIDER';
                      document.body.append(label);
                      window.__caption = (message) => { label.textContent = message; };
                    }"""
                )
                page.wait_for_timeout(1800)
                page.get_by_role("button", name="Start listening").click()
                page.evaluate(
                    "window.__caption('VOICE REQUEST: AC servicing Saturday after 2 PM, "
                    "under ₹1,000')"
                )
                page.evaluate(
                    "window.__say('Book AC servicing this Saturday after 2 PM under Rs 1,000')"
                )
                page.locator("#voice-start-task").wait_for(state="visible")
                page.wait_for_function(
                    "document.querySelector('#voice-start-task').disabled === false"
                )
                page.wait_for_timeout(3200)
                page.wait_for_function("window.__recognizers.length >= 2")
                page.evaluate("window.__caption('REVIEWED DRAFT · SAY START TASK')")
                page.evaluate("window.__say('start task')")
                page.locator("#customer-task .approval-card").wait_for(state="visible")
                page.evaluate(
                    "window.__caption('₹1,150 QUOTE EXCEEDS ₹1,000 BUDGET · "
                    "APPROVAL REQUIRED')"
                )
                page.wait_for_timeout(4000)
                page.wait_for_function("window.__recognizers.length >= 3")
                page.evaluate("window.__say('approve quote')")
                page.locator("#customer-task .status-pill.completed").wait_for(state="visible")
                page.evaluate("window.__caption('SIMULATED BOOKING CONFIRMED · EVIDENCE RECORDED')")
                page.wait_for_timeout(4000)
                page.get_by_role("button", name="Operations", exact=True).click()
                page.evaluate(
                    "window.__caption('OPERATOR VIEW · AUDIT TRAIL AND RECOVERY CONTROLS')"
                )
                page.wait_for_timeout(4000)
                page.get_by_role("button", name="Memory", exact=True).click()
                page.evaluate(
                    "window.__caption('EDITABLE PREFERENCE MEMORY · LOCAL PORTFOLIO PROTOTYPE')"
                )
                page.wait_for_timeout(3500)
                video = page.video
                context.close()
                browser.close()
                if video is None:
                    raise RuntimeError("Playwright did not create a video")
                Path(video.path()).replace(OUTPUT)
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
    print(OUTPUT)


if __name__ == "__main__":
    main()
