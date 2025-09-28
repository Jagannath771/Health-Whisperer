# nav.py
import streamlit as st
from pathlib import Path

THEME_PATH = Path(__file__).parent / "theme.css"

def _inject_theme():
    css = ""
    try:
        css = THEME_PATH.read_text(encoding="utf-8")
    except Exception:
        pass
    if css:
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
        # Optional: enable image background if you created ./assets/bg.png
        st.markdown("<script>document.body.classList.add('hw-bg-image');</script>", unsafe_allow_html=True)

def apply_global_ui():
    """
    Call at the top of EVERY page before drawing content.
    Hides Streamlit's sidebar + collapse button, sets layout wide, injects theme.css.
    """
    st.set_page_config(layout="wide", initial_sidebar_state="collapsed")
    # Safety net hide (in case CSS fails for a sec)
    st.markdown("""
    <style>
      [data-testid="stSidebar"], [data-testid="stSidebarNav"],
      section[data-testid="stSidebar"], section[data-testid="stSidebarNav"],
      div[data-testid="stSidebar"], div[data-testid="stSidebarNav"] { display: none !important; }
      [data-testid="stAppViewContainer"] > .main { margin-left: 0 !important; }
    </style>
    """, unsafe_allow_html=True)
    _inject_theme()

def top_nav(is_authed: bool = False, on_sign_out=None, current: str = ""):
    on_sign_out = on_sign_out or (lambda: None)

    # wrapper + minimal structure (styling is from theme.css)
    st.markdown('<div class="hw-bar-wrap"><div class="hw-bar">', unsafe_allow_html=True)

    main_links = [
        ("Home",        "app.py",                   "🏠 Home"),
        ("Get Started", "pages/04_Get_Started.py",  "🚀 Get Started"),
        ("Dashboard",   "pages/05_Dashboard.py",    "📊 Dashboard"),
        ("Notify",      "pages/08_Notifications.py","🔔 Notifications"),
    ]
    log_links = [
        ("LogPhysical",  "pages/06a_Log_Physical.py",  "🏃 Log Physical"),
        ("LogMental",    "pages/06b_Log_Mental.py",    "🧠 Log Mental"),
        ("LogNutrition", "pages/06c_Log_Nutrition.py", "🍽️ Log Nutrition"),
    ]

    # main group
    st.markdown('<div class="hw-group">', unsafe_allow_html=True)
    for key, path, label in main_links:
        cls = "hw-pill hw-active" if key == current else "hw-pill"
        st.markdown(f'<span class="{cls}">', unsafe_allow_html=True)
        st.page_link(path, label=label)
        st.markdown('</span>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

    # logs group
    st.markdown('<div class="hw-group">', unsafe_allow_html=True)
    for key, path, label in log_links:
        cls = "hw-pill hw-active" if key == current else "hw-pill"
        st.markdown(f'<span class="{cls}">', unsafe_allow_html=True)
        st.page_link(path, label=label)
        st.markdown('</span>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

    # spacer
    st.markdown('<div style="flex:1"></div>', unsafe_allow_html=True)

    # auth
    if is_authed:
        if st.button("Sign out"):
            on_sign_out()
            st.switch_page("app.py")
    else:
        st.page_link("pages/02_Sign_In.py", label="Sign in")

    st.markdown('</div></div>', unsafe_allow_html=True)
