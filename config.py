import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class Settings:
    # Twilio
    TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_PHONE_NUMBER: str = os.getenv("TWILIO_PHONE_NUMBER", "")

    # Amazon Polly
    AWS_ACCESS_KEY_ID: str = os.getenv("AWS_ACCESS_KEY_ID", "")
    AWS_SECRET_ACCESS_KEY: str = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    AWS_REGION: str = os.getenv("AWS_REGION", "us-west-2")

    # Livekit
    LIVEKIT_API_KEY: str = os.getenv("LIVEKIT_API_KEY", "")
    LIVEKIT_API_SECRET: str = os.getenv("LIVEKIT_API_SECRET", "")
    LIVEKIT_URL: str = os.getenv("LIVEKIT_URL", "")

    # Gmail API
    GMAIL_ADDRESS: str = os.getenv("GMAIL_ADDRESS", "")

    # Composio API
    COMPOSIO_API_KEY: str = os.getenv("COMPOSIO_API_KEY", "")

    # Google Sheets API
    GOOGLE_SHEETS_CREDENTIALS_JSON: str = os.getenv(
        "GOOGLE_SHEETS_CREDENTIALS_JSON", ""
    )
    GOOGLE_SHEET_ID: str = os.getenv("GOOGLE_SHEET_ID", "")

    # JotForm API (optional)
    JOTFORM_API_KEY: str = os.getenv("JOTFORM_API_KEY", "")

    # OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

    # Pinecone/Weaviate (RAG Vector DB)
    PINECONE_API_KEY: str = os.getenv("PINECONE_API_KEY", "")
    PINECONE_INDEX_NAME: str = os.getenv("PINECONE_INDEX_NAME", "")
    PINECONE_ENVIRONMENT: str = os.getenv("PINECONE_ENVIRONMENT", "")

    # TTS, SST
    ELEVEN_API_KEY: str = os.getenv("ELEVEN_API_KEY", "")
    DEEPGRAM_API_KEY: str = os.getenv("DEEPGRAM_API_KEY", "")

    # AWS Credentials for S3
    AWS_ACCESS_KEY_ID: str = os.getenv("AWS_ACCESS_KEY_ID", "")
    AWS_SECRET_ACCESS_KEY: str = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    AWS_DEFAULT_REGION: str = os.getenv("AWS_DEFAULT_REGION", "us-west-1")


settings = Settings()
