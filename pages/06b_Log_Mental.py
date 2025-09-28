# pages/06b_Log_Mental.py
import time, json
from datetime import datetime, timezone, date
from zoneinfo import ZoneInfo
import streamlit as st
from httpx import ReadError
from postgrest.exceptions import APIError

from supa import get_sb
from services.llm_openai import embed_text
from nav import apply_global_ui, top_nav

apply_global_ui()
st.set_page_config(page_title="Log Mental - Health Whisperer",
                   layout="wide",
                   initial_sidebar_state="collapsed")
st.markdown("""
<style>
  section[data-testid='stSidebarNav']{display:none;}
  .soft { background: linear-gradient(180deg, rgba(250,250,250,.95), rgba(245,245,245,.9)); border:1px solid rgba(0,0,0,.06); border-radius: 12px; padding: 12px 14px; }
</style>
""", unsafe_allow_html=True)

def on_sign_out(sb=None):
    try:
        if sb: sb.auth.sign_out()
    finally:
        st.session_state.pop("sb_session", None)

is_authed = "sb_session" in st.session_state
top_nav(is_authed, on_sign_out, current="Log Mental")
if not is_authed:
    st.warning("Please sign in first.")
    st.switch_page("pages/02_Sign_In.py")
    st.stop()

uid = st.session_state["sb_session"]["user_id"]
access_token = st.session_state["sb_session"]["access_token"]
sb = get_sb(access_token)

def exec_with_retry(req, tries: int = 3, base_delay: float = 0.4):
    for i in range(tries):
        try:
            return req.execute()
        except APIError as e:
            if "PGRST303" in str(e) or "JWT expired" in str(e):
                st.error("Your session expired. Please sign in again.")
                on_sign_out(sb)
                st.switch_page("pages/02_Sign_In.py"); st.stop()
            raise
        except Exception as e:
            if "10035" in str(e) or isinstance(e, ReadError):
                time.sleep(base_delay * (i + 1)); continue
            raise
    return req.execute()

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
           .eq("uid", uid).eq("source", "manual").eq("log_date", t).limit(1))
    r = exec_with_retry(req)
    return (r.data[0] if r.data else {}) or {}

today_row = _get_today_manual(uid)

st.title("🧠 Log Mental Well-being")
st.caption("Mood, stress, anxiety, focus & quick journaling — saved to today’s manual metrics, plus optional journal table for RAG.")

c1, c2, c3, c4 = st.columns(4)
mood   = c1.slider("Mood (1–5)",    1, 5, int(today_row.get("mood") or 3))
stress = c2.slider("Stress (1–5)",  1, 5, int(today_row.get("stress_level") or 3))
anx    = c3.slider("Anxiety (1–5)", 1, 5, int(today_row.get("anxiety_level") or 2))
focus  = c4.slider("Focus (1–5)",   1, 5, int(today_row.get("focus_level") or 3))

cbt_tag = st.selectbox("Quick CBT tag (optional)", ["", "reframe", "gratitude", "exposure", "journaling", "mindfulness"])
journal = st.text_area("Journal", value=today_row.get("journal") or "", height=180,
                       placeholder="Free-write—what’s on your mind?")

if st.button("💾 Save Mental"):
    now_u = datetime.now(timezone.utc)
    payload = {
        "uid": uid,
        "source": "manual",
        "log_date": _today(uid).isoformat(),
        "ts": now_u.isoformat(),              # <-- CRITICAL for worker/bot visibility
        "mood": int(mood),
        "stress_level": int(stress),
        "anxiety_level": int(anx),
        "focus_level": int(focus),
        "journal": journal or None,
        "journal_tag": (cbt_tag or None),
    }
    try:
        exec_with_retry(sb.table("hw_metrics").upsert(payload, on_conflict="uid,source,log_date"))
    except Exception:
        exec_with_retry(sb.table("hw_metrics").insert(payload))

    # Also log optional journal to vector table for RAG
    try:
        emb = embed_text(journal) if (journal and journal.strip()) else None
        exec_with_retry(sb.table("hw_journal").insert({
            "uid": uid,
            "ts": now_u.isoformat(),
            "text": journal or "",
            "tags": [cbt_tag] if cbt_tag else [],
            "embedding": emb
        }))
    except Exception:
        pass

    # Fire a lightweight event so the nudge worker can react immediately
    try:
        exec_with_retry(sb.table("hw_events").insert({
            "uid": uid,
            "kind": "metrics_saved",
            "payload": {"source": "manual", "mood": int(mood)}
        }))
    except Exception:
        pass

    st.success("Mental well-being saved for today.")
    today_row = _get_today_manual(uid)
