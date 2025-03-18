from typing import Annotated

from livekit.agents.llm import function_tool
from livekit.agents.voice import Agent, RunContext
from pydantic import Field


@function_tool()
async def update_information(
    field: Annotated[
        str,
        Field(
            description="The type of information to be updated, either 'phone_number', 'email', or 'name'"
        ),
    ],
    info: Annotated[str, Field(description="The new user provided information")],
    context: RunContext,
) -> str:
    """
    Updates information on record about the user. The only fields to update are names, phone numbers, and emails.
    """
    userinfo = context.userdata["userinfo"]
    if field == "name":
        userinfo.name = info
    elif field == "phone_number":
        userinfo.phone = info
    elif field == "email":
        userinfo.email = info

    return "Got it, thank you!"


@function_tool()
async def transfer_to_receptionist(context: RunContext) -> tuple[Agent, str]:
    """Transfers the user to the receptionist for any office inquiries or when they are finished with managing appointments."""
    return context.userdata[
        "agents"
    ].receptionist, "Transferring you to our receptionist!"


@function_tool()
async def transfer_to_scheduler(
    action: Annotated[
        str,
        Field(
            description="The appointment action requested, either 'schedule', 'reschedule', or 'cancel'"
        ),
    ],
    context: RunContext,
) -> tuple[Agent, str]:
    """
    Transfers the user to the Scheduler to manage appointments.
    """
    return context.userdata["agents"].scheduler(
        service=action
    ), "Transferring you to our scheduler!"


@function_tool()
async def transfer_to_messenger(context: RunContext) -> tuple[Agent, str]:
    """
    Transfers the user to the messenger if they want to leave a message for the office.
    """
    return context.userdata["agents"].messenger, "Transferring you to our messenger!"
