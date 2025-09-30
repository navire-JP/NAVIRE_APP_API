from pathlib import Path
from typing import Optional
from pypdf import PdfReader

from app.core.config import get_settings


def extract_text(file_id: str, pages: Optional[str] = None) -> str:
    """
    Extrait le texte d'un PDF stocké dans STORAGE_PATH.
    - file_id : UUID du fichier (sans extension)
    - pages   : "12-24,30" → sélection de pages
    """
    settings = get_settings()
    pdf_path = Path(settings.STORAGE_PATH) / f"{file_id}.pdf"

    if not pdf_path.exists():
        return ""

    reader = PdfReader(str(pdf_path))

    # Si pas de filtre → toutes les pages
    selected_pages = range(len(reader.pages))

    if pages:
        selected_pages = []
        for part in pages.split(","):
            part = part.strip()
            if "-" in part:
                start, end = part.split("-")
                selected_pages.extend(range(int(start) - 1, int(end)))
            else:
                selected_pages.append(int(part) - 1)

    text_parts = []
    for i in selected_pages:
        if 0 <= i < len(reader.pages):
            page = reader.pages[i]
            text_parts.append(page.extract_text() or "")

    return "\n".join(text_parts)
