# core/db.py
from typing import Dict, Any

DATABASE: Dict[str, Dict[str, Any]] = {
    "files": {},     # file_id -> {name, pages_text: List[str], page_count: int}
    "sessions": {},  # session_id -> {file_id, difficulty, pages, questions, score}
}
