# pages/06c_Log_Nutrition.py
import time
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo
import dotenv
dotenv.load_dotenv()

import streamlit as st
from httpx import ReadError

from services.nutrition_llm import estimate_meal, save_meal
from supa import get_sb
from nav import apply_global_ui, top_nav

apply_global_ui()
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
sb = get_sb(access_token)  # authed client for RLS

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

def _load_today_water_events(uid_: str) -> list[dict]:
    tz = _user_tz(uid_)
    start_l = datetime.combine(_today(uid_), datetime.min.time(), tzinfo=tz)
    end_l = start_l + timedelta(days=1)
    r = (sb.table("hw_events").select("*")
         .eq("uid", uid_)
         .eq("kind", "water_logged")
         .gte("ts", start_l.astimezone(timezone.utc).isoformat())
         .lt("ts", end_l.astimezone(timezone.utc).isoformat())
         .order("ts", desc=True).execute())
    return r.data or []

st.title("🍽️ Log Nutrition")
st.caption("Free-text meals. Use “Parse with AI” to estimate macros, or save raw quickly (now vectorized for RAG).")

# ---------------- Meals ----------------
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
    parsed_min = {"items": [], "totals": {}}
    save_meal(uid, raw_text=txt, parsed=parsed_min, when_utc=when_u, meal_type=meal_type, access_token=access_token)

def _ai_block(txt: str, when, meal_type: str):
    if not txt:
        return
    when_u = _to_utc_from_local_time(when, uid) if when else datetime.now(timezone.utc)
    parsed = estimate_meal(txt)
    save_meal(uid, raw_text=txt, parsed=parsed, when_utc=when_u, meal_type=meal_type, access_token=access_token)

if save_raw:
    for text, when, mt in [(b_txt, b_time, "breakfast"), (l_txt, l_time, "lunch"),
                           (d_txt, d_time, "dinner"), (s_txt, s_time, "snacks")]:
        _save_raw_block(text, when, mt)
    st.success("Saved raw meals (vectorized).")
elif parse_ai:
    for text, when, mt in [(b_txt, b_time, "breakfast"), (l_txt, l_time, "lunch"),
                           (d_txt, d_time, "dinner"), (s_txt, s_time, "snacks")]:
        _ai_block(text, when, mt)
    st.success("Parsed & saved meals (vectorized).")

# ---------------- Water ----------------
st.divider()
st.subheader("💧 Log Water Intake")
with st.form("water_form"):
    water_amt = st.number_input("Water (ml)", min_value=50, max_value=2000, step=50, value=250)
    water_time = st.time_input("Time", value=None, step=300)
    submitted_water = st.form_submit_button("Save Water Log")

if submitted_water:
    try:
        when_u = _to_utc_from_local_time(water_time, uid) if water_time else datetime.now(timezone.utc)
        now_iso = when_u.isoformat()
        today_d = _today(uid).isoformat()

        # 1) Read today's manual row
        r = exec_with_retry(
            sb.table("hw_metrics")
              .select("id, water_ml")
              .eq("uid", uid).eq("source","manual").eq("log_date", today_d)
              .limit(1)
        )
        cur = (r.data[0] if r.data else None) or {}
        new_total = int(cur.get("water_ml") or 0) + int(water_amt)

        # 2) Upsert the daily manual row (single row per day by unique (uid,log_date,source))
        payload = {
            "uid": uid,
            "source": "manual",
            "log_date": today_d,
            "ts": now_iso,              # keep the latest update time
            "water_ml": new_total
        }
        exec_with_retry(sb.table("hw_metrics").upsert(payload, on_conflict="uid,log_date,source"))

        # 3) Append a timestamped event so we can list exact times
        exec_with_retry(sb.table("hw_events").insert({
            "uid": uid,
            "kind": "water_logged",
            "ts": now_iso,
            "processed": False,
            "payload": {"water_ml": int(water_amt)}
        }))

        st.success(f"Added {int(water_amt)} ml (today total: {new_total} ml)")
        st.rerun()
    except Exception as e:
        st.error(f"Could not save water log: {e}")

# ---------------- Meals list ----------------
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
        st.markdown(f"**{m.get('meal_type','?').title()}** · {ts_local} — {f'{int(kcal)} kcal' if kcal is not None else 'kcal unknown'}")
        if m.get("items"): st.caption(str(m["items"]))
        if m.get("blurb"): st.caption(f"⃟ {m['blurb']}")

# ---------------- Water list ----------------
st.divider()
st.subheader("Today’s water intake")
events = _load_today_water_events(uid)
if not events:
    st.info("No water logs today yet.")
else:
    tz = _user_tz(uid)
    total_ml = sum(int((e.get("payload") or {}).get("water_ml") or 0) for e in events)
    st.metric("Total Water", f"{total_ml} ml")
    for e in events:
        ts_local = datetime.fromisoformat(e["ts"].replace("Z","+00:00")).astimezone(tz).strftime("%b %d, %Y • %I:%M %p")
        amt = int((e.get("payload") or {}).get("water_ml") or 0)
        st.markdown(f"- {amt} ml at {ts_local}")
