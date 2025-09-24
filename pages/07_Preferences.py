# pages/07_Preferences.py
import time
from datetime import datetime, timezone
from datetime import datetime as dt
import streamlit as st
from postgrest.exceptions import APIError

from nav import top_nav
from supa import get_sb  # same helper used by Log Nutrition

st.set_page_config(page_title="Preferences - Health Whisperer",
                   layout="centered",
                   initial_sidebar_state="collapsed")

# ---------------------------
# Small CSS (optional)
# ---------------------------
st.markdown("""
<style>
  section[data-testid='stSidebarNav']{display:none;}
  .soft { background: linear-gradient(180deg, rgba(250,250,250,.95), rgba(245,245,245,.9));
          border:1px solid rgba(0,0,0,.06); border-radius: 12px; padding: 12px 14px; }
</style>
""", unsafe_allow_html=True)

# ---------------------------
# Navbar & Auth gate
# ---------------------------
def on_sign_out():
    get_sb().auth.sign_out()     # sign out the SDK session
    st.session_state.pop("sb_session", None)

is_authed = "sb_session" in st.session_state
top_nav(is_authed, on_sign_out, current="Preferences")

if not is_authed:
    st.warning("Please sign in first.")
    st.switch_page("pages/02_Sign_In.py")
    st.stop()

uid = st.session_state["sb_session"]["user_id"]
access_token = st.session_state["sb_session"]["access_token"]

# Build authed client (critical for RLS), mirroring Log Nutrition
sb = get_sb(access_token)

# ---------------------------
# Helpers
# ---------------------------
def exec_with_retry(req, tries: int = 3, base_delay: float = 0.4):
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

def load_prefs(uid_: str) -> dict:
    r = exec_with_retry(sb.table("hw_preferences").select("*").eq("uid", uid_).maybe_single())
    row = r.data or None
    if row:
        return row
    # Defaults per schema
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

def idx_or_prepend(options: list[str], value: str, default_first: str | None = None) -> tuple[list[str], int]:
    """
    If value is in options -> return (options, index).
    If not, prepend the value to options so selectbox can show it, return index 0.
    If value is falsy -> use default_first if provided (and ensure it’s present).
    """
    if value and value in options:
        return options, options.index(value)
    if not value and default_first:
        if default_first not in options:
            options = [default_first] + options
        return options, options.index(default_first)
    # Prepend unknown legacy value (e.g., "coach")
    if value:
        options = [value] + [opt for opt in options if opt != value]
        return options, 0
    return options, 0

def safe_time(val: str, fallback: str) -> dt.time:
    try:
        return dt.strptime(val or fallback, "%H:%M:%S").time()
    except Exception:
        return dt.strptime(fallback, "%H:%M:%S").time()

# ---------------------------
# UI
# ---------------------------
st.title("⚙️ Preferences")

# Optional: quick whoami sanity check
try:
    who = exec_with_retry(sb.rpc("whoami"))
    st.caption(f"whoami() reports: {who.data}")
except Exception as e:
    st.caption(f"whoami() failed: {e}")

# Ensure FK target exists before touching prefs (matches FK in schema)
ensure_hw_user(uid)

current = load_prefs(uid)

with st.form("prefs_form", clear_on_submit=False):
    st.subheader("Notifications")

    # Channels
    channel_opts = ["telegram", "inapp", "email", "none"]
    channel_val = current.get("nudge_channel", "telegram")
    channel_opts, channel_idx = idx_or_prepend(channel_opts, channel_val, default_first="telegram")
    nudge_channel = st.selectbox("Primary nudge channel", options=channel_opts, index=channel_idx)

    # Cadence
    cadence_opts = ["smart", "frequent", "sparse", "off"]
    cadence_val = current.get("nudge_cadence", "smart")
    cadence_opts, cadence_idx = idx_or_prepend(cadence_opts, cadence_val, default_first="smart")
    nudge_cadence = st.selectbox("Nudge cadence", options=cadence_opts, index=cadence_idx)

    # Tone — include legacy/custom values gracefully (e.g., "coach")
    tone_opts = ["gentle", "direct", "cheerful", "clinical"]
    tone_val = current.get("nudge_tone", "gentle")
    tone_opts, tone_idx = idx_or_prepend(tone_opts, tone_val, default_first="gentle")
    nudge_tone = st.selectbox("Tone", options=tone_opts, index=tone_idx)

    st.divider()
    st.subheader("Quiet Hours")
    col_q1, col_q2 = st.columns(2)
    with col_q1:
        quiet_start = st.time_input("Quiet start", value=safe_time(current.get("quiet_start"), "22:00:00"))
    with col_q2:
        quiet_end = st.time_input("Quiet end", value=safe_time(current.get("quiet_end"), "07:00:00"))

    st.divider()
    st.subheader("Health Goals")
    col1, col2 = st.columns(2)
    with col1:
        daily_step_goal = st.number_input("Daily step goal", min_value=0, step=500,
                                          value=int(current.get("daily_step_goal", 8000) or 8000))
        daily_water_ml = st.number_input("Daily water (ml)", min_value=0, step=100,
                                         value=int(current.get("daily_water_ml", 2000) or 2000))
        protein_target_g = st.number_input("Protein target (g)", min_value=0, step=5,
                                           value=int(current.get("protein_target_g", 80) or 80))
    with col2:
        daily_calorie_goal = st.number_input("Daily calories (kcal)", min_value=0, step=50,
                                             value=int(current.get("daily_calorie_goal", 2000) or 2000))
        sleep_goal_min = st.number_input("Sleep goal (minutes)", min_value=0, step=15,
                                         value=int(current.get("sleep_goal_min", 420) or 420))

    st.divider()
    st.subheader("Reminders")
    col_r1, col_r2, col_r3 = st.columns(3)
    with col_r1:
        remind_hydration = st.toggle("Hydration", value=bool(current.get("remind_hydration", True)))
    with col_r2:
        remind_steps = st.toggle("Steps", value=bool(current.get("remind_steps", True)))
    with col_r3:
        remind_sleep = st.toggle("Sleep", value=bool(current.get("remind_sleep", False)))

    st.divider()
    st.subheader("Integrations")
    tz = st.text_input("Time zone (IANA)", value=current.get("tz", "America/New_York") or "America/New_York")
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
        # denormalized columns for simple queries
        "daily_step_goal": int(daily_step_goal),
        "daily_water_ml": int(daily_water_ml),
        "daily_calorie_goal": int(daily_calorie_goal),
        "sleep_goal_min": int(sleep_goal_min),
        "protein_target_g": int(protein_target_g),
        "remind_hydration": bool(remind_hydration),
        "remind_steps": bool(remind_steps),
        "remind_sleep": bool(remind_sleep),
        "tz": tz.strip() or "America/New_York",
        "telegram_chat_id": telegram_chat_id.strip() or None,
        "calendar_ics_url": calendar_ics_url.strip() or None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
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

with st.expander("Debug (session)"):
    ss = st.session_state.get("sb_session", {})
    st.json({
        "uid": uid,
        "has_access_token": bool(ss.get("access_token")),
        "has_refresh_token": bool(ss.get("refresh_token")),
        "current_nudge_tone": current.get("nudge_tone"),
        "current_nudge_channel": current.get("nudge_channel"),
        "current_nudge_cadence": current.get("nudge_cadence"),
    })
