# pages/06c_Log_Nutrition.py
import time
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo

import streamlit as st
from httpx import ReadError
from supabase import create_client

from nav import top_nav
# We’ll reuse your existing nutrition module. In Step 2 we’ll swap it to OpenAI.
from services.nutrition_llm import estimate_meal, save_meal  # to be migrated to OpenAI next

# ---------- Page config ----------
st.set_page_config(page_title="Log Nutrition - Health Whisperer",
                   layout="wide",
                   initial_sidebar_state="collapsed")
st.markdown("""
<style>
  section[data-testid='stSidebarNav']{display:none;}
  .soft { background: linear-gradient(180deg, rgba(250,250,250,.95), rgba(245,245,245,.9)); border:1px solid rgba(0,0,0,.06); border-radius: 12px; padding: 12px 14px; }
</style>
""", unsafe_allow_html=True)

# ---------- Retry helper ----------
def exec_with_retry(req, tries: int = 3, base_delay: float = 0.4):
    for i in range(tries):
        try:
            return req.execute()
        except Exception as e:
            msg = str(e)
            if "10035" in msg or isinstance(e, ReadError):
                time.sleep(base_delay * (i + 1))
                continue
            raise
    return req.execute()

# ---------- Supabase client ----------
@st.cache_resource
def get_sb():
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    return create_client(url, key)
sb = get_sb()

# ---------- Navbar / Auth ----------
def on_sign_out():
    sb.auth.sign_out()
    st.session_state.pop("sb_session", None)

is_authed = "sb_session" in st.session_state
top_nav(is_authed, on_sign_out, current="Log Nutrition")
if not is_authed:
    st.warning("Please sign in first.")
    st.switch_page("pages/02_Sign_In.py")
    st.stop()

uid = st.session_state["sb_session"]["user_id"]

# ---------- Helpers ----------
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

def _to_utc_from_local_time(local_time, uid: str) -> datetime:
    tz = _user_tz(uid)
    base = datetime.combine(_today(uid), datetime.min.time(), tzinfo=tz)
    # Put given time into "today" at local tz
    t = base.replace(hour=local_time.hour, minute=local_time.minute)
    return t.astimezone(timezone.utc)

def _load_today_meals(uid: str) -> list[dict]:
    tz = _user_tz(uid)
    start_l = datetime.combine(_today(uid), datetime.min.time(), tzinfo=tz)
    end_l = start_l + timedelta(days=1)
    r = (sb.table("hw_meals").select("*")
         .eq("uid", uid)
         .gte("ts", start_l.astimezone(timezone.utc).isoformat())
         .lt("ts", end_l.astimezone(timezone.utc).isoformat())
         .order("ts", desc=True).execute())
    return r.data or []

# ---------- UI ----------
st.title("🍽️ Log Nutrition")
st.caption("Enter free-text meals. Use “Parse with AI” to estimate portions & macros, or save raw quickly.")

bc, lc = st.columns(2)
with bc:
    b_txt = st.text_area("Breakfast (free text)", placeholder="e.g., 2 eggs, 2 toast with butter, coffee with milk")
    b_time = st.time_input("Breakfast time", value=None, step=300)
with lc:
    l_txt = st.text_area("Lunch (free text)", placeholder="e.g., chicken wrap, yogurt")
    l_time = st.time_input("Lunch time", value=None, step=300)

dc, sc = st.columns(2)
with dc:
    d_txt = st.text_area("Dinner (free text)")
    d_time = st.time_input("Dinner time", value=None, step=300)
with sc:
    s_txt = st.text_area("Snacks (free text)")
    s_time = st.time_input("Snacks time", value=None, step=300)

st.divider()
c1, c2 = st.columns([1,1])
save_raw = c1.button("💾 Save Raw (no AI)")
parse_ai = c2.button("✨ Parse with AI (estimate & save)")

def _save_raw_block(txt: str, when, meal_type: str):
    if not txt: return
    when_u = _to_utc_from_local_time(when, uid) if when else datetime.now(timezone.utc)
    payload = {"uid": uid, "ts": when_u.isoformat(), "meal_type": meal_type, "items": txt, "calories": None}
    exec_with_retry(sb.table("hw_meals").insert(payload))

def _ai_block(txt: str, when, meal_type: str):
    if not txt: return
    when_u = _to_utc_from_local_time(when, uid) if when else datetime.now(timezone.utc)
    parsed = estimate_meal(txt)  # NOTE: Uses current module; Step 2 will switch to OpenAI
    save_meal(uid, raw_text=txt, parsed=parsed, when_utc=when_u, meal_type=meal_type)

if save_raw:
    for text, when, mt in [(b_txt, b_time, "breakfast"), (l_txt, l_time, "lunch"),
                           (d_txt, d_time, "dinner"), (s_txt, s_time, "snacks")]:
        _save_raw_block(text, when, mt)
    st.success("Saved raw meals for today.")
elif parse_ai:
    for text, when, mt in [(b_txt, b_time, "breakfast"), (l_txt, l_time, "lunch"),
                           (d_txt, d_time, "dinner"), (s_txt, s_time, "snacks")]:
        _ai_block(text, when, mt)
    st.success("Parsed, estimated & saved meals for today.")

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
