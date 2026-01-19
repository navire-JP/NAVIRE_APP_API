from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File as FastAPIFile
from sqlalchemy.orm import Session
from sqlalchemy import select, desc, delete

from app.db.database import get_db
from app.db.models import User, File as FileModel
from app.routers.auth import get_current_user
from app.core.config import USER_FILES_DIR, MAX_UPLOAD_BYTES

router = APIRouter(prefix="/files", tags=["files"])


# ============================================================
# Utils
# ============================================================

def _is_pdf(upload: UploadFile) -> bool:
    name = (upload.filename or "").lower()
    ct = (upload.content_type or "").lower()
    return name.endswith(".pdf") or ct in (
        "application/pdf",
        "application/x-pdf",
        "application/octet-stream",
    )


def compute_file_entitlements(user: User) -> tuple[int, int | None]:
    """
    Retourne (files_limit, ttl_hours)
    """
    if user.is_admin or user.plan == "navire_ai_plus":
        return 10, None

    if user.plan == "navire_ai":
        return 3, None

    # free
    return 1, 24


def purge_expired_files(db: Session, user: User) -> int:
    """
    Supprime les fichiers expirés (DB + disque).
    Retourne le nombre supprimé.
    """
    now = datetime.utcnow()

    rows = db.execute(
        select(FileModel).where(
            FileModel.user_id == user.id,
            FileModel.expires_at.is_not(None),
            FileModel.expires_at < now,
        )
    ).scalars().all()

    deleted = 0

    for row in rows:
        # Suppression disque
        try:
            p = Path(row.path)
            if p.exists():
                p.unlink()
        except Exception:
            pass

        db.delete(row)
        deleted += 1

    if deleted:
        db.commit()

    return deleted


def count_active_files(db: Session, user: User) -> int:
    """
    Compte les fichiers non expirés.
    """
    now = datetime.utcnow()

    rows = db.execute(
        select(FileModel).where(
            FileModel.user_id == user.id,
            (
                (FileModel.expires_at.is_(None)) |
                (FileModel.expires_at > now)
            ),
        )
    ).scalars().all()

    return len(rows)


# ============================================================
# Routes
# ============================================================

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

    # 🔥 Purge automatique des expirés
    purge_expired_files(db, current_user)

    # 🔒 Quota
    files_limit, ttl_hours = compute_file_entitlements(current_user)
    used = count_active_files(db, current_user)

    if used >= files_limit:
        raise HTTPException(
            status_code=403,
            detail=f"Quota atteint ({used}/{files_limit} fichiers autorisés).",
        )

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

    # ⏳ Expiration
    expires_at = None
    if ttl_hours is not None:
        expires_at = datetime.utcnow() + timedelta(hours=ttl_hours)

    # Enregistrement DB
    row = FileModel(
        user_id=current_user.id,
        filename_original=file.filename,
        filename_stored=stored_name,
        path=str(stored_path),
        size_bytes=total,
        expires_at=expires_at,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return {
        "id": row.id,
        "filename_original": row.filename_original,
        "size_bytes": row.size_bytes,
        "created_at": row.created_at,
        "expires_at": row.expires_at,
        "quota": {
            "used": used + 1,
            "limit": files_limit,
        },
    }


@router.get("")
def list_files(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 🔥 Purge auto
    purge_expired_files(db, current_user)

    rows = db.execute(
        select(FileModel)
        .where(FileModel.user_id == current_user.id)
        .order_by(desc(FileModel.created_at))
    ).scalars().all()

    files_limit, _ = compute_file_entitlements(current_user)

    return {
        "items": [
            {
                "id": r.id,
                "filename_original": r.filename_original,
                "size_bytes": r.size_bytes,
                "created_at": r.created_at,
                "expires_at": r.expires_at,
            }
            for r in rows
        ],
        "quota": {
            "used": len(rows),
            "limit": files_limit,
        },
    }


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

    # Suppression disque
    try:
        p = Path(row.path)
        if p.exists():
            p.unlink()
    except Exception:
        pass

    # Suppression DB
    db.delete(row)
    db.commit()

    return {"ok": True}
