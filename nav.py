# nav.py
import streamlit as st
from pathlib import Path

THEME_PATH = Path(__file__).parent / "theme.css"

def _inject_theme():
    try:
        css = THEME_PATH.read_text(encoding="utf-8")
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
    except Exception:
        pass

def apply_global_ui():
    """Call at the top of EVERY page before drawing content."""
    st.set_page_config(layout="wide", initial_sidebar_state="collapsed")
    # Hard-disable the Streamlit sidebar so only our top bar is visible.
    st.markdown("""
    <style>
      [data-testid="stSidebar"], [data-testid="stSidebarNav"] { display:none !important; }
      [data-testid="stAppViewContainer"] > .main { margin-left:0 !important; }
      .hw-bar-wrap {
        position:sticky; top:0; z-index:999; background:rgba(15,17,23,.70);
        -webkit-backdrop-filter:blur(10px); backdrop-filter:blur(10px);
        border-bottom:1px solid rgba(255,255,255,.06);
        margin-bottom:.25rem;
      }
    </style>
    """, unsafe_allow_html=True)
    _inject_theme()

def top_nav(is_authed: bool = False, on_sign_out=None, current: str = ""):
    """
    Single-row nav using page_link + columns (renders horizontally).
    `current` in {"Home","GetStarted","Dashboard","Notify","LogPhysical","LogMental","LogNutrition"}
    """
    on_sign_out = on_sign_out or (lambda: None)

    items = [
        ("🏠 Home", "app.py", "Home"),
        ("🚀 Get Started", "pages/04_Get_Started.py", "GetStarted"),
        ("📊 Dashboard", "pages/05_Dashboard.py", "Dashboard"),
        ("🏃 Log Physical", "pages/06a_Log_Physical.py", "LogPhysical"),
        ("🧠 Log Mental", "pages/06b_Log_Mental.py", "LogMental"),
        ("🍽️ Log Nutrition", "pages/06c_Log_Nutrition.py", "LogNutrition"),
    ]

    # Sticky wrapper
    st.markdown('<div class="hw-bar-wrap">', unsafe_allow_html=True)

    # One row: N link columns + auth column pinned right
    weights = [1] * len(items) + [0.8]
    cols = st.columns(weights, gap="small")

    for (label, path, key), col in zip(items, cols[:-1]):
        with col:
            # Render as pill-styled page_link (CSS in theme.css)
            st.page_link(path, label=label, use_container_width=True, icon=None,
                         help=None, disabled=False)

            # Apply "active" class by echoing a small scoped style when this item is current
            if key == current:
                st.markdown("""
                <style>
                  /* highlight the most recent page_link rendered in this column */
                  div[data-testid="stVerticalBlock"] a[data-testid^="stPageLink"]{
                    border-color: rgba(20,176,143,.55)!important;
                    background: rgba(20,176,143,.16)!important;
                    box-shadow:0 10px 22px rgba(20,176,143,.18)!important;
                  }
                </style>
                """, unsafe_allow_html=True)

    with cols[-1]:
        if is_authed:
            if st.button("Sign out", key="nav_signout", use_container_width=True):
                on_sign_out()
                st.switch_page("app.py")
        else:
            st.page_link("pages/02_Sign_In.py", label="Sign in", icon=None, use_container_width=True)

    st.markdown('</div>', unsafe_allow_html=True)
