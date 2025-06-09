import uuid
import asyncio
import logging
import json
import os
import psutil
from typing import cast, Annotated
from typing_extensions import TypedDict

from livekit import rtc, agents
from livekit.agents import (
    Agent,
    AgentSession,
    AutoSubscribe,
    JobContext,
    WorkerOptions,
    ChatContext,
    ModelSettings,
    FunctionTool,
    NOT_GIVEN,
    llm as livekit_llm,
)
from livekit.plugins import (
    deepgram,
    silero,
    aws,
    openai,
)
from pydantic import Field

from prompt import SYSTEM_PROMPT
from helpers import data_parse_from_chat, extract_json_from_reply
from store import form_service

# --- Logging Setup ---
logging.basicConfig(
    format="%(asctime)s %(levelname)s:%(message)s",
    level=logging.INFO,
)


def handle_async_exception(loop, context):
    logging.error(f"Caught async exception: {context}")


asyncio.get_event_loop().set_exception_handler(handle_async_exception)


def log_open_files():
    """Log the number of open files and connections (for debugging resource leaks)."""
    proc = psutil.Process(os.getpid())
    logging.info(
        f"Open files: {len(proc.open_files())}, Open connections: {len(proc.connections())}"
    )


class ResponseEmotion(TypedDict):
    voice_instructions: Annotated[
        str,
        Field(
            ...,
            description="Concise TTS directive for tone, emotion, intonation, and speed",
        ),
    ]
    response: str


async def maybe_awaitable(val):
    if asyncio.iscoroutine(val):
        return await val
    return val


class VoiceAIAgent:
    def __init__(self):
        # Only instantiate these once per agent
        self.vad = silero.VAD.load
        self.stt = deepgram.STT
        self.tts = aws.TTS(
            voice="Ruth",
            speech_engine="generative",
            language="en-US",
            region="us-east-1",
        )
        self.llm = openai.LLM(model="gpt-4o-mini")
        self.user_phone = None  # Will be set in session

    async def llm_node(
        self,
        chat_ctx: ChatContext,
        tools: list[FunctionTool],
        model_settings: ModelSettings,
    ):
        llm = cast(openai.LLM, self.llm)
        tool_choice = model_settings.tool_choice if model_settings else NOT_GIVEN
        async with llm.chat(
            chat_ctx=chat_ctx,
            tools=tools,
            tool_choice=tool_choice,
            response_format=ResponseEmotion,
        ) as stream:
            async for chunk in stream:
                yield chunk

    def _generate_chat_id(self) -> str:
        return uuid.uuid4().hex

    def get_participant_details(self, participant: rtc.RemoteParticipant) -> dict:
        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            user_phone_number = participant.attributes.get("sip.phoneNumber", "unknown")
            twilio_phone_number = participant.attributes.get(
                "sip.trunkPhoneNumber", "unknown"
            )
            chat_id = self._generate_chat_id()
            return {
                "user_phone_number": user_phone_number,
                "twilio_phone_number": twilio_phone_number,
                "chat_id": chat_id,
            }
        else:
            return {
                "user_phone_number": participant.identity,
                "twilio_phone_number": "unknown",
                "chat_id": self._generate_chat_id(),
            }

    async def initialize_voice_session(self, ctx: JobContext) -> Agent:
        await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
        logging.info("Connected to room: %s", ctx.room.isconnected())
        participant = await ctx.wait_for_participant()

        details = self.get_participant_details(participant)
        user_phone = details["user_phone_number"]
        twilio_phone = details["twilio_phone_number"]
        chat_id = details["chat_id"]
        self.user_phone = user_phone

        initial_ctx = livekit_llm.ChatContext()
        initial_ctx.add_message(
            role="system",
            content=[SYSTEM_PROMPT],
            id=chat_id,
        )

        logging.info(f"user_phone: {user_phone}")
        logging.info(f"twilio_phone: {twilio_phone}")

        agent = Agent(
            instructions=(
                "You are a voice assistant created by LiveKit. Your interface "
                "with users will be voice. You should use short and concise "
                "responses, and avoid usage of unpronouncable punctuation."
            ),
            vad=self.vad(),
            stt=self.stt(),
            llm=self.llm,
            tts=self.tts,
            chat_ctx=initial_ctx,
        )
        logging.info("Agent initialized with VAD, STT, TTS, and LLM")
        return agent

    async def entrypoint(self, ctx: JobContext) -> None:
        logging.info("Voice AI Agent entrypoint called")
        session = None
        try:
            agent = await self.initialize_voice_session(ctx)
            logging.info("Agent initialized")
            session = AgentSession()
            logging.info("Starting agent session")
            await session.start(
                agent=agent,
                room=ctx.room,
            )
            logging.info("Agent session started")
            await session.say("Hello, how can I help you?", allow_interruptions=True)

            chat_ctx = agent.chat_ctx
            tools = []
            model_settings = ModelSettings()
            reply_msg = ""
            async for chunk in self.llm_node(chat_ctx, tools, model_settings):
                if chunk.delta and chunk.delta.content:
                    reply_msg += chunk.delta.content

            json_reply_msg = json.loads(reply_msg)
            logging.info(f"LLM response: {json_reply_msg.get('response')}")
            collected_data = extract_json_from_reply(json_reply_msg.get("response", ""))
            if collected_data:
                logging.info(f"Collected data: {collected_data}")
                intent = collected_data.get("intent")
                if intent == "PRIVATE_PAY":
                    goodbye_msg = "Thanks for calling! Please reply with the trip details listed above so we can prepare your quote and confirm availability."
                elif intent == "INSURANCE_CASE_MANAGERS":
                    goodbye_msg = "Thank you — we’ve received the transport request for [Patient Name]. We’ll forward this to dispatch for review and follow up shortly."
                elif intent == "DISCHARGE":
                    goodbye_msg = "Got it! Our dispatch team will review this now and follow up shortly."
                else:
                    goodbye_msg = "Thank you for your call."

                await session.say(goodbye_msg, allow_interruptions=False)
                logging.info("Goodbye message sent, closing session.")
                await session.aclose()
                session = None  # Mark as closed

                parsed_data = data_parse_from_chat(
                    collected_data, "voice_call", self.user_phone
                )
                # Assuming store_intake_data is sync, run in thread pool:
                loop = asyncio.get_running_loop()
                success = await loop.run_in_executor(
                    None,
                    form_service.store_intake_data,
                    parsed_data,
                    intent,
                )
                if success:
                    logging.info("State stored successfully.")
                else:
                    logging.error("Failed to store state.")
            # Optional: log open files after session
            # log_open_files()
        except Exception as e:
            logging.exception(f"Exception in entrypoint: {e}")
        finally:
            # Ensure session is closed even if an error occurs
            if session is not None:
                await session.aclose()
                logging.info("Session forcibly closed in finally block.")
            # Optional: log open files at the end
            # log_open_files()

    def run(self) -> None:
        logging.info("Voice AI Agent is running...")
        try:
            agents.cli.run_app(
                WorkerOptions(entrypoint_fnc=self.entrypoint, load_threshold=1)
            )
        except Exception as e:
            logging.exception(f"Exception in agent run: {e}")


if __name__ == "__main__":
    VoiceAIAgent().run()
