"""
app/services/prepa_adjuris_billing.py
======================================
Calcul de la facturation Prép'AdJuris à partir du calendrier des séances.

Module PUR : aucune base de données, aucun appel Stripe. Tout ce qui touche
au monde extérieur vit dans app/services/prepa_adjuris_facturation.py ; ici
on ne fait que des calculs sur des dates, ce qui les rend testables
(tests/test_prepa_adjuris_billing.py).

Règles (cahier des charges) :
  - une séance est due si elle COMMENCE après l'inscription ;
  - la première séance due est payée d'avance à l'inscription (20 €) ;
  - les suivantes sont prélevées à terme échu, le dernier jour de leur mois
    à PREPA_HEURE_PRELEVEMENT (heure de Paris) ;
  - un mois sans séance donne un prélèvement de 0 € ;
  - rien n'est facturé après PREPA_FIN_PROGRAMME.

Correspondance Stripe : chaque mois M devient une phase d'échéancier qui
s'ouvre à la date de prélèvement de M, avec billing_cycle_anchor="phase_start".
Stripe facture une phase À SON OUVERTURE : la phase ouverte le 31 octobre
porte donc les séances d'octobre. C'est exactement ce qui manquait à
l'ancienne version, où la phase ouverte fin septembre portait octobre.
"""

from __future__ import annotations

import calendar
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.core.prepa_adjuris_config import (
    PREPA_FIN_PROGRAMME,
    PREPA_HEURE_PRELEVEMENT,
    PREPA_MATIERE_NAMES,
    PREPA_PRIX_SEANCE_CENTS,
    PREPA_TZ,
)

TZ = ZoneInfo(PREPA_TZ)

Mois = tuple[int, int]  # (année, mois)

_MOIS_FR = (
    "janvier", "février", "mars", "avril", "mai", "juin", "juillet",
    "août", "septembre", "octobre", "novembre", "décembre",
)
_JOURS_FR = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")


# ============================================================
# Mois et dates de prélèvement
# ============================================================

def cle_mois(m: Mois) -> str:
    return f"{m[0]:04d}-{m[1]:02d}"


def parse_mois(cle: str) -> Mois:
    annee, mois = cle.split("-")
    return int(annee), int(mois)


def mois_suivant(m: Mois) -> Mois:
    return (m[0] + 1, 1) if m[1] == 12 else (m[0], m[1] + 1)


def mois_de(dt: datetime) -> Mois:
    """Mois calendaire d'un instant, en heure de Paris : un cours le 31 à
    23 h 30 reste dans son mois, quelle que soit l'heure UTC."""
    local = _aware(dt).astimezone(TZ)
    return local.year, local.month


def fin_programme() -> Mois:
    return parse_mois(PREPA_FIN_PROGRAMME)


def date_prelevement(m: Mois) -> datetime:
    """Dernier jour du mois m à PREPA_HEURE_PRELEVEMENT (Paris), en UTC.
    zoneinfo gère le passage heure d'été / heure d'hiver."""
    heure, minute = (int(x) for x in PREPA_HEURE_PRELEVEMENT.split(":"))
    dernier_jour = calendar.monthrange(m[0], m[1])[1]
    local = datetime(m[0], m[1], dernier_jour, heure, minute, tzinfo=TZ)
    return local.astimezone(timezone.utc)


def premier_mois_facturable(reference: datetime) -> Mois:
    """Premier mois dont le prélèvement n'est pas encore passé à `reference`."""
    m = mois_de(reference)
    if date_prelevement(m) <= _aware(reference):
        m = mois_suivant(m)
    return m


def _aware(dt: datetime) -> datetime:
    """Les dates sans fuseau (SQLite en dev) sont considérées comme UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def seances_hebdomadaires(
    jour_semaine: int, heure: str, depuis: datetime, fin: Mois | None = None
) -> list[datetime]:
    """
    Une séance par semaine (lundi = 0), à l'heure de Paris donnée, du jour de
    `depuis` jusqu'à la fin du programme. Sert de calendrier de secours tant
    que l'agenda d'une matière n'est pas saisi.
    """
    from datetime import timedelta

    fin = fin or fin_programme()
    heures, minutes = (int(x) for x in heure.split(":"))
    jour = _aware(depuis).astimezone(TZ).date()
    jour += timedelta(days=(jour_semaine - jour.weekday()) % 7)
    dates = []
    while (jour.year, jour.month) <= fin:
        dates.append(
            datetime(jour.year, jour.month, jour.day, heures, minutes, tzinfo=TZ)
            .astimezone(timezone.utc)
        )
        jour += timedelta(days=7)
    return dates


# ============================================================
# Échéancier d'une matière
# ============================================================

@dataclass
class Echeancier:
    """
    Facturation d'une matière pour un élève.

    mois    : {"2026-10": 3, ...} séances prélevées à la fin de chaque mois,
              dans l'ordre, y compris les mois à 0.
    depuis  : premier mois facturable (sert de borne aux recalculs).
    credits : {"2026-10": 1, ...} séances déjà remboursées (crédit Stripe)
              sur un mois prélevé, après annulation d'un cours.
    """
    matiere_key: str
    inscrit_le: datetime
    seance_prepayee: datetime | None
    mois: dict[str, int]
    depuis: str
    credits: dict[str, int] = field(default_factory=dict)

    @property
    def total_seances(self) -> int:
        return sum(self.mois.values())

    def montant_cents(self, cle: str) -> int:
        return self.mois.get(cle, 0) * PREPA_PRIX_SEANCE_CENTS

    def to_dict(self) -> dict:
        return {
            "matiere_key": self.matiere_key,
            "inscrit_le": _aware(self.inscrit_le).isoformat(),
            "seance_prepayee": (
                _aware(self.seance_prepayee).isoformat() if self.seance_prepayee else None
            ),
            "mois": dict(self.mois),
            "depuis": self.depuis,
            "credits": dict(self.credits),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Echeancier":
        return cls(
            matiere_key=d["matiere_key"],
            inscrit_le=_parse_dt(d["inscrit_le"]),
            seance_prepayee=_parse_dt(d["seance_prepayee"]) if d.get("seance_prepayee") else None,
            mois={k: int(v) for k, v in (d.get("mois") or {}).items()},
            depuis=d.get("depuis") or next(iter(d.get("mois") or {}), ""),
            credits={k: int(v) for k, v in (d.get("credits") or {}).items()},
        )


def _parse_dt(s: str) -> datetime:
    return _aware(datetime.fromisoformat(s.replace("Z", "+00:00")))


def calculer_echeancier(
    matiere_key: str,
    dates_seances: list[datetime],
    inscrit_le: datetime,
    facturable_apres: datetime | None = None,
    fin: Mois | None = None,
) -> Echeancier:
    """
    Échéancier d'une matière pour une inscription à `inscrit_le`.

    dates_seances    : début des séances PRÉVUES de la matière (pas les
                       annulées, pas les séances communes).
    facturable_apres : rien n'est prélevé pour un mois dont la date de
                       prélèvement est déjà passée à cet instant. Égal à
                       inscrit_le pour une inscription normale ; « maintenant »
                       pour une inscription antidatée (lien de paiement admin).
    """
    inscrit_le = _aware(inscrit_le)
    reference = max(inscrit_le, _aware(facturable_apres)) if facturable_apres else inscrit_le

    a_venir = sorted(_aware(d) for d in dates_seances if _aware(d) > inscrit_le)
    prepayee = a_venir[0] if a_venir else None
    facturables = a_venir[1:]

    debut = premier_mois_facturable(reference)
    fin = fin or fin_programme()

    mois: dict[str, int] = {}
    m = debut
    while m <= fin:
        mois[cle_mois(m)] = 0
        m = mois_suivant(m)

    for d in facturables:
        k = cle_mois(mois_de(d))
        if k in mois:
            mois[k] += 1

    return Echeancier(
        matiere_key=matiere_key,
        inscrit_le=inscrit_le,
        seance_prepayee=prepayee,
        mois=mois,
        depuis=cle_mois(debut),
    )


def recalculer(ancien: Echeancier, dates_seances: list[datetime]) -> Echeancier:
    """Même inscription, calendrier à jour : sert après une modification de
    l'agenda. La borne `depuis` est conservée, les crédits aussi."""
    depuis = parse_mois(ancien.depuis) if ancien.depuis else None
    reference = (
        date_prelevement(_mois_precedent(depuis)) if depuis else ancien.inscrit_le
    )
    nouveau = calculer_echeancier(
        ancien.matiere_key, dates_seances, ancien.inscrit_le,
        facturable_apres=max(reference, ancien.inscrit_le),
        fin=parse_mois(max(ancien.mois)) if ancien.mois else None,
    )
    nouveau.credits = dict(ancien.credits)
    return nouveau


def _mois_precedent(m: Mois) -> Mois:
    return (m[0] - 1, 12) if m[1] == 1 else (m[0], m[1] - 1)


# ============================================================
# Comparaison avant / après une modification de l'agenda
# ============================================================

@dataclass
class Ecart:
    """Différence sur un mois entre l'échéancier stocké et le recalcul."""
    mois: str
    avant: int
    apres: int
    deja_preleve: bool

    @property
    def type(self) -> str:
        if not self.deja_preleve:
            return "ajustement"           # le prélèvement à venir change
        return "credit" if self.apres < self.avant else "non_facture"

    @property
    def delta_cents(self) -> int:
        return (self.apres - self.avant) * PREPA_PRIX_SEANCE_CENTS


def comparer(ancien: Echeancier, nouveau: Echeancier, maintenant: datetime) -> list[Ecart]:
    """
    Écarts mois par mois. Pour un mois déjà prélevé, `avant` tient compte des
    séances déjà remboursées : une annulation n'est créditée qu'une fois.
    """
    maintenant = _aware(maintenant)
    ecarts = []
    for k in sorted(set(ancien.mois) | set(nouveau.mois)):
        preleve = date_prelevement(parse_mois(k)) <= maintenant
        avant = ancien.mois.get(k, 0) - (ancien.credits.get(k, 0) if preleve else 0)
        apres = nouveau.mois.get(k, 0)
        if avant != apres:
            ecarts.append(Ecart(k, avant, apres, preleve))
    return ecarts


def fusionner(ancien: Echeancier, nouveau: Echeancier, maintenant: datetime) -> Echeancier:
    """
    Échéancier à stocker après recalcul : les mois futurs prennent les
    nouvelles quantités ; un mois déjà prélevé garde ce qui a été facturé et
    cumule les séances créditées.
    """
    maintenant = _aware(maintenant)
    resultat = Echeancier(
        matiere_key=ancien.matiere_key,
        inscrit_le=ancien.inscrit_le,
        seance_prepayee=nouveau.seance_prepayee,
        mois=dict(nouveau.mois),
        depuis=ancien.depuis,
        credits=dict(ancien.credits),
    )
    for ecart in comparer(ancien, nouveau, maintenant):
        if not ecart.deja_preleve:
            continue
        resultat.mois[ecart.mois] = ancien.mois.get(ecart.mois, 0)
        if ecart.type == "credit":
            resultat.credits[ecart.mois] = (
                ancien.credits.get(ecart.mois, 0) + (ecart.avant - ecart.apres)
            )
    # Les mois prélevés sans écart gardent aussi la quantité facturée.
    for k, q in ancien.mois.items():
        if date_prelevement(parse_mois(k)) <= maintenant:
            resultat.mois[k] = q
    return resultat


# ============================================================
# Phases Stripe
# ============================================================

def phases_mensuelles(
    echeanciers: list[Echeancier], prices_recurring: dict[str, str]
) -> list[dict]:
    """
    Une phase par mois facturé, toutes matières de l'abonnement confondues
    (un item par matière, chacun avec sa quantité).

    La phase du mois M s'ouvre à la date de prélèvement de M et se ferme à
    celle de M+1. La dernière couvre donc un mois entier après le dernier
    prélèvement : l'abonnement reste actif (grade conservé) jusqu'à la fin
    du mois suivant, puis s'arrête sans nouvelle facture. Une fin immédiate
    risquerait d'annuler l'abonnement avant que la facture de décembre soit
    encaissée.
    """
    cles = sorted(set().union(*(e.mois.keys() for e in echeanciers))) if echeanciers else []
    phases = []
    for k in cles:
        m = parse_mois(k)
        phases.append({
            "start_date": int(date_prelevement(m).timestamp()),
            "end_date": int(date_prelevement(mois_suivant(m)).timestamp()),
            "items": [
                {"price": prices_recurring[e.matiere_key], "quantity": e.mois.get(k, 0)}
                for e in echeanciers
            ],
            "billing_cycle_anchor": "phase_start",
            "proration_behavior": "none",
        })
    return phases


def fin_abonnement(echeanciers: list[Echeancier]) -> datetime | None:
    """Fin de la dernière phase : date à laquelle l'abonnement s'arrête."""
    cles = sorted(set().union(*(e.mois.keys() for e in echeanciers))) if echeanciers else []
    if not cles:
        return None
    return date_prelevement(mois_suivant(parse_mois(cles[-1])))


# ============================================================
# Devis : plusieurs matières, une inscription
# ============================================================

def prelevements(echeanciers: list[Echeancier]) -> list[dict]:
    """Prélèvements à venir, toutes matières confondues, dans l'ordre."""
    cles = sorted(set().union(*(e.mois.keys() for e in echeanciers))) if echeanciers else []
    lignes = []
    for k in cles:
        m = parse_mois(k)
        detail = {e.matiere_key: e.mois.get(k, 0) for e in echeanciers}
        lignes.append({
            "mois": k,
            "date": date_prelevement(m).isoformat(),
            "seances": sum(detail.values()),
            "montant_cents": sum(detail.values()) * PREPA_PRIX_SEANCE_CENTS,
            "detail": detail,
        })
    return lignes


def devis_vers_metadata(echeanciers: list[Echeancier]) -> str:
    """
    Devis figé dans la metadata de la Checkout Session, pour que le webhook
    applique exactement ce que l'élève a vu. Format compact : une valeur de
    metadata Stripe est limitée à 500 caractères.
      {"d":"2026-10","m":{"L1_x":{"p":"2026-10-08T19:00:00+00:00","q":[3,4,4]}}}
    """
    if not echeanciers:
        return ""
    depuis = echeanciers[0].depuis
    contenu = {
        "d": depuis,
        "m": {
            e.matiere_key: {
                "p": _aware(e.seance_prepayee).isoformat() if e.seance_prepayee else None,
                "q": list(e.mois.values()),
            }
            for e in echeanciers
        },
    }
    return json.dumps(contenu, separators=(",", ":"))


def devis_depuis_metadata(brut: str, inscrit_le: datetime) -> list[Echeancier] | None:
    """Inverse de devis_vers_metadata. None si absent ou illisible."""
    if not brut:
        return None
    try:
        contenu = json.loads(brut)
        depuis = contenu["d"]
        echeanciers = []
        for key, v in contenu["m"].items():
            m = parse_mois(depuis)
            mois = {}
            for q in v["q"]:
                mois[cle_mois(m)] = int(q)
                m = mois_suivant(m)
            echeanciers.append(Echeancier(
                matiere_key=key,
                inscrit_le=_aware(inscrit_le),
                seance_prepayee=_parse_dt(v["p"]) if v.get("p") else None,
                mois=mois,
                depuis=depuis,
            ))
        return echeanciers
    except (KeyError, ValueError, TypeError, AttributeError):
        return None


# ============================================================
# Textes (page Stripe, site, console)
# ============================================================

def date_fr(dt: datetime, avec_jour: bool = False, avec_heure: bool = False) -> str:
    local = _aware(dt).astimezone(TZ)
    jour = "1er" if local.day == 1 else str(local.day)
    texte = f"{jour} {_MOIS_FR[local.month - 1]}"
    if avec_jour:
        texte = f"{_JOURS_FR[local.weekday()]} {texte}"
    if avec_heure:
        texte += f" à {local.hour} h {local.minute:02d}" if local.minute else f" à {local.hour} h"
    return texte


def euros(cents: int) -> str:
    return f"{cents // 100} €" if cents % 100 == 0 else f"{cents / 100:.2f} €".replace(".", ",")


def _enumerer(mots: list[str]) -> str:
    return mots[0] if len(mots) == 1 else ", ".join(mots[:-1]) + " et " + mots[-1]


def _seances_facturees(e: Echeancier, dates: list[datetime], mois: str) -> list[datetime] | None:
    """Dates des séances de `e` prélevées pour `mois`, ou None si elles ne
    correspondent pas au nombre de l'échéancier (on n'affiche alors que le
    nombre, jamais une liste fausse)."""
    apres = e.seance_prepayee or e.inscrit_le
    liste = sorted(
        d for d in (_aware(x) for x in dates)
        if d > _aware(apres) and cle_mois(mois_de(d)) == mois
    )
    return liste if len(liste) == e.mois.get(mois, 0) else None


def _texte_compact(echeanciers: list[Echeancier], debut: str) -> str:
    lignes = [
        f"{euros(p['montant_cents'])} le {date_fr(datetime.fromisoformat(p['date']))}"
        for p in prelevements(echeanciers)
    ]
    if not lignes:
        return debut
    return (
        f"{debut} Ensuite, les séances suivantes sont prélevées à la fin de chaque "
        f"mois (20 € la séance) : {_enumerer(lignes)}. Aucun prélèvement après."
    )


STRIPE_TEXTE_MAX = 1200


def _nom(matiere_key: str) -> str:
    return PREPA_MATIERE_NAMES.get(matiere_key, matiere_key)


def _jour_seance(d: datetime) -> str:
    """« Mardi 20 octobre à 21 h »."""
    texte = date_fr(d, avec_jour=True, avec_heure=True)
    return texte[0].upper() + texte[1:]


def _bloc_inscription(echeanciers: list[Echeancier], inscription_cents: int | None) -> str:
    plusieurs = len(echeanciers) > 1
    if inscription_cents is None:
        return (
            "Aucun montant n'est débité ce jour : les frais d'inscription ont déjà "
            "été réglés. Le moyen de paiement est enregistré pour les prélèvements "
            "ci-dessous."
        )
    total = inscription_cents * len(echeanciers)
    texte = f"Montant débité ce jour : {euros(total)}"
    if plusieurs:
        texte += f" ({euros(inscription_cents)} × {len(echeanciers)} matières)"
    texte += ". Ce montant correspond au règlement anticipé de la prochaine séance"
    jours = {e.seance_prepayee for e in echeanciers if e.seance_prepayee}
    if len(jours) == 1 and all(e.seance_prepayee for e in echeanciers):
        d = next(iter(jours))
        texte += (" de chaque matière" if plusieurs else "") + f", le {date_fr(d, avec_jour=True, avec_heure=True)}."
    elif jours:
        texte += " de chaque matière : " + " ; ".join(
            f"{_nom(e.matiere_key)}, le {date_fr(e.seance_prepayee, avec_jour=True, avec_heure=True)}"
            for e in echeanciers if e.seance_prepayee
        ) + "."
    else:
        texte += "."
    return texte


def _bloc_regle(plusieurs: bool) -> str:
    prix = euros(PREPA_PRIX_SEANCE_CENTS)
    return (
        f"Modalités de facturation : chaque séance est facturée {prix}, à raison "
        "d'une séance par semaine" + (" et par matière" if plusieurs else "") + ". "
        "Le montant mensuel varie donc selon le nombre de semaines de cours dans "
        "le mois. Il est prélevé automatiquement sur le moyen de paiement "
        "enregistré, le dernier jour de chaque mois, au titre des séances de ce mois."
    )


def _bloc_mois(echeanciers: list[Echeancier], p: dict, dates: dict[str, list[datetime]]) -> str:
    mois = p["mois"]
    nom = _MOIS_FR[parse_mois(mois)[1] - 1].upper()
    prelev = date_fr(datetime.fromisoformat(p["date"]))
    n = p["seances"]
    if n == 0:
        return f"{nom} : aucune séance, aucun prélèvement."

    plusieurs = len(echeanciers) > 1
    prix = euros(PREPA_PRIX_SEANCE_CENTS)
    listes = [_seances_facturees(e, dates.get(e.matiere_key, []), mois) for e in echeanciers]
    memes = all(x is not None for x in listes) and all(x == listes[0] for x in listes)

    if memes:
        n1 = len(listes[0])
        lignes = [f"{nom} : {n1} séance{'s' if n1 > 1 else ''}"]
        lignes += [f"▪ {_jour_seance(d)}" for d in listes[0]]
        calcul = f"{n1} × {prix}"
        if plusieurs:
            calcul = f"{n1} séance{'s' if n1 > 1 else ''} × {len(echeanciers)} matières × {prix}"
    else:
        lignes = [f"{nom} : {n} séance{'s' if n > 1 else ''}"]
        if all(x is not None for x in listes):
            lignes += [
                f"▪ {_jour_seance(d)} ({_nom(e.matiere_key)})"
                for d, e in sorted(
                    ((d, e) for e, x in zip(echeanciers, listes) for d in x),
                    key=lambda c: c[0],
                )
            ]
        calcul = f"{n} × {prix}"
    lignes.append(f"Total : {calcul} = {euros(p['montant_cents'])}, prélevé le {prelev}.")
    return "\n".join(lignes)


def _bloc_fin(echeanciers: list[Echeancier], inscription_cents: int | None) -> str:
    lignes = prelevements(echeanciers)
    total = sum(p["montant_cents"] for p in lignes)
    texte = f"Montant total des prélèvements à venir : {euros(total)}"
    if inscription_cents:
        texte += f", en sus des {euros(inscription_cents * len(echeanciers))} réglés ce jour"
    texte += "."
    if lignes:
        dernier = _MOIS_FR[parse_mois(lignes[-1]["mois"])[1] - 1]
        texte += f" Aucun prélèvement n'interviendra après {dernier}."
    return texte


def blocs_recap(
    echeanciers: list[Echeancier],
    inscription_cents: int | None,
    dates: dict[str, list[datetime]],
) -> list[str]:
    """Paragraphes du récapitulatif détaillé : inscription, règle de calcul,
    un bloc par mois (chaque séance listée, puis le total), total."""
    return (
        [_bloc_inscription(echeanciers, inscription_cents), _bloc_regle(len(echeanciers) > 1)]
        + [_bloc_mois(echeanciers, p, dates) for p in prelevements(echeanciers)]
        + [_bloc_fin(echeanciers, inscription_cents)]
    )


def _debut_compact(echeanciers: list[Echeancier], inscription_cents: int | None) -> str:
    if inscription_cents is None:
        return "Inscription déjà réglée."
    plusieurs = len(echeanciers) > 1
    total = inscription_cents * len(echeanciers)
    prepayees = [
        f"{_nom(e.matiere_key)} : {date_fr(e.seance_prepayee, avec_jour=True)}"
        for e in echeanciers if e.seance_prepayee
    ]
    debut = f"Aujourd'hui : {euros(total)}, qui règle{'nt' if plusieurs else ''} d'avance la prochaine séance"
    if prepayees:
        debut += " (" + " ; ".join(prepayees) + ")"
    return debut + "."


def texte_recap(
    echeanciers: list[Echeancier],
    inscription_cents: int | None = PREPA_PRIX_SEANCE_CENTS,
    dates: dict[str, list[datetime]] | None = None,
) -> str:
    """
    Explication du paiement (site, console). inscription_cents=None :
    inscription déjà réglée (lien admin).

    dates : séances prévues de chaque matière (agenda). Si elles sont
    fournies, le texte est détaillé : chaque mois liste ses séances une par
    une, puis son total, pour que l'élève comprenne pourquoi un mois coûte
    40 € et un autre 100 €. Sinon, version courte (montant et date de chaque
    prélèvement), limitée à 1 200 caractères.
    """
    if not echeanciers:
        return ""
    if dates is None:
        return _texte_compact(echeanciers, _debut_compact(echeanciers, inscription_cents))[:STRIPE_TEXTE_MAX]
    return "\n\n".join(blocs_recap(echeanciers, inscription_cents, dates))


def textes_stripe(
    echeanciers: list[Echeancier],
    inscription_cents: int | None,
    dates: dict[str, list[datetime]],
) -> dict:
    """
    custom_text de la Checkout Session. Stripe limite chaque texte à 1 200
    caractères : le récapitulatif détaillé commence au-dessus du bouton de
    paiement (submit) et continue en dessous (after_submit) si besoin, en
    coupant entre deux paragraphes. S'il ne tient pas, version courte.
    """
    blocs = blocs_recap(echeanciers, inscription_cents, dates)
    parties: list[list[str]] = [[], []]
    i = 0
    for b in blocs:
        while i < 2 and len("\n\n".join(parties[i] + [b])) > STRIPE_TEXTE_MAX:
            i += 1
        if i == 2:
            compact = _texte_compact(echeanciers, _debut_compact(echeanciers, inscription_cents))
            return {"submit": {"message": compact[:STRIPE_TEXTE_MAX]}}
        parties[i].append(b)
    out = {"submit": {"message": "\n\n".join(parties[0])}}
    if parties[1]:
        out["after_submit"] = {"message": "\n\n".join(parties[1])}
    return out
