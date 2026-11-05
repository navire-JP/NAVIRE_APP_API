from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File as FastAPIFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import select, desc

from app.db.database import get_db
from app.db.models import User, File as FileModel
from app.routers.auth import get_current_user
from app.core.config import USER_FILES_DIR, MAX_UPLOAD_BYTES
from app.core.limits import check_file_limit, get_file_ttl, get_user_limit
from app.db.models import NavireChunk  # NAVIRE — nettoyage des chunks liés aux PDF
from app.services.navire_ingest import ingest_user_file  # NAVIRE — indexation RAG des PDF perso

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


def purge_expired_files(db: Session, user: User) -> int:
    """
    Supprime les fichiers expirés (DB + disque).
    Retourne le nombre supprimé.
    """
    now = datetime.now(timezone.utc)

    rows = db.execute(
        select(FileModel).where(
            FileModel.user_id == user.id,
            FileModel.expires_at.is_not(None),
            FileModel.expires_at < now,
        )
    ).scalars().all()

    deleted = 0
    for row in rows:
        try:
            p = Path(row.path)
            if p.exists():
                p.unlink()
        except Exception:
            pass
        # NAVIRE : retirer les chunks indexés de ce PDF expiré
        try:
            db.query(NavireChunk).filter(
                NavireChunk.source_type == "pdf_user",
                NavireChunk.source_id == row.id,
            ).delete()
        except Exception as e:
            print(f"⚠️ Purge chunks NAVIRE échouée pour file {row.id}: {e}")
        db.delete(row)
        deleted += 1

    if deleted:
        db.commit()

    return deleted


def count_active_files(db: Session, user: User) -> int:
    """Compte les fichiers non expirés de l'utilisateur."""
    now = datetime.now(timezone.utc)

    return db.execute(
        select(FileModel).where(
            FileModel.user_id == user.id,
            (FileModel.expires_at.is_(None)) | (FileModel.expires_at > now),
        )
    ).scalars().all().__len__()


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

    # 🔥 Purge automatique des expirés avant de compter
    purge_expired_files(db, current_user)

    # 🔒 Quota — délègue à limits.py (lève 403 si dépassé)
    check_file_limit(current_user, db)

    # Compte actuel pour la réponse quota
    used = count_active_files(db, current_user)
    files_limit = get_user_limit(current_user, "files_total")

    # Dossier user : storage/UserFiles/<user_id>/
    user_dir = Path(USER_FILES_DIR) / str(current_user.id)
    user_dir.mkdir(parents=True, exist_ok=True)

    stored_name = f"{uuid4().hex}.pdf"
    stored_path = user_dir / stored_name

    # Écriture + contrôle taille max (stream)
    total = 0
    try:
        with open(stored_path, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)  # 1 MB
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

    # ⏳ Expiration selon le plan
    ttl_hours = get_file_ttl(current_user)
    expires_at = None
    if ttl_hours is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)

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

    # NAVIRE : indexer le PDF pour le RAG (cloisonné par user via owner_user_id).
    # Non bloquant : un échec d'indexation ne doit pas faire échouer l'upload.
    try:
        ingest_user_file(db, row)
    except Exception as e:
        print(f"⚠️ Indexation NAVIRE échouée pour file {row.id}: {e}")

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
@router.get("/")
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

    files_limit = get_user_limit(current_user, "files_total")

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


# ============================================================
# Explorateur (onglet Fichiers) : tout ce que l'utilisateur peut lire
# ============================================================

_EMBED = Path(__file__).resolve().parents[2] / "embeds" / "fichiers.html"

# Onglet Fichiers = « mon espace » : ce qui est à l'élève (déposé, rendu, reçu,
# mis de côté). Le contenu NAVIRE a sa maison ailleurs (cours → Bibliothèque,
# fil d'actus → Actus) ; ici il n'apparaît qu'en raccourci ou en nœud
# fantôme de la carte, jamais en copie.

def _iso(d) -> str | None:
    return d.isoformat() if d else None


def _section_mes_pdf(db: Session, user: User) -> dict:
    from app.db.models import LibDocument

    rows = db.execute(
        select(FileModel).where(FileModel.user_id == user.id).order_by(desc(FileModel.created_at))
    ).scalars().all()
    # Chaque PDF a son double « perso » dans la Lib (créé par /lib/catalogue) : bouton « Étudier dans la Lib ».
    try:
        from app.routers.lib import _sync_perso
        _sync_perso(db, user)
    except Exception as e:
        db.rollback()
        print(f"⚠️ Explorateur : synchro Lib perso impossible : {e}")
    lib_ids = {
        d.source_file_id: d.id for d in db.execute(
            select(LibDocument).where(LibDocument.owner_id == user.id)
        ).scalars().all()
    }
    return {
        "id": "mes-pdf",
        "label": "Mes PDF",
        "icon": "user",
        "upload": True,
        "items": [
            {
                "id": f"f-{r.id}",
                "type": "pdf",
                "label": r.filename_original,
                "size_bytes": r.size_bytes,
                "date": _iso(r.created_at),
                "expires_at": _iso(r.expires_at),
                "src": f"/files/{r.id}/download?inline=1",
                "delete": f"/files/{r.id}",
                "study": f"/lib?doc={lib_ids[r.id]}" if r.id in lib_ids else None,
            }
            for r in rows
        ],
        "quota": {"used": len(rows), "limit": get_user_limit(user, "files_total")},
    }


def _section_copies(db: Session, user: User) -> dict:
    """Copies rendues et corrigés reçus : Prép'AdJuris (et l'ancienne Prépa NAVIRE si l'accès est actif)."""
    from app.core.limits import has_active_prepa_access
    from app.db.models import PrepaAdjurisDevoir, PrepaExercise, PrepaSubmission
    from app.services.devoirs import devoir_row

    dossiers: dict[str, dict] = {}

    def dossier(nom: str) -> list:
        d = dossiers.setdefault(nom, {"id": f"copies-{len(dossiers)}", "type": "folder", "label": nom, "children": []})
        return d["children"]

    for d in db.execute(
        select(PrepaAdjurisDevoir)
        .where(PrepaAdjurisDevoir.user_id == user.id)
        .order_by(PrepaAdjurisDevoir.created_at.desc(), PrepaAdjurisDevoir.id.desc())
    ).scalars().all():
        r = devoir_row(db, d)
        meta = {
            "statut": "Corrigé" if r["statut"] == "corrige" else "En attente de correction",
            "note": r["note"] or None, "commentaire": r["commentaire"] or None, "corrige_par": r["corrige_par"],
        }
        enfants = dossier(r["matiere_label"])
        enfants.append({"id": f"d-{d.id}", "type": "pdf", "label": f"{r['titre']} (copie)", "size_bytes": r["size_bytes"],
                        "date": r["created_at"], "src": f"/lib/prepa/devoirs/{d.id}/fichier", "meta": meta})
        if r["correction"]:
            enfants.append({"id": f"c-{d.id}", "type": "pdf", "label": f"{r['titre']} (corrigé)", "date": r["corrige_le"],
                            "src": f"/lib/prepa/devoirs/{d.id}/correction", "meta": meta})

    if has_active_prepa_access(user):
        for sub, exo in db.execute(
            select(PrepaSubmission, PrepaExercise)
            .join(PrepaExercise, PrepaExercise.id == PrepaSubmission.exercise_id)
            .where(PrepaSubmission.user_id == user.id)
            .order_by(PrepaSubmission.submitted_at.desc())
        ).all():
            nom = f"S{exo.week_number} · {exo.titre}" if exo.week_number else exo.titre
            dossier("Prépa NAVIRE").append({
                "id": f"pr-{sub.id}", "type": "pdf", "label": f"{nom} (copie)", "size_bytes": sub.size_bytes,
                "date": _iso(sub.submitted_at), "src": f"/prepa/submissions/{sub.id}/download",
                "meta": {"statut": "Corrigé" if sub.corrected_at else "En attente de correction",
                         "note": sub.note or None, "commentaire": sub.feedback or None},
            })

    return {
        "id": "copies", "label": "Mes copies", "icon": "check",
        "action": {"label": "Déposer une copie", "link": "/lib?prepa=devoirs"},
        "items": list(dossiers.values()),
    }


def _section_enregistres(db: Session, user: User) -> dict:
    """Raccourcis : actus mises de côté (elles s'ouvrent dans Actus)."""
    from app.db.models import VeilleBookmark, VeilleItem
    from app.routers.veille import RUBRIQUES

    actus = db.execute(
        select(VeilleItem)
        .join(VeilleBookmark, VeilleBookmark.item_id == VeilleItem.id)
        .where(VeilleBookmark.user_id == user.id)
        .order_by(VeilleBookmark.id.desc())
    ).scalars().all()
    return {
        "id": "enregistres", "label": "Enregistrés", "icon": "bookmark",
        "items": [
            {
                "id": f"a-{it.id}", "type": "actu", "label": it.title,
                "date": _iso(it.published_at or it.created_at), "link": f"actu:{it.id}",
                "meta": {"essentiel": it.essentiel, "impact": it.impact,
                         "rubrique": RUBRIQUES.get(it.category, it.category.capitalize()),
                         "source_name": it.source_name, "source_url": it.source_url},
            }
            for it in actus
        ],
    }


@router.get("/explorer")
def explorer(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Onglet Fichiers = l'espace de l'élève, en trois sections :
      - Mes PDF      : ses PDF (seule écriture possible : ajout / suppression),
                       chacun avec son lien « Étudier dans la Lib »
      - Mes copies   : copies rendues et corrigés reçus
      - Enregistrés  : raccourcis vers les actus mises de côté
    Le contenu NAVIRE (cours Lib, documents Prépa, fil d'actus) n'est pas
    recopié ici : il a sa maison dans la Bibliothèque et dans Actus.
    Chaque fichier pointe vers une route qui revérifie l'accès côté serveur.
    """
    purge_expired_files(db, current_user)
    sections = [_section_mes_pdf(db, current_user)]
    for build in (_section_copies, _section_enregistres):
        try:
            sections.append(build(db, current_user))
        except Exception as e:  # une source en panne ne vide pas tout l'onglet
            db.rollback()
            print(f"⚠️ Explorateur : section {build.__name__} indisponible : {e}")
    return {"sections": sections, "max_upload_bytes": MAX_UPLOAD_BYTES}


@router.get("/embed", response_class=HTMLResponse, include_in_schema=False)
def embed():
    contenu = _EMBED.read_text(encoding="utf-8")
    contenu = contenu.replace('API: "https://navire-app-api.onrender.com"', 'API: ""')
    return HTMLResponse(
        '<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>NAVIRE Fichiers</title></head>'
        f'<body style="margin:0;background:transparent">{contenu}</body></html>'
    )


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

    # NAVIRE : retirer les chunks indexés de ce PDF
    try:
        db.query(NavireChunk).filter(
            NavireChunk.source_type == "pdf_user",
            NavireChunk.source_id == row.id,
        ).delete()
    except Exception as e:
        print(f"⚠️ Suppression chunks NAVIRE échouée pour file {row.id}: {e}")

    db.delete(row)
    db.commit()

    return {"ok": True}

class RenamePayload(BaseModel):
    filename: str = Field(..., min_length=1, max_length=200)


def _get_owned_file(db: Session, user: User, file_id: int) -> FileModel:
    row = db.execute(
        select(FileModel).where(FileModel.id == file_id)
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Fichier introuvable.")
    if row.user_id != user.id:
        raise HTTPException(status_code=403, detail="Accès interdit.")
    return row


@router.patch("/{file_id}")
def rename_file(
    file_id: int,
    payload: RenamePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Renomme le fichier (nom affiché uniquement, le fichier stocké ne bouge pas)."""
    row = _get_owned_file(db, current_user, file_id)

    name = payload.filename.strip().replace("/", "-").replace("\\", "-")
    if not name:
        raise HTTPException(status_code=400, detail="Nom invalide.")
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    row.filename_original = name

    # NAVIRE cite le nom du fichier : on le tient à jour dans les chunks.
    for chunk in db.query(NavireChunk).filter(
        NavireChunk.source_type == "pdf_user",
        NavireChunk.source_id == row.id,
    ):
        chunk.extra = {**(chunk.extra or {}), "filename": name}

    db.commit()
    return {"id": row.id, "filename_original": row.filename_original}


@router.get("/{file_id}/download")
def download_file(
    file_id: int,
    inline: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """`inline=1` : le PDF s'affiche dans le lecteur (onglet Fichiers) au lieu d'être téléchargé."""
    row = _get_owned_file(db, current_user, file_id)
    p = Path(row.path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="Fichier absent du serveur.")
    return FileResponse(
        p, media_type="application/pdf", filename=row.filename_original,
        content_disposition_type="inline" if inline else "attachment",
    )
