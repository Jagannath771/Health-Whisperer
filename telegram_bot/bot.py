# bot.py
import os, logging, json, datetime as dt, re
from typing import Optional, Tuple
from dotenv import load_dotenv
from supabase import create_client
from postgrest.exceptions import APIError
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler,
    ContextTypes, filters
)

from services.llm_openai import chat_text, embed_text
from services.memory import retrieve_health_context  # RAG for chat

# =================== Env & Clients ===================
load_dotenv()
SUPABASE_URL   = os.getenv("SUPABASE_URL")
SUPABASE_KEY   = os.getenv("SUPABASE_SERVICE_ROLE_KEY")  # service role for bot/worker
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not all([SUPABASE_URL, SUPABASE_KEY, TELEGRAM_TOKEN]):
    raise RuntimeError("Missing .env values")

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

# =================== Logging ===================
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("hw-bot")

# =================== Helpers ===================
def get_profile_for_telegram_id(tg_id: int):
    link_res = sb.table("tg_links").select("user_id").eq("telegram_id", tg_id).maybe_single().execute()
    data = getattr(link_res, "data", None)
    if not data:
        return None
    prof = sb.table("profiles").select("*").eq("id", data["user_id"]).maybe_single().execute()
    return getattr(prof, "data", None)

def user_timezone(uid: str) -> ZoneInfo:
    tz = "America/New_York"
    try:
        r = sb.table("hw_preferences").select("tz").eq("uid", uid).maybe_single().execute()
        tz = (getattr(r, "data", {}) or {}).get("tz") or tz
    except Exception:
        pass
    try:
        return ZoneInfo(tz)
    except Exception:
        return ZoneInfo("America/New_York")

def ensure_hw_user(uid: str, tg_id: Optional[int]):
    try:
        sb.table("hw_users").upsert({
            "uid": uid,
            "tg_chat_id": int(tg_id) if tg_id else None
        }).execute()
    except Exception as e:
        log.info("ensure_hw_user skipped/failed: %s", e)

def _make_meal_blurb(mtype: str, items: str | None, calories: int | None) -> str:
    parts = []
    if mtype: parts.append(mtype.capitalize())
    if items: parts.append(items)
    if calories is not None: parts.append(f"~{int(calories)} kcal")
    return " • ".join(parts)

def today_range_for_user(tz: ZoneInfo) -> Tuple[str, str]:
    now_l = dt.datetime.now(tz)
    start_l = now_l.replace(hour=0, minute=0, second=0, microsecond=0)
    end_l = start_l + dt.timedelta(days=1)
    return start_l.astimezone(dt.timezone.utc).isoformat(), end_l.astimezone(dt.timezone.utc).isoformat()

def get_today_metrics(uid: str, tz: ZoneInfo):
    start, end = today_range_for_user(tz)
    res = (sb.table("hw_metrics").select("*")
           .eq("uid", uid).gte("ts", start).lt("ts", end)
           .order("ts", desc=True).limit(1).execute())
    return (getattr(res, "data", None) or [None])[0]

def _parse_items_kcal(text: str):
    text = (text or "").strip()
    if text.lower() in ("none", "no", "nil", "na"):
        return None, 0
    parts = text.split(";")
    if len(parts) == 2:
        items = parts[0].strip()
        try: cal = int(parts[1].strip())
        except: cal = None
    else:
        items = text
        toks = items.split()
        cal = None
        if toks and toks[-1].isdigit():
            cal = int(toks[-1]); items = " ".join(toks[:-1]).strip()
    return (items or None), cal

def upsert_meals(uid: str, day_meals: dict) -> bool:
    """Insert hw_meals with blurb + embedding so RAG can retrieve."""
    try:
        rows = []
        now_iso = dt.datetime.now(dt.timezone.utc).isoformat()

        for mtype in ["breakfast","lunch","dinner","snacks"]:
            m = (day_meals.get(mtype) or {})
            items = m.get("items")
            kcal  = int(m.get("calories") or 0) if m.get("calories") is not None else None
            if items or (kcal is not None):
                blurb = _make_meal_blurb(mtype, items, kcal)
                emb = embed_text(blurb)
                rows.append({
                    "uid": uid,
                    "ts": now_iso,
                    "meal_type": mtype,
                    "items": items,
                    "calories": kcal,
                    "blurb": blurb,
                    "embedding": emb,
                    "source": "bot"
                })

        if rows:
            sb.table("hw_meals").insert(rows).execute()
        return True
    except Exception as e:
        log.info("hw_meals insert failed; falling back to meals_json. %s", e)
        return False

def save_metrics(uid: str, answers: dict, tg_id_for_fix: Optional[int]):
    """Persist a check-in. CRITICAL: include ts so 'today' filters work."""
    total_cal = sum(int((answers["meals"].get(k) or {}).get("calories") or 0)
                    for k in ["breakfast","lunch","dinner","snacks"])
    meals_ok = upsert_meals(uid, answers["meals"])

    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()  # <-- add ts
    payload = {
        "uid": uid,
        "source": "bot",
        "ts": now_iso,                    # <--- key fix
        "heart_rate": answers.get("heart_rate"),
        "steps": answers.get("steps"),
        "sleep_minutes": answers.get("sleep_minutes"),
        "mood": answers.get("mood"),
        "meal_quality": answers.get("meal_quality"),
        "calories": total_cal,
        "notes": answers.get("last_sport"),
    }
    if not meals_ok:
        payload["meals_json"] = json.dumps(answers["meals"])

    try:
        sb.table("hw_metrics").insert(payload).execute()
        log.info("Saved metrics for uid=%s (kcal=%s steps=%s sleep=%s)", uid, total_cal, answers.get("steps"), answers.get("sleep_minutes"))
    except APIError as e:
        if getattr(e, "code", "") == "23503" or "not present in table \"hw_users\"" in str(e):
            ensure_hw_user(uid, tg_id_for_fix)
            sb.table("hw_metrics").insert(payload).execute()
            log.info("Saved metrics after creating hw_users for uid=%s", uid)
        else:
            raise

def _recent_context_snippets(uid: str, k: int = 8) -> str:
    """Short snippets from unified history (journal, meals blurbs, chat)."""
    try:
        ctx = retrieve_health_context(uid, "chat", k=k) or []
        snips = []
        for r in ctx:
            if isinstance(r, dict):
                for key in ("text", "items", "blurb"):
                    v = r.get(key)
                    if isinstance(v, str) and v.strip():
                        snips.append(v.strip())
        joined = " ".join(snips)
        return joined[:2000]
    except Exception:
        return ""

def _fmt(val, unit=""):
    if val is None: return "—"
    try:
        if unit:
            return f"{int(val)}{unit}"
        return f"{int(val)}"
    except Exception:
        return str(val)

def summarize_physical(today: dict) -> str:
    steps = today.get("steps")
    sleep = today.get("sleep_minutes")
    hr    = today.get("heart_rate")
    pain  = today.get("pain_level")
    energy= today.get("energy_level")
    parts = []
    parts.append(f"Steps: {_fmt(steps)}")
    parts.append(f"Sleep: {_fmt(sleep,' min')}")
    if hr is not None: parts.append(f"HR: {_fmt(hr,' bpm')}")
    if pain is not None: parts.append(f"Pain: {_fmt(pain)} /5")
    if energy is not None: parts.append(f"Energy: {_fmt(energy)} /5")
    return " | ".join(parts)

def summarize_mental(today: dict) -> str:
    mood   = today.get("mood")
    stress = today.get("stress_level")
    anx    = today.get("anxiety_level")
    focus  = today.get("focus_level")
    parts = []
    if mood is not None:   parts.append(f"Mood: {_fmt(mood)} /5")
    if stress is not None: parts.append(f"Stress: {_fmt(stress)} /5")
    if anx is not None:    parts.append(f"Anxiety: {_fmt(anx)} /5")
    if focus is not None:  parts.append(f"Focus: {_fmt(focus)} /5")
    return " | ".join(parts) if parts else "No mental metrics logged today."

def build_prompt(profile: dict, user_text: str, today: dict) -> str:
    # Enrich with recent context + latest metrics
    ctx_snips = _recent_context_snippets(profile["id"], k=8)
    return f"""
You are Health Whisperer, a supportive wellness coach. Give brief, actionable, safe suggestions.
Never give medical diagnoses. If symptoms are serious, advise seeing a clinician.

User profile:
- Name: {profile.get('full_name')}
- Age: {profile.get('age')}
- Gender: {profile.get('gender')}
- Height (cm): {profile.get('height_cm')}
- Weight (kg): {profile.get('weight_kg')}
- Activity level: {profile.get('activity_level')}
- Goals: {profile.get('goals')}
- Conditions: {profile.get('conditions')}
- Medications: {profile.get('medications')}
- Timezone: {profile.get('timezone')}

Latest metrics today:
{today}

Recent context (journal/meals/chat blurbs, last few days):
{ctx_snips}

User message: {user_text}

Return 1–2 short bullet points with practical next steps. Keep it under 80 words total.
""".strip()

# =================== Conversation: /checkin ===================
(
    ASK_BREAKFAST, ASK_LUNCH, ASK_DINNER, ASK_SNACKS,
    ASK_MOOD, ASK_MEAL_QUALITY,
    ASK_WORKOUT, ASK_HR, ASK_STEPS, ASK_SLEEP
) = range(10)

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hi! I'm Health Whisperer.\n\n"
        "1) In the website, get your /link code\n"
        "2) Send: /link ABCD1234\n"
        "3) Use /checkin to log your day quickly, or just say hi!"
    )

async def whoami(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    link = sb.table("tg_links").select("user_id, link_code").eq("telegram_id", tg_id).maybe_single().execute().data or {}
    await update.message.reply_text(f"telegram_id={tg_id}\nlinked_uid={link.get('user_id')}\nlink_code={link.get('link_code')}")

async def unlink(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    try:
        row = sb.table("tg_links").select("user_id").eq("telegram_id", tg_id).maybe_single().execute().data or {}
        uid = row.get("user_id")
        if uid:
            sb.table("hw_preferences").update({"telegram_chat_id": None}).eq("uid", uid).execute()
            sb.table("hw_users").update({"tg_chat_id": None}).eq("uid", uid).execute()
        sb.table("tg_links").delete().eq("telegram_id", tg_id).execute()
        await update.message.reply_text("This Telegram account is unlinked. Use /link <CODE> to link again.")
    except Exception as e:
        log.exception("unlink failed: %s", e)
        await update.message.reply_text("Unlink failed. Try again later.")

async def link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await update.message.reply_text("Usage: /link ABCD1234")
    code = ctx.args[0].strip().upper()
    tg_id = update.effective_user.id

    try:
        res = sb.table("tg_links").select("user_id, link_code, telegram_id").eq("link_code", code).maybe_single().execute()
        row = getattr(res, "data", None)
        if not row:
            return await update.message.reply_text("Invalid or expired code. Generate a fresh one in the website.")

        user_id_for_code = row["user_id"]
        sb.table("tg_links").update({"telegram_id": tg_id}).eq("link_code", code).execute()
        try:
            sb.table("tg_links").delete().neq("link_code", code).eq("telegram_id", tg_id).execute()
        except Exception:
            pass

        ensure_hw_user(user_id_for_code, tg_id)
        try:
            sb.table("hw_preferences").upsert({"uid": user_id_for_code, "telegram_chat_id": tg_id}).execute()
        except Exception:
            pass

        log.info("Linked telegram_id=%s to uid=%s", tg_id, user_id_for_code)
        return await update.message.reply_text("Linked! You can now receive personalized nudges.")
    except APIError as e:
        log.exception("Supabase APIError during /link: %s", e)
        return await update.message.reply_text("Link failed due to a database constraint. Generate a new code and try again.")
    except Exception as e:
        log.exception("Unexpected /link failure: %s", e)
        return await update.message.reply_text("Link failed. Please try again in a minute.")

async def checkin_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    profile = get_profile_for_telegram_id(tg_id)
    if not profile:
        return await update.message.reply_text("Please link your account first: /link <CODE>.")

    ensure_hw_user(profile["id"], tg_id)

    ctx.user_data["checkin"] = {
        "uid": profile["id"],
        "meals": {"breakfast":{}, "lunch":{}, "dinner":{}, "snacks":{}}
    }
    await update.message.reply_text("Let’s do a quick check-in. 🍽️ What did you have for **breakfast**? (items; kcal)")
    return ASK_BREAKFAST

async def ask_lunch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    items, cal = _parse_items_kcal(update.message.text)
    ctx.user_data["checkin"]["meals"]["breakfast"] = {"items": items, "calories": cal}
    await update.message.reply_text("What about **lunch**? (items; kcal)")
    return ASK_LUNCH

async def ask_dinner(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    items, cal = _parse_items_kcal(update.message.text)
    ctx.user_data["checkin"]["meals"]["lunch"] = {"items": items, "calories": cal}
    await update.message.reply_text("What about **dinner**? (items; kcal)")
    return ASK_DINNER

async def ask_snacks(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    items, cal = _parse_items_kcal(update.message.text)
    ctx.user_data["checkin"]["meals"]["dinner"] = {"items": items, "calories": cal}
    await update.message.reply_text("Any **snacks**? (items; kcal) If none, say 'none'.")
    return ASK_SNACKS

async def ask_mood(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    items, cal = _parse_items_kcal(update.message.text)
    ctx.user_data["checkin"]["meals"]["snacks"] = {"items": items, "calories": cal}
    await update.message.reply_text("How’s your **mood** today (1–5)?")
    return ASK_MOOD

async def ask_meal_quality(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try: mood = int(update.message.text.strip())
    except: mood = None
    ctx.user_data["checkin"]["mood"] = mood
    await update.message.reply_text("How would you rate **meal quality** (1–5)?")
    return ASK_MEAL_QUALITY

async def ask_workout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try: mq = int(update.message.text.strip())
    except: mq = None
    ctx.user_data["checkin"]["meal_quality"] = mq
    await update.message.reply_text("Did you **work out** today? If yes, what was your last sport?")
    return ASK_WORKOUT

async def ask_hr(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["checkin"]["last_sport"] = update.message.text.strip()
    await update.message.reply_text("What’s your **heart rate** right now (bpm)?")
    return ASK_HR

async def ask_steps(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try: hr = int(update.message.text.strip())
    except: hr = None
    ctx.user_data["checkin"]["heart_rate"] = hr
    await update.message.reply_text("How many **steps** so far today?")
    return ASK_STEPS

async def ask_sleep(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try: steps = int(update.message.text.strip())
    except: steps = None
    ctx.user_data["checkin"]["steps"] = steps
    await update.message.reply_text("How many **minutes of sleep** last night?")
    return ASK_SLEEP

async def finish_checkin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try: sleep = int(update.message.text.strip())
    except: sleep = None
    ctx.user_data["checkin"]["sleep_minutes"] = sleep

    data = ctx.user_data["checkin"]
    try:
        save_metrics(data["uid"], data, update.effective_user.id)
    except Exception as e:
        log.exception("Failed to save check-in: %s", e)
        return await update.message.reply_text("I couldn't save your check-in. Please try again.")

    total_cal = sum(int((data["meals"].get(k) or {}).get("calories") or 0) for k in ["breakfast","lunch","dinner","snacks"])
    log.info("Check-in complete for uid=%s (kcal≈%s, steps=%s, sleep=%s)", data["uid"], total_cal, data.get("steps"), data.get("sleep_minutes"))
    await update.message.reply_text(
        f"✅ Logged! Calories≈{total_cal}, mood={data.get('mood')}, HR={data.get('heart_rate')}, "
        f"steps={data.get('steps')}, sleep={data.get('sleep_minutes')}."
    )
    return ConversationHandler.END

# =================== Free-text: smart Q&A first; LLM second ===================
STEP_PAT = re.compile(r"\b(steps?|step\s*count)\b", re.I)
SLEEP_PAT = re.compile(r"\b(sleep|minutes\s*of\s*sleep)\b", re.I)
HR_PAT = re.compile(r"\b(heart\s*rate|hr)\b", re.I)
PHYS_PAT = re.compile(r"\b(physical\s*health|fitness|how\s*am\s*i\s*physically)\b", re.I)
MENT_PAT = re.compile(r"\b(mental\s*health|mood|stress|anxiety|focus|how\s*am\s*i\s*mentally)\b", re.I)

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    try:
        profile = get_profile_for_telegram_id(tg_id)
    except Exception as e:
        log.exception("Profile lookup failed: %s", e)
        return await update.message.reply_text("Profile lookup failed. Check Supabase creds / RLS.")

    if not profile:
        return await update.message.reply_text("Please link your account first: /link <CODE> (from the website).")

    tz = user_timezone(profile["id"])
    today = get_today_metrics(profile["id"], tz) or {}

    text = update.message.text or ""
    # 1) Quick answers from today's metrics
    if today:
        if STEP_PAT.search(text):
            val = today.get("steps")
            return await update.message.reply_text(f"Steps today: {_fmt(val)}.")
        if SLEEP_PAT.search(text):
            val = today.get("sleep_minutes")
            return await update.message.reply_text(f"Sleep last night: {_fmt(val,' min')}.")
        if HR_PAT.search(text):
            val = today.get("heart_rate")
            return await update.message.reply_text(f"Current HR (last log): {_fmt(val,' bpm')}.")
        if PHYS_PAT.search(text):
            return await update.message.reply_text(f"Physical snapshot — {summarize_physical(today)}")
        if MENT_PAT.search(text):
            return await update.message.reply_text(f"Mental snapshot — {summarize_mental(today)}")

    # 2) If no direct metric intent, generate a brief contextual tip
    prompt = build_prompt(profile, text, today)
    try:
        reply = chat_text(
            "You are Personalized Health Whisperer. Keep replies under 80 words; no medical diagnosis.",
            prompt
        )
        msg = reply.strip() if reply else "I'm here for you."
        await update.message.reply_text(msg)
        log.info("Replied to free-text for uid=%s", profile["id"])
    except Exception as e:
        log.exception("LLM generation failed: %s", e)
        await update.message.reply_text("I couldn't generate a tip right now. Please try again later.")

# =================== Error handler & app wiring ===================
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Bot error", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text("Oops, something went wrong. Please try again.")
    except Exception:
        pass

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_error_handler(on_error)

    checkin = ConversationHandler(
        entry_points=[CommandHandler("checkin", checkin_start)],
        states={
            ASK_BREAKFAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_lunch)],
            ASK_LUNCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_dinner)],
            ASK_DINNER: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_snacks)],
            ASK_SNACKS: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_mood)],
            ASK_MOOD: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_meal_quality)],
            ASK_MEAL_QUALITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_workout)],
            ASK_WORKOUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_hr)],
            ASK_HR: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_steps)],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(CommandHandler("unlink", unlink))
    app.add_handler(CommandHandler("link", link))
    app.add_handler(checkin)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    log.info("Health Whisperer bot running (polling).")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
