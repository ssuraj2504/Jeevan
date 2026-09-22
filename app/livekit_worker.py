"""Optional LiveKit telephony worker for real outbound provider calls.

Install the `voice` extra and configure LiveKit before running this module. The
default web application never imports it, so the deterministic demo needs no
telephony credentials.
"""

from __future__ import annotations

import json
import os
import uuid

from livekit import agents
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    RunContext,
    TurnHandlingOptions,
    function_tool,
    inference,
)
from livekit.protocol.sip import CreateSIPParticipantRequest


class ProviderCallAgent(Agent):
    def __init__(
        self, *, task_id: str, callback_url: str, callback_secret: str, brief: str
    ) -> None:
        self.task_id = task_id
        self.callback_url = callback_url
        self.callback_secret = callback_secret
        super().__init__(
            instructions=(
                "You are Jeevan, calling a household service provider on behalf of a customer. "
                f"Request: {brief}. Ask for an exact all-inclusive price and an exact appointment "
                "slot. Never claim a booking is confirmed. Read the captured details back once, "
                "then call capture_quote. If no slot exists, call report_failure."
            )
        )

    async def _post_event(self, payload: dict) -> None:
        from livekit.agents import utils

        session = utils.http_context.http_session()
        async with session.post(
            self.callback_url,
            json={"task_id": self.task_id, "event_id": str(uuid.uuid4()), **payload},
            headers={"X-Jeevan-Signature": self.callback_secret},
            timeout=12,
        ) as response:
            response.raise_for_status()

    @function_tool()
    async def capture_quote(
        self,
        context: RunContext,
        provider_name: str,
        quote_rupees: int,
        slot: str,
    ) -> str:
        """Capture a provider's final quote and slot after reading both back.

        Args:
            provider_name: Name of the service provider.
            quote_rupees: All-inclusive quoted amount in Indian rupees.
            slot: Exact appointment date and time window.
        """
        await self._post_event(
            {
                "event_type": "quote",
                "provider_name": provider_name,
                "quote_rupees": quote_rupees,
                "slot": slot,
                "text": "Voice agent captured a verified quote and slot.",
            }
        )
        return "Quote recorded. Thank the provider and end the call without making a booking."

    @function_tool()
    async def report_failure(self, context: RunContext, reason: str, retryable: bool) -> str:
        """Report that the call could not produce a complete quote.

        Args:
            reason: Concise reason the call did not succeed.
            retryable: Whether another call could reasonably succeed.
        """
        await self._post_event(
            {"event_type": "call_failed", "reason": reason, "retryable": retryable, "text": reason}
        )
        return "Failure recorded. Politely end the call."


server = AgentServer()


@server.rtc_session(agent_name="jeevan-provider-caller")
async def entrypoint(ctx: agents.JobContext) -> None:
    metadata = json.loads(ctx.job.metadata or "{}")
    task_id = metadata["task_id"]
    phone_number = metadata.get("phone_number")
    if phone_number:
        await ctx.api.sip.create_sip_participant(
            CreateSIPParticipantRequest(
                sip_trunk_id=os.environ["LIVEKIT_SIP_TRUNK_ID"],
                sip_call_to=phone_number,
                room_name=ctx.room.name,
                participant_identity=f"provider-{task_id}",
                participant_name="Service provider",
                wait_until_answered=True,
            )
        )

    session = AgentSession(
        stt=inference.STT(model="deepgram/nova-3", language="multi"),
        llm=inference.LLM(model="google/gemma-4-31b-it"),
        tts=inference.TTS(model="inworld/inworld-tts-2", voice="Ashley"),
        turn_handling=TurnHandlingOptions(turn_detection=inference.TurnDetector()),
    )
    await session.start(
        room=ctx.room,
        agent=ProviderCallAgent(
            task_id=task_id,
            callback_url=metadata["callback_url"],
            callback_secret=os.environ["JEEVAN_WEBHOOK_SECRET"],
            brief=metadata["brief"],
        ),
    )


if __name__ == "__main__":
    agents.cli.run_app(server)
