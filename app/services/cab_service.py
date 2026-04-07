# app/services/cab_service.py
"""
Service NavireCab — Génération de dossiers et calcul des scores.

Support 1 : Génération complète par IA (OpenAI gpt-4o-mini)
Support 2 : Templates stockés en DB ou en mémoire (fallback)
"""

from __future__ import annotations

import json
import random
import logging
from typing import Optional

from openai import OpenAI
from sqlalchemy.orm import Session

from app.db.models import CabDossierTemplate

logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURATION
# ============================================================

OPENAI_MODEL = "gpt-4o-mini"
OPENAI_MAX_TOKENS = 4000
GENERATION_TIMEOUT = 60  # secondes

DIFFICULTY_PROMPTS = {
    "easy": "niveau L3/M1, questions directes, un seul problème juridique, pas de subtilités",
    "medium": "niveau M2/CRFPA, deux ou trois problèmes imbriqués, quelques subtilités jurisprudentielles",
    "hard": "niveau avocat junior, problèmes complexes, distinctions jurisprudentielles fines, exceptions, conflits de normes"
}

THEMES = [
    "contrats (formation, exécution, inexécution)",
    "responsabilité civile (délictuelle et contractuelle)",
    "droit des sociétés (SARL, SAS, SA)",
    "droit du travail (contrat, licenciement, rupture)",
    "droit de la consommation",
    "droit des sûretés",
    "procédure civile"
]


# ============================================================
# TEMPLATES EN MÉMOIRE (fallback si DB vide)
# ============================================================

FALLBACK_TEMPLATES = [
    {
        "code": "contrat_inexec_01",
        "title": "Retard de livraison - SAS TechDistrib",
        "theme": "contrats",
        "difficulty": "medium",
        "branch": "droit des affaires",
        "content": {
            "mail": {
                "subject": "URGENT - Retard livraison fournisseur / Client mécontent",
                "from": "Marie Dupont <m.dupont@cabinet-avocats.fr>",
                "body": """Bonjour,

Je te transmets un dossier urgent. Notre client, la SAS TechDistrib, est furieux.

En résumé : ils ont commandé 500 unités de composants électroniques à la société MicroParts SARL le 15 janvier. Le contrat prévoyait une livraison au plus tard le 15 février. 

Nous sommes le 10 mars et toujours rien. MicroParts invoque des "difficultés d'approvisionnement" liées à un sous-traitant asiatique.

TechDistrib a perdu un marché important avec Carrefour à cause de ce retard et veut agir.

Peux-tu analyser le dossier et me faire un point sur les options ?

Merci,
Marie"""
            },
            "attachment": """CONTRAT DE VENTE DE MARCHANDISES

Entre :
- SAS TechDistrib, 45 rue de l'Industrie, 69003 Lyon (ci-après "l'Acheteur")
- SARL MicroParts, 12 avenue des Techniques, 31000 Toulouse (ci-après "le Vendeur")

Article 1 - Objet
Le Vendeur s'engage à livrer à l'Acheteur 500 unités de composants électroniques référence MP-2024-X.

Article 2 - Prix
Prix unitaire : 45€ HT. Total : 22 500€ HT.
Paiement : 30% à la commande, solde à la livraison.

Article 3 - Livraison
Date de livraison : au plus tard le 15 février 2026.
Lieu : entrepôt de l'Acheteur, Lyon.
Le non-respect du délai de livraison pourra donner lieu à des pénalités de retard de 1% du prix total par semaine de retard, plafonnées à 10%.

Article 4 - Force majeure
Aucune des parties ne sera responsable d'un manquement à ses obligations si ce manquement résulte d'un cas de force majeure au sens de l'article 1218 du Code civil.

Article 5 - Résolution
En cas de manquement grave d'une partie à ses obligations, l'autre partie pourra résoudre le contrat de plein droit 15 jours après mise en demeure restée infructueuse.

Fait à Lyon, le 15 janvier 2026

---
ÉCHANGES EMAIL :

De: TechDistrib → MicroParts (20 février 2026)
Objet: Retard livraison
Nous constatons que la livraison prévue le 15 février n'a pas eu lieu. Merci de nous informer.

De: MicroParts → TechDistrib (25 février 2026)
Notre sous-traitant en Chine fait face à des difficultés de production suite à des coupures d'électricité.

De: TechDistrib → MicroParts (5 mars 2026)
Mise en demeure : livrez sous 8 jours ou nous engagerons des poursuites. Nous avons perdu le marché Carrefour.""",
            "phases": [
                {
                    "question": "Quelle est la qualification juridique principale de la situation ?",
                    "choices": [
                        "Vice caché affectant les marchandises",
                        "Inexécution d'une obligation contractuelle de livraison",
                        "Nullité du contrat pour erreur sur la substance",
                        "Responsabilité délictuelle du vendeur"
                    ],
                    "correct": 1,
                    "debrief": "Il s'agit d'une inexécution contractuelle : le vendeur n'a pas respecté son obligation de livraison à la date convenue. Le contrat est valablement formé, les marchandises n'ont pas été livrées donc pas de vice caché, et la responsabilité est contractuelle car les parties sont liées par un contrat.",
                    "refs": ["Art. 1217 C. civ.", "Art. 1231-1 C. civ."]
                },
                {
                    "question": "Les difficultés du sous-traitant chinois peuvent-elles constituer un cas de force majeure exonératoire ?",
                    "choices": [
                        "Oui, car elles sont extérieures au vendeur",
                        "Oui, si le vendeur prouve l'imprévisibilité et l'irrésistibilité",
                        "Non, car le vendeur a une obligation de résultat",
                        "Non, car les difficultés d'un sous-traitant ne sont jamais un cas de force majeure"
                    ],
                    "correct": 1,
                    "debrief": "La force majeure suppose trois critères cumulatifs : extériorité, imprévisibilité et irrésistibilité (art. 1218 C. civ.). Le vendeur doit prouver que ces difficultés remplissent les trois critères. De simples 'difficultés d'approvisionnement' sont généralement insuffisantes.",
                    "refs": ["Art. 1218 C. civ.", "Cass. com., 16 sept. 2014, n°13-20.306"]
                },
                {
                    "question": "Quelles pénalités TechDistrib peut-elle réclamer à ce stade (10 mars) ?",
                    "choices": [
                        "Aucune, car le contrat ne prévoit pas de pénalités",
                        "1% par semaine soit environ 3% (3 semaines de retard)",
                        "10% (plafond contractuel atteint)",
                        "Pénalités illimitées car le plafond est abusif"
                    ],
                    "correct": 1,
                    "debrief": "Le contrat prévoit 1% par semaine de retard, plafonnées à 10%. Au 10 mars, le retard est d'environ 3 semaines (depuis le 15 février), soit 3% de 22 500€ = 675€. Le plafond n'est pas atteint.",
                    "refs": ["Art. 1231-5 C. civ. (clause pénale)"]
                },
                {
                    "question": "TechDistrib peut-elle obtenir réparation du préjudice lié à la perte du marché Carrefour ?",
                    "choices": [
                        "Non, car c'est un préjudice indirect et imprévisible",
                        "Oui, si elle prouve le lien de causalité et que ce préjudice était prévisible",
                        "Oui, automatiquement car tout préjudice est indemnisable",
                        "Non, car les pénalités contractuelles excluent tout autre dommages-intérêts"
                    ],
                    "correct": 1,
                    "debrief": "En matière contractuelle, le débiteur n'est tenu que des dommages prévisibles lors de la conclusion du contrat (art. 1231-3), sauf faute lourde. TechDistrib devra prouver la réalité du préjudice, le lien de causalité, et que MicroParts pouvait prévoir ce type de conséquence.",
                    "refs": ["Art. 1231-3 C. civ.", "Art. 1231-4 C. civ."]
                },
                {
                    "question": "Quelle est la meilleure stratégie à recommander à TechDistrib ?",
                    "choices": [
                        "Résoudre immédiatement le contrat et chercher un autre fournisseur",
                        "Attendre encore pour maximiser les pénalités de retard",
                        "Mettre en demeure avec délai, puis résoudre si inexécution persiste, tout en préparant une action en dommages-intérêts",
                        "Saisir immédiatement le tribunal en référé pour forcer la livraison"
                    ],
                    "correct": 2,
                    "debrief": "La stratégie optimale combine : mise en demeure formelle (déjà faite), résolution du contrat si le délai expire sans livraison (art. 1226), puis action en dommages-intérêts pour pénalités + perte de marché. L'exécution forcée est peu utile si le fournisseur ne peut pas livrer.",
                    "refs": ["Art. 1217 C. civ.", "Art. 1226 C. civ."]
                }
            ]
        }
    },
    {
        "code": "rc_magasin_01",
        "title": "Chute dans un supermarché - Mme Bernard",
        "theme": "responsabilité",
        "difficulty": "medium",
        "branch": "droit civil",
        "content": {
            "mail": {
                "subject": "Nouveau dossier - Accident corporel Carrefour",
                "from": "Pierre Martin <p.martin@cabinet-avocats.fr>",
                "body": """Salut,

On a un nouveau dossier. Mme Bernard, 58 ans, a fait une chute dans le Carrefour Market de Villeurbanne le 12 février.

Elle a glissé sur une flaque d'huile dans le rayon épicerie. Résultat : fracture du poignet gauche + traumatisme crânien léger. 3 semaines d'ITT.

Elle veut attaquer le magasin. J'ai rassemblé les pièces.

Pierre"""
            },
            "attachment": """RAPPORT D'ACCIDENT - CARREFOUR MARKET VILLEURBANNE

Date : 12 février 2026, 15h30
Lieu : Rayon épicerie, allée centrale

Victime : Mme Jeanne BERNARD, née le 03/05/1968

Circonstances déclarées :
"Je faisais mes courses. En passant dans le rayon épicerie, j'ai glissé et je suis tombée en arrière. Quand je me suis relevée, j'ai vu une flaque d'huile et une bouteille cassée."

Témoins :
- M. Jean PETIT (client) : "J'ai vu la dame tomber. Il y avait de l'huile par terre. Pas de panneau 'sol glissant'."
- Mme Sophie DURAND (employée) : "J'ai entendu la chute. La bouteille était cassée. Je ne sais pas depuis combien de temps."

Constatations du responsable :
- Flaque d'huile ~40cm de diamètre
- Bouteille d'huile 1L cassée
- Pas de panneau de signalisation
- Dernier passage agent d'entretien : 14h00

---
CERTIFICAT MÉDICAL (Urgences HEH - 12/02/2026)
- Fracture poignet gauche (radius distal)
- Traumatisme crânien léger
- ITT : 21 jours

---
COURRIER CARREFOUR (28 février 2026)
"Notre assureur considère que votre responsabilité est partiellement engagée. Proposition d'indemnisation à 50%." """,
            "phases": [
                {
                    "question": "Sur quel fondement juridique principal Mme Bernard peut-elle engager la responsabilité de Carrefour ?",
                    "choices": [
                        "Art. 1240 C. civ. - Responsabilité pour faute prouvée",
                        "Art. 1242 al. 1 C. civ. - Responsabilité du fait des choses",
                        "Art. 1245 et s. C. civ. - Responsabilité du fait des produits défectueux",
                        "Art. 1241 C. civ. - Responsabilité pour négligence"
                    ],
                    "correct": 1,
                    "debrief": "Le fondement optimal est l'article 1242 al. 1 (responsabilité du fait des choses). La flaque d'huile est une 'chose' dont Carrefour avait la garde. Ce régime dispense la victime de prouver une faute.",
                    "refs": ["Art. 1242 al. 1 C. civ.", "Cass. civ. 2e, 5 janv. 1956, Oxygène liquide"]
                },
                {
                    "question": "Carrefour peut-il s'exonérer en invoquant qu'un client a cassé la bouteille ?",
                    "choices": [
                        "Oui, c'est un fait d'un tiers exonératoire",
                        "Oui, car Carrefour n'a pas créé le danger",
                        "Non, sauf à prouver que ce fait présente les caractères de la force majeure",
                        "Non, car le gardien est toujours responsable sans exonération possible"
                    ],
                    "correct": 2,
                    "debrief": "Le fait d'un tiers n'est exonératoire que s'il présente les caractères de la force majeure. La casse d'une bouteille par un client est prévisible dans un supermarché. Carrefour doit anticiper ce risque.",
                    "refs": ["Art. 1242 al. 1 C. civ.", "Cass. civ. 2e, 15 déc. 2011"]
                },
                {
                    "question": "L'argument de Carrefour sur le 'défaut de vigilance' de Mme Bernard peut-il prospérer ?",
                    "choices": [
                        "Oui, la victime doit toujours regarder où elle marche",
                        "Oui, mais seulement pour une exonération partielle",
                        "Non, car une flaque d'huile transparente n'est pas visible",
                        "Non, sauf si la faute de la victime est prouvée (exonération partielle possible)"
                    ],
                    "correct": 3,
                    "debrief": "La faute de la victime peut exonérer partiellement si elle est prouvée. Mais ici, l'huile est transparente et aucun panneau ne signalait le danger. L'argument de Carrefour est faible.",
                    "refs": ["Cass. civ. 2e, 13 juill. 2006"]
                },
                {
                    "question": "Quels postes de préjudice Mme Bernard peut-elle réclamer ?",
                    "choices": [
                        "Uniquement les frais médicaux",
                        "Frais médicaux + perte de revenus pendant l'ITT",
                        "Tous les postes de la nomenclature Dintilhac applicables",
                        "Un forfait légal de 5 000€ pour les accidents de la vie courante"
                    ],
                    "correct": 2,
                    "debrief": "Mme Bernard peut réclamer tous ses préjudices selon la nomenclature Dintilhac : frais médicaux, perte de revenus, déficit fonctionnel temporaire, souffrances endurées, etc. Pas de forfait légal.",
                    "refs": ["Nomenclature Dintilhac", "Principe de réparation intégrale"]
                },
                {
                    "question": "Quelle stratégie recommandez-vous face à la proposition de 50% ?",
                    "choices": [
                        "Accepter car un procès est long et incertain",
                        "Refuser et exiger 100% avec expertise médicale préalable",
                        "Saisir directement le tribunal",
                        "Déposer plainte au pénal pour blessures involontaires"
                    ],
                    "correct": 1,
                    "debrief": "La stratégie optimale : refuser les 50% (juridiquement infondés), faire réaliser une expertise médicale pour évaluer les préjudices, négocier sur la base de 100%. Si refus, alors tribunal.",
                    "refs": ["Art. 1242 al. 1 C. civ."]
                }
            ]
        }
    },
    {
        "code": "societes_conflit_01",
        "title": "Révocation abusive du dirigeant - SARL InnoTech",
        "theme": "sociétés",
        "difficulty": "hard",
        "branch": "droit des affaires",
        "content": {
            "mail": {
                "subject": "Dossier Moreau c/ SARL InnoTech - Révocation gérant",
                "from": "Claire Leroy <c.leroy@cabinet-avocats.fr>",
                "body": """Hello,

Dossier complexe. M. Moreau était gérant et associé minoritaire (20%) de la SARL InnoTech. Il vient d'être révoqué lors d'une AGE convoquée à la va-vite.

Il conteste les conditions de la révocation et veut des dommages-intérêts.

Les associés majoritaires (80%) sont les frères Dupuis qui veulent le pousser dehors.

Claire"""
            },
            "attachment": """EXTRAIT K-BIS SARL INNOTECH
RCS Lyon 823 456 789 | Capital : 50 000€
Gérant : M. Thomas MOREAU (jusqu'au 01/03/2026)

Associés :
- Thomas MOREAU : 200 parts (20%)
- Jean DUPUIS : 400 parts (40%)
- Paul DUPUIS : 400 parts (40%)

---
STATUTS (extraits)

Art. 12 - Gérance
Le gérant peut être révoqué par décision des associés représentant plus de la moitié des parts. La révocation sans juste motif peut donner lieu à dommages-intérêts.

Art. 15 - Assemblées
Convocation par LRAR 15 jours au moins avant l'AG.

---
CONVOCATION AGE (reçue le 24 février 2026)
Date : 1er mars 2026, 10h00
Ordre du jour : Révocation de M. Moreau

---
PV AGE DU 1ER MARS 2026
Présents : Jean DUPUIS (400), Paul DUPUIS (400)
Absent : Thomas MOREAU (convocation reçue le 25 février)

Résolution 1 : Révocation de M. Moreau avec effet immédiat.
Motif : perte de confiance et divergences stratégiques.
Vote : 800 parts POUR. Adoptée.

---
EMAIL DE M. MOREAU (2 mars 2026)
"J'ai reçu la convocation le 25 février pour une AG le 1er mars ! J'étais en déplacement à l'étranger (connu des Dupuis). On ne m'a jamais rien reproché avant. La société va bien (+30% CA). Les Dupuis veulent revendre à un concurrent, je m'y suis toujours opposé." """,
            "phases": [
                {
                    "question": "La convocation à l'AGE respecte-t-elle les formes légales et statutaires ?",
                    "choices": [
                        "Oui, le délai de 15 jours a été respecté",
                        "Non, le délai se compte à partir de la réception (25 fév → 1er mars = 4 jours)",
                        "Non, car la convocation aurait dû être faite par le gérant",
                        "Oui, car 15 jours et non 15 jours francs"
                    ],
                    "correct": 1,
                    "debrief": "Le délai se décompte à partir de la réception. Moreau a reçu le 25 février pour une AG le 1er mars = 4 jours seulement. Les statuts exigent 15 jours minimum. Convocation irrégulière.",
                    "refs": ["Art. L223-27 C. com.", "Art. R223-20 C. com."]
                },
                {
                    "question": "L'irrégularité de la convocation peut-elle entraîner la nullité des délibérations ?",
                    "choices": [
                        "Non, simple formalité sans conséquence",
                        "Oui, si M. Moreau prouve un grief",
                        "Oui, automatiquement car délai d'ordre public",
                        "Non, car Moreau n'avait pas la majorité de toute façon"
                    ],
                    "correct": 1,
                    "debrief": "La nullité pour vice de forme n'est pas automatique. Il faut démontrer un grief (art. L235-1). Ici, le grief est évident : Moreau n'a pas pu assister à l'AG qui a décidé de sa révocation.",
                    "refs": ["Art. L235-1 C. com.", "Cass. com., 9 juill. 2013"]
                },
                {
                    "question": "Sur le fond, la révocation de M. Moreau est-elle valable ?",
                    "choices": [
                        "Oui, le gérant de SARL est révocable ad nutum",
                        "Oui, les majoritaires ont le droit de révoquer",
                        "Non, la révocation sans juste motif est nulle",
                        "Non, l'absence de juste motif ouvre droit à DI mais la révocation reste valable"
                    ],
                    "correct": 3,
                    "debrief": "En SARL, le gérant est révocable par les associés (art. L223-25). Si la révocation intervient sans juste motif, elle ouvre droit à dommages-intérêts MAIS reste valable. Elle n'est pas nulle.",
                    "refs": ["Art. L223-25 C. com.", "Cass. com., 14 mai 2013"]
                },
                {
                    "question": "Le motif 'perte de confiance et divergences stratégiques' est-il un juste motif ?",
                    "choices": [
                        "Oui, la perte de confiance suffit toujours",
                        "Non, il faut une faute ou une inaptitude du gérant",
                        "Non, c'est un prétexte pour l'évincer",
                        "Cela dépend de l'appréciation du juge"
                    ],
                    "correct": 1,
                    "debrief": "Le juste motif suppose une faute de gestion, une inaptitude, ou un comportement rendant impossible le maintien. La simple 'perte de confiance' sans faits précis ne suffit pas. L'absence de reproche antérieur et la bonne santé de la société suggèrent une révocation sans juste motif.",
                    "refs": ["Art. L223-25 al. 2 C. com.", "Cass. com., 4 mai 2010"]
                },
                {
                    "question": "Quelle stratégie contentieuse recommandez-vous à M. Moreau ?",
                    "choices": [
                        "Action en nullité de l'AGE + action en DI pour révocation abusive",
                        "Référé pour suspendre la révocation et réintégration",
                        "Action en dissolution pour mésentente",
                        "Uniquement DI sans contester la révocation"
                    ],
                    "correct": 0,
                    "debrief": "Stratégie optimale : (1) nullité de l'AGE pour irrégularité de convocation (grief démontré), (2) subsidiairement, DI pour révocation sans juste motif. Le référé n'est pas adapté. La dissolution est prématurée.",
                    "refs": ["Art. L235-1 C. com.", "Art. L223-25 C. com."]
                }
            ]
        }
    }
]


# ============================================================
# GÉNÉRATION SUPPORT 1 (IA)
# ============================================================

def _generate_dossier_ia(
    difficulty: str,
    theme: str | None,
    openai_client: OpenAI | None = None
) -> dict | None:
    """Génère un dossier complet via OpenAI. Retourne None si échec."""
    if openai_client is None:
        try:
            openai_client = OpenAI()
        except Exception as e:
            logger.error(f"[CAB] OpenAI init failed: {e}")
            return None

    chosen_theme = theme or random.choice(THEMES)
    difficulty_desc = DIFFICULTY_PROMPTS.get(difficulty, DIFFICULTY_PROMPTS["medium"])

    system_prompt = """Tu es un expert en droit français. Tu crées des cas pratiques réalistes pour des étudiants en droit.

CONSIGNES :
1. Génère un dossier de simulation "cabinet d'avocat" avec un email, une pièce jointe et 5 questions QCM
2. Chaque question a 4 choix dont UN SEUL est correct (index 0-3)
3. Les références juridiques doivent être exactes (articles, jurisprudences réelles)
4. Le debrief explique pourquoi la bonne réponse est correcte

FORMAT JSON STRICT :
{
  "mail": {"subject": "...", "from": "Prénom Nom <email>", "body": "..."},
  "attachment": "...",
  "phases": [
    {"question": "...", "choices": ["A", "B", "C", "D"], "correct": 0, "debrief": "...", "refs": ["Art. ..."]}
  ],
  "meta": {"theme": "...", "difficulty": "...", "branch": "..."}
}"""

    user_prompt = f"""Génère un dossier NavireCab :
THÈME : {chosen_theme}
DIFFICULTÉ : {difficulty} ({difficulty_desc})
PHASES : 5 questions

JSON uniquement, sans markdown."""

    try:
        response = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=OPENAI_MAX_TOKENS,
            temperature=0.7,
            timeout=GENERATION_TIMEOUT
        )

        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        dossier = json.loads(raw)
        if _validate_dossier(dossier):
            logger.info(f"[CAB] IA generation success: theme={chosen_theme}")
            return dossier
        else:
            logger.warning("[CAB] IA dossier invalid")
            return None

    except Exception as e:
        logger.error(f"[CAB] IA generation failed: {e}")
        return None


# ============================================================
# GÉNÉRATION SUPPORT 2 (Templates)
# ============================================================

def _get_template_from_db(db: Session, difficulty: str) -> dict | None:
    """Récupère un template depuis la DB."""
    templates = db.query(CabDossierTemplate).filter(
        CabDossierTemplate.is_active == True,
        CabDossierTemplate.difficulty == difficulty
    ).all()

    if not templates:
        # Fallback sur tous les templates actifs
        templates = db.query(CabDossierTemplate).filter(
            CabDossierTemplate.is_active == True
        ).all()

    if not templates:
        return None

    template = random.choice(templates)

    # Incrémenter times_used
    template.times_used += 1
    db.commit()

    dossier = template.content_json.copy()
    dossier["meta"] = {
        "theme": template.theme,
        "difficulty": template.difficulty,
        "branch": template.branch,
        "template_code": template.code
    }
    return dossier


def _get_template_fallback(difficulty: str) -> dict | None:
    """Fallback sur les templates en mémoire si DB vide."""
    matching = [t for t in FALLBACK_TEMPLATES if t["difficulty"] == difficulty]
    if not matching:
        matching = FALLBACK_TEMPLATES

    if not matching:
        return None

    template = random.choice(matching)
    dossier = template["content"].copy()
    dossier["meta"] = {
        "theme": template["theme"],
        "difficulty": template["difficulty"],
        "branch": template.get("branch", ""),
        "template_code": template["code"]
    }
    return dossier


# ============================================================
# ORCHESTRATEUR PRINCIPAL
# ============================================================

def generate_dossier(
    db: Session,
    support_type: int = 2,
    difficulty: str = "medium",
    theme: str | None = None,
) -> tuple[dict, int]:
    """
    Génère un dossier NavireCab.

    Args:
        db: Session SQLAlchemy
        support_type: 1 = IA, 2 = Template
        difficulty: easy | medium | hard
        theme: thème optionnel (ignoré en Support 2)

    Returns:
        (dossier_dict, actual_support_type)
    """
    actual_support = support_type

    # Support 1 : IA
    if support_type == 1:
        dossier = _generate_dossier_ia(difficulty, theme)
        if dossier:
            return dossier, 1
        logger.warning("[CAB] IA failed, fallback to Support 2")
        actual_support = 2

    # Support 2 : Template DB
    dossier = _get_template_from_db(db, difficulty)
    if dossier:
        return dossier, actual_support

    # Ultime fallback : templates en mémoire
    dossier = _get_template_fallback(difficulty)
    if dossier:
        logger.warning("[CAB] Using in-memory fallback template")
        return dossier, actual_support

    raise ValueError("No dossier available")


# ============================================================
# VALIDATION
# ============================================================

def _validate_dossier(dossier: dict) -> bool:
    """Vérifie la structure minimale d'un dossier."""
    try:
        if "mail" not in dossier:
            return False
        if not all(k in dossier["mail"] for k in ["subject", "from", "body"]):
            return False
        if "attachment" not in dossier or not dossier["attachment"]:
            return False
        if "phases" not in dossier or len(dossier["phases"]) < 3:
            return False

        for phase in dossier["phases"]:
            if not all(k in phase for k in ["question", "choices", "correct", "debrief"]):
                return False
            if len(phase["choices"]) != 4:
                return False
            if not isinstance(phase["correct"], int) or phase["correct"] not in range(4):
                return False

        return True
    except Exception:
        return False


# ============================================================
# SCORING
# ============================================================

def calculate_phase_score(
    phase: dict,
    user_choice: int,
    user_ref: str | None = None
) -> dict:
    """
    Calcule le score d'une phase.

    Barème :
    - Bonne réponse : 4 points
    - Mauvaise réponse : 0 points
    - Bonus référence correcte : +1 point
    """
    correct = user_choice == phase["correct"]
    base_points = 4 if correct else 0

    ref_bonus = False
    if user_ref and correct:
        expected_refs = phase.get("refs", [])
        user_ref_clean = user_ref.strip().lower()
        for ref in expected_refs:
            if user_ref_clean in ref.lower() or ref.lower() in user_ref_clean:
                ref_bonus = True
                break

    return {
        "points": base_points + (1 if ref_bonus else 0),
        "correct": correct,
        "ref_bonus": ref_bonus,
        "expected": phase["correct"],
        "given": user_choice
    }


def calculate_final_score(answers: list[dict], num_phases: int = 5) -> dict:
    """
    Calcule la note finale /20.

    Max théorique : 5 × (4 + 1) = 25 points → normalisé sur 20
    """
    raw_score = sum(a.get("points", 0) for a in answers)
    correct_count = sum(1 for a in answers if a.get("correct", False))
    ref_bonus_count = sum(1 for a in answers if a.get("ref_bonus", False))

    max_possible = num_phases * 5
    score_20 = round((raw_score / max_possible) * 20, 1) if max_possible > 0 else 0

    if score_20 < 8:
        mention = "insuffisant"
    elif score_20 < 10:
        mention = "fragile"
    elif score_20 < 12:
        mention = "passable"
    elif score_20 < 14:
        mention = "assez bien"
    elif score_20 < 16:
        mention = "bien"
    elif score_20 < 18:
        mention = "très bien"
    else:
        mention = "excellent"

    return {
        "raw_score": raw_score,
        "max_possible": max_possible,
        "score_20": score_20,
        "mention": mention,
        "correct_count": correct_count,
        "ref_bonus_count": ref_bonus_count
    }