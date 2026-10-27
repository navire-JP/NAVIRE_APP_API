"""
app/routers/lib.py
==================
NAVIRE Lib : bibliothèque de cours, dissertations et fiches, lue dans l'embed
(embeds/lib.html), en deux modes :

- Lib (tout compte) : simple consultation, le document entier, sans point de
  passage ni Elo. Les documents Lib+ n'y figurent pas.
- Lib+ (abonnés NAVIRE AI, NAVIRE AI+ ou AdJuris) : tous les documents, et des
  points de passage dans le cours. Chacun pose une seule question ; tant
  qu'elle n'est pas réussie, la suite reste floutée. Chaque essai tire une
  autre question de la réserve du point de passage ; après LIB_ESSAIS_MAX
  échecs, l'élève peut passer à la suite (sans Elo). Un chat NAVIRE AI, nourri
  de l'extrait lu, suit chaque point de passage.

Étudiant (auth NAVIRE) :
  GET  /lib/catalogue                     → documents publiés + progression
  GET  /lib/documents/{id}?mode=lib|plus  → plan, blocs lisibles, verrou suivant
  GET  /lib/checkpoints/{id}/question     → la question de l'essai (sans réponse)
  POST /lib/checkpoints/{id}/submit       → correction, progression, Elo
  POST /lib/checkpoints/{id}/passer       → suite ouverte après les échecs
  POST /lib/chat                          → question à NAVIRE AI sur le cours
  GET  /lib/embed                         → page d'embed servie par l'API

Admin (header X-Admin-Code) :
  GET    /lib/admin/documents
  POST   /lib/admin/documents                       (import .docx / .pdf)
  GET    /lib/admin/documents/{id}                  (blocs + réponses)
  PATCH  /lib/admin/documents/{id}
  DELETE /lib/admin/documents/{id}
  POST   /lib/admin/documents/{id}/fichier          (nouvelle version du fichier)
  POST   /lib/admin/documents/{id}/detecter         (replace les points de passage)
  GET    /lib/admin/documents/{id}/source           (fichier d'origine)
  POST   /lib/admin/documents/{id}/checkpoints      (point de passage manuel)
  PATCH  /lib/admin/checkpoints/{id}
  DELETE /lib/admin/checkpoints/{id}
  POST   /lib/admin/checkpoints/{id}/generer-ia     (questions par OpenAI)

Le contenu verrouillé n'est jamais envoyé : l'API ne renvoie que les blocs
jusqu'au point de passage ouvert, plus quelques blocs d'aperçu que l'embed
affiche floutés.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File as FastAPIFile, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.config import STORAGE_PATH
from app.db.database import get_db
from app.core.limits import check_navire_daily_limit
from app.db.models import LibCheckpoint, LibDocument, LibProgress, PrepaAdjurisEnrollment, User
from app.routers.admin import verify_admin_code
from app.routers.auth import get_current_user, get_current_user_optional
from app.services import lib_parser as parser
from app.services.elo import apply_elo_delta, tier_from_elo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/lib", tags=["lib"])

LIB_DIR = Path(STORAGE_PATH) / "Lib"
LIB_MAX_BYTES = 50 * 1024 * 1024

# Réglages par défaut des points de passage détectés : le k-ième (k = 0, 1…)
# exige k × PALIER Elo et en rapporte RECOMPENSE. Un élève qui valide tout
# le cours gagne donc exactement l'Elo du verrou suivant ; l'Elo des QCM
# NAVIRE lui donne de l'avance.
ELO_PALIER = int(os.getenv("LIB_ELO_PALIER", "10"))
RECOMPENSE = int(os.getenv("LIB_RECOMPENSE_ELO", "10"))
SEUIL_PCT = int(os.getenv("LIB_SEUIL_PCT", "60"))
ESSAIS_MAX = int(os.getenv("LIB_ESSAIS_MAX", "3"))
TEASER_BLOCKS = 4

PLANS_PLUS = ("membre", "membre+")  # NAVIRE AI, NAVIRE AI+
REFUS_PLUS = "NAVIRE Lib+ est réservé aux abonnés NAVIRE AI, NAVIRE AI+ et AdJuris."

KINDS = ("cours", "dissertation", "fiche", "cas_pratique")


# ============================================================
# Outils
# ============================================================

def has_plus(db: Session, user: User | None) -> bool:
    """Lib+ : admin, abonné NAVIRE AI / NAVIRE AI+, ou inscrit AdJuris actif."""
    if not user:
        return False
    if user.is_admin or (user.plan or "free") in PLANS_PLUS:
        return True
    return db.execute(
        select(PrepaAdjurisEnrollment.id).where(
            PrepaAdjurisEnrollment.user_id == user.id, PrepaAdjurisEnrollment.status == "active"
        ).limit(1)
    ).first() is not None


def _require_plus(db: Session, user: User) -> None:
    if not has_plus(db, user):
        raise HTTPException(403, detail=REFUS_PLUS)


def _blocks(doc: LibDocument) -> list[dict]:
    return (doc.content or {}).get("blocks") or []


def _active(doc: LibDocument) -> list[LibCheckpoint]:
    return sorted((c for c in doc.checkpoints if c.is_active), key=lambda c: (c.anchor_block, c.id))


def _progress_map(db: Session, user: User | None, doc_ids: list[int]) -> dict[int, LibProgress]:
    if not user or not doc_ids:
        return {}
    rows = db.execute(
        select(LibProgress).where(LibProgress.user_id == user.id, LibProgress.document_id.in_(doc_ids))
    ).scalars().all()
    return {p.checkpoint_id: p for p in rows}


def _total_points(c: LibCheckpoint) -> int:
    return sum(int(q.get("points", 1)) for q in (c.questions or []))


def _state(doc: LibDocument, prog: dict[int, LibProgress], elo: int) -> dict:
    """
    Où en est l'élève : premier point de passage non validé (le verrou), état
    de chaque point de passage, index du dernier bloc lisible.
    """
    blocks = _blocks(doc)
    cps = _active(doc)
    gate = None
    items = []
    for c in cps:
        p = prog.get(c.id)
        passed = bool(p and p.passed)
        final = not parser.locks_content(blocks, c.anchor_block)
        if passed:
            status = "passed"
        elif gate is None:
            status = "elo" if elo < c.elo_requis else "open"
            gate = c
        else:
            status = "locked"
        items.append({
            "id": c.id,
            "anchor": c.anchor_block,
            "anchor_id": f"b{c.anchor_block}",
            "titre": c.titre,
            "elo_requis": c.elo_requis,
            "seuil_pct": c.seuil_pct,
            "recompense_elo": c.recompense_elo,
            "final": final,
            "status": status,
            "passed": passed,
            "skipped": bool(p and p.skipped),
            "best_score_pct": p.best_score_pct if p else 0,
            "attempts": p.attempts if p else 0,
            "essais_max": ESSAIS_MAX,
            "peut_passer": not passed and bool(p) and p.attempts >= ESSAIS_MAX,
        })
    gate_final = gate is not None and not parser.locks_content(blocks, gate.anchor_block)
    last = len(blocks) - 1 if gate is None or gate_final else gate.anchor_block
    return {"gate": gate, "items": items, "last_visible": last}


def _user_card(user: User | None, plus: bool = False) -> dict | None:
    if not user:
        return None
    elo = int(user.elo or 0)
    return {"id": user.id, "username": user.username, "elo": elo, "acces_plus": plus, **tier_from_elo(elo)}


def _summary(doc: LibDocument, prog: dict[int, LibProgress] | None = None, elo: int = 0) -> dict:
    cps = _active(doc)
    passed = sum(1 for c in cps if prog and prog.get(c.id) and prog[c.id].passed)
    stats = (doc.content or {}).get("stats") or {}
    return {
        "id": doc.id,
        "kind": doc.kind,
        "niveau": doc.niveau,
        "matiere": doc.matiere,
        "titre": doc.titre,
        "sous_titre": doc.sous_titre,
        "kicker": doc.kicker,
        "maj": doc.maj,
        "description": doc.description,
        "plus": doc.is_plus,
        "elo_requis": doc.elo_requis,
        "verrouille": elo < doc.elo_requis,
        "nb_etapes": sum(1 for b in _blocks(doc) if b.get("t") == "h1" and not parser.is_free_title(b.get("text", ""))),
        "nb_points_passage": len(cps),
        "valides": passed,
        "progression_pct": round(100 * passed / len(cps)) if cps else 0,
        "minutes": stats.get("minutes"),
        "mots": stats.get("words"),
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
    }


def _get_published(db: Session, doc_id: int) -> LibDocument:
    doc = db.get(LibDocument, doc_id)
    if not doc or not doc.is_published:
        raise HTTPException(404, detail="Document introuvable.")
    return doc


def _checkpoint_for_user(db: Session, cp_id: int, user: User) -> tuple[LibCheckpoint, dict, dict]:
    """Point de passage accessible à l'élève, ou 403 avec la raison."""
    _require_plus(db, user)
    cp = db.get(LibCheckpoint, cp_id)
    if not cp or not cp.is_active:
        raise HTTPException(404, detail="Point de passage introuvable.")
    doc = _get_published(db, cp.document_id)
    elo = int(user.elo or 0)
    if elo < doc.elo_requis:
        raise HTTPException(403, detail=f"Ce document demande {doc.elo_requis} Elo.")
    prog = _progress_map(db, user, [doc.id])
    st = _state(doc, prog, elo)
    item = next(i for i in st["items"] if i["id"] == cp.id)
    if item["status"] == "locked":
        raise HTTPException(403, detail="Valide d'abord les points de passage précédents.")
    if item["status"] == "elo":
        raise HTTPException(403, detail=f"Il te faut {cp.elo_requis} Elo pour ce point de passage (tu en as {elo}).")
    return cp, item, {"doc": doc, "prog": prog}


# ============================================================
# Étudiant
# ============================================================

@router.get("/catalogue")
def catalogue(db: Session = Depends(get_db), user: User | None = Depends(get_current_user_optional)):
    docs = db.execute(
        select(LibDocument).where(LibDocument.is_published.is_(True)).order_by(LibDocument.ordre, LibDocument.id)
    ).scalars().all()
    prog = _progress_map(db, user, [d.id for d in docs])
    elo = int(user.elo or 0) if user else 0
    return {"items": [_summary(d, prog, elo) for d in docs], "user": _user_card(user, has_plus(db, user))}


@router.get("/documents/{doc_id}")
def document(
    doc_id: int,
    mode: Literal["lib", "plus"] = "plus",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    doc = _get_published(db, doc_id)
    plus = has_plus(db, user)
    elo = int(user.elo or 0)
    blocks = _blocks(doc)
    plan_all = (doc.content or {}).get("plan") or parser.build_plan(blocks)

    if mode == "lib":
        # Consultation : le document entier, sans point de passage ni Elo.
        if doc.is_plus:
            raise HTTPException(403, detail="Ce document est réservé à NAVIRE Lib+.")
        return {
            "mode": "lib",
            "document": _summary(doc),
            "plan": [{**h, "locked": False} for h in plan_all],
            "blocks": blocks,
            "teaser": [],
            "checkpoints": [],
            "gate": None,
            "user": _user_card(user, plus),
        }

    _require_plus(db, user)
    prog = _progress_map(db, user, [doc.id])
    summary = _summary(doc, prog, elo)
    if elo < doc.elo_requis:
        return {
            "mode": "plus",
            "document": summary,
            "plan": [{**h, "locked": True} for h in plan_all],
            "blocks": [],
            "teaser": blocks[:TEASER_BLOCKS],
            "checkpoints": [],
            "gate": {"type": "document", "elo_requis": doc.elo_requis, "elo": elo},
            "user": _user_card(user, plus),
        }

    st = _state(doc, prog, elo)
    last = st["last_visible"]
    gate = st["gate"]
    gate_item = next((i for i in st["items"] if gate and i["id"] == gate.id), None)
    locks = gate_item is not None and not gate_item["final"]
    return {
        "mode": "plus",
        "document": summary,
        "plan": [{**h, "locked": int(h["id"][1:]) > last} for h in plan_all],
        "blocks": blocks[: last + 1],
        "teaser": blocks[last + 1: last + 1 + TEASER_BLOCKS] if locks else [],
        "checkpoints": st["items"],
        "gate": ({"type": "checkpoint", **gate_item, "elo": elo} if gate_item else None),
        "user": _user_card(user, plus),
    }


def _current_question(cp: LibCheckpoint, item: dict) -> dict:
    """
    La question de l'essai en cours : chaque essai tire la suivante de la
    réserve du point de passage (un nouvel essai n'est pas la même question).
    """
    pool = cp.questions or []
    if not pool:
        raise HTTPException(409, detail="Ce point de passage n'a pas encore de question.")
    return pool[int(item["attempts"]) % len(pool)]


@router.get("/checkpoints/{cp_id}/question")
def question(cp_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    cp, item, _ = _checkpoint_for_user(db, cp_id, user)
    return {"checkpoint": item, "question": parser.public_question(_current_question(cp, item))}


class SubmitIn(BaseModel):
    question_id: str = Field(..., min_length=1, max_length=20)
    answer: int | str | None = None


@router.post("/checkpoints/{cp_id}/submit")
def submit(cp_id: int, payload: SubmitIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    cp, item, ctx = _checkpoint_for_user(db, cp_id, user)
    q = _current_question(cp, item)
    if q["id"] != payload.question_id:
        raise HTTPException(409, detail="La question a changé : recharge le point de passage.")
    result = parser.grade([q], {q["id"]: payload.answer})
    reussi = result["corrections"][0]["correct"]

    now = datetime.now(timezone.utc)
    p = ctx["prog"].get(cp.id)
    if p is None:
        p = LibProgress(user_id=user.id, document_id=cp.document_id, checkpoint_id=cp.id)
        db.add(p)
    deja = bool(p.passed)
    p.attempts = int(p.attempts or 0) + 1
    p.last_score_pct = result["score_pct"]
    p.best_score_pct = max(int(p.best_score_pct or 0), result["score_pct"])
    p.last_attempt_at = now
    gain = 0
    if reussi and not deja:
        p.passed = True
        p.passed_at = now
        gain = int(cp.recompense_elo or 0)
        p.elo_gagne = gain
    db.commit()

    elo = int(user.elo or 0)
    if gain:
        # Idempotent : un seul EloEvent par (élève, point de passage).
        elo = apply_elo_delta(
            db, user_id=user.id, delta=gain, source="lib",
            session_id=f"lib-cp-{cp.id}", question_index=0,
            meta={"document_id": cp.document_id, "score_pct": result["score_pct"]},
        )
    passed = reussi or deja
    peut_passer = not passed and p.attempts >= ESSAIS_MAX
    # Tant que l'élève peut encore retenter, la réponse attendue reste cachée.
    correction = result["corrections"][0] if passed or peut_passer else \
        {"id": q["id"], "correct": False, "points": 0, "max": result["total"], "explication": ""}
    return {
        "correction": correction,
        "passed": passed,
        "reussi": reussi,
        "deja_valide": deja,
        "elo_gagne": gain,
        "elo": elo,
        "unlocked": reussi and not deja and not item["final"],
        "essais": p.attempts,
        "essais_max": ESSAIS_MAX,
        "essais_restants": 0 if passed else max(0, ESSAIS_MAX - p.attempts),
        "peut_passer": peut_passer,
    }


@router.post("/checkpoints/{cp_id}/passer")
def passer(cp_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Après ESSAIS_MAX échecs, la suite s'ouvre quand même, sans Elo."""
    cp, item, ctx = _checkpoint_for_user(db, cp_id, user)
    p = ctx["prog"].get(cp.id)
    if p and p.passed:
        return {"passed": True, "unlocked": False}
    if not item["peut_passer"]:
        raise HTTPException(403, detail=f"Encore {ESSAIS_MAX - item['attempts']} essai(s) avant de pouvoir passer.")
    p.passed = True
    p.skipped = True
    p.passed_at = datetime.now(timezone.utc)
    db.commit()
    return {"passed": True, "unlocked": not item["final"]}


# ============================================================
# Chat NAVIRE AI sur le cours (Lib+)
# ============================================================

class ChatIn(BaseModel):
    document_id: int
    message: str = Field(..., min_length=1, max_length=2000)
    checkpoint_id: int | None = None
    conversation_id: int | None = None


def _extrait(doc: LibDocument, prog: dict, elo: int, cp_id: int | None) -> tuple[str, str]:
    """Section lue (jusqu'au point de passage, ou jusqu'au verrou) : (titre, texte)."""
    blocks = _blocks(doc)
    st = _state(doc, prog, elo)
    fin = st["last_visible"]
    cps = _active(doc)
    if cp_id is not None:
        cp = next((c for c in cps if c.id == cp_id), None)
        if cp is not None:
            fin = min(fin, cp.anchor_block)
    debut = max([c.anchor_block for c in cps if c.anchor_block < fin], default=-1) + 1
    texte = "\n".join(b.get("text", "") for b in blocks[debut: fin + 1])
    return parser._section_title(blocks, fin) if blocks else "", texte[-12000:]


@router.post("/chat")
def chat(payload: ChatIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _require_plus(db, user)
    doc = _get_published(db, payload.document_id)
    elo = int(user.elo or 0)
    if elo < doc.elo_requis:
        raise HTTPException(403, detail=f"Ce document demande {doc.elo_requis} Elo.")
    check_navire_daily_limit(user, db)
    section, texte = _extrait(doc, _progress_map(db, user, [doc.id]), elo, payload.checkpoint_id)
    context = (
        f"CONTEXTE NAVIRE LIB+ : l'étudiant lit « {doc.titre} »"
        + (f", partie « {section} »" if section else "")
        + ". Il vient de répondre au point de passage et pose une question sur ce passage. "
        "Réponds d'abord à partir de l'extrait ci-dessous, de façon brève et pédagogique, "
        "sans tiret cadratin (—).\n\n"
        f"EXTRAIT DU COURS :\n{texte}"
    )
    from app.services.navire_chat import send_message  # import local : client OpenAI

    try:
        r = send_message(
            db=db, user=user, conversation_id=payload.conversation_id,
            user_message=payload.message, mode="chat", context=context,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("lib: chat NAVIRE AI impossible")
        raise HTTPException(502, detail=f"NAVIRE AI ne répond pas : {e}")
    return {"conversation_id": r["conversation_id"], "reply": r["reply"], "sources": r.get("sources", [])}


_EMBED = Path(__file__).resolve().parents[2] / "embeds" / "lib.html"


@router.get("/embed", response_class=HTMLResponse, include_in_schema=False)
def embed():
    contenu = _EMBED.read_text(encoding="utf-8")
    contenu = contenu.replace('API: "https://navire-app-api.onrender.com"', 'API: ""')
    return HTMLResponse(
        '<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>NAVIRE Lib</title></head>'
        f'<body style="margin:0;background:transparent">{contenu}</body></html>'
    )


# ============================================================
# Admin
# ============================================================

def _admin_cp(c: LibCheckpoint, blocks: list[dict], stats: dict | None = None) -> dict:
    anchor = blocks[c.anchor_block] if 0 <= c.anchor_block < len(blocks) else None
    return {
        "id": c.id,
        "anchor_block": c.anchor_block,
        "anchor_text": (anchor or {}).get("text", "")[:140],
        "section": parser._section_title(blocks, c.anchor_block) if blocks else "",
        "titre": c.titre,
        "elo_requis": c.elo_requis,
        "seuil_pct": c.seuil_pct,
        "recompense_elo": c.recompense_elo,
        "questions": c.questions or [],
        "source": c.source,
        "is_active": c.is_active,
        "final": not parser.locks_content(blocks, c.anchor_block),
        "total_points": _total_points(c),
        **(stats or {"tentatives": 0, "valides": 0}),
    }


def _admin_doc(db: Session, doc: LibDocument, *, full: bool = False) -> dict:
    blocks = _blocks(doc)
    rows = db.execute(
        select(LibProgress.checkpoint_id, func.count(), func.sum(case((LibProgress.passed.is_(True), 1), else_=0)))
        .where(LibProgress.document_id == doc.id)
        .group_by(LibProgress.checkpoint_id)
    ).all()
    per_cp = {r[0]: {"tentatives": int(r[1] or 0), "valides": int(r[2] or 0)} for r in rows}
    lecteurs = db.execute(
        select(func.count(func.distinct(LibProgress.user_id))).where(LibProgress.document_id == doc.id)
    ).scalar_one()
    out = {
        **_summary(doc),
        "is_published": doc.is_published,
        "ordre": doc.ordre,
        "source_filename": doc.source_filename,
        "size_bytes": doc.size_bytes,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "lecteurs": int(lecteurs or 0),
        "nb_blocs": len(blocks),
        "is_plus": doc.is_plus,
        "checkpoints": [_admin_cp(c, blocks, per_cp.get(c.id)) for c in sorted(doc.checkpoints, key=lambda c: c.anchor_block)],
    }
    if full:
        out["blocks"] = blocks
        out["plan"] = (doc.content or {}).get("plan") or []
    return out


def _admin_get(db: Session, doc_id: int) -> LibDocument:
    doc = db.get(LibDocument, doc_id)
    if not doc:
        raise HTTPException(404, detail="Document introuvable.")
    return doc


async def _read_upload(file: UploadFile) -> bytes:
    name = (file.filename or "").lower()
    if not name.endswith((".docx", ".pdf")):
        raise HTTPException(400, detail="Dépose un fichier .docx ou .pdf.")
    data = await file.read(LIB_MAX_BYTES + 1)
    if len(data) > LIB_MAX_BYTES:
        raise HTTPException(413, detail="Fichier trop lourd (50 Mo max).")
    if not data:
        raise HTTPException(400, detail="Fichier vide.")
    return data


def _store(data: bytes, filename: str) -> str:
    LIB_DIR.mkdir(parents=True, exist_ok=True)
    ext = ".docx" if filename.lower().endswith(".docx") else ".pdf"
    path = LIB_DIR / f"{uuid4().hex}{ext}"
    path.write_bytes(data)
    return str(path)


def _parse(data: bytes, filename: str) -> dict:
    try:
        return parser.parse_document(data, filename)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:  # fichier corrompu, PDF chiffré…
        logger.exception("lib: import impossible")
        raise HTTPException(400, detail=f"Lecture du fichier impossible : {e}")


def _apply_detection(db: Session, doc: LibDocument, *, palier: int, recompense: int, keep_existing: bool) -> int:
    """
    Place les points de passage détectés. Avec keep_existing, les points
    existants sont réutilisés dans l'ordre (la progression des élèves est
    conservée) : seules leur position et, s'ils viennent de la détection, leurs
    questions sont mises à jour. Renvoie le nombre de points de passage.
    """
    found = parser.detect_checkpoints(_blocks(doc), seed=doc.id or 0)
    old = sorted(doc.checkpoints, key=lambda c: c.anchor_block) if keep_existing else []
    if not keep_existing:
        _delete_checkpoints(db, list(doc.checkpoints))
    for k, f in enumerate(found):
        if k < len(old):
            c = old[k]
            c.anchor_block = f["anchor"]
            if c.source not in ("ia", "manuel"):
                c.questions = f["questions"]
                c.titre = f["titre"]
                c.source = f["source"]
        else:
            doc.checkpoints.append(LibCheckpoint(
                anchor_block=f["anchor"],
                titre=f["titre"],
                questions=f["questions"],
                source=f["source"],
                elo_requis=k * palier,
                recompense_elo=recompense,
                seuil_pct=SEUIL_PCT,
            ))
    if keep_existing:
        _delete_checkpoints(db, old[len(found):])
    return len(found)


def _delete_checkpoints(db: Session, cps: list[LibCheckpoint]) -> None:
    ids = [c.id for c in cps if c.id]
    if ids:
        # SQLite n'applique pas ON DELETE CASCADE : on nettoie à la main.
        db.query(LibProgress).filter(LibProgress.checkpoint_id.in_(ids)).delete(synchronize_session=False)
    for c in cps:
        if c.document is not None and c in c.document.checkpoints:
            c.document.checkpoints.remove(c)
        elif c.id:
            db.delete(c)


@router.get("/admin/documents", dependencies=[Depends(verify_admin_code)])
def admin_list(db: Session = Depends(get_db)):
    docs = db.execute(select(LibDocument).order_by(LibDocument.ordre, LibDocument.id)).scalars().all()
    return {"items": [_admin_doc(db, d) for d in docs]}


@router.post("/admin/documents", dependencies=[Depends(verify_admin_code)])
async def admin_import(
    file: UploadFile = FastAPIFile(...),
    kind: str = Form("cours"),
    niveau: str = Form(""),
    matiere: str = Form(""),
    titre: str = Form(""),
    description: str = Form(""),
    auto: bool = Form(True),
    elo_palier: int = Form(ELO_PALIER),
    recompense_elo: int = Form(RECOMPENSE),
    publier: bool = Form(False),
    plus: bool = Form(False),
    db: Session = Depends(get_db),
):
    if kind not in KINDS:
        raise HTTPException(400, detail=f"Type inconnu : {kind}.")
    data = await _read_upload(file)
    parsed = _parse(data, file.filename or "")
    meta = parsed["meta"]
    count = db.execute(select(func.count()).select_from(LibDocument)).scalar_one()
    doc = LibDocument(
        kind=kind,
        niveau=niveau.strip()[:10],
        matiere=matiere.strip()[:120],
        titre=(titre.strip() or _titre_case(meta.get("titre")) or Path(file.filename or "Document").stem)[:200],
        sous_titre=(meta.get("sous_titre") or "")[:400],
        kicker=(meta.get("kicker") or "")[:120],
        maj=(meta.get("maj") or "")[:120],
        description=description.strip(),
        content={k: parsed[k] for k in ("blocks", "plan", "stats")},
        source_filename=(file.filename or "")[:255],
        source_path=_store(data, file.filename or ""),
        size_bytes=len(data),
        ordre=int(count or 0) + 1,
        is_published=publier,
        is_plus=plus,
    )
    db.add(doc)
    db.flush()
    n = _apply_detection(db, doc, palier=elo_palier, recompense=recompense_elo, keep_existing=False) if auto else 0
    db.commit()
    db.refresh(doc)
    return {**_admin_doc(db, doc), "detection": {"points_passage": n, **parsed["stats"]}}


def _titre_case(t: str | None) -> str:
    t = (t or "").strip()
    return t[:1] + t[1:].lower() if t.isupper() else t


@router.get("/admin/documents/{doc_id}", dependencies=[Depends(verify_admin_code)])
def admin_get(doc_id: int, db: Session = Depends(get_db)):
    return _admin_doc(db, _admin_get(db, doc_id), full=True)


class DocPatch(BaseModel):
    titre: str | None = Field(None, max_length=200)
    sous_titre: str | None = Field(None, max_length=400)
    kicker: str | None = Field(None, max_length=120)
    maj: str | None = Field(None, max_length=120)
    description: str | None = None
    kind: Literal["cours", "dissertation", "fiche", "cas_pratique"] | None = None
    niveau: str | None = Field(None, max_length=10)
    matiere: str | None = Field(None, max_length=120)
    elo_requis: int | None = Field(None, ge=0, le=100000)
    ordre: int | None = None
    is_published: bool | None = None
    is_plus: bool | None = None


@router.patch("/admin/documents/{doc_id}", dependencies=[Depends(verify_admin_code)])
def admin_patch(doc_id: int, payload: DocPatch, db: Session = Depends(get_db)):
    doc = _admin_get(db, doc_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(doc, k, v.strip() if isinstance(v, str) else v)
    db.commit()
    db.refresh(doc)
    return _admin_doc(db, doc)


@router.delete("/admin/documents/{doc_id}", dependencies=[Depends(verify_admin_code)])
def admin_delete(doc_id: int, db: Session = Depends(get_db)):
    doc = _admin_get(db, doc_id)
    path = doc.source_path
    db.query(LibProgress).filter(LibProgress.document_id == doc.id).delete(synchronize_session=False)
    db.delete(doc)
    db.commit()
    try:
        if path:
            Path(path).unlink(missing_ok=True)
    except OSError:
        pass
    return {"ok": True}


@router.post("/admin/documents/{doc_id}/fichier", dependencies=[Depends(verify_admin_code)])
async def admin_reimport(doc_id: int, file: UploadFile = FastAPIFile(...), db: Session = Depends(get_db)):
    """Nouvelle version du cours : blocs remplacés, progression conservée."""
    doc = _admin_get(db, doc_id)
    data = await _read_upload(file)
    parsed = _parse(data, file.filename or "")
    old_path = doc.source_path
    doc.content = {k: parsed[k] for k in ("blocks", "plan", "stats")}
    doc.maj = (parsed["meta"].get("maj") or doc.maj)[:120]
    doc.source_filename = (file.filename or "")[:255]
    doc.source_path = _store(data, file.filename or "")
    doc.size_bytes = len(data)
    n = _apply_detection(db, doc, palier=ELO_PALIER, recompense=RECOMPENSE, keep_existing=True)
    db.commit()
    db.refresh(doc)
    try:
        if old_path and old_path != doc.source_path:
            Path(old_path).unlink(missing_ok=True)
    except OSError:
        pass
    return {**_admin_doc(db, doc), "detection": {"points_passage": n, **parsed["stats"]}}


class DetectIn(BaseModel):
    elo_palier: int = Field(ELO_PALIER, ge=0, le=10000)
    recompense_elo: int = Field(RECOMPENSE, ge=0, le=1000)


@router.post("/admin/documents/{doc_id}/detecter", dependencies=[Depends(verify_admin_code)])
def admin_detect(doc_id: int, payload: DetectIn | None = None, db: Session = Depends(get_db)):
    """Remplace tous les points de passage (la progression des élèves est perdue)."""
    payload = payload or DetectIn()
    doc = _admin_get(db, doc_id)
    n = _apply_detection(db, doc, palier=payload.elo_palier, recompense=payload.recompense_elo, keep_existing=False)
    db.commit()
    db.refresh(doc)
    return {**_admin_doc(db, doc), "detection": {"points_passage": n}}


@router.get("/admin/documents/{doc_id}/source", dependencies=[Depends(verify_admin_code)])
def admin_source(doc_id: int, db: Session = Depends(get_db)):
    doc = _admin_get(db, doc_id)
    if not doc.source_path or not Path(doc.source_path).exists():
        raise HTTPException(404, detail="Fichier d'origine introuvable.")
    media = "application/pdf" if doc.source_path.endswith(".pdf") else \
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return FileResponse(doc.source_path, media_type=media, filename=doc.source_filename or Path(doc.source_path).name)


class QuestionIn(BaseModel):
    id: str = Field(..., min_length=1, max_length=20)
    type: Literal["qcm", "courte"]
    question: str = Field(..., min_length=3, max_length=2000)
    choices: list[str] | None = None
    answer: int | None = None
    accepted: list[str] | None = None
    points: int = Field(1, ge=1, le=20)
    explication: str = Field("", max_length=2000)


def _check_questions(qs: list[QuestionIn]) -> list[dict]:
    out, seen = [], set()
    for q in qs:
        if q.id in seen:
            raise HTTPException(400, detail=f"Identifiant de question en double : {q.id}.")
        seen.add(q.id)
        d = q.model_dump()
        if q.type == "qcm":
            ch = [c.strip() for c in (q.choices or []) if c and c.strip()]
            if len(ch) < 2 or q.answer is None or not (0 <= q.answer < len(ch)):
                raise HTTPException(400, detail=f"{q.id} : un QCM demande 2 choix ou plus et une bonne réponse.")
            d["choices"] = ch
            d.pop("accepted", None)
        else:
            acc = [a.strip() for a in (q.accepted or []) if a and a.strip()]
            if not acc:
                raise HTTPException(400, detail=f"{q.id} : indique au moins une réponse acceptée.")
            d["accepted"] = acc
            d.pop("choices", None)
            d.pop("answer", None)
        out.append(d)
    return out


class CheckpointIn(BaseModel):
    anchor_block: int = Field(..., ge=0)
    titre: str = Field("Point de passage", max_length=200)
    elo_requis: int = Field(0, ge=0, le=100000)
    seuil_pct: int = Field(SEUIL_PCT, ge=0, le=100)
    recompense_elo: int = Field(RECOMPENSE, ge=0, le=1000)
    questions: list[QuestionIn] = Field(default_factory=list)


class CheckpointPatch(BaseModel):
    anchor_block: int | None = Field(None, ge=0)
    titre: str | None = Field(None, max_length=200)
    elo_requis: int | None = Field(None, ge=0, le=100000)
    seuil_pct: int | None = Field(None, ge=0, le=100)
    recompense_elo: int | None = Field(None, ge=0, le=1000)
    questions: list[QuestionIn] | None = None
    is_active: bool | None = None


def _check_anchor(doc: LibDocument, anchor: int) -> None:
    if not 0 <= anchor < len(_blocks(doc)):
        raise HTTPException(400, detail="Position hors du document.")


@router.post("/admin/documents/{doc_id}/checkpoints", dependencies=[Depends(verify_admin_code)])
def admin_add_checkpoint(doc_id: int, payload: CheckpointIn, db: Session = Depends(get_db)):
    doc = _admin_get(db, doc_id)
    _check_anchor(doc, payload.anchor_block)
    questions = _check_questions(payload.questions)
    if not questions:
        # Sans questions fournies : celles des paragraphes qui précèdent.
        blocks = _blocks(doc)
        prev = max([c.anchor_block for c in doc.checkpoints if c.anchor_block < payload.anchor_block], default=-1)
        questions = parser.questions_from_paragraphs(blocks[prev + 1: payload.anchor_block + 1], seed=payload.anchor_block)
    c = LibCheckpoint(
        anchor_block=payload.anchor_block, titre=payload.titre.strip() or "Point de passage",
        elo_requis=payload.elo_requis, seuil_pct=payload.seuil_pct, recompense_elo=payload.recompense_elo,
        questions=questions, source="manuel",
    )
    doc.checkpoints.append(c)
    db.commit()
    db.refresh(c)
    return _admin_cp(c, _blocks(doc))


@router.patch("/admin/checkpoints/{cp_id}", dependencies=[Depends(verify_admin_code)])
def admin_patch_checkpoint(cp_id: int, payload: CheckpointPatch, db: Session = Depends(get_db)):
    c = db.get(LibCheckpoint, cp_id)
    if not c:
        raise HTTPException(404, detail="Point de passage introuvable.")
    data = payload.model_dump(exclude_unset=True)
    if data.get("anchor_block") is not None:
        _check_anchor(c.document, data["anchor_block"])
    if payload.questions is not None:
        c.questions = _check_questions(payload.questions)
        c.source = "manuel"
        data.pop("questions")
    for k, v in data.items():
        if v is not None:
            setattr(c, k, v.strip() if isinstance(v, str) else v)
    db.commit()
    db.refresh(c)
    return _admin_cp(c, _blocks(c.document))


@router.delete("/admin/checkpoints/{cp_id}", dependencies=[Depends(verify_admin_code)])
def admin_delete_checkpoint(cp_id: int, db: Session = Depends(get_db)):
    c = db.get(LibCheckpoint, cp_id)
    if not c:
        raise HTTPException(404, detail="Point de passage introuvable.")
    _delete_checkpoints(db, [c])
    db.commit()
    return {"ok": True}


# ============================================================
# Questions générées par l'IA
# ============================================================

_IA_PROMPT = """Tu es enseignant en droit. À partir de l'extrait de cours ci-dessous, rédige {n} questions
pour vérifier qu'un étudiant l'a compris avant de passer à la suite.
- {n_qcm} QCM à 4 choix, une seule bonne réponse, leurres plausibles tirés du cours ;
- {n_courte} questions à réponse courte (un mot ou une référence : notion, article, arrêt),
  avec les variantes de réponse acceptables.
Chaque question porte sur le contenu de l'extrait, jamais sur des connaissances extérieures.
Chaque question doit se suffire à elle-même : l'étudiant n'en verra qu'une à la fois.
N'utilise jamais de tiret cadratin (—) : préfère la virgule, les deux-points ou les parenthèses.

Réponds UNIQUEMENT en JSON :
{{"questions": [
  {{"type": "qcm", "question": "...", "choices": ["...", "...", "...", "..."], "answer": 0, "explication": "..."}},
  {{"type": "courte", "question": "...", "accepted": ["...", "..."], "explication": "..."}}
]}}

EXTRAIT :
{texte}
"""


def generer_questions_ia(texte: str, n_qcm: int = 5, n_courte: int = 2) -> list[dict]:
    from openai import OpenAI  # import local : l'API démarre sans clé OpenAI

    client = OpenAI(timeout=float(os.getenv("OPENAI_TIMEOUT_SEC", "40")))
    rep = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL_LIB", os.getenv("OPENAI_MODEL", "gpt-4o-mini")),
        messages=[
            {"role": "system", "content": "Tu réponds en JSON valide, en français."},
            {"role": "user", "content": _IA_PROMPT.format(
                n=n_qcm + n_courte, n_qcm=n_qcm, n_courte=n_courte, texte=texte[:14000])},
        ],
        response_format={"type": "json_object"},
        temperature=0.4,
    )
    data = json.loads(rep.choices[0].message.content or "{}")
    out = []
    for q in data.get("questions") or []:
        t = q.get("type")
        if t == "qcm" and len(q.get("choices") or []) >= 2 and isinstance(q.get("answer"), int) \
                and 0 <= q["answer"] < len(q["choices"]):
            out.append({"type": "qcm", "question": str(q["question"]), "choices": [str(c) for c in q["choices"]],
                        "answer": q["answer"], "points": 1, "explication": str(q.get("explication", ""))})
        elif t == "courte" and q.get("accepted"):
            out.append({"type": "courte", "question": str(q["question"]),
                        "accepted": [str(a) for a in q["accepted"]], "points": 3,
                        "explication": str(q.get("explication", ""))})
    for k, q in enumerate(out):
        q["id"] = f"q{k + 1}"
    return out


@router.post("/admin/checkpoints/{cp_id}/generer-ia", dependencies=[Depends(verify_admin_code)])
def admin_generate(cp_id: int, db: Session = Depends(get_db)):
    c = db.get(LibCheckpoint, cp_id)
    if not c:
        raise HTTPException(404, detail="Point de passage introuvable.")
    blocks = _blocks(c.document)
    prev = max([x.anchor_block for x in c.document.checkpoints if x.anchor_block < c.anchor_block], default=-1)
    texte = "\n".join(b.get("text", "") for b in blocks[prev + 1: c.anchor_block + 1])
    if len(texte) < 200:
        raise HTTPException(400, detail="Extrait trop court pour générer des questions.")
    try:
        qs = generer_questions_ia(texte)
    except Exception as e:
        logger.exception("lib: génération IA impossible")
        raise HTTPException(502, detail=f"Génération impossible : {e}")
    if not qs:
        raise HTTPException(502, detail="L'IA n'a renvoyé aucune question exploitable, réessaie.")
    c.questions = qs
    c.source = "ia"
    db.commit()
    db.refresh(c)
    return _admin_cp(c, blocks)
