"""
app/routers/presences.py
=========================
Présence aux cours. Règles : app/services/presences.py.

Professeur (auth NAVIRE, professeur actif) :
  GET   /professeurs/me/cours?jours=60          → ses cours récents, présents comptés
  GET   /professeurs/cours/{id}/presences       → élèves du groupe, minutes, présent
  PATCH /professeurs/cours/{id}/presences       → cocher / décocher (48 h après le cours)
  Aperçu admin : ?voir_comme=<id professeur> sur les GET (lecture seule).

Console (auth NAVIRE, is_admin) :
  GET   /professeurs/admin/cours?mois=&classe_id=
  GET   /professeurs/admin/cours/{id}/presences
  PATCH /professeurs/admin/cours/{id}/presences  → sans limite de temps (sauf mois versé)
  POST  /professeurs/admin/cours                 → cours oublié sur Discord, créé terminé

Relevés du bot : app/routers/discord_bot.py (/discord/cours/vocaux,
/discord/cours/{id}/presence).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import PrepaAdjurisClasse, PrepaAdjurisCours, PrepaAdjurisProfesseur, User
from app.routers.auth import get_current_user
from app.routers.professeurs import VOIR_COMME, require_admin, require_professeur, require_professeur_lecture
from app.services import presences as svc
from app.services.prepa_adjuris_billing import cle_mois, mois_de, parse_mois

router = APIRouter(prefix="/professeurs", tags=["professeurs-presences"])


class PresenceIn(BaseModel):
    present: bool
    presence_id: int | None = None
    enrollment_id: int | None = None

    @model_validator(mode="after")
    def _cible(self):
        if self.presence_id is None and self.enrollment_id is None:
            raise ValueError("Indique l'étudiant à cocher.")
        return self


class CoursOublieIn(BaseModel):
    classe_id: int
    professeur_id: int
    debut: datetime
    duree_minutes: int = Field(60, ge=15, le=240)


def _cours(db: Session, cours_id: int) -> PrepaAdjurisCours:
    c = db.get(PrepaAdjurisCours, cours_id)
    if c is None:
        raise HTTPException(404, detail="Cours introuvable.")
    return c


def _cours_du_prof(db: Session, prof: PrepaAdjurisProfesseur, cours_id: int) -> PrepaAdjurisCours:
    c = _cours(db, cours_id)
    if c.professeur_id != prof.id:
        raise HTTPException(404, detail="Cours introuvable.")
    return c


def _corriger(db: Session, c: PrepaAdjurisCours, user: User, payload: PresenceIn, admin: bool) -> dict:
    try:
        svc.corriger(db, c, user, payload.present, admin,
                     presence_id=payload.presence_id, enrollment_id=payload.enrollment_id)
    except svc.PresenceErreur as e:
        raise HTTPException(400, detail=str(e))
    return svc.liste(db, c, admin=admin)


# ============================================================
# Professeur
# ============================================================

@router.get("/me/cours")
def mes_cours(
    jours: int = Query(60, ge=1, le=400),
    voir_comme: int | None = VOIR_COMME,
    db: Session = Depends(get_db),
    prof: PrepaAdjurisProfesseur = Depends(require_professeur_lecture),
):
    depuis = svc.utcnow() - timedelta(days=jours)
    return {
        "items": [svc.cours_row(db, c) for c in svc.cours_du_prof(db, prof, depuis)],
        "seuil_minutes": svc.PREPA_PRESENCE_MIN_MINUTES,
        "correction_heures": svc.PREPA_PRESENCE_CORRECTION_HEURES,
    }


@router.get("/cours/{cours_id}/presences")
def presences_du_cours(
    cours_id: int,
    voir_comme: int | None = VOIR_COMME,
    db: Session = Depends(get_db),
    prof: PrepaAdjurisProfesseur = Depends(require_professeur_lecture),
):
    return svc.liste(db, _cours_du_prof(db, prof, cours_id))


@router.patch("/cours/{cours_id}/presences")
def corriger_presence(
    cours_id: int,
    payload: PresenceIn,
    db: Session = Depends(get_db),
    prof: PrepaAdjurisProfesseur = Depends(require_professeur),
    user: User = Depends(get_current_user),
):
    return _corriger(db, _cours_du_prof(db, prof, cours_id), user, payload, admin=False)


# ============================================================
# Console
# ============================================================

@router.get("/admin/cours")
def admin_cours(
    mois: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
    classe_id: int | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    cle = mois or cle_mois(mois_de(svc.utcnow()))
    annee, m = parse_mois(cle)
    # Fenêtre large en UTC, puis filtre exact sur le mois de Paris.
    debut = datetime(annee, m, 1, tzinfo=timezone.utc) - timedelta(days=1)
    fin = datetime(annee + (m == 12), m % 12 + 1, 1, tzinfo=timezone.utc) + timedelta(days=1)
    q = select(PrepaAdjurisCours).where(PrepaAdjurisCours.started_at >= debut, PrepaAdjurisCours.started_at < fin)
    if classe_id:
        q = q.where(PrepaAdjurisCours.classe_id == classe_id)
    rows = [c for c in db.execute(q.order_by(PrepaAdjurisCours.started_at.desc())).scalars() if cle_mois(mois_de(c.started_at)) == cle]
    return {"mois": cle, "items": [svc.cours_row(db, c) for c in rows], "seuil_minutes": svc.PREPA_PRESENCE_MIN_MINUTES}


@router.get("/admin/cours/{cours_id}/presences")
def admin_presences(cours_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return svc.liste(db, _cours(db, cours_id), admin=True)


@router.patch("/admin/cours/{cours_id}/presences")
def admin_corriger(cours_id: int, payload: PresenceIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    return _corriger(db, _cours(db, cours_id), admin, payload, admin=True)


@router.post("/admin/cours")
def admin_cours_oublie(payload: CoursOublieIn, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    classe = db.get(PrepaAdjurisClasse, payload.classe_id)
    prof = db.get(PrepaAdjurisProfesseur, payload.professeur_id)
    if classe is None or prof is None:
        raise HTTPException(404, detail="Groupe ou professeur introuvable.")
    try:
        c = svc.creer_cours_oublie(db, classe, prof, payload.debut, payload.duree_minutes)
    except svc.PresenceErreur as e:
        raise HTTPException(400, detail=str(e))
    return svc.liste(db, c, admin=True)

