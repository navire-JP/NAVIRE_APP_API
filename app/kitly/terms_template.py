"""
Modèle de CGV proposé au freelance (« template-v1 »), pré-rempli à partir de
son identité de vendeur et des types d'offres qu'il vend. Le freelance relit,
modifie puis publie : il reste seul responsable de ses CGV (avertissement
TERMS_DISCLAIMER affiché avec chaque version). Modèle à faire valider par un
professionnel du droit avant d'en faire une promesse commerciale.
"""

from __future__ import annotations

from app.kitly.models import KitlySellerLegalProfile

TEMPLATE_VERSION = "template-v1"
TODO = "[à compléter]"


def _identity(lp: KitlySellerLegalProfile | None, display_name: str) -> tuple[str, str]:
    if lp is None:
        return display_name or TODO, f"{display_name or TODO}, {TODO} (adresse), {TODO} (email)"
    name = lp.trade_name or lp.legal_name or display_name or TODO
    parts = [lp.legal_name or name]
    if lp.legal_form:
        parts.append(lp.legal_form)
    if lp.is_professional:
        parts.append(f"SIREN {lp.siren}" if lp.siren else f"SIREN {TODO}")
        if lp.rcs_rm:
            parts.append(lp.rcs_rm)
        if lp.vat_number and not lp.vat_exempt:
            parts.append(f"TVA {lp.vat_number}")
    address = ", ".join(x for x in [lp.address_line, f"{lp.postal_code} {lp.city}".strip()] if x) or f"{TODO} (adresse)"
    parts.append(address)
    parts.append(lp.professional_email or f"{TODO} (email)")
    if lp.phone:
        parts.append(lp.phone)
    return name, ", ".join(parts)


def build_terms(lp: KitlySellerLegalProfile | None, display_name: str, kinds: set[str]) -> list[dict]:
    name, identity = _identity(lp, display_name)
    pro = lp.is_professional if lp else True
    physical, digital = "physical" in kinds, "digital" in kinds
    service = bool(kinds & {"service", "appointment"})
    vat = ("Prix en euros, TVA non applicable (article 293 B du Code général des impôts)."
           if (lp.vat_exempt if lp else True) else "Prix en euros toutes taxes comprises (TTC).")

    a: list[dict] = []
    a.append({"key": "objet", "title": "Article 1 — Objet et identité du vendeur", "body": (
        f"Les présentes conditions générales de vente (CGV) s'appliquent aux ventes conclues entre {name} "
        f"(ci-après « le Vendeur ») et toute personne achetant depuis sa vitrine Kitly (ci-après « le Client »).\n\n"
        f"Vendeur : {identity}.\n\n"
        "Kitly fournit la vitrine en ligne et le service de paiement sécurisé ; il n'est pas partie au contrat de vente."
        + ("" if pro else "\n\nLe Vendeur agit en tant que particulier, non en tant que professionnel."))})
    a.append({"key": "prix", "title": "Article 2 — Offres et prix", "body": (
        "Les caractéristiques essentielles de chaque produit ou prestation sont présentées sur sa fiche. "
        f"{vat} Les frais de livraison éventuels sont indiqués avant la validation de la commande. "
        "Le prix applicable est celui affiché au moment de la commande.")})
    a.append({"key": "commande", "title": "Article 3 — Commande et paiement", "body": (
        "Le Client choisit son offre, ses options et, le cas échéant, son mode de livraison, puis accepte les présentes CGV "
        "avant de payer. Le paiement s'effectue en ligne par carte bancaire, via le prestataire de paiement sécurisé Stripe ; "
        "il est exigible à la commande. La vente est conclue à la confirmation du paiement, dont le Client reçoit un email "
        "récapitulatif. Les présentes CGV, dans la version acceptée, restent consultables depuis la vitrine.")})
    if physical:
        a.append({"key": "livraison", "title": "Article 4 — Livraison", "body": (
            "Les produits sont livrés à l'adresse indiquée par le Client lors du paiement, ou remis en retrait sur place si cette "
            "option est proposée et choisie. Le délai de livraison est celui indiqué sur la fiche du produit ; à défaut, la "
            "livraison intervient au plus tard 30 jours après la commande. En cas de retard, le Client peut enjoindre le Vendeur "
            "de livrer dans un délai supplémentaire raisonnable puis, à défaut, résoudre la vente (articles L216-1 et suivants du "
            "Code de la consommation). Le risque de perte ou d'endommagement est transféré au Client à la réception des produits.")})
    if digital:
        a.append({"key": "numerique", "title": "Article 5 — Contenus numériques", "body": (
            "Les contenus numériques sont fournis après confirmation du paiement, par un lien de téléchargement ou d'accès envoyé "
            "par email, selon les modalités indiquées sur la fiche (format, durée d'accès, licence d'utilisation). Le Client "
            "s'engage à respecter la licence indiquée et à ne pas redistribuer le contenu.")})
    if service:
        a.append({"key": "prestations", "title": "Article 6 — Prestations de services", "body": (
            "Les prestations sont exécutées selon la description, le délai et les modalités (à distance ou en présentiel) indiqués "
            "sur leur fiche. Le Vendeur contacte le Client après la commande pour convenir de leur organisation. Le Client fournit "
            "en temps utile les informations et éléments nécessaires à leur réalisation.")})
    if pro:
        retract = [
            "Le Client consommateur dispose d'un délai de 14 jours pour exercer son droit de rétractation, sans avoir à motiver "
            "sa décision (articles L221-18 et suivants du Code de la consommation)."
        ]
        if physical:
            retract.append("Pour un produit, le délai court à compter de sa réception. Le Client renvoie le produit au plus tard "
                           "14 jours après avoir informé le Vendeur de sa décision ; les frais de retour sont à sa charge.")
        if service:
            retract.append("Pour une prestation, le délai court à compter de la commande. Si le Client a expressément demandé que "
                           "la prestation commence avant la fin de ce délai, il paie la part déjà réalisée en cas de rétractation ; "
                           "une prestation pleinement exécutée ne peut plus faire l'objet d'une rétractation.")
        if digital:
            retract.append("Pour un contenu numérique fourni immédiatement, le Client renonce à son droit de rétractation en "
                           "donnant son accord exprès lors de la commande (article L221-28, 13° du Code de la consommation).")
        retract.append("Les biens confectionnés selon les spécifications du Client ou nettement personnalisés sont exclus du "
                       "droit de rétractation. Pour se rétracter, le Client adresse au Vendeur une déclaration dénuée "
                       "d'ambiguïté (par exemple par email, aux coordonnées de l'article 1). Le Vendeur rembourse la totalité "
                       "des sommes versées, frais de livraison initiaux inclus, au plus tard 14 jours après avoir été informé de "
                       "la décision du Client" + (" (il peut différer le remboursement jusqu'à réception du produit retourné)" if physical else "")
                       + ", par le même moyen de paiement.")
        a.append({"key": "retractation", "title": "Article 7 — Droit de rétractation", "body": "\n\n".join(retract)})
        a.append({"key": "garanties", "title": "Article 8 — Garanties légales", "body": (
            "Le Vendeur est tenu de la garantie légale de conformité (articles L217-3 et suivants du Code de la consommation), "
            "qui permet au Client d'obtenir la mise en conformité du bien ou du contenu numérique, ou à défaut une réduction du prix "
            "ou la résolution de la vente, pendant 2 ans à compter de la délivrance, et de la garantie des vices cachés "
            "(articles 1641 et suivants du Code civil).")})
    else:
        a.append({"key": "garanties", "title": "Article 7 — Garanties", "body": (
            "Le Vendeur, particulier, est tenu de la garantie des vices cachés (articles 1641 et suivants du Code civil). Les "
            "dispositions du Code de la consommation propres aux ventes conclues avec un professionnel ne s'appliquent pas.")})
    a.append({"key": "donnees", "title": "Article 9 — Données personnelles", "body": (
        "Les données du Client (identité, coordonnées, adresse de livraison) sont traitées par le Vendeur pour exécuter la "
        "commande et répondre à ses demandes, puis conservées le temps nécessaire aux obligations légales. Le Client peut "
        "exercer ses droits d'accès, de rectification et d'effacement en écrivant au Vendeur. Le paiement est traité par Stripe ; "
        "le Vendeur n'a jamais accès aux données de carte bancaire.")})
    if pro:
        mediator = (f"{lp.mediator_name}" + (f" ({lp.mediator_url})" if lp.mediator_url else "")) if lp and lp.mediator_name else TODO
        a.append({"key": "litiges", "title": "Article 10 — Réclamations, médiation et droit applicable", "body": (
            "Toute réclamation est à adresser en priorité au Vendeur, aux coordonnées de l'article 1. À défaut de solution amiable, "
            "le Client consommateur peut recourir gratuitement au médiateur de la consommation suivant : "
            f"{mediator} (articles L612-1 et suivants du Code de la consommation). Les présentes CGV sont soumises au droit "
            "français ; à défaut d'accord, les tribunaux compétents sont ceux désignés par la loi.")})
    else:
        a.append({"key": "litiges", "title": "Article 10 — Litiges et droit applicable", "body": (
            "Toute réclamation est à adresser en priorité au Vendeur. Les présentes CGV sont soumises au droit français ; à défaut "
            "d'accord amiable, les tribunaux compétents sont ceux désignés par la loi.")})
    return a


def missing_fields(lp: KitlySellerLegalProfile | None) -> list[str]:
    """Informations du vendeur à compléter avant de publier (signalées dans le builder)."""
    if lp is None:
        return ["legal_name", "address_line", "professional_email"]
    need = [k for k in ("legal_name", "address_line", "postal_code", "city", "professional_email") if not getattr(lp, k)]
    if lp.is_professional:
        need += [k for k in ("siren", "mediator_name") if not getattr(lp, k)]
    return need
