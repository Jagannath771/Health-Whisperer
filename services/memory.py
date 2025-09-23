# services/memory.py  (OpenAI version)
import os
from datetime import datetime, timezone
from typing import List
import dotenv
dotenv.load_dotenv()  # take environment variables from .env.

from supabase import create_client
from services.llm_openai import embed_text, chat_text

# --- Env & clients ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")


if not (SUPABASE_URL and SUPABASE_KEY):
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

def _now():
    return datetime.now(timezone.utc)

def log_chat(uid: str, role: str, text: str, metadata: dict | None = None):
    emb = embed_text(text)
    sb.table("hw_chat_history").insert({
        "uid": uid,
        "role": role,
        "text": text,
        "metadata": metadata or {},
        "embedding": emb
    }).execute()

def retrieve_context(uid: str, query: str, k: int = 6) -> list[dict]:
    qv = embed_text(query)
    # Vector search via your RPC; falls back to most-recent if RPC missing
    res = sb.rpc("match_user_history", {"uid_in": uid, "query_embedding": qv, "match_count": k}).execute()
    if getattr(res, "data", None):
        return res.data
    msgs = (sb.table("hw_chat_history")
              .select("*").eq("uid", uid)
              .order("ts", desc=True).limit(k).execute().data or [])
    return msgs

def update_user_summary(uid: str):
    msgs = (sb.table("hw_chat_history")
              .select("role,text").eq("uid", uid)
              .order("ts", desc=True).limit(50).execute().data or [])
    if not msgs:
        return None
    convo = "\n".join([f"{m['role']}: {m['text']}" for m in msgs[::-1]])
    prompt = f"""Summarize stable preferences, routines, constraints, and health goals from the chat below.
Return <=10 lines, no PII.

Conversation:
{convo}
"""
    # Use adapter chat_text which returns a plain string
    summ = chat_text(
        "You are a concise memory summarizer.",
        prompt,
    )
    if not summ:
        return None
    emb = embed_text(summ)
    # Upsert into user summaries table
    sb.table("hw_user_summaries").upsert({
        "uid": uid,
        "summary": summ,
        "updated_at": _now().isoformat(),
        "embedding": emb
    }).execute()
    return summ

def personal_context(uid: str, query_hint: str = "nudges") -> str:
    """
    Compose lightweight personal context:
      - Long-term summary (from hw_user_summaries; fallback to hw_user_memory)
      - Top-k recent chat notes (vector/RPC or recency fallback)
    Never assume .execute() returns an object; guard all .data access.
    """
    # 1) Try summaries table first (this is what update_user_summary() writes)
    summ = ""
    try:
        r = sb.table("hw_user_summaries").select("summary").eq("uid", uid).maybe_single().execute()
        if r and getattr(r, "data", None):
            summ = (r.data or {}).get("summary") or ""
    except Exception:
        pass

    # 2) Fallback to old memory table if present
    if not summ:
        try:
            r = sb.table("hw_user_memory").select("summary").eq("uid", uid).maybe_single().execute()
            if r and getattr(r, "data", None):
                summ = (r.data or {}).get("summary") or ""
        except Exception:
            pass

    # 3) If still empty, try to build & store one from recent chat
    if not summ:
        try:
            built = update_user_summary(uid)
            summ = built or ""
        except Exception:
            summ = ""

    # 4) Recent notes via vector search (already safely coded)
    recents = retrieve_context(uid, query_hint, k=6) or []
    recent_text = "\n".join([f"- {r.get('text','')}" for r in recents if r.get("text")])

    return f"Long-term summary:\n{summ}\n\nRecent notes:\n{recent_text}".strip()


def retrieve_health_context(uid: str, query: str, k: int = 8) -> list[dict]:
    """
    Unified RAG: chat history + journal + meals.
    Requires RPCs: match_user_history, match_journal, match_meals.
    """
    v = embed_text(query)
    a = sb.rpc("match_user_history", {"uid_in": uid, "query_embedding": v, "match_count": k}).execute()
    b = sb.rpc("match_journal",      {"uid_in": uid, "query_embedding": v, "match_count": k}).execute()
    c = sb.rpc("match_meals",        {"uid_in": uid, "query_embedding": v, "match_count": k}).execute()
    A = getattr(a, "data", []) or []
    B = getattr(b, "data", []) or []
    C = getattr(c, "data", []) or []

    # sort by similarity then recency if ts exists
    def key(r):
        sim = r.get("similarity", 0.0)
        ts  = r.get("ts") or r.get("created_at") or ""
        return (sim, ts)

    return sorted(A + B + C, key=key, reverse=True)[:k]
