# utils/pdf_tools.py
from io import BytesIO
from typing import List, Optional
from pypdf import PdfReader
from fastapi import HTTPException

def extract_pages_text_from_bytes(file_bytes: bytes) -> List[str]:
    try:
        reader = PdfReader(BytesIO(file_bytes))
        pages = []
        for p in reader.pages:
            txt = p.extract_text() or ""
            pages.append(txt)
        return pages
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction PDF échouée: {e}")

def parse_pages_str(pages_str: Optional[str], total_pages: int) -> List[int]:
    if not pages_str:
        return list(range(1, total_pages + 1))
    pages = set()
    for part in pages_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            try:
                a, b = part.split("-", 1)
                start, end = int(a), int(b)
                if start <= end:
                    for p in range(start, end + 1):
                        if 1 <= p <= total_pages:
                            pages.add(p)
            except:
                continue
        else:
            try:
                p = int(part)
                if 1 <= p <= total_pages:
                    pages.add(p)
            except:
                continue
    out = sorted(pages)
    return out if out else list(range(1, total_pages + 1))
