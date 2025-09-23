from typing import Optional, List
from pypdf import PdfReader
from fastapi import HTTPException

def extract_text_from_pdf(path: str, pages_str: Optional[str] = None) -> str:
    try:
        with open(path, "rb") as f:
            reader = PdfReader(f)
            if pages_str:
                wanted = _parse_pages_str(pages_str, len(reader.pages))
                indices = [i - 1 for i in wanted] if wanted else range(len(reader.pages))
            else:
                indices = range(len(reader.pages))
            chunks = []
            for i in indices:
                txt = reader.pages[i].extract_text() or ""
                if txt.strip():
                    chunks.append(txt)
            return "\n".join(chunks)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction PDF échouée: {e}")

def _parse_pages_str(pages_str: str, total_pages: int) -> List[int]:
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
    return sorted(pages)
