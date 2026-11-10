"""
app/services/navire_chat.py
=============================
Logique de conversation NAVIRE : function-calling + RAG.

Le modèle dispose d'OUTILS qu'il choisit lui-même selon la question :
  - get_user_stats     -> ELO, rang, réussite QCM du user courant (SQL direct)
  - list_user_files    -> les PDF hébergés par le user courant (SQL direct)
  - get_latest_actus   -> dernières actus ou articles NAVIRE (SQL direct)
  - search_legal_base  -> recherche (sens + références exactes) dans les cours
                          NAVIRE Lib, les articles, les actus et les PDF du user

Règles :
  - Questions plateforme (stats, fichiers, actus) -> outils SQL, données réelles
  - Questions juridiques -> search_legal_base d'abord ; la base NAVIRE prime,
    les connaissances générales du modèle complètent, en le disant
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
TOP_K_CHUNKS = 10
MAX_SOURCES = 6   # sources affichées sous une réponse (une par document)
MAX_TOOL_ROUNDS = 4

_client = OpenAI()


# ============================================================
# SYSTEM PROMPT
# ============================================================

SYSTEM_PROMPT = """Tu es NAVIRE AI, l'assistant juridique de la plateforme NAVIRE, pour les étudiants en droit français (de la L1 au doctorat et au CRFPA). Tu raisonnes comme un bon chargé de TD : précis, rigoureux, pédagogue.

OUTILS — utilise-les dès que la question s'y prête :
- Stats, ELO, classement, réussite QCM de l'utilisateur -> get_user_stats
- Fichiers / PDF de l'utilisateur -> list_user_files
- Dernières actualités juridiques ou derniers articles NAVIRE -> get_latest_actus (kind="article" pour les articles)
- Toute question de DROIT (notion, régime, jurisprudence, méthodologie, contenu d'un cours ou d'un PDF) -> search_legal_base, AVANT de répondre. Tu peux l'appeler plusieurs fois avec des formulations différentes (notion, article, arrêt, matière) si le premier résultat est pauvre.

LA BASE NAVIRE contient : les cours NAVIRE Lib, les articles rédigés par des doctorants vérifiés et l'équipe, les actualités juridiques, et les PDF personnels de l'utilisateur.

RÈGLES DE RÉPONSE EN DROIT :
1. Les extraits de search_legal_base priment : appuie-toi d'abord sur eux et cite-les précisément (« d'après le cours NAVIRE Lib *Droit des obligations*, section … », « d'après l'article de … »).
2. Si la base ne couvre pas tout, complète avec tes connaissances générales du droit français, en le signalant clairement (« En règle générale… », « Hors des cours NAVIRE… »). Tu n'es pas obligé de refuser de répondre.
3. Ne cite JAMAIS un numéro d'article, une date ou une référence d'arrêt dont tu n'es pas sûr : décris la règle et la juridiction plutôt qu'inventer une référence. Signale quand le droit a pu évoluer (réforme de 2016 du droit des contrats, etc.).
4. Structure : réponds d'abord à la question en une ou deux phrases, puis développe (définition, conditions, effets, exceptions, jurisprudence clé, exemple). Utilise des titres courts et des listes quand c'est utile.
5. MÉTHODOLOGIE, quand on te le demande ou que la question l'appelle :
   - Fiche d'arrêt : faits, procédure, prétentions, problème de droit, solution, portée.
   - Commentaire d'arrêt : sens, valeur, portée ; plan en deux parties et deux sous-parties, avec chapeaux.
   - Dissertation : définition des termes, problématique, plan en deux parties apparentes et équilibrées.
   - Cas pratique : faits qualifiés, problème de droit, règle (majeure), application (mineure), conclusion, pour chaque problème.
6. Adapte la profondeur au niveau de l'utilisateur : plus d'explications et d'exemples en L1-L2, plus de nuances, de débats doctrinaux et de jurisprudence en master et au-delà.

RÈGLES GÉNÉRALES :
- Salutations, remerciements, messages de test : réponse naturelle et brève, sans outil.
- Données de plateforme (stats, fichiers, actus) : appuie-toi sur les vraies données des outils, ne devine jamais.
- Style : direct, clair, structuré, tutoiement, en français.
- Termine une réponse juridique importante par un rappel discret de vérifier les points clés dans le cours ou les sources officielles quand c'est pertinent, sans le répéter à chaque message."""


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
            "description": "Récupère les dernières actualités juridiques ou les derniers articles NAVIRE (rédigés par des doctorants et l'équipe). Peut filtrer par catégorie.",
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
                    "kind": {
                        "type": "string",
                        "enum": ["actu", "article"],
                        "description": "« article » pour les articles NAVIRE, « actu » pour les actualités (optionnel : les deux).",
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
            "description": "Recherche dans la base juridique NAVIRE : cours NAVIRE Lib, articles des doctorants et de l'équipe, actualités, et PDF personnels de l'utilisateur. Retrouve aussi les références exactes (article 1240, n° de pourvoi). À utiliser pour TOUTE question de droit, éventuellement plusieurs fois.",
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


def _tool_get_latest_actus(db: Session, category: str | None = None, limit: int = 5, kind: str | None = None) -> dict:
    limit = max(1, min(int(limit or 5), 10))
    q = db.query(VeilleItem).order_by(VeilleItem.created_at.desc())
    if category:
        q = q.filter(VeilleItem.category == category)
    if kind in ("actu", "article"):
        q = q.filter(VeilleItem.kind == kind)
    items = q.limit(limit).all()
    return {
        "count": len(items),
        "actus": [
            {
                "type": it.kind or "actu",
                "titre": it.title,
                "essentiel": it.essentiel,
                "impact": it.impact,
                "categorie": it.category,
                "source": it.source_name,
                "auteur": it.author.username if it.kind == "article" and it.author else None,
                "lien": it.source_url,
                "date": it.published_at.isoformat() if it.published_at else (it.created_at.isoformat() if it.created_at else None),
            }
            for it in items
        ],
    }


def _source_types(use_navire_docs: bool, use_my_docs: bool, plus: bool = False) -> list[str]:
    types: list[str] = []
    if use_navire_docs:
        types += ["lib", "article", "actu", "cours"]
        if plus:
            types.append("lib_plus")   # cours réservés à NAVIRE Lib+
    if use_my_docs:
        types.append("pdf_user")
    return types


LIB_URL = os.getenv("NAVIRE_LIB_URL", "https://navire-ai.com/lib")
ACTUS_URL = os.getenv("NAVIRE_ACTUS_URL", "https://navire-ai.com/actus")


def _doc_label(c) -> str:
    """Libellé court d'une source, au niveau du document (sans le chemin de section)."""
    x = c.extra or {}
    if c.source_type in ("lib", "lib_plus"):
        return f"NAVIRE Lib — {x.get('titre', '')}"
    if c.source_type == "article":
        by = f" (par {x['author']})" if x.get("author") else ""
        return f"Article NAVIRE — {x.get('titre', '')}{by}"
    if c.source_type == "actu":
        return f"Actualité — {x.get('titre', '')}"
    if c.source_type == "cours":
        return f"Cours — {x.get('cours_titre', '')}"
    if c.source_type == "pdf_user":
        return f"Votre document — {x.get('filename', '')}"
    return "Source"


def _label(c) -> tuple[str, str | None]:
    """Libellé de citation et lien vers la source, selon le type d'extrait."""
    x = c.extra or {}
    if c.source_type in ("lib", "lib_plus"):
        section = f" › {x.get('section')}" if x.get("section") else ""
        return f"NAVIRE Lib — {x.get('titre', '')}{section}", f"{LIB_URL}?doc={c.source_id}"
    if c.source_type == "article":
        by = f" (par {x['author']})" if x.get("author") else ""
        return f"Article NAVIRE — {x.get('titre', '')}{by}", x.get("url")
    if c.source_type == "actu":
        return f"Actualité — {x.get('titre', '')} ({x.get('source_name', '')})", f"{ACTUS_URL}?actu={c.source_id}"
    if c.source_type == "cours":
        return f"Cours — {x.get('cours_titre', '')} / {x.get('lecon_titre', '')}", None
    if c.source_type == "pdf_user":
        return f"Votre document — {x.get('filename', '')}", None
    return "Source", None


def _tool_search_legal_base(
    db: Session,
    user: User,
    query: str,
    use_navire_docs: bool = True,
    use_my_docs: bool = True,
    file_ids: list[int] | None = None,
) -> dict:
    try:
        from app.routers.lib import has_plus
        plus = has_plus(db, user)
    except Exception:
        plus = False
    source_types = _source_types(use_navire_docs, use_my_docs, plus)
    if not source_types:
        return {
            "found": False,
            "extraits": [],
            "note": "L'utilisateur a déconnecté toutes les sources (base NAVIRE et documents perso). "
                    "Invite-le à en réactiver une dans l'onglet Sources.",
        }

    query_embedding = embed_text(query)
    annee_filter = getattr(user, "prepa_annee", None)
    chunks = search_chunks(
        db,
        query_embedding=query_embedding,
        user_id=user.id,
        top_k=TOP_K_CHUNKS,
        source_types=source_types,
        annee_filter=annee_filter,
        user_file_ids=file_ids if use_my_docs else None,
        query_text=query,
    )
    if not chunks:
        return {"found": False, "extraits": [], "note": "Aucun extrait pertinent. La base est peut-être vide ou la question hors périmètre."}

    extraits = []
    for c in chunks:
        label, url = _label(c)
        kind = "lib" if c.source_type == "lib_plus" else c.source_type
        extraits.append({
            "source": label, "kind": kind, "url": url, "contenu": c.content,
            # Une source = un document (cours, article, actu, PDF), pas un extrait
            "doc": f"{kind}:{c.source_id}",
            "doc_label": _doc_label(c),
        })

    return {"found": True, "extraits": extraits}


def _run_tool(db: Session, user: User, name: str, arguments: dict, search_opts: dict | None = None) -> dict:
    try:
        if name == "get_user_stats":
            return _tool_get_user_stats(db, user)
        if name == "list_user_files":
            return _tool_list_user_files(db, user)
        if name == "get_latest_actus":
            return _tool_get_latest_actus(db, arguments.get("category"), arguments.get("limit", 5), arguments.get("kind"))
        if name == "search_legal_base":
            return _tool_search_legal_base(db, user, arguments.get("query", ""), **(search_opts or {}))
        return {"error": f"Outil inconnu : {name}"}
    except Exception as e:
        print(f"⚠️ NAVIRE tool {name} error: {e}")
        return {"error": f"Erreur lors de l'exécution de {name}."}


def _user_context(user: User) -> str:
    """
    Qui est l'utilisateur, pour que NAVIRE l'appelle par son pseudo et
    adapte le niveau de ses explications à son année d'études.
    """
    lines = [f"- Pseudo : {user.username}"]
    if user.study_level:
        lines.append(f"- Niveau d'études : {user.study_level}")
    if user.university:
        lines.append(f"- Université : {user.university}")
    return (
        "\n\nUTILISATEUR (tu lui parles directement) :\n"
        + "\n".join(lines)
        + "\nAppelle-le par son pseudo quand c'est naturel (salutations, encouragements), "
        "sans le répéter à chaque message. Adapte le niveau de tes explications à son année."
    )


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
    use_navire_docs: bool = True,
    use_my_docs: bool = True,
    file_ids: list[int] | None = None,
    context: str | None = None,
    tier: str | None = None,
) -> dict:
    """
    context : texte ajouté au prompt système (non enregistré dans la
    conversation), par ex. l'extrait de cours lu dans NAVIRE Lib+.
    use_navire_docs / use_my_docs : sources que search_legal_base a le droit
    d'interroger (réglées par l'utilisateur dans l'onglet Sources).
    file_ids : sous-ensemble des PDF perso à utiliser (None = tous).
    tier : niveau de modèle choisi (rapide | avance | expert), voir
    app/core/navire_models.py. Le débit des crédits est fait par la route.
    """
    from app.core.navire_models import (
        candidates, get_tier, is_model_unavailable, is_reasoning_model, mark_dead, mark_working,
    )

    tier_id, tier_conf = get_tier(tier)
    model_queue = candidates(tier_id)   # replis si un modèle est retiré
    model = model_queue[0]
    conv = get_or_create_conversation(db, user, conversation_id, mode)
    search_opts = {
        "use_navire_docs": use_navire_docs,
        "use_my_docs": use_my_docs,
        "file_ids": file_ids,
    }
    now_iso = datetime.now(timezone.utc).isoformat()

    # Historique de CETTE conversation (mémoire courte)
    history_messages = [
        {"role": m["role"], "content": m["content"]}
        for m in (conv.messages or [])[-10:]
    ]

    system_prompt = SYSTEM_PROMPT + _user_context(user)
    if not use_navire_docs or not use_my_docs:
        actives = []
        if use_navire_docs:
            actives.append("la base NAVIRE (cours Lib, articles, actus)")
        if use_my_docs:
            actives.append("les documents personnels de l'utilisateur")
        system_prompt += (
            "\n\nSOURCES ACTIVES : l'utilisateur a limité la recherche à "
            + (" et ".join(actives) if actives else "aucune source")
            + ". search_legal_base n'interroge que ces sources."
        )
    if context:
        system_prompt += "\n\n" + context

    messages = (
        [{"role": "system", "content": system_prompt}]
        + history_messages
        + [{"role": "user", "content": user_message}]
    )

    sources_cited: list[dict] = []

    # Boucle de function-calling : le modèle peut enchaîner plusieurs outils.
    assistant_reply = ""
    completed = False   # vraie réponse du modèle (sinon : pas de débit de crédits)
    for _round in range(MAX_TOOL_ROUNDS + 1):
        while True:
            params = {"model": model, "messages": messages, "tools": TOOLS, "tool_choice": "auto"}
            if is_reasoning_model(model):
                # Modèle à raisonnement : pas de température ; le budget couvre
                # aussi le raisonnement, d'où une marge plus large.
                params["max_completion_tokens"] = tier_conf.get("reasoning_tokens", tier_conf["max_tokens"])
            else:
                params["temperature"] = 0.3
                params["max_tokens"] = tier_conf["max_tokens"]
            try:
                rep = _client.chat.completions.create(**params)
                mark_working(tier_id, model)
                break
            except Exception as e:
                # Modèle retiré ou absent du compte OpenAI : on passe au suivant.
                if not is_model_unavailable(e) or len(model_queue) <= 1:
                    raise
                print(f"⚠️ NAVIRE : modèle {model} indisponible ({tier_id}), repli. {str(e)[:160]}")
                mark_dead(model)
                model_queue.pop(0)
                model = model_queue[0]
        msg = rep.choices[0].message

        if not msg.tool_calls:
            assistant_reply = (msg.content or "").strip()
            completed = bool(assistant_reply)
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
            result = _run_tool(db, user, tc.function.name, args, search_opts)

            # Tracer les sources RAG pour l'affichage front
            if tc.function.name == "search_legal_base" and result.get("found"):
                for ex in result.get("extraits", []):
                    sources_cited.append({
                        "type": "rag",
                        "kind": ex.get("kind", ""),
                        "label": ex.get("doc_label") or ex.get("source", ""),
                        "url": ex.get("url"),
                        "doc": ex.get("doc"),
                    })

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, ensure_ascii=False),
            })
    else:
        assistant_reply = "Je n'ai pas réussi à finaliser ma réponse. Réessaie en reformulant."

    if not assistant_reply:
        assistant_reply = "Je n'ai pas de réponse à te donner sur ce point."

    # Une source par document (dans l'ordre de pertinence), au plus MAX_SOURCES
    seen = set()
    unique_sources = []
    for s in sources_cited:
        key = s.get("doc") or s["label"]
        if s["label"] and key not in seen:
            seen.add(key)
            unique_sources.append(s)
    unique_sources = unique_sources[:MAX_SOURCES]

    # Sauvegarde de la conversation (les sources sont gardées pour que
    # l'historique les réaffiche sous chaque réponse)
    conv.messages = (conv.messages or []) + [
        {"role": "user", "content": user_message, "at": now_iso, "tier": tier_id},
        {
            "role": "assistant",
            "content": assistant_reply,
            "at": datetime.now(timezone.utc).isoformat(),
            "sources": unique_sources,
            "tier": tier_id,
        },
    ]
    if not conv.title:
        conv.title = user_message[:80]
    db.add(conv)
    db.commit()
    db.refresh(conv)

    _enforce_conversation_retention(db, user.id)

    return {
        "conversation_id": conv.id,
        "reply": assistant_reply,
        "sources": unique_sources,
        "tier": tier_id,
        "model": model,
        "completed": completed,
    }