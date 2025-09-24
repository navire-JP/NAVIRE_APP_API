# core/config.py
import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY manquant")

# Autoriser tout en dev, peaufiner en prod si besoin
ALLOWED_ORIGINS = (os.getenv("ALLOWED_ORIGINS") or "*").split(",")

client = OpenAI(api_key=OPENAI_API_KEY)

