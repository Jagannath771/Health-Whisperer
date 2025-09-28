# pages/05_Dashboard.py
import time
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo
import os
import numpy as np
import pandas as pd
import streamlit as st
from httpx import ReadError
import matplotlib.pyplot as plt
from postgrest.exceptions import APIError
import plotly.express as px
import plotly.graph_objects as go

from supa import get_sb
from services.memory import personal_context
from services.llm_openai import chat_text
from nav import apply_global_ui, top_nav

# ========= Page/UI bootstrap =========
apply_global_ui()
st.set_page_config(page_title="Dashboard - Health Whisperer",
                   layout="wide",
                   initial_sidebar_state="collapsed")
st.markdown("<style>section[data-testid='stSidebarNav']{display:none;}</style>", unsafe_allow_html=True)

# ========= Auth / Nav =========
def on_sign_out(sb=None):
    try:
        if sb:
            sb.auth.sign_out()
    finally:
        st.session_state.pop("sb_session", None)

is_authed = "sb_session" in st.session_state
top_nav(is_authed, on_sign_out, current="Dashboard")
if not is_authed:
    st.warning("Please sign in first.")
    st.switch_page("pages/02_Sign_In.py")
    st.stop()

uid = st.session_state["sb_session"]["user_id"]
access_token = st.session_state["sb_session"]["access_token"]
sb = get_sb(access_token)  # <-- authed client per request

# ========= Retry helper =========
def exec_with_retry(req, tries: int = 3, base_delay: float = 0.4):
    for i in range(tries):
        try:
            return req.execute()
        except APIError as e:
            # JWT expired / PGRST303 => sign out and bounce
            if "PGRST303" in str(e) or "JWT expired" in str(e):
                st.error("Your session expired. Please sign in again.")
                on_sign_out(sb)
                st.switch_page("pages/02_Sign_In.py")
                st.stop()
            raise
        except Exception as e:
            msg = str(e)
            # transient httpx read error or Supabase edge hiccup
            if "10035" in msg or isinstance(e, ReadError):
                time.sleep(base_delay * (i + 1))
                continue
            raise
    return req.execute()

# ========= Time helpers =========
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

def _start_end_days(uid: str, days_back: int = 30):
    tz = _user_tz(uid)
    now_l = datetime.now(timezone.utc).astimezone(tz)
    start_l = (now_l - timedelta(days=days_back)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start_l.astimezone(timezone.utc), now_l.astimezone(timezone.utc)

def _fmt_ts(ts_iso: str | None) -> str:
    if not ts_iso:
        return "—"
    try:
        tz = _user_tz(uid)
        return (datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
                .astimezone(tz).strftime("%b %d, %Y • %I:%M %p"))
    except Exception:
        return ts_iso

# ========= Parsing helpers =========
def _to_dt(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, format="ISO8601", utc=True, errors="coerce")

def _to_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")

def _safe_max(series: pd.Series | None, default: int | float = 0):
    if series is None:
        return default
    s = _to_num(series)
    if s.empty:
        return default
    m = s.max(skipna=True)
    return default if pd.isna(m) else m

def _safe_sum(series: pd.Series | None, default: int | float = 0):
    if series is None:
        return default
    s = _to_num(series).fillna(0)
    if s.empty:
        return default
    return s.sum()

# ========= Data loaders =========
METRIC_COLS_BASE = [
    "ts", "source", "steps", "water_ml", "sleep_minutes", "heart_rate",
    "mood", "meal_quality", "calories"
]
# physical + mental signals we want to visualize
METRIC_COLS_EXTRA = ["pain_level", "energy_level", "stress_level", "anxiety_level", "focus_level"]

def load_meals(uid: str, days_back: int = 30) -> pd.DataFrame:
    start_u, end_u = _start_end_days(uid, days_back)
    req = (sb.table("hw_meals").select("*")
           .eq("uid", uid)
           .gte("ts", start_u.isoformat())
           .lt("ts", end_u.isoformat())
           .order("ts", desc=True))
    r = exec_with_retry(req)
    rows = r.data or []
    if not rows:
        return pd.DataFrame(columns=["ts","meal_type","calories","protein_g","carbs_g","fat_g","fiber_g","sugar_g","sodium_mg","items","raw_text"])
    df = pd.DataFrame(rows)
    df["ts"] = _to_dt(df["ts"])
    for col in ["calories","protein_g","carbs_g","fat_g","fiber_g","sugar_g","sodium_mg"]:
        if col in df.columns:
            df[col] = _to_num(df[col])
    return df

def load_metrics(uid: str, days_back: int = 30) -> pd.DataFrame:
    start_u, end_u = _start_end_days(uid, days_back)
    req = (sb.table("hw_metrics").select("*")
           .eq("uid", uid)
           .gte("ts", start_u.isoformat())
           .lt("ts", end_u.isoformat())
           .order("ts", desc=True))
    r = exec_with_retry(req)
    rows = r.data or []
    if not rows:
        return pd.DataFrame(columns=METRIC_COLS_BASE + METRIC_COLS_EXTRA)
    df = pd.DataFrame(rows)
    df["ts"] = _to_dt(df["ts"])
    for col in METRIC_COLS_BASE[2:] + METRIC_COLS_EXTRA:
        if col in df.columns:
            df[col] = _to_num(df[col])
    return df

def get_prefs(uid: str) -> dict:
    try:
        r = exec_with_retry(sb.table("hw_preferences").select("*").eq("uid", uid).maybe_single())
        return r.data or {}
    except Exception:
        return {}

# ========= Load everything =========
profile = (sb.table("profiles").select("*").eq("id", uid).maybe_single().execute().data or {})
prefs = get_prefs(uid)

# Top-row interactive filters
st.title("Your Dashboard")
flt_col1, flt_col2, flt_col3 = st.columns([1.2, 1, 1])
with flt_col1:
    days_back = st.slider("Window (days)", 7, 90, 30, 1, help="How far back to visualize")
with flt_col2:
    smooth_win = st.slider("Smoothing (days)", 1, 7, 3, 1, help="Rolling window for trend lines")
with flt_col3:
    tz = _user_tz(uid)
    st.caption(f"Timezone: {tz.key if hasattr(tz,'key') else str(tz)}")

meals_df   = load_meals(uid, days_back=days_back)
metrics_df = load_metrics(uid, days_back=days_back)

# ========= Today overview (LOCAL-DATE based, fixes empty today) =========
tz = _user_tz(uid)
today_local = _today(uid)

if not meals_df.empty:
    meals_df["date_local"] = meals_df["ts"].dt.tz_convert(tz).dt.date
    today_meals = meals_df[meals_df["date_local"] == today_local]
else:
    today_meals = pd.DataFrame(columns=meals_df.columns if not meals_df.empty else [])

if not metrics_df.empty:
    metrics_df["date_local"] = metrics_df["ts"].dt.tz_convert(tz).dt.date
    today_metrics = metrics_df[metrics_df["date_local"] == today_local]
else:
    today_metrics = pd.DataFrame(columns=metrics_df.columns if not metrics_df.empty else [])

kcal_goal  = int(prefs.get("daily_calorie_goal") or 2000)
water_goal = int(prefs.get("daily_water_ml") or 2000)
steps_goal = int(prefs.get("daily_step_goal") or 8000)
sleep_goal = int(prefs.get("sleep_goal_min") or 420)

today_kcal   = int(_safe_sum(today_meals.get("calories")))
today_water  = int(_safe_max(today_metrics.get("water_ml")))
today_steps  = int(_safe_max(today_metrics.get("steps")))
today_sleep  = int(_safe_max(today_metrics.get("sleep_minutes")))
_mood_val    = _safe_max(today_metrics.get("mood"))
today_mood   = None if _mood_val in (0, None) else int(_mood_val)

def _latest_today(series: pd.Series) -> int | None:
    if series is None or series.empty:
        return None
    s = _to_num(series)
    if s.empty:
        return None
    v = s.iloc[0] if s.index.size > 0 else None
    return None if pd.isna(v) else int(v)

today_energy  = _latest_today(today_metrics.get("energy_level"))
today_pain    = _latest_today(today_metrics.get("pain_level"))
today_stress  = _latest_today(today_metrics.get("stress_level"))
today_anxiety = _latest_today(today_metrics.get("anxiety_level"))
today_focus   = _latest_today(today_metrics.get("focus_level"))

# ========= KPI rows (5 + 5, readable) =========
row1 = st.columns(5)
row2 = st.columns(5)

row1[0].metric("Calories", f"{today_kcal:,}")
row1[0].caption(f"Goal: {kcal_goal:,} kcal")

row1[1].metric("Water", f"{today_water:,} ml")
row1[1].caption(f"Goal: {water_goal:,} ml")

row1[2].metric("Steps", f"{today_steps:,}")
row1[2].caption(f"Goal: {steps_goal:,}")

row1[3].metric("Sleep", f"{today_sleep:,} min")
row1[3].caption(f"Goal: {sleep_goal:,} min")

row1[4].metric("Mood", "—" if today_mood is None else f"{today_mood}")

row2[0].metric("Energy",  "—" if today_energy  is None else f"{today_energy}")
row2[1].metric("Pain",    "—" if today_pain    is None else f"{today_pain}")
row2[2].metric("Stress",  "—" if today_stress  is None else f"{today_stress}")
row2[3].metric("Anxiety", "—" if today_anxiety is None else f"{today_anxiety}")
row2[4].metric("Focus",   "—" if today_focus   is None else f"{today_focus}")

st.divider()

# ========= Tabs =========
tab_over, tab_trend, tab_twin, tab_meals, tab_badges = st.tabs(
    ["Overview", "Trends & Correlations", "Digital Twin", "Meals & Journal", "Badges & Streaks"]
)

# ======== OVERVIEW ========
with tab_over:
    c1, c2 = st.columns([2, 1])
    with c1:
        st.subheader("Calories (last window)")
        if not meals_df.empty and "calories" in meals_df.columns:
            md = meals_df.copy()
            md["date"] = md["ts"].dt.tz_convert("UTC").dt.date
            cal_day = md.groupby("date", as_index=False)["calories"].sum(numeric_only=True)
            cal_day["roll"] = cal_day["calories"].rolling(smooth_win, min_periods=1).mean()

            fig = go.Figure()
            fig.add_trace(go.Scatter(x=cal_day["date"], y=cal_day["calories"],
                                     mode="lines+markers", name="calories"))
            fig.add_trace(go.Scatter(x=cal_day["date"], y=cal_day["roll"],
                                     mode="lines", name="calories (roll)", line=dict(dash="dash")))
            fig.update_layout(margin=dict(l=10, r=10, t=10, b=10), hovermode="x unified", legend_title_text="")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No meals logged in this window.")

    with c2:
        st.subheader("Hydration today")
        done = min(today_water, water_goal) if water_goal else 0
        remaining = max(water_goal - done, 0)
        fig, ax = plt.subplots()
        ax.pie([done, remaining], startangle=90, wedgeprops=dict(width=0.35))
        ax.set(aspect="equal")
        ax.text(0, 0, f"{done}/{water_goal}\nml", ha="center", va="center")
        st.pyplot(fig)

    st.subheader("Today’s meals")
    if today_meals.empty:
        st.info("No meals today yet.")
    else:
        for _, row in today_meals.sort_values("ts", ascending=False).iterrows():
            ts_local = row["ts"].astimezone(tz).strftime("%b %d, %Y • %I:%M %p")
            kcal = int((row.get("calories") or 0) or 0)
            p = int((row.get("protein_g") or 0) or 0)
            c = int((row.get("carbs_g") or 0) or 0)
            f_ = int((row.get("fat_g") or 0) or 0)
            fiber = row.get("fiber_g")
            fiber_val = 0 if fiber is None or pd.isna(fiber) else int(fiber)
            fiber_txt = f" • Fiber:{fiber_val}" if fiber_val else ""
            st.markdown(f"**{ts_local}** — **{kcal} kcal** (P:{p} C:{c} F:{f_}){fiber_txt}")

# ======== TRENDS ========
with tab_trend:
    st.subheader("Pick metrics to visualize")

    # Build a daily frame with max per day for most metrics, sum for calories
    m = metrics_df.copy()
    if not m.empty:
        m["date"] = m["ts"].dt.tz_convert("UTC").dt.date

    meal_day = pd.DataFrame()
    if not meals_df.empty:
        meal_day = meals_df.copy()
        meal_day["date"] = meal_day["ts"].dt.tz_convert("UTC").dt.date
        meal_day = meal_day.groupby("date", as_index=False)["calories"].sum(numeric_only=True)

    # daily aggregates (max for behavioral metrics)
    daily = pd.DataFrame()
    if not m.empty:
        daily = m.groupby("date", as_index=False)[
            ["steps","water_ml","sleep_minutes","heart_rate",
             "mood","meal_quality","pain_level","energy_level",
             "stress_level","anxiety_level","focus_level"]
        ].max(numeric_only=True)
    if not meal_day.empty:
        daily = daily.merge(meal_day, on="date", how="outer")

    if daily.empty:
        st.info("No metrics in this window.")
    else:
        daily = daily.sort_values("date").reset_index(drop=True)

        # rolling smoothing
        for col in [c for c in daily.columns if c != "date"]:
            daily[f"{col}_roll"] = daily[col].rolling(smooth_win, min_periods=1).mean()

        metric_choices = [
            "calories","steps","water_ml","sleep_minutes","heart_rate",
            "mood","meal_quality","pain_level","energy_level","stress_level","anxiety_level","focus_level"
        ]
        picked = st.multiselect("Chart metrics", metric_choices, default=["steps","calories","mood"])

        if picked:
            show_roll = st.checkbox("Show smoothed trend", True)
            show_range = st.toggle(
                "Show zoom slider", value=False,
                help="Adds a small timeline slider under the chart"
            )

            # Build Plotly figure
            fig = go.Figure()
            for col in picked:
                fig.add_trace(go.Scatter(
                    x=daily["date"], y=daily[col], mode="lines+markers", name=col
                ))
                if show_roll:
                    rc = f"{col}_roll"
                    if rc in daily.columns:
                        fig.add_trace(go.Scatter(
                            x=daily["date"], y=daily[rc], mode="lines",
                            name=f"{col} (roll)", line=dict(dash="dash")
                        ))

            fig.update_layout(hovermode="x unified", legend_title_text="",
                              margin=dict(l=10, r=10, t=10, b=10))
            # OFF unless toggled
            fig.update_xaxes(rangeslider_visible=show_range)
            st.plotly_chart(fig, use_container_width=True)

        st.markdown("#### Correlations (last window)")

        ccols = [
            "calories","steps","water_ml","sleep_minutes","heart_rate",
            "mood","meal_quality","pain_level","energy_level","stress_level","anxiety_level","focus_level"
        ]

        # Keep numeric columns that have enough data & variance
        df_num = daily[ccols].apply(pd.to_numeric, errors="coerce")
        valid_cols = [
            c for c in df_num.columns
            if df_num[c].count() >= 3 and df_num[c].nunique(dropna=True) > 1
        ]

        if len(valid_cols) < 2:
            st.info("Not enough data variation to compute correlations for this window. "
                    "Try increasing the date range or logging more metrics.")
        else:
            corr_df = df_num[valid_cols].corr().round(2)

            as_table = st.toggle("Show as table", value=False)
            if as_table:
                st.dataframe(corr_df, use_container_width=True)
            else:
                heat = px.imshow(
                    corr_df, text_auto=True, aspect="auto",
                    color_continuous_scale="RdBu", zmin=-1, zmax=1
                )
                heat.update_layout(
                    margin=dict(l=10, r=10, t=10, b=10),
                    coloraxis_colorbar=dict(title="r")
                )
                st.plotly_chart(heat, use_container_width=True)
            st.caption("Tip: Look for relationships like higher steps ↔ better mood, or stress ↔ sleep.")

# ======== DIGITAL TWIN ========
with tab_twin:
    st.subheader("Future You — 6-month projection (multi-scenario)")
    # pull simple daily series
    kcal_daily  = metrics_df.groupby(metrics_df["ts"].dt.date)["calories"].max(numeric_only=True) if not metrics_df.empty else pd.Series(dtype=float)
    steps_daily = metrics_df.groupby(metrics_df["ts"].dt.date)["steps"].max(numeric_only=True)    if not metrics_df.empty else pd.Series(dtype=float)
    water_daily = metrics_df.groupby(metrics_df["ts"].dt.date)["water_ml"].max(numeric_only=True) if not metrics_df.empty else pd.Series(dtype=float)
    sleep_daily = metrics_df.groupby(metrics_df["ts"].dt.date)["sleep_minutes"].max(numeric_only=True) if not metrics_df.empty else pd.Series(dtype=float)
    mood_daily  = metrics_df.groupby(metrics_df["ts"].dt.date)["mood"].max(numeric_only=True) if not metrics_df.empty else pd.Series(dtype=float)
    stress_d    = metrics_df.groupby(metrics_df["ts"].dt.date)["stress_level"].max(numeric_only=True) if not metrics_df.empty else pd.Series(dtype=float)
    anxiety_d   = metrics_df.groupby(metrics_df["ts"].dt.date)["anxiety_level"].max(numeric_only=True) if not metrics_df.empty else pd.Series(dtype=float)
    focus_d     = metrics_df.groupby(metrics_df["ts"].dt.date)["focus_level"].max(numeric_only=True) if not metrics_df.empty else pd.Series(dtype=float)

    kcal_avg  = float(kcal_daily.mean())  if not kcal_daily.empty  else 2000.0
    steps_avg = float(steps_daily.mean()) if not steps_daily.empty else 6000.0
    water_avg = float(water_daily.mean()) if not water_daily.empty else 1200.0
    sleep_avg = float(sleep_daily.mean()) if not sleep_daily.empty else 360.0
    mood_avg  = float(mood_daily.mean())  if not mood_daily.empty  else 3.0
    stress_avg  = float(stress_d.mean())  if not stress_d.empty    else 3.0
    anxiety_avg = float(anxiety_d.mean()) if not anxiety_d.empty   else 3.0
    focus_avg   = float(focus_d.mean())   if not focus_d.empty     else 3.0

    def activity_factor(level: str) -> float:
        m = {"Sedentary":1.2,"Lightly active":1.375,"Moderately active":1.55,"Very active":1.725,"Athlete":1.9}
        if not level: return 1.2
        for k,v in m.items():
            if k.lower() in str(level).lower(): return v
        return 1.2

    def estimate_tdee(profile: dict, steps_avg: float, sleep_avg: float, water_avg: float) -> float:
        age = int(profile.get("age") or 30)
        h = float(profile.get("height_cm") or 170.0)
        w = float(profile.get("weight_kg") or 75.0)
        gender = (profile.get("gender") or "").lower()
        bmr = 10*w + 6.25*h - 5*age + (5 if gender.startswith("m") else -161 if gender.startswith("f") else -78)
        af = activity_factor(profile.get("activity_level"))
        # modest activity & recovery adjustments
        steps_bonus = 80.0 * max(0.0, (steps_avg - 6000.0) / 2000.0)
        sleep_pen = -100.0 if sleep_avg < 360 else (-50.0 if sleep_avg < 420 else 0.0)
        water_pen = -40.0 if water_avg < 1000 else 0.0
        return bmr * af + steps_bonus + sleep_pen + water_pen

    def wellbeing_score(mood: float, stress: float, anxiety: float, focus: float) -> float:
        mood_n   = (mood - 1) / 4.0
        focus_n  = (focus - 1) / 4.0
        stress_n = 1 - (stress - 1) / 4.0
        anxiety_n= 1 - (anxiety - 1) / 4.0
        raw = 0.30*mood_n + 0.30*focus_n + 0.20*stress_n + 0.20*anxiety_n
        return round(100*raw, 1)

    base_wellbeing = wellbeing_score(mood_avg, stress_avg, anxiety_avg, focus_avg)

    colA, colB, colC, colD = st.columns(4)
    with colA:
        delta_steps = st.slider("Δ Steps/day", 0, 6000, 2000, 250)
    with colB:
        delta_sleep = st.slider("Δ Sleep/night (min)", -120, 120, 30, 15)
    with colC:
        delta_water = st.slider("Δ Water/day (ml)", -1000, 1000, 400, 100)
    with colD:
        delta_kcal  = st.slider("Δ Intake/day (kcal)", -600, 600, -150, 50)

    def project_weight_series(profile: dict,
                              kcal_intake: float, steps_avg: float, sleep_avg: float, water_avg: float,
                              delta_steps: int = 0, delta_sleep: int = 0, delta_water: int = 0, delta_intake: int = 0,
                              days: int = 180, adherence: float = 1.0) -> list[float]:
        w0 = float(profile.get("weight_kg") or 75.0)
        series, w = [], w0
        for _ in range(days+1):
            tdee = estimate_tdee(profile, steps_avg+delta_steps, sleep_avg+delta_sleep, water_avg+delta_water)
            intake = kcal_intake + delta_intake
            delta_kg = ((intake - tdee) / 7700.0) * adherence
            w = max(35.0, w + delta_kg)
            series.append(w)
        return series

    def bmi_series(kg_series: list[float], height_cm: float) -> list[float]:
        m2 = (float(profile.get("height_cm") or 170.0)/100.0)**2
        return [round(w/m2, 1) for w in kg_series]

    def adherence_multiplier(mood: float, stress: float, anxiety: float, focus: float, sleep_avg: float) -> float:
        term = 1.0
        term *= 1.02 if mood >= 3.5 else 0.98
        term *= 1.02 if focus >= 3.5 else 0.98
        term *= 0.97 if stress >= 3.5 else 1.00
        term *= 0.97 if anxiety >= 3.5 else 1.00
        term *= 0.97 if sleep_avg < 360 else 1.00
        return max(0.88, min(1.08, term))

    adherence = adherence_multiplier(mood_avg, stress_avg, anxiety_avg, focus_avg, sleep_avg)

    series_base = project_weight_series(profile, kcal_avg, steps_avg, sleep_avg, water_avg,
                                        days=180, adherence=adherence)
    series_plan = project_weight_series(profile, kcal_avg, steps_avg+delta_steps, sleep_avg+delta_sleep,
                                        water_avg+delta_water, delta_intake=delta_kcal,
                                        days=180, adherence=adherence)

    twin_fig = go.Figure()
    twin_fig.add_trace(go.Scatter(x=list(range(181)), y=series_base, mode="lines", name="Current routine (kg)"))
    twin_fig.add_trace(go.Scatter(x=list(range(181)), y=series_plan, mode="lines", name="Planned changes (kg)"))
    twin_fig.update_layout(xaxis_title="Days", yaxis_title="Weight (kg)", margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(twin_fig, use_container_width=True)

    wb_plan = wellbeing_score(min(5.0, mood_avg+0.5),
                              max(1.0, stress_avg-0.5),
                              max(1.0, anxiety_avg-0.5),
                              min(5.0, focus_avg+0.5))
    kpi1, kpi2, kpi3 = st.columns(3)
    kpi1.info(f"Wellbeing Score (now): **{base_wellbeing} / 100**")
    kpi2.success(f"Wellbeing (with better balance): **{wb_plan} / 100**")
    kpi3.caption("Wellbeing combines mood, focus, stress, anxiety (toy model).")

# ======== MEALS & JOURNAL ========
with tab_meals:
    st.subheader("Meals in window")
    if meals_df.empty:
        st.info("No meals in this window.")
    else:
        show_cols = ["ts","meal_type","calories","protein_g","carbs_g","fat_g","fiber_g","sugar_g","sodium_mg","items","raw_text"]
        have_cols = [c for c in show_cols if c in meals_df.columns]
        st.dataframe(meals_df[have_cols], use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Nudge preview (AI-generated)")
    ask = st.text_area("Enter situation or goal", value="", placeholder="e.g., motivate me to drink more water")
    if os.getenv("OPENAI_API_KEY"):
        if st.button("Generate nudge") and ask:
            ctx = personal_context(uid, query_hint="nudges")
            prompt = f"""
You are generating a single, kind micro-nudge for a health app user.

User request (verbatim):
{ask}

Personal context (summaries + recent notes):
{ctx}

Constraints:
- 1 actionable suggestion, plain English, max 80 words.
- Be specific (time, duration, quantity). Avoid generic platitudes.
"""
            txt = chat_text("You are Personalized Health Whisperer. Concise, safe, <80 words.", prompt)
            st.success(txt or "I'm here for you.")
    else:
        st.info("Set OPENAI_API_KEY to enable nudge previews here.")

# ======== BADGES & STREAKS ========
with tab_badges:
    st.subheader("Engagement")
    def goal_hits_by_day(metrics_df: pd.DataFrame, prefs: dict) -> pd.DataFrame:
        if metrics_df.empty:
            return pd.DataFrame(columns=["day","steps_hit","water_hit","sleep_hit","any_hit","steps","water_ml","sleep_minutes"])
        df = metrics_df.copy()
        df["day"] = df["ts"].dt.tz_convert(timezone.utc).dt.date
        agg = df.groupby("day").agg({"steps":"max","water_ml":"max","sleep_minutes":"max"}).reset_index()
        steps_goal = int(prefs.get("daily_step_goal") or 8000)
        water_goal = int(prefs.get("daily_water_ml") or 2000)
        sleep_goal = int(prefs.get("sleep_goal_min") or 420)
        agg["steps_hit"] = (agg["steps"] >= steps_goal)
        agg["water_hit"] = (agg["water_ml"] >= water_goal)
        agg["sleep_hit"] = (agg["sleep_minutes"] >= sleep_goal)
        agg["any_hit"]   = agg[["steps_hit","water_hit","sleep_hit"]].any(axis=1)
        return agg.sort_values("day")

    def current_streak(hit_series: pd.Series) -> int:
        cnt = 0
        for ok in reversed(list(hit_series)):
            if ok: cnt += 1
            else: break
        return cnt

    BADGE_RULES = [
        ("WATER_7D", "Hydration Hero (7-day)", lambda hits: int(hits["water_hit"].tail(7).sum()) >= 7),
        ("STEPS_10K", "10k Steps Day",        lambda hits: ((hits["steps"] >= 10000).tail(1).any()) or (hits["steps_hit"].tail(1).any())),
        ("SLEEP_7x",  "Sleep Consistency",    lambda hits: int(hits["sleep_hit"].tail(7).sum()) >= 5),
    ]

    def evaluate_badges(uid: str, hits: pd.DataFrame):
        earned = []
        for code, label, rule in BADGE_RULES:
            try:
                if not hits.empty and rule(hits):
                    earned.append((code,label))
            except Exception:
                pass
        for code, label in earned:
            try:
                sb.table("hw_badges").upsert({
                    "uid": uid, "code": code, "earned_on": datetime.now(timezone.utc).date()
                }, on_conflict="uid,code").execute()
            except Exception:
                pass
        return earned

    hits = goal_hits_by_day(metrics_df, prefs)
    streak_any = current_streak(hits["any_hit"]) if not hits.empty else 0
    weekly = hits.tail(7) if not hits.empty else pd.DataFrame()
    water_hits = int(weekly["water_hit"].sum()) if not weekly.empty else 0
    step_days  = int(weekly["steps_hit"].sum()) if not weekly.empty else 0
    sleep_days = int(weekly["sleep_hit"].sum()) if not weekly.empty else 0

    g1, g2, g3, g4 = st.columns(4)
    g1.success(f"🔥 Streak (any goal): {streak_any} days")
    g2.info(f"💧 Hydration days (7d): {water_hits}/7")
    g3.info(f"🚶 Steps days (7d): {step_days}/7")
    g4.info(f"😴 Sleep days (7d): {sleep_days}/7")

    earned = evaluate_badges(uid, hits)
    if earned:
        st.balloons()
        st.success("New badges unlocked: " + ", ".join(lbl for _, lbl in earned))
    else:
        st.caption("Keep going to unlock badges like **Hydration Hero**, **Sleep Consistency**, and a **10k Steps Day**!")
