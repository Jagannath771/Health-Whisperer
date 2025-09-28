# services/nutrition_llm.py
from __future__ import annotations
from supa import get_sb
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from services.llm_openai import chat_json, embed_text

try:
    import streamlit as st  # type: ignore
except Exception:
    st = None

try:
    from supabase import create_client  # type: ignore
except Exception:
    create_client = None

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

def estimate_meal(text: str) -> Dict[str, Any]:
    system = (
        "You are a careful nutrition estimator. "
        "Return ONLY JSON with fields: items[], totals. "
        "Each item has: name, portion, calories, protein_g, carbs_g, fat_g, sodium_mg, sugar_g. "
        "Use null when unsure; never invent unrealistic values."
    )
    user = f"Estimate this meal: {text}\nReturn valid JSON."
    data = chat_json(system, user)

    def _num_or_none(x):
        try:
            if x is None: return None
            if isinstance(x, (int, float)): return float(x)
            s = str(x).strip()
            if s == "" or s.lower() in {"none","null","nan"}: return None
            return float(s)
        except Exception:
            return None

    items: List[Dict[str, Any]] = []
    for it in (data.get("items") or []):
        items.append({
            "name": it.get("name"),
            "portion": it.get("portion"),
            "calories": _num_or_none(it.get("calories")),
            "protein_g": _num_or_none(it.get("protein_g")),
            "carbs_g":   _num_or_none(it.get("carbs_g")),
            "fat_g":     _num_or_none(it.get("fat_g")),
            "sodium_mg": _num_or_none(it.get("sodium_mg")),
            "sugar_g":   _num_or_none(it.get("sugar_g")),
        })

    totals = data.get("totals") or {}
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
    if its:
        names = [str((it or {}).get("name") or "").strip() for it in its]
        names = [n for n in names if n][:2]
        if names:
            parts.append(" • " + ", ".join(names))
    if not parts:
        parts = [raw_text.strip()[:120]]
    return " — ".join(parts)

def save_meal(
    uid: str,
    raw_text: str,
    parsed: Dict[str, Any],
    when_utc,
    meal_type: str,
    access_token: str,
) -> None:
    sb = get_sb(access_token)
    totals = (parsed or {}).get("totals") or {}
    blurb = build_blurb(raw_text, parsed)

    payload = {
        "uid": uid,
        "ts": (when_utc).isoformat(),
        "meal_type": meal_type,
        "items": raw_text,
        "calories":  _to_int((totals or {}).get("calories")),
        "protein_g": _to_int((totals or {}).get("protein_g")),
        "carbs_g":   _to_int((totals or {}).get("carbs_g")),
        "fat_g":     _to_int((totals or {}).get("fat_g")),
        "sugar_g":   _to_int((totals or {}).get("sugar_g")),
        "sodium_mg": _to_int((totals or {}).get("sodium_mg")),
        "blurb": blurb,
        "items_json": (parsed or {}).get("items"),
        "parsed": parsed,
    }
    try:
        payload["embedding"] = embed_text(blurb or raw_text)
    except Exception:
        payload.pop("embedding", None)

    clean = {k: v for k, v in payload.items() if v is not None}
    sb.table("hw_meals").insert(clean).execute()
