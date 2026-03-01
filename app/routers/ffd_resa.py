# app/routers/ffd_resa.py
# Paiement "bar / carte" pour asso (widget WordPress) + Stripe Checkout + Webhook
#
# Endpoints:
#   GET  /ffd/resa/health
#   GET  /ffd/resa/menu?type=BOISSON|PLAT
#   POST /ffd/resa/checkout-session
#   POST /ffd/resa/webhook
#
# Menu file:
#   app/data/menu.json   (format: {"BOISSON":[{id,label,price_cents,category}], "PLAT":[...]})
#
# ENV:
#   STRIPE_SECRET_KEY=sk_test_... or sk_live_...
#   STRIPE_WEBHOOK_SECRET=whsec_...
#   FRONT_SUCCESS_URL=https://ton-site.tld/success
#   FRONT_CANCEL_URL=https://ton-site.tld/cancel
#   FFD_RESA_MENU_PATH=app/data/menu.json                      (optionnel)
#   FFD_RESA_PAYMENTS_LOG=./storage/ffd_resa_payments.jsonl     (optionnel)
#
# API key (optionnel):
#   API_KEY=NAVIRE_APIKEY_2026_0001
#   FFD_RESA_REQUIRE_API_KEY=false   (true => exige header x-api-key sur checkout + menu)
#
# CORS (géré dans main.py normalement):
#   CORS_ORIGINS=https://ton-site.tld,https://www.ton-site.tld

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, List, Literal, Optional

import stripe
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field, conint

MenuType = Literal["BOISSON", "PLAT"]

# ----------------------------
# Config
# ----------------------------
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
FRONT_SUCCESS_URL = os.getenv("FRONT_SUCCESS_URL", "").strip()
FRONT_CANCEL_URL = os.getenv("FRONT_CANCEL_URL", "").strip()

# menu.json (par défaut: app/data/menu.json)
FFD_RESA_MENU_PATH = os.getenv("FFD_RESA_MENU_PATH", "app/data/menu.json").strip()
FFD_RESA_PAYMENTS_LOG = os.getenv("FFD_RESA_PAYMENTS_LOG", "./storage/ffd_resa_payments.jsonl").strip()

# API Key (optionnel)
API_KEY = os.getenv("API_KEY", "").strip()  # ex: NAVIRE_APIKEY_2026_0001
FFD_RESA_REQUIRE_API_KEY = os.getenv("FFD_RESA_REQUIRE_API_KEY", "false").strip().lower() in ("1", "true", "yes")

CURRENCY = "eur"

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

router = APIRouter(prefix="/ffd/resa", tags=["ffd_resa"])


# ----------------------------
# Utils
# ----------------------------
def _require_key(x_api_key: Optional[str]) -> None:
    """Protection optionnelle (⚠️ un widget WP ne peut pas cacher la key)."""
    if not FFD_RESA_REQUIRE_API_KEY:
        return
    expected = API_KEY
    if not expected:
        raise HTTPException(status_code=500, detail="API_KEY manquante côté serveur (env).")
    if (x_api_key or "") != expected:
        raise HTTPException(status_code=401, detail="Unauthorized (x-api-key)")

def _ensure_stripe_ready() -> None:
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="STRIPE_SECRET_KEY manquante (env).")
    if not FRONT_SUCCESS_URL or not FRONT_CANCEL_URL:
        raise HTTPException(status_code=500, detail="FRONT_SUCCESS_URL / FRONT_CANCEL_URL manquants (env).")

def _safe_write_jsonl(path: str, payload: dict) -> None:
    p = Path(path)
    if p.parent and str(p.parent) not in ("", "."):
        p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")

def _load_menu() -> Dict[str, List[dict]]:
    p = Path(FFD_RESA_MENU_PATH)
    if not p.exists():
        raise HTTPException(status_code=500, detail=f"Menu introuvable: {FFD_RESA_MENU_PATH}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Menu illisible (JSON): {e}")

    if not isinstance(data, dict) or "BOISSON" not in data or "PLAT" not in data:
        raise HTTPException(status_code=500, detail="menu.json invalide: doit contenir BOISSON et PLAT")
    if not isinstance(data["BOISSON"], list) or not isinstance(data["PLAT"], list):
        raise HTTPException(status_code=500, detail="menu.json invalide: BOISSON/PLAT doivent être des listes")

    return data

def _build_index(menu: Dict[str, List[dict]]) -> Dict[str, dict]:
    idx: Dict[str, dict] = {}
    for t in ("BOISSON", "PLAT"):
        for it in menu[t]:
            # validation minimale
            if not isinstance(it, dict):
                continue
            _id = str(it.get("id", "")).strip()
            label = str(it.get("label", "")).strip()
            price_cents = it.get("price_cents", None)
            if not _id or not label:
                continue
            try:
                pc = int(price_cents)
            except Exception:
                continue
            if pc < 0:
                continue
            idx[_id] = {
                "id": _id,
                "label": label,
                "price_cents": pc,
                "category": str(it.get("category", "")).strip(),
                "type": t,
            }
    return idx


# ----------------------------
# Schemas
# ----------------------------
class CartLineIn(BaseModel):
    item_id: str
    qty: conint(ge=1, le=20) = 1

class CheckoutIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    donation_eur: conint(ge=0, le=50) = 0
    items: List[CartLineIn] = Field(min_length=1, max_length=50)
    event_id: Optional[str] = Field(default=None, max_length=60)
    table_id: Optional[str] = Field(default=None, max_length=60)

class CheckoutOut(BaseModel):
    checkout_url: str


# ----------------------------
# Endpoints
# ----------------------------
@router.get("/health")
def health():
    return {"ok": True, "module": "ffd_resa", "require_api_key": FFD_RESA_REQUIRE_API_KEY}

@router.get("/menu")
def menu(type: MenuType, x_api_key: str | None = Header(default=None, alias="x-api-key")):
    _require_key(x_api_key)

    data = _load_menu()
    # retourne seulement le type demandé
    return {"type": type, "items": data[type]}

@router.post("/checkout-session", response_model=CheckoutOut)
def checkout_session(body: CheckoutIn, x_api_key: str | None = Header(default=None, alias="x-api-key")):
    _require_key(x_api_key)
    _ensure_stripe_ready()

    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name vide.")

    menu = _load_menu()
    idx = _build_index(menu)

    line_items = []
    subtotal_cents = 0

    # build line_items depuis item_id (prix server-side)
    for line in body.items:
        it = idx.get(line.item_id)
        if not it:
            raise HTTPException(status_code=400, detail=f"item_id inconnu: {line.item_id}")
        qty = int(line.qty)
        unit = int(it["price_cents"])
        subtotal_cents += unit * qty

        line_items.append({
            "price_data": {
                "currency": CURRENCY,
                "product_data": {"name": it["label"]},
                "unit_amount": unit,
            },
            "quantity": qty,
        })

    donation_cents = int(body.donation_eur) * 100
    if donation_cents > 0:
        line_items.append({
            "price_data": {
                "currency": CURRENCY,
                "product_data": {"name": "Don à l'association"},
                "unit_amount": donation_cents,
            },
            "quantity": 1,
        })

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=line_items,
            success_url=FRONT_SUCCESS_URL + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=FRONT_CANCEL_URL,
            metadata={
                "payer_name": name,
                "donation_eur": str(body.donation_eur),
                "event_id": body.event_id or "",
                "table_id": body.table_id or "",
                "subtotal_cents": str(subtotal_cents),
                "created_at_unix": str(int(time.time())),
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stripe error: {e}")

    # log "created" (audit simple)
    _safe_write_jsonl(FFD_RESA_PAYMENTS_LOG, {
        "kind": "checkout_session_created",
        "session_id": session.get("id"),
        "payer_name": name,
        "donation_eur": int(body.donation_eur),
        "items": [l.model_dump() for l in body.items],
        "ts": int(time.time()),
    })

    return CheckoutOut(checkout_url=session.url)

@router.post("/webhook")
async def webhook(request: Request, stripe_signature: str = Header(default="", alias="Stripe-Signature")):
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="STRIPE_WEBHOOK_SECRET manquante (env).")

    payload = await request.body()
    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=stripe_signature,
            secret=STRIPE_WEBHOOK_SECRET,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook signature invalid: {e}")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        _safe_write_jsonl(FFD_RESA_PAYMENTS_LOG, {
            "kind": "checkout_completed",
            "session_id": session.get("id"),
            "amount_total": session.get("amount_total"),
            "currency": session.get("currency"),
            "payment_status": session.get("payment_status"),
            "metadata": session.get("metadata"),
            "created": session.get("created"),
        })

    return {"ok": True}