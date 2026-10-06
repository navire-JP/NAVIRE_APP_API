"""
app/routers/prepa_adjuris_agenda.py
====================================
Agenda des cours et facturation Prép'AdJuris — API de la console admin
(navire-ai.com/console, onglet AdJuris → Agenda / Facturation).

L'agenda fait foi pour la facturation : chaque création, déplacement,
annulation ou suppression d'une séance recalcule l'échéancier Stripe des
élèves de la matière. Toutes les écritures acceptent ?simulation=true :
rien n'est enregistré, la réponse donne seulement l'impact (élèves
concernés, mois, montants) pour la fenêtre de confirmation.

Admin (header X-Admin-Code, comme le reste de la console AdJuris) :
  GET    /prepa/adjuris/admin/agenda                      séances + créneaux
  POST   /prepa/adjuris/admin/agenda/seances              créer une séance
  POST   /prepa/adjuris/admin/agenda/serie                créer une série hebdo
  PATCH  /prepa/adjuris/admin/agenda/seances/{id}         déplacer / modifier
  POST   /prepa/adjuris/admin/agenda/seances/{id}/annuler
  POST   /prepa/adjuris/admin/agenda/seances/{id}/retablir
  DELETE /prepa/adjuris/admin/agenda/seances/{id}
  GET    /prepa/adjuris/admin/facturation                 échéanciers des élèves
  POST   /prepa/adjuris/admin/facturation/{id}/resynchroniser
  GET    /prepa/adjuris/admin/alertes
  POST   /prepa/adjuris/admin/lien-paiement               lien Stripe manuel
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.prepa_adjuris_config import (
    PREPA_CRENEAUX,
    PREPA_FIN_PROGRAMME,
    PREPA_HEURE_PRELEVEMENT,
    PREPA_MATIERE_NAMES,
    PREPA_NIVEAUX,
    PREPA_PRICES,
    PREPA_PRIX_SEANCE_CENTS,
    matiere_niveau,
    matieres_du_niveau,
    prices_configures,
)
from app.db.database import get_db
from app.db.models import PrepaAdjurisEnrollment, PrepaAdjurisSeance, User
from app.routers.admin import verify_admin_code
from app.routers.prepa_adjuris import _creer_checkout_session, _devis_public
from app.routers.prepa_adjuris_espace import _serialize_seance
from app.services import prepa_adjuris_facturation as facturation
from app.services.prepa_adjuris_billing import TZ

router = APIRouter(
    prefix="/prepa/adjuris/admin",
    tags=["prepa-adjuris-agenda"],
    dependencies=[Depends(verify_admin_code)],
)


# ============================================================
# Schemas
# ============================================================

class SeanceCreeIn(BaseModel):
    niveau: str
    date_debut: datetime
    matiere_key: str | None = None      # vide → séance commune (jamais facturée)
    titre: str | None = ""
    duree_minutes: int | None = 60
    lien: str | None = ""


class SerieIn(BaseModel):
    """Une séance par semaine, au même jour et à la même heure de Paris."""
    niveau: str
    matieres: list[str] | None = None   # vide → toutes les matières du niveau
    jour_semaine: int = Field(..., ge=0, le=6)   # lundi = 0
    heure: str = Field(..., pattern=r"^\d{1,2}:\d{2}$")
    du: date
    au: date
    sauf: list[date] = []
    duree_minutes: int = 60
    titre: str = ""
    lien: str = ""


class SeanceModifIn(BaseModel):
    titre: str | None = None
    date_debut: datetime | None = None
    duree_minutes: int | None = None
    lien: str | None = None
    statut: str | None = None           # prevue | annulee


class LienPaiementIn(BaseModel):
    email: str
    matieres: list[str] = Field(..., min_length=1)
    # Inscription antidatée : séance couverte par des 20 € déjà réglés
    # autrement. Laisser vide pour une inscription « maintenant ».
    inscrit_le: datetime | None = None
    inscription_deja_payee: bool = False


# ============================================================
# Helpers
# ============================================================

def _niveau_ok(niveau: str) -> str:
    n = (niveau or "").strip().upper()
    if n not in PREPA_NIVEAUX:
        raise HTTPException(400, detail=f"Niveau invalide ({', '.join(PREPA_NIVEAUX)}).")
    return n


def _matiere_ok(key: str | None, niveau: str) -> str | None:
    key = (key or "").strip() or None
    if key is None:
        return None
    if key not in PREPA_PRICES:
        raise HTTPException(400, detail=f"Matière inconnue : {key}")
    if matiere_niveau(key) != niveau:
        raise HTTPException(400, detail=f"La matière {key} n'appartient pas au niveau {niveau}.")
    return key


def _seance_ou_404(db: Session, seance_id: int) -> PrepaAdjurisSeance:
    s = db.execute(
        select(PrepaAdjurisSeance).where(PrepaAdjurisSeance.id == seance_id)
    ).scalar_one_or_none()
    if not s:
        raise HTTPException(404, detail="Séance introuvable.")
    return s


def _utc(dt: datetime) -> datetime:
    """Une date sans fuseau venant de la console est une heure de Paris.
    Tout est enregistré en UTC (SQLite, en dev, ne garde pas le fuseau)."""
    return (dt if dt.tzinfo else dt.replace(tzinfo=TZ)).astimezone(timezone.utc)


def _resume_impacts(impacts: list[dict]) -> dict:
    eleves = {i["enrollment_id"] for i in impacts}
    return {
        "nb_eleves": len(eleves),
        "delta_total_cents": sum(i.get("delta_cents", 0) for i in impacts),
        "credits_cents": -sum(i["delta_cents"] for i in impacts if i.get("type") == "credit"),
    }


def _enregistrer(db: Session, matieres: list[str | None], simulation: bool, rendu) -> dict:
    """
    Fin commune des écritures de l'agenda.
      simulation : flush, calcul de l'impact, puis rollback (rien n'est écrit) ;
      sinon      : commit, puis recalcul réel de la facturation.
    `rendu` produit la réponse ; il est appelé avant le rollback.
    """
    cles = [m for m in matieres if m]
    if simulation:
        db.flush()
        impacts = facturation.resynchroniser_matieres(db, cles, simulation=True)
        reponse = rendu()
        db.rollback()
    else:
        db.commit()
        reponse = rendu()
        impacts = facturation.resynchroniser_matieres(db, cles)
    return {
        "ok": True,
        "simulation": simulation,
        **reponse,
        "impacts": impacts,
        "resume": _resume_impacts(impacts),
    }


# ============================================================
# Agenda
# ============================================================

@router.get("/agenda")
def agenda(
    debut: datetime | None = None,
    fin: datetime | None = None,
    niveau: str | None = None,
    matiere: str | None = None,
    db: Session = Depends(get_db),
):
    """Séances sur une période (par défaut : tout), plus la configuration
    utile à la console (matières, créneaux habituels, inscrits actifs)."""
    q = select(PrepaAdjurisSeance).order_by(PrepaAdjurisSeance.date_debut)
    if debut:
        q = q.where(PrepaAdjurisSeance.date_debut >= _utc(debut))
    if fin:
        q = q.where(PrepaAdjurisSeance.date_debut < _utc(fin))
    if niveau:
        q = q.where(PrepaAdjurisSeance.niveau == niveau.strip().upper())
    if matiere:
        q = q.where(PrepaAdjurisSeance.matiere_key == matiere)
    rows = db.execute(q).scalars().all()

    inscrits: dict[str, int] = {}
    for key, in db.execute(
        select(PrepaAdjurisEnrollment.matiere_key).where(
            PrepaAdjurisEnrollment.status.in_(facturation.STATUTS_EN_COURS)
        )
    ).all():
        inscrits[key] = inscrits.get(key, 0) + 1

    return {
        "total": len(rows),
        "items": [_serialize_seance(s) for s in rows],
        "matieres": [
            {
                "key": k,
                "label": PREPA_MATIERE_NAMES.get(k, k),
                "niveau": matiere_niveau(k),
                "prix_configures": prices_configures(k),
                "inscrits": inscrits.get(k, 0),
            }
            for k in PREPA_PRICES
        ],
        "creneaux": {
            n: {"jour_semaine": j, "heure": h, "duree_minutes": d}
            for n, (j, h, d) in PREPA_CRENEAUX.items()
        },
        "niveaux": list(PREPA_NIVEAUX),
        "facturation": {
            "prix_seance_cents": PREPA_PRIX_SEANCE_CENTS,
            "heure_prelevement": PREPA_HEURE_PRELEVEMENT,
            "fin_programme": PREPA_FIN_PROGRAMME,
        },
    }


@router.post("/agenda/seances")
def creer_seance(
    payload: SeanceCreeIn,
    simulation: bool = Query(False),
    db: Session = Depends(get_db),
):
    niveau = _niveau_ok(payload.niveau)
    key = _matiere_ok(payload.matiere_key, niveau)
    row = PrepaAdjurisSeance(
        niveau=niveau,
        matiere_key=key,
        titre=(payload.titre or "").strip(),
        date_debut=_utc(payload.date_debut),
        duree_minutes=payload.duree_minutes or 60,
        lien=(payload.lien or "").strip(),
        statut="prevue",
    )
    db.add(row)
    return _enregistrer(db, [key], simulation, lambda: {"seance": _serialize_seance(row)})


@router.post("/agenda/serie")
def creer_serie(
    payload: SerieIn,
    simulation: bool = Query(False),
    db: Session = Depends(get_db),
):
    """
    Crée une séance par semaine pour une ou plusieurs matières (par défaut
    les 3 matières du niveau), du `du` au `au` inclus, sauf les dates
    exclues. Une séance qui existe déjà (même matière, même début) est
    ignorée : la série peut être rejouée sans créer de doublon.
    """
    niveau = _niveau_ok(payload.niveau)
    matieres = payload.matieres or matieres_du_niveau(niveau)
    matieres = [_matiere_ok(m, niveau) for m in matieres]
    if payload.au < payload.du:
        raise HTTPException(400, detail="La date de fin précède la date de début.")
    if (payload.au - payload.du).days > 400:
        raise HTTPException(400, detail="Série trop longue (13 mois maximum).")

    heure, minute = (int(x) for x in payload.heure.split(":"))
    exclues = set(payload.sauf)

    jours: list[date] = []
    d = payload.du + timedelta(days=(payload.jour_semaine - payload.du.weekday()) % 7)
    while d <= payload.au:
        if d not in exclues:
            jours.append(d)
        d += timedelta(days=7)

    existantes = {
        (k, facturation._aware(dt))
        for k, dt in db.execute(
            select(PrepaAdjurisSeance.matiere_key, PrepaAdjurisSeance.date_debut).where(
                PrepaAdjurisSeance.matiere_key.in_(matieres)
            )
        ).all()
    }

    crees, ignores = [], 0
    for jour in jours:
        debut = datetime(jour.year, jour.month, jour.day, heure, minute, tzinfo=TZ)
        for key in matieres:
            if (key, debut.astimezone(timezone.utc)) in existantes:
                ignores += 1
                continue
            row = PrepaAdjurisSeance(
                niveau=niveau, matiere_key=key, titre=payload.titre.strip(),
                date_debut=debut.astimezone(timezone.utc), duree_minutes=payload.duree_minutes,
                lien=payload.lien.strip(), statut="prevue",
            )
            db.add(row)
            crees.append(row)

    return _enregistrer(
        db, matieres, simulation,
        lambda: {
            "crees": len(crees),
            "ignores": ignores,
            "dates": [j.isoformat() for j in jours],
            "seances": [_serialize_seance(r) for r in crees] if not simulation else [],
        },
    )


@router.patch("/agenda/seances/{seance_id}")
def modifier_seance(
    seance_id: int,
    payload: SeanceModifIn,
    simulation: bool = Query(False),
    db: Session = Depends(get_db),
):
    s = _seance_ou_404(db, seance_id)
    if payload.titre is not None:
        s.titre = payload.titre.strip()
    if payload.date_debut is not None:
        s.date_debut = _utc(payload.date_debut)
    if payload.duree_minutes is not None:
        s.duree_minutes = payload.duree_minutes
    if payload.lien is not None:
        s.lien = payload.lien.strip()
    if payload.statut is not None:
        if payload.statut not in ("prevue", "annulee"):
            raise HTTPException(400, detail="Statut invalide (prevue | annulee).")
        s.statut = payload.statut
    return _enregistrer(db, [s.matiere_key], simulation, lambda: {"seance": _serialize_seance(s)})


@router.post("/agenda/seances/{seance_id}/annuler")
def annuler_seance(seance_id: int, simulation: bool = Query(False), db: Session = Depends(get_db)):
    s = _seance_ou_404(db, seance_id)
    s.statut = "annulee"
    return _enregistrer(db, [s.matiere_key], simulation, lambda: {"seance": _serialize_seance(s)})


@router.post("/agenda/seances/{seance_id}/retablir")
def retablir_seance(seance_id: int, simulation: bool = Query(False), db: Session = Depends(get_db)):
    s = _seance_ou_404(db, seance_id)
    s.statut = "prevue"
    return _enregistrer(db, [s.matiere_key], simulation, lambda: {"seance": _serialize_seance(s)})


@router.delete("/agenda/seances/{seance_id}")
def supprimer_seance(seance_id: int, simulation: bool = Query(False), db: Session = Depends(get_db)):
    """Pour corriger une erreur de saisie. Un cours qui n'a pas lieu doit
    plutôt être annulé : il reste visible, barré, dans l'agenda."""
    s = _seance_ou_404(db, seance_id)
    key = s.matiere_key
    db.delete(s)
    return _enregistrer(db, [key], simulation, lambda: {"deleted_id": seance_id})


# ============================================================
# Facturation
# ============================================================

@router.get("/facturation")
def facturation_eleves(
    niveau: str | None = None,
    matiere: str | None = None,
    email: str | None = None,
    db: Session = Depends(get_db),
):
    """Échéancier de chaque inscription en cours, et total à venir par mois."""
    q = select(PrepaAdjurisEnrollment).where(
        PrepaAdjurisEnrollment.status.in_(facturation.STATUTS_EN_COURS)
    ).order_by(PrepaAdjurisEnrollment.matiere_key, PrepaAdjurisEnrollment.email)
    if matiere:
        q = q.where(PrepaAdjurisEnrollment.matiere_key == matiere)
    if email:
        q = q.where(PrepaAdjurisEnrollment.email == email.strip().lower())
    rows = db.execute(q).scalars().all()
    if niveau:
        rows = [r for r in rows if matiere_niveau(r.matiere_key) == niveau.strip().upper()]

    maintenant = facturation.utcnow()
    items = [facturation.resume_facturation(r, maintenant) for r in rows]

    a_venir: dict[str, dict] = {}
    for it in items:
        for m in it["mois"]:
            if m["preleve"]:
                continue
            ligne = a_venir.setdefault(m["mois"], {"mois": m["mois"], "date": m["date"], "montant_cents": 0, "seances": 0})
            ligne["montant_cents"] += m["montant_cents"]
            ligne["seances"] += m["seances"]

    return {
        "total": len(items),
        "items": items,
        "a_venir": [a_venir[k] for k in sorted(a_venir)],
    }


@router.post("/facturation/{enrollment_id}/resynchroniser")
def resynchroniser(enrollment_id: int, db: Session = Depends(get_db)):
    """Recalcul forcé de l'abonnement de cette inscription et mise à jour
    de son échéancier Stripe (crée l'échéancier s'il manque)."""
    e = db.execute(
        select(PrepaAdjurisEnrollment).where(PrepaAdjurisEnrollment.id == enrollment_id)
    ).scalar_one_or_none()
    if not e:
        raise HTTPException(404, detail="Inscription introuvable.")
    if not e.echeancier or not e.stripe_subscription_id:
        raise HTTPException(400, detail="Inscription historique : pas d'échéancier calculé.")
    impacts = facturation.synchroniser_abonnement(db, e.stripe_subscription_id, forcer_stripe=True)
    db.refresh(e)
    return {"ok": e.echeancier_statut == "ok", "impacts": impacts, **facturation.resume_facturation(e)}


@router.get("/alertes")
def alertes(db: Session = Depends(get_db)):
    items = facturation.alertes(db)
    return {"total": len(items), "items": items}


# ============================================================
# Lien de paiement manuel
# ============================================================

@router.post("/lien-paiement")
def lien_paiement(payload: LienPaiementIn, db: Session = Depends(get_db)):
    """
    Crée un lien de paiement Stripe pour un élève, à lui transmettre.

    Cas d'usage : un élève a déjà réglé ses 20 € autrement (ex. M1, cours
    du 3 octobre). inscrit_le = juste avant ce cours et
    inscription_deja_payee = true : rien n'est encaissé au paiement, la carte
    est enregistrée, et les séances suivantes sont prélevées en fin de mois.
    Les mois dont le prélèvement est déjà passé ne sont jamais facturés.
    """
    email = payload.email.strip().lower()
    matieres = list(dict.fromkeys(payload.matieres))
    inconnues = [m for m in matieres if m not in PREPA_PRICES]
    if inconnues:
        raise HTTPException(400, detail=f"Matière inconnue : {inconnues[0]}")

    deja = set(db.execute(
        select(PrepaAdjurisEnrollment.matiere_key).where(
            PrepaAdjurisEnrollment.email == email,
            PrepaAdjurisEnrollment.status.in_(facturation.STATUTS_EN_COURS),
            PrepaAdjurisEnrollment.source == "stripe",
        )
    ).scalars().all())
    a_payer = [m for m in matieres if m not in deja]
    if not a_payer:
        raise HTTPException(400, detail="Cet élève a déjà un abonnement pour ces matières.")

    inscrit_le = _utc(payload.inscrit_le) if payload.inscrit_le else None
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    session = _creer_checkout_session(
        db, a_payer, email, user=user,
        inscrit_le=inscrit_le,
        inscription_deja_payee=payload.inscription_deja_payee,
        metadata_extra={"origine": "lien_admin"},
    )

    maintenant = facturation.utcnow()
    devis = facturation.calculer_devis(db, a_payer, inscrit_le or maintenant, facturable_apres=maintenant)
    return {
        "checkout_url": session.url,
        "expire_le": datetime.fromtimestamp(session.expires_at, timezone.utc).isoformat()
        if getattr(session, "expires_at", None) else None,
        "matieres": a_payer,
        **_devis_public(devis, None if payload.inscription_deja_payee else PREPA_PRIX_SEANCE_CENTS),
    }
