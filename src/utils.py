import os
import random
import structlog
from datetime import datetime, timezone, timedelta
from typing import List
from dotenv import load_dotenv

load_dotenv()

logger = structlog.get_logger()

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def jitter(seconds: int = 30) -> int:
    return random.randint(0, max(1, seconds))

def rand_delay(min_s: int, max_s: int) -> int:
    return random.randint(min_s, max_s)

def parse_admin_ids(env_val: str) -> List[int]:
    if not env_val:
        return []
    return [int(x.strip()) for x in env_val.split(",") if x.strip().isdigit()]
