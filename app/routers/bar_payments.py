# app/routers/bar_payments.py
# Paiement Stripe pour L'Abreuvoir
#
# Compatibilité : stripe >= 5.0 (testé v11.x)
# Les modules stripe.error ont été renommés en stripe v5+.
# Ce fichier utilise uniquement stripe.StripeError (API publique stable).
#
# Variables d'environnement (Render) :
#   STRIPE_SECRET_KEY        sk_live_...   (ou sk_test_... en dev)
#   STRIPE_WEBHOOK_SECRET    whsec_...
#   STRIPE_SUCCESS_URL       https://ffdebat.org/abreuvoir/?success=1
#   STRIPE_CANCEL_URL        https://ffdebat.org/abreuvoir/?cancel=1
#   STRIPE_CURRENCY          eur
#
# Dans main.py :
#   from app.routers.bar_payments import router as bar_router
#   app.include_router(bar_router)

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import stripe
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.routers.ffd_resa import (
    _generate_id,
    _save_orders,
    _with_cors,
    _with_orders_lock,
)

router = APIRouter(prefix="/bar", tags=["bar_payments"])


# ── CORS preflight pour TOUTES les routes /bar/* ──────────────────────────────
# Sans ce handler, le navigateur bloque les POST/GET cross-origin avant
# qu'ils atteignent Stripe ou la logique métier.
@router.options("/{path:path}")
def bar_cors_preflight(path: str, request: Request):
    return _with_cors(request, Response(status_code=204))

# ── config ────────────────────────────────────────────────────────────────────
STRIPE_SECRET_KEY     = os.getenv("STRIPE_SECRET_KEY",     "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
STRIPE_CURRENCY       = os.getenv("STRIPE_CURRENCY",       "eur").strip().lower()
STRIPE_SUCCESS_URL    = os.getenv("STRIPE_SUCCESS_URL",    "").strip()
STRIPE_CANCEL_URL     = os.getenv("STRIPE_CANCEL_URL",     "").strip()
ASSO_CENTS_DEFAULT    = int(os.getenv("ASSO_CENTS_DEFAULT", "75"))

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# ── Compat : stripe v5+ a renommé les exceptions ─────────────────────────────
try:
    _StripeError                = stripe.StripeError              # v5+
    _SignatureVerificationError = stripe.SignatureVerificationError
except AttributeError:
    _StripeError                = stripe.error.StripeError        # v2/v3/v4
    _SignatureVerificationError = stripe.error.SignatureVerificationError


# ── Pydantic models ───────────────────────────────────────────────────────────
class CheckoutItem(BaseModel):
    label:      str
    qty:        int = Field(..., ge=1)
    unit_cents: int = Field(..., ge=0)


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


def _build_success_url(base: str, order_id: str) -> str:
    """
    Gère le cas où STRIPE_SUCCESS_URL contient deja un '?' ou pas.
    Ex:  https://ffdebat.org/abreuvoir/?success=1   -> &order_id=...
         https://ffdebat.org/abreuvoir/              -> ?success=1&order_id=...
    """
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}order_id={order_id}&session_id={{CHECKOUT_SESSION_ID}}"


# ── POST /bar/checkout-session ────────────────────────────────────────────────
@router.post("/checkout-session")
def create_checkout_session(body: CheckoutCreate, request: Request):
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="STRIPE_SECRET_KEY manquante.")
    if not STRIPE_SUCCESS_URL or not STRIPE_CANCEL_URL:
        raise HTTPException(status_code=500, detail="STRIPE_SUCCESS_URL / STRIPE_CANCEL_URL manquantes.")

    order_id = _generate_id()
    new_order: Dict[str, Any] = {
        "id":                    order_id,
        "table":                 body.table.strip(),
        "name":                  body.name.strip(),
        "phone":                 body.phone.strip(),
        "method":                "stripe",
        "items": [{"label": it.label, "qty": it.qty, "price_cents": it.unit_cents} for it in body.items],
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

    line_items: List[dict] = []
    for it in body.items:
        line_items.append({
            "price_data": {
                "currency":     STRIPE_CURRENCY,
                "product_data": {"name": it.label},
                "unit_amount":  it.unit_cents,
            },
            "quantity": it.qty,
        })

    if body.donation_cents > 0:
        line_items.append({
            "price_data": {
                "currency":     STRIPE_CURRENCY,
                "product_data": {"name": "Don a l'association"},
                "unit_amount":  body.donation_cents,
            },
            "quantity": 1,
        })

    success_url = _build_success_url(STRIPE_SUCCESS_URL, order_id)

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=line_items,
            success_url=success_url,
            cancel_url=STRIPE_CANCEL_URL,
            metadata={
                "order_id": order_id,
                "table":    created["table"],
                "name":     created["name"],
            },
        )
    except _StripeError as e:
        def _rollback(orders: List[dict]) -> None:
            orders[:] = [o for o in orders if o.get("id") != order_id]
            _save_orders(orders)
        _with_orders_lock(_rollback)
        msg = getattr(e, "user_message", None) or str(e)
        raise HTTPException(status_code=502, detail=f"Erreur Stripe : {msg}")

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


# ── GET /bar/checkout-verify ──────────────────────────────────────────────────
@router.get("/checkout-verify")
def checkout_verify(order_id: str, session_id: str, request: Request):
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="STRIPE_SECRET_KEY manquante.")

    try:
        sess = stripe.checkout.Session.retrieve(session_id)
    except _StripeError as e:
        raise HTTPException(status_code=400, detail=f"Session Stripe introuvable : {e}")

    # Compat objet Stripe (attribut) ou dict
    meta = getattr(sess, "metadata", None) or {}
    if not isinstance(meta, dict):
        meta = dict(meta)
    meta_order_id = meta.get("order_id", "")

    if meta_order_id != order_id:
        raise HTTPException(status_code=400, detail="order_id ne correspond pas a la session Stripe.")

    payment_status = getattr(sess, "payment_status", None) or sess.get("payment_status", "")
    pi             = getattr(sess, "payment_intent", None) or sess.get("payment_intent")
    paid           = payment_status == "paid"

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


# ── POST /bar/stripe/webhook ──────────────────────────────────────────────────
@router.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="STRIPE_WEBHOOK_SECRET manquante.")

    payload = await request.body()
    sig     = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except _SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Signature webhook invalide.")
    except Exception:
        raise HTTPException(status_code=400, detail="Webhook payload invalide.")

    if event["type"] == "checkout.session.completed":
        sess = event["data"]["object"]
        meta = getattr(sess, "metadata", None) or sess.get("metadata") or {}
        if not isinstance(meta, dict):
            meta = dict(meta)
        order_id = meta.get("order_id")
        sess_id  = getattr(sess, "id", None) or sess.get("id")
        pi       = getattr(sess, "payment_intent", None) or sess.get("payment_intent")

        if order_id:
            def _mark_paid(orders: List[dict]) -> bool:
                for o in orders:
                    if o.get("id") == order_id:
                        o["payment_status"]        = "paid"
                        o["stripe_session_id"]     = sess_id
                        o["stripe_payment_intent"] = pi
                        _save_orders(orders)
                        return True
                return False
            _with_orders_lock(_mark_paid)

    return JSONResponse({"ok": True})