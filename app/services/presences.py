"""
app/services/presences.py
==========================
Présence aux cours Prép'AdJuris. Routes : app/routers/presences.py (espace
professeur et console), app/routers/discord_bot.py (relevés du bot). Bot :
app/bot_discord/cogs/presence_cours.py.

Règles :
  - Relevé automatique : pendant un cours lancé depuis #cours-start (statut
    en_cours), le bot envoie chaque minute la liste des membres du salon
    vocal du groupe (PrepaAdjurisClasse.discord_vocal_id). Chaque relevé
    ajoute à chacun le temps écoulé depuis le relevé précédent (2 minutes au
    plus : un bot redémarré ne crédite pas le temps où il ne voyait rien).
  - Présent dès PREPA_PRESENCE_MIN_MINUTES (5) dans le vocal pendant le cours.
  - Le professeur du cours n'est jamais compté. Un membre Discord sans compte
    NAVIRE relié est affiché (« Discord non relié ») mais jamais compté :
    le professeur coche l'étudiant à la main.
  - Correction : le professeur coche ou décoche un étudiant pendant
    PREPA_PRESENCE_CORRECTION_HEURES (48 h) après la fin du cours, la
    console ensuite. Jamais sur un mois déjà versé au professeur. Une
    correction fixe la présence (source prof | admin) : les relevés suivants
    ne la recalculent plus. Auteur et heure sont gardés.
  - Cours oublié sur Discord : la console le crée après coup (terminé),
    puis coche les présents.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.prepa_adjuris_config import (
    PREPA_COURS_DUREE_MINUTES,
    PREPA_PRESENCE_CORRECTION_HEURES,
    PREPA_PRESENCE_MIN_MINUTES,
    matiere_label,
)
from app.db.models import (
    PrepaAdjurisAffectation,
    PrepaAdjurisClasse,
    PrepaAdjurisCours,
    PrepaAdjurisProfesseur,
    PrepaAdjurisRemuneration,
    PrepaPresence,
    User,
)
from app.services import cours_live
from app.services.prepa_adjuris_billing import cle_mois, mois_de

RELEVE_MAX_SECONDES = 120     # crédit maximal d'un relevé
RELEVE_MIN_SECONDES = 20      # relevé envoyé deux fois : le second est ignoré


class PresenceErreur(Exception):
    """Message lisible, renvoyé tel quel (400)."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: datetime | None) -> datetime | None:
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    dt = aware(dt)
    return dt.isoformat() if dt else None


def seuil_secondes() -> int:
    return PREPA_PRESENCE_MIN_MINUTES * 60


# ============================================================
# Relevé du bot
# ============================================================

def cours_a_relever(db: Session) -> list[dict]:
    """Cours en cours dont le groupe a un salon vocal : ce que le bot doit regarder."""
    rows = db.execute(
        select(PrepaAdjurisCours).where(PrepaAdjurisCours.statut == "en_cours")
    ).scalars().all()
    out = []
    for c in rows:
        vocal = (c.classe.discord_vocal_id or "").strip() if c.classe else ""
        if vocal:
            out.append({"cours_id": c.id, "channel_id": vocal})
    return out


def _inscription_par_user(db: Session, classe: PrepaAdjurisClasse) -> dict[int, int]:
    """user_id → inscription dans le groupe (élèves sans compte exclus)."""
    return {e.user_id: e.enrollment_id for e in cours_live.eleves_du_groupe(db, classe) if e.user_id}


def enregistrer_releve(
    db: Session, c: PrepaAdjurisCours, membres: list[dict], maintenant: datetime | None = None
) -> int:
    """Un relevé du salon vocal : membres = [{discord_id, nom}]. Renvoie le
    nombre de secondes créditées (0 si ignoré)."""
    maintenant = aware(maintenant) or utcnow()
    if c.statut != "en_cours":
        return 0
    depuis = aware(c.presence_releve_at) or aware(c.started_at)
    ecart = int((maintenant - depuis).total_seconds())
    if c.presence_releve_at is not None and ecart < RELEVE_MIN_SECONDES:
        return 0
    credit = max(0, min(ecart, RELEVE_MAX_SECONDES))
    c.presence_releve_at = maintenant

    ids = {str(m.get("discord_id") or "").strip(): str(m.get("nom") or "").strip() for m in membres}
    ids.pop("", None)
    prof_discord = (c.professeur.user.discord_id or "") if c.professeur and c.professeur.user else ""
    ids.pop(prof_discord, None)
    if not ids or not credit:
        db.commit()
        return 0

    users = {
        u.discord_id: u for u in db.execute(select(User).where(User.discord_id.in_(list(ids)))).scalars()
    }
    lignes = {p.user_id: p for p in _lignes(db, c) if p.user_id}
    non_relies = {p.discord_id: p for p in _lignes(db, c) if not p.user_id and p.discord_id}
    inscriptions = None
    for discord_id, nom in ids.items():
        u = users.get(discord_id)
        if u is not None:
            p = lignes.get(u.id)
            if p is None:
                if inscriptions is None:
                    inscriptions = _inscription_par_user(db, c.classe)
                p = PrepaPresence(cours_id=c.id, user_id=u.id, enrollment_id=inscriptions.get(u.id),
                                  discord_id=discord_id, nom=(nom or u.username or "")[:170])
                db.add(p)
                lignes[u.id] = p
        else:
            p = non_relies.get(discord_id)
            if p is None:
                p = PrepaPresence(cours_id=c.id, discord_id=discord_id, nom=nom[:170])
                db.add(p)
                non_relies[discord_id] = p
        p.secondes = (p.secondes or 0) + credit
        p.vu_le = maintenant
        if p.source == "discord":
            p.present = bool(p.user_id) and p.secondes >= seuil_secondes()
    db.commit()
    return credit


# ============================================================
# Liste et corrections
# ============================================================

def _lignes(db: Session, c: PrepaAdjurisCours) -> list[PrepaPresence]:
    return db.execute(
        select(PrepaPresence).where(PrepaPresence.cours_id == c.id).order_by(PrepaPresence.id)
    ).scalars().all()


def fin_cours(c: PrepaAdjurisCours) -> datetime:
    return aware(c.ended_at) or (aware(c.started_at) + timedelta(minutes=c.duree_comptee_minutes or PREPA_COURS_DUREE_MINUTES))


def limite_correction_prof(c: PrepaAdjurisCours) -> datetime:
    return fin_cours(c) + timedelta(hours=PREPA_PRESENCE_CORRECTION_HEURES)


def mois_verse(db: Session, c: PrepaAdjurisCours) -> bool:
    """Le mois du cours a-t-il déjà été versé au professeur pour ce groupe ?"""
    a = db.execute(
        select(PrepaAdjurisAffectation.id).where(
            PrepaAdjurisAffectation.professeur_id == c.professeur_id,
            PrepaAdjurisAffectation.classe_id == c.classe_id,
        )
    ).scalar_one_or_none()
    if a is None:
        return False
    return db.execute(
        select(PrepaAdjurisRemuneration.id).where(
            PrepaAdjurisRemuneration.affectation_id == a,
            PrepaAdjurisRemuneration.mois == cle_mois(mois_de(c.started_at)),
            PrepaAdjurisRemuneration.verse_le.is_not(None),
        )
    ).first() is not None


def droit_correction(db: Session, c: PrepaAdjurisCours, admin: bool, maintenant: datetime | None = None) -> str | None:
    """None si la correction est permise, sinon la raison."""
    maintenant = aware(maintenant) or utcnow()
    if mois_verse(db, c):
        return "Ce mois a déjà été versé : la présence ne peut plus changer. L'équipe peut annuler le versement depuis la console."
    if not admin and maintenant > limite_correction_prof(c):
        return f"La correction est possible {PREPA_PRESENCE_CORRECTION_HEURES} h après le cours. Au-delà, demande à l'équipe NAVIRE."
    return None


def liste(db: Session, c: PrepaAdjurisCours, admin: bool = False, maintenant: datetime | None = None) -> dict:
    """Les élèves du groupe (cochés ou non), puis les autres personnes vues
    dans le vocal (hors groupe, Discord non relié)."""
    maintenant = aware(maintenant) or utcnow()
    lignes = _lignes(db, c)
    par_user = {p.user_id: p for p in lignes if p.user_id}
    par_inscription = {p.enrollment_id: p for p in lignes if p.enrollment_id and not p.user_id}
    vus: set[int] = set()
    eleves = []
    for e in cours_live.eleves_du_groupe(db, c.classe):
        p = par_user.get(e.user_id) if e.user_id else par_inscription.get(e.enrollment_id)
        if p is not None:
            vus.add(p.id)
        eleves.append(_eleve_row(p, e.enrollment_id, e.user_id, e.nom, bool(e.discord_id) or bool(p and p.discord_id)))
    autres = [
        {**_eleve_row(p, p.enrollment_id, p.user_id, p.nom, bool(p.discord_id)),
         "hors_groupe": bool(p.user_id), "non_relie": not p.user_id and bool(p.discord_id)}
        for p in lignes if p.id not in vus
    ]
    refus = droit_correction(db, c, admin, maintenant)
    return {
        "cours": cours_row(db, c, maintenant),
        "eleves": eleves,
        "autres": autres,
        "modifiable": refus is None,
        "raison": refus,
        "seuil_minutes": PREPA_PRESENCE_MIN_MINUTES,
    }


def _eleve_row(p: PrepaPresence | None, enrollment_id, user_id, nom: str, discord_relie: bool) -> dict:
    return {
        "presence_id": p.id if p else None,
        "enrollment_id": enrollment_id,
        "user_id": user_id,
        "nom": nom or (p.nom if p else ""),
        "minutes": (p.secondes or 0) // 60 if p else 0,
        "present": bool(p and p.present),
        "source": p.source if p else None,
        "modifie_le": _iso(p.modifie_le) if p else None,
        "discord_relie": discord_relie,
    }


def corriger(
    db: Session, c: PrepaAdjurisCours, auteur: User, present: bool, admin: bool,
    presence_id: int | None = None, enrollment_id: int | None = None, maintenant: datetime | None = None,
) -> None:
    refus = droit_correction(db, c, admin, maintenant)
    if refus:
        raise PresenceErreur(refus)
    if presence_id is not None:
        p = db.get(PrepaPresence, presence_id)
        if p is None or p.cours_id != c.id:
            raise PresenceErreur("Présence introuvable dans ce cours.")
        if not p.user_id and not p.enrollment_id:
            raise PresenceErreur("Ce membre Discord n'a pas de compte NAVIRE relié : coche l'étudiant dans la liste du groupe.")
    elif enrollment_id is not None:
        e = next((x for x in cours_live.eleves_du_groupe(db, c.classe) if x.enrollment_id == enrollment_id), None)
        if e is None:
            raise PresenceErreur("Cet étudiant ne fait pas partie du groupe.")
        cle = (PrepaPresence.user_id == e.user_id) if e.user_id else (PrepaPresence.enrollment_id == enrollment_id)
        p = db.execute(select(PrepaPresence).where(PrepaPresence.cours_id == c.id, cle)).scalars().first()
        if p is None:
            p = PrepaPresence(cours_id=c.id, user_id=e.user_id, enrollment_id=enrollment_id,
                              discord_id=e.discord_id, nom=e.nom, secondes=0)
            db.add(p)
    else:
        raise PresenceErreur("Indique l'étudiant à cocher.")
    p.present = present
    p.source = "admin" if admin else "prof"
    p.modifie_par_id = auteur.id
    p.modifie_le = maintenant or utcnow()
    db.commit()


# ============================================================
# Cours
# ============================================================

def cours_row(db: Session, c: PrepaAdjurisCours, maintenant: datetime | None = None) -> dict:
    maintenant = aware(maintenant) or utcnow()
    lignes = _lignes(db, c)
    p = c.professeur
    return {
        "id": c.id,
        "classe_id": c.classe_id,
        "groupe": c.classe.nom if c.classe else "",
        "matiere_key": c.matiere_key,
        "matiere_label": matiere_label(c.matiere_key),
        "professeur_id": c.professeur_id,
        "professeur": f"{p.prenom} {p.nom}".strip() if p else "",
        "debut": _iso(c.started_at),
        "fin": _iso(fin_cours(c)),
        "statut": c.statut,
        "duree_comptee_minutes": c.duree_comptee_minutes,
        "cree_par_admin": bool(c.cree_par_admin),
        "vocal": bool((c.classe.discord_vocal_id or "").strip()) if c.classe else False,
        "releve": c.presence_releve_at is not None,
        "nb_presents": sum(1 for x in lignes if x.present),
        "limite_correction": _iso(limite_correction_prof(c)),
    }


def cours_du_prof(db: Session, prof: PrepaAdjurisProfesseur, depuis: datetime) -> list[PrepaAdjurisCours]:
    return db.execute(
        select(PrepaAdjurisCours)
        .where(PrepaAdjurisCours.professeur_id == prof.id, PrepaAdjurisCours.started_at >= depuis)
        .order_by(PrepaAdjurisCours.started_at.desc())
    ).scalars().all()


def creer_cours_oublie(
    db: Session, classe: PrepaAdjurisClasse, prof: PrepaAdjurisProfesseur, debut: datetime, duree_minutes: int,
) -> PrepaAdjurisCours:
    if not any(a.classe_id == classe.id for a in prof.affectations):
        raise PresenceErreur("Ce professeur n'est pas affecté à ce groupe.")
    debut = aware(debut)
    if debut > utcnow():
        raise PresenceErreur("Un cours oublié est forcément passé.")
    c = PrepaAdjurisCours(
        classe_id=classe.id,
        matiere_key=classe.matiere_key,
        professeur_id=prof.id,
        seance_id=cours_live.seance_proche(db, classe.matiere_key, debut),
        statut="termine",
        started_at=debut,
        ended_at=debut + timedelta(minutes=duree_minutes),
        duree_comptee_minutes=duree_minutes,
        cree_par_admin=True,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c
