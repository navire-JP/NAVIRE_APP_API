"""
app/routers/navire.py
======================
Endpoints NAVIRE : chat, historique des 10 dernières conversations, et
ingestion (admin) des sources.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import User, NavireConversation, NavireChunk, File
from app.routers.auth import get_current_user
from app.core.limits import check_navire_daily_limit, navire_daily_usage
from app.services.navire_chat import send_message
from app.services.navire_ingest import (
    backfill_missing,
    ingest_all_lib_documents,
    ingest_all_prepa_courses,
    ingest_all_veille_items,
    ingest_user_file,
)

router = APIRouter(prefix="/navire", tags=["navire"])

# Logger d'uvicorn : écrit sur stderr sans tampon, donc visible tout de suite
# dans les logs Render (un print() peut rester bloqué dans le tampon de stdout).
logger = logging.getLogger("uvicorn.error")

ADMIN_CODE = "THORKISHERE"  # même convention que le reste de l'admin console


# ============================================================
# Schémas
# ============================================================

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    conversation_id: int | None = None
    mode: str = "chat"  # chat | cas_pratique | note_synthese
    # Sources interrogées par la recherche juridique (onglet Sources du front)
    use_navire_docs: bool = True          # cours + actus de la base NAVIRE
    use_my_docs: bool = True              # PDF perso de l'utilisateur
    file_ids: list[int] | None = Field(default=None, max_length=200)  # None = tous les PDF perso
    # Niveau de modèle : rapide (gratuit, quota du jour) | avance | expert (crédits)
    tier: str = "rapide"


class ChatResponse(BaseModel):
    conversation_id: int
    reply: str
    sources: list[dict]
    tier: str = "rapide"
    credits_spent: int = 0
    credits: dict | None = None


# ============================================================
# Chat
# ============================================================

@router.post("/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.core.navire_models import TIERS, get_tier
    from app.services import navire_credits

    if (payload.tier or "rapide").lower() not in TIERS:
        raise HTTPException(status_code=400, detail="Modèle inconnu.")
    tier_id, tier_conf = get_tier(payload.tier)
    cost = int(tier_conf["cost"])

    if cost > 0:
        # Modèles Avancé / Expert : payés en crédits, hors quota quotidien.
        solde = navire_credits.summary(db, user)
        if solde["balance"] < cost:
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "NAVIRE_CREDITS_EXHAUSTED",
                    "message": (
                        f"Il te faut {cost} crédit{'s' if cost > 1 else ''} pour le modèle "
                        f"{tier_conf['label']} : il t'en reste {solde['balance']}. "
                        "Tes crédits se rechargent le 1er du mois ; le modèle Rapide reste disponible."
                    ),
                    "credits": solde,
                    "cost": cost,
                },
            )
    else:
        check_navire_daily_limit(user, db)

    if payload.mode not in ("chat", "cas_pratique", "note_synthese"):
        raise HTTPException(status_code=400, detail="Mode invalide.")

    # Une exception non gérée donne un 500 sans en-têtes CORS : le navigateur
    # l'affiche alors comme une coupure réseau. On renvoie une vraie erreur
    # JSON (avec CORS) et on garde la trace complète dans les logs Render.
    try:
        result = send_message(
            db=db,
            user=user,
            conversation_id=payload.conversation_id,
            user_message=payload.message,
            mode=payload.mode,
            use_navire_docs=payload.use_navire_docs,
            use_my_docs=payload.use_my_docs,
            file_ids=payload.file_ids,
            tier=tier_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.exception("NAVIRE chat error (user %s)", user.id)
        message = "NAVIRE a rencontré une erreur. Réessaie dans un instant."
        if user.is_admin:
            # Diagnostic direct dans le chat, réservé aux admins
            message += f"\n\n[admin] {type(e).__name__} : {str(e)[:400]}"
        raise HTTPException(
            status_code=502,
            detail={"code": "NAVIRE_ERROR", "message": message},
        )

    # Débit seulement si le modèle a vraiment répondu.
    spent = 0
    if cost > 0 and result.get("completed"):
        navire_credits.debit(db, user, cost, tier_id, result.get("model") or "", result.get("conversation_id"))
        spent = cost
    result["credits_spent"] = spent
    result["credits"] = navire_credits.summary(db, user)
    return result


@router.get("/quota")
def get_quota(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Questions NAVIRE (modèle Rapide) utilisées aujourd'hui (limit None =
    illimité), crédits du mois pour les modèles Avancé / Expert, et la liste
    des niveaux pour le sélecteur.
    """
    from app.core.navire_models import public_tiers
    from app.services import navire_credits

    used, limit = navire_daily_usage(user, db)
    return {
        "plan": user.plan,
        "used": used,
        "limit": limit,
        "remaining": None if limit is None else max(0, limit - used),
        "credits": navire_credits.summary(db, user),
        "tiers": public_tiers(),
    }


# ============================================================
# Documents perso — état d'indexation pour l'onglet Sources / Fichiers
# ============================================================

def _owned_file(db: Session, user: User, file_id: int) -> File:
    f = db.query(File).filter(File.id == file_id, File.user_id == user.id).first()
    if not f:
        raise HTTPException(status_code=404, detail="Fichier introuvable.")
    return f


@router.get("/documents")
def list_documents(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    PDF perso de l'utilisateur avec le nombre de chunks indexés : 0 = pas
    lisible par NAVIRE (PDF scanné sans texte, ou indexation échouée).
    """
    from sqlalchemy import func as sa_func

    files = (
        db.query(File)
        .filter(File.user_id == user.id)
        .order_by(File.created_at.desc())
        .all()
    )
    counts = dict(
        db.query(NavireChunk.source_id, sa_func.count(NavireChunk.id))
        .filter(
            NavireChunk.source_type == "pdf_user",
            NavireChunk.owner_user_id == user.id,
        )
        .group_by(NavireChunk.source_id)
        .all()
    )
    return [
        {
            "id": f.id,
            "filename": f.filename_original,
            "size_bytes": f.size_bytes,
            "created_at": f.created_at,
            "expires_at": f.expires_at,
            "chunks": counts.get(f.id, 0),
        }
        for f in files
    ]


@router.post("/documents/{file_id}/reindex")
def reindex_document(
    file_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Relance l'indexation d'un PDF perso (après un échec à l'import)."""
    f = _owned_file(db, user, file_id)
    try:
        created = ingest_user_file(db, f)
    except Exception as e:
        print(f"⚠️ Réindexation NAVIRE échouée pour file {f.id}: {e}")
        raise HTTPException(status_code=502, detail="Indexation impossible pour le moment.")
    return {"id": f.id, "chunks": created}


# ============================================================
# Historique — 10 dernières conversations
# ============================================================

@router.get("/conversations")
def list_conversations(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversations = (
        db.query(NavireConversation)
        .filter(NavireConversation.user_id == user.id)  # cloisonnement
        .order_by(NavireConversation.updated_at.desc())
        .limit(10)
        .all()
    )
    return [
        {
            "id": c.id,
            "title": c.title,
            "mode": c.mode,
            "updated_at": c.updated_at,
            "message_count": len(c.messages or []),
        }
        for c in conversations
    ]


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conv = (
        db.query(NavireConversation)
        .filter(
            NavireConversation.id == conversation_id,
            NavireConversation.user_id == user.id,  # cloisonnement — jamais d'accès croisé
        )
        .first()
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation introuvable.")

    return {
        "id": conv.id,
        "title": conv.title,
        "mode": conv.mode,
        "messages": conv.messages,
        "created_at": conv.created_at,
        "updated_at": conv.updated_at,
    }


@router.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conv = (
        db.query(NavireConversation)
        .filter(
            NavireConversation.id == conversation_id,
            NavireConversation.user_id == user.id,
        )
        .first()
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation introuvable.")
    db.delete(conv)
    db.commit()
    return {"ok": True}


# ============================================================
# Admin — ingestion des sources (à appeler manuellement après ajout
# de contenu, ou brancher sur un cron plus tard)
# ============================================================

@router.post("/admin/ingest/courses")
def admin_ingest_courses(
    db: Session = Depends(get_db),
    x_admin_code: str = Header(...),
):
    if x_admin_code != ADMIN_CODE:
        raise HTTPException(status_code=403, detail="Accès refusé.")
    return ingest_all_prepa_courses(db)


@router.post("/admin/ingest/actus")
def admin_ingest_actus(
    db: Session = Depends(get_db),
    x_admin_code: str = Header(...),
    limit: int | None = None,
):
    if x_admin_code != ADMIN_CODE:
        raise HTTPException(status_code=403, detail="Accès refusé.")
    return ingest_all_veille_items(db, limit=limit)


@router.post("/admin/ingest/user-files")
def admin_ingest_user_files(
    db: Session = Depends(get_db),
    x_admin_code: str = Header(...),
):
    """
    RATTRAPAGE : ingère tous les PDF users déjà présents en base qui n'ont
    pas encore de chunks dans l'index (uploadés avant le déploiement du hook).
    Idempotent : les fichiers déjà indexés sont réingérés proprement
    (suppression des anciens chunks avant réinsertion).
    """
    if x_admin_code != ADMIN_CODE:
        raise HTTPException(status_code=403, detail="Accès refusé.")

    files = db.query(File).all()
    processed = 0
    chunks_total = 0
    errors: list[dict] = []
    for f in files:
        try:
            created = ingest_user_file(db, f)
            chunks_total += created
            processed += 1
        except Exception as e:
            errors.append({"file_id": f.id, "filename": f.filename_original, "error": str(e)})

    return {
        "files_processed": processed,
        "chunks_created": chunks_total,
        "errors": errors,
    }


@router.get("/admin/index-status")
def admin_index_status(
    db: Session = Depends(get_db),
    x_admin_code: str = Header(...),
):
    """
    DIAGNOSTIC : état réel de l'index vectoriel, par type de source.
    Permet de vérifier en un coup d'œil si les ingestions ont bien tourné.
    """
    if x_admin_code != ADMIN_CODE:
        raise HTTPException(status_code=403, detail="Accès refusé.")

    from sqlalchemy import func as sa_func

    rows = (
        db.query(NavireChunk.source_type, sa_func.count(NavireChunk.id))
        .group_by(NavireChunk.source_type)
        .all()
    )
    counts = {source_type: count for source_type, count in rows}
    return {
        "total_chunks": sum(counts.values()),
        "by_source": {
            "lib": counts.get("lib", 0),
            "lib_plus": counts.get("lib_plus", 0),
            "article": counts.get("article", 0),
            "actu": counts.get("actu", 0),
            "cours": counts.get("cours", 0),
            "pdf_user": counts.get("pdf_user", 0),
        },
    }


@router.post("/admin/ingest/lib")
def admin_ingest_lib(
    db: Session = Depends(get_db),
    x_admin_code: str = Header(...),
):
    """Réindexe tous les cours NAVIRE Lib (les non publiés sont retirés de l'index)."""
    if x_admin_code != ADMIN_CODE:
        raise HTTPException(status_code=403, detail="Accès refusé.")
    return ingest_all_lib_documents(db)


@router.post("/admin/ingest/missing")
def admin_ingest_missing(
    db: Session = Depends(get_db),
    x_admin_code: str = Header(...),
):
    """Rattrapage : indexe les cours Lib, articles et actus qui n'ont pas encore de chunks."""
    if x_admin_code != ADMIN_CODE:
        raise HTTPException(status_code=403, detail="Accès refusé.")
    return backfill_missing(db)