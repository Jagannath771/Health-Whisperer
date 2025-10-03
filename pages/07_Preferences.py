# pages/07_Preferences.py
import streamlit as st
from datetime import datetime, timezone as tzmod
from datetime import datetime as dt
from postgrest.exceptions import APIError

from nav import apply_global_ui, top_nav
from supa import get_sb
import zoneinfo

# ===== Global UI / Nav =====
apply_global_ui()
st.set_page_config(page_title="Preferences - Health Whisperer", layout="wide", initial_sidebar_state="collapsed")

is_authed = "sb_session" in st.session_state
top_nav(is_authed=is_authed, current="GetStarted")  # alias highlights the Get Started pill

# ===== Auth guard =====
def on_sign_out():
    try:
        get_sb().auth.sign_out()
    finally:
        st.session_state.pop("sb_session", None)

if not is_authed:
    st.warning("Please sign in first.")
    st.switch_page("pages/02_Sign_In.py")
    st.stop()

uid = st.session_state["sb_session"]["user_id"]
access_token = st.session_state["sb_session"]["access_token"]
sb = get_sb(access_token)  # authed client (RLS-safe)

# ===== Helpers =====
def exec_with_retry(req, tries: int = 3, base_delay: float = 0.4):
    import time
    for i in range(tries):
        try:
            return req.execute()
        except APIError as e:
            if "PGRST303" in str(e) or "JWT expired" in str(e):
                st.error("Your session expired. Please sign in again.")
                on_sign_out()
                st.switch_page("pages/02_Sign_In.py")
                st.stop()
            time.sleep(base_delay * (i + 1))
        except Exception:
            time.sleep(base_delay * (i + 1))
    return req.execute()

def ensure_hw_user(uid_: str):
    exec_with_retry(sb.table("hw_users").upsert({"uid": uid_}))

def idx_or_prepend(options: list[str], value: str, default_first: str | None = None):
    if value and value in options:
        return options, options.index(value)
    if not value and default_first:
        if default_first not in options:
            options = [default_first] + options
        return options, options.index(default_first)
    if value:
        options = [value] + [o for o in options if o != value]
        return options, 0
    return options, 0

def safe_time(val: str, fallback: str) -> dt.time:
    try:
        return dt.strptime(val or fallback, "%H:%M:%S").time()
    except Exception:
        return dt.strptime(fallback, "%H:%M:%S").time()

def load_prefs(uid_: str) -> dict:
    r = exec_with_retry(sb.table("hw_preferences").select("*").eq("uid", uid_).maybe_single())
    row = r.data or None
    if row:
        return row
    # Defaults aligned to your current schema
    return {
        "uid": uid_,
        "nudge_channel": "telegram",
        "quiet_start": "22:00:00",
        "quiet_end": "07:00:00",
        "nudge_cadence": "smart",
        "nudge_tone": "gentle",
        "goals": {},
        "remind_hydration": True,
        "remind_steps": True,
        "remind_sleep": False,
        "tz": "America/New_York",
        "daily_calorie_goal": 2000,
        "daily_step_goal": 8000,
        "daily_water_ml": 2000,
        "sleep_goal_min": 420,
        "protein_target_g": 80,
        "telegram_chat_id": None,
        "calendar_ics_url": None,
        "last_nudge_hash": None,
        "telegram_chat_id_bigint": None,
    }

def upsert_prefs(payload: dict):
    exec_with_retry(sb.table("hw_preferences").upsert(payload, on_conflict="uid"))

# ===== Hero =====
st.markdown("""
<div class="hw-hero">
  <h1>Preferences</h1>
  <h3>Tune your nudges</h3>
  <p>Choose the channel, cadence, tone, and quiet hours. Toggle specific nudges —
  we’ll keep things helpful, not noisy.</p>
</div>
""", unsafe_allow_html=True)

st.page_link("pages/04_Get_Started.py", label="← Back to Get Started", icon="🚀")

st.divider()

# ===== Data load / ensure FK target =====
ensure_hw_user(uid)
current = load_prefs(uid)

# Quick glance metrics
c1, c2, c3 = st.columns(3)
with c1:
    st.metric("Primary channel", (current.get("nudge_channel") or "telegram").title())
with c2:
    st.metric("Cadence", (current.get("nudge_cadence") or "smart").title())
with c3:
    st.metric("Quiet hours", f"{(current.get('quiet_start') or '22:00:00')[:5]} → {(current.get('quiet_end') or '07:00:00')[:5]}")

st.divider()

# ===== Form =====
with st.form("prefs_form", clear_on_submit=False):
    st.subheader("Notifications")
    channel_opts = ["telegram", "inapp", "email", "none"]
    channel_opts, channel_idx = idx_or_prepend(channel_opts, current.get("nudge_channel"), default_first="telegram")
    nudge_channel = st.selectbox("Primary nudge channel", options=channel_opts, index=channel_idx)

    cadence_opts = ["smart", "frequent", "sparse", "off"]
    cadence_opts, cadence_idx = idx_or_prepend(cadence_opts, current.get("nudge_cadence"), default_first="smart")
    nudge_cadence = st.selectbox("Nudge cadence", options=cadence_opts, index=cadence_idx)

    tone_opts = ["gentle", "direct", "cheerful", "clinical"]
    tone_opts, tone_idx = idx_or_prepend(tone_opts, current.get("nudge_tone"), default_first="gentle")
    nudge_tone = st.selectbox("Tone", options=tone_opts, index=tone_idx)

    st.divider()
    st.subheader("Quiet hours")
    q1, q2 = st.columns(2)
    with q1:
        quiet_start = st.time_input("Quiet start", value=safe_time(current.get("quiet_start"), "22:00:00"))
    with q2:
        quiet_end = st.time_input("Quiet end", value=safe_time(current.get("quiet_end"), "07:00:00"))

    st.divider()
    st.subheader("Health goals")
    g1, g2 = st.columns(2)
    with g1:
        daily_step_goal = st.number_input("Daily steps", min_value=0, step=500,
                                          value=int(current.get("daily_step_goal", 8000) or 8000))
        daily_water_ml = st.number_input("Daily water (ml)", min_value=0, step=100,
                                         value=int(current.get("daily_water_ml", 2000) or 2000))
        protein_target_g = st.number_input("Protein target (g)", min_value=0, step=5,
                                           value=int(current.get("protein_target_g", 80) or 80))
    with g2:
        daily_calorie_goal = st.number_input("Daily calories (kcal)", min_value=0, step=50,
                                             value=int(current.get("daily_calorie_goal", 2000) or 2000))
        sleep_goal_min = st.number_input("Sleep goal (minutes)", min_value=0, step=15,
                                         value=int(current.get("sleep_goal_min", 420) or 420))

    st.divider()
    st.subheader("Reminders")
    r1, r2, r3 = st.columns(3)
    with r1:
        remind_hydration = st.toggle("Hydration", value=bool(current.get("remind_hydration", True)))
    with r2:
        remind_steps = st.toggle("Steps", value=bool(current.get("remind_steps", True)))
    with r3:
        remind_sleep = st.toggle("Sleep", value=bool(current.get("remind_sleep", False)))

    st.divider()
    st.subheader("Integrations")

    # Use zoneinfo list of available time zones
    tz_options = sorted(zoneinfo.available_timezones())

    # Pick current value or default to America/New_York
    current_tz = current.get("tz") or "America/New_York"
    if current_tz not in tz_options:
        tz_options = [current_tz] + tz_options  # ensure it's in the list

    tz = st.selectbox("Time zone (IANA)", options=tz_options, index=tz_options.index(current_tz))

    telegram_chat_id = st.text_input("Telegram chat ID (text)", value=current.get("telegram_chat_id") or "")
    calendar_ics_url = st.text_input("Calendar ICS URL", value=current.get("calendar_ics_url") or "")

    submitted = st.form_submit_button("Save Preferences", use_container_width=True)

if submitted:
    payload = {
        "uid": uid,
        "nudge_channel": nudge_channel,
        "quiet_start": quiet_start.strftime("%H:%M:%S"),
        "quiet_end": quiet_end.strftime("%H:%M:%S"),
        "nudge_cadence": nudge_cadence,
        "nudge_tone": nudge_tone,
        "goals": {
            "daily_step_goal": int(daily_step_goal),
            "daily_water_ml": int(daily_water_ml),
            "daily_calorie_goal": int(daily_calorie_goal),
            "sleep_goal_min": int(sleep_goal_min),
            "protein_target_g": int(protein_target_g),
        },
        # denormalized columns (keep for simple queries)
        "daily_step_goal": int(daily_step_goal),
        "daily_water_ml": int(daily_water_ml),
        "daily_calorie_goal": int(daily_calorie_goal),
        "sleep_goal_min": int(sleep_goal_min),
        "protein_target_g": int(protein_target_g),
        "remind_hydration": bool(remind_hydration),
        "remind_steps": bool(remind_steps),
        "remind_sleep": bool(remind_sleep),
        "tz": (tz.strip() if tz else "America/New_York"),
        "telegram_chat_id": (telegram_chat_id.strip() or None),
        "calendar_ics_url": (calendar_ics_url.strip() or None),
        "updated_at": datetime.now(tzmod.utc).isoformat(),
    }
    try:
        ensure_hw_user(uid)
        upsert_prefs(payload)
        st.success("Preferences saved ✅")
    except APIError as e:
        if "PGRST303" in str(e) or "JWT expired" in str(e):
            st.error("Your session expired. Please sign in again.")
            on_sign_out()
            st.switch_page("pages/02_Sign_In.py")
            st.stop()
        else:
            st.error(f"Failed to save preferences: {e}")
    except Exception as e:
        st.error(f"Failed to save preferences: {e}")
