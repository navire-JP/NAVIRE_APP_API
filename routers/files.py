import os, uuid
from fastapi import APIRouter, UploadFile, File, HTTPException
from pypdf import PdfReader
from core.config import STORAGE_DIR
from core.db import DATABASE

router = APIRouter()

def extract_text_from_pdf(path: str) -> str:
    try:
        with open(path, "rb") as f:
            reader = PdfReader(f)
            texts = [(page.extract_text() or "") for page in reader.pages]
        return "\n".join(texts)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction PDF échouée: {e}")

@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename)[1].lower()
    if ext != ".pdf":
        raise HTTPException(status_code=400, detail="Seuls les fichiers PDF sont acceptés.")
    file_id = str(uuid.uuid4())
    out_path = os.path.join(STORAGE_DIR, f"{file_id}{ext}")
    with open(out_path, "wb") as out:
        out.write(await file.read())
    text = extract_text_from_pdf(out_path)
    DATABASE["files"][file_id] = {"name": file.filename, "path": out_path, "text": text}
    return {"file_id": file_id, "filename": file.filename}

@router.get("/")
def list_files():
    return [{"file_id": fid, "filename": meta["name"]} for fid, meta in DATABASE["files"].items()]
