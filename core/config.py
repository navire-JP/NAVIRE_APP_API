import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY manquant")

STORAGE_DIR = os.getenv("STORAGE_DIR", "./storage")
ALLOWED_ORIGINS = (os.getenv("ALLOWED_ORIGINS") or "*").split(",")

os.makedirs(STORAGE_DIR, exist_ok=True)

client = OpenAI(api_key=OPENAI_API_KEY)
