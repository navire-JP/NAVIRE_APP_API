"""
app/services/prof_profils.py
=============================
Fiches publiques des professeurs (page /professeurs#slug, embed d'accueil
« Nos professeurs »). Routes : app/routers/prof_profils.py.

Règles :
  - Une fiche par professeur (ProfProfil), créée vide à la première visite
    du professeur dans « Mon profil public ».
  - Le professeur édite un brouillon, puis le soumet ; un admin le publie
    (le brouillon devient la version publique) ou le refuse avec un motif.
    Une fiche déjà publiée reste en ligne, inchangée, pendant qu'un nouveau
    brouillon attend.
  - Visible du public : publiée au moins une fois, non masquée, professeur
    actif. Les matières viennent des affectations actives du professeur
    (groupes actifs) : aucune matière saisie sur la fiche.
  - Nom affiché : prénom + nom si le professeur l'accepte
    (nom_complet_public), sinon prénom + initiale. Le slug suit ce choix :
    « camille-martin » ou « camille-m », suffixé « -2 », « -3 » en cas
    d'homonyme. Il est fixé à la publication et ne change ensuite que si le
    choix du nom change.
  - Aucune donnée privée dans les réponses publiques : ni email, ni
    rémunération, ni lien Discord, ni identifiant de compte.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.cloudinary_client import resolve_avatar_url
from app.core.prepa_adjuris_config import (
    PREPA_CRENEAUX,
    PREPA_MATIERE_NAMES,
    PREPA_PRICES,
    PREPA_PRIX_SEANCE_CENTS,
    matiere_niveau,
)
from app.db.models import PrepaAdjurisProfesseur, PrepaAdjurisSeance, ProfProfil

STATUTS_PRO: dict[str, str] = {
    "juriste": "Juriste",
    "charge_td": "Chargé de TD",
    "avocat": "Avocat",
}
ETATS = ("brouillon", "en_attente", "publie", "refuse")

DESCRIPTION_MAX = 1200
DESCRIPTION_MIN_SOUMISSION = 80
FORMATION_MAX = 6
CHAMP_FORMATION_MAX = 160
PROCHAINES_SEANCES = 5

# Champs que le professeur édite (brouillon) et que la publication copie.
CHAMPS_CONTENU = (
    "nom_complet_public",
    "photo_url",
    "statut_pro",
    "poste",
    "experience_annees",
    "formation",
    "description",
    "linkedin_url",
)

_JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
_LINKEDIN = re.compile(r"^https://([a-z]{2,3}\.)?linkedin\.com/", re.I)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    dt = _aware(dt)
    return dt.isoformat() if dt else None


# ============================================================
# Nom et slug
# ============================================================

def _ascii(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c))


def slugifier(texte: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", _ascii(texte).lower()).strip("-")
    return s[:90]


def prenom_de(p: PrepaAdjurisProfesseur) -> str:
    return (p.prenom or "").strip() or ((p.user.username if p.user else "") or "").strip() or "Professeur"


def nom_affiche(p: PrepaAdjurisProfesseur, complet: bool) -> str:
    prenom, nom = prenom_de(p), (p.nom or "").strip()
    if not nom:
        return prenom
    return f"{prenom} {nom}" if complet else f"{prenom} {nom[:1].upper()}."


def base_slug(p: PrepaAdjurisProfesseur, complet: bool) -> str:
    prenom, nom = prenom_de(p), (p.nom or "").strip()
    morceaux = [prenom, nom if complet else nom[:1]] if nom else [prenom]
    return slugifier(" ".join(morceaux)) or f"professeur-{p.id}"


def slug_unique(db: Session, base: str, profil_id: int | None) -> str:
    pris = {
        s for (s,) in db.execute(
            select(ProfProfil.slug).where(ProfProfil.slug.is_not(None), ProfProfil.id != (profil_id or 0))
        )
    }
    if base not in pris:
        return base
    n = 2
    while f"{base}-{n}" in pris:
        n += 1
    return f"{base}-{n}"


# ============================================================
# Accès
# ============================================================

def profil_de(db: Session, prof: PrepaAdjurisProfesseur, creer: bool = True) -> ProfProfil | None:
    f = db.execute(select(ProfProfil).where(ProfProfil.professeur_id == prof.id)).scalar_one_or_none()
    if f is None and creer:
        f = ProfProfil(professeur_id=prof.id, formation=[])
        db.add(f)
        db.flush()
    return f


def est_public(f: ProfProfil) -> bool:
    return bool(f.publie_le and f.slug and not f.masque and f.professeur and f.professeur.status == "active")


def profils_publics(db: Session) -> list[ProfProfil]:
    rows = db.execute(
        select(ProfProfil)
        .where(ProfProfil.publie_le.is_not(None), ProfProfil.masque.is_(False))
        .order_by(ProfProfil.ordre, ProfProfil.publie_le, ProfProfil.id)
    ).scalars().all()
    return [f for f in rows if est_public(f)]


def profil_public(db: Session, slug: str) -> ProfProfil | None:
    f = db.execute(select(ProfProfil).where(ProfProfil.slug == (slug or "").strip().lower())).scalar_one_or_none()
    return f if f and est_public(f) else None


# ============================================================
# Contenu : version publiée, brouillon, validation
# ============================================================

def contenu_publie(f: ProfProfil) -> dict:
    return {
        "nom_complet_public": bool(f.nom_complet_public),
        "photo_url": f.photo_url,
        "statut_pro": f.statut_pro or "",
        "poste": f.poste or "",
        "experience_annees": f.experience_annees,
        "formation": list(f.formation or []),
        "description": f.description or "",
        "linkedin_url": f.linkedin_url,
    }


def contenu_brouillon(f: ProfProfil) -> dict:
    """Ce que le professeur édite : son brouillon, à défaut la version publiée."""
    base = contenu_publie(f)
    if f.brouillon:
        base.update({k: v for k, v in f.brouillon.items() if k in CHAMPS_CONTENU})
    return base


def nettoyer_formation(lignes: list) -> list[dict]:
    out = []
    for l in lignes or []:
        if not isinstance(l, dict):
            continue
        ligne = {
            k: str(l.get(k) or "").strip()[:CHAMP_FORMATION_MAX]
            for k in ("diplome", "etablissement", "annee")
        }
        if ligne["diplome"] or ligne["etablissement"]:
            out.append(ligne)
    if len(out) > FORMATION_MAX:
        raise ValueError(f"{FORMATION_MAX} lignes de formation au maximum.")
    return out


def valider_linkedin(url: str | None) -> str | None:
    url = (url or "").strip()
    if not url:
        return None
    if not url.lower().startswith("http"):
        url = "https://" + url
    url = re.sub(r"^http://", "https://", url, flags=re.I)
    if not _LINKEDIN.match(url) or len(url) > 300:
        raise ValueError("Le lien doit être celui de ton profil LinkedIn (https://www.linkedin.com/in/…).")
    return url


def manques_pour_soumettre(c: dict) -> list[str]:
    """Ce qu'il manque au brouillon pour être soumis à validation."""
    manques = []
    if not c.get("photo_url"):
        manques.append("une photo")
    if c.get("statut_pro") not in STATUTS_PRO:
        manques.append("ton statut")
    if not (c.get("poste") or "").strip():
        manques.append("ton poste actuel")
    if not c.get("experience_annees") or c["experience_annees"] < 1:
        manques.append("tes années d'expérience (1 an minimum)")
    if not c.get("formation"):
        manques.append("au moins une ligne de formation")
    if len((c.get("description") or "").strip()) < DESCRIPTION_MIN_SOUMISSION:
        manques.append(f"une description d'au moins {DESCRIPTION_MIN_SOUMISSION} caractères")
    return manques


def enregistrer_brouillon(f: ProfProfil, valeurs: dict) -> None:
    """Remplace le brouillon. Un brouillon en attente ou refusé redevient un
    brouillon : il faudra le soumettre à nouveau."""
    c = contenu_brouillon(f)
    c.update({k: v for k, v in valeurs.items() if k in CHAMPS_CONTENU})
    f.brouillon = c
    f.etat = "brouillon"
    f.motif_refus = ""
    f.soumis_le = None


def soumettre(f: ProfProfil) -> None:
    c = contenu_brouillon(f)
    manques = manques_pour_soumettre(c)
    if manques:
        raise ValueError("Il manque " + ", ".join(manques) + ".")
    f.brouillon = c
    f.etat = "en_attente"
    f.motif_refus = ""
    f.soumis_le = utcnow()


def publier(db: Session, f: ProfProfil) -> None:
    """Le brouillon devient la version publique. Slug fixé à la première
    publication, recalculé seulement si le choix du nom affiché change."""
    c = contenu_brouillon(f)
    manques = manques_pour_soumettre(c)
    if manques:
        raise ValueError("Fiche incomplète : il manque " + ", ".join(manques) + ".")
    nom_change = bool(c["nom_complet_public"]) != bool(f.nom_complet_public)
    for k in CHAMPS_CONTENU:
        setattr(f, k, c[k])
    if not f.slug or nom_change:
        f.slug = slug_unique(db, base_slug(f.professeur, f.nom_complet_public), f.id)
    f.brouillon = None
    f.etat = "publie"
    f.motif_refus = ""
    f.publie_le = f.publie_le or utcnow()


def refuser(f: ProfProfil, motif: str) -> None:
    f.etat = "refuse"
    f.motif_refus = motif.strip()[:300]


# ============================================================
# Matières, créneaux, séances
# ============================================================

def _ordre_matieres() -> dict[str, int]:
    return {k: i for i, k in enumerate(PREPA_PRICES)}


def groupes_du_prof(p: PrepaAdjurisProfesseur) -> dict[str, int]:
    """Matière → groupe du professeur (le premier groupe actif s'il en a
    plusieurs dans la même matière), dans l'ordre de la config."""
    out: dict[str, int] = {}
    for a in sorted(p.affectations, key=lambda x: x.classe_id):
        c = a.classe
        if c and c.is_active and c.matiere_key in PREPA_PRICES and c.matiere_key not in out:
            out[c.matiere_key] = c.id
    ordre = _ordre_matieres()
    return dict(sorted(out.items(), key=lambda kv: ordre.get(kv[0], 99)))


def creneau(niveau: str) -> str:
    c = PREPA_CRENEAUX.get(niveau)
    if not c:
        return ""
    jour, heure, _ = c
    h, _, m = heure.partition(":")
    return f"{_JOURS[jour].capitalize()}, {int(h)} h" + (f" {m}" if m and m != "00" else "")


def prochaines_seances(db: Session, matiere_key: str, n: int = PROCHAINES_SEANCES) -> list[dict]:
    """Séances à venir de la matière (agenda de la console), sans lien de visio."""
    rows = db.execute(
        select(PrepaAdjurisSeance)
        .where(
            PrepaAdjurisSeance.matiere_key == matiere_key,
            PrepaAdjurisSeance.statut != "annulee",
            PrepaAdjurisSeance.date_debut >= utcnow(),
        )
        .order_by(PrepaAdjurisSeance.date_debut)
        .limit(n)
    ).scalars().all()
    return [{"date": _iso(s.date_debut), "duree_minutes": s.duree_minutes, "titre": s.titre or ""} for s in rows]


def matiere_row(key: str) -> dict:
    return {"key": key, "niveau": matiere_niveau(key), "nom": PREPA_MATIERE_NAMES.get(key, key)}


# ============================================================
# Sérialisation
# ============================================================

def _photo(f: ProfProfil, c: dict | None = None) -> str:
    c = c or contenu_publie(f)
    return c.get("photo_url") or resolve_avatar_url(f.professeur.user.avatar_url if f.professeur and f.professeur.user else None)


def carte_publique(f: ProfProfil) -> dict:
    """Carte du catalogue et de l'embed d'accueil. Liste blanche de champs."""
    p = f.professeur
    desc = (f.description or "").strip()
    return {
        "slug": f.slug,
        "nom": nom_affiche(p, f.nom_complet_public),
        "photo_url": _photo(f),
        "statut": f.statut_pro or "",
        "statut_label": STATUTS_PRO.get(f.statut_pro or "", ""),
        "verifie": bool(f.verifie_le),
        "poste": f.poste or "",
        "experience_annees": f.experience_annees,
        "extrait": desc if len(desc) <= 180 else desc[:177].rsplit(" ", 1)[0] + "…",
        "matieres": [matiere_row(k) for k in groupes_du_prof(p)],
    }


def fiche_publique(db: Session, f: ProfProfil) -> dict:
    """Fiche complète : carte + formation, description, et par matière le
    créneau, les prochaines séances et les offres."""
    out = carte_publique(f)
    out.update({
        "formation": list(f.formation or []),
        "description": f.description or "",
        "linkedin_url": f.linkedin_url,
        "cours": [
            {
                **matiere_row(k),
                "creneau": creneau(matiere_niveau(k)),
                "prochaines_seances": prochaines_seances(db, k),
                "abonnement": {"prix_seance_cents": PREPA_PRIX_SEANCE_CENTS},
            }
            for k in groupes_du_prof(f.professeur)
        ],
    })
    return out


def vue_prof(f: ProfProfil) -> dict:
    """Ce que voit le professeur dans « Mon profil public »."""
    p = f.professeur
    c = contenu_brouillon(f)
    return {
        "etat": f.etat,
        "motif_refus": f.motif_refus or "",
        "soumis_le": _iso(f.soumis_le),
        "publie": bool(f.publie_le),
        "visible": est_public(f),
        "masque": bool(f.masque),
        "verifie": bool(f.verifie_le),
        "slug": f.slug,
        "brouillon_en_cours": f.brouillon is not None and bool(f.publie_le),
        "contenu": c,
        "manques": manques_pour_soumettre(c),
        "apercu": {
            "nom_complet": nom_affiche(p, True),
            "nom_court": nom_affiche(p, False),
            "slug_complet": base_slug(p, True),
            "slug_court": base_slug(p, False),
            "photo_url": _photo(f, c),
            "matieres": [matiere_row(k) for k in groupes_du_prof(p)],
        },
        "statuts": [{"key": k, "label": v} for k, v in STATUTS_PRO.items()],
        "limites": {
            "description_max": DESCRIPTION_MAX,
            "description_min": DESCRIPTION_MIN_SOUMISSION,
            "formation_max": FORMATION_MAX,
        },
    }


def vue_admin(f: ProfProfil) -> dict:
    p = f.professeur
    u = p.user if p else None
    return {
        "id": f.id,
        "professeur_id": f.professeur_id,
        "prenom": p.prenom if p else "",
        "nom": p.nom if p else "",
        "email": u.email if u else None,
        "professeur_status": p.status if p else None,
        "etat": f.etat,
        "motif_refus": f.motif_refus or "",
        "soumis_le": _iso(f.soumis_le),
        "publie_le": _iso(f.publie_le),
        "verifie_le": _iso(f.verifie_le),
        "masque": bool(f.masque),
        "ordre": f.ordre,
        "slug": f.slug,
        "visible": est_public(f),
        "publie": contenu_publie(f) if f.publie_le else None,
        "brouillon": contenu_brouillon(f) if (f.brouillon is not None or not f.publie_le) else None,
        "manques": manques_pour_soumettre(contenu_brouillon(f)),
        "nom_affiche": nom_affiche(p, contenu_brouillon(f)["nom_complet_public"]) if p else "",
        "photo_url": _photo(f, contenu_brouillon(f)),
        "matieres": [matiere_row(k) for k in groupes_du_prof(p)] if p else [],
    }


# ============================================================
# Inscription Prép'AdJuris depuis une fiche
# ============================================================

def groupes_pour_inscription(db: Session, slug: str | None, matieres: list[str]) -> tuple[int | None, dict[str, int]]:
    """Professeur et groupes à retenir quand l'inscription part d'une fiche :
    (professeur_id, {matière: groupe}) pour les matières que ce professeur
    enseigne. Slug inconnu, fiche masquée ou professeur suspendu : (None, {}),
    l'inscription se fait comme d'habitude. Jamais bloquant."""
    if not slug:
        return None, {}
    f = profil_public(db, slug)
    if f is None:
        return None, {}
    groupes = groupes_du_prof(f.professeur)
    retenus = {k: groupes[k] for k in matieres if k in groupes}
    return (f.professeur_id if retenus else None), retenus


def metadata_prof(professeur_id: int | None, groupes: dict[str, int]) -> dict:
    if not professeur_id or not groupes:
        return {}
    return {
        "prof_id": str(professeur_id),
        "prof_classes": ",".join(f"{k}:{cid}" for k, cid in groupes.items()),
    }


def lire_metadata_prof(meta: dict) -> tuple[int | None, dict[str, int]]:
    try:
        pid = int(meta.get("prof_id") or 0) or None
    except (TypeError, ValueError):
        return None, {}
    groupes: dict[str, int] = {}
    for morceau in (meta.get("prof_classes") or "").split(","):
        k, _, cid = morceau.partition(":")
        if k and cid.isdigit():
            groupes[k] = int(cid)
    return (pid if groupes else None), groupes
