import stripe

from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from app.meoles_site.config import meoles_settings
from app.meoles_site.stripe_routes import PRICE_META

stripe.api_key = meoles_settings.STRIPE_SECRET_KEY_MEOLES

router = APIRouter(prefix="/meoles/admin", tags=["meoles-admin"])


def _parse_session(s) -> dict:
    """Normalise une Stripe Checkout Session en dict exploitable."""
    customer  = s.customer_details or {}
    shipping  = (s.shipping_details or {})
    addr      = getattr(shipping, "address", None) or {}

    # Line items (expand nécessaire, pas toujours dispo selon l'appel)
    items = []
    if hasattr(s, "line_items") and s.line_items:
        for line in s.line_items.data:
            price_id = line.price.id if line.price else ""
            meta     = PRICE_META.get(price_id, {})
            items.append({
                "label":    meta.get("label", line.description or price_id),
                "key":      meta.get("key", ""),
                "quantity": line.quantity or 1,
                "price":    (line.price.unit_amount or 0) / 100,
            })

    return {
        "stripe_session_id": s.id,
        "created_at":        s.created,          # unix timestamp
        "status":            s.payment_status,   # paid | unpaid
        "customer_name":     getattr(customer, "name",  None) or "—",
        "customer_email":    getattr(customer, "email", None) or "—",
        "amount_total":      (s.amount_total or 0) / 100,
        "currency":          s.currency or "eur",
        "shipping_city":     getattr(addr, "city",    None) or "—",
        "shipping_country":  getattr(addr, "country", None) or "—",
        "items":             items,
    }


@router.get("/orders")
def get_orders(
    limit: int = Query(default=20, ge=1, le=100),
    starting_after: Optional[str] = Query(default=None),
):
    """
    Retourne les dernières commandes MEOLES payées via Payment Links ou Checkout.
    Paramètres :
      - limit          : nb de résultats (1-100, défaut 20)
      - starting_after : ID Stripe de la dernière session vue (pagination curseur)
    """
    try:
        params = dict(
            limit=limit,
            expand=["data.line_items"],
        )
        if starting_after:
            params["starting_after"] = starting_after

        sessions = stripe.checkout.Session.list(**params)

        orders = []
        for s in sessions.data:
            # On ne garde que les sessions payées
            if s.payment_status != "paid":
                continue
            orders.append(_parse_session(s))

        return {
            "count":   len(orders),
            "has_more": sessions.has_more,
            "orders":  orders,
        }

    except stripe.error.StripeError as e:
        raise HTTPException(502, f"Stripe error : {e}")


@router.get("/orders/{stripe_session_id}")
def get_order(stripe_session_id: str):
    """Détail d'une commande par son ID Stripe Checkout Session."""
    try:
        s = stripe.checkout.Session.retrieve(
            stripe_session_id,
            expand=["line_items"],
        )
        return _parse_session(s)
    except stripe.error.InvalidRequestError:
        raise HTTPException(404, "Session introuvable")
    except stripe.error.StripeError as e:
        raise HTTPException(502, f"Stripe error : {e}")