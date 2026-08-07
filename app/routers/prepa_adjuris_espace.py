"""
app/routers/prepa_adjuris_espace.py
====================================
Espace pédagogique Prép'AdJuris : ressources PDF, séances, accès accordés à la
main. Volontairement séparé de prepa_adjuris.py, qui reste centré sur le
paiement (Stripe) et la liaison Discord.

Étudiant (auth NAVIRE) :
  GET  /prepa/adjuris/espace                    → matières, méthodo, prochain cours
  GET  /prepa/adjuris/ressources/{id}/download  → PDF, accès vérifié côté serveur
  GET  /prepa/adjuris/seances                   → séances visibles (calendrier)

Admin (header X-Admin-Code) :
  GET    /prepa/adjuris/admin/etudiants
  GET    /prepa/adjuris/admin/ressources
  POST   /prepa/adjuris/admin/ressources                  (upload PDF)
  PATCH  /prepa/adjuris/admin/ressources/{id}
  DELETE /prepa/adjuris/admin/ressources/{id}
  GET    /prepa/adjuris/admin/ressources/{id}/download
  GET    /prepa/adjuris/admin/seances
  POST   /prepa/adjuris/admin/seances
  PATCH  /prepa/adjuris/admin/seances/{id}
  DELETE /prepa/adjuris/admin/seances/{id}
  POST   /prepa/adjuris/admin/acces               (accès sans paiement, limité)
  POST   /prepa/adjuris/admin/acces/revoquer

expirer_acces_adjuris() est appelée par le job horaire de subscriptions.py.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    UploadFile,
    File as FastAPIFile,
    Form,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import (
    PrepaAdjurisEnrollment,
    PrepaAdjurisInscription,
    PrepaAdjurisRessource,
    PrepaAdjurisSeance,
    User,
)
from app.core.prepa_storage import (
    save_prepa_pdf,
    delete_prepa_file,
    PREPA_ADJURIS_DIR,
    PREPA_COURSE_MAX_BYTES,
)
from app.core.config import DISCORD_GUILD_ID, DISCORD_PREPA_ADJURIS_CHANNEL_ID
from app.core.prepa_adjuris_config import (
    PREPA_PRICES,
    PREPA_MATIERE_NAMES,
    matiere_niveau,
)
from app.routers.auth import get_current_user
from app.routers.admin import verify_admin_code
from app.bot_discord.role_sync import (
    assign_adjuris_role_sync,
    remove_adjuris_role_sync,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/prepa/adjuris", tags=["prepa-adjuris-espace"])

VALID_NIVEAUX = {"L1", "L2", "L3"}


def _discord_url() -> str | None:
    if DISCORD_GUILD_ID and DISCORD_PREPA_ADJURIS_CHANNEL_ID:
        return f"https://discord.com/channels/{DISCORD_GUILD_ID}/{DISCORD_PREPA_ADJURIS_CHANNEL_ID}"
    return None


# ============================================================
# Schemas
# ============================================================

class AccesIn(BaseModel):
    email: str
    matiere_key: str
    jours: int | None = 7
    avec_grade: bool = True   # passe aussi User.plan à "prepa" (rôle Discord)


class AccesRevokeIn(BaseModel):
    email: str
    matiere_key: str


class RessourceUpdateIn(BaseModel):
    titre: str | None = None
    description: str | None = None
    ordre: int | None = None
    is_published: bool | None = None


class SeanceIn(BaseModel):
    niveau: str
    date_debut: datetime
    matiere_key: str | None = None   # vide → séance commune au niveau
    titre: str | None = ""
    duree_minutes: int | None = 60
    lien: str | None = ""


class SeanceUpdateIn(BaseModel):
    titre: str | None = None
    date_debut: datetime | None = None
    duree_minutes: int | None = None
    lien: str | None = None
    statut: str | None = None        # prevue | annulee


# ============================================================
# Sérialisation
# ============================================================

def _serialize_ressource(r: PrepaAdjurisRessource) -> dict:
    return {
        "id": r.id,
        "titre": r.titre,
        "description": r.description,
        "filename_original": r.filename_original,
        "size_bytes": r.size_bytes,
        "ordre": r.ordre,
        "is_published": r.is_published,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _serialize_seance(s: PrepaAdjurisSeance) -> dict:
    return {
        "id": s.id,
        "niveau": s.niveau,
        "matiere_key": s.matiere_key,
        "matiere_label": (
            PREPA_MATIERE_NAMES.get(s.matiere_key, s.matiere_key)
            if s.matiere_key else "Séance commune"
        ),
        "titre": s.titre,
        "date_debut": s.date_debut.isoformat() if s.date_debut else None,
        "duree_minutes": s.duree_minutes,
        "lien": s.lien,
        "statut": s.statut,
    }


# ============================================================
# Espace étudiant
# ============================================================

@router.get("/espace")
def mon_espace_adjuris(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Contenu complet de l'espace étudiant : ses matières et celles de son
    niveau (verrouillées s'il n'y est pas inscrit), le bloc méthodo, et la
    prochaine séance.

    Le niveau est figé par les matières souscrites : un L1 ne voit que des
    matières de L1. Tant qu'il n'a rien payé, l'espace est vide et le front
    renvoie vers l'inscription.
    """
    enrollments = db.execute(
        select(PrepaAdjurisEnrollment).where(
            PrepaAdjurisEnrollment.user_id == user.id,
            PrepaAdjurisEnrollment.status.in_(("active", "payment_failed")),
        )
    ).scalars().all()

    actives = {e.matiere_key for e in enrollments if e.status == "active"}
    niveaux = sorted({matiere_niveau(e.matiere_key) for e in enrollments})
    niveau = niveaux[0] if niveaux else None

    if not niveau:
        return {
            "niveau": None, "inscrit": False, "matieres": [],
            "methodo": [], "prochaine_seance": None,
            "discord_url": _discord_url(), "discord_lie": bool(user.discord_id),
        }

    # Ressources publiées du niveau, en une requête.
    ressources = db.execute(
        select(PrepaAdjurisRessource)
        .where(
            PrepaAdjurisRessource.niveau == niveau,
            PrepaAdjurisRessource.is_published.is_(True),
        )
        .order_by(PrepaAdjurisRessource.ordre, PrepaAdjurisRessource.id)
    ).scalars().all()

    par_matiere: dict[str, list] = {}
    methodo = []
    for r in ressources:
        if r.matiere_key:
            par_matiere.setdefault(r.matiere_key, []).append(r)
        else:
            methodo.append(r)

    # Toutes les matières du niveau : celles non souscrites sont renvoyées
    # verrouillées, sans leurs ressources.
    matieres = []
    for key in PREPA_PRICES:
        if matiere_niveau(key) != niveau:
            continue
        debloquee = key in actives
        liste = par_matiere.get(key, [])
        matieres.append({
            "matiere_key": key,
            "label": PREPA_MATIERE_NAMES.get(key, key),
            "debloquee": debloquee,
            "nb_ressources": len(liste),
            "ressources": [_serialize_ressource(r) for r in liste] if debloquee else [],
        })

    # Prochaine séance : commune au niveau, ou d'une matière souscrite.
    maintenant = datetime.now(timezone.utc)
    seances = db.execute(
        select(PrepaAdjurisSeance)
        .where(
            PrepaAdjurisSeance.niveau == niveau,
            PrepaAdjurisSeance.statut == "prevue",
            PrepaAdjurisSeance.date_debut >= maintenant,
        )
        .order_by(PrepaAdjurisSeance.date_debut)
    ).scalars().all()

    prochaine = next(
        (s for s in seances if s.matiere_key is None or s.matiere_key in actives),
        None,
    )

    return {
        "niveau": niveau,
        "inscrit": bool(actives),
        "matieres": matieres,
        "methodo": [_serialize_ressource(r) for r in methodo],
        "prochaine_seance": _serialize_seance(prochaine) if prochaine else None,
        "discord_url": _discord_url(),
        "discord_lie": bool(user.discord_id),
    }


@router.get("/ressources/{ressource_id}/download")
def telecharger_ressource(
    ressource_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Sert un PDF de l'espace. L'accès est vérifié ici, pas côté front : une
    ressource de matière n'est servie qu'à un étudiant inscrit à cette matière.
    """
    r = db.execute(
        select(PrepaAdjurisRessource).where(PrepaAdjurisRessource.id == ressource_id)
    ).scalar_one_or_none()
    if not r or not r.is_published:
        raise HTTPException(status_code=404, detail="Ressource introuvable.")

    actives = set(db.execute(
        select(PrepaAdjurisEnrollment.matiere_key).where(
            PrepaAdjurisEnrollment.user_id == user.id,
            PrepaAdjurisEnrollment.status == "active",
        )
    ).scalars().all())

    if not actives:
        raise HTTPException(status_code=403, detail="Aucune inscription active.")

    niveaux = {matiere_niveau(k) for k in actives}
    if r.matiere_key:
        # Ressource de matière : réservée aux inscrits de cette matière.
        if r.matiere_key not in actives:
            raise HTTPException(status_code=403, detail="Matière non débloquée.")
    elif r.niveau not in niveaux:
        # Méthodo : réservée aux étudiants du niveau.
        raise HTTPException(status_code=403, detail="Niveau non autorisé.")

    if not Path(r.path).exists():
        raise HTTPException(status_code=404, detail="Fichier introuvable sur le disque.")
    # inline : le front l'affiche dans une visionneuse, comme PREPASSERELLE.
    return FileResponse(
        r.path,
        media_type="application/pdf",
        filename=r.filename_original,
        content_disposition_type="inline",
    )


@router.get("/seances")
def mes_seances(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Toutes les séances visibles par l'étudiant — alimente le calendrier."""
    actives = set(db.execute(
        select(PrepaAdjurisEnrollment.matiere_key).where(
            PrepaAdjurisEnrollment.user_id == user.id,
            PrepaAdjurisEnrollment.status == "active",
        )
    ).scalars().all())
    if not actives:
        return {"niveau": None, "items": []}

    niveau = sorted({matiere_niveau(k) for k in actives})[0]
    rows = db.execute(
        select(PrepaAdjurisSeance)
        .where(PrepaAdjurisSeance.niveau == niveau)
        .order_by(PrepaAdjurisSeance.date_debut)
    ).scalars().all()

    return {
        "niveau": niveau,
        "items": [
            _serialize_seance(s) for s in rows
            if s.matiere_key is None or s.matiere_key in actives
        ],
    }


# ============================================================
# Admin — ressources (PDF matières et méthodo)
# ============================================================

@router.get("/admin/ressources", dependencies=[Depends(verify_admin_code)])
def admin_list_ressources(db: Session = Depends(get_db)):
    """Toutes les ressources, publiées ou non. Header : X-Admin-Code."""
    rows = db.execute(
        select(PrepaAdjurisRessource).order_by(
            PrepaAdjurisRessource.niveau,
            PrepaAdjurisRessource.matiere_key,
            PrepaAdjurisRessource.ordre,
            PrepaAdjurisRessource.id,
        )
    ).scalars().all()
    return {
        "total": len(rows),
        "items": [
            {
                **_serialize_ressource(r),
                "niveau": r.niveau,
                "matiere_key": r.matiere_key,
                "matiere_label": (
                    PREPA_MATIERE_NAMES.get(r.matiere_key, r.matiere_key)
                    if r.matiere_key else "Méthodologie"
                ),
            }
            for r in rows
        ],
    }


@router.post("/admin/ressources", dependencies=[Depends(verify_admin_code)])
async def admin_upload_ressource(
    file: UploadFile = FastAPIFile(...),
    niveau: str = Form(...),
    titre: str = Form(...),
    matiere_key: str = Form(""),
    description: str = Form(""),
    ordre: int = Form(0),
    db: Session = Depends(get_db),
):
    """
    Dépose un PDF. matiere_key vide → ressource du bloc méthodo du niveau.
    """
    niveau = niveau.strip().upper()
    if niveau not in VALID_NIVEAUX:
        raise HTTPException(status_code=400, detail="Niveau invalide (L1, L2 ou L3).")

    key = matiere_key.strip() or None
    if key:
        if key not in PREPA_PRICES:
            raise HTTPException(status_code=400, detail=f"Matière inconnue : {key}")
        if matiere_niveau(key) != niveau:
            raise HTTPException(
                status_code=400,
                detail=f"La matière {key} n'appartient pas au niveau {niveau}.",
            )

    meta = await save_prepa_pdf(
        file, PREPA_ADJURIS_DIR / niveau, max_bytes=PREPA_COURSE_MAX_BYTES
    )
    row = PrepaAdjurisRessource(
        niveau=niveau,
        matiere_key=key,
        titre=titre.strip() or meta["filename_original"],
        description=description.strip(),
        filename_original=meta["filename_original"],
        filename_stored=meta["filename_stored"],
        path=meta["path"],
        size_bytes=meta["size_bytes"],
        ordre=ordre,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, **_serialize_ressource(row)}


@router.patch("/admin/ressources/{ressource_id}", dependencies=[Depends(verify_admin_code)])
def admin_update_ressource(
    ressource_id: int,
    payload: RessourceUpdateIn,
    db: Session = Depends(get_db),
):
    r = db.execute(
        select(PrepaAdjurisRessource).where(PrepaAdjurisRessource.id == ressource_id)
    ).scalar_one_or_none()
    if not r:
        raise HTTPException(status_code=404, detail="Ressource introuvable.")

    if payload.titre is not None:
        r.titre = payload.titre.strip()
    if payload.description is not None:
        r.description = payload.description.strip()
    if payload.ordre is not None:
        r.ordre = payload.ordre
    if payload.is_published is not None:
        r.is_published = payload.is_published
    db.commit()
    db.refresh(r)
    return {"ok": True, **_serialize_ressource(r)}


@router.delete("/admin/ressources/{ressource_id}", dependencies=[Depends(verify_admin_code)])
def admin_delete_ressource(ressource_id: int, db: Session = Depends(get_db)):
    r = db.execute(
        select(PrepaAdjurisRessource).where(PrepaAdjurisRessource.id == ressource_id)
    ).scalar_one_or_none()
    if not r:
        raise HTTPException(status_code=404, detail="Ressource introuvable.")
    delete_prepa_file(r.path)
    db.delete(r)
    db.commit()
    return {"ok": True, "deleted_id": ressource_id}


@router.get("/admin/ressources/{ressource_id}/download", dependencies=[Depends(verify_admin_code)])
def admin_download_ressource(ressource_id: int, db: Session = Depends(get_db)):
    r = db.execute(
        select(PrepaAdjurisRessource).where(PrepaAdjurisRessource.id == ressource_id)
    ).scalar_one_or_none()
    if not r:
        raise HTTPException(status_code=404, detail="Ressource introuvable.")
    if not Path(r.path).exists():
        raise HTTPException(status_code=404, detail="Fichier introuvable sur le disque.")
    return FileResponse(r.path, media_type="application/pdf", filename=r.filename_original)


# ============================================================
# Admin — séances (cours datés)
# ============================================================

@router.get("/admin/seances", dependencies=[Depends(verify_admin_code)])
def admin_list_seances(db: Session = Depends(get_db)):
    rows = db.execute(
        select(PrepaAdjurisSeance).order_by(PrepaAdjurisSeance.date_debut)
    ).scalars().all()
    return {"total": len(rows), "items": [_serialize_seance(s) for s in rows]}


@router.post("/admin/seances", dependencies=[Depends(verify_admin_code)])
def admin_create_seance(payload: SeanceIn, db: Session = Depends(get_db)):
    """
    Programme une séance. matiere_key vide → séance commune au niveau
    (cas de la séance offerte du 10 septembre).
    """
    niveau = payload.niveau.strip().upper()
    if niveau not in VALID_NIVEAUX:
        raise HTTPException(status_code=400, detail="Niveau invalide (L1, L2 ou L3).")

    key = (payload.matiere_key or "").strip() or None
    if key:
        if key not in PREPA_PRICES:
            raise HTTPException(status_code=400, detail=f"Matière inconnue : {key}")
        if matiere_niveau(key) != niveau:
            raise HTTPException(
                status_code=400,
                detail=f"La matière {key} n'appartient pas au niveau {niveau}.",
            )

    row = PrepaAdjurisSeance(
        niveau=niveau,
        matiere_key=key,
        titre=(payload.titre or "").strip(),
        date_debut=payload.date_debut,
        duree_minutes=payload.duree_minutes or 60,
        lien=(payload.lien or "").strip(),
        statut="prevue",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, **_serialize_seance(row)}


@router.patch("/admin/seances/{seance_id}", dependencies=[Depends(verify_admin_code)])
def admin_update_seance(
    seance_id: int,
    payload: SeanceUpdateIn,
    db: Session = Depends(get_db),
):
    """
    Modifie ou annule une séance. Annuler ne change PAS la facturation :
    les quantités Stripe restent celles de PREPA_MONTHLY_QUANTITIES.
    """
    s = db.execute(
        select(PrepaAdjurisSeance).where(PrepaAdjurisSeance.id == seance_id)
    ).scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Séance introuvable.")

    if payload.titre is not None:
        s.titre = payload.titre.strip()
    if payload.date_debut is not None:
        s.date_debut = payload.date_debut
    if payload.duree_minutes is not None:
        s.duree_minutes = payload.duree_minutes
    if payload.lien is not None:
        s.lien = payload.lien.strip()
    if payload.statut is not None:
        if payload.statut not in ("prevue", "annulee"):
            raise HTTPException(status_code=400, detail="Statut invalide (prevue | annulee).")
        s.statut = payload.statut
    db.commit()
    db.refresh(s)
    return {"ok": True, **_serialize_seance(s)}


@router.delete("/admin/seances/{seance_id}", dependencies=[Depends(verify_admin_code)])
def admin_delete_seance(seance_id: int, db: Session = Depends(get_db)):
    s = db.execute(
        select(PrepaAdjurisSeance).where(PrepaAdjurisSeance.id == seance_id)
    ).scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Séance introuvable.")
    db.delete(s)
    db.commit()
    return {"ok": True, "deleted_id": seance_id}


# ============================================================
# Admin — accès accordés à la main (essai, geste commercial…)
# ============================================================

@router.post("/admin/acces", dependencies=[Depends(verify_admin_code)])
def admin_accorder_acces(payload: AccesIn, db: Session = Depends(get_db)):
    """
    Ouvre l'accès à une matière sans paiement, pour une durée limitée
    (7 jours par défaut). Le rôle Discord de la matière est posé immédiatement
    si le compte est lié, et retiré automatiquement à l'échéance par
    expirer_acces_adjuris().

    avec_grade=True passe aussi User.plan à "prepa" jusqu'à la même échéance :
    c'est ce qui déclenche le rôle d'abonnement côté Discord (PLAN_TO_ROLE).
    ⚠️ Ce plan ouvre également l'accès aux cours PREPASSERELLE, qui partagent
    le même champ. Mettre avec_grade=False pour n'accorder que la matière.
    """
    if payload.matiere_key not in PREPA_PRICES:
        raise HTTPException(status_code=400, detail=f"Matière inconnue : {payload.matiere_key}")

    email = payload.email.strip().lower()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Aucun compte NAVIRE avec cet email.")

    jours = max(1, min(int(payload.jours or 7), 365))
    echeance = datetime.now(timezone.utc) + timedelta(days=jours)

    # Une ligne déjà présente pour cette matière est prolongée plutôt que
    # dupliquée — sauf si elle vient d'un paiement Stripe encore actif.
    existante = db.execute(
        select(PrepaAdjurisEnrollment).where(
            PrepaAdjurisEnrollment.user_id == user.id,
            PrepaAdjurisEnrollment.matiere_key == payload.matiere_key,
        ).order_by(desc(PrepaAdjurisEnrollment.created_at))
    ).scalars().first()

    if existante and existante.source == "stripe" and existante.status == "active":
        raise HTTPException(status_code=400, detail={
            "code": "DEJA_PAYE",
            "message": "Cet étudiant a déjà payé cette matière.",
        })

    if existante:
        existante.status = "active"
        existante.source = "admin"
        existante.expires_at = echeance
        existante.email = email
    else:
        db.add(PrepaAdjurisEnrollment(
            user_id=user.id,
            email=email,
            matiere_key=payload.matiere_key,
            status="active",
            source="admin",
            expires_at=echeance,
        ))

    if payload.avec_grade:
        user.plan = "prepa"
        user.prepa_annee = matiere_niveau(payload.matiere_key)
        user.prepa_expires_at = echeance
    db.commit()

    if user.discord_id:
        assign_adjuris_role_sync(user.discord_id, payload.matiere_key)

    return {
        "ok": True,
        "email": email,
        "matiere_key": payload.matiere_key,
        "matiere_label": PREPA_MATIERE_NAMES.get(payload.matiere_key, payload.matiere_key),
        "expires_at": echeance.isoformat(),
        "jours": jours,
        "grade_prepa": bool(payload.avec_grade),
        "discord_lie": bool(user.discord_id),
    }


@router.post("/admin/acces/revoquer", dependencies=[Depends(verify_admin_code)])
def admin_revoquer_acces(payload: AccesRevokeIn, db: Session = Depends(get_db)):
    """Coupe immédiatement un accès accordé à la main et retire le rôle Discord."""
    email = payload.email.strip().lower()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Aucun compte NAVIRE avec cet email.")

    enrollment = db.execute(
        select(PrepaAdjurisEnrollment).where(
            PrepaAdjurisEnrollment.user_id == user.id,
            PrepaAdjurisEnrollment.matiere_key == payload.matiere_key,
            PrepaAdjurisEnrollment.source == "admin",
        )
    ).scalars().first()
    if not enrollment:
        raise HTTPException(status_code=404, detail="Aucun accès manuel sur cette matière.")

    enrollment.status = "expired"
    db.commit()

    if user.discord_id:
        remove_adjuris_role_sync(user.discord_id, payload.matiere_key)

    return {"ok": True, "email": email, "matiere_key": payload.matiere_key}


def expirer_acces_adjuris(db_factory) -> int:
    """
    Passe en "expired" les accès manuels arrivés à échéance et retire les rôles
    Discord correspondants. Appelé par le job horaire existant
    (check_expired_subscriptions). Ne lève jamais : un échec ne doit pas
    interrompre le reste du job.
    """
    db: Session = db_factory()
    try:
        maintenant = datetime.now(timezone.utc)
        echus = db.execute(
            select(PrepaAdjurisEnrollment).where(
                PrepaAdjurisEnrollment.status == "active",
                PrepaAdjurisEnrollment.expires_at.isnot(None),
                PrepaAdjurisEnrollment.expires_at < maintenant,
            )
        ).scalars().all()

        for e in echus:
            e.status = "expired"
        if echus:
            db.commit()

        for e in echus:
            if not e.user_id:
                continue
            user = db.execute(select(User).where(User.id == e.user_id)).scalar_one_or_none()
            if user and user.discord_id:
                remove_adjuris_role_sync(user.discord_id, e.matiere_key)
            logger.info(
                "Adjuris : accès expiré (%s, %s)", e.email or e.user_id, e.matiere_key
            )
        return len(echus)
    except Exception as exc:  # noqa: BLE001 — ne doit jamais casser le job horaire
        logger.error("expirer_acces_adjuris : %s", exc)
        db.rollback()
        return 0
    finally:
        db.close()


# ============================================================
# Admin — étudiants
# ============================================================

@router.get("/admin/etudiants", dependencies=[Depends(verify_admin_code)])
def admin_list_etudiants(db: Session = Depends(get_db)):
    """
    Étudiants ayant un accès (payé ou accordé), regroupés par personne et non
    par matière. Distinct de /admin/inscriptions, qui liste les formulaires
    déposés sans paiement. Header : X-Admin-Code.
    """
    rows = db.execute(
        select(PrepaAdjurisEnrollment, User)
        .join(User, PrepaAdjurisEnrollment.user_id == User.id, isouter=True)
        .where(PrepaAdjurisEnrollment.status != "expired")
        .order_by(desc(PrepaAdjurisEnrollment.created_at))
    ).all()

    # Regroupe par email : un étudiant peut avoir plusieurs matières, et son
    # compte NAVIRE peut ne pas exister encore (paiement avant inscription).
    par_email: dict[str, dict] = {}
    for enr, user in rows:
        email = (enr.email or (user.email if user else "") or "").lower()
        if not email:
            continue
        fiche = par_email.setdefault(email, {
            "email": email,
            "user_id": enr.user_id,
            "username": user.username if user else None,
            "compte_cree": user is not None,
            "discord_lie": bool(user.discord_id) if user else False,
            "niveaux": set(),
            "matieres": [],
            "premiere_inscription": enr.created_at,
        })
        if enr.user_id and not fiche["user_id"]:
            fiche["user_id"] = enr.user_id
        fiche["niveaux"].add(matiere_niveau(enr.matiere_key))
        fiche["matieres"].append({
            "matiere_key": enr.matiere_key,
            "label": PREPA_MATIERE_NAMES.get(enr.matiere_key, enr.matiere_key),
            "niveau": matiere_niveau(enr.matiere_key),
            "status": enr.status,
            "source": enr.source,
            "expires_at": enr.expires_at.isoformat() if enr.expires_at else None,
            "stripe_subscription_id": enr.stripe_subscription_id,
            "created_at": enr.created_at.isoformat() if enr.created_at else None,
        })
        if enr.created_at and fiche["premiere_inscription"] and enr.created_at < fiche["premiere_inscription"]:
            fiche["premiere_inscription"] = enr.created_at

    # Complète prénom/nom depuis le formulaire d'inscription quand il existe.
    identites = {
        i.email: (i.prenom, i.nom)
        for i in db.execute(select(PrepaAdjurisInscription)).scalars().all()
    }

    items = []
    for email, f in par_email.items():
        prenom, nom = identites.get(email, ("", ""))
        actives = [m for m in f["matieres"] if m["status"] == "active"]
        items.append({
            "email": email,
            "prenom": prenom,
            "nom": nom,
            "user_id": f["user_id"],
            "username": f["username"],
            "compte_cree": f["compte_cree"],
            "discord_lie": f["discord_lie"],
            "niveau": sorted(f["niveaux"])[0] if len(f["niveaux"]) == 1 else "mixte",
            "matieres": f["matieres"],
            "nb_actives": len(actives),
            "a_un_impaye": any(m["status"] == "payment_failed" for m in f["matieres"]),
            "premiere_inscription": (
                f["premiere_inscription"].isoformat() if f["premiere_inscription"] else None
            ),
        })

    items.sort(key=lambda x: x["premiere_inscription"] or "", reverse=True)
    return {
        "total": len(items),
        "total_matieres_actives": sum(i["nb_actives"] for i in items),
        "items": items,
    }