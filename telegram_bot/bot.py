# bot.py
import os, logging, json, datetime as dt, re
from typing import Optional, Tuple, List, Dict
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

# RAG optional
try:
    from services.memory import retrieve_health_context
except Exception:
    retrieve_health_context = None

# =================== Env & client ===================
load_dotenv()
SUPABASE_URL   = os.getenv("SUPABASE_URL")
SUPABASE_KEY   = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not all([SUPABASE_URL, SUPABASE_KEY, TELEGRAM_TOKEN]):
    raise RuntimeError("Missing .env values (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY/KEY, TELEGRAM_TOKEN)")
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

# =================== Logging ===================
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("hw-bot")

# =================== Helpers ===================
def get_profile_for_telegram_id(tg_id: int):
    res = sb.table("tg_links").select("user_id").eq("telegram_id", tg_id).maybe_single().execute()
    row = getattr(res, "data", None)
    if not row:
        return None
    prof = sb.table("profiles").select("*").eq("id", row["user_id"]).maybe_single().execute()
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
        sb.table("hw_users").upsert({"uid": uid, "tg_chat_id": int(tg_id) if tg_id else None}).execute()
    except Exception as e:
        log.info("ensure_hw_user skipped/failed: %s", e)

def day_range_utc(tz: ZoneInfo, when: Optional[dt.datetime] = None) -> Tuple[str, str]:
    now_l = (when or dt.datetime.now(tz))
    start_l = now_l.replace(hour=0, minute=0, second=0, microsecond=0)
    end_l = start_l + dt.timedelta(days=1)
    return start_l.astimezone(dt.timezone.utc).isoformat(), end_l.astimezone(dt.timezone.utc).isoformat()

def rolling_window_utc(hours: int) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)).isoformat()

def get_metrics_window(uid: str, tz: ZoneInfo, hours_back: int = 48) -> Optional[dict]:
    """Prefer today’s latest; else last N hours."""
    start_today, end_today = day_range_utc(tz)
    try:
        r = (sb.table("hw_metrics").select("*")
             .eq("uid", uid).gte("ts", start_today).lt("ts", end_today)
             .order("ts", desc=True).limit(1).execute())
        row = (getattr(r, "data", None) or [None])[0]
        if row:
            return row
        since = rolling_window_utc(hours_back)
        r2 = (sb.table("hw_metrics").select("*")
              .eq("uid", uid).gte("ts", since).order("ts", desc=True).limit(1).execute())
        return (getattr(r2, "data", None) or [None])[0]
    except Exception:
        return None

def _as_local(ts_iso: str, tz: ZoneInfo) -> str:
    try:
        t = dt.datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).astimezone(tz)
        return t.strftime("%-I:%M %p")
    except Exception:
        return ""

def get_meals_today(uid: str, tz: ZoneInfo) -> List[Dict]:
    """Meals in today's local window."""
    start, end = day_range_utc(tz)
    try:
        r = (sb.table("hw_meals").select("meal_type, items, calories, ts")
             .eq("uid", uid).gte("ts", start).lt("ts", end)
             .order("ts", asc=True).execute())
        return r.data or []
    except Exception:
        return []

def get_meals_recent(uid: str, hours_back: int = 36) -> List[Dict]:
    """Fallback: meals in last N hours (rolling)."""
    try:
        since = rolling_window_utc(hours_back)
        r = (sb.table("hw_meals").select("meal_type, items, calories, ts")
             .eq("uid", uid).gte("ts", since).order("ts", asc=True).execute())
        return r.data or []
    except Exception:
        return []

def get_meals_from_metrics(uid: str, tz: ZoneInfo, hours_back: int = 36) -> List[Dict]:
    """Fallback: read meals_json from recent hw_metrics rows if hw_meals is empty."""
    try:
        since = rolling_window_utc(hours_back)
        r = (sb.table("hw_metrics").select("meals_json, ts")
             .eq("uid", uid).gte("ts", since).order("ts", asc=True).execute())
        out = []
        for row in r.data or []:
            mj = row.get("meals_json")
            if not mj:
                continue
            try:
                meals = json.loads(mj)
            except Exception:
                continue
            for mtype in ("breakfast", "lunch", "dinner", "snacks"):
                m = (meals.get(mtype) or {})
                items = (m.get("items") or "") or None
                cal = m.get("calories")
                # only if something is present
                if items or cal not in (None, "", "null"):
                    out.append({
                        "meal_type": mtype,
                        "items": items,
                        "calories": cal,
                        "ts": row.get("ts"),
                    })
        return out
    except Exception:
        return []

def fmt_meal_row(r: Dict, tz: ZoneInfo) -> str:
    m = (r.get("meal_type") or "").capitalize()
    items = (r.get("items") or "").strip()
    kcal = r.get("calories")
    tail = f" • ~{int(kcal)} kcal" if isinstance(kcal, (int, float)) else ""
    t = _as_local(r.get("ts",""), tz)
    when = f" • {t}" if t else ""
    return f"- {m}: {items}{tail}{when}".strip()

def _recent_context_snippets(uid: str, k: int = 8) -> str:
    if not retrieve_health_context:
        return ""
    try:
        ctx = retrieve_health_context(uid, "chat", k=k) or []
        snips = []
        for r in ctx:
            if isinstance(r, dict):
                for key in ("text", "items", "blurb", "notes"):
                    v = r.get(key)
                    if isinstance(v, str) and v.strip():
                        snips.append(v.strip())
        return " ".join(snips)[:2000]
    except Exception:
        return ""

def _fmt(v, unit=""):
    if v is None:
        return "—"
    try:
        return f"{int(v)}{unit}"
    except Exception:
        return str(v)

def summarize_physical(day: dict) -> str:
    if not day:
        return "No physical metrics logged today."
    parts = [f"Steps: {_fmt(day.get('steps'))}",
             f"Sleep: {_fmt(day.get('sleep_minutes'),' min')}"]
    if day.get("heart_rate") is not None:
        parts.append(f"HR: {_fmt(day.get('heart_rate'),' bpm')}")
    if day.get("pain_level") is not None:
        parts.append(f"Pain: {_fmt(day.get('pain_level'))}/5")
    if day.get("energy_level") is not None:
        parts.append(f"Energy: {_fmt(day.get('energy_level'))}/5")
    return " | ".join(parts)

def summarize_mental(day: dict) -> str:
    if not day:
        return "No mental metrics logged today."
    parts = []
    if day.get("mood") is not None:         parts.append(f"Mood: {_fmt(day.get('mood'))}/5")
    if day.get("stress_level") is not None: parts.append(f"Stress: {_fmt(day.get('stress_level'))}/5")
    if day.get("anxiety_level") is not None:parts.append(f"Anxiety: {_fmt(day.get('anxiety_level'))}/5")
    if day.get("focus_level") is not None:  parts.append(f"Focus: {_fmt(day.get('focus_level'))}/5")
    return " | ".join(parts) if parts else "No mental metrics logged today."

# =================== Chat memory persistence ===================
def log_chat(uid: str, role: str, text: str):
    try:
        emb = embed_text(text) if text and len(text) < 2000 else None
    except Exception:
        emb = None
    try:
        sb.table("hw_chat").insert({"uid": uid, "role": role, "text": text, "embedding": emb}).execute()
    except Exception as e:
        log.info("log_chat failed (non-fatal): %s", e)

def get_chat_history(uid: str, limit: int = 10) -> List[Dict]:
    try:
        r = (sb.table("hw_chat").select("role,text,ts")
             .eq("uid", uid).order("ts", desc=True).limit(limit).execute())
        return list(reversed(r.data or []))
    except Exception:
        return []

def build_prompt(profile: dict, user_text: str, today: dict, convo: List[Dict]) -> str:
    try:
        nrows = (sb.table("hw_nudges_log").select("payload")
                 .eq("uid", profile["id"]).order("ts", desc=True).limit(3).execute().data or [])
        nudges = []
        for n in nrows:
            p = n.get("payload")
            if isinstance(p, str):
                try: p = json.loads(p)
                except: p = {}
            msg = (p or {}).get("msg")
            if isinstance(msg, str): nudges.append(msg)
    except Exception:
        nudges = []

    convo_lines = []
    for m in convo[-8:]:
        role = "User" if m.get("role") == "user" else "Coach"
        text = (m.get("text") or "").replace("\n", " ").strip()
        if text:
            convo_lines.append(f"{role}: {text}")
    convo_blob = "\n".join(convo_lines)
    ctx_snips = _recent_context_snippets(profile["id"], k=8)

    return f"""
You are Health Whisperer, a supportive wellness coach. Be brief, actionable, and safe.
No medical diagnosis; if serious symptoms, advise seeing a clinician.

User profile: {profile}
Latest metrics (prefer today; else last 48h): {today}
Recent nudges: {nudges}
Conversation so far:
{convo_blob}

Recent context (journal/meals/chat blurbs): {ctx_snips}

User says: {user_text}

Reply with 1–2 short bullet points (<80 words total).
""".strip()

# =================== Conversation: /checkin (full flow) ===================
(
    ASK_BREAKFAST, ASK_LUNCH, ASK_DINNER, ASK_SNACKS,
    ASK_MOOD, ASK_MEAL_QUALITY,
    ASK_WORKOUT, ASK_HR, ASK_STEPS, ASK_SLEEP
) = range(10)

def _parse_items_kcal(text: str):
    t = (text or "").strip()
    if t.lower() in ("none", "no", "nil", "na"):
        return None, 0
    parts = t.split(";")
    if len(parts) == 2:
        items = parts[0].strip()
        try:
            cal = int(parts[1].strip())
        except:
            cal = None
    else:
        items = t
        toks = items.split()
        cal = None
        if toks and toks[-1].isdigit():
            cal = int(toks[-1]); items = " ".join(toks[:-1]).strip()
    return (items or None), cal

def _make_meal_blurb(mtype: str, items: str | None, calories: int | None) -> str:
    parts = []
    if mtype: parts.append(mtype.capitalize())
    if items: parts.append(items)
    if calories is not None: parts.append(f"~{int(calories)} kcal")
    return " • ".join(parts)

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
                    "uid": uid, "ts": now_iso, "meal_type": mtype,
                    "items": items, "calories": kcal, "blurb": blurb,
                    "embedding": emb, "source": "bot"
                })
        if rows:
            sb.table("hw_meals").insert(rows).execute()
        return True
    except Exception as e:
        log.info("hw_meals insert failed; will store in metrics.meals_json instead. %s", e)
        return False

def save_metrics(uid: str, answers: dict, tg_id_for_fix: Optional[int]):
    total_cal = sum(int((answers["meals"].get(k) or {}).get("calories") or 0)
                    for k in ["breakfast","lunch","dinner","snacks"])
    meals_ok = upsert_meals(uid, answers["meals"])

    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    payload = {
        "uid": uid,
        "source": "bot",
        "ts": now_iso,
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
        # hw_users FK safety net
        if getattr(e, "code", "") == "23503" or "not present in table \"hw_users\"" in str(e):
            ensure_hw_user(uid, tg_id_for_fix)
            sb.table("hw_metrics").insert(payload).execute()
            log.info("Saved metrics after creating hw_users for uid=%s", uid)
        else:
            raise

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hi! I'm Health Whisperer. 🌱\n\n"
        "✅ I send timely nudges.\n"
        "💬 I’m also your personal health chatbot — ask about steps, sleep, stress, meals, etc.\n\n"
        "Get started:\n1) In the website, get your /link code\n2) Send: /link ABCD1234\n3) Use /checkin for quick logging, or just chat with me!"
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
        await update.message.reply_text("Unlinked. Use /link <CODE> to link again.")
    except Exception as e:
        log.exception("unlink failed: %s", e)
        await update.message.reply_text("Unlink failed. Try again later.")

async def link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await update.message.reply_text("Usage: /link ABCD1234")
    code = ctx.args[0].strip().upper()
    tg_id = update.effective_user.id
    try:
        res = sb.table("tg_links").select("user_id, link_code").eq("link_code", code).maybe_single().execute()
        row = getattr(res, "data", None)
        if not row:
            return await update.message.reply_text("Invalid or expired code. Generate a fresh one in the website.")
        uid = row["user_id"]
        sb.table("tg_links").update({"telegram_id": tg_id}).eq("link_code", code).execute()
        ensure_hw_user(uid, tg_id)
        try:
            sb.table("hw_preferences").upsert({"uid": uid, "telegram_chat_id": tg_id}).execute()
        except Exception:
            pass
        await update.message.reply_text("Linked! You can now receive nudges and chat with me.")
    except Exception as e:
        log.exception("link failed: %s", e)
        await update.message.reply_text("Link failed. Try again in a minute.")

# ======== /checkin flow (unchanged) ========
(
    ASK_BREAKFAST, ASK_LUNCH, ASK_DINNER, ASK_SNACKS,
    ASK_MOOD, ASK_MEAL_QUALITY,
    ASK_WORKOUT, ASK_HR, ASK_STEPS, ASK_SLEEP
) = range(10)

def _parse_items_kcal(text: str):
    t = (text or "").strip()
    if t.lower() in ("none", "no", "nil", "na"):
        return None, 0
    parts = t.split(";")
    if len(parts) == 2:
        items = parts[0].strip()
        try:
            cal = int(parts[1].strip())
        except:
            cal = None
    else:
        items = t
        toks = items.split()
        cal = None
        if toks and toks[-1].isdigit():
            cal = int(toks[-1]); items = " ".join(toks[:-1]).strip()
    return (items or None), cal

def _make_meal_blurb(mtype: str, items: str | None, calories: int | None) -> str:
    parts = []
    if mtype: parts.append(mtype.capitalize())
    if items: parts.append(items)
    if calories is not None: parts.append(f"~{int(calories)} kcal")
    return " • ".join(parts)

def upsert_meals(uid: str, day_meals: dict) -> bool:
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
                    "uid": uid, "ts": now_iso, "meal_type": mtype,
                    "items": items, "calories": kcal, "blurb": blurb,
                    "embedding": emb, "source": "bot"
                })
        if rows:
            sb.table("hw_meals").insert(rows).execute()
        return True
    except Exception as e:
        log.info("hw_meals insert failed; will store in metrics.meals_json instead. %s", e)
        return False

def save_metrics(uid: str, answers: dict, tg_id_for_fix: Optional[int]):
    total_cal = sum(int((answers["meals"].get(k) or {}).get("calories") or 0)
                    for k in ["breakfast","lunch","dinner","snacks"])
    meals_ok = upsert_meals(uid, answers["meals"])

    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    payload = {
        "uid": uid,
        "source": "bot",
        "ts": now_iso,
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
        else:
            raise

async def checkin_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    profile = get_profile_for_telegram_id(tg_id)
    if not profile:
        return await update.message.reply_text("Please link your account first: /link <CODE>.")
    ensure_hw_user(profile["id"], tg_id)
    ctx.user_data["checkin"] = {"uid": profile["id"], "meals": {"breakfast":{}, "lunch":{}, "dinner":{}, "snacks":{}}}
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
    try:
        mood = int(update.message.text.strip())
    except:
        mood = None
    ctx.user_data["checkin"]["mood"] = mood
    await update.message.reply_text("How would you rate **meal quality** (1–5)?")
    return ASK_MEAL_QUALITY

async def ask_workout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        mq = int(update.message.text.strip())
    except:
        mq = None
    ctx.user_data["checkin"]["meal_quality"] = mq
    await update.message.reply_text("Did you **work out** today? If yes, what was your last sport?")
    return ASK_WORKOUT

async def ask_hr(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["checkin"]["last_sport"] = update.message.text.strip()
    await update.message.reply_text("What’s your **heart rate** right now (bpm)?")
    return ASK_HR

async def ask_steps(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        hr = int(update.message.text.strip())
    except:
        hr = None
    ctx.user_data["checkin"]["heart_rate"] = hr
    await update.message.reply_text("How many **steps** so far today?")
    return ASK_STEPS

async def ask_sleep(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        steps = int(update.message.text.strip())
    except:
        steps = None
    ctx.user_data["checkin"]["steps"] = steps
    await update.message.reply_text("How many **minutes of sleep** last night?")
    return ASK_SLEEP

async def finish_checkin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        sleep = int(update.message.text.strip())
    except:
        sleep = None
    ctx.user_data["checkin"]["sleep_minutes"] = sleep

    data = ctx.user_data["checkin"]
    try:
        save_metrics(data["uid"], data, update.effective_user.id)
    except Exception as e:
        log.exception("Failed to save check-in: %s", e)
        return await update.message.reply_text("I couldn't save your check-in. Please try again.")

    total_cal = sum(int((data["meals"].get(k) or {}).get("calories") or 0) for k in ["breakfast","lunch","dinner","snacks"])
    await update.message.reply_text(
        f"✅ Logged! Calories≈{total_cal}, mood={data.get('mood')}, HR={data.get('heart_rate')}, "
        f"steps={data.get('steps')}, sleep={data.get('sleep_minutes')}."
    )
    return ConversationHandler.END

# =================== Free-text intents + LLM with memory ===================
STEP_PAT  = re.compile(r"\b(steps?|step\s*count)\b", re.I)
SLEEP_PAT = re.compile(r"\b(sleep|minutes\s*of\s*sleep)\b", re.I)
HR_PAT    = re.compile(r"\b(heart\s*rate|hr)\b", re.I)
PHYS_PAT  = re.compile(r"\b(physical\s*health|fitness|how\s*am\s*i\s*physically)\b", re.I)
MENT_PAT  = re.compile(r"\b(mental\s*health|mood|stress|anxiety|focus|how\s*am\s*i\s*mentally)\b", re.I)
MEALS_PAT = re.compile(r"\b(what\s+did\s+i\s+eat|meals?\s+today|today'?s\s+meals?)\b", re.I)

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    profile = get_profile_for_telegram_id(tg_id)
    if not profile:
        return await update.message.reply_text("Please link your account first: /link <CODE> (from the website).")

    uid = profile["id"]
    tz = user_timezone(uid)
    text = (update.message.text or "").strip()
    if not text:
        return

    # Save the user's message
    log_chat(uid, "user", text)

    # Load day windows (today → else last 48h fallback)
    today_metrics = get_metrics_window(uid, tz, hours_back=48) or {}

    # Quick intents
    if STEP_PAT.search(text):  return await update.message.reply_text(f"Steps today: {_fmt(today_metrics.get('steps'))}.")
    if SLEEP_PAT.search(text): return await update.message.reply_text(f"Sleep last night: {_fmt(today_metrics.get('sleep_minutes'),' min')}.")
    if HR_PAT.search(text):    return await update.message.reply_text(f"Current HR (last log): {_fmt(today_metrics.get('heart_rate'),' bpm')}.")
    if PHYS_PAT.search(text):  return await update.message.reply_text(f"Physical snapshot — {summarize_physical(today_metrics)}")
    if MENT_PAT.search(text):  return await update.message.reply_text(f"Mental snapshot — {summarize_mental(today_metrics)}")
    if MEALS_PAT.search(text):
        meals = get_meals_today(uid, tz)
        source = "today"
        if not meals:
            meals = get_meals_recent(uid, hours_back=36)
            source = "last 36h"
        if not meals:
            meals = get_meals_from_metrics(uid, tz, hours_back=36)
            source = "metrics:last 36h"
        if not meals:
            return await update.message.reply_text("I don’t see meals for today (or last 36h). If you added them on the site, please ensure the timestamp is saved with timezone/UTC.")
        lines = [fmt_meal_row(m, tz) for m in meals]
        return await update.message.reply_text(f"Meals ({source}):\n" + "\n".join(lines))

    # LLM with conversation memory + recent context
    convo = get_chat_history(uid, limit=10)
    prompt = build_prompt(profile, text, today_metrics, convo)
    try:
        reply = chat_text("You are Personalized Health Whisperer. Keep replies under 80 words; no medical diagnosis.", prompt)
        msg = reply.strip() if reply else "I'm here for you."
    except Exception as e:
        log.exception("LLM generation failed: %s", e)
        msg = "I couldn't generate a tip right now. Please try again later."

    # Save assistant reply
    log_chat(uid, "assistant", msg)
    await update.message.reply_text(msg)

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
            ASK_LUNCH:     [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_dinner)],
            ASK_DINNER:    [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_snacks)],
            ASK_SNACKS:    [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_mood)],
            ASK_MOOD:      [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_meal_quality)],
            ASK_MEAL_QUALITY:[MessageHandler(filters.TEXT & ~filters.COMMAND, ask_workout)],
            ASK_WORKOUT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_hr)],
            ASK_HR:        [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_steps)],
            ASK_STEPS:     [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_sleep)],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
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
