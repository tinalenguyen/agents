import asyncio
import logging
import datetime
import aiohttp
import os
from enum import Enum
from dataclasses import dataclass

from dotenv import load_dotenv
from livekit.agents import (
    AutoSubscribe,
    JobContext,
    WorkerOptions,
    cli,
    llm,
    NotGivenOr,
    NOT_GIVEN,
)

from livekit.agents.llm import ai_function
from livekit.agents.pipeline import AgentTask, PipelineAgent
from livekit.plugins import openai


@dataclass
class UserInfo:
    name: str = ""
    email: NotGivenOr[str] = NOT_GIVEN
    phone: NotGivenOr[str] = NOT_GIVEN
    message: NotGivenOr[str] = NOT_GIVEN


class APIRequests(Enum):
    GET_APPTS = "get_appts"
    GET_APPT = "get_appt"
    CANCEL = "cancel"
    RESCHEDULE = "reschedule"
    SCHEDULE = "schedule"


class Receptionist(AgentTask):
    def __init__(self) -> None:
        super().__init__(
            instructions="""You are Alloy, a receptionist at the LiveKit Dental Office scheduling dentist appointments for users. Always speak in English.
                            The office is closed on Sundays but operates from 10AM to 12PM and 1PM to 4PM PT otherwise. The services offered are routine checkups and tooth extractions.
                            The location of the office is 123 LiveKit Lane, California. When managing appointments or taking a message, first ask for the user's name then redirect them to the right agent.""",
            llm=openai.realtime.RealtimeModel(voice="alloy"),
        )

    # will be adding a TTS() for variety
    async def on_enter(self) -> None:
        speech_handle = await self.agent.generate_reply(
            instructions="Welcome the user to the LiveKit Dental Office and ask how you can assist."
        )

    @ai_function()
    async def appointment(self, service: str):
        """This function allows for users to schedule, reschedule, or cancel an appointment.

        Args:
            service (str): Either "schedule", "reschedule", or "cancel"
        """
        return Scheduler(service=service), "I'll be transferring you to Echo."

    @ai_function()
    async def take_message(self):
        """This function allows for users to leave a message for the office."""
        return Messenger(), "I'll be transferring you to Shimmer."

    @ai_function()
    async def get_name(self, name: str | None) -> None:
        """Records the user's name

        Args:
            name (str | None): User's name
        """
        self.agent.userdata.name = name
        speech_handle = await self.agent.generate_reply(instructions=f"Thanks {name}!")


class Scheduler(AgentTask):
    def __init__(self, *, service: str) -> None:
        super().__init__(
            instructions="You are Echo, a scheduler managing appointments for the LiveKit dental office.",
            llm=openai.realtime.RealtimeModel(voice="echo"),
        )
        self._service_requested = service

    async def on_enter(self) -> None:
        speech_handle = await self.agent.generate_reply(
            instructions=f"Introduce yourself and ask {self.agent.userdata.name} to confirm that they would like to {self._service_requested} an appointment. If they are scheduling a new appointment, ask for their email."
        )

    async def send_request(
        self, *, request: APIRequests, uid: str = "", time: str = "", note: str = ""
    ) -> dict:
        headers = {
            "cal-api-version": "2024-08-13",
            "Authorization": "Bearer " + os.getenv("CAL_API_KEY"),
        }
        async with aiohttp.ClientSession() as session:
            data = {}
            if request.value == "get_appts":
                payload = {"attendeeName": self.agent.userdata.name}
                async with session.post(
                    "https://api.cal.com/v2/bookings", params=payload, headers=headers
                ) as response:
                    data = await response.json()

            if request.value == "cancel":
                async with session.post(
                    "https://api.cal.com/v2/bookings/{uid}/cancel", headers=headers
                ) as response:
                    data = await response.json()

            if request.value == "schedule":
                attendee_details = {
                    "name": self.agent.userdata.name,
                    "email": self.agent.userdata.email,
                    "timeZone": "America/Los_Angeles",
                }
                payload = {
                    "start": time,
                    "eventTypeId": 1,
                    "attendee": attendee_details,
                    "bookingFieldsResponses": {"notes": note},
                }
                async with session.post(
                    "https://api.cal.com/v2/bookings", json=payload, headers=headers
                ) as response:
                    data = await response.json()

            else:
                raise Exception("API Request Failed")
            return data

    @ai_function()
    async def schedule(self, description: str, date: str) -> None:
        """Schedules a new appointment for users.
        Args:
            description (str): Reason for scheduling appointment, either "routine checkup" or "tooth extraction"
            date (str): Date and time for the appointment in ISO 8601 format in UTC timezone.
        """
        response = await self.send_request(
            request=APIRequests.SCHEDULE, time=date, note=description
        )
        if response.status == "success":
            sp = await self.agent.generate_reply(
                instructions="Tell the user you were able to schedule the appointment successfully."
            )

    @ai_function()
    async def cancel(self) -> None:
        """Cancels an existing appointment"""
        response = await self.send_request(request=APIRequests.GET_APPTS)
        if response.data:
            sp = await self.agent.generate_reply(
                f"Canceling the appointment found on {response.data.start} for {response.data.title}."
            )
            confirmation = await self.send_request(
                request=APIRequests.CANCEL, uid=response.data.uid
            )
            if confirmation.status == "success":
                return Receptionist(), "You're all set, transferring you back to Alloy."
        else:
            sp = await self.agent.generate_reply(
                "There are no appointments under your name, would you like to schedule one?"
            )

    @ai_function()
    async def reschedule(self) -> None:
        """Reschedules an existing appointment"""
        ...
        # check for existing appt -> confirm correct appt -> adjust time -> schedule new, cancel old

    @ai_function()
    async def get_email(self, email: str) -> None:
        """Records the user's email.

        Args:
            email (str): The user's email
        """
        self.agent.userdata.email = email


class Messenger(AgentTask):
    def __init__(self) -> None:
        super().__init__(
            instructions="You are Shimmer, an assistant taking messages for the LiveKit dental office.",
            llm=openai.realtime.RealtimeModel(voice="shimmer"),
        )

    async def on_enter(self) -> None:
        self.agent.say(
            f"Alright {self.agent.userdata.name}, please state your message whenever you're ready."
        )

    # either directly use the transcript or function calling


load_dotenv()

logger = logging.getLogger("dental-scheduler")
logger.setLevel(logging.INFO)


async def entrypoint(ctx: JobContext):
    agent = PipelineAgent(
        task=Receptionist(), userdata=UserInfo(), llm=openai.realtime.RealtimeModel()
    )

    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    await agent.start(room=ctx.room)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
