"""
app/routers/social.py
=====================
Posts des élèves, abonnements entre élèves et posts enregistrés.

Un post : 100 caractères au plus, avec au choix une image (JPG, PNG, WEBP)
et un document (PDF ou Word, pour partager un devoir). Il s'affiche sur le
profil de son auteur (embeds/profil.html) ; le fil /feed viendra ensuite.

Routes :
  POST   /social/posts                    écrire un post (multipart)
  DELETE /social/posts/{id}               supprimer (auteur ou admin)
  GET    /social/users/{id}/posts         posts d'un élève, page par page
  PUT    /social/posts/{id}/save          enregistrer un post
  DELETE /social/posts/{id}/save          retirer des enregistrés
  GET    /social/me/saved                 mes posts enregistrés
  PUT    /social/users/{id}/follow        suivre un élève
  DELETE /social/users/{id}/follow        ne plus le suivre
  GET    /social/users/{id}/followers     ses abonnés
  GET    /social/users/{id}/following     ses abonnements

Les profils (GET /users/me/profile-summary, GET /users/{id}/profile)
renvoient aussi un bloc `social` : compteurs, « je le suis », et la première
page de ses posts (voir `social_block`).
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.cloudinary_client import (
    MAX_AVATAR_BYTES,
    MAX_POST_DOC_BYTES,
    is_allowed_image,
    post_doc_ext,
    resolve_avatar_url,
    upload_post_doc,
    upload_post_image,
)
from app.db.database import get_db
from app.db.models import User, UserFollow, UserPost, UserPostSave
from app.routers.auth import get_current_user, get_current_user_optional
from app.services.elo_rewards import revoke_post, reward_post

router = APIRouter(prefix="/social", tags=["social"])

POST_MAX_CHARS = 100
POSTS_PER_DAY = 20          # garde-fou contre le spam, large pour un usage normal
PAGE = 10
PAGE_MAX = 30


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime | None) -> str | None:
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.isoformat()


def _clean_text(raw: str | None) -> str:
    """Espaces superflus retirés ; les retours à la ligne simples sont gardés."""
    lines = [" ".join(l.split()) for l in (raw or "").replace("\r", "").split("\n")]
    out = "\n".join(lines).strip()
    while "\n\n\n" in out:
        out = out.replace("\n\n\n", "\n\n")
    return out


def _user_mini(u: User | None) -> dict | None:
    if u is None:
        return None
    return {
        "id": u.id,
        "username": u.username,
        "avatar_url": resolve_avatar_url(u.avatar_url),
        "university": u.university,
    }


def _saved_ids(db: Session, viewer: User | None, post_ids: list[int]) -> set[int]:
    if not viewer or not post_ids:
        return set()
    return set(db.execute(
        select(UserPostSave.post_id).where(
            UserPostSave.user_id == viewer.id, UserPostSave.post_id.in_(post_ids)
        )
    ).scalars().all())


def _save_counts(db: Session, post_ids: list[int]) -> dict[int, int]:
    if not post_ids:
        return {}
    rows = db.execute(
        select(UserPostSave.post_id, func.count(UserPostSave.id))
        .where(UserPostSave.post_id.in_(post_ids))
        .group_by(UserPostSave.post_id)
    ).all()
    return {pid: int(n) for pid, n in rows}


def _serialize(db: Session, posts: list[UserPost], viewer: User | None) -> list[dict]:
    ids = [p.id for p in posts]
    saved, counts = _saved_ids(db, viewer, ids), _save_counts(db, ids)
    out = []
    for p in posts:
        out.append({
            "id": p.id,
            "text": p.text,
            "image_url": p.image_url,
            "document": {"url": p.doc_url, "name": p.doc_name, "size": p.doc_size} if p.doc_url else None,
            "created_at": _iso(p.created_at),
            "author": _user_mini(p.author),
            "is_saved": p.id in saved,
            "saves_count": counts.get(p.id, 0),
            "can_delete": bool(viewer and (viewer.id == p.author_id or viewer.is_admin)),
        })
    return out


def posts_page(db: Session, author_id: int, viewer: User | None, offset: int = 0, limit: int = PAGE) -> dict:
    total = db.execute(
        select(func.count(UserPost.id)).where(UserPost.author_id == author_id)
    ).scalar_one()
    posts = db.execute(
        select(UserPost)
        .where(UserPost.author_id == author_id)
        .order_by(UserPost.created_at.desc(), UserPost.id.desc())
        .offset(offset).limit(limit)
    ).scalars().all()
    return {
        "total": int(total or 0),
        "offset": offset,
        "has_more": offset + len(posts) < (total or 0),
        "items": _serialize(db, list(posts), viewer),
    }


def social_block(db: Session, u: User, viewer: User | None) -> dict:
    """Compteurs d'abonnés / abonnements, « je le suis » et premiers posts."""
    followers = db.execute(
        select(func.count(UserFollow.id)).where(UserFollow.followed_id == u.id)
    ).scalar_one()
    following = db.execute(
        select(func.count(UserFollow.id)).where(UserFollow.follower_id == u.id)
    ).scalar_one()
    is_following = follows_me = False
    if viewer and viewer.id != u.id:
        is_following = db.execute(
            select(UserFollow.id).where(UserFollow.follower_id == viewer.id, UserFollow.followed_id == u.id)
        ).first() is not None
        follows_me = db.execute(
            select(UserFollow.id).where(UserFollow.follower_id == u.id, UserFollow.followed_id == viewer.id)
        ).first() is not None
    return {
        "followers": int(followers or 0),
        "following": int(following or 0),
        "is_following": is_following,
        "follows_me": follows_me,
        "posts": posts_page(db, u.id, viewer),
    }


def _get_user(db: Session, user_id: int) -> User:
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")
    return u


def _get_post(db: Session, post_id: int) -> UserPost:
    p = db.get(UserPost, post_id)
    if not p:
        raise HTTPException(status_code=404, detail="Post introuvable")
    return p


# ============================================================
# Posts
# ============================================================

@router.post("/posts")
async def create_post(
    text: str = Form(default=""),
    image: UploadFile | None = File(default=None),
    document: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    text = _clean_text(text)
    if len(text) > POST_MAX_CHARS:
        raise HTTPException(status_code=400, detail=f"{POST_MAX_CHARS} caractères au plus.")

    has_image = image is not None and bool(image.filename)
    has_doc = document is not None and bool(document.filename)
    if not text and not has_image and not has_doc:
        raise HTTPException(status_code=400, detail="Le post est vide.")

    since = _utcnow() - timedelta(days=1)
    today = db.execute(
        select(func.count(UserPost.id)).where(UserPost.author_id == me.id, UserPost.created_at >= since)
    ).scalar_one()
    if (today or 0) >= POSTS_PER_DAY:
        raise HTTPException(status_code=429, detail="Trop de posts aujourd'hui, réessaie demain.")

    # Contrôles avant tout envoi à Cloudinary
    img_bytes = doc_bytes = None
    doc_ext = None
    if has_image:
        if not is_allowed_image(image.content_type):
            raise HTTPException(status_code=400, detail="Image : JPG, PNG ou WEBP.")
        img_bytes = await image.read()
        if len(img_bytes) > MAX_AVATAR_BYTES:
            raise HTTPException(status_code=413, detail=f"Image trop lourde ({MAX_AVATAR_BYTES // (1024 * 1024)} Mo max).")
    if has_doc:
        doc_ext = post_doc_ext(document.filename, document.content_type)
        if not doc_ext:
            raise HTTPException(status_code=400, detail="Document : PDF ou Word (.docx, .doc).")
        doc_bytes = await document.read()
        if len(doc_bytes) > MAX_POST_DOC_BYTES:
            raise HTTPException(status_code=413, detail=f"Document trop lourd ({MAX_POST_DOC_BYTES // (1024 * 1024)} Mo max).")
        if not doc_bytes:
            raise HTTPException(status_code=400, detail="Document vide.")

    key = secrets.token_hex(6)
    post = UserPost(author_id=me.id, text=text)
    try:
        if img_bytes is not None:
            post.image_url = upload_post_image(img_bytes, me.id, key)
        if doc_bytes is not None:
            post.doc_url = upload_post_doc(doc_bytes, me.id, key, doc_ext)
            post.doc_name = (document.filename or f"document.{doc_ext}").strip()[:200]
            post.doc_size = len(doc_bytes)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Échec de l'envoi du fichier : {exc}")

    db.add(post)
    db.commit()
    db.refresh(post)
    # +5 (+7 avec un devoir/fichier joint), +10 de plus pour le tout premier post
    elo_gagne = reward_post(db, me, post.id, has_file=post.doc_url is not None)
    db.refresh(post)
    return {**_serialize(db, [post], me)[0], "elo_gagne": elo_gagne}


@router.delete("/posts/{post_id}")
def delete_post(post_id: int, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    p = _get_post(db, post_id)
    if p.author_id != me.id and not me.is_admin:
        raise HTTPException(status_code=403, detail="Seul l'auteur peut supprimer ce post.")
    author_id, post_id = p.author_id, p.id
    db.execute(delete(UserPostSave).where(UserPostSave.post_id == p.id))
    db.delete(p)
    db.commit()
    revoke_post(db, author_id, post_id)   # le post supprimé ne rapporte plus d'elo
    return {"ok": True}


@router.get("/users/{user_id}/posts")
def user_posts(
    user_id: int,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=PAGE, ge=1, le=PAGE_MAX),
    db: Session = Depends(get_db),
    viewer: User | None = Depends(get_current_user_optional),
):
    _get_user(db, user_id)
    return posts_page(db, user_id, viewer, offset, limit)


# ============================================================
# Enregistrer
# ============================================================

@router.put("/posts/{post_id}/save")
def save_post(post_id: int, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    _get_post(db, post_id)
    exists = db.execute(
        select(UserPostSave.id).where(UserPostSave.user_id == me.id, UserPostSave.post_id == post_id)
    ).first()
    if not exists:
        db.add(UserPostSave(user_id=me.id, post_id=post_id))
        db.commit()
    return {"ok": True, "is_saved": True}


@router.delete("/posts/{post_id}/save")
def unsave_post(post_id: int, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    db.execute(delete(UserPostSave).where(UserPostSave.user_id == me.id, UserPostSave.post_id == post_id))
    db.commit()
    return {"ok": True, "is_saved": False}


@router.get("/me/saved")
def my_saved(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=PAGE, ge=1, le=PAGE_MAX),
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    total = db.execute(
        select(func.count(UserPostSave.id)).where(UserPostSave.user_id == me.id)
    ).scalar_one()
    posts = db.execute(
        select(UserPost)
        .join(UserPostSave, UserPostSave.post_id == UserPost.id)
        .where(UserPostSave.user_id == me.id)
        .order_by(UserPostSave.created_at.desc(), UserPostSave.id.desc())
        .offset(offset).limit(limit)
    ).scalars().all()
    return {
        "total": int(total or 0),
        "offset": offset,
        "has_more": offset + len(posts) < (total or 0),
        "items": _serialize(db, list(posts), me),
    }


# ============================================================
# Suivre
# ============================================================

def _counts(db: Session, user_id: int) -> dict:
    return {
        "followers": int(db.execute(
            select(func.count(UserFollow.id)).where(UserFollow.followed_id == user_id)
        ).scalar_one() or 0),
    }


@router.put("/users/{user_id}/follow")
def follow(user_id: int, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    _get_user(db, user_id)
    if user_id == me.id:
        raise HTTPException(status_code=400, detail="Tu ne peux pas te suivre toi-même.")
    exists = db.execute(
        select(UserFollow.id).where(UserFollow.follower_id == me.id, UserFollow.followed_id == user_id)
    ).first()
    if not exists:
        db.add(UserFollow(follower_id=me.id, followed_id=user_id))
        db.commit()
    return {"ok": True, "is_following": True, **_counts(db, user_id)}


@router.delete("/users/{user_id}/follow")
def unfollow(user_id: int, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    db.execute(delete(UserFollow).where(UserFollow.follower_id == me.id, UserFollow.followed_id == user_id))
    db.commit()
    return {"ok": True, "is_following": False, **_counts(db, user_id)}


def _follow_list(db: Session, user_id: int, viewer: User | None, mode: str, offset: int, limit: int) -> dict:
    _get_user(db, user_id)
    if mode == "followers":
        where, other = UserFollow.followed_id == user_id, UserFollow.follower_id
    else:
        where, other = UserFollow.follower_id == user_id, UserFollow.followed_id
    total = db.execute(select(func.count(UserFollow.id)).where(where)).scalar_one()
    users = db.execute(
        select(User)
        .join(UserFollow, other == User.id)
        .where(where)
        .order_by(UserFollow.created_at.desc(), UserFollow.id.desc())
        .offset(offset).limit(limit)
    ).scalars().all()
    mine = set()
    if viewer and users:
        mine = set(db.execute(
            select(UserFollow.followed_id).where(
                UserFollow.follower_id == viewer.id, UserFollow.followed_id.in_([u.id for u in users])
            )
        ).scalars().all())
    return {
        "total": int(total or 0),
        "offset": offset,
        "has_more": offset + len(users) < (total or 0),
        "items": [
            {**_user_mini(u), "is_following": u.id in mine, "is_me": bool(viewer and viewer.id == u.id)}
            for u in users
        ],
    }


@router.get("/users/{user_id}/followers")
def followers(
    user_id: int,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    viewer: User | None = Depends(get_current_user_optional),
):
    return _follow_list(db, user_id, viewer, "followers", offset, limit)


@router.get("/users/{user_id}/following")
def following(
    user_id: int,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    viewer: User | None = Depends(get_current_user_optional),
):
    return _follow_list(db, user_id, viewer, "following", offset, limit)
