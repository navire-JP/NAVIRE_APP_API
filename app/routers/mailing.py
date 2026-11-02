"""
Mails de la console admin : rédiger, prévisualiser, s'envoyer un test, puis
envoyer à une ou plusieurs catégories (ambassadeurs, professeurs, une
matière Prép'AdJuris…), par Brevo, dans le gabarit NAVIRE et signé par
l'admin qui envoie (photo de profil + pseudo).

Règles métier : app/services/mailing.py.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import MailCampaign, User
from app.routers.auth import get_current_user
from app.services import mailing as svc

router = APIRouter(prefix="/admin/mailing", tags=["admin-mailing"])


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not getattr(user, "is_admin", False):
        raise HTTPException(status_code=403, detail="Admin only")
    return user


# ============================================================
# Schémas
# ============================================================

class AttachmentIn(BaseModel):
    name: str = Field(..., max_length=200)
    content_b64: str | None = None
    # Absent = PJ déjà enregistrée sur le brouillon, reprise telle quelle.


class CampaignIn(BaseModel):
    subject: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=50_000)
    segments: list[str] = Field(default_factory=list, max_length=100)
    attachments: list[AttachmentIn] | None = None
    # None = ne pas toucher aux PJ du brouillon.


class PreviewIn(BaseModel):
    subject: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=50_000)


class RecipientsIn(BaseModel):
    segments: list[str] = Field(default_factory=list, max_length=100)


# ============================================================
# Helpers
# ============================================================

def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _row(db: Session, c: MailCampaign, full: bool = False) -> dict:
    sender = db.get(User, c.sent_by_id or c.created_by_id) if (c.sent_by_id or c.created_by_id) else None
    out = {
        "id": c.id,
        "subject": c.subject,
        "segments": [
            {"key": k, "label": svc.segment_label(db, k) or k} for k in (c.segments or [])
        ],
        "attachments": [{"name": a["name"], "size": a["size"]} for a in (c.attachments or [])],
        "status": c.status,
        "recipients_count": c.recipients_count,
        "sent_count": c.sent_count,
        "failed_count": c.failed_count,
        "error": c.error,
        "author": sender.username if sender else None,
        "sent_at": _iso(c.sent_at),
        "created_at": _iso(c.created_at),
        "updated_at": _iso(c.updated_at),
    }
    if full:
        out["body"] = c.body
    return out


def _check_segments(db: Session, keys: list[str]) -> list[str]:
    keys = list(dict.fromkeys(k.strip() for k in keys if k and k.strip()))
    unknown = [k for k in keys if svc.segment_label(db, k) is None]
    if unknown:
        raise HTTPException(400, detail=f"Catégorie inconnue : {', '.join(unknown)}")
    return keys


def _get(db: Session, campaign_id: int) -> MailCampaign:
    c = db.get(MailCampaign, campaign_id)
    if c is None:
        raise HTTPException(404, detail="Mail introuvable.")
    return c


def _draft(db: Session, campaign_id: int) -> MailCampaign:
    c = _get(db, campaign_id)
    if c.status != "draft":
        raise HTTPException(409, detail="Ce mail a déjà été envoyé : dupliquez-le pour le renvoyer.")
    return c


def _apply(db: Session, c: MailCampaign, payload: CampaignIn) -> None:
    c.subject = payload.subject.strip()
    c.body = payload.body
    c.segments = _check_segments(db, payload.segments)
    if payload.attachments is not None:
        existantes = {a["name"]: a for a in (c.attachments or [])}
        items = []
        for a in payload.attachments:
            if a.content_b64:
                items.append({"name": a.name, "content_b64": a.content_b64})
            elif a.name in existantes:
                items.append(existantes[a.name])
            else:
                raise HTTPException(400, detail=f"« {a.name} » : contenu manquant.")
        try:
            c.attachments = svc.clean_attachments(items)
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc))


# ============================================================
# Catégories et aperçu
# ============================================================

@router.get("/segments")
def segments(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """Catégories de destinataires proposées, avec leur effectif."""
    return {
        "items": svc.list_segments(db),
        "limits": {
            "max_attachments": svc.MAX_ATTACHMENTS,
            "max_attachments_bytes": svc.MAX_ATTACHMENTS_BYTES,
            "extensions": sorted(svc.ALLOWED_EXTENSIONS),
        },
    }


@router.post("/recipients")
def recipients(payload: RecipientsIn, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """Destinataires effectifs d'une sélection (union, sans doublons)."""
    keys = _check_segments(db, payload.segments)
    dest = svc.resolve_recipients(db, keys)
    items = [{"email": e, "prenom": p} for e, p in sorted(dest.items())]
    return {"count": len(items), "items": items}


@router.post("/preview")
def preview(payload: PreviewIn, admin: User = Depends(require_admin)):
    """Rendu exact du mail, signé par l'admin connecté ({prenom} = son pseudo)."""
    subject, html = svc.preview(payload.subject, payload.body, admin, admin.username)
    return {"subject": subject, "html": html}


# ============================================================
# Brouillons et historique
# ============================================================

@router.get("/campaigns")
def list_campaigns(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    rows = db.execute(
        select(MailCampaign).order_by(desc(MailCampaign.updated_at), desc(MailCampaign.id)).limit(200)
    ).scalars().all()
    return {"items": [_row(db, c) for c in rows]}


@router.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return _row(db, _get(db, campaign_id), full=True)


@router.post("/campaigns")
def create_campaign(payload: CampaignIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    c = MailCampaign(created_by_id=admin.id, status="draft", segments=[], attachments=[])
    _apply(db, c, payload)
    db.add(c)
    db.commit()
    db.refresh(c)
    return _row(db, c, full=True)


@router.put("/campaigns/{campaign_id}")
def update_campaign(
    campaign_id: int, payload: CampaignIn,
    db: Session = Depends(get_db), _: User = Depends(require_admin),
):
    c = _draft(db, campaign_id)
    _apply(db, c, payload)
    db.commit()
    db.refresh(c)
    return _row(db, c, full=True)


@router.post("/campaigns/{campaign_id}/duplicate")
def duplicate_campaign(campaign_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    src = _get(db, campaign_id)
    c = MailCampaign(
        created_by_id=admin.id, status="draft",
        subject=src.subject, body=src.body,
        segments=list(src.segments or []), attachments=list(src.attachments or []),
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return _row(db, c, full=True)


@router.delete("/campaigns/{campaign_id}")
def delete_campaign(campaign_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    c = _get(db, campaign_id)
    if c.status == "sending":
        raise HTTPException(409, detail="Envoi en cours.")
    db.delete(c)
    db.commit()
    return {"ok": True}


# ============================================================
# Envoi
# ============================================================

def _pret(c: MailCampaign) -> None:
    if not c.subject.strip():
        raise HTTPException(400, detail="L'objet est vide.")
    if not c.body.strip():
        raise HTTPException(400, detail="Le message est vide.")


@router.post("/campaigns/{campaign_id}/test")
def test_campaign(campaign_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Envoie le mail à l'admin connecté seulement (objet « [TEST] … »)."""
    c = _get(db, campaign_id)
    _pret(c)
    r = svc.send_test(c, admin)
    if not r["sent"]:
        raise HTTPException(502, detail=f"Envoi refusé : {r['detail']}")
    return {"ok": True, "to": admin.email, "detail": r["detail"]}


@router.post("/campaigns/{campaign_id}/send")
def send_campaign(campaign_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """
    Envoie le brouillon à ses catégories. Irréversible : la console demande
    confirmation avec le nombre exact de destinataires avant d'appeler ici.
    """
    c = _draft(db, campaign_id)
    _pret(c)
    if not c.segments:
        raise HTTPException(400, detail="Aucune catégorie de destinataires choisie.")
    if not svc.resolve_recipients(db, list(c.segments)):
        raise HTTPException(400, detail="Aucun destinataire dans ces catégories.")
    c = svc.send_campaign(db, c, admin)
    return _row(db, c, full=True)
