"""
Liens entre NAVIRE Lib et Actus (veille juridique).

Chaque contenu garde sa maison : le cours dans la Lib, l'actu dans Actus. On
ajoute seulement des passerelles, pertinentes ou absentes :
  - un cours Lib propose jusqu'à 3 actus récentes de sa matière
    (GET /lib/documents/{id}/actus) ;
  - une actu propose jusqu'à 2 cours à réviser (champ `cours` de
    GET /veille/actus/{id}).

Deux filtres :
  1. matière → rubrique de la veille (civil, penal, public, affaires) ;
  2. mots-clés communs entre l'actu (titre, essentiel, impact) et le cours
     (titre, sous-titre, description, plan), avec un seuil : sous le seuil,
     rien n'est proposé.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import LibDocument, VeilleItem

ACTUS_MAX = 3
COURS_MAX = 2
FENETRE_JOURS = 120
SEUIL = 2                 # mots-clés communs minimum, rubrique reconnue
SEUIL_SANS_RUBRIQUE = 3   # matière non reconnue : on exige plus

KINDS_LABEL = {
    "cours": "Cours", "fiche": "Fiche", "fiche_arret": "Fiche d'arrêt", "commentaire": "Commentaire",
    "dissertation": "Dissertation", "cas_pratique": "Cas pratique",
}

# Matière (texte libre de la Lib) → rubrique de la veille, par mots repères.
_REPERES = {
    "public": ("constitution", "administrati", "public", "libert", "fiscal", "finances publiques",
               "europeen", "union europeenne", "international public", "ijae", "institution"),
    "penal": ("penal", "criminel", "criminolog", "procedure penale"),
    "affaires": ("societe", "affaires", "commercial", "distribution", "concurrence", "entreprise",
                 "bancaire", "financier", "travail", "social", "consommation", "propriete intellectuelle"),
    "civil": ("obligation", "contrat", "surete", "famille", "biens", "introduction au droit", "civil",
              "responsabilit", "succession", "personne", "regime", "procedure civile", "preuve"),
}

# Mots trop généraux pour relier une actu à un cours (en plus des mots vides de la veille).
_GENERIQUES = {
    "droits", "juridique", "juridiques", "jurisprudence", "principe", "principes", "regime", "régime",
    "notion", "notions", "partie", "titre", "chapitre", "section", "introduction", "conclusion",
    "general", "général", "generale", "générale", "conditions", "condition", "effets", "effet",
    "france", "francais", "français", "francaise", "française", "publique", "public", "civile",
    "fiche", "cours", "étape", "etape", "questions", "question", "points", "passage", "matière",
}


def _plain(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(c) != "Mn")


def rubriques_de(matiere: str) -> set[str]:
    """Rubriques de veille d'une matière Lib (vide si la matière n'est pas reconnue)."""
    m = _plain(matiere)
    return {rub for rub, mots in _REPERES.items() if any(w in m for w in mots)}


def _mots(texte: str) -> set[str]:
    from app.routers.veille import _keywords   # mêmes règles que « À lire aussi »

    return {w for w in _keywords(texte or "") if w not in _GENERIQUES and _plain(w) not in _GENERIQUES}


def mots_actu(it: VeilleItem) -> set[str]:
    return _mots(f"{it.title} {it.essentiel} {it.impact}")


_CACHE_COURS: dict[tuple[int, str], set[str]] = {}


def mots_cours(doc: LibDocument) -> set[str]:
    """Mots-clés d'un cours : titres, description et plan (mis en cache par version)."""
    cle = (doc.id, doc.updated_at.isoformat() if doc.updated_at else "")
    if cle not in _CACHE_COURS:
        plan = (doc.content or {}).get("plan") or []
        if not plan:
            plan = [{"text": b.get("text", "")} for b in (doc.content or {}).get("blocks") or []
                    if b.get("t") in ("h1", "h2", "h3")]
        texte = " ".join([doc.titre, doc.sous_titre, doc.description, doc.matiere] + [h.get("text", "") for h in plan])
        _CACHE_COURS[cle] = _mots(texte)
    return _CACHE_COURS[cle]


def actus_du_cours(db: Session, doc: LibDocument) -> list[VeilleItem]:
    """Jusqu'à 3 actus récentes de la matière du cours, les plus proches d'abord."""
    if doc.owner_id is not None:          # PDF perso : pas de matière fiable
        return []
    rubs = rubriques_de(doc.matiere or doc.titre)
    seuil = SEUIL if rubs else SEUIL_SANS_RUBRIQUE
    mots = mots_cours(doc)
    if not mots:
        return []
    depuis = datetime.now(timezone.utc) - timedelta(days=FENETRE_JOURS)
    date = func.coalesce(VeilleItem.published_at, VeilleItem.created_at)
    q = select(VeilleItem).where(date >= depuis).order_by(date.desc(), VeilleItem.id.desc()).limit(400)
    if rubs:
        q = q.where(VeilleItem.category.in_(rubs | {"general"}))
    notes = []
    for it in db.execute(q).scalars().all():
        score = len(mots & mots_actu(it))
        if it.category not in rubs and rubs:
            score -= 1                    # « Général » : seulement s'il colle vraiment
        if score >= seuil:
            notes.append((score, it))
    notes.sort(key=lambda x: (-x[0], -(x[1].published_at or x[1].created_at).timestamp()))
    return [it for _, it in notes[:ACTUS_MAX]]


def cours_de_l_actu(db: Session, it: VeilleItem) -> list[dict]:
    """Jusqu'à 2 cours publiés à réviser après l'actu (fiche d'arrêt en tête si elle colle)."""
    from app.routers.lib import KINDS

    mots = mots_actu(it)
    if not mots:
        return []
    docs = db.execute(
        select(LibDocument).where(
            LibDocument.owner_id.is_(None),
            LibDocument.prepa_ressource_id.is_(None),
            LibDocument.is_published.is_(True),
            LibDocument.kind.in_(KINDS),
        )
    ).scalars().all()
    notes = []
    for d in docs:
        rubs = rubriques_de(d.matiere or d.titre)
        if rubs and it.category != "general" and it.category not in rubs:
            continue
        score = len(mots & mots_cours(d))
        seuil = SEUIL if rubs else SEUIL_SANS_RUBRIQUE
        if score < seuil:
            continue
        if d.kind == "fiche_arret":
            score += 1
        notes.append((score, d))
    notes.sort(key=lambda x: (-x[0], x[1].ordre, x[1].id))
    return [
        {"id": d.id, "titre": d.titre, "kind": d.kind, "kind_label": KINDS_LABEL.get(d.kind, d.kind),
         "matiere": d.matiere, "niveau": d.niveau, "plus": d.is_plus}
        for _, d in notes[:COURS_MAX]
    ]
