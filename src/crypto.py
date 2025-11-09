from dotenv import load_dotenv
load_dotenv()
from telethon import TelegramClient
import asyncio
import os
from cryptography.fernet import Fernet, InvalidToken

FERNET_KEY = os.getenv("FERNET_KEY")
if not FERNET_KEY:
    raise RuntimeError("FERNET_KEY not set. Generate one and put it in .env")

# Fernet expects bytes
fernet = Fernet(FERNET_KEY.encode())

def encrypt_bytes(data: bytes) -> bytes:
    return fernet.encrypt(data)

def decrypt_bytes(token: bytes) -> bytes:
    try:
        return fernet.decrypt(token)
    except InvalidToken:
        raise ValueError("Invalid encryption token")

def encrypt_str(s: str) -> bytes:
    return encrypt_bytes(s.encode())

def decrypt_str(token: bytes) -> str:
    return decrypt_bytes(token).decode()

api_id_str = os.getenv("API_ID")
api_hash = os.getenv("API_HASH")

if not api_id_str or not api_hash:
    raise RuntimeError("API_ID and API_HASH must be set in the environment (e.g. in .env)")

try:
    api_id = int(api_id_str)
except ValueError:
    raise RuntimeError("API_ID must be an integer")

_bot = None

async def get_bot() -> TelegramClient:
    """Create or reuse a single TelegramClient instance safely."""
    global _bot
    if _bot is None:
        # ensure there is an event loop for Telethon
        loop = asyncio.get_running_loop()
        _bot = TelegramClient("bot_session", api_id, api_hash, loop=loop)
        
        bot_token = os.getenv("BOT_TOKEN")
        if not bot_token:
            raise RuntimeError("BOT_TOKEN not set in .env")
        
        await _bot.start(bot_token=bot_token)
    return _bot


# bot = TelegramClient("bot_session", api_id, api_hash)  # bot session on disk
