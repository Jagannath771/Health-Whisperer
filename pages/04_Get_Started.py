# pages/04_Get_Started.py
import streamlit as st
import secrets, string
from postgrest.exceptions import APIError

from nav import apply_global_ui, top_nav
from supa import get_sb

# ===== Global UI / Nav =====
apply_global_ui()
is_authed = "sb_session" in st.session_state
top_nav(is_authed=is_authed, current="GetStarted")

st.set_page_config(page_title="Get Started - Health Whisperer",
                   layout="wide",
                   initial_sidebar_state="collapsed")

# ===== Auth guard =====
def on_sign_out(sb=None):
    try:
        if sb: 
            sb.auth.sign_out()
    finally:
        st.session_state.pop("sb_session", None)

if "sb_session" not in st.session_state:
    st.warning("Please sign in first.")
    st.switch_page("pages/02_Sign_In.py")
    st.stop()

user_id = st.session_state["sb_session"]["user_id"]
access_token = st.session_state["sb_session"]["access_token"]
sb = get_sb(access_token)  # <-- authed client
bot_username = st.secrets.get("app", {}).get("bot_username", "HealthWhispererBot")

# ===== Retry helper =====
def exec_with_retry(req, tries: int = 3, base_delay: float = 0.4):
    import time
    for i in range(tries):
        try:
            return req.execute()
        except APIError as e:
            if "PGRST303" in str(e) or "JWT expired" in str(e):
                st.error("Your session expired. Please sign in again.")
                on_sign_out(sb)
                st.switch_page("pages/02_Sign_In.py")
                st.stop()
            if i == tries - 1:
                raise
        except (SystemExit, KeyboardInterrupt):
            raise
        except BaseException:
            time.sleep(base_delay * (i + 1))
    return req.execute()

# ===== Page content =====
st.title("Get Started")
st.write("Link your Telegram and set your **Preferences** so nudges are timely and personal.")

# --- Step 1: Telegram linking ---
st.subheader("1) Connect to the Telegram Bot")
st.caption("This lets us deliver timely nudges right to your chat.")

def get_or_create_link_info(user_id: str):
    sel = exec_with_retry(
        sb.table("tg_links")
          .select("link_code, telegram_id")
          .eq("user_id", user_id)
          .maybe_single()
    )
    data = getattr(sel, "data", None) or {}

    # Already linked
    if data.get("telegram_id"):
        return {"linked": True, "link_code": data.get("link_code")}

    # Not linked yet, reuse existing code
    if data.get("link_code"):
        return {"linked": False, "link_code": data["link_code"]}

    # No row → create new code
    code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    exec_with_retry(sb.table("tg_links").upsert({"user_id": user_id, "link_code": code}))
    return {"linked": False, "link_code": code}

try:
    link_info = get_or_create_link_info(user_id)
except Exception as e:
    st.error(f"Couldn’t fetch or create your Telegram link code: {e}")
    st.stop()

if link_info["linked"]:
    st.success("✅ Your Telegram account is already linked! You’re good to go 🎉")
else:
    code = link_info["link_code"]
    st.markdown(f"""
    **How to link**
    1. Open Telegram and start a chat with **@{bot_username}** → [t.me/{bot_username}](https://t.me/{bot_username})  
    2. Send: ```/link {code}``` to connect your account.  
    3. After linking, just chat with the bot to get **personalized nudges**.
    """)
st.info("If you change your profile or preferences later, nudges will use the updated info.")

st.divider()

# --- Step 2: Preferences ---
st.subheader("2) Set your Preferences")
st.caption("Quiet hours, tone, cadence, and which nudges you want (steps, water, mental).")

# Quick preview of current preferences
try:
    pref_res = exec_with_retry(
        sb.table("hw_preferences").select(
            "primary_channel,nudge_cadence,quiet_start,quiet_end,tone,"
            "nudges_steps,nudges_water,nudges_mental"
        ).eq("uid", user_id).maybe_single()
    )
    pref = getattr(pref_res, "data", None) or {}
except Exception:
    pref = {}

c1, c2, c3 = st.columns(3)
with c1:
    st.metric("Primary channel", (pref.get("primary_channel") or "telegram").title())
with c2:
    st.metric("Cadence", (pref.get("nudge_cadence") or "smart").title())
with c3:
    qs = pref.get("quiet_start") or "21:00"
    qe = pref.get("quiet_end") or "07:00"
    st.metric("Quiet hours", f"{qs} → {qe}")

st.page_link("pages/07_Preferences.py", label="Open Preferences →", icon="⚙️")

st.divider()

# --- Step 3: Done ---
st.subheader("3) You’re all set 🎉")
st.write(
    "You can return to **Preferences** anytime to tweak cadence, tone and quiet hours. "
    "Try logging your **Physical**, **Mental**, and **Nutrition** entries to see the nudges adapt."
)
