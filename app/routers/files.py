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
VEILLE_RECENTES = 60


def _iso(d) -> str | None:
    return d.isoformat() if d else None


def _section_mes_fichiers(db: Session, user: User) -> dict:
    rows = db.execute(
        select(FileModel).where(FileModel.user_id == user.id).order_by(desc(FileModel.created_at))
    ).scalars().all()
    return {
        "id": "mes-fichiers",
        "label": "Mes fichiers",
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
            }
            for r in rows
        ],
        "quota": {"used": len(rows), "limit": get_user_limit(user, "files_total")},
    }


def _par_dossier(items: list[dict], cle: str, prefixe: str) -> list[dict]:
    """Range des fichiers en sous-dossiers, dans l'ordre d'apparition."""
    dossiers: dict[str, dict] = {}
    for it in items:
        nom = it.pop(cle, None) or "Autres"
        d = dossiers.setdefault(nom, {"id": f"{prefixe}-{len(dossiers)}", "type": "folder", "label": nom, "children": []})
        d["children"].append(it)
    return list(dossiers.values())


def _section_prepa(db: Session, user: User) -> dict | None:
    from app.routers.lib import adjuris  # import local : évite un cycle

    rows = adjuris(db, user)["items"]
    if not rows:
        return None
    items = [
        {
            "id": f"r-{r['id']}",
            "type": "pdf",
            "label": r["titre"],
            "size_bytes": r["size_bytes"],
            "date": r["created_at"],
            "src": r["url"],
            "meta": {"description": r["description"], "auteur": "Ton chargé de TD" if r["professeur"] else "NAVIRE"},
            "_dossier": r["matiere_label"],
        }
        for r in rows
    ]
    return {"id": "prepa", "label": "Prép'AdJuris", "icon": "school", "items": _par_dossier(items, "_dossier", "prepa")}


def _section_devoirs(db: Session, user: User) -> dict | None:
    from app.db.models import PrepaAdjurisDevoir
    from app.services.devoirs import devoir_row

    rows = db.execute(
        select(PrepaAdjurisDevoir)
        .where(PrepaAdjurisDevoir.user_id == user.id)
        .order_by(PrepaAdjurisDevoir.created_at.desc(), PrepaAdjurisDevoir.id.desc())
    ).scalars().all()
    if not rows:
        return None
    items = []
    for d in rows:
        r = devoir_row(db, d)
        meta = {
            "statut": "Corrigé" if r["statut"] == "corrige" else "En attente de correction",
            "note": r["note"] or None,
            "commentaire": r["commentaire"] or None,
            "corrige_par": r["corrige_par"],
        }
        # La copie mise en forme par NAVIRE, en PDF : une fois corrigée, avec
        # les révisions, les commentaires, la note et l'appréciation du
        # correcteur (même rendu que le téléchargement depuis l'onglet Prépa).
        corrigee = r["statut"] == "corrige"
        items.append({
            "id": f"d-{d.id}", "type": "pdf", "label": f"{r['titre']} ({'corrigée' if corrigee else 'copie'})",
            "date": r["corrige_le"] if corrigee else r["created_at"],
            "src": f"/lib/prepa/devoirs/{d.id}/export?format=pdf", "meta": meta, "_dossier": r["matiere_label"],
        })
        if r["correction"]:
            items.append({
                "id": f"c-{d.id}", "type": "pdf", "label": f"{r['titre']} (PDF du correcteur)",
                "date": r["corrige_le"], "src": f"/lib/prepa/devoirs/{d.id}/correction",
                "meta": meta, "_dossier": r["matiere_label"],
            })
    return {"id": "devoirs", "label": "Mes devoirs", "icon": "check", "items": _par_dossier(items, "_dossier", "devoirs")}


def _section_prepasserelle(db: Session, user: User) -> dict | None:
    """Prépa NAVIRE (PREPASSERELLE) : cours, sujets et corrigés publiés de l'année, mes rendus."""
    from app.core.limits import has_active_prepa_access
    from app.db.models import PrepaCourse, PrepaExercise, PrepaSubmission

    if not has_active_prepa_access(user) or not user.prepa_annee:
        return None
    annee = user.prepa_annee
    dossiers = []

    cours = db.execute(
        select(PrepaCourse)
        .where(PrepaCourse.annee == annee, PrepaCourse.is_published.is_(True))
        .order_by(PrepaCourse.ordre, PrepaCourse.id)
    ).scalars().all()
    for c in cours:
        enfants = [
            {"id": f"pc-{f.id}", "type": "pdf", "label": f.titre or f.filename_original, "size_bytes": f.size_bytes,
             "date": _iso(c.updated_at), "src": f"/prepa/courses/files/{f.id}/download",
             "meta": {"description": c.description}}
            for f in c.files
        ]
        if enfants:
            dossiers.append({"id": f"pc-dir-{c.id}", "type": "folder", "label": c.titre, "children": enfants})

    exos = db.execute(
        select(PrepaExercise)
        .where(PrepaExercise.annee == annee, PrepaExercise.is_published.is_(True))
        .order_by(PrepaExercise.week_number, PrepaExercise.id)
    ).scalars().all()
    rendus = {
        r.exercise_id: r for r in db.execute(
            select(PrepaSubmission).where(PrepaSubmission.user_id == user.id)
        ).scalars().all()
    }
    exo_items = []
    for e in exos:
        nom = f"S{e.week_number} · {e.titre}" if e.week_number else e.titre
        if e.subject_path:
            exo_items.append({"id": f"ps-{e.id}", "type": "pdf", "label": f"{nom} (sujet)",
                              "src": f"/prepa/exercises/{e.id}/subject/download",
                              "meta": {"description": e.consigne}})
        if e.corrige_published and e.corrige_path:
            exo_items.append({"id": f"pk-{e.id}", "type": "pdf", "label": f"{nom} (corrigé)",
                              "src": f"/prepa/exercises/{e.id}/corrige/download"})
        r = rendus.get(e.id)
        if r:
            exo_items.append({
                "id": f"pr-{r.id}", "type": "pdf", "label": f"{nom} (ma copie)", "size_bytes": r.size_bytes,
                "date": _iso(r.submitted_at), "src": f"/prepa/submissions/{r.id}/download",
                "meta": {"statut": "Corrigé" if r.corrected_at else "En attente de correction",
                         "note": r.note or None, "commentaire": r.feedback or None},
            })
    if exo_items:
        dossiers.append({"id": "pe-dir", "type": "folder", "label": "Exercices", "children": exo_items})
    if not dossiers:
        return None
    return {"id": "prepasserelle", "label": "Prépa NAVIRE", "icon": "school", "items": dossiers}


def _section_veille(db: Session, user: User) -> dict | None:
    from app.db.models import VeilleBookmark, VeilleItem
    from app.routers.veille import RUBRIQUES, _date_key, _hub_item, _read_ids, _saved_ids

    read_ids, saved_ids = _read_ids(db, user), _saved_ids(db, user)

    def actu(it: VeilleItem, prefixe: str) -> dict:
        h = _hub_item(it, read_ids, saved_ids)
        return {
            "id": f"{prefixe}-{it.id}", "type": "actu", "label": h["title"], "date": h["published_at"],
            "meta": {
                "essentiel": h["essentiel"], "impact": h["impact"], "rubrique": h["category_label"],
                # Article : on montre qui l'a publié plutôt qu'une source
                "source_name": (f"Publié par {h['author']['name']}" if h.get("kind") == "article" and h.get("author") else h["source_name"]),
                "source_url": h["source_url"],
                "minutes": h["reading_minutes"], "lu": h["is_read"],
                # "article" : icône des articles, lien navire-ai.com/actus/NA<id>
                "kind": h.get("kind", "actu"), "url": h.get("url"),
            },
        }

    enregistrees = db.execute(
        select(VeilleItem)
        .join(VeilleBookmark, VeilleBookmark.item_id == VeilleItem.id)
        .where(VeilleBookmark.user_id == user.id)
        .order_by(_date_key().desc())
    ).scalars().all()
    recentes = db.execute(
        select(VeilleItem).order_by(_date_key().desc(), VeilleItem.id.desc()).limit(VEILLE_RECENTES)
    ).scalars().all()
    if not enregistrees and not recentes:
        return None

    dossiers = []
    if enregistrees:
        dossiers.append({"id": "veille-saved", "type": "folder", "label": "Enregistrées",
                         "children": [actu(it, "vs") for it in enregistrees]})
    par_rub: dict[str, list] = {}
    for it in recentes:
        par_rub.setdefault(it.category, []).append(actu(it, "v"))
    for cat, enfants in par_rub.items():
        dossiers.append({"id": f"veille-{cat}", "type": "folder",
                         "label": RUBRIQUES.get(cat, cat.capitalize()), "children": enfants})
    return {"id": "veille", "label": "Veille juridique", "icon": "news", "items": dossiers}


def _section_lib(db: Session, user: User) -> dict | None:
    from app.db.models import LibDocument
    from app.routers.lib import KINDS

    labels = {"cours": "Cours", "fiche": "Fiches", "fiche_arret": "Fiches d'arrêt", "commentaire": "Commentaires",
              "dissertation": "Dissertations", "cas_pratique": "Cas pratiques"}
    docs = db.execute(
        select(LibDocument)
        .where(
            LibDocument.owner_id.is_(None),
            LibDocument.prepa_ressource_id.is_(None),
            LibDocument.is_published.is_(True),
            LibDocument.kind.in_(KINDS),
        )
        .order_by(LibDocument.ordre, LibDocument.id)
    ).scalars().all()
    if not docs:
        return None
    dossiers: dict[str, dict] = {}
    for d in docs:
        dossier = dossiers.setdefault(d.kind, {"id": f"lib-{d.kind}", "type": "folder",
                                               "label": labels.get(d.kind, d.kind), "children": []})
        dossier["children"].append({
            "id": f"l-{d.id}", "type": "lib", "label": d.titre, "date": _iso(d.updated_at),
            "link": f"/lib?doc={d.id}",
            "read": f"/lib/documents/{d.id}?mode={'plus' if d.is_plus else 'lib'}",
            "meta": {"matiere": d.matiere, "niveau": d.niveau, "description": d.description or d.sous_titre,
                     "plus": d.is_plus},
        })
    ordre = [k for k in labels if k in dossiers]
    return {"id": "lib", "label": "NAVIRE Lib", "icon": "book", "items": [dossiers[k] for k in ordre]}


@router.get("/explorer")
def explorer(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Arborescence de l'onglet Fichiers : uniquement ce que l'utilisateur a le
    droit de lire. Ses PDF (seule section modifiable : ajout / suppression),
    les documents Prép'AdJuris de ses matières, ses copies et corrigés, la
    veille (enregistrées + dernières actus) et le catalogue NAVIRE Lib.
    Chaque fichier pointe vers une route qui revérifie l'accès côté serveur.
    """
    purge_expired_files(db, current_user)
    sections = [_section_mes_fichiers(db, current_user)]
    for build in (_section_prepa, _section_devoirs, _section_prepasserelle, _section_veille, _section_lib):
        try:
            s = build(db, current_user)
        except Exception as e:  # une source en panne ne vide pas tout l'onglet
            db.rollback()
            print(f"⚠️ Explorateur : section {build.__name__} indisponible : {e}")
            continue
        if s:
            sections.append(s)
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
