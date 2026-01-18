from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File as FastAPIFile
from sqlalchemy.orm import Session
from sqlalchemy import select, desc

from app.db.database import get_db
from app.db.models import User, File as FileModel
from app.routers.auth import get_current_user  # pattern existant
from app.core.config import USER_FILES_DIR, MAX_UPLOAD_BYTES

router = APIRouter(prefix="/files", tags=["files"])


def _is_pdf(upload: UploadFile) -> bool:
    # On accepte si content-type PDF OU extension .pdf (certains clients mettent octet-stream)
    name = (upload.filename or "").lower()
    ct = (upload.content_type or "").lower()
    return name.endswith(".pdf") or ct in ("application/pdf", "application/x-pdf", "application/octet-stream")


@router.post("/upload")
async def upload_file(
    file: UploadFile = FastAPIFile(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="Fichier manquant.")

    if not _is_pdf(file):
        raise HTTPException(status_code=400, detail="Seuls les fichiers PDF sont acceptés.")

    # Dossier user : storage/UserFiles/<user_id>/
    user_dir = Path(USER_FILES_DIR) / str(current_user.id)
    user_dir.mkdir(parents=True, exist_ok=True)

    # Nom stocké unique
    stored_name = f"{uuid4().hex}.pdf"
    stored_path = user_dir / stored_name

    # Ecriture + contrôle taille max (stream)
    total = 0
    try:
        with open(stored_path, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    # cleanup
                    try:
                        out.close()
                    except Exception:
                        pass
                    if stored_path.exists():
                        stored_path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"Fichier trop volumineux (max {MAX_UPLOAD_BYTES} bytes).",
                    )
                out.write(chunk)
    finally:
        await file.close()

    # Enregistrement DB
    row = FileModel(
        user_id=current_user.id,
        filename_original=file.filename,
        filename_stored=stored_name,
        path=str(stored_path),
        size_bytes=total,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return {
        "id": row.id,
        "filename_original": row.filename_original,
        "size_bytes": row.size_bytes,
        "created_at": row.created_at,
    }


@router.get("")
def list_files(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = db.execute(
        select(FileModel)
        .where(FileModel.user_id == current_user.id)
        .order_by(desc(FileModel.created_at))
    ).scalars().all()

    return [
        {
            "id": r.id,
            "filename_original": r.filename_original,
            "size_bytes": r.size_bytes,
            "created_at": r.created_at,
        }
        for r in rows
    ]


@router.delete("/{file_id}")
def delete_file(
    file_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = db.execute(
        select(FileModel).where(FileModel.id == file_id)
    ).scalar_one_or_none()

    if not row:
        raise HTTPException(status_code=404, detail="Fichier introuvable.")

    if row.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Accès interdit.")

    # Supprimer le fichier disque
    try:
        p = Path(row.path)
        if p.exists():
            p.unlink()
    except Exception:
        # On ne bloque pas la suppression DB si le disque a un souci,
        # mais on remonte une erreur si tu préfères strict :
        # raise HTTPException(status_code=500, detail="Erreur suppression fichier.")
        pass

    # Supprimer la row DB
    db.delete(row)
    db.commit()

    return {"ok": True}
