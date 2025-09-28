# pages/06a_Log_Physical.py
import time
from datetime import datetime, timezone, date
from zoneinfo import ZoneInfo
import streamlit as st
from httpx import ReadError
from postgrest.exceptions import APIError

from supa import get_sb
from nav import apply_global_ui, top_nav

apply_global_ui()
st.set_page_config(page_title="Log Physical - Health Whisperer",
                   layout="wide",
                   initial_sidebar_state="collapsed")
st.markdown("<style>section[data-testid='stSidebarNav']{display:none;}</style>", unsafe_allow_html=True)

# ---- Auth / Nav ----
def on_sign_out(sb=None):
    try:
        if sb: sb.auth.sign_out()
    finally:
        st.session_state.pop("sb_session", None)

is_authed = "sb_session" in st.session_state
top_nav(is_authed, on_sign_out, current="Log Physical")
if not is_authed:
    st.warning("Please sign in first.")
    st.switch_page("pages/02_Sign_In.py")
    st.stop()

uid = st.session_state["sb_session"]["user_id"]
access_token = st.session_state["sb_session"]["access_token"]
sb = get_sb(access_token)  # authed client (RLS)

# ---- Retry helper ----
def exec_with_retry(req, tries: int = 3, base_delay: float = 0.4):
    for i in range(tries):
        try:
            return req.execute()
        except APIError as e:
            if "PGRST303" in str(e) or "JWT expired" in str(e):
                st.error("Your session expired. Please sign in again.")
                on_sign_out(sb)
                st.switch_page("pages/02_Sign_In.py")
                st.stop()
            raise
        except Exception as e:
            msg = str(e)
            if "10035" in msg or isinstance(e, ReadError):
                time.sleep(base_delay * (i + 1)); continue
            raise
    return req.execute()

# ---- Helpers ----
def _user_tz(uid: str) -> ZoneInfo:
    try:
        r = exec_with_retry(sb.table("hw_preferences").select("tz").eq("uid", uid).maybe_single())
        tz = (r.data or {}).get("tz") or "America/New_York"
    except Exception:
        tz = "America/New_York"
    try:
        return ZoneInfo(tz)
    except Exception:
        return ZoneInfo("America/New_York")

def _today(uid: str) -> date:
    return datetime.now(timezone.utc).astimezone(_user_tz(uid)).date()

def _get_today_manual(uid: str) -> dict:
    t = _today(uid).isoformat()
    req = (sb.table("hw_metrics").select("*")
           .eq("uid", uid).eq("source", "manual").eq("log_date", t)
           .limit(1))
    r = exec_with_retry(req)
    return (r.data[0] if r.data else {}) or {}

# ---- UI ----
today_row = _get_today_manual(uid)

st.title("🏃 Log Physical Health")
st.caption("Steps, sleep, heart rate, pain & energy — saved to today’s manual metrics row.")

c1, c2, c3 = st.columns(3)
steps = c1.number_input("Steps today", min_value=0, value=int(today_row.get("steps") or 0), step=100)
sleep_min = c2.number_input("Sleep last night (min)", min_value=0, max_value=1000,
                            value=int(today_row.get("sleep_minutes") or 0), step=10)
heart_rate = c3.number_input("Heart rate (bpm)", min_value=30, max_value=220,
                             value=int(today_row.get("heart_rate") or 70), step=1)

c4, c5 = st.columns(2)
pain = c4.slider("Pain (1–5)", 1, 5, int(today_row.get("pain_level") or 1))
energy = c5.slider("Energy (1–5)", 1, 5, int(today_row.get("energy_level") or 3))

note = st.text_area("Short note (optional)", value=today_row.get("notes") or "",
                    placeholder="e.g., Morning run; sore calves; long sit today")

if st.button("💾 Save Physical"):
    now_u = datetime.now(timezone.utc)
    payload = {
        "uid": uid,
        "source": "manual",
        "log_date": _today(uid).isoformat(),
        "ts": now_u.isoformat(),                 # <-- CRITICAL for worker/ bot visibility
        "steps": int(steps or 0),
        "sleep_minutes": int(sleep_min or 0),
        "heart_rate": int(heart_rate or 0),
        "pain_level": int(pain or 0),
        "energy_level": int(energy or 0),
        "notes": note or None,
    }
    try:
        # upsert keeps one row per (uid, source, log_date) but refreshes ts and values
        exec_with_retry(sb.table("hw_metrics").upsert(payload, on_conflict="uid,source,log_date"))
        st.success("Saved to today’s manual metrics.")
    except Exception:
        exec_with_retry(sb.table("hw_metrics").insert(payload))
        st.info("Saved as a new manual metrics row for today.")

    # Fire a lightweight event so the nudge worker can react immediately
    try:
        exec_with_retry(sb.table("hw_events").insert({
            "uid": uid,
            "kind": "metrics_saved",
            "payload": {"source": "manual", "steps": int(steps or 0)}
        }))
    except Exception:
        pass

    today_row = _get_today_manual(uid)
