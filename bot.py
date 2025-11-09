import asyncio
import os
from typing import List, Tuple, Dict, Any, Optional
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError
from src.crypto import encrypt_str
from src.models import SessionLocal, Base, engine, User, Account, Job, EventLog, GroupStat
from sqlalchemy import select
from src.utils import logger, now_utc, parse_admin_ids
from src.kpi import my_stats
from dotenv import load_dotenv

load_dotenv()

api_id_str = os.getenv("API_ID")
api_hash = os.getenv("API_HASH")

if not api_id_str or not api_hash:
    raise RuntimeError("API_ID and API_HASH must be set in the environment (e.g. in .env)")

try:
    api_id = int(api_id_str)
except ValueError:
    raise RuntimeError("API_ID must be an integer")

# bot token is read at runtime to avoid import-time KeyError
BOT_TOKEN = os.getenv("BOT_TOKEN")

# state machine (very small, per-user)
user_states: Dict[int, Dict[str, Any]] = {}  # {user_id: {"stage": str, "tmp": dict}}

async def init_db():
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("db.init.success")
    except Exception as e:
        logger.exception("db.init.error", error=str(e))
        raise

# created and started inside main(), then handlers are registered
bot: Optional[TelegramClient] = None

def kb(rows: List[List[Tuple[str, str]]]):
    # simple inline keyboard helper
    from telethon import Button
    return [[Button.inline(text, data=data.encode()) for (text,data) in row] for row in rows]

async def start(ev: events.NewMessage.Event):
    uid = ev.sender_id
    logger.info("handler.start", user_id=uid)
    async with SessionLocal() as s:
        if not await s.get(User, uid):
            s.add(User(id=uid))
            await s.commit()
    text = (
        "سلام! من ربات مدیریت اکانت‌های Telethon هستم.\n"
        "از منو یکی را انتخاب کن:\n"
        "• افزودن اکانت جدید\n"
        "• مدیریت سشن‌ها\n"
        "• /my_stats برای مشاهده آمار\n"
    )
    await ev.respond(text, buttons=kb([
        [("➕ ایجاد اکانت", "add_account"), ("🧾 مدیریت سشن‌ها", "sessions")],
        [("📊 آمار من", "stats")]
    ]))

async def stats_cb(ev: events.CallbackQuery.Event):
    logger.info("handler.stats", user_id=ev.sender_id)
    a, g, q, f = await my_stats(ev.sender_id)
    await ev.edit(f"📊 آمار شما:\n"
                  f"اکانت فعال: {a}\n"
                  f"گروه‌های ساخته‌شده 24h: {g}\n"
                  f"Jobهای در صف/اجرا: {q}\n"
                  f"شکست‌ها: {f}")

async def add_account_cb(ev: events.CallbackQuery.Event):
    uid = ev.sender_id
    logger.info("handler.add_account.begin", user_id=uid)
    user_states[uid] = {"stage":"api_id","tmp":{}}
    await ev.respond("لطفاً `api_id` را بفرست.", parse_mode="md")
    await ev.answer()

async def consent_yes(ev: events.CallbackQuery.Event):
    uid = ev.sender_id
    logger.info("handler.consent_yes", user_id=uid)
    st = user_states.get(uid)
    if not st or st.get("stage") not in ("consent",):
        await ev.answer("وضعیت نامعتبر. لطفاً دوباره از منو شروع کن.", alert=True)
        return

    api_id = st["tmp"]["api_id"]
    api_hash = st["tmp"]["api_hash"]
    phone = st["tmp"]["phone"]

    # send code request and store phone_code_hash and the transient session string
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    try:
        logger.info("login.send_code_request", user_id=uid, phone=phone)
        sent = await client.send_code_request(phone)
    except Exception as e:
        logger.exception("login.send_code.error", user_id=uid, error=str(e))
        await ev.respond(f"ارسال کد ناموفق: {e}")
        await client.disconnect()
        return

    # save session_str so we can recreate the same session for sign_in later
    session_str = client.session.save()
    st["tmp"]["session_str"] = session_str
    st["tmp"]["phone_code_hash"] = getattr(sent, "phone_code_hash", None)
    st["stage"] = "await_code"
    await ev.respond("کد ارسال شد. کد را بفرست (مثلاً 12345).")
    await client.disconnect()
    await ev.answer()

async def consent_no(ev: events.CallbackQuery.Event):
    logger.info("handler.consent_no", user_id=ev.sender_id)
    user_states.pop(ev.sender_id, None)
    await ev.edit("لغو شد.")

async def generic_inbox(ev: events.NewMessage.Event):
    uid = ev.sender_id
    logger.info("handler.generic_inbox", user_id=uid)
    if uid not in user_states:
        return
    state = user_states[uid]
    stage = state.get("stage")

    if stage == "api_id":
        try:
            state["tmp"]["api_id"] = int(ev.raw_text.strip())
        except:
            logger.warning("login.api_id.invalid", user_id=uid, value=ev.raw_text.strip())
            await ev.respond("api_id عددی نیست. دوباره بفرست.")
            return
        state["stage"] = "api_hash"
        await ev.respond("حالا `api_hash` را بفرست.", parse_mode="md")

    elif stage == "api_hash":
        state["tmp"]["api_hash"] = ev.raw_text.strip()
        state["stage"] = "phone"
        await ev.respond("شمارهٔ تلفن را با کد کشور بفرست (مثلاً +98912xxxxxxx).")

    elif stage == "phone":
        phone = ev.raw_text.strip()
        if not phone.startswith("+"):
            await ev.respond("فرمت شماره صحیح نیست. با + و کد کشور بفرست (مثلاً +98912...)")
            return
        api_id = state["tmp"]["api_id"]
        api_hash = state["tmp"]["api_hash"]

        # disclaimers / consent
        await ev.respond("✅ با ادامه، تایید می‌کنی که مالک این شماره هستی و قوانین تلگرام را نقض نمی‌کنی. تایید؟",
                         buttons=kb([[("تایید", "consent_yes"), ("لغو", "consent_no")]]))
        state["tmp"]["phone"] = phone
        state["stage"] = "consent"
        logger.info("login.phone.set", user_id=uid, phone=phone)

    elif stage == "await_code":
        code = ev.raw_text.strip()
        api_id = state["tmp"]["api_id"]
        api_hash = state["tmp"]["api_hash"]
        phone = state["tmp"]["phone"]
        phone_code_hash = state["tmp"].get("phone_code_hash")
        session_str = state["tmp"].get("session_str")

        # recreate the same transient session used for send_code_request
        if session_str:
            client = TelegramClient(StringSession(session_str), api_id, api_hash)
        else:
            client = TelegramClient(StringSession(), api_id, api_hash)

        await client.connect()
        try:
            # try sign-in with code (pass phone_code_hash if available)
            logger.info("login.sign_in.code", user_id=uid)
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        except SessionPasswordNeededError:
            # keep session_str for password step and ask for 2FA password
            state["tmp"]["session_str"] = client.session.save()
            state["stage"] = "await_password"
            await ev.respond("حساب شما دارای رمز دو مرحله‌ای است. لطفاً رمز را بفرست.")
            await client.disconnect()
            return
        except Exception as e:
            logger.exception("login.sign_in.code.error", user_id=uid, error=str(e))
            await ev.respond(f"ورود ناموفق: {e}")
            await client.disconnect()
            return

        # successful sign in -> save session string (encrypted) and persist as needed
        session_str = client.session.save()
        enc_session = encrypt_str(session_str)  # bytes
        enc_api_hash = encrypt_str(api_hash)  # encrypt api_hash too
        await client.disconnect()

        # Save account to database
        async with SessionLocal() as s:
            account = Account(
                owner_id=uid,
                api_id=str(api_id),
                api_hash_enc=enc_api_hash,
                phone=phone,
                session_enc=enc_session,
                is_active=True
            )
            s.add(account)
            await s.commit()

        logger.info("login.sign_in.success", user_id=uid)
        await ev.respond("✅ ورود موفق! اکانت شما ذخیره شد.")
        user_states.pop(uid, None)  # clear user state
        return

    elif stage == "await_password":
        password = ev.raw_text.strip()
        api_id = state["tmp"]["api_id"]
        api_hash = state["tmp"]["api_hash"]
        phone = state["tmp"]["phone"]
        session_str = state["tmp"].get("session_str")

        # recreate the same transient session used for previous steps
        if session_str:
            client = TelegramClient(StringSession(session_str), api_id, api_hash)
        else:
            client = TelegramClient(StringSession(), api_id, api_hash)

        await client.connect()
        try:
            # complete sign-in with password
            logger.info("login.sign_in.password", user_id=uid)
            await client.sign_in(password=password)
        except Exception as e:
            logger.exception("login.sign_in.password.error", user_id=uid, error=str(e))
            await ev.respond(f"رمز اشتباه یا ورود ناموفق: {e}")
            await client.disconnect()
            return

        session_str = client.session.save()
        enc_session = encrypt_str(session_str)
        enc_api_hash = encrypt_str(api_hash)  # encrypt api_hash too
        await client.disconnect()

        # Save account to database
        async with SessionLocal() as s:
            account = Account(
                owner_id=uid,
                api_id=str(api_id),
                api_hash_enc=enc_api_hash,
                phone=phone,
                session_enc=enc_session,
                is_active=True
            )
            s.add(account)
            await s.commit()

        logger.info("login.sign_in.password.success", user_id=uid)
        await ev.respond("✅ ورود موفق با رمز دومرحله‌ای! اکانت شما ذخیره شد.")
        user_states.pop(uid, None)  # clear user state
        return

async def sessions_menu(ev: events.CallbackQuery.Event):
    uid = ev.sender_id
    logger.info("handler.sessions_menu", user_id=uid)
    async with SessionLocal() as s:
        res = await s.execute(select(Account).where(Account.owner_id==uid))
        accounts = res.scalars().all()
    if not accounts:
        await ev.respond("هیچ اکانتی ثبت نشده.", buttons=kb([[("➕ ایجاد اکانت", "add_account")]]))
        return
    rows = []
    for a in accounts:
        state = "فعال ✅" if a.is_active else "غیرفعال ⏸"
        rows.append([(f"{a.phone} — {state}", f"acc_{a.id}")])
    await ev.respond("اکانت‌های شما:", buttons=kb(rows + [[("بازگشت", "back_home")]]))

async def account_actions(ev: events.CallbackQuery.Event):
    aid = int(ev.data.decode().split("_")[1])
    logger.info("handler.account_actions", account_id=aid, user_id=ev.sender_id)
    buttons = kb([
        [("⏸ غیرفعال", f"acc_disable_{aid}"), ("▶️ فعال", f"acc_enable_{aid}")],
        [("🗑 حذف", f"acc_delete_{aid}")],
        [("🔁 enqueue", f"acc_enqueue_{aid}")],
        [("⬅️ بازگشت", "sessions")]
    ])
    await ev.respond(f"مدیریت اکانت #{aid}", buttons=buttons)

async def acc_disable(ev: events.CallbackQuery.Event):
    aid = int(ev.data.decode().split("_")[2])
    logger.info("handler.acc_disable", account_id=aid, user_id=ev.sender_id)
    async with SessionLocal() as s:
        a = await s.get(Account, aid)
        if a:
            a.is_active = False
            await s.commit()
    await ev.answer("اکانت غیرفعال شد.")

async def acc_enable(ev: events.CallbackQuery.Event):
    aid = int(ev.data.decode().split("_")[2])
    logger.info("handler.acc_enable", account_id=aid, user_id=ev.sender_id)
    async with SessionLocal() as s:
        a = await s.get(Account, aid)
        if a:
            a.is_active = True
            a.total_floodwait_s_24h = 0
            await s.commit()
    await ev.answer("اکانت فعال شد.")

async def acc_delete(ev: events.CallbackQuery.Event):
    aid = int(ev.data.decode().split("_")[2])
    logger.info("handler.acc_delete", account_id=aid, user_id=ev.sender_id)
    async with SessionLocal() as s:
        a = await s.get(Account, aid)
        if a:
            await s.delete(a)
            await s.commit()
    await ev.answer("اکانت حذف شد.")

async def acc_enqueue(ev: events.CallbackQuery.Event):
    from src.m_queue import schedule_next_for_account
    aid = int(ev.data.decode().split("_")[2])
    logger.info("handler.acc_enqueue", account_id=aid, user_id=ev.sender_id)
    async with SessionLocal() as s:
        a = await s.get(Account, aid)
        if not a:
            await ev.answer("اکانت یافت نشد.")
            return
        await schedule_next_for_account(s, a)
    await ev.answer("Job اضافه شد.")

async def my_stats_cmd(ev: events.NewMessage.Event):
    logger.info("handler.my_stats", user_id=ev.sender_id)
    a,g,q,f = await my_stats(ev.sender_id)
    await ev.respond(f"📊 آمار شما:\n"
                     f"اکانت فعال: {a}\n"
                     f"گروه‌های ساخته‌شده 24h: {g}\n"
                     f"Jobهای در صف/اجرا: {q}\n"
                     f"شکست‌ها: {f}")

def register_handlers(c: TelegramClient) -> None:
    # Register handlers programmatically
    c.add_event_handler(start, events.NewMessage(pattern="/start"))
    c.add_event_handler(stats_cb, events.CallbackQuery(pattern=b"stats"))
    c.add_event_handler(add_account_cb, events.CallbackQuery(pattern=b"add_account"))
    c.add_event_handler(consent_yes, events.CallbackQuery(pattern=b"consent_yes"))
    c.add_event_handler(consent_no, events.CallbackQuery(pattern=b"consent_no"))
    c.add_event_handler(generic_inbox, events.NewMessage())
    c.add_event_handler(sessions_menu, events.CallbackQuery(pattern=b"sessions"))
    c.add_event_handler(account_actions, events.CallbackQuery(pattern=b"acc_"))
    c.add_event_handler(acc_disable, events.CallbackQuery(pattern=b"acc_disable_"))
    c.add_event_handler(acc_enable, events.CallbackQuery(pattern=b"acc_enable_"))
    c.add_event_handler(acc_delete, events.CallbackQuery(pattern=b"acc_delete_"))
    c.add_event_handler(acc_enqueue, events.CallbackQuery(pattern=b"acc_enqueue_"))
    c.add_event_handler(my_stats_cmd, events.NewMessage(pattern="/my_stats"))
    logger.info("handlers.registered")

async def main():
    try:
        await init_db()
        logger.info("bot.starting")

        if not BOT_TOKEN:
            raise RuntimeError("BOT_TOKEN not set in .env")

        # create client with the running loop and then register handlers
        loop = asyncio.get_running_loop()
        c = TelegramClient("bot_session", api_id, api_hash, loop=loop)
        await c.start(bot_token=BOT_TOKEN)
        global bot
        bot = c
        register_handlers(c)

        info = await c.get_me()
        logger.info("bot.running", username=info.username)

        await c.run_until_disconnected()
        
    except Exception as e:
        logger.exception("bot.startup.error", error=str(e))
        raise

if __name__ == "__main__":
    asyncio.run(main())
