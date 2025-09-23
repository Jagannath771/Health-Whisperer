# nav.py
import streamlit as st

# Key, path, label
LINKS = [
    ("Home",          "app.py",                  "🏠 Home"),
    ("Sign Up",       "pages/01_Sign_Up.py",     "📝 Sign Up"),
    ("Sign In",       "pages/02_Sign_In.py",     "🔐 Sign In"),
    ("My Profile",    "pages/03_My_Profile.py",  "🧩 My Profile"),
    ("Dashboard",     "pages/05_Dashboard.py",   "📊 Dashboard"),
    ("Get Started",   "pages/04_Get_Started.py", "🚀 Get Started"),
    ("Log Physical",  "pages/06a_Log_Physical.py","🏃 Log Physical"),
    ("Log Mental",    "pages/06b_Log_Mental.py", "🧠 Log Mental"),
    ("Log Nutrition", "pages/06c_Log_Nutrition.py","🍽️ Log Nutrition"),
    ("Preferences",   "pages/07_Preferences.py", "⚙️ Preferences"),
    ("Notifications", "pages/08_Notifications.py","🔔 Notifications"),
]

def _render_links(current_key: str):
    """Render links and put a subtle marker on the active one."""
    # left/mid/right columns like your original layout
    left, right = st.columns([9, 1])
    with left:
        # group roughly like before
        for key, path, label in LINKS:
            # add a small dot to the active page
            lbl = f"{label} •" if key == current_key else label
            st.page_link(path, label=lbl)
    with right:
        st.markdown('<div class="hw-right"></div>', unsafe_allow_html=True)

def top_nav(is_authed: bool = False, on_sign_out=None, current: str = ""):
    """
    Minimal, sticky nav bar with page links and an optional Sign out.
    Call like: top_nav(is_authed, on_sign_out, current="Home")
    """
    on_sign_out = on_sign_out or (lambda: None)

    # Styles (unchanged)
    st.markdown("""
    <style>
      section[data-testid="stSidebarNav"] { display:none; }
      .hw-bar-wrap { position: sticky; top: 0; z-index: 999;
                     background: rgba(255,255,255,0.78);
                     -webkit-backdrop-filter: blur(8px); backdrop-filter: blur(8px);
                     border-bottom: 1px solid rgba(0,0,0,0.06); }
      .hw-bar { display:flex; gap:12px; align-items:center; padding: 10px 4px; overflow-x:auto; }
      .block-container { padding-top: 8px; }
      .hw-right { display:flex; justify-content:flex-end; }
    </style>
    """, unsafe_allow_html=True)

    st.markdown('<div class="hw-bar-wrap">', unsafe_allow_html=True)

    # ✅ FIX: pass the current page key into _render_links
    _render_links(current)

    # Right-side auth button area (kept as in your file)
    right = st.columns([1])[0]
    with right:
        st.markdown('<div class="hw-right">', unsafe_allow_html=True)
        if is_authed:
            if st.button("Sign out", use_container_width=True):
                on_sign_out()
                st.switch_page("app.py")
        else:
            st.page_link("pages/02_Sign_In.py", label="Sign in", use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)
