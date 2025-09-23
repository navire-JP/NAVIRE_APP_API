# routers/files.py
import uuid
from fastapi import APIRouter, UploadFile, File, HTTPException
from core.db import DATABASE
from utils.pdf_tools import extract_pages_text_from_bytes

router = APIRouter()

MAX_FILE_MB = 25  # garde une limite raisonnable sur le free tier

@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Seuls les fichiers PDF sont acceptés.")

    data = await file.read()
    if len(data) > MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"PDF trop volumineux (> {MAX_FILE_MB} Mo).")

    pages_text = extract_pages_text_from_bytes(data)
    if not any(p.strip() for p in pages_text):
        raise HTTPException(status_code=400, detail="Le PDF ne contient pas de texte exploitable.")

    file_id = str(uuid.uuid4())
    DATABASE["files"][file_id] = {
        "name": file.filename,
        "pages_text": pages_text,
        "page_count": len(pages_text),
    }
    return {"file_id": file_id, "filename": file.filename, "page_count": len(pages_text)}

@router.get("/")
def list_files():
    return [
        {"file_id": fid, "filename": meta["name"], "page_count": meta.get("page_count", 0)}
        for fid, meta in DATABASE["files"].items()
    ]
