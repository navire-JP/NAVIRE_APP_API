"""
app/services/devoirs.py
=======================
Devoirs Prép'AdJuris : un étudiant dépose une copie (PDF) depuis la vue Prépa
de NAVIRE Lib, les professeurs de son groupe la corrigent depuis leur espace
(onglet Devoirs). Routes : app/routers/lib_prepa.py (étudiant) et
app/routers/professeurs.py (professeur).

Règles :
  - Dépôt réservé aux matières où l'étudiant a une inscription active.
  - Le groupe de l'étudiant est figé au dépôt (classe_id) : ce sont les
    professeurs de ce groupe qui voient la copie. Les admins voient tout.
  - Une copie se retire tant qu'elle n'est pas corrigée.
  - Corriger = commentaire, note et/ou copie corrigée (PDF) : la copie passe
    « corrigée » et l'étudiant reçoit un mail.
  - Les mails (nouvelle copie aux professeurs, correction à l'étudiant) sont
    envoyés au mieux : un échec d'envoi ne bloque jamais l'action.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from html import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import FRONTEND_URL
from app.core.prepa_adjuris_config import PREPA_MATIERE_NAMES, matiere_label
from app.db.models import (
    PrepaAdjurisAffectation,
    PrepaAdjurisClasse,
    PrepaAdjurisDevoir,
    PrepaAdjurisProfesseur,
    User,
)

logger = logging.getLogger(__name__)

STATUTS = ("depose", "corrige")


def aware(dt: datetime | None) -> datetime | None:
    """SQLite rend des dates naïves : on les relit en UTC."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _iso(dt: datetime | None) -> str | None:
    dt = aware(dt)
    return dt.isoformat() if dt else None


def groupe_pour(db: Session, user_id: int, matiere_key: str) -> int | None:
    """Groupe de l'étudiant dans la matière (même règle que l'espace professeurs)."""
    from app.services.professeurs import eleves_de_la_matiere

    for groupe, lignes in eleves_de_la_matiere(db, matiere_key):
        if any(e.user_id == user_id for e in lignes):
            return groupe
    return None


def devoir_row(db: Session, d: PrepaAdjurisDevoir, *, pour_prof: bool = False) -> dict:
    row = {
        "id": d.id,
        "matiere_key": d.matiere_key,
        "matiere": PREPA_MATIERE_NAMES.get(d.matiere_key, d.matiere_key),
        "matiere_label": matiere_label(d.matiere_key),
        "classe_id": d.classe_id,
        "titre": d.titre,
        "message": d.message,
        "filename_original": d.filename_original,
        "size_bytes": d.size_bytes,
        "statut": d.statut,
        "commentaire": d.commentaire,
        "note": d.note,
        "correction": bool(d.correction_path),
        "correction_filename": d.correction_filename or None,
        "corrige_le": _iso(d.corrige_le),
        "created_at": _iso(d.created_at),
    }
    if d.corrige_par_id:
        prof = db.execute(
            select(PrepaAdjurisProfesseur).where(PrepaAdjurisProfesseur.user_id == d.corrige_par_id)
        ).scalar_one_or_none()
        u = db.get(User, d.corrige_par_id)
        nom = " ".join(x for x in ((prof.prenom, prof.nom) if prof else ()) if x).strip()
        row["corrige_par"] = nom or (u.username if u else None)
    else:
        row["corrige_par"] = None
    if pour_prof:
        u = db.get(User, d.user_id)
        c = db.get(PrepaAdjurisClasse, d.classe_id) if d.classe_id else None
        row["etudiant"] = {
            "user_id": d.user_id,
            "username": u.username if u else None,
            "email": u.email if u else None,
            "university": u.university if u else None,
        }
        row["classe_nom"] = c.nom if c else None
    return row


def emails_professeurs(db: Session, classe_id: int | None) -> list[str]:
    if not classe_id:
        return []
    rows = db.execute(
        select(User.email)
        .join(PrepaAdjurisProfesseur, PrepaAdjurisProfesseur.user_id == User.id)
        .join(PrepaAdjurisAffectation, PrepaAdjurisAffectation.professeur_id == PrepaAdjurisProfesseur.id)
        .where(
            PrepaAdjurisAffectation.classe_id == classe_id,
            PrepaAdjurisProfesseur.status == "active",
        )
    ).scalars().all()
    return sorted({e for e in rows if e})


# ============================================================
# Mails (envoyés en tâche de fond, au mieux)
# ============================================================

def _bouton(url: str, texte: str) -> str:
    return (
        f'<a href="{url}" style="display:inline-block;background:#4F7DFF;color:#fff;'
        'padding:12px 22px;border-radius:99px;text-decoration:none;font-weight:600;margin:14px 0;">'
        f"{texte}</a>"
    )


def mail_nouvelle_copie(destinataires: list[str], etudiant: str, matiere: str, titre: str) -> None:
    from app.services.email import layout, send_mail

    subject = f"Nouvelle copie à corriger : {matiere}"
    html = layout(f"""
    <p style="margin-top:0;">Bonjour,</p>
    <p><strong>{escape(etudiant)}</strong> a déposé une copie en <strong>{escape(matiere)}</strong> :
    « {escape(titre)} ».</p>
    <p>Elle t'attend dans l'onglet Devoirs de ton espace professeur.</p>
    {_bouton(f"{FRONTEND_URL}/professeurs", "Ouvrir mon espace")}
""", preheader=f"{etudiant} a déposé « {titre} ».")
    for to in destinataires:
        try:
            send_mail(to, subject, html)
        except Exception:  # pragma: no cover - send_mail ne lève pas, par sécurité
            logger.exception("devoirs: mail professeur impossible")


def mail_copie_corrigee(to: str, prenom: str, matiere: str, titre: str) -> None:
    from app.services.email import layout, send_mail

    subject = f"Ta copie est corrigée : {titre}"
    html = layout(f"""
    <p style="margin-top:0;">Bonjour {escape(prenom)},</p>
    <p>Ton professeur a corrigé ta copie en <strong>{escape(matiere)}</strong> : « {escape(titre)} ».</p>
    <p>Retrouve son commentaire dans l'onglet Devoirs de la vue Prépa, sur NAVIRE Lib.</p>
    {_bouton(f"{FRONTEND_URL}/lib?prepa=devoirs", "Voir la correction")}
""", preheader="Le commentaire de ton professeur t'attend sur NAVIRE Lib.")
    try:
        send_mail(to, subject, html)
    except Exception:  # pragma: no cover
        logger.exception("devoirs: mail étudiant impossible")
