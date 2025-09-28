# pages/04_Get_Started.py
import streamlit as st
import secrets, string
from postgrest.exceptions import APIError

from nav import apply_global_ui, top_nav

apply_global_ui()
from supa import get_sb

st.set_page_config(page_title="Get Started - Health Whisperer",
                   layout="wide",
                   initial_sidebar_state="collapsed")
st.markdown("""<style>section[data-testid="stSidebarNav"]{display:none;}</style>""", unsafe_allow_html=True)

def on_sign_out(sb=None):
    try:
        if sb: sb.auth.sign_out()
    finally:
        st.session_state.pop("sb_session", None)

is_authed = "sb_session" in st.session_state
top_nav(is_authed, on_sign_out, current="Get Started")

if "sb_session" not in st.session_state:
    st.warning("Please sign in first.")
    st.switch_page("pages/02_Sign_In.py")
    st.stop()

user_id = st.session_state["sb_session"]["user_id"]
access_token = st.session_state["sb_session"]["access_token"]
sb = get_sb(access_token)  # <-- authed client
bot_username = st.secrets["app"].get("bot_username", "HealthWhispererBot")

# Retry helper
def exec_with_retry(req, tries: int = 3, base_delay: float = 0.4):
    import time
    for i in range(tries):
        try:
            return req.execute()
        except APIError as e:
            if "PGRST303" in str(e) or "JWT expired" in str(e):
                st.error("Your session expired. Please sign in again.")
                on_sign_out(sb); st.switch_page("pages/02_Sign_In.py"); st.stop()
            raise
        except Exception:
            time.sleep(base_delay * (i + 1))
    return req.execute()

st.title("Connect to the Telegram Bot")
st.write("Follow these steps to link your Telegram with your Health Whisperer profile.")

def get_or_create_link_code(user_id: str) -> str:
    sel = exec_with_retry(sb.table("tg_links").select("link_code, telegram_id").eq("user_id", user_id).maybe_single())
    data = sel.data or None
    if data and data.get("link_code"):
        return data["link_code"]
    code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    exec_with_retry(sb.table("tg_links").upsert({"user_id": user_id, "link_code": code}))
    return code

code = get_or_create_link_code(user_id)

st.markdown(f'''
1. Open Telegram and start a chat with **@{bot_username}** → [t.me/{bot_username}](https://t.me/{bot_username})  
2. Send: `/link {code}` to connect your account.  
3. After linking, simply chat with the bot to receive **personalized nudges**.
''')

st.info("If you change your profile here later, nudges will use the updated info.")
