# Jeevan: 75-second portfolio walkthrough

Record the app in Chrome or Edge with your own microphone. Keep the **Simulation mode** badge visible. Use only the seeded demo address; do not speak a real address or phone number.

| Time | Show | Say |
| --- | --- | --- |
| 0–10 s | Request page and microphone button | “Jeevan is a voice-initiated household booking prototype. The provider call and booking in this walkthrough are simulated.” |
| 10–25 s | Press **Start listening**; speak the request | “Book AC servicing this Saturday after 2 PM, under one thousand rupees.” |
| 25–35 s | Editable transcript and extracted fields | “It extracts the date, time, budget, and saved address. Nothing has been booked yet.” |
| 35–50 s | Say **start task**; show quote and approval card | “The simulated provider quotes ₹1,150, above my ₹1,000 limit, so Jeevan stops for approval.” |
| 50–60 s | Say **approve quote**; show confirmation reference | “Approval is bound to this quote version. The sandbox booking creates one confirmation.” |
| 60–75 s | Open Operations, then Memory | “The timeline records each decision. An operator can recover a blocked task, and preferences remain editable.” |

Run `RUN_BROWSER_E2E=1 pytest` and show the result at the end if time permits. State that browser tests inject mocked speech recognition; the walkthrough demonstrates the actual browser microphone. Do not describe the simulated provider call, sandbox portal, or optional LiveKit adapter as a real external booking.
