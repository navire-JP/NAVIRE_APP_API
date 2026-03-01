# app/routers/bar_payments.py
# Paiement Stripe pour L'Abreuvoir
#
# Endpoints:
#   POST /bar/checkout-session   -> crée commande "unpaid" + session Stripe Checkout
#   GET  /bar/checkout-verify    -> vérifie côté serveur que Stripe a bien encaissé
#   POST /bar/stripe/webhook     -> webhook Stripe (checkout.session.completed)
#   OPTIONS /bar/*               -> CORS preflight (géré par ffd_resa)
#
# Variables d'environnement à ajouter sur Render:
#   STRIPE_SECRET_KEY        sk_live_...   (ou sk_test_... en dev)
#   STRIPE_WEBHOOK_SECRET    whsec_...     (récupéré dans Dashboard Stripe → Webhooks)
#   STRIPE_SUCCESS_URL       https://ffdebat.org/abreuvoir/?success=1
#   STRIPE_CANCEL_URL        https://ffdebat.org/abreuvoir/?cancel=1
#   STRIPE_CURRENCY          eur
#
# IDs Stripe production:
#   Produit : prod_U4A7eUrJIFCcte
#   Tarif   : price_1T61mrLeRHpDiZMs7e9bMElF   (utilisé UNIQUEMENT pour le don fixe
#              si tu veux un tarif catalogue — les articles du menu utilisent price_data
#              dynamique pour coller aux vrais prix du menu.json)
#
# pip install stripe  (+ ajouter stripe dans requirements.txt)

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import stripe
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ── réutilise les helpers thread-safe de ffd_resa ──────────────────────────
from app.routers.ffd_resa import (
    _generate_id,
    _load_orders,
    _save_orders,
    _with_cors,
    _with_orders_lock,
)

router = APIRouter(prefix="/bar", tags=["bar_payments"])

# ── config ──────────────────────────────────────────────────────────────────
STRIPE_SECRET_KEY     = os.getenv("STRIPE_SECRET_KEY",     "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
STRIPE_CURRENCY       = os.getenv("STRIPE_CURRENCY",       "eur").strip().lower()
STRIPE_SUCCESS_URL    = os.getenv("STRIPE_SUCCESS_URL",    "").strip()
STRIPE_CANCEL_URL     = os.getenv("STRIPE_CANCEL_URL",     "").strip()

# Tarif Stripe pour le DON LIBRE (produit catalogue prod_U4A7eUrJIFCcte)
# Ce price_id est à montant FIXE — on l'utilise comme fallback ou pour
# les dons d'un montant prédéfini.  Les articles menu utilisent price_data
# dynamique (montant exact calculé côté serveur).
STRIPE_DON_PRICE_ID   = os.getenv("STRIPE_DON_PRICE_ID", "price_1T61mrLeRHpDiZMs7e9bMElF").strip()

ASSO_CENTS_DEFAULT    = int(os.getenv("ASSO_CENTS_DEFAULT", "75"))

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY


# ── Pydantic models ─────────────────────────────────────────────────────────
class CheckoutItem(BaseModel):
    label:      str
    qty:        int = Field(..., ge=1)
    unit_cents: int = Field(..., ge=0)   # prix article + 0,75 € asso déjà inclus


class CheckoutCreate(BaseModel):
    table:          str
    name:           str
    phone:          str
    method:         str = "stripe"
    items:          List[CheckoutItem]
    donation_cents: int = Field(default=0, ge=0)
    asso_cents:     int = Field(default=0, ge=0)
    total_cents:    int = Field(..., ge=0)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── POST /bar/checkout-session ───────────────────────────────────────────────
@router.post("/checkout-session")
def create_checkout_session(body: CheckoutCreate, request: Request):
    """
    1. Crée la commande dans orders.json avec payment_status="unpaid".
    2. Construit les line_items Stripe (price_data dynamique par article).
    3. Ajoute le don en price_data dynamique (montant libre).
    4. Crée la session Stripe Checkout et sauvegarde le session_id.
    5. Retourne { ok, order_id, checkout_url }.
    """
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="STRIPE_SECRET_KEY manquante côté serveur.")
    if not STRIPE_SUCCESS_URL or not STRIPE_CANCEL_URL:
        raise HTTPException(
            status_code=500,
            detail="STRIPE_SUCCESS_URL / STRIPE_CANCEL_URL manquantes côté serveur.",
        )

    # ── 1) persister la commande en "unpaid" ──────────────────────────────
    order_id = _generate_id()
    new_order: Dict[str, Any] = {
        "id":                    order_id,
        "table":                 body.table.strip(),
        "name":                  body.name.strip(),
        "phone":                 body.phone.strip(),
        "method":                "stripe",
        "items":                 [
            {"label": it.label, "qty": it.qty, "price_cents": it.unit_cents}
            for it in body.items
        ],
        "donation_cents":        body.donation_cents,
        "asso_cents":            body.asso_cents,
        "total_cents":           body.total_cents,
        "status":                "pending",
        "ts":                    _now_iso(),
        "done_at":               None,
        "payment_status":        "unpaid",
        "stripe_session_id":     None,
        "stripe_payment_intent": None,
    }

    def _insert(orders: List[dict]) -> dict:
        orders.append(new_order)
        _save_orders(orders)
        return new_order

    created = _with_orders_lock(_insert)

    # ── 2) construire les line_items (price_data dynamique) ───────────────
    line_items: List[dict] = []
    for it in body.items:
        line_items.append({
            "price_data": {
                "currency":     STRIPE_CURRENCY,
                "product_data": {"name": it.label},
                "unit_amount":  it.unit_cents,   # centimes, asso inclus
            },
            "quantity": it.qty,
        })

    # ── 3) don libre (price_data dynamique si montant > 0) ────────────────
    if body.donation_cents > 0:
        line_items.append({
            "price_data": {
                "currency":     STRIPE_CURRENCY,
                "product_data": {"name": "Don à l'association ♥"},
                "unit_amount":  body.donation_cents,
            },
            "quantity": 1,
        })

    # ── 4) créer la session Stripe ────────────────────────────────────────
    # {CHECKOUT_SESSION_ID} est un placeholder Stripe (pas une f-string Python)
    success_url = (
        f"{STRIPE_SUCCESS_URL}"
        f"&order_id={order_id}"
        f"&session_id={{CHECKOUT_SESSION_ID}}"
    )

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=line_items,
            success_url=success_url,
            cancel_url=STRIPE_CANCEL_URL,
            metadata={
                "order_id": order_id,
                "table":    created["table"],
                "name":     created["name"],
            },
        )
    except stripe.error.StripeError as e:
        # En cas d'erreur Stripe, on supprime la commande créée pour éviter les doublons
        def _rollback(orders: List[dict]) -> None:
            orders[:] = [o for o in orders if o.get("id") != order_id]
            _save_orders(orders)

        _with_orders_lock(_rollback)
        raise HTTPException(status_code=502, detail=f"Erreur Stripe : {e.user_message or str(e)}")

    # ── 5) sauver le stripe_session_id ────────────────────────────────────
    def _set_session(orders: List[dict]) -> Optional[dict]:
        for o in orders:
            if o.get("id") == order_id:
                o["stripe_session_id"] = session.id
                _save_orders(orders)
                return o
        return None

    _with_orders_lock(_set_session)

    return _with_cors(
        request,
        JSONResponse({"ok": True, "order_id": order_id, "checkout_url": session.url}),
    )


# ── GET /bar/checkout-verify ─────────────────────────────────────────────────
@router.get("/checkout-verify")
def checkout_verify(order_id: str, session_id: str, request: Request):
    """
    Appelé par Widget 1 après le redirect Stripe success.
    Vérifie côté serveur que payment_status=="paid", puis marque la commande.
    Retourne { ok, paid, order_id }.
    """
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="STRIPE_SECRET_KEY manquante côté serveur.")

    # ── 1) récupérer la session Stripe ────────────────────────────────────
    try:
        sess = stripe.checkout.Session.retrieve(session_id)
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=f"Session Stripe introuvable : {e}")

    # ── 2) vérifier la cohérence order_id ────────────────────────────────
    meta_order_id = (sess.get("metadata") or {}).get("order_id", "")
    if meta_order_id != order_id:
        raise HTTPException(
            status_code=400,
            detail="order_id ne correspond pas à la session Stripe.",
        )

    paid = sess.get("payment_status") == "paid"
    pi   = sess.get("payment_intent")

    # ── 3) marquer la commande comme payée ────────────────────────────────
    if paid:
        def _mark_paid(orders: List[dict]) -> Optional[dict]:
            for o in orders:
                if o.get("id") == order_id:
                    o["payment_status"]        = "paid"
                    o["stripe_payment_intent"] = pi
                    _save_orders(orders)
                    return o
            return None

        updated = _with_orders_lock(_mark_paid)
        if not updated:
            raise HTTPException(status_code=404, detail="Commande introuvable.")

    return _with_cors(
        request,
        JSONResponse({"ok": True, "paid": paid, "order_id": order_id}),
    )


# ── POST /bar/stripe/webhook ─────────────────────────────────────────────────
@router.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    """
    Webhook Stripe (checkout.session.completed).
    Marque la commande comme payée même si le navigateur ne revient jamais
    sur la success_url (fermeture, crash, réseau coupé…).

    Dashboard Stripe → Developers → Webhooks :
      URL : https://TON-SERVICE.onrender.com/bar/stripe/webhook
      Events : checkout.session.completed
    """
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="STRIPE_WEBHOOK_SECRET manquante côté serveur.")

    payload = await request.body()
    sig     = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Signature webhook invalide.")
    except Exception:
        raise HTTPException(status_code=400, detail="Webhook payload invalide.")

    if event["type"] == "checkout.session.completed":
        sess     = event["data"]["object"]
        order_id = (sess.get("metadata") or {}).get("order_id")
        if order_id:
            def _mark_paid(orders: List[dict]) -> bool:
                for o in orders:
                    if o.get("id") == order_id:
                        o["payment_status"]        = "paid"
                        o["stripe_session_id"]     = sess.get("id")
                        o["stripe_payment_intent"] = sess.get("payment_intent")
                        _save_orders(orders)
                        return True
                return False

            _with_orders_lock(_mark_paid)

    # Stripe exige une réponse 200 même si on ne fait rien de l'event
    return JSONResponse({"ok": True})