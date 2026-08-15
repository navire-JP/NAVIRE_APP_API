"""
app/core/navire_plans.py
=========================
Catalogue des abonnements NAVIRE (Membre / Membre+) et résolution des Prices
Stripe correspondants.

Pourquoi ce fichier
-------------------
Les Prices Prép'AdJuris sont figés en dur (app/core/prepa_adjuris_config.py),
mais ceux des abonnements NAVIRE ne vivaient que dans des variables
d'environnement vides par défaut : sans elles, /subscriptions/checkout
répondait 500 « Configuration Stripe manquante » et l'embed d'offres ne
pouvait générer aucun lien de paiement.

Ordre de résolution d'un Price, du plus explicite au plus automatique :

  1. Variable d'environnement (STRIPE_PRICE_MEMBRE_MONTHLY, …). Si elle est
     posée, elle gagne toujours — c'est le moyen d'épingler un Price créé à la
     main dans le dashboard.
  2. Cache mémoire du process (un Price ne change jamais après résolution).
  3. Recherche par lookup_key dans le compte Stripe. `Price.list` est
     fortement cohérent, contrairement à l'API Search : deux checkouts
     simultanés retrouvent bien le même Price.
  4. Création du Product + du Price au tarif du catalogue ci-dessous, avec ce
     même lookup_key. La fois suivante, l'étape 3 le retrouve.

Conséquence : le paiement fonctionne sans aucune manipulation préalable dans
le dashboard, et un tarif déjà créé n'est jamais dupliqué.

⚠️ Changer un montant ici ne modifie PAS un Price déjà créé (Stripe les rend
immuables). Pour changer un tarif : créer le nouveau Price dans le dashboard
et poser son id dans la variable d'environnement correspondante, ou modifier
le lookup_key ci-dessous pour forcer la création d'un nouveau Price.
"""

from __future__ import annotations

import logging
import threading

import stripe

from app.core.config import STRIPE_PRICES, STRIPE_SECRET_KEY

logger = logging.getLogger(__name__)

# Plans vendus par l'embed d'offres. Les clés `plan` sont celles stockées dans
# User.plan / Subscription.plan et attendues par app/core/limits.py.
PLAN_MEMBRE = "membre"
PLAN_MEMBRE_PLUS = "membre+"

CYCLE_MONTHLY = "monthly"
CYCLE_ANNUAL = "annual"

# Libellés affichés (emails, messages d'erreur, réponses admin).
PLAN_LABELS: dict[str, str] = {
    "free": "Gratuit",
    PLAN_MEMBRE: "Membre",
    PLAN_MEMBRE_PLUS: "Membre+",
    "prepa": "PREPASSERELLE",
    "beta": "Bêta",
}

# Nom du produit Stripe par plan — visible par l'élève sur la page de paiement
# et sur ses factures.
PRODUCT_NAMES: dict[str, str] = {
    PLAN_MEMBRE: "NAVIRE — Membre",
    PLAN_MEMBRE_PLUS: "NAVIRE — Membre+",
}

PRODUCT_DESCRIPTIONS: dict[str, str] = {
    PLAN_MEMBRE: "QCM illimités depuis tes PDF, 500 flashcards, 7 documents, "
                 "classement ELO et veille juridique.",
    PLAN_MEMBRE_PLUS: "QCM illimités depuis tes PDF, 1 000 flashcards, "
                      "24 documents, accès prioritaire aux nouveautés.",
}

# Tarifs publics affichés par l'embed, en centimes d'euro.
#   Membre    :  9,99 €/mois — annuel 71,93 €/an  (soit  5,99 €/mois, -40 %)
#   Membre+   : 16,99 €/mois — annuel 122,33 €/an (soit 10,19 €/mois, -40 %)
# L'annuel est facturé en UNE fois : Stripe prélève 71,93 € pour douze mois,
# le « 5,99 € / mois » de l'embed n'est qu'un équivalent mensuel d'affichage.
PLAN_CATALOG: dict[tuple[str, str], dict] = {
    (PLAN_MEMBRE, CYCLE_MONTHLY): {
        "lookup_key": "navire_membre_monthly",
        "amount_cents": 999,
        "interval": "month",
        "env_var": "STRIPE_PRICE_MEMBRE_MONTHLY",
        "nickname": "Membre — mensuel",
    },
    (PLAN_MEMBRE, CYCLE_ANNUAL): {
        "lookup_key": "navire_membre_annual",
        "amount_cents": 7193,
        "interval": "year",
        "env_var": "STRIPE_PRICE_MEMBRE_ANNUAL",
        "nickname": "Membre — annuel",
    },
    (PLAN_MEMBRE_PLUS, CYCLE_MONTHLY): {
        "lookup_key": "navire_membre_plus_monthly",
        "amount_cents": 1699,
        "interval": "month",
        "env_var": "STRIPE_PRICE_MEMBRE_PLUS_MONTHLY",
        "nickname": "Membre+ — mensuel",
    },
    (PLAN_MEMBRE_PLUS, CYCLE_ANNUAL): {
        "lookup_key": "navire_membre_plus_annual",
        "amount_cents": 12233,
        "interval": "year",
        "env_var": "STRIPE_PRICE_MEMBRE_PLUS_ANNUAL",
        "nickname": "Membre+ — annuel",
    },
}


class PriceUnavailable(RuntimeError):
    """Le Price n'a pas pu être résolu ni créé (clé Stripe absente, API HS…)."""


# Résolution sérialisée : deux requêtes simultanées sur le même plan ne doivent
# pas créer deux Prices portant le même lookup_key.
_LOCK = threading.RLock()

# (plan, cycle) -> price_id, et price_id -> (plan, cycle) pour le webhook.
_PRICE_CACHE: dict[tuple[str, str], str] = {}
_PLAN_BY_PRICE: dict[str, tuple[str, str]] = {}
_PRODUCT_CACHE: dict[str, str] = {}


def plan_label(plan: str | None) -> str:
    """Libellé lisible d'un plan ('membre+' -> 'Membre+')."""
    return PLAN_LABELS.get(plan or "free", plan or "free")


def _env_price_id(plan: str, cycle: str) -> str:
    """Price posé en variable d'environnement, chaîne vide si absent."""
    return (STRIPE_PRICES.get(plan, {}) or {}).get(cycle, "") or ""


def _remember(plan: str, cycle: str, price_id: str) -> str:
    _PRICE_CACHE[(plan, cycle)] = price_id
    _PLAN_BY_PRICE[price_id] = (plan, cycle)
    return price_id


def _find_price_by_lookup_key(lookup_key: str) -> str | None:
    """Price actif portant ce lookup_key, ou None. Fortement cohérent."""
    try:
        found = stripe.Price.list(lookup_keys=[lookup_key], active=True, limit=1)
    except stripe.StripeError as exc:
        logger.warning("Stripe : lookup_key %s illisible (%s)", lookup_key, exc)
        return None
    data = found.get("data") or []
    return data[0]["id"] if data else None


def _ensure_product(plan: str) -> str:
    """
    Product Stripe du plan. Réutilise celui d'un Price déjà créé pour l'autre
    cycle (mensuel/annuel partagent le même produit, comme dans le dashboard),
    sinon en crée un.
    """
    if plan in _PRODUCT_CACHE:
        return _PRODUCT_CACHE[plan]

    # 1. Un Price du même plan existe déjà (env ou lookup_key) → même produit.
    for cycle in (CYCLE_MONTHLY, CYCLE_ANNUAL):
        entry = PLAN_CATALOG.get((plan, cycle))
        if not entry:
            continue
        price_id = _PRICE_CACHE.get((plan, cycle)) or _env_price_id(plan, cycle) \
            or _find_price_by_lookup_key(entry["lookup_key"])
        if not price_id:
            continue
        try:
            price = stripe.Price.retrieve(price_id)
        except stripe.StripeError:
            continue
        product = price.get("product")
        if isinstance(product, dict):
            product = product.get("id")
        if product:
            _PRODUCT_CACHE[plan] = product
            return product

    # 2. Aucun Price NAVIRE encore créé pour ce plan → nouveau produit.
    product = stripe.Product.create(
        name=PRODUCT_NAMES.get(plan, f"NAVIRE — {plan}"),
        description=PRODUCT_DESCRIPTIONS.get(plan),
        metadata={"navire_plan": plan},
    )
    logger.info("Stripe : produit créé pour le plan %s (%s)", plan, product["id"])
    _PRODUCT_CACHE[plan] = product["id"]
    return product["id"]


def resolve_price_id(plan: str, cycle: str, allow_create: bool = True) -> str:
    """
    Price Stripe à facturer pour (plan, cycle). Lève PriceUnavailable si le
    Price ne peut être ni retrouvé ni créé.

    allow_create=False sert à l'inspection (endpoint admin) : on regarde ce qui
    existe sans rien créer dans le compte Stripe.
    """
    entry = PLAN_CATALOG.get((plan, cycle))

    # Plans hors catalogue (beta, prepa…) : uniquement ce que dit la config.
    if not entry:
        price_id = _env_price_id(plan, cycle)
        if not price_id:
            raise PriceUnavailable(f"Aucun Price Stripe configuré pour {plan}/{cycle}.")
        return _remember(plan, cycle, price_id)

    # 1. Variable d'environnement — priorité absolue.
    env_price = _env_price_id(plan, cycle)
    if env_price:
        return _remember(plan, cycle, env_price)

    with _LOCK:
        # 2. Cache process.
        cached = _PRICE_CACHE.get((plan, cycle))
        if cached:
            return cached

        if not STRIPE_SECRET_KEY:
            raise PriceUnavailable("Clé Stripe absente : impossible de résoudre le Price.")

        stripe.api_key = STRIPE_SECRET_KEY

        # 3. Price déjà présent dans le compte.
        existing = _find_price_by_lookup_key(entry["lookup_key"])
        if existing:
            return _remember(plan, cycle, existing)

        if not allow_create:
            raise PriceUnavailable(f"Aucun Price Stripe pour {plan}/{cycle}.")

        # 4. Création au tarif du catalogue.
        try:
            price = stripe.Price.create(
                currency="eur",
                unit_amount=entry["amount_cents"],
                recurring={"interval": entry["interval"]},
                product=_ensure_product(plan),
                lookup_key=entry["lookup_key"],
                transfer_lookup_key=True,
                nickname=entry["nickname"],
                metadata={"navire_plan": plan, "navire_cycle": cycle},
            )
        except stripe.StripeError as exc:
            # Course entre deux process (deux workers Render) : le lookup_key
            # vient peut-être d'être pris par l'autre — on relit avant d'échouer.
            retry = _find_price_by_lookup_key(entry["lookup_key"])
            if retry:
                return _remember(plan, cycle, retry)
            raise PriceUnavailable(f"Création du Price {plan}/{cycle} refusée : {exc}") from exc

        logger.info(
            "Stripe : Price créé %s (%s %s — %s c€)",
            price["id"], plan, cycle, entry["amount_cents"],
        )
        return _remember(plan, cycle, price["id"])


def plan_from_price_id(price_id: str) -> tuple[str, str] | None:
    """
    Chemin inverse : (plan, cycle) porté par un Price. Utilisé par le webhook
    quand l'élève change de formule depuis le Customer Portal — l'event
    customer.subscription.updated ne transporte alors aucune metadata à jour,
    seulement le nouveau Price.
    """
    if not price_id:
        return None

    if price_id in _PLAN_BY_PRICE:
        return _PLAN_BY_PRICE[price_id]

    # Prices épinglés par variable d'environnement.
    for plan, cycles in STRIPE_PRICES.items():
        for cycle, configured in (cycles or {}).items():
            if configured and configured == price_id:
                return (plan, cycle)

    # Dernier recours : demander à Stripe (lookup_key ou metadata du Price).
    try:
        stripe.api_key = STRIPE_SECRET_KEY
        price = stripe.Price.retrieve(price_id)
    except stripe.StripeError:
        return None

    lookup = price.get("lookup_key") or ""
    for (plan, cycle), entry in PLAN_CATALOG.items():
        if entry["lookup_key"] == lookup:
            _PLAN_BY_PRICE[price_id] = (plan, cycle)
            return (plan, cycle)

    meta = price.get("metadata") or {}
    plan, cycle = meta.get("navire_plan"), meta.get("navire_cycle")
    if plan and cycle:
        _PLAN_BY_PRICE[price_id] = (plan, cycle)
        return (plan, cycle)

    return None


def catalog_overview(allow_create: bool = False) -> list[dict]:
    """
    État des quatre Prices vendus par l'embed — alimente l'endpoint admin.
    Ne crée rien par défaut : sert à vérifier ce que voit la prod.
    """
    rows = []
    for (plan, cycle), entry in PLAN_CATALOG.items():
        env_price = _env_price_id(plan, cycle)
        row = {
            "plan": plan,
            "plan_label": plan_label(plan),
            "billing_cycle": cycle,
            "amount_cents": entry["amount_cents"],
            "amount_eur": round(entry["amount_cents"] / 100, 2),
            "interval": entry["interval"],
            "lookup_key": entry["lookup_key"],
            "env_var": entry["env_var"],
            "source": "env" if env_price else None,
            "price_id": env_price or None,
            "error": None,
        }
        if not env_price:
            try:
                price_id = resolve_price_id(plan, cycle, allow_create=allow_create)
                row["price_id"] = price_id
                row["source"] = "stripe"
            except PriceUnavailable as exc:
                row["error"] = str(exc)
        rows.append(row)
    return rows
