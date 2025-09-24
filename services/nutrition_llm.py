# services/nutrition_llm.py
from __future__ import annotations
from supa import get_sb
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Reuse your OpenAI helpers (lazy import + secrets )
from services.llm_openai import chat_json, embed_text  # :contentReference[oaicite:2]{index=2}

# Supabase + Streamlit are optional at import-time to avoid hard crashes
try:
    import streamlit as st  # type: ignore
except Exception:
    st = None

try:
    from supabase import create_client  # type: ignore
except Exception:
    create_client = None  # We'll guard calls if supabase client can't be made


# ---------- Small utils ----------

def _sb():
    """
    Build a Supabase client from Streamlit secrets.
    Guarded so that just importing this module won't crash if not available.
    """
    if create_client is None or st is None:
        raise RuntimeError("Supabase or Streamlit not available in this environment.")
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    return create_client(url, key)

def _to_int(v: Optional[float]) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(round(float(v)))
    except Exception:
        return None

def _sum_safe(values: List[Optional[float]]) -> Optional[float]:
    nums = [float(v) for v in values if v is not None]
    return round(sum(nums), 2) if nums else None


# ---------- Public API ----------

def estimate_meal(text: str) -> Dict[str, Any]:
    """
    Parse free-text food into a structured estimate.
    Returns a dict with fields:
      {
        "items": [
          {"name": str, "portion": str | None,
           "calories": float | None, "protein_g": float | None,
           "carbs_g": float | None, "fat_g": float | None,
           "sodium_mg": float | None, "sugar_g": float | None}
          ...
        ],
        "totals": {"calories": float | None, "protein_g": ..., ...}
      }
    """
    system = (
        "You are a careful nutrition estimator. "
        "Return ONLY JSON with fields: items[], totals. "
        "Each item has: name, portion, calories, protein_g, carbs_g, fat_g, sodium_mg, sugar_g. "
        "Use null when unsure; never invent unrealistic values."
    )
    user = f"Estimate this meal: {text}\nReturn valid JSON."

    data = chat_json(system, user)  # uses your configured OpenAI model  :contentReference[oaicite:3]{index=3}

    # Normalize shape
    items: List[Dict[str, Any]] = []
    for it in (data.get("items") or []):
        items.append({
            "name": it.get("name"),
            "portion": it.get("portion"),
            "calories": _num_or_none(it.get("calories")),
            "protein_g": _num_or_none(it.get("protein_g")),
            "carbs_g": _num_or_none(it.get("carbs_g")),
            "fat_g": _num_or_none(it.get("fat_g")),
            "sodium_mg": _num_or_none(it.get("sodium_mg")),
            "sugar_g": _num_or_none(it.get("sugar_g")),
        })

    totals = data.get("totals") or {}
    # If totals absent, compute from items
    totals = {
        "calories": _sum_safe([it["calories"] for it in items]),
        "protein_g": _sum_safe([it["protein_g"] for it in items]),
        "carbs_g":   _sum_safe([it["carbs_g"] for it in items]),
        "fat_g":     _sum_safe([it["fat_g"] for it in items]),
        "sodium_mg": _sum_safe([it["sodium_mg"] for it in items]),
        "sugar_g":   _sum_safe([it["sugar_g"] for it in items]),
    } if not data.get("totals") else {
        "calories": _num_or_none(totals.get("calories")),
        "protein_g": _num_or_none(totals.get("protein_g")),
        "carbs_g":   _num_or_none(totals.get("carbs_g")),
        "fat_g":     _num_or_none(totals.get("fat_g")),
        "sodium_mg": _num_or_none(totals.get("sodium_mg")),
        "sugar_g":   _num_or_none(totals.get("sugar_g")),
    }

    return {"items": items, "totals": totals}


def build_blurb(raw_text: str, parsed: Dict[str, Any]) -> str:
    its = (parsed or {}).get("items") or []
    tot = (parsed or {}).get("totals") or {}
    parts = []
    # left: quick human summary
    if tot:
        cal = tot.get("calories")
        p = tot.get("protein_g"); c = tot.get("carbs_g"); f = tot.get("fat_g")
        macro = " · ".join(
            [f"{int(cal)} kcal" if cal else "kcal ?",
             f"P {int(p)}g" if p is not None else "P ?",
             f"C {int(c)}g" if c is not None else "C ?",
             f"F {int(f)}g" if f is not None else "F ?"]
        )
        parts.append(macro)
    # right: first 2 items, if any
    if its:
        names = [str((it or {}).get("name") or "").strip() for it in its]
        names = [n for n in names if n][:2]
        if names:
            parts.append(" • " + ", ".join(names))
    # fallback to raw text if nothing else
    if not parts:
        parts = [raw_text.strip()[:120]]
    return " — ".join(parts)

def _num_or_none(x):
    try:
        if x is None: return None
        if isinstance(x, (int, float)): return float(x)
        s = str(x).strip()
        if s == "" or s.lower() in {"none","null","nan"}: return None
        return float(s)
    except Exception:
        return None

def _to_int(x: Optional[float]) -> Optional[int]:
    if x is None: return None
    try:
        return int(round(float(x)))
    except Exception:
        return None

def save_meal(
    uid: str,
    raw_text: str,
    parsed: Dict[str, Any],
    when_utc: Optional[datetime],
    meal_type: str,
    access_token: str,
) -> None:
    sb = get_sb(access_token)

    totals = (parsed or {}).get("totals") or {}
    blurb = build_blurb(raw_text, parsed)

    payload = {
        "uid": uid,
        "ts": (when_utc or datetime.now(timezone.utc)).isoformat(),
        "meal_type": meal_type,
        "items": raw_text,

        # integer macro columns as per your schema
        "calories":  _to_int(_num_or_none(totals.get("calories"))),
        "protein_g": _to_int(_num_or_none(totals.get("protein_g"))),
        "carbs_g":   _to_int(_num_or_none(totals.get("carbs_g"))),
        "fat_g":     _to_int(_num_or_none(totals.get("fat_g"))),
        "sugar_g":   _to_int(_num_or_none(totals.get("sugar_g"))),
        "sodium_mg": _to_int(_num_or_none(totals.get("sodium_mg"))),

        # new rich fields (will be ignored if column missing)
        "blurb": blurb,
        "items_json": (parsed or {}).get("items"),
        "parsed": parsed,
    }

    # add embedding only if the column exists
    try:
        payload["embedding"] = embed_text(blurb or raw_text)
    except Exception:
        # If embed model not configured, skip silently
        payload.pop("embedding", None)

    clean = {k: v for k, v in payload.items() if v is not None}
    sb.table("hw_meals").insert(clean).execute()

def parse_and_log(
    uid: str,
    raw_text: str,
    meal_type_hint: Optional[str] = None,
    access_token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Convenience wrapper:
      1) estimate_meal(raw_text)
      2) choose meal_type (hint > parsed > 'snacks')
      3) save_meal(...) with the SAME user token for RLS

    Returns a small dict you can echo to UI.
    """
    if access_token is None:
        raise ValueError("parse_and_log requires access_token to satisfy RLS")

    # Use your existing estimator (keep its implementation unchanged elsewhere in this file)
    parsed = estimate_meal(raw_text)  # noqa: F821 (exists in this module)

    mt = meal_type_hint or (
        parsed.get("meal_type") if isinstance(parsed, dict) else None
    ) or "snacks"

    when_u = datetime.now(timezone.utc)
    save_meal(
        uid=uid,
        raw_text=raw_text,
        parsed=parsed,
        when_utc=when_u,
        meal_type=mt,
        access_token=access_token,
    )

    return {
        "saved": {
            "uid": uid,
            "meal_type": mt,
            **(parsed.get("totals") or {}),
        }
    }