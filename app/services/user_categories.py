"""
Catégories de profil (universités, niveaux d'études) et statuts vérifiés.

- Les listes vivent dans la table meta_options, gérée depuis la console.
  Au premier accès elle est remplie avec les préréglages historiques
  (app/data/options.json s'il existe, sinon DEFAULT_OPTIONS de meta.py), plus
  le niveau « Doctorant », qui demande une vérification.
- Un niveau marqué requires_verification n'est jamais appliqué directement :
  le choisir ouvre une demande (StatusVerification). Le niveau et son badge
  arrivent sur le compte quand un admin la valide.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import MetaOption, StatusVerification, User

KINDS = ("university", "study_level")

DOCTORANT = {
    "label": "Doctorant",
    "badge": "Doctorant",
    "verification_hint": (
        "Indique ton université, ton école doctorale, ton sujet de thèse et ton "
        "directeur de thèse. Une attestation d'inscription en doctorat accélère la validation."
    ),
}

OPTIONS_PATH = Path("app/data/options.json")


# ============================================================
# Listes
# ============================================================

def _legacy_presets() -> dict:
    from app.routers.meta import DEFAULT_OPTIONS

    try:
        with open(OPTIONS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("universities") and data.get("study_levels"):
            return data
    except Exception:
        pass
    return DEFAULT_OPTIONS


def ensure_seeded(db: Session) -> None:
    """Remplit meta_options avec les préréglages si la table est vide."""
    if db.execute(select(func.count(MetaOption.id))).scalar_one():
        return

    presets = _legacy_presets()
    for i, label in enumerate(presets.get("universities", [])):
        db.add(MetaOption(kind="university", label=label, position=i))
    levels = list(presets.get("study_levels", []))
    for i, label in enumerate(levels):
        db.add(MetaOption(kind="study_level", label=label, position=i))
    if DOCTORANT["label"] not in levels:
        db.add(MetaOption(
            kind="study_level", position=len(levels), requires_verification=True, **DOCTORANT,
        ))
    try:
        db.commit()
    except Exception:
        # Deux workers qui remplissent en même temps : l'autre a gagné.
        db.rollback()


def list_options(db: Session, kind: str | None = None, include_inactive: bool = False) -> list[MetaOption]:
    ensure_seeded(db)
    stmt = select(MetaOption)
    if kind:
        stmt = stmt.where(MetaOption.kind == kind)
    if not include_inactive:
        stmt = stmt.where(MetaOption.active.is_(True))
    stmt = stmt.order_by(MetaOption.kind, MetaOption.position, MetaOption.label)
    return list(db.execute(stmt).scalars().all())


def option_dict(o: MetaOption) -> dict:
    return {
        "id": o.id,
        "kind": o.kind,
        "label": o.label,
        "position": o.position,
        "active": bool(o.active),
        "requires_verification": bool(o.requires_verification),
        "badge": o.badge,
        "verification_hint": o.verification_hint,
    }


def public_options(db: Session) -> dict:
    """Format de GET /meta/options : listes simples (compatibles) + détails."""
    opts = list_options(db)
    unis = [o for o in opts if o.kind == "university"]
    levels = [o for o in opts if o.kind == "study_level"]
    return {
        "universities": [o.label for o in unis],
        "study_levels": [o.label for o in levels],
        "study_level_options": [
            {
                "label": o.label,
                "requires_verification": bool(o.requires_verification),
                "badge": o.badge,
                "verification_hint": o.verification_hint,
            }
            for o in levels
        ],
    }


def find_option(db: Session, kind: str, label: str) -> MetaOption | None:
    ensure_seeded(db)
    return db.execute(
        select(MetaOption).where(MetaOption.kind == kind, MetaOption.label == label)
    ).scalar_one_or_none()


# ============================================================
# Statuts vérifiés
# ============================================================

def verification_dict(v: StatusVerification, with_user: bool = False) -> dict:
    d = {
        "id": v.id,
        "label": v.label,
        "badge": v.badge,
        "status": v.status,
        "message": v.message,
        "proof_url": v.proof_url,
        "rejection_reason": v.rejection_reason,
        "created_at": v.created_at.isoformat() if v.created_at else None,
        "reviewed_at": v.reviewed_at.isoformat() if v.reviewed_at else None,
    }
    if with_user and v.user is not None:
        u = v.user
        d["user"] = {
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "university": u.university,
            "study_level": u.study_level,
        }
    return d


def _open_request(db: Session, user: User, label: str) -> StatusVerification | None:
    return db.execute(
        select(StatusVerification)
        .where(
            StatusVerification.user_id == user.id,
            StatusVerification.label == label,
            StatusVerification.status.in_(("pending", "approved")),
        )
        .order_by(StatusVerification.id.desc())
    ).scalars().first()


def request_verification(
    db: Session,
    user: User,
    option: MetaOption,
    message: str = "",
    proof_url: str | None = None,
) -> StatusVerification:
    """Ouvre (ou complète) la demande en cours pour cette option."""
    v = _open_request(db, user, option.label)
    if v is None:
        v = StatusVerification(
            user_id=user.id, option_id=option.id, label=option.label, badge=option.badge,
            status="pending",
        )
        db.add(v)
    if v.status == "pending":
        if message:
            v.message = message.strip()[:4000]
        if proof_url:
            v.proof_url = proof_url
    db.flush()
    return v


def apply_study_level(db: Session, user: User, level: str | None) -> str:
    """
    Applique un niveau d'études choisi par l'élève.

    Renvoie "set" si le niveau est posé, "pending" si une vérification est
    nécessaire (le niveau actuel est alors conservé), "unchanged" sinon.
    Ne commit pas : à l'appelant de le faire.
    """
    level = (level or "").strip()
    if not level or level == user.study_level:
        return "unchanged"

    option = find_option(db, "study_level", level)
    if option is not None and option.requires_verification:
        existing = _open_request(db, user, option.label)
        if existing is not None and existing.status == "approved":
            user.study_level = option.label
            return "set"
        if user.id is None:
            db.flush()
        request_verification(db, user, option)
        return "pending"

    user.study_level = level
    return "set"


def user_badges(db: Session, user: User) -> list[str]:
    rows = db.execute(
        select(StatusVerification.badge).where(
            StatusVerification.user_id == user.id,
            StatusVerification.status == "approved",
            StatusVerification.badge.is_not(None),
        )
    ).scalars().all()
    return sorted(set(b for b in rows if b))


def user_verifications(db: Session, user: User) -> list[StatusVerification]:
    return list(db.execute(
        select(StatusVerification)
        .where(StatusVerification.user_id == user.id)
        .order_by(StatusVerification.id.desc())
    ).scalars().all())


def review(db: Session, v: StatusVerification, admin: User, decision: str, reason: str = "") -> None:
    """decision : approve | reject | revoke."""
    now = datetime.now(timezone.utc)
    user = db.get(User, v.user_id)

    if decision == "approve":
        v.status = "approved"
        v.rejection_reason = None
        if user is not None:
            user.study_level = v.label
    elif decision == "reject":
        v.status = "rejected"
        v.rejection_reason = (reason or "").strip()[:300] or None
    elif decision == "revoke":
        v.status = "revoked"
        v.rejection_reason = (reason or "").strip()[:300] or None
        if user is not None and user.study_level == v.label:
            user.study_level = None
    else:
        raise ValueError(decision)

    v.reviewed_by_id = admin.id
    v.reviewed_at = now
