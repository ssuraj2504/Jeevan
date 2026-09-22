from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProviderBooking, Task


class PriceChanged(RuntimeError):
    def __init__(self, new_price_paise: int):
        super().__init__("Provider changed the price before confirmation")
        self.new_price_paise = new_price_paise


@dataclass(frozen=True)
class BookingEvidence:
    confirmation_ref: str
    source: str


def _confirmation_ref(task_id: str) -> str:
    return f"JVN-{task_id.split('-')[0].upper()}"


class SimulatedBrowserAdapter:
    def confirm(self, db: Session, task: Task) -> BookingEvidence:
        existing = db.scalar(select(ProviderBooking).where(ProviderBooking.task_id == task.id))
        if existing:
            return BookingEvidence(existing.confirmation_ref, "reconciled_existing_booking")

        if task.scenario == "price_change" and task.quote_paise == 95_000:
            raise PriceChanged(115_000)

        reference = _confirmation_ref(task.id)
        booking = ProviderBooking(
            task_id=task.id,
            confirmation_ref=reference,
            provider_name=task.provider_name or "CoolCare Services",
            slot=task.quoted_slot or "Saturday, 4:00 PM - 5:00 PM",
            price_paise=task.quote_paise or 0,
            address=task.address,
        )
        db.add(booking)
        db.flush()
        return BookingEvidence(reference, "simulated_provider_portal")


class PlaywrightBrowserAdapter:
    """Bounded browser automation for the included provider portal.

    Only named form fields are used; the agent never executes model-generated code
    or arbitrary selectors.
    """

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def confirm(self, db: Session, task: Task) -> BookingEvidence:
        existing = db.scalar(select(ProviderBooking).where(ProviderBooking.task_id == task.id))
        if existing:
            return BookingEvidence(existing.confirmation_ref, "reconciled_existing_booking")

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Install Jeevan with the 'browser' extra to use Playwright") from exc

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"{self.base_url}/demo-provider", wait_until="networkidle")
            page.get_by_label("Task ID").fill(task.id)
            page.get_by_label("Customer address").fill(task.address)
            page.get_by_label("Appointment slot").fill(task.quoted_slot or "")
            page.get_by_label("Agreed price").fill(str((task.quote_paise or 0) // 100))
            page.get_by_role("button", name="Confirm appointment").click()
            confirmation = page.locator("[data-confirmation-ref]").get_attribute(
                "data-confirmation-ref"
            )
            browser.close()

        if not confirmation:
            raise RuntimeError("Provider portal did not return confirmation evidence")

        db.add(
            ProviderBooking(
                task_id=task.id,
                confirmation_ref=confirmation,
                provider_name=task.provider_name or "CoolCare Services",
                slot=task.quoted_slot or "",
                price_paise=task.quote_paise or 0,
                address=task.address,
            )
        )
        db.flush()
        return BookingEvidence(confirmation, "playwright_provider_portal")
