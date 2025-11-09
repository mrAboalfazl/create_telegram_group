from sqlalchemy import select, func, text
from .models import SessionLocal, Account, GroupStat, Job
from datetime import timedelta
from .utils import now_utc

async def my_stats(owner_id: int):
    async with SessionLocal() as s:
        # active accounts
        q_active = await s.execute(select(func.count()).select_from(Account).where(Account.owner_id==owner_id, Account.is_active==True))
        active_accounts = q_active.scalar_one()

        # groups last 24h
        since = now_utc() - timedelta(hours=24)
        q_groups = await s.execute(select(func.count()).select_from(GroupStat).join(Account, Account.id==GroupStat.account_id).where(Account.owner_id==owner_id, GroupStat.created_at>=since))
        groups_24h = q_groups.scalar_one()

        # queued jobs (owner)
        q_jobs = await s.execute(select(func.count()).select_from(Job).join(Account, Account.id==Job.account_id).where(Account.owner_id==owner_id, Job.status.in_(["queued","running"])))
        jobs_q = q_jobs.scalar_one()

        # failed jobs
        q_fail = await s.execute(select(func.count()).select_from(Job).join(Account, Account.id==Job.account_id).where(Account.owner_id==owner_id, Job.status=="failed"))
        jobs_failed = q_fail.scalar_one()

        return active_accounts, groups_24h, jobs_q, jobs_failed
