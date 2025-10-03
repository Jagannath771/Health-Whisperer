# app.py
from dotenv import load_dotenv
load_dotenv()

import streamlit as st
from nav import apply_global_ui, top_nav

# ---- Optional: authed client (unchanged behavior) ----
def _get_sb_if_available():
    try:
        from supa import get_sb
        token = st.session_state.get("sb_session", {}).get("access_token")
        return get_sb(token) if token else None
    except Exception:
        return None

sb = _get_sb_if_available()

# ---- Global UI ----
apply_global_ui()
st.set_page_config(page_title="Health Whisperer", page_icon="💬", layout="wide", initial_sidebar_state="collapsed")

# ---- Auth helpers ----
def on_sign_out():
    if sb:
        try: sb.auth.sign_out()
        except Exception: pass
    for k in ("sb_session","email","user_id","full_name"):
        st.session_state.pop(k, None)
    st.switch_page("app.py")

is_authed = "sb_session" in st.session_state

# ---- Top navigation ----
top_nav(is_authed=is_authed, on_sign_out=on_sign_out, current="Home")

# ========= Masthead content =========
# (No changes needed here; with the new single-row nav, the hero shows immediately below.)
display_name = st.session_state.get("full_name")

if not display_name and sb and "sb_session" in st.session_state:
    try:
        uid = st.session_state["sb_session"].get("user_id")
        if uid:
            res = sb.table("profiles").select("full_name").eq("id", uid).maybe_single().execute()
            data = getattr(res, "data", None) or {}
            display_name = data.get("full_name")
    except Exception:
        pass

headline = f"Welcome back{', ' + display_name if display_name else ''} 👋"
st.markdown(f"""
<div class="hw-hero">
  <h1>Health Whisperer</h1>
  <h3>{headline}</h3>
  <p>
    A simple, private companion that turns your daily context into
    <b>timely, caring nudges</b>—delivered on Telegram.
    No dashboards to decipher. Just small, practical whispers when they matter.
  </p>
</div>
""", unsafe_allow_html=True)

st.info("⚠️ Health Whisperer is for education and habit support only — not medical advice.", icon="⚠️")

# ---- Quick Actions ----
c1, c2, c3 = st.columns(3)
with c1:
    st.markdown("### 🧩 Profile")
    st.write("Keep your basics and goals up to date for better nudges.")
    st.page_link("pages/03_My_Profile.py", label="Open My Profile →", icon="🧩")
with c2:
    st.markdown("### 🚀 Connect Telegram")
    st.write("Link your chat to start receiving context-aware nudges.")
    st.page_link("pages/04_Get_Started.py", label="Get Started →", icon="🚀")
with c3:
    st.markdown("### 📊 Dashboard")
    st.write("Glanceable insights at a high level — no fluff.")
    st.page_link("pages/05_Dashboard.py", label="Open Dashboard →", icon="📊")

st.divider()

# ---- How it works ----
st.subheader("How it works")
hw1, hw2, hw3 = st.columns(3)
with hw1:
    st.markdown("**1) Set up once**")
    st.write("Create your profile (goals, basics). Link Telegram with a one-time code.")
with hw2:
    st.markdown("**2) Live context**")
    st.write("We use your inputs and preferences (quiet hours, tone, cadence) to time helpful nudges.")
with hw3:
    st.markdown("**3) Timely whispers**")
    st.write("Short, kind nudges for steps, hydration, and headspace — at useful moments.")

st.divider()

# ---- What you’ll find inside ----
st.subheader("What you’ll find inside")
f1, f2, f3 = st.columns(3)
with f1:
    st.markdown("**🔔 Notifications**")
    st.write("Recent nudges in one place. Revisit tips, mark as read.")
    st.page_link("pages/08_Notifications.py", label="Open Notifications →", icon="🔔")
with f2:
    st.markdown("**⚙️ Preferences**")
    st.write("Quiet hours, tone, cadence — tune it to your style.")
    st.page_link("pages/07_Preferences.py", label="Open Preferences →", icon="⚙️")
with f3:
    st.markdown("**🙋 Help & Feedback**")
    st.write("We improve fast — share feedback on the Get Started page.")

st.divider()

# ---- Log today (06a / 06b / 06c) ----
st.subheader("Log today (quick entry)")
lc1, lc2, lc3 = st.columns(3)
with lc1:
    st.markdown("**🏃 Physical**")
    st.write("Steps, sleep, heart rate, quick notes.")
    st.page_link("pages/06a_Log_Physical.py", label="Open →")
with lc2:
    st.markdown("**🧠 Mental**")
    st.write("Mood, stress, short journal.")
    st.page_link("pages/06b_Log_Mental.py", label="Open →")
with lc3:
    st.markdown("**🍽️ Nutrition**")
    st.write("Free-text meals with smart parsing.")
    st.page_link("pages/06c_Log_Nutrition.py", label="Open →")

st.divider()

# ---- Philosophy & Privacy ----
pp1, pp2 = st.columns(2)
with pp1:
    st.subheader("Why not just another dashboard?")
    st.write(
        "- Stats are useful, but *timing is everything*.\n"
        "- We suggest the *next small step*, not a bigger to-do list.\n"
        "- You control what’s shared, when we nudge, and the tone."
    )
with pp2:
    st.subheader("Privacy by design")
    st.write(
        "- Your data is scoped to your account via row-level security.\n"
        "- Adjust or delete information anytime from **My Profile**.\n"
        "- Telegram is used only to deliver your own nudges."
    )

st.divider()
st.caption("© 2025 Health Whisperer — Educational use only, not a medical device.")

if not is_authed:
    st.warning("You’re browsing as a guest. Sign in to personalize your nudges and sync data.", icon="🔑")
    st.page_link("pages/02_Sign_In.py", label="Sign in →", icon="🔑")
