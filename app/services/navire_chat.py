"""
app/services/navire_chat.py
=============================
Logique de conversation NAVIRE : function-calling + RAG.

Le modèle dispose d'OUTILS qu'il choisit lui-même selon la question :
  - get_user_stats     -> ELO, rang, réussite QCM du user courant (SQL direct)
  - list_user_files    -> les PDF hébergés par le user courant (SQL direct)
  - get_latest_actus   -> dernières actus juridiques, option catégorie (SQL direct)
  - search_legal_base  -> RAG vectoriel (cours + actus + PDF du user, cloisonné)

Règles :
  - Questions plateforme (stats, fichiers, actus) -> outils SQL, données réelles
  - Questions juridiques -> search_legal_base UNIQUEMENT, jamais de droit inventé
  - Small talk (bonjour, test, merci) -> réponse naturelle sans outil
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from openai import OpenAI
from sqlalchemy.orm import Session

from app.db.models import (
    NavireConversation,
    User,
    File,
    VeilleItem,
    QcmSessionHistory,
)
from app.services.navire_ingest import embed_text
from app.services.navire_retrieval import search_chunks

OPENAI_MODEL_NAVIRE = os.getenv("OPENAI_MODEL_NAVIRE", os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
MAX_CONVERSATIONS_PER_USER = 10
TOP_K_CHUNKS = 8
MAX_TOOL_ROUNDS = 3

_client = OpenAI()


# ============================================================
# SYSTEM PROMPT
# ============================================================

SYSTEM_PROMPT = """Tu es NAVIRE, l'assistant intelligent de la plateforme MAJOR, destiné aux étudiants en droit français.

Tu as accès à des OUTILS. Utilise-les systématiquement quand la question s'y prête :
- Stats, ELO, classement, réussite QCM de l'utilisateur -> get_user_stats
- Fichiers/PDF de l'utilisateur -> list_user_files
- Actualités juridiques (dernières actus, actus par thème) -> get_latest_actus
- Toute question de DROIT (cours, notions, méthodo, contenu d'un PDF) -> search_legal_base

RÈGLES SUR LE DROIT (strictes) :
- Tu ne réponds à une question juridique QU'À PARTIR des extraits retournés par search_legal_base.
- Si search_legal_base ne retourne rien d'utile, dis-le honnêtement et suggère de reformuler ou d'importer un document. N'invente JAMAIS de contenu juridique.
- Cite ta source quand tu réponds sur le droit (nom du cours, de la leçon, de l'actu ou du document).

RÈGLES GÉNÉRALES :
- Pour les salutations, remerciements ou messages de test, réponds naturellement et brièvement, sans outil.
- Pour les données de plateforme (stats, fichiers, actus), les outils renvoient les VRAIES données : appuie-toi dessus, ne devine jamais.
- Si on te demande des actus pertinentes, utilise get_latest_actus (éventuellement avec une catégorie) et recommande les plus utiles avec une phrase d'explication chacune.
- Style : direct, clair, structuré, tutoiement. Tu es un outil de travail efficace, pas un chatbot bavard.
- Réponds toujours en français."""


# ============================================================
# OUTILS — définitions OpenAI
# ============================================================

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_user_stats",
            "description": "Récupère les statistiques réelles de l'utilisateur courant : ELO, rang au classement, taux de réussite QCM, nombre de sessions.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_user_files",
            "description": "Liste les fichiers PDF hébergés par l'utilisateur courant sur la plateforme.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_latest_actus",
            "description": "Récupère les dernières actualités juridiques publiées sur la plateforme. Peut filtrer par catégorie.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["civil", "penal", "public", "affaires", "general"],
                        "description": "Catégorie d'actus (optionnel).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Nombre d'actus à récupérer (défaut 5, max 10).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_legal_base",
            "description": "Recherche sémantique dans la base juridique : cours MAJOR (L1/L2/L3 + méthodo), actualités, et documents PDF personnels de l'utilisateur. À utiliser pour TOUTE question de droit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "La question ou les termes juridiques à rechercher.",
                    }
                },
                "required": ["query"],
            },
        },
    },
]


# ============================================================
# OUTILS — implémentations (SQL direct, cloisonné par user)
# ============================================================

def _tool_get_user_stats(db: Session, user: User) -> dict:
    rank = (
        db.query(User)
        .filter(User.elo > user.elo)
        .count()
    ) + 1
    total_ranked = db.query(User).filter(User.elo > 0).count()

    sessions = (
        db.query(QcmSessionHistory)
        .filter(QcmSessionHistory.user_id == user.id)
        .all()
    )
    total_sessions = len(sessions)
    total_correct = sum(s.correct_answers for s in sessions)
    total_answered = sum(s.correct_answers + s.wrong_answers for s in sessions)
    success_rate = round(100 * total_correct / total_answered, 1) if total_answered else 0.0

    return {
        "username": user.username,
        "elo": user.elo,
        "rank": rank,
        "total_ranked_users": total_ranked,
        "grade": user.grade,
        "plan": user.plan,
        "qcm": {
            "total_sessions": total_sessions,
            "questions_answered": total_answered,
            "success_rate_percent": success_rate,
        },
        "university": user.university or "non renseignée",
        "study_level": user.study_level or "non renseigné",
    }


def _tool_list_user_files(db: Session, user: User) -> dict:
    now = datetime.now(timezone.utc)
    files = (
        db.query(File)
        .filter(File.user_id == user.id)
        .order_by(File.created_at.desc())
        .all()
    )
    items = []
    for f in files:
        expired = f.expires_at is not None and f.expires_at.replace(tzinfo=timezone.utc) < now if f.expires_at and f.expires_at.tzinfo is None else (f.expires_at is not None and f.expires_at < now)
        items.append({
            "filename": f.filename_original,
            "size_kb": round(f.size_bytes / 1024),
            "uploaded_at": f.created_at.isoformat() if f.created_at else None,
            "expired": bool(expired),
        })
    return {"count": len(items), "files": items}


def _tool_get_latest_actus(db: Session, category: str | None = None, limit: int = 5) -> dict:
    limit = max(1, min(int(limit or 5), 10))
    q = db.query(VeilleItem).order_by(VeilleItem.created_at.desc())
    if category:
        q = q.filter(VeilleItem.category == category)
    items = q.limit(limit).all()
    return {
        "count": len(items),
        "actus": [
            {
                "titre": it.title,
                "essentiel": it.essentiel,
                "impact": it.impact,
                "categorie": it.category,
                "source": it.source_name,
                "date": it.published_at.isoformat() if it.published_at else (it.created_at.isoformat() if it.created_at else None),
            }
            for it in items
        ],
    }


def _tool_search_legal_base(db: Session, user: User, query: str) -> dict:
    query_embedding = embed_text(query)
    annee_filter = getattr(user, "prepa_annee", None)
    chunks = search_chunks(
        db,
        query_embedding=query_embedding,
        user_id=user.id,
        top_k=TOP_K_CHUNKS,
        annee_filter=annee_filter,
    )
    if not chunks:
        return {"found": False, "extraits": [], "note": "Aucun extrait pertinent. La base est peut-être vide ou la question hors périmètre."}

    extraits = []
    for c in chunks:
        label = {
            "cours": f"Cours — {c.extra.get('cours_titre', '')} / {c.extra.get('lecon_titre', '')}",
            "actu": f"Actualité — {c.extra.get('titre', '')} ({c.extra.get('source_name', '')})",
            "pdf_user": f"Votre document — {c.extra.get('filename', '')}",
        }.get(c.source_type, "Source")
        extraits.append({"source": label, "contenu": c.content})

    return {"found": True, "extraits": extraits}


def _run_tool(db: Session, user: User, name: str, arguments: dict) -> dict:
    try:
        if name == "get_user_stats":
            return _tool_get_user_stats(db, user)
        if name == "list_user_files":
            return _tool_list_user_files(db, user)
        if name == "get_latest_actus":
            return _tool_get_latest_actus(db, arguments.get("category"), arguments.get("limit", 5))
        if name == "search_legal_base":
            return _tool_search_legal_base(db, user, arguments.get("query", ""))
        return {"error": f"Outil inconnu : {name}"}
    except Exception as e:
        print(f"⚠️ NAVIRE tool {name} error: {e}")
        return {"error": f"Erreur lors de l'exécution de {name}."}


# ============================================================
# CONVERSATIONS — rétention + accès
# ============================================================

def _enforce_conversation_retention(db: Session, user_id: int) -> None:
    conversations = (
        db.query(NavireConversation)
        .filter(NavireConversation.user_id == user_id)
        .order_by(NavireConversation.updated_at.desc())
        .all()
    )
    if len(conversations) > MAX_CONVERSATIONS_PER_USER:
        for old_conv in conversations[MAX_CONVERSATIONS_PER_USER:]:
            db.delete(old_conv)
        db.commit()


def get_or_create_conversation(
    db: Session, user: User, conversation_id: int | None, mode: str = "chat"
) -> NavireConversation:
    if conversation_id:
        conv = (
            db.query(NavireConversation)
            .filter(
                NavireConversation.id == conversation_id,
                NavireConversation.user_id == user.id,  # cloisonnement
            )
            .first()
        )
        if conv:
            return conv

    conv = NavireConversation(user_id=user.id, messages=[], mode=mode, title="")
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


# ============================================================
# POINT D'ENTRÉE
# ============================================================

def send_message(
    db: Session,
    user: User,
    conversation_id: int | None,
    user_message: str,
    mode: str = "chat",
) -> dict:
    conv = get_or_create_conversation(db, user, conversation_id, mode)
    now_iso = datetime.now(timezone.utc).isoformat()

    # Historique de CETTE conversation (mémoire courte)
    history_messages = [
        {"role": m["role"], "content": m["content"]}
        for m in (conv.messages or [])[-10:]
    ]

    messages = (
        [{"role": "system", "content": SYSTEM_PROMPT}]
        + history_messages
        + [{"role": "user", "content": user_message}]
    )

    sources_cited: list[dict] = []

    # Boucle de function-calling : le modèle peut enchaîner plusieurs outils.
    assistant_reply = ""
    for _round in range(MAX_TOOL_ROUNDS + 1):
        rep = _client.chat.completions.create(
            model=OPENAI_MODEL_NAVIRE,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.3,
            max_tokens=1100,
        )
        msg = rep.choices[0].message

        if not msg.tool_calls:
            assistant_reply = (msg.content or "").strip()
            break

        # Le modèle demande des outils : on les exécute tous, on répond, on reboucle.
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ],
        })

        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            result = _run_tool(db, user, tc.function.name, args)

            # Tracer les sources RAG pour l'affichage front
            if tc.function.name == "search_legal_base" and result.get("found"):
                for ex in result.get("extraits", []):
                    sources_cited.append({"type": "rag", "label": ex.get("source", "")})

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, ensure_ascii=False),
            })
    else:
        assistant_reply = "Je n'ai pas réussi à finaliser ma réponse. Réessaie en reformulant."

    if not assistant_reply:
        assistant_reply = "Je n'ai pas de réponse à te donner sur ce point."

    # Sauvegarde de la conversation
    conv.messages = (conv.messages or []) + [
        {"role": "user", "content": user_message, "at": now_iso},
        {"role": "assistant", "content": assistant_reply, "at": datetime.now(timezone.utc).isoformat()},
    ]
    if not conv.title:
        conv.title = user_message[:80]
    db.add(conv)
    db.commit()
    db.refresh(conv)

    _enforce_conversation_retention(db, user.id)

    # Dédupliquer les sources
    seen = set()
    unique_sources = []
    for s in sources_cited:
        if s["label"] and s["label"] not in seen:
            seen.add(s["label"])
            unique_sources.append(s)

    return {
        "conversation_id": conv.id,
        "reply": assistant_reply,
        "sources": unique_sources,
    }