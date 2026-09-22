import os

import pytest


@pytest.mark.browser
@pytest.mark.skipif(os.getenv("RUN_BROWSER_E2E") != "1", reason="set RUN_BROWSER_E2E=1")
def test_provider_portal_returns_confirmation_evidence() -> None:
    """Run against `uvicorn app.main:app` on port 8000 after installing Chromium."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://127.0.0.1:8000/demo-provider")
        page.get_by_label("Task ID").fill("test-task-1234")
        page.get_by_label("Customer address").fill("HSR Layout, Bengaluru")
        page.get_by_label("Appointment slot").fill("Saturday, 4:00 PM")
        page.get_by_label("Agreed price").fill("900")
        page.get_by_role("button", name="Confirm appointment").click()
        assert (
            page.locator("[data-confirmation-ref]").get_attribute("data-confirmation-ref")
            == "JVN-TEST"
        )
        browser.close()
