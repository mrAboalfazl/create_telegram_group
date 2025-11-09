from __future__ import annotations
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from typing import List
from sqlalchemy import String, Integer, BigInteger, LargeBinary, DateTime, ForeignKey, Text, Boolean, Index
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncAttrs
from sqlalchemy.sql import func
import os

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./data.db")

class Base(AsyncAttrs, DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Telegram user id
    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now())
    accounts: Mapped[List["Account"]] = relationship(back_populates="owner", cascade="all, delete-orphan")

class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    api_id: Mapped[str] = mapped_column(String(32))
    api_hash_enc: Mapped[bytes] = mapped_column(LargeBinary)  # encrypted
    phone: Mapped[str] = mapped_column(String(32))
    session_enc: Mapped[bytes] = mapped_column(LargeBinary)   # encrypted Telethon StringSession
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), nullable=True)
    total_floodwait_s_24h: Mapped[int] = mapped_column(Integer, default=0)

    owner: Mapped["User"] = relationship(back_populates="accounts")
    jobs: Mapped[List["Job"]] = relationship(back_populates="account", cascade="all, delete-orphan")

class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(32), default="CREATE_GROUP")
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|failed|paused
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    next_run_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now())
    payload: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str] = mapped_column(Text, default="")

    account: Mapped["Account"] = relationship(back_populates="jobs")

Index("idx_jobs_ready", Job.status, Job.next_run_at)

class GroupStat(Base):
    __tablename__ = "group_stats"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now())

class EventLog(Base):
    __tablename__ = "event_logs"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    level: Mapped[str] = mapped_column(String(8), default="info")  # info|warn|error
    code: Mapped[str] = mapped_column(String(32), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now())

engine = create_async_engine(
    DATABASE_URL, 
    echo=False, 
    future=True,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
