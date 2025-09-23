from typing import Dict, Any

DATABASE: Dict[str, Dict[str, Any]] = {
    "files": {},     # file_id -> {name, path, text}
    "sessions": {},  # session_id -> {file_id, questions}
}
