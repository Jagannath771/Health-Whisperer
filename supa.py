# supa_client.py
import os
import dotenv
dotenv.load_dotenv()  # take environment variables from .env.
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"] or ""
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"] or ""

def get_sb(user_access_token: str | None = None):
    """
    Returns a Supabase client. If a user access token is provided,
    subsequent PostgREST requests run AS THAT USER (auth.uid() is set).
    """
    sb = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    if user_access_token:
        sb.postgrest.auth(user_access_token)
    return sb
