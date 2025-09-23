# services/llm_openai.py
import os
from typing import Any, Dict, List, Optional
try:
    import streamlit as st  # optional: for st.secrets in web app
except Exception:
    st = None
from openai import OpenAI

OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-5-mini")
OPENAI_EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")  # 1536 dims (fits pgvector index limit)

def _get_api_key() -> str:
    key = os.getenv("OPENAI_API_KEY")
    if not key and st is not None:
        key = (st.secrets.get("openai") or {}).get("api_key")
    if not key:
        raise RuntimeError("Missing OPENAI_API_KEY (env or [openai].api_key in secrets.toml).")
    return key

def _get_base_url() -> Optional[str]:
    # Let people override base URL if using a proxy
    if st is not None:
        return (st.secrets.get("openai") or {}).get("base_url") or os.getenv("OPENAI_BASE_URL")
    return os.getenv("OPENAI_BASE_URL")

def _client() -> OpenAI:
    return OpenAI(api_key=_get_api_key(), base_url=_get_base_url())

# ---- Embeddings ----
def embed_text(text: str) -> List[float]:
    client = _client()
    out = client.embeddings.create(model=OPENAI_EMBED_MODEL, input=text)
    return out.data[0].embedding  # 1536-d

# ---- Chat completions (text) ----
# ---- Chat completions (text) ----
# services/llm_openai.py

def chat_text(system: str, user: str, **kwargs) -> str:
    client = _client()
    try:
        out = client.chat.completions.create(
            model=OPENAI_CHAT_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            **kwargs
        )
    except Exception as e:
        # If the model rejects params like top_p / frequency_penalty,
        # retry once with no extra kwargs.
        if "Unsupported parameter" in str(e) or "unsupported_parameter" in str(e):
            out = client.chat.completions.create(
                model=OPENAI_CHAT_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
            )
        else:
            raise
    return (out.choices[0].message.content or "").strip()



# ---- JSON helper (robust JSON backstop) ----
def chat_json(system: str, user: str) -> Dict[str, Any]:
    """
    Ask model to return JSON; fall back to best-effort parse if it returns text.
    """
    import json
    client = _client()
    try:
        out = client.chat.completions.create(
            model=OPENAI_CHAT_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        raw = (out.choices[0].message.content or "").strip()
        return json.loads(raw)
    except Exception:
        try:
            # Fallback: try to yank JSON substring
            start = raw.find("{"); end = raw.rfind("}")
            if start >= 0 and end > start:
                return json.loads(raw[start:end+1])
        except Exception:
            pass
        return {}
