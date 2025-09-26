# pages/03_My_Profile.py
import streamlit as st
import pytz
from postgrest.exceptions import APIError

from nav import apply_global_ui, top_nav

apply_global_ui()
from supa import get_sb

st.set_page_config(page_title="My Profile - Health Whisperer",
                   layout="wide",
                   initial_sidebar_state="collapsed")
st.markdown("""<style>section[data-testid="stSidebarNav"]{display:none;}</style>""", unsafe_allow_html=True)

# ---- Auth / Nav ----
def on_sign_out(sb=None):
    try:
        if sb: sb.auth.sign_out()
    finally:
        st.session_state.pop("sb_session", None)

is_authed = "sb_session" in st.session_state
top_nav(is_authed, on_sign_out, current="My Profile")
if not is_authed:
    st.warning("Please sign in first.")
    st.switch_page("pages/02_Sign_In.py")
    st.stop()

uid = st.session_state["sb_session"]["user_id"]
email = st.session_state["sb_session"].get("email", "")
access_token = st.session_state["sb_session"]["access_token"]
sb = get_sb(access_token)  # <-- authed client

# ---- Retry helper ----
def exec_with_retry(req, tries: int = 3, base_delay: float = 0.4):
    for i in range(tries):
        try:
            return req.execute()
        except APIError as e:
            if "PGRST303" in str(e) or "JWT expired" in str(e):
                st.error("Your session expired. Please sign in again.")
                on_sign_out(sb); st.switch_page("pages/02_Sign_In.py"); st.stop()
            raise
        except Exception:
            import time; time.sleep(base_delay * (i + 1))
    return req.execute()

def ensure_hw_user(uid_: str):
    try:
        exec_with_retry(sb.table("hw_users").upsert({"uid": uid_}))
    except Exception:
        pass

st.title("My Profile")

# ---- Profile block ----
try:
    res = exec_with_retry(sb.table("profiles").select("*").eq("id", uid).maybe_single())
    row = res.data or None
except Exception:
    row = None

if not row:
    try:
        exec_with_retry(sb.table("profiles").insert({"id": uid, "email": email, "full_name": ""}))
        reread = exec_with_retry(sb.table("profiles").select("*").eq("id", uid).maybe_single())
        row = reread.data or {}
    except Exception as e:
        st.error(f"Couldn't create your profile record automatically: {e}")
        st.stop()
else:
    row = row or {}

with st.form("profile"):
    full_name = st.text_input("Full name", value=row.get("full_name", ""))
    age = st.number_input("Age", min_value=0, max_value=120, value=int(row.get("age") or 0))
    gender_opts = ["Prefer not to say", "Female", "Male", "Non-binary", "Other"]
    gval = row.get("gender", "Prefer not to say")
    gender = st.selectbox("Gender", gender_opts, index=gender_opts.index(gval) if gval in gender_opts else 0)
    height_cm = st.number_input("Height (cm)", min_value=0.0, max_value=300.0, value=float(row.get("height_cm") or 0.0))
    weight_kg = st.number_input("Weight (kg)", min_value=0.0, max_value=500.0, value=float(row.get("weight_kg") or 0.0))
    act_opts = ["Sedentary", "Lightly active", "Moderately active", "Very active", "Athlete"]
    aval = row.get("activity_level", "Sedentary")
    activity_level = st.selectbox("Activity level", act_opts, index=act_opts.index(aval) if aval in act_opts else 0)
    goals_txt = st.text_area("Goals (free text)", value=row.get("goals", ""))
    conditions = st.text_area("Conditions (optional)", value=row.get("conditions", ""))
    medications = st.text_area("Medications/Supplements (optional)", value=row.get("medications", ""))

    tz_list = pytz.all_timezones
    tz_value = row.get("timezone") or ("America/New_York" if "America/New_York" in tz_list else tz_list[0])
    profile_timezone = st.selectbox("Profile timezone (legacy, optional)", tz_list, index=tz_list.index(tz_value))

    save = st.form_submit_button("Save profile", type="primary")

if save:
    payload = {
        "id": uid, "email": email, "full_name": full_name,
        "age": int(age), "gender": gender,
        "height_cm": float(height_cm), "weight_kg": float(weight_kg),
        "activity_level": activity_level, "goals": goals_txt,
        "conditions": conditions, "medications": medications,
        "timezone": profile_timezone,
    }
    try:
        exec_with_retry(sb.table("profiles").upsert(payload))
        st.success("Profile saved!")
    except Exception as e:
        st.error(f"Could not save profile: {e}")

st.divider()
st.subheader("Preferences for Nudges & Scheduling")

# Ensure FK target exists when working with preferences
ensure_hw_user(uid)

# Read prefs safely
try:
    pref_res = exec_with_retry(sb.table("hw_preferences").select("*").eq("uid", uid).maybe_single())
    pref = pref_res.data or {}
except Exception:
    pref = {}

# Ensure a prefs row exists so form always has data
if not pref:
    try:
        exec_with_retry(sb.table("hw_preferences").insert({"uid": uid, "tz": "America/New_York"}))
        pref_res = exec_with_retry(sb.table("hw_preferences").select("*").eq("uid", uid).maybe_single())
        pref = pref_res.data or {"uid": uid, "tz": "America/New_York"}
    except Exception:
        pref = {"uid": uid, "tz": "America/New_York"}

zones = [
    "America/New_York","America/Chicago","America/Denver","America/Los_Angeles",
    "Europe/London","Europe/Paris","Asia/Kolkata","UTC",
]
cur_tz = pref.get("tz") or "America/New_York"
tz_index = zones.index(cur_tz) if cur_tz in zones else zones.index("America/New_York")

with st.form("prefs"):
    tz = st.selectbox("Your time zone (used for 'today' + pacing)", zones, index=tz_index)

    c1, c2, c3 = st.columns(3)
    daily_calorie_goal = c1.number_input("Daily calories goal", min_value=1000, max_value=5000, value=int(pref.get("daily_calorie_goal") or 2000), step=50)
    daily_step_goal = c2.number_input("Daily steps goal", min_value=1000, max_value=40000, value=int(pref.get("daily_step_goal") or 8000), step=500)
    daily_water_ml = c3.number_input("Daily water (ml)", min_value=500, max_value=6000, value=int(pref.get("daily_water_ml") or 2000), step=100)

    c4, c5 = st.columns(2)
    protein_target_g = c4.number_input("Protein target (g)", min_value=20, max_value=300, value=int(pref.get("protein_target_g") or 80), step=5)
    sleep_goal_min = c5.number_input("Sleep goal (minutes)", min_value=240, max_value=720, value=int(pref.get("sleep_goal_min") or 420), step=15)

    st.caption("Telegram: message the bot to get your chat ID (paste it below).")
    telegram_chat_id = st.text_input("Telegram chat ID", value=pref.get("telegram_chat_id") or "", help="The worker will send nudges here.")

    channel_choices = ["telegram", "inapp"]
    cadence_choices = ["smart", "hourly", "3_per_day"]
    nudge_channel = st.selectbox("Nudge channel", channel_choices,
        index=channel_choices.index(pref.get("nudge_channel", "telegram")) if pref.get("nudge_channel", "telegram") in channel_choices else 0)
    nudge_cadence = st.selectbox("Cadence", cadence_choices,
        index=cadence_choices.index(pref.get("nudge_cadence", "smart")) if pref.get("nudge_cadence", "smart") in cadence_choices else 0)

    save_prefs = st.form_submit_button("Save preferences", type="primary")

if save_prefs:
    try:
        exec_with_retry(sb.table("hw_preferences").upsert({
            "uid": uid, "tz": tz,
            "daily_calorie_goal": int(daily_calorie_goal),
            "daily_step_goal": int(daily_step_goal),
            "daily_water_ml": int(daily_water_ml),
            "protein_target_g": int(protein_target_g),
            "sleep_goal_min": int(sleep_goal_min),
            "telegram_chat_id": (telegram_chat_id or None),
            "nudge_channel": nudge_channel,
            "nudge_cadence": nudge_cadence,
        }))
        st.success("Preferences saved.")
    except Exception as e:
        st.error(f"Could not save preferences: {e}")

st.info("Heads-up: the worker uses your timezone to compute local-day pacing and sends Telegram nudges if a chat ID is set.")
