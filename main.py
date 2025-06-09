import uuid
import asyncio
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

# from livekit.agents.stt import SpeechEventType, SpeechEvent
from typing import cast, Annotated
from prompt import SYSTEM_PROMPT
from pydantic import Field
from typing_extensions import TypedDict
import json
from helpers import data_parse_from_chat, extract_json_from_reply
from store import form_service


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
        self.vad = silero.VAD.load
        self.stt = deepgram.STT
        self.tts = aws.TTS(
            voice="Ruth",
            speech_engine="generative",
            language="en-US",
            region="us-east-1",
        )
        self.llm = openai.LLM(model="gpt-4o-mini")

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
            user_phone_number = participant.attributes["sip.phoneNumber"]
            twilio_phone_number = participant.attributes["sip.trunkPhoneNumber"]
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
        print("Connected to room", ctx.room.isconnected())
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

        print("user_phone", user_phone)
        print("twilio_phone", twilio_phone)

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
        print("Agent initialized with VAD, STT, TTS, and LLM")
        # ctx.add_shutdown_callback(self.cleanup_session(user_phone=user_phone))
        return agent

    async def entrypoint(self, ctx: JobContext) -> None:
        print("Voice AI Agent entrypoint called")
        agent = await self.initialize_voice_session(ctx)
        print("Agent initialized")
        session = AgentSession()
        print("Starting agent session")
        await session.start(
            agent=agent,
            room=ctx.room,
        )
        print("Agent session started")
        await session.say("Hello, how can I help you?", allow_interruptions=True)

        chat_ctx = agent.chat_ctx
        tools = []
        model_settings = ModelSettings()
        reply_msg = ""
        async for chunk in self.llm_node(chat_ctx, tools, model_settings):
            if chunk.delta and chunk.delta.content:
                reply_msg += chunk.delta.content
        json_reply_msg = json.loads(reply_msg)
        print(json_reply_msg["response"])
        collected_data = extract_json_from_reply(json_reply_msg["response"])
        if collected_data:
            print(collected_data)
            if collected_data.get("intent") == "PRIVATE_PAY":
                goodbye_msg = "Thanks for calling! Please reply with the trip details listed above so we can prepare your quote and confirm availability."
            elif collected_data.get("intent") == "INSURANCE_CASE_MANAGERS":
                goodbye_msg = "Thank you — we’ve received the transport request for [Patient Name]. We’ll forward this to dispatch for review and follow up shortly."
            elif collected_data.get("intent") == "DISCHARGE":
                goodbye_msg = "Got it! Our dispatch team will review this now and follow up shortly."
            await session.say(goodbye_msg, allow_interruptions=False)
            print("Goodbye message sent, closing session.")
            await session.aclose()
            parsed_data = data_parse_from_chat(
                collected_data, "voice_call", self.user_phone
            )
            success = form_service.store_intake_data(
                parsed_data, collected_data.get("intent")
            )
            if success:
                print("State stored successfully.")

    def run(self) -> None:
        print("Voice AI Agent is running...")
        try:
            agents.cli.run_app(
                WorkerOptions(entrypoint_fnc=self.entrypoint, load_threshold=1)
            )
        except Exception as e:
            print(f"Exception in on_text_delta: {e}")


VoiceAIAgent().run()
