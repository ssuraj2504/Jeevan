from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.models import Task
from app.voice_intake import simulated_slot


@dataclass(frozen=True)
class CallOutcome:
    success: bool
    provider_name: str = "CoolCare Services"
    quote_paise: int | None = None
    slot: str | None = None
    retryable: bool = False
    reason: str | None = None
    transcript: list[tuple[str, str]] = field(default_factory=list)


class SimulatedVoiceAdapter:
    """Deterministic call outcomes for demos and repeatable evaluations."""

    def call(
        self,
        *,
        scenario: str,
        attempt: int,
        service_type: str,
        requested_date: str,
        time_window: str,
    ) -> CallOutcome:
        introduction = [
            ("Jeevan", f"Hello, I am calling to arrange {service_type} for a customer."),
            ("Provider", "Sure. What day and time do they prefer?"),
            ("Jeevan", f"They prefer {requested_date}, {time_window.lower()}. What can you offer?"),
        ]

        if scenario == "no_answer" and attempt == 1:
            return CallOutcome(
                success=False,
                retryable=True,
                reason="Provider did not answer the first call",
                transcript=[("System", "The call rang for 30 seconds without an answer.")],
            )
        if scenario == "dropped_call" and attempt == 1:
            return CallOutcome(
                success=False,
                retryable=True,
                reason="Call dropped before the quote was confirmed",
                transcript=introduction
                + [("System", "Connection lost. Partial details were preserved.")],
            )
        if scenario == "unavailable":
            return CallOutcome(
                success=False,
                retryable=False,
                reason="Provider has no matching slots this weekend",
                transcript=introduction
                + [("Provider", "We are fully booked this weekend and have no matching slot.")],
            )

        quote_paise = {
            "happy": 85_000,
            "above_budget": 115_000,
            "no_answer": 90_000,
            "dropped_call": 95_000,
            "price_change": 95_000,
        }.get(scenario, 85_000)
        slot = simulated_slot(requested_date, time_window)
        if not slot:
            return CallOutcome(
                success=False,
                reason="The requested time could not be matched to a safe demo slot",
                transcript=introduction,
            )
        transcript = introduction + [
            ("Provider", f"We can do {slot} for Rs {quote_paise // 100}."),
            (
                "Jeevan",
                "I have captured the slot and price. I will confirm after checking the limits.",
            ),
        ]
        return CallOutcome(
            success=True,
            provider_name="CoolCare Services",
            quote_paise=quote_paise,
            slot=slot,
            transcript=transcript,
        )


class LiveKitVoiceAdapter:
    """Configuration boundary for a real LiveKit SIP worker.

    The production worker posts structured call events to Jeevan's webhook. Keeping
    that path separate means the orchestrator behaves identically in simulation.
    """

    def __init__(
        self,
        *,
        url: str | None,
        api_key: str | None,
        api_secret: str | None,
        sip_trunk_id: str | None,
        agent_name: str,
        callback_url: str,
        callback_secret: str,
    ):
        if not all((url, api_key, api_secret, sip_trunk_id)):
            raise RuntimeError("LiveKit URL, API key, secret, and SIP trunk ID are required")
        self.url = url
        self.api_key = api_key
        self.api_secret = api_secret
        self.sip_trunk_id = sip_trunk_id
        self.agent_name = agent_name
        self.callback_url = callback_url
        self.callback_secret = callback_secret

    async def dispatch(self, task: Task) -> str:
        if not task.phone:
            raise RuntimeError("A provider phone number is required for a real call")

        from livekit import api

        room_name = f"jeevan-{task.id}"
        metadata = json.dumps(
            {
                "task_id": task.id,
                "phone_number": task.phone,
                "brief": (
                    f"{task.service_type}; {task.requested_date}, {task.time_window}; "
                    f"budget Rs {(task.budget_paise or 0) // 100}"
                ),
                "callback_url": self.callback_url,
            }
        )
        async with api.LiveKitAPI(
            url=self.url,
            api_key=self.api_key,
            api_secret=self.api_secret,
        ) as livekit_api:
            dispatch = await livekit_api.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(
                    agent_name=self.agent_name,
                    room=room_name,
                    metadata=metadata,
                )
            )
        return dispatch.id
