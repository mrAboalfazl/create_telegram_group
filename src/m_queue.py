from __future__ import annotations
from typing import List, Optional, Union
import json
from datetime import timedelta
from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import AsyncSession
from telethon import functions
from telethon.errors import FloodWaitError, SessionPasswordNeededError, RPCError
from .models import Job, Account, GroupStat, EventLog , SessionLocal
from .crypto import decrypt_str
from .utils import now_utc, jitter, rand_delay, logger
import math
import asyncio
import random
import os

MIN_DELAY = int(os.getenv("MIN_DELAY_SECONDS", "600"))
MAX_DELAY = int(os.getenv("MAX_DELAY_SECONDS", "3600"))
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS_PER_GROUP", "3"))
FLOODWAIT_THRESHOLD = int(os.getenv("FLOODWAIT_THRESHOLD_SECONDS_PER_24H", "3600"))
GROUP_PREFIX = os.getenv("GROUP_TITLE_PREFIX", "")
TARGET_PER_24H = int(os.getenv("TARGET_PER_24H", "48"))
SCHEDULE_JITTER_SECONDS = int(os.getenv("SCHEDULE_JITTER_SECONDS", "300"))  # پیش‌فرض 5 دقیقه

def _compute_delay_seconds() -> int:
    \"\"\"Deterministic interval based on TARGET_PER_24H with symmetric jitter.
    Falls back to MIN/MAX when TARGET_PER_24H <= 0.
    \"\"\"
    if TARGET_PER_24H and TARGET_PER_24H > 0:
        base = int(math.ceil(86400 / TARGET_PER_24H))
        j = jitter(SCHEDULE_JITTER_SECONDS)
        # symmetric jitter: +/- j
        if random.randint(0, 1) == 0:
            delay = base + j
        else:
            delay = max(1, base - j)
        return delay
    # fallback legacy random window
    return rand_delay(MIN_DELAY, MAX_DELAY)

from telethon import TelegramClient
from telethon.sessions import StringSession

async def lease_next_job(session: AsyncSession) ->Optional[Job] :
    # NOTE: For SQLite, true cross-process locking is limited.
    # In Postgres, use SKIP LOCKED.
    # Here we do a simple "take first ready" pattern within a transaction.
    now = now_utc()
    q = await session.execute(
        select(Job)
        .where(and_(Job.status=="queued", Job.next_run_at<=now))
        .order_by(Job.id.asc())
        .limit(1)
    )
    job = q.scalar_one_or_none()
    if job:
        job.status = "running"
        await session.commit()
        await session.refresh(job)
    return job

async def schedule_next_for_account(session: AsyncSession, account: Account):
    delay = _compute_delay_seconds()
    # Enqueue new job
    j = Job(
        account_id=account.id,
        type="CREATE_GROUP",
        status="queued",
        attempts=0,
        max_attempts=MAX_ATTEMPTS,
        payload="{}",
        next_run_at=now_utc() + timedelta(seconds=delay),
    )
    session.add(j)
    await session.commit()

async def notify(session: AsyncSession, owner_id: int, level: str, code: str, message: str):
    ev = EventLog(owner_id=owner_id, level=level, code=code, message=message)
    session.add(ev)
    await session.commit()

async def create_telethon_client_from_account(account: Account) -> TelegramClient:
    api_id = int(account.api_id)
    api_hash = decrypt_str(account.api_hash_enc)
    session_str = decrypt_str(account.session_enc)
    client = TelegramClient(StringSession(session_str), api_id, api_hash)
    await client.connect()
    return client

async def process_job(job: Job):
    async with SessionLocal() as s:
        # reload with account
        job = await s.get(Job, job.id)
        if not job:
            return
        account = await s.get(Account, job.account_id)
        if not account or not account.is_active:
            job.status = "failed"
            job.error = "Account not found or inactive"
            await s.commit()
            return

        # enforce per-account single concurrency: ensure there is no other running job for this account
        # (best-effort for SQLite single-process; for multi-process use DB-level locks)
        try:
            client = await create_telethon_client_from_account(account)
        except Exception as e:
            job.status = "failed"
            job.error = f"ClientInitError: {e}"
            await s.commit()
            await notify(s, account.owner_id, "error", "client_init", "خطا در ایجاد کلاینت اکانت.")
            return

        try:
            title = f"{GROUP_PREFIX} {random.randint(100000, 999999)}".strip()
            await client(functions.channels.CreateChannelRequest(
                title=title,
                about="",
                megagroup=True
            ))
            # success
            account.last_used_at = now_utc()
            s.add(GroupStat(account_id=account.id))
            job.status = "done"
            await s.commit()

            await notify(s, account.owner_id, "info", "group_created", f"یک گروه جدید ساخته شد: {title}")

            # schedule next to reach target
            await schedule_next_for_account(s, account)

        except FloodWaitError as fw:
            # schedule after floodwait + small jitter
            job.attempts += 1
            job.status = "queued"
            wait_s = fw.seconds + jitter(30)
            job.next_run_at = now_utc() + timedelta(seconds=wait_s)
            await s.commit()

            # accumulate floodwait in last 24h window (naive counter)
            account.total_floodwait_s_24h += fw.seconds
            await s.commit()
            await notify(s, account.owner_id, "warn", "floodwait", f"FloodWait {fw.seconds}s. اجرای بعدی بعد از {wait_s}s")

            if account.total_floodwait_s_24h > FLOODWAIT_THRESHOLD:
                account.is_active = False
                await s.commit()
                await notify(s, account.owner_id, "warn", "paused", "به دلیل FloodWait زیاد در 24 ساعت گذشته، اکانت موقتاً متوقف شد.")
        except RPCError as re:
            job.attempts += 1
            if job.attempts >= job.max_attempts:
                job.status = "failed"
                job.error = f"RPCError: {re.__class__.__name__}"
            else:
                backoff = 2 ** job.attempts * 10 + jitter(20)
                job.status = "queued"
                job.next_run_at = now_utc() + timedelta(seconds=backoff)
                job.error = f"retrying due to {re.__class__.__name__}"
            await s.commit()
            await notify(s, account.owner_id, "error", "rpc_error", "خطا در ساخت گروه؛ تلاش مجدد با backoff.")
        except Exception as e:
            job.attempts += 1
            if job.attempts >= job.max_attempts:
                job.status = "failed"
                job.error = f"Unexpected: {e}"
            else:
                backoff = 2 ** job.attempts * 10 + jitter(20)
                job.status = "queued"
                job.next_run_at = now_utc() + timedelta(seconds=backoff)
                job.error = "Unexpected; retry later"
            await s.commit()
            await notify(s, account.owner_id, "error", "unexpected", "خطای غیرمنتظره؛ تلاش مجدد.")
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

async def worker_loop(pool_size: int = 4, stop_event: Optional[asyncio.Event] = None):
    stop_event = stop_event or asyncio.Event()
    async with SessionLocal() as s:
        # initial
        pass

    async def _one():
        async with SessionLocal() as s:
            job = await lease_next_job(s)
            if not job:
                await asyncio.sleep(1.0)
                return
            await process_job(job)

    tasks = []
    try:
        while not stop_event.is_set():
            # launch up to pool_size concurrent fetches
            tasks = [asyncio.create_task(_one()) for _ in range(pool_size)]
            await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        return
