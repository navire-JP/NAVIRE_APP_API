"""
app/routers/prepa_epreuves.py
=============================
Épreuves chronométrées Prép'AdJuris : dissertation, commentaire d'arrêt,
cas pratique, fiche d'arrêt.

Professeur (embeds/professeurs.html, rubrique « Épreuves ») :
  GET    /professeurs/epreuves                 ses épreuves (admin : toutes), avec
                                               qui l'a commencée / rendue
  POST   /professeurs/epreuves                 créer (multipart : sujet PDF facultatif)
  PATCH  /professeurs/epreuves/{id}            modifier (titre, sujet, dates, durée)
  DELETE /professeurs/epreuves/{id}
  GET    /professeurs/epreuves/{id}/sujet      le PDF du sujet

Étudiant (embeds/prepa.html, rubrique « Épreuves ») :
  GET    /lib/prepa/epreuves                   épreuves de ses groupes + ses sessions
  POST   /lib/prepa/epreuves/sessions          lancer : {epreuve_id} (planifiée) ou
                                               {type, matiere_key, titre, sujet} (libre)
  GET    /lib/prepa/epreuves/sessions/{id}     session en cours (sujet, chrono serveur)
  GET    /lib/prepa/epreuves/sessions/{id}/sujet   le PDF du sujet (épreuve commencée)
  POST   /lib/prepa/epreuves/sessions/{id}/rendre  la copie (.docx / .pdf) : devient un
                                               devoir, corrigé par le professeur du groupe
  PUT    /lib/prepa/epreuves/sessions/{id}/brouillon    la copie en cours de saisie
                                               dans NAVIRE (écran blanc), au fil de l'eau
  POST   /lib/prepa/epreuves/sessions/{id}/rendre-texte la copie saisie dans NAVIRE
  POST   /lib/prepa/epreuves/sessions/{id}/abandonner   (quitter l'écran d'épreuve)

Règles :
  - Une épreuve planifiée se commence entre ouvre_le et ferme_le (la
    « persistance » choisie par le professeur), une seule fois par étudiant.
    Une fois commencée, le chrono court sa durée entière, même au-delà de
    ferme_le. Le sujet n'est visible qu'une fois l'épreuve commencée.
  - Le chrono est tenu par le serveur (started_at) : recharger la page ne le
    remet pas à zéro.
  - La copie peut être rendue après la fin du chrono : elle est marquée
    « rendue en retard » (minutes de retard), sans être refusée.
  - L'étudiant écrit sa copie dans NAVIRE, sur un écran blanc : quitter la
    page abandonne l'épreuve (POST …/abandonner, envoyé par l'embed). Une
    épreuve abandonnée ne se reprend plus et ne se rend plus.
  - Entraînement libre : l'étudiant choisit le type, la matière (une de ses
    matières, pour que la copie parte au bon professeur) et son sujet.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File as FastAPIFile, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import MAX_UPLOAD_BYTES
from app.core.prepa_adjuris_config import PREPA_MATIERE_NAMES
from app.core.prepa_storage import PREPA_ADJURIS_DIR, delete_prepa_file, save_prepa_pdf
from app.db.database import get_db
from app.db.models import (
    PrepaAdjurisClasse,
    PrepaEpreuve,
    PrepaEpreuveSession,
    User,
)
from app.routers.auth import get_current_user

prof_router = APIRouter(prefix="/professeurs/epreuves", tags=["epreuves"])
eleve_router = APIRouter(prefix="/lib/prepa/epreuves", tags=["epreuves"])

# type → (libellé, durée par défaut en minutes)
TYPES: dict[str, tuple[str, int]] = {
    "dissertation": ("Dissertation", 180),
    "commentaire": ("Commentaire d'arrêt", 180),
    "cas_pratique": ("Cas pratique", 180),
    "fiche_arret": ("Fiche d'arrêt", 75),
}
TypeEpreuve = Literal["dissertation", "commentaire", "cas_pratique", "fiche_arret"]
SUJETS_DIR = PREPA_ADJURIS_DIR / "epreuves"
SUJET_MAX = 20000


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def iso(dt: datetime | None) -> str | None:
    dt = aware(dt)
    return dt.isoformat() if dt else None


def etat(e: PrepaEpreuve, now: datetime | None = None) -> str:
    now = now or utcnow()
    if now < aware(e.ouvre_le):
        return "a_venir"
    if now > aware(e.ferme_le):
        return "fermee"
    return "ouverte"


def fin_session(s: PrepaEpreuveSession) -> datetime:
    return aware(s.started_at) + timedelta(minutes=s.duree_minutes)


def session_row(s: PrepaEpreuveSession, *, avec_sujet: bool = True) -> dict:
    now = utcnow()
    fin = fin_session(s)
    retard = 0
    if s.rendu_le and aware(s.rendu_le) > fin:
        retard = int((aware(s.rendu_le) - fin).total_seconds() // 60)
    statut = "rendue" if s.rendu_le else "abandonnee" if s.abandonnee else "en_cours" if now < fin else "terminee"
    row = {
        "id": s.id,
        "epreuve_id": s.epreuve_id,
        "type": s.type,
        "type_label": TYPES.get(s.type, (s.type, 0))[0],
        "titre": s.titre,
        "matiere_key": s.matiere_key,
        "matiere": PREPA_MATIERE_NAMES.get(s.matiere_key, s.matiere_key),
        "duree_minutes": s.duree_minutes,
        "started_at": iso(s.started_at),
        "fin_at": iso(fin),
        "rendu_le": iso(s.rendu_le),
        "retard_minutes": retard,
        "devoir_id": s.devoir_id,
        "statut": statut,
        "libre": s.epreuve_id is None,
        "server_now": iso(now),
    }
    if avec_sujet:
        row["sujet"] = s.sujet
    return row


def epreuve_row(db: Session, e: PrepaEpreuve, *, pour_prof: bool) -> dict:
    c = db.get(PrepaAdjurisClasse, e.classe_id)
    row = {
        "id": e.id,
        "classe_id": e.classe_id,
        "classe_nom": c.nom if c else None,
        "matiere_key": c.matiere_key if c else None,
        "matiere": PREPA_MATIERE_NAMES.get(c.matiere_key, c.matiere_key) if c else None,
        "type": e.type,
        "type_label": TYPES.get(e.type, (e.type, 0))[0],
        "titre": e.titre,
        "duree_minutes": e.duree_minutes,
        "ouvre_le": iso(e.ouvre_le),
        "ferme_le": iso(e.ferme_le),
        "etat": etat(e),
        "sujet_pdf": bool(e.sujet_path),
    }
    if pour_prof:
        row["sujet"] = e.sujet
        sessions = db.execute(
            select(PrepaEpreuveSession).where(PrepaEpreuveSession.epreuve_id == e.id)
        ).scalars().all()
        noms = {u.id: (u.username or u.email) for u in db.execute(
            select(User).where(User.id.in_([s.user_id for s in sessions] or [-1]))
        ).scalars().all()}
        row["sessions"] = [
            {**session_row(s, avec_sujet=False), "etudiant": noms.get(s.user_id)} for s in sessions
        ]
        row["commencees"] = len(sessions)
        row["rendues"] = sum(1 for s in sessions if s.rendu_le)
    return row


# ============================================================
# Professeur
# ============================================================

def _acces(db: Session, user: User):
    from app.routers.professeurs import require_correcteur

    return require_correcteur(db, user)


def _epreuve_du_prof(db: Session, acces, epreuve_id: int) -> PrepaEpreuve:
    _, classes = acces
    e = db.get(PrepaEpreuve, epreuve_id)
    if e is None or (classes is not None and e.classe_id not in classes):
        raise HTTPException(404, detail="Épreuve introuvable.")
    return e


def _dates(ouvre_le: datetime, ferme_le: datetime | None, persistance_heures: float | None) -> tuple[datetime, datetime]:
    ouvre = aware(ouvre_le)
    if ferme_le is not None:
        ferme = aware(ferme_le)
    elif persistance_heures:
        ferme = ouvre + timedelta(hours=persistance_heures)
    else:
        ferme = ouvre + timedelta(hours=24)
    if ferme <= ouvre:
        raise HTTPException(400, detail="La fin de disponibilité doit suivre l'ouverture.")
    return ouvre, ferme


@prof_router.get("")
def lister_epreuves(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _, classes = _acces(db, user)
    q = select(PrepaEpreuve).order_by(PrepaEpreuve.ouvre_le.desc(), PrepaEpreuve.id.desc())
    if classes is not None:
        if not classes:
            return {"items": [], "types": _types()}
        q = q.where(PrepaEpreuve.classe_id.in_(classes))
    return {"items": [epreuve_row(db, e, pour_prof=True) for e in db.execute(q).scalars().all()], "types": _types()}


def _types() -> list[dict]:
    return [{"type": k, "label": v[0], "duree_minutes": v[1]} for k, v in TYPES.items()]


@prof_router.post("")
async def creer_epreuve(
    classe_id: int = Form(...),
    type: TypeEpreuve = Form(...),
    titre: str = Form(""),
    sujet: str = Form(""),
    duree_minutes: int | None = Form(None),
    ouvre_le: datetime = Form(...),
    ferme_le: datetime | None = Form(None),
    persistance_heures: float | None = Form(None),
    file: UploadFile | None = FastAPIFile(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _, classes = _acces(db, user)
    if classes is not None and classe_id not in classes:
        raise HTTPException(404, detail="Groupe introuvable.")
    if db.get(PrepaAdjurisClasse, classe_id) is None:
        raise HTTPException(404, detail="Groupe introuvable.")
    duree = duree_minutes or TYPES[type][1]
    if not 10 <= duree <= 600:
        raise HTTPException(400, detail="Durée entre 10 minutes et 10 heures.")
    if len(sujet) > SUJET_MAX:
        raise HTTPException(400, detail="Sujet trop long.")
    ouvre, ferme = _dates(ouvre_le, ferme_le, persistance_heures)
    meta = None
    if file is not None and file.filename:
        meta = await save_prepa_pdf(file, SUJETS_DIR / str(classe_id), max_bytes=MAX_UPLOAD_BYTES)
    if not sujet.strip() and meta is None:
        raise HTTPException(400, detail="Donne un sujet (texte ou PDF).")
    e = PrepaEpreuve(
        classe_id=classe_id, cree_par_id=user.id, type=type,
        titre=(titre.strip() or TYPES[type][0])[:200], sujet=sujet.strip(),
        sujet_filename=(meta["filename_original"][:255] if meta else ""), sujet_path=(meta["path"] if meta else ""),
        duree_minutes=duree, ouvre_le=ouvre, ferme_le=ferme,
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return epreuve_row(db, e, pour_prof=True)


class EpreuveModifIn(BaseModel):
    titre: str | None = Field(None, max_length=200)
    sujet: str | None = Field(None, max_length=SUJET_MAX)
    duree_minutes: int | None = Field(None, ge=10, le=600)
    ouvre_le: datetime | None = None
    ferme_le: datetime | None = None


@prof_router.patch("/{epreuve_id}")
def modifier_epreuve(epreuve_id: int, payload: EpreuveModifIn, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    e = _epreuve_du_prof(db, _acces(db, user), epreuve_id)
    if payload.titre is not None and payload.titre.strip():
        e.titre = payload.titre.strip()
    if payload.sujet is not None:
        e.sujet = payload.sujet.strip()
    if payload.duree_minutes is not None:
        e.duree_minutes = payload.duree_minutes
    ouvre = aware(payload.ouvre_le) if payload.ouvre_le else aware(e.ouvre_le)
    ferme = aware(payload.ferme_le) if payload.ferme_le else aware(e.ferme_le)
    if ferme <= ouvre:
        raise HTTPException(400, detail="La fin de disponibilité doit suivre l'ouverture.")
    e.ouvre_le, e.ferme_le = ouvre, ferme
    db.commit()
    db.refresh(e)
    return epreuve_row(db, e, pour_prof=True)


@prof_router.delete("/{epreuve_id}")
def supprimer_epreuve(epreuve_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    e = _epreuve_du_prof(db, _acces(db, user), epreuve_id)
    if e.sujet_path:
        delete_prepa_file(e.sujet_path)
    db.delete(e)
    db.commit()
    return {"ok": True, "id": epreuve_id}


@prof_router.get("/{epreuve_id}/sujet")
def sujet_prof(epreuve_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    e = _epreuve_du_prof(db, _acces(db, user), epreuve_id)
    return _pdf(e)


def _pdf(e: PrepaEpreuve | None):
    if e is None or not e.sujet_path or not Path(e.sujet_path).exists():
        raise HTTPException(404, detail="Pas de sujet en PDF.")
    return FileResponse(e.sujet_path, media_type="application/pdf", filename=e.sujet_filename or "sujet.pdf",
                        content_disposition_type="inline")


# ============================================================
# Étudiant
# ============================================================

def _groupes(db: Session, user: User) -> set[int]:
    from app.services.professeurs import groupes_de_l_etudiant

    return groupes_de_l_etudiant(db, user.id)


@eleve_router.get("")
def mes_epreuves(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app.routers.lib_prepa import matieres_actives

    groupes = _groupes(db, user)
    now = utcnow()
    planifiees = db.execute(
        select(PrepaEpreuve)
        .where(PrepaEpreuve.classe_id.in_(groupes or {-1}), PrepaEpreuve.ferme_le >= now - timedelta(days=30))
        .order_by(PrepaEpreuve.ouvre_le)
    ).scalars().all() if groupes else []
    sessions = db.execute(
        select(PrepaEpreuveSession).where(PrepaEpreuveSession.user_id == user.id)
        .order_by(PrepaEpreuveSession.started_at.desc())
    ).scalars().all()
    par_epreuve = {s.epreuve_id: s for s in sessions if s.epreuve_id}
    return {
        "planifiees": [
            {**epreuve_row(db, e, pour_prof=False),
             "session": session_row(par_epreuve[e.id], avec_sujet=False) if e.id in par_epreuve else None}
            for e in planifiees
        ],
        "sessions": [session_row(s, avec_sujet=False) for s in sessions],
        "types": _types(),
        "matieres": [{"matiere_key": k, "label": PREPA_MATIERE_NAMES.get(k, k)} for k in sorted(matieres_actives(db, user))],
        "server_now": iso(now),
    }


class SessionIn(BaseModel):
    epreuve_id: int | None = None
    type: TypeEpreuve | None = None
    matiere_key: str | None = None
    titre: str = Field("", max_length=200)
    sujet: str = Field("", max_length=SUJET_MAX)
    duree_minutes: int | None = Field(None, ge=10, le=600)


@eleve_router.post("/sessions")
def lancer(payload: SessionIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app.routers.lib_prepa import matieres_actives

    now = utcnow()
    if payload.epreuve_id is not None:
        e = db.get(PrepaEpreuve, payload.epreuve_id)
        if e is None or e.classe_id not in _groupes(db, user):
            raise HTTPException(404, detail="Épreuve introuvable.")
        st = etat(e, now)
        if st == "a_venir":
            raise HTTPException(400, detail="Cette épreuve n'est pas encore ouverte.")
        deja = db.execute(select(PrepaEpreuveSession).where(
            PrepaEpreuveSession.epreuve_id == e.id, PrepaEpreuveSession.user_id == user.id)).scalar_one_or_none()
        if deja is not None:
            return session_row(deja)
        if st == "fermee":
            raise HTTPException(400, detail="Cette épreuve est fermée.")
        c = db.get(PrepaAdjurisClasse, e.classe_id)
        s = PrepaEpreuveSession(
            user_id=user.id, epreuve_id=e.id, type=e.type, titre=e.titre, matiere_key=c.matiere_key,
            sujet=e.sujet, duree_minutes=e.duree_minutes, started_at=now,
        )
    else:
        if payload.type is None:
            raise HTTPException(400, detail="Choisis le type d'épreuve.")
        actives = matieres_actives(db, user)
        if payload.matiere_key not in actives:
            raise HTTPException(403, detail="Choisis une de tes matières.")
        s = PrepaEpreuveSession(
            user_id=user.id, epreuve_id=None, type=payload.type,
            titre=(payload.titre.strip() or TYPES[payload.type][0])[:200], matiere_key=payload.matiere_key,
            sujet=payload.sujet.strip(), duree_minutes=payload.duree_minutes or TYPES[payload.type][1], started_at=now,
        )
    db.add(s)
    try:
        db.commit()
    except IntegrityError:  # double clic : la session existe déjà
        db.rollback()
        s = db.execute(select(PrepaEpreuveSession).where(
            PrepaEpreuveSession.epreuve_id == payload.epreuve_id, PrepaEpreuveSession.user_id == user.id)).scalar_one()
    db.refresh(s)
    return session_row(s)


def _ma_session(db: Session, user: User, session_id: int) -> PrepaEpreuveSession:
    s = db.get(PrepaEpreuveSession, session_id)
    if s is None or s.user_id != user.id:
        raise HTTPException(404, detail="Épreuve introuvable.")
    return s


@eleve_router.get("/sessions/{session_id}")
def ma_session(session_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    s = _ma_session(db, user, session_id)
    row = session_row(s)
    e = db.get(PrepaEpreuve, s.epreuve_id) if s.epreuve_id else None
    row["sujet_pdf"] = bool(e and e.sujet_path)
    row["brouillon"] = s.brouillon if not s.rendu_le and not s.abandonnee else None
    return row


def _modifiable(s: PrepaEpreuveSession) -> None:
    if s.rendu_le:
        raise HTTPException(400, detail="Copie déjà rendue.")
    if s.abandonnee:
        raise HTTPException(400, detail="Épreuve abandonnée : tu as quitté l'écran d'épreuve.")


class BrouillonIn(BaseModel):
    blocs: list = Field(default_factory=list, max_length=3000)
    notes: str = Field("", max_length=SUJET_MAX)


@eleve_router.put("/sessions/{session_id}/brouillon")
def enregistrer_brouillon(session_id: int, payload: BrouillonIn, db: Session = Depends(get_db),
                          user: User = Depends(get_current_user)):
    """La copie en cours de saisie, enregistrée au fil de l'eau (un incident
    réseau ou une panne ne fait pas perdre le travail)."""
    s = _ma_session(db, user, session_id)
    _modifiable(s)
    s.brouillon = {"blocs": payload.blocs, "notes": payload.notes, "saved_at": iso(utcnow())}
    db.commit()
    return {"ok": True, "saved_at": s.brouillon["saved_at"]}


@eleve_router.get("/sessions/{session_id}/sujet")
def mon_sujet(session_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    s = _ma_session(db, user, session_id)
    return _pdf(db.get(PrepaEpreuve, s.epreuve_id) if s.epreuve_id else None)


@eleve_router.post("/sessions/{session_id}/abandonner")
def abandonner(session_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    s = _ma_session(db, user, session_id)
    if s.rendu_le:
        raise HTTPException(400, detail="Copie déjà rendue.")
    if s.abandonnee:
        return session_row(s)
    s.abandonnee = True
    db.commit()
    return session_row(s)


@eleve_router.post("/sessions/{session_id}/rendre")
async def rendre(
    session_id: int,
    background: BackgroundTasks,
    file: UploadFile = FastAPIFile(...),
    message: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.routers.lib_prepa import deposer_devoir

    s = _ma_session(db, user, session_id)
    _modifiable(s)
    now = utcnow()
    mot = _mot(s, now)
    row = await deposer_devoir(
        background=background, file=file, matiere_key=s.matiere_key, titre=s.titre,
        message=(mot + ("\n\n" + message.strip() if message.strip() else ""))[:4000], db=db, user=user,
    )
    s = db.get(PrepaEpreuveSession, session_id)
    s.rendu_le, s.devoir_id, s.brouillon = now, row["id"], None
    db.commit()
    return {"session": session_row(s, avec_sujet=False), "devoir": row}


def _mot(s: PrepaEpreuveSession, now: datetime, sorties: int = 0) -> str:
    """Mot joint à la copie pour le correcteur : temps passé, retard, sorties de l'écran."""
    duree = int((min(now, fin_session(s)) - aware(s.started_at)).total_seconds() // 60)
    mot = f"Épreuve chronométrée · {TYPES.get(s.type, (s.type,))[0]} · {duree} min sur {s.duree_minutes}"
    if now > fin_session(s):
        mot += f" · rendue {int((now - fin_session(s)).total_seconds() // 60)} min après la fin"
    if sorties:
        mot += f" · {sorties} sortie{'s' if sorties > 1 else ''} de l'écran d'épreuve"
    return mot


class RenduTexteIn(BaseModel):
    blocs: list = Field(default_factory=list, max_length=3000)
    message: str = Field("", max_length=3000)
    sorties: int = Field(0, ge=0, le=10000)


@eleve_router.post("/sessions/{session_id}/rendre-texte")
def rendre_texte(session_id: int, payload: RenduTexteIn, background: BackgroundTasks,
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """La copie saisie dans NAVIRE : elle devient un devoir, comme un fichier déposé."""
    from app.routers.lib_prepa import creer_devoir, ecrire_saisie

    s = _ma_session(db, user, session_id)
    _modifiable(s)
    now = utcnow()
    meta, contenu = ecrire_saisie(user, s.titre, payload.blocs)
    mot = _mot(s, now, payload.sorties)
    row = creer_devoir(db, user, background, matiere_key=s.matiere_key, titre=s.titre,
                       message=(mot + ("\n\n" + payload.message.strip() if payload.message.strip() else ""))[:4000],
                       meta=meta, contenu=contenu)
    s = db.get(PrepaEpreuveSession, session_id)
    s.rendu_le, s.devoir_id, s.brouillon = now, row["id"], None
    db.commit()
    return {"session": session_row(s, avec_sujet=False), "devoir": row}

