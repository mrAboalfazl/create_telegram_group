import asyncio
import os
from sqlalchemy import text
from .models import Base, engine, SessionLocal, Account, Job
from .m_queue import worker_loop, schedule_next_for_account
from .utils import logger

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def bootstrap_targets():
    # ensure each active account has at least one queued job
    async with SessionLocal() as s:
        res = await s.execute(text("""
            SELECT accounts.id FROM accounts
            LEFT JOIN jobs ON jobs.account_id = accounts.id AND jobs.status IN ('queued','running')
            WHERE accounts.is_active = 1
            GROUP BY accounts.id
            HAVING COUNT(jobs.id) = 0
        """))
        for (acc_id,) in res.fetchall():
            acc = await s.get(Account, acc_id)
            await schedule_next_for_account(s, acc)

async def main():
    await init_db()
    await bootstrap_targets()
    pool = int(os.getenv("CONCURRENT_WORKERS_PER_ACCOUNT", "1"))  # we reuse as pool size
    logger.info("worker.start", pool=pool)
    stop = asyncio.Event()
    await worker_loop(pool, stop)

if __name__ == "__main__":
    asyncio.run(main())
