"""
Page d'accueil publique (landing) : données des embeds hero et avis.

Public (sans compte) :
  GET /landing/hero          → 4 premiers du classement (pseudo, photo) et nombre de membres
  GET /landing/avis          → avis affichés, dans l'ordre de la console
  GET /landing/embed/hero    → embeds/landing_hero.html servi par l'API
  GET /landing/embed/avis    → embeds/landing_avis.html servi par l'API

Console (admin, onglet « Avis ») :
  GET    /admin/avis         → tous les avis, masqués compris
  GET    /admin/avis/comptes?q= → recherche du compte à relier (pseudo ou email)
  POST   /admin/avis         → ajoute un avis relié à un compte (user_id)
  PATCH  /admin/avis/{id}    → texte, note, mention, ordre, affiché / masqué
  DELETE /admin/avis/{id}

Le nom et la photo d'un avis viennent toujours du compte relié : aucun avis
sans vrai compte NAVIRE.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.core.cloudinary_client import resolve_avatar_url
from app.db.database import get_db
from app.db.models import LandingAvis, User
from app.routers.admin_console import require_admin

router = APIRouter(tags=["landing"])

EMBEDS_DIR = Path(__file__).resolve().parents[2] / "embeds"


# ============================================================
# Helpers
# ============================================================

def _clean(v: str | None) -> str | None:
    v = " ".join((v or "").split())
    return v or None


def _auteur(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "avatar_url": resolve_avatar_url(u.avatar_url),
        "university": u.university or "",
        "study_level": u.study_level or "",
    }


def _mention(a: LandingAvis) -> str:
    if a.mention:
        return a.mention
    u = a.user
    return " · ".join(x for x in (u.study_level, u.university) if x)


def _avis_public(a: LandingAvis) -> dict:
    return {
        "id": a.id,
        "texte": a.texte,
        "note": int(a.note or 5),
        "mention": _mention(a),
        "user": {"username": a.user.username, "avatar_url": resolve_avatar_url(a.user.avatar_url)},
    }


def _avis_admin(a: LandingAvis) -> dict:
    return {
        "id": a.id,
        "texte": a.texte,
        "note": int(a.note or 5),
        "mention": a.mention,
        "mention_affichee": _mention(a),
        "position": a.position,
        "active": bool(a.active),
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "user": {**_auteur(a.user), "email": a.user.email},
    }


def _embed(fichier: str, titre: str) -> HTMLResponse:
    contenu = (EMBEDS_DIR / fichier).read_text(encoding="utf-8")
    contenu = contenu.replace('API: "https://navire-app-api.onrender.com"', 'API: ""')
    return HTMLResponse(
        '<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{titre}</title></head>'
        f'<body style="margin:0;background:#05070d">{contenu}</body></html>'
    )


# ============================================================
# Public
# ============================================================

@router.get("/landing/hero")
def landing_hero(db: Session = Depends(get_db)):
    """4 premiers du classement général (Elo), pour la pastille du hero."""
    top = db.execute(
        select(User).where(User.elo > 0).order_by(desc(User.elo), User.id).limit(4)
    ).scalars().all()
    membres = db.execute(select(func.count(User.id))).scalar_one()
    return {
        "top": [{"username": u.username, "avatar_url": resolve_avatar_url(u.avatar_url)} for u in top],
        "membres": int(membres or 0),
    }


@router.get("/landing/avis")
def landing_avis(db: Session = Depends(get_db)):
    rows = db.execute(
        select(LandingAvis)
        .where(LandingAvis.active.is_(True))
        .order_by(LandingAvis.position, LandingAvis.id)
    ).scalars().all()
    return {"items": [_avis_public(a) for a in rows if a.user]}


@router.get("/landing/embed/hero", response_class=HTMLResponse, include_in_schema=False)
def embed_hero():
    return _embed("landing_hero.html", "NAVIRE")


@router.get("/landing/embed/avis", response_class=HTMLResponse, include_in_schema=False)
def embed_avis():
    return _embed("landing_avis.html", "Avis NAVIRE")


# ============================================================
# Console admin
# ============================================================

class AvisIn(BaseModel):
    user_id: int
    texte: str = Field(..., min_length=1, max_length=600)
    note: int = Field(default=5, ge=1, le=5)
    mention: str | None = Field(default=None, max_length=80)
    active: bool = True


class AvisPatch(BaseModel):
    user_id: int | None = None
    texte: str | None = Field(default=None, min_length=1, max_length=600)
    note: int | None = Field(default=None, ge=1, le=5)
    mention: str | None = Field(default=None, max_length=80)
    position: int | None = None
    active: bool | None = None


def _texte(v: str | None) -> str:
    # Garde les retours à la ligne, retire les espaces en trop autour.
    t = "\n".join(" ".join(l.split()) for l in (v or "").strip().splitlines()).strip()
    if not t:
        raise HTTPException(400, detail="Avis vide.")
    return t


def _user_or_404(db: Session, user_id: int) -> User:
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, detail="Compte introuvable.")
    return u


@router.get("/admin/avis")
def admin_avis_list(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    rows = db.execute(
        select(LandingAvis).order_by(LandingAvis.position, LandingAvis.id)
    ).scalars().all()
    return {"items": [_avis_admin(a) for a in rows if a.user]}


@router.get("/admin/avis/comptes")
def admin_avis_comptes(q: str = "", db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """
    Comptes dont le pseudo ou l'email contient `q` (sans tenir compte des
    majuscules), exacts d'abord. Route propre à l'onglet Avis : /admin/users
    (app/routers/admin.py) ne filtre pas.
    """
    s = (q or "").strip()
    if len(s) < 2:
        return {"items": []}
    like = f"%{s}%"
    rows = db.execute(
        select(User)
        .where(User.email.ilike(like) | User.username.ilike(like))
        .order_by(User.id)
        .limit(50)
    ).scalars().all()
    low = s.lower()
    rows.sort(key=lambda u: (
        0 if low in ((u.email or "").lower(), (u.username or "").lower()) else 1,
        0 if (u.username or "").lower().startswith(low) or (u.email or "").lower().startswith(low) else 1,
        u.id,
    ))
    return {"items": [{**_auteur(u), "email": u.email} for u in rows[:8]]}


@router.post("/admin/avis")
def admin_avis_create(body: AvisIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    _user_or_404(db, body.user_id)
    position = (db.execute(select(func.max(LandingAvis.position))).scalar_one() or 0) + 1
    a = LandingAvis(
        user_id=body.user_id,
        texte=_texte(body.texte),
        note=body.note,
        mention=_clean(body.mention),
        active=body.active,
        position=position,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return _avis_admin(a)


@router.patch("/admin/avis/{avis_id}")
def admin_avis_update(
    avis_id: int, body: AvisPatch, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    a = db.get(LandingAvis, avis_id)
    if not a:
        raise HTTPException(404, detail="Avis introuvable.")
    fields = body.model_dump(exclude_unset=True)
    if fields.get("user_id") is not None:
        _user_or_404(db, body.user_id)
        a.user_id = body.user_id
    if fields.get("texte") is not None:
        a.texte = _texte(body.texte)
    if fields.get("note") is not None:
        a.note = body.note
    if "mention" in fields:
        a.mention = _clean(body.mention)
    if fields.get("position") is not None:
        a.position = body.position
    if fields.get("active") is not None:
        a.active = body.active
    db.commit()
    db.refresh(a)
    return _avis_admin(a)


@router.delete("/admin/avis/{avis_id}")
def admin_avis_delete(avis_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    a = db.get(LandingAvis, avis_id)
    if not a:
        raise HTTPException(404, detail="Avis introuvable.")
    db.delete(a)
    db.commit()
    return {"ok": True, "deleted_id": avis_id}
