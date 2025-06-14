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
import requests

from livekit.plugins import deepgram, silero, aws, openai
import multiprocessing
from typing import cast, Annotated
from prompt import SYSTEM_PROMPT
from pydantic import Field
from typing_extensions import TypedDict
from helpers import data_parse_from_chat, extract_json_from_reply
from config import settings


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
        pass

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
            vad=self.vad,
            stt=self.stt,
            llm=self.llm,
            tts=self.tts,
            chat_ctx=initial_ctx,
        )
        print("Agent initialized with VAD, STT, TTS, and LLM")
        # ctx.add_shutdown_callback(self.cleanup_session(user_phone=user_phone))
        return agent

    async def entrypoint(self, ctx: JobContext) -> None:
        self.vad = silero.VAD.load(
            min_speech_duration=0.05,
            min_silence_duration=1,
            prefix_padding_duration=0.5,
            max_buffered_speech=60.0,
            activation_threshold=0.6,
            sample_rate=16000,
            force_cpu=False,
        )
        self.stt = deepgram.STT(model="nova-3", language="en-US")
        self.llm = openai.realtime.RealtimeModel()
        self.tts = aws.TTS(
            voice="Ruth",
            speech_engine="generative",
            language="en-US",
            region="us-east-1",
        )

        print("Voice AI Agent entrypoint called")
        agent = await self.initialize_voice_session(ctx)
        print("Agent initialized")
        session = AgentSession()
        print("Starting agent session")
        await session.start(
            agent=agent,
            room=ctx.room,
        )
        session.say(
            "Hi! This is Golden State Medical Transport. How can I assist you today?"
        )
        print("Agent session started")

        async def handle_conversation_item(event: agents.ConversationItemAddedEvent):
            if event.item.role == "user":
                print("user message:", event.item.content)
            if event.item.role == "assistant":
                print("assistant message:", event.item.content)
                reply_msg = event.item.content[0]
                collected_data = extract_json_from_reply(reply_msg)
                if collected_data:
                    print(collected_data)
                    if collected_data.get("intent") == "PRIVATE_PAY":
                        goodbye_msg = "Thanks for calling! Please reply with the trip details listed above so we can prepare your quote and confirm availability."
                    elif collected_data.get("intent") == "INSURANCE_CASE_MANAGERS":
                        goodbye_msg = "Thank you — we’ve received the transport request for [Patient Name]. We’ll forward this to dispatch for review and follow up shortly."
                    elif collected_data.get("intent") == "DISCHARGE":
                        goodbye_msg = "Got it! Our dispatch team will review this now and follow up shortly."

                    await session.say(goodbye_msg)
                    print("Goodbye message sent, closing session.")
                    parsed_data = data_parse_from_chat(
                        collected_data, "voice_call", self.user_phone
                    )
                    payload = {
                        "intent": collected_data.get("intent"),
                        "data": parsed_data,
                    }
                    requests.post(
                        settings.BACKEND_URL + "/store/",
                        json=payload,
                        headers={"Content-Type": "application/json"},
                        timeout=30,
                    )
                    await session.aclose()
                    # if success:
                    #     print("State stored successfully.")

        def on_conversation_item_added(event: agents.ConversationItemAddedEvent):
            asyncio.create_task(handle_conversation_item(event))

        session.on("conversation_item_added", on_conversation_item_added)
        try:
            await session.start(agent=agent, room=ctx.room)
            # Keep the session alive until closed
            while session._activity:
                await asyncio.sleep(1)

        except Exception as e:
            print(f"Session error: {e}")
        finally:
            if session._activity:
                await session.aclose()

    def run(self) -> None:
        print("Voice AI Agent is running...")
        try:
            agents.cli.run_app(
                WorkerOptions(entrypoint_fnc=self.entrypoint, load_threshold=1)
            )
        except Exception as e:
            print(f"Exception in on_text_delta: {e}")


if __name__ == "__main__":
    multiprocessing.set_start_method("spawn")
    VoiceAIAgent().run()
