# services/nutrition_llm.py
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Reuse your OpenAI helpers (lazy import + secrets handling)
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


def save_meal(
    uid: str,
    raw_text: str,
    parsed: Dict[str, Any],
    when_utc: Optional[datetime],
    meal_type: str,
) -> None:
    """
    Inserts a meal into hw_meals with best-available fields,
    and (optionally) an embedding of a concise blurb for retrieval.
    """
    ts = when_utc or datetime.now(timezone.utc)

    # Extract totals
    totals = parsed.get("totals") or {}
    calories = _to_int(_num_or_none(totals.get("calories")))
    protein_g = _num_or_none(totals.get("protein_g"))
    carbs_g   = _num_or_none(totals.get("carbs_g"))
    fat_g     = _num_or_none(totals.get("fat_g"))
    sodium_mg = _num_or_none(totals.get("sodium_mg"))
    sugar_g   = _num_or_none(totals.get("sugar_g"))

    # Build a compact factual blurb for retrieval (RAG)
    items_preview = "; ".join(
        f"{(it.get('name') or '').strip()}"
        + (f" ({it.get('portion')})" if it.get("portion") else "")
        for it in (parsed.get("items") or [])
        if it.get("name")
    )
    blurb = (
        f"{meal_type.title()} • {items_preview or raw_text} "
        f"→ kcal {calories if calories is not None else 'unk'}; "
        f"P {fmt_num(protein_g)}g; C {fmt_num(carbs_g)}g; F {fmt_num(fat_g)}g; "
        f"Na {fmt_num(sodium_mg)}mg; Sug {fmt_num(sugar_g)}g"
    )

    # Try to embed the blurb
    embedding: Optional[List[float]] = None
    try:
        embedding = embed_text(blurb)  # 1536-d vector  :contentReference[oaicite:4]{index=4}
    except Exception:
        # If embedding fails (missing key, offline, etc.), continue without it
        embedding = None

    payload = {
        "uid": uid,
        "ts": ts.isoformat(),
        "meal_type": meal_type,
        "items": raw_text,
        "calories": calories,
        "protein_g": protein_g,
        "carbs_g": carbs_g,
        "fat_g": fat_g,
        "sodium_mg": sodium_mg,
        "sugar_g": sugar_g,
        "parsed": parsed,       # if you created a JSONB column, this will land there
        "blurb": blurb,         # if you added a text column for display
        "embedding": embedding, # if you added a vector column (optional)
    }

    # Remove None so we don't trip NOT NULL / type casting
    clean = {k: v for k, v in payload.items() if v is not None}

    # Insert with a retry that drops 'embedding' if schema doesn't have it yet
    sb = _sb()
    try:
        sb.table("hw_meals").insert(clean).execute()
    except Exception as e:
        if "embedding" in clean:
            clean.pop("embedding", None)
            sb.table("hw_meals").insert(clean).execute()
        else:
            raise


# ---------- Helpers ----------

def _num_or_none(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        if isinstance(x, (int, float)):
            return float(x)
        s = str(x).strip()
        return float(s) if s not in ("", "null", "None") else None
    except Exception:
        return None

def fmt_num(x: Optional[float]) -> str:
    if x is None:
        return "unk"
    try:
        # show as integer if close to whole number, else 1 decimal
        if abs(x - round(x)) < 0.05:
            return str(int(round(x)))
        return f"{x:.1f}"
    except Exception:
        return "unk"
