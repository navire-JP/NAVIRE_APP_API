from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import List, Optional, Dict

from fastapi import APIRouter, UploadFile, File, HTTPException, status
from fastapi.responses import FileResponse

# -------- Config de base --------
router = APIRouter(prefix="/files", tags=["files"])

# Mode mock (Render free)
USE_FAKE = os.getenv("USE_FAKE_FILES", "0") == "1"

# Dossier de stockage (non persistant sur Render free)
STORAGE_DIR = Path(os.getenv("STORAGE_DIR", "storage")).resolve()
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

# -------- PDF page count helper (optionnel) --------
def _safe_count_pdf_pages(file_path: Path) -> Optional[int]:
    """
    Essaie de compter les pages PDF.
    1) utils.pdf_tools.count_pdf_pages si dispo
    2) fallback PyPDF2/PdfReader si installé
    3) sinon None
    """
    # 1) utils.pdf_tools
    try:
        from utils.pdf_tools import count_pdf_pages  # type: ignore
        try:
            return int(count_pdf_pages(str(file_path)))
        except Exception:
            pass
    except Exception:
        pass

    # 2) PyPDF2 (fallback)
    try:
        from PyPDF2 import PdfReader  # type: ignore
        try:
            with open(file_path, "rb") as f:
                return len(PdfReader(f).pages)
        except Exception:
            pass
    except Exception:
        pass

    # 3) inconnu
    return None


# -------- In-memory store (pour mock & index simple) --------
# Structure: file_id -> {"file_id": str, "filename": str, "page_count": Optional[int], "path": str}
FILES_DB: Dict[str, Dict[str, Optional[str]]] = {}

def _scan_storage_once() -> None:
    """
    Recharge un index minimal depuis le disque (non persistant sur Render).
    Ne touche pas au mode mock.
    """
    if USE_FAKE:
        return
    for p in STORAGE_DIR.glob("*"):
        if not p.is_file():
            continue
        file_id = p.stem  # on stocke en "<uuid>.<ext>"
        # Si le nom est au format "<uuid>_<filename>.ext", on recolle le nom d'origine
        original_name = p.name
        # Essayer de retirer l'UUID en tête si on a ce format
        try:
            prefix, rest = original_name.split("_", 1)
            uuid.UUID(prefix)
            filename = rest
        except Exception:
            filename = original_name

        # compter pages si PDF
        page_count = _safe_count_pdf_pages(p) if filename.lower().endswith(".pdf") else None

        FILES_DB[file_id] = {
            "file_id": file_id,
            "filename": filename,
            "page_count": page_count,
            "path": str(p),
        }

# Au démarrage, on peuple depuis le disque (si non mock)
_scan_storage_once()

# -------- MOCK DATA --------
_FAKE_FILES: List[Dict[str, Optional[str]]] = [
    {
        "file_id": "11111111-1111-1111-1111-111111111111",
        "filename": "demo1.pdf",
        "page_count": 6,
        "path": None,  # pas de fichier réel
    },
    {
        "file_id": "22222222-2222-2222-2222-222222222222",
        "filename": "demo2.pdf",
        "page_count": 12,
        "path": None,
    },
]


def _current_store() -> Dict[str, Dict[str, Optional[str]]]:
    if USE_FAKE:
        # convertir la liste en dict file_id -> item (pour accès rapide)
        return {item["file_id"]: item for item in _FAKE_FILES}  # type: ignore
    return FILES_DB


# =========================
#          ROUTES
# =========================

@router.get("", summary="Lister les fichiers")
async def list_files() -> List[Dict[str, Optional[str]]]:
    """
    Retourne une liste de fichiers au format attendu par le client Flutter:
    [
      { "file_id": "...", "filename": "...", "page_count": 12 },
      ...
    ]
    """
    store = _current_store()
    # ordonner par nom
    res = []
    for item in store.values():
        res.append(
            {
                "file_id": item["file_id"],
                "filename": item["filename"],
                "page_count": item.get("page_count"),  # peut être None
            }
        )
    # tri simple par nom de fichier
    res.sort(key=lambda x: (x["filename"] or "").lower())
    return res


@router.post("/upload", summary="Uploader un fichier (multipart)")
async def upload_file(file: UploadFile = File(...)) -> Dict[str, Optional[str]]:
    """
    - En mode MOCK, on simule l'ajout dans la liste.
    - En mode réel, on écrit le fichier dans STORAGE_DIR
      sous un nom '<uuid>_<filename>' pour conserver l'original utilisateur.
    """
    # Sécuriser le nom (très basique)
    original_name = os.path.basename(file.filename or "upload.bin")

    # Création d'un id
    file_id = str(uuid.uuid4())

    if USE_FAKE:
        # Simule l'ajout
        item = {
            "file_id": file_id,
            "filename": original_name,
            "page_count": None,
            "path": None,
        }
        _FAKE_FILES.append(item)  # type: ignore
        return {
            "file_id": file_id,
            "filename": original_name,
            "page_count": None,
        }

    # Chemin sur disque: "<uuid>_<nomoriginal>"
    target = STORAGE_DIR / f"{file_id}_{original_name}"
    try:
        with open(target, "wb") as out:
            chunk = await file.read()  # pour fichiers moyens; sinon lire par chunks
            out.write(chunk)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Write failed: {e}")

    # Si PDF -> compter pages
    page_count: Optional[int] = None
    if original_name.lower().endswith(".pdf"):
        page_count = _safe_count_pdf_pages(target)

    # Enregistrer en mémoire
    FILES_DB[file_id] = {
        "file_id": file_id,
        "filename": original_name,
        "page_count": page_count,
        "path": str(target),
    }

    return {
        "file_id": file_id,
        "filename": original_name,
        "page_count": page_count,
    }


@router.get("/{file_id}", summary="Obtenir les métadonnées d'un fichier")
async def get_file_meta(file_id: str) -> Dict[str, Optional[str]]:
    store = _current_store()
    item = store.get(file_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    return {
        "file_id": item["file_id"],
        "filename": item["filename"],
        "page_count": item.get("page_count"),
    }


@router.get("/{file_id}/download", response_class=FileResponse, summary="Télécharger un fichier")
async def download_file(file_id: str):
    if USE_FAKE:
        raise HTTPException(status_code=400, detail="Download unavailable in mock mode")

    item = FILES_DB.get(file_id)
    if not item or not item.get("path"):
        raise HTTPException(status_code=404, detail="File not found")

    path = Path(item["path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="File missing on disk")

    return FileResponse(path, filename=item["filename"] or path.name)


@router.delete("/{file_id}", summary="Supprimer un fichier")
async def delete_file(file_id: str) -> Dict[str, str]:
    store = _current_store()
    item = store.get(file_id)
    if not item:
        raise HTTPException(status_code=404, detail="File not found")

    if USE_FAKE:
        # supprimer de la liste mock
        idx = next((i for i, it in enumerate(_FAKE_FILES) if it["file_id"] == file_id), None)  # type: ignore
        if idx is not None:
            _FAKE_FILES.pop(idx)
        return {"status": "deleted"}

    # supprimer sur disque si présent
    path_str = item.get("path")
    if path_str:
        try:
            p = Path(path_str)
            if p.exists():
                p.unlink(missing_ok=True)
        except Exception:
            # on ignore l'erreur disque pour ne pas bloquer
            pass

    # supprimer en mémoire
    FILES_DB.pop(file_id, None)
    return {"status": "deleted"}
