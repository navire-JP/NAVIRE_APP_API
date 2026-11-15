"""
Questionnaire juridique vendeur (v1) et garde-fous des droits impératifs.

Le questionnaire est servi au front (GET /kitly/legal/questionnaire) : les
questions dépendent du type d'offre. Les réponses sont validées ici AVANT
enregistrement : un vendeur qui vend à des consommateurs ne peut pas choisir
une option contraire au Code de la consommation (ex. « aucun retour » sur un
produit physique standard).

⚠️ Les questions et les règles ci-dessous sont un socle technique. Elles
doivent être relues par un juriste avant l'ouverture du générateur de CGV au
public (cf. cahier des charges, partie VIII).

Références principales :
  - L. 221-18 : droit de rétractation de 14 jours (vente à distance) ;
  - L. 221-24 : remboursement sous 14 jours après rétractation ;
  - L. 221-25 : exécution d'un service pendant le délai, sur demande expresse,
                montant proportionnel dû en cas de rétractation ;
  - L. 221-28 : exceptions (1° service pleinement exécuté avec renonciation,
                3° biens personnalisés, 13° contenu numérique avec accord
                exprès et renoncement) ;
  - L. 216-1 : livraison au plus tard 30 jours sans date convenue ;
  - L. 216-4 : risque de perte transféré au consommateur à la prise de
               possession physique — le colis perdu reste à la charge du vendeur ;
  - L. 612-1 : médiateur de la consommation obligatoire.
"""

from __future__ import annotations

QUESTIONNAIRE_VERSION = "v1"
SCOPES = ("general", "physical", "digital", "service", "appointment")


def _q(key, label, type_, **kw) -> dict:
    return {"key": key, "label": label, "type": type_, **kw}


QUESTIONS: dict[str, list[dict]] = {
    "general": [
        _q("sells_to_consumers", "Vends-tu à des particuliers (consommateurs) ?", "bool", default=True,
           help="Si oui, les règles protectrices du Code de la consommation s'appliquent."),
        _q("payment_timing", "Quand le client paie-t-il ?", "choice",
           choices=["at_order", "deposit", "after_delivery"], default="at_order"),
        _q("deposit_percent", "Pourcentage d'acompte", "int", min=1, max=99, show_if={"payment_timing": "deposit"}),
        _q("payment_methods", "Moyens de paiement acceptés", "multi",
           choices=["card", "bank_transfer", "paypal", "cash"], default=["card"]),
        _q("mediator_name", "Nom de ton médiateur de la consommation", "text",
           help="Obligatoire pour vendre à des consommateurs (art. L. 612-1)."),
        _q("mediator_url", "Site du médiateur", "text"),
    ],
    "physical": [
        _q("delivery_zones", "Dans quelles zones livres-tu ?", "multi",
           choices=["fr_metro", "fr_dom_tom", "eu", "world"], default=["fr_metro"]),
        _q("delivery_min_days", "Délai minimal de livraison (jours ouvrés)", "int", min=0, max=365),
        _q("delivery_max_days", "Délai maximal de livraison (jours ouvrés)", "int", min=0, max=365),
        _q("carriers", "Transporteur(s) utilisé(s)", "text"),
        _q("shipping_included", "Les frais de livraison sont-ils inclus dans le prix ?", "bool", default=False),
        _q("pickup_available", "Proposes-tu un retrait en main propre ?", "bool", default=False),
        _q("lost_parcel_risk", "Qui supporte un colis perdu ou endommagé pendant le transport ?", "choice",
           choices=["seller", "buyer"], default="seller"),
        _q("custom_made_only", "Tous tes produits sont-ils fabriqués sur mesure / personnalisés ?", "bool", default=False,
           help="Seule exception courante au droit de rétractation pour les biens (art. L. 221-28, 3°)."),
        _q("returns_accepted", "Acceptes-tu les retours ?", "bool", default=True),
        _q("retraction_days", "Délai de rétractation (jours)", "int", min=0, max=365, default=14),
        _q("return_costs_by", "Qui paie les frais de retour ?", "choice", choices=["buyer", "seller"], default="buyer"),
        _q("return_conditions", "Dans quel état le produit doit-il être retourné ?", "text"),
        _q("refund_delay_days", "Sous combien de jours rembourses-tu ?", "int", min=0, max=60, default=14),
        _q("exchange_offered", "Proposes-tu un échange ?", "bool", default=False),
        _q("condition", "Produits neufs ou d'occasion ?", "choice", choices=["new", "used", "both"], default="new"),
        _q("commercial_warranty_months", "Garantie commerciale supplémentaire (mois, 0 = aucune)", "int",
           min=0, max=120, default=0),
        _q("safety_info", "Informations de sécurité ou d'utilisation particulières", "text"),
    ],
    "digital": [
        _q("delivery_mode", "Comment le client reçoit-il le produit ?", "choice",
           choices=["download", "email_link", "account_access"], default="download"),
        _q("immediate_access", "Le produit est-il accessible immédiatement après paiement ?", "bool", default=True),
        _q("access_duration_days", "Durée d'accès en jours (vide = illimitée)", "int", min=1, max=3650),
        _q("requires_account", "L'accès nécessite-t-il un compte ?", "bool", default=False),
        _q("license", "Licence / droits d'utilisation accordés", "text"),
        _q("waive_withdrawal", "Souhaites-tu que le client renonce à son droit de rétractation pour un accès immédiat ?",
           "bool", default=True, help="Possible uniquement avec son accord exprès ET sa reconnaissance de la perte du droit "
                                      "(art. L. 221-28, 13°). Kitly affichera les cases à cocher au paiement."),
        _q("require_express_consent", "Le client devra cocher son accord exprès pour une exécution immédiate", "bool",
           default=True, show_if={"waive_withdrawal": True}),
        _q("require_waiver_ack", "Le client devra reconnaître perdre son droit de rétractation", "bool",
           default=True, show_if={"waive_withdrawal": True}),
    ],
    "service": [
        _q("delay_min_days", "Délai minimum de réalisation (jours)", "int", min=0, max=730),
        _q("delay_max_days", "Délai maximum de réalisation (jours)", "int", min=0, max=730),
        _q("location_mode", "Prestation à distance ou en présentiel ?", "choice",
           choices=["remote", "onsite", "both"], default="remote"),
        _q("zones", "Zones d'intervention (si présentiel)", "text"),
        _q("deliverables", "Livrables inclus", "text"),
        _q("revisions_included", "Nombre de modifications comprises", "int", min=0, max=100, default=2),
        _q("extra_fees", "Des frais supplémentaires peuvent-ils être facturés ?", "bool", default=False),
        _q("extra_fees_description", "Lesquels ?", "text", show_if={"extra_fees": True}),
        _q("validation_process", "Comment se déroule la validation finale ?", "text"),
        _q("cancellation_allowed", "Le client peut-il annuler (hors droit de rétractation) ?", "bool", default=True),
        _q("cancellation_notice_days", "Jusqu'à combien de jours avant le début ?", "int", min=0, max=365,
           show_if={"cancellation_allowed": True}),
        _q("started_work_policy", "En cas d'annulation après le début du travail, que factures-tu ?", "choice",
           choices=["proportional", "nothing", "deposit_kept"], default="proportional"),
        _q("early_start", "La prestation peut-elle commencer avant la fin du délai de rétractation de 14 jours ?",
           "bool", default=True),
        _q("require_express_request", "Le client devra demander expressément ce commencement anticipé", "bool",
           default=True, show_if={"early_start": True}),
    ],
    "appointment": [
        _q("cancellation_notice_hours", "Délai d'annulation sans frais (heures avant le rendez-vous)", "int",
           min=0, max=720, default=24),
        _q("reschedule_allowed", "Le client peut-il reporter ?", "bool", default=True),
        _q("late_cancellation_policy", "Annulation tardive par le client", "choice",
           choices=["full_refund", "partial_refund", "no_refund"], default="no_refund"),
        _q("no_show_policy", "En cas d'absence du client", "choice",
           choices=["full_refund", "partial_refund", "no_refund"], default="no_refund"),
        _q("seller_cancellation", "Si tu dois annuler toi-même", "choice",
           choices=["full_refund", "reschedule_or_refund"], default="reschedule_or_refund"),
        _q("payment_timing", "Paiement à la réservation ou après le rendez-vous ?", "choice",
           choices=["at_booking", "after"], default="at_booking"),
        _q("early_start", "Un rendez-vous peut-il avoir lieu moins de 14 jours après la réservation ?", "bool",
           default=True),
        _q("require_express_request", "Le client devra demander expressément l'exécution avant la fin du délai",
           "bool", default=True, show_if={"early_start": True}),
    ],
}


def _coerce(question: dict, value):
    """Convertit une réponse au type attendu ; lève ValueError sinon."""
    if value is None or value == "":
        return None
    t = question["type"]
    if t == "bool":
        if isinstance(value, bool):
            return value
        raise ValueError("Réponse oui / non attendue.")
    if t == "int":
        if isinstance(value, bool):
            raise ValueError("Nombre attendu.")
        v = int(value)
        if "min" in question and v < question["min"]:
            raise ValueError(f"Minimum : {question['min']}.")
        if "max" in question and v > question["max"]:
            raise ValueError(f"Maximum : {question['max']}.")
        return v
    if t == "choice":
        if value not in question["choices"]:
            raise ValueError("Choix invalide.")
        return value
    if t == "multi":
        if not isinstance(value, list) or any(v not in question["choices"] for v in value):
            raise ValueError("Choix invalide.")
        return list(dict.fromkeys(value))
    return str(value).strip()[:2000]


def validate_answers(scope: str, answers: dict, sells_to_consumers: bool = True) -> tuple[dict, dict, list[str]]:
    """
    Rend (réponses nettoyées, erreurs {clé: message}, avertissements).
    Les erreurs bloquent l'enregistrement ; les avertissements s'affichent.
    `sells_to_consumers` vient du périmètre « general » (vrai par défaut : le
    cas le plus protecteur).
    """
    if scope not in QUESTIONS:
        raise ValueError("Périmètre inconnu.")
    questions = {q["key"]: q for q in QUESTIONS[scope]}
    clean: dict = {}
    errors: dict[str, str] = {}
    warnings: list[str] = []

    for key, value in (answers or {}).items():
        q = questions.get(key)
        if q is None:
            continue  # clé inconnue : ignorée (questionnaire d'une autre version)
        try:
            v = _coerce(q, value)
        except (ValueError, TypeError) as exc:
            errors[key] = str(exc) or "Réponse invalide."
            continue
        if v is not None:
            clean[key] = v

    if scope == "general":
        sells_to_consumers = clean.get("sells_to_consumers", True)
    b2c = sells_to_consumers

    if scope == "general":
        if clean.get("payment_timing") == "deposit" and not clean.get("deposit_percent"):
            errors["deposit_percent"] = "Indique le pourcentage d'acompte."
        if b2c and not clean.get("mediator_name"):
            warnings.append("Pour vendre à des consommateurs, tu dois adhérer à un médiateur de la consommation "
                            "(art. L. 612-1) et l'indiquer dans tes CGV.")

    elif scope == "physical":
        lo, hi = clean.get("delivery_min_days"), clean.get("delivery_max_days")
        if lo is not None and hi is not None and hi < lo:
            errors["delivery_max_days"] = "Le délai maximal doit être supérieur ou égal au délai minimal."
        if hi is not None and hi > 30:
            warnings.append("Sans date convenue avec le client, la livraison doit intervenir sous 30 jours (art. L. 216-1).")
        if b2c:
            custom = clean.get("custom_made_only", False)
            if not custom:
                if clean.get("returns_accepted") is False:
                    errors["returns_accepted"] = ("Un consommateur dispose d'un droit de rétractation de 14 jours "
                                                  "(art. L. 221-18) : les retours ne peuvent pas être refusés, sauf "
                                                  "produits personnalisés.")
                if clean.get("retraction_days") is not None and clean["retraction_days"] < 14:
                    errors["retraction_days"] = "Minimum légal : 14 jours (art. L. 221-18). Tu peux proposer plus."
            if clean.get("refund_delay_days") is not None and clean["refund_delay_days"] > 14:
                errors["refund_delay_days"] = "Le remboursement doit intervenir sous 14 jours maximum (art. L. 221-24)."
            if clean.get("lost_parcel_risk") == "buyer":
                errors["lost_parcel_risk"] = ("Vis-à-vis d'un consommateur, le vendeur supporte la perte ou la casse "
                                              "jusqu'à la remise du colis (art. L. 216-4).")
        warnings.append("Les garanties légales de conformité (2 ans) et des vices cachés s'appliquent en plus de toute "
                        "garantie commerciale : elles figureront automatiquement dans tes CGV.")

    elif scope == "digital":
        if b2c and clean.get("waive_withdrawal"):
            for key in ("require_express_consent", "require_waiver_ack"):
                if clean.get(key) is False:
                    errors[key] = ("La renonciation au droit de rétractation n'est valable qu'avec l'accord exprès du "
                                   "client et sa reconnaissance de la perte de ce droit (art. L. 221-28, 13°).")
            clean.setdefault("require_express_consent", True)
            clean.setdefault("require_waiver_ack", True)

    elif scope in ("service", "appointment"):
        if scope == "service":
            lo, hi = clean.get("delay_min_days"), clean.get("delay_max_days")
            if lo is not None and hi is not None and hi < lo:
                errors["delay_max_days"] = "Le délai maximum doit être supérieur ou égal au délai minimum."
            if b2c and clean.get("started_work_policy") == "deposit_kept":
                warnings.append("Pendant le délai de rétractation, seul un montant proportionnel au travail déjà "
                                "réalisé peut être conservé (art. L. 221-25).")
        if scope == "appointment" and clean.get("seller_cancellation") not in (None, "full_refund", "reschedule_or_refund"):
            errors["seller_cancellation"] = "Si tu annules, le client doit être remboursé ou se voir proposer un report."
        if b2c and clean.get("early_start") and clean.get("require_express_request") is False:
            errors["require_express_request"] = ("Une exécution avant la fin du délai de rétractation exige une demande "
                                                 "expresse du client (art. L. 221-25).")
        if b2c and clean.get("early_start"):
            clean.setdefault("require_express_request", True)

    return clean, errors, warnings
