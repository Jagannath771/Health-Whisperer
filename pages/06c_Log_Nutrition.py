# pages/06c_Log_Nutrition.py
import time
import os
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo
import dotenv
dotenv.load_dotenv()

import streamlit as st
from httpx import ReadError

from nav import top_nav
from services.nutrition_llm import estimate_meal, save_meal
from supa import get_sb

st.set_page_config(page_title="Log Nutrition - Health Whisperer",
                   layout="wide",
                   initial_sidebar_state="collapsed")

st.markdown("""
<style>
  section[data-testid='stSidebarNav']{display:none;}
  .soft { background: linear-gradient(180deg, rgba(250,250,250,.95), rgba(245,245,245,.9));
          border:1px solid rgba(0,0,0,.06); border-radius: 12px; padding: 12px 14px; }
</style>
""", unsafe_allow_html=True)

def exec_with_retry(req, tries: int = 3, base_delay: float = 0.4):
    for i in range(tries):
        try:
            return req.execute()
        except Exception as e:
            if "10035" in str(e) or isinstance(e, ReadError):
                time.sleep(base_delay * (i + 1))
                continue
            raise
    return req.execute()

def on_sign_out():
    get_sb().auth.sign_out()
    st.session_state.pop("sb_session", None)

is_authed = "sb_session" in st.session_state
top_nav(is_authed, on_sign_out, current="Log Nutrition")
if not is_authed:
    st.warning("Please sign in first.")
    st.switch_page("pages/02_Sign_In.py")
    st.stop()

uid = st.session_state["sb_session"]["user_id"]
access_token = st.session_state["sb_session"]["access_token"]

# Build authed client (critical for RLS)
sb = get_sb(access_token)

# DEBUG: prove what PostgREST sees (comment out later)
try:
    who = sb.rpc("whoami").execute()
    st.caption(f"whoami() reports: {who.data}")
except Exception as e:
    st.caption(f"whoami() failed: {e}")

def _user_tz(uid_: str) -> ZoneInfo:
    try:
        r = exec_with_retry(sb.table("hw_preferences").select("tz").eq("uid", uid_).maybe_single())
        tz = (r.data or {}).get("tz") or "America/New_York"
    except Exception:
        tz = "America/New_York"
    try:
        return ZoneInfo(tz)
    except Exception:
        return ZoneInfo("America/New_York")

def _today(uid_: str) -> date:
    return datetime.now(timezone.utc).astimezone(_user_tz(uid_)).date()

def _to_utc_from_local_time(local_time, uid_: str) -> datetime:
    tz = _user_tz(uid_)
    base = datetime.combine(_today(uid_), datetime.min.time(), tzinfo=tz)
    t = base.replace(hour=local_time.hour, minute=local_time.minute)
    return t.astimezone(timezone.utc)

def _load_today_meals(uid_: str) -> list[dict]:
    tz = _user_tz(uid_)
    start_l = datetime.combine(_today(uid_), datetime.min.time(), tzinfo=tz)
    end_l = start_l + timedelta(days=1)
    r = (sb.table("hw_meals").select("*")
         .eq("uid", uid_)
         .gte("ts", start_l.astimezone(timezone.utc).isoformat())
         .lt("ts", end_l.astimezone(timezone.utc).isoformat())
         .order("ts", desc=True).execute())
    return r.data or []

st.title("🍽️ Log Nutrition")
st.caption("Free-text meals. Use “Parse with AI” to estimate macros, or save raw quickly.")

bc, lc = st.columns(2)
with bc:
    b_txt = st.text_area("Breakfast", placeholder="e.g., 2 eggs, 2 toast with butter, coffee with milk")
    b_time = st.time_input("Breakfast time", value=None, step=300)
with lc:
    l_txt = st.text_area("Lunch", placeholder="e.g., chicken wrap, yogurt")
    l_time = st.time_input("Lunch time", value=None, step=300)

dc, sc = st.columns(2)
with dc:
    d_txt = st.text_area("Dinner")
    d_time = st.time_input("Dinner time", value=None, step=300)
with sc:
    s_txt = st.text_area("Snacks")
    s_time = st.time_input("Snacks time", value=None, step=300)

st.divider()
c1, c2 = st.columns([1,1])
save_raw = c1.button("💾 Save Raw (no AI)")
parse_ai = c2.button("✨ Parse with AI (estimate & save)")

def _save_raw_block(txt: str, when, meal_type: str):
    if not txt:
        return
    when_u = _to_utc_from_local_time(when, uid) if when else datetime.now(timezone.utc)
    payload = {
        "uid": uid,  # MUST equal auth.uid()
        "ts": when_u.isoformat(),
        "meal_type": meal_type,  # breakfast/lunch/dinner/snacks
        "items": txt,
        "calories": None
    }
    exec_with_retry(sb.table("hw_meals").insert(payload))

def _ai_block(txt: str, when, meal_type: str):
    if not txt:
        return
    when_u = _to_utc_from_local_time(when, uid) if when else datetime.now(timezone.utc)
    parsed = estimate_meal(txt)
    # run under authed user
    save_meal(uid, raw_text=txt, parsed=parsed, when_utc=when_u, meal_type=meal_type, access_token=access_token)

if save_raw:
    for text, when, mt in [(b_txt, b_time, "breakfast"), (l_txt, l_time, "lunch"),
                           (d_txt, d_time, "dinner"), (s_txt, s_time, "snacks")]:
        _save_raw_block(text, when, mt)
    st.success("Saved raw meals.")
elif parse_ai:
    for text, when, mt in [(b_txt, b_time, "breakfast"), (l_txt, l_time, "lunch"),
                           (d_txt, d_time, "dinner"), (s_txt, s_time, "snacks")]:
        _ai_block(text, when, mt)
    st.success("Parsed & saved meals.")

st.divider()
st.subheader("Today’s meals")
meals = _load_today_meals(uid)
if not meals:
    st.info("No meals today yet.")
else:
    tz = _user_tz(uid)
    for m in meals:
        ts_local = datetime.fromisoformat(m["ts"].replace("Z","+00:00")).astimezone(tz).strftime("%b %d, %Y • %I:%M %p")
        kcal = m.get("calories")
        st.markdown(f"**{m.get('meal_type','?').title()}** · {ts_local} — {f'{int(kcal)} kcal' if kcal else 'kcal unknown'}")
        if m.get("items"):
            st.caption(str(m["items"]))
