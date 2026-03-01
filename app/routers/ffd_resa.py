# app/routers/ffd_resa.py
# FFD RESA — MENU BRIDGE + ORDERS (sans Stripe pour l'instant)
#
# Endpoints:
#   GET  /ffd/resa/health
#   GET  /ffd/resa/menu                      -> renvoie TOUT (BOISSON + PLAT)
#   GET  /ffd/resa/menu?type=BOISSON         -> renvoie seulement BOISSON
#   GET  /ffd/resa/menu?type=PLAT            -> renvoie seulement PLAT
#   OPTIONS /ffd/resa/*                      -> preflight CORS
#
#   POST   /ffd/resa/order                   -> créer une commande
#   GET    /ffd/resa/orders                  -> lister toutes les commandes (phone masqué si pas admin)
#   GET    /ffd/resa/order/{id}              -> récupérer une commande
#   PATCH  /ffd/resa/order/{id}/status       -> changer le statut (admin requis)
#   DELETE /ffd/resa/order/{id}              -> supprimer (admin requis)
#   GET    /ffd/resa/orders/export           -> export CSV (admin requis)
#
# Fichiers:
#   app/data/menu.json       -> carte (BOISSON + PLAT)
#   app/data/orders.json     -> stockage des commandes (auto-créé)
#
# ENV:
#   FFD_RESA_MENU_PATH=app/data/menu.json
#   FFD_RESA_ORDERS_PATH=app/data/orders.json
#   CORS_FFD=https://ffdebat.org,https://www.ffdebat.org
#   FFD_RESA_REQUIRE_API_KEY=false
#   API_KEY=NAVIRE_APIKEY_2026_0001
#   FFD_ADMIN_CODE=NAWRES03             -> code admin pour les actions sensibles
#
# TODO (après récupération des codes Stripe):
#   POST /bar/checkout-session          -> créer une session Stripe Checkout

from __future__ import annotations

import csv
import fcntl
import io
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

MenuType = Literal["BOISSON", "PLAT"]
OrderStatus = Literal["pending", "done"]
PayMethod = Literal["cash", "stripe"]

router = APIRouter(prefix="/ffd/resa", tags=["ffd_resa"])

# ─────────────────────────────────────────
# ENV / CONFIG
# ─────────────────────────────────────────
FFD_RESA_MENU_PATH   = os.getenv("FFD_RESA_MENU_PATH",   "app/data/menu.json").strip()
FFD_RESA_ORDERS_PATH = os.getenv("FFD_RESA_ORDERS_PATH", "app/data/orders.json").strip()

CORS_FFD        = os.getenv("CORS_FFD", "https://ffdebat.org").strip()
ALLOWED_ORIGINS = [o.strip() for o in CORS_FFD.split(",") if o.strip()]

API_KEY                   = os.getenv("API_KEY", "").strip()
FFD_RESA_REQUIRE_API_KEY  = os.getenv("FFD_RESA_REQUIRE_API_KEY", "false").strip().lower() in ("1", "true", "yes")
FFD_ADMIN_CODE            = os.getenv("FFD_ADMIN_CODE", "NAWRES03").strip()

CORS_ALLOW_HEADERS = "Content-Type, Authorization, x-api-key, x-admin-code"
CORS_ALLOW_METHODS = "GET, POST, PATCH, DELETE, OPTIONS"


# ─────────────────────────────────────────
# CORS helpers
# ─────────────────────────────────────────
def _cors_origin(request: Request) -> Optional[str]:
    origin = request.headers.get("origin")
    if not origin:
        return None
    if "*" in ALLOWED_ORIGINS:
        return "*"
    return origin if origin in ALLOWED_ORIGINS else None


def _with_cors(request: Request, response: Response) -> Response:
    origin = _cors_origin(request)
    if origin:
        response.headers["Access-Control-Allow-Origin"]  = origin
        response.headers["Vary"]                          = "Origin"
        response.headers["Access-Control-Allow-Methods"]  = CORS_ALLOW_METHODS
        response.headers["Access-Control-Allow-Headers"]  = CORS_ALLOW_HEADERS
        response.headers["Access-Control-Max-Age"]        = "3600"
    return response


@router.options("/{path:path}")
def cors_preflight(path: str, request: Request):
    return _with_cors(request, Response(status_code=204))


# ─────────────────────────────────────────
# Auth helpers
# ─────────────────────────────────────────
def _require_key(x_api_key: Optional[str]) -> None:
    if not FFD_RESA_REQUIRE_API_KEY:
        return
    if not API_KEY:
        raise HTTPException(status_code=500, detail="API_KEY manquante côté serveur.")
    if (x_api_key or "") != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized (x-api-key)")


def _is_admin(x_admin_code: Optional[str]) -> bool:
    return bool(FFD_ADMIN_CODE) and (x_admin_code or "").strip() == FFD_ADMIN_CODE


def _require_admin(x_admin_code: Optional[str]) -> None:
    if not _is_admin(x_admin_code):
        raise HTTPException(status_code=403, detail="Code admin invalide (x-admin-code).")


# ─────────────────────────────────────────
# Menu helpers
# ─────────────────────────────────────────
def _load_menu() -> Dict[str, List[dict]]:
    p = Path(FFD_RESA_MENU_PATH)
    if not p.exists():
        raise HTTPException(status_code=500, detail=f"Menu introuvable : {FFD_RESA_MENU_PATH}")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Menu illisible (JSON) : {e}")
    if not isinstance(raw, dict) or "BOISSON" not in raw or "PLAT" not in raw:
        raise HTTPException(status_code=500, detail="menu.json invalide : doit contenir BOISSON et PLAT.")

    def clean(items: List[dict]) -> List[dict]:
        out: List[dict] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            _id   = str(it.get("id", "")).strip()
            label = str(it.get("label", "")).strip()
            if not _id or not label:
                continue
            try:
                price_cents = int(it.get("price_cents", -1))
            except Exception:
                continue
            if price_cents < 0:
                continue
            out.append({"id": _id, "label": label, "price_cents": price_cents,
                        "category": str(it.get("category", "")).strip()})
        return out

    return {"BOISSON": clean(raw["BOISSON"]), "PLAT": clean(raw["PLAT"])}


# ─────────────────────────────────────────
# Orders storage helpers (JSON file + flock)
# ─────────────────────────────────────────
def _orders_path() -> Path:
    p = Path(FFD_RESA_ORDERS_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load_orders() -> List[dict]:
    p = _orders_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_orders(orders: List[dict]) -> None:
    p = _orders_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(orders, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)  # atomic on POSIX


def _with_orders_lock(fn):
    """
    Exécute fn(orders) -> orders de manière thread-safe via flock.
    Retourne la valeur de retour de fn.
    """
    p = _orders_path()
    lock_file = p.with_suffix(".lock")
    with open(lock_file, "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            orders = _load_orders()
            result = fn(orders)
            return result
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _generate_id() -> str:
    """ID simple : timestamp ms + 3 digits random."""
    import random
    return str(int(time.time() * 1000)) + str(random.randint(100, 999))


# ─────────────────────────────────────────
# Pydantic models
# ─────────────────────────────────────────
class OrderItem(BaseModel):
    label:       str
    qty:         int   = Field(..., ge=1)
    price_cents: int   = Field(..., ge=0)


class OrderCreate(BaseModel):
    table:          str
    name:           str
    phone:          str
    method:         PayMethod
    items:          List[OrderItem]
    donation_cents: int = Field(default=0, ge=0)
    asso_cents:     int = Field(default=0, ge=0)
    total_cents:    int = Field(default=0, ge=0)
    status:         OrderStatus = "pending"


class StatusUpdate(BaseModel):
    status: OrderStatus


# ─────────────────────────────────────────
# Masking helper (phone)
# ─────────────────────────────────────────
def _mask_phone(phone: str) -> str:
    """Ex : 0612345678 -> 06•••••678"""
    clean = phone.replace(" ", "").replace(".", "").replace("-", "")
    if len(clean) >= 6:
        return clean[:2] + "•" * (len(clean) - 5) + clean[-3:]
    return "•" * len(clean)


def _public_order(o: dict) -> dict:
    """Retourne la commande avec téléphone masqué."""
    out = {**o}
    if "phone" in out:
        out["phone"] = _mask_phone(str(out["phone"]))
    return out


# ─────────────────────────────────────────
# MENU ENDPOINTS
# ─────────────────────────────────────────
@router.get("/health")
def health(request: Request):
    payload = {
        "ok": True,
        "module": "ffd_resa",
        "menu_path": FFD_RESA_MENU_PATH,
        "orders_path": FFD_RESA_ORDERS_PATH,
        "allowed_origins": ALLOWED_ORIGINS,
        "require_api_key": FFD_RESA_REQUIRE_API_KEY,
        "admin_code_set": bool(FFD_ADMIN_CODE),
        "orders_count": len(_load_orders()),
    }
    return _with_cors(request, JSONResponse(payload))


@router.get("/menu")
def menu(
    request: Request,
    type: Optional[MenuType] = Query(default=None),
    x_api_key: Optional[str] = Header(default=None, alias="x-api-key"),
):
    _require_key(x_api_key)
    data = _load_menu()
    if type is None:
        payload = {"items": data, "count": {"BOISSON": len(data["BOISSON"]), "PLAT": len(data["PLAT"])}}
    else:
        payload = {"type": type, "items": data[type], "count": len(data[type])}
    return _with_cors(request, JSONResponse(payload))


# ─────────────────────────────────────────
# ORDER ENDPOINTS
# ─────────────────────────────────────────

@router.post("/order", status_code=201)
def create_order(body: OrderCreate, request: Request):
    """
    Crée une commande. Public (le widget client l'appelle directement).
    """
    now = datetime.now(timezone.utc).isoformat()
    order_id = _generate_id()

    new_order = {
        "id":             order_id,
        "table":          body.table.strip(),
        "name":           body.name.strip(),
        "phone":          body.phone.strip(),
        "method":         body.method,
        "items":          [i.model_dump() for i in body.items],
        "donation_cents": body.donation_cents,
        "asso_cents":     body.asso_cents,
        "total_cents":    body.total_cents,
        "status":         "pending",
        "ts":             now,
        "done_at":        None,
    }

    def _insert(orders: List[dict]) -> dict:
        orders.append(new_order)
        _save_orders(orders)
        return new_order

    created = _with_orders_lock(_insert)
    return _with_cors(request, JSONResponse({"ok": True, "order": created}, status_code=201))


@router.get("/orders")
def list_orders(
    request: Request,
    x_admin_code: Optional[str] = Header(default=None, alias="x-admin-code"),
):
    """
    Liste toutes les commandes.
    - Admin (x-admin-code correct) : voit les numéros de téléphone en clair.
    - Public : téléphone masqué.
    """
    orders = _load_orders()
    admin  = _is_admin(x_admin_code)

    if admin:
        visible = orders
    else:
        visible = [_public_order(o) for o in orders]

    payload = {
        "ok":     True,
        "admin":  admin,
        "count":  len(visible),
        "orders": visible,
    }
    return _with_cors(request, JSONResponse(payload))


@router.get("/orders/export")
def export_orders(
    request: Request,
    x_admin_code: Optional[str] = Header(default=None, alias="x-admin-code"),
):
    """
    Export CSV de toutes les commandes. Réservé à l'admin.
    """
    _require_admin(x_admin_code)
    orders = _load_orders()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL)

    # En-tête
    writer.writerow([
        "id", "table", "name", "phone", "method",
        "status", "ts", "done_at",
        "items_detail", "nb_articles",
        "sous_total_eur", "asso_eur", "don_eur", "total_eur",
    ])

    for o in orders:
        items_str = " | ".join(
            f"{it.get('qty', 1)}× {it.get('label', '?')} ({(it.get('price_cents', 0) / 100):.2f}€)"
            for it in (o.get("items") or [])
        )
        nb_articles = sum(it.get("qty", 1) for it in (o.get("items") or []))
        sous_total  = sum(it.get("qty", 1) * it.get("price_cents", 0) for it in (o.get("items") or []))

        writer.writerow([
            o.get("id", ""),
            o.get("table", ""),
            o.get("name", ""),
            o.get("phone", ""),
            o.get("method", ""),
            o.get("status", ""),
            o.get("ts", ""),
            o.get("done_at", ""),
            items_str,
            nb_articles,
            f"{sous_total / 100:.2f}".replace(".", ","),
            f"{o.get('asso_cents', 0) / 100:.2f}".replace(".", ","),
            f"{o.get('donation_cents', 0) / 100:.2f}".replace(".", ","),
            f"{o.get('total_cents', 0) / 100:.2f}".replace(".", ","),
        ])

    csv_bytes = output.getvalue().encode("utf-8-sig")  # BOM pour Excel français
    filename  = f"commandes_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"

    resp = Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
    return _with_cors(request, resp)


@router.get("/order/{order_id}")
def get_order(
    order_id: str,
    request: Request,
    x_admin_code: Optional[str] = Header(default=None, alias="x-admin-code"),
):
    orders = _load_orders()
    order  = next((o for o in orders if o.get("id") == order_id), None)
    if not order:
        raise HTTPException(status_code=404, detail=f"Commande {order_id} introuvable.")

    admin   = _is_admin(x_admin_code)
    payload = {"ok": True, "admin": admin, "order": order if admin else _public_order(order)}
    return _with_cors(request, JSONResponse(payload))


@router.patch("/order/{order_id}/status")
def update_status(
    order_id: str,
    body: StatusUpdate,
    request: Request,
    x_admin_code: Optional[str] = Header(default=None, alias="x-admin-code"),
):
    """
    Met à jour le statut d'une commande. Réservé à l'admin.
    """
    _require_admin(x_admin_code)

    def _update(orders: List[dict]) -> dict:
        for o in orders:
            if o.get("id") == order_id:
                o["status"]  = body.status
                o["done_at"] = datetime.now(timezone.utc).isoformat() if body.status == "done" else None
                _save_orders(orders)
                return o
        return {}

    updated = _with_orders_lock(_update)
    if not updated:
        raise HTTPException(status_code=404, detail=f"Commande {order_id} introuvable.")

    return _with_cors(request, JSONResponse({"ok": True, "order": updated}))


@router.delete("/order/{order_id}", status_code=200)
def delete_order(
    order_id: str,
    request: Request,
    x_admin_code: Optional[str] = Header(default=None, alias="x-admin-code"),
):
    """
    Supprime une commande définitivement. Réservé à l'admin.
    """
    _require_admin(x_admin_code)

    def _delete(orders: List[dict]) -> bool:
        for i, o in enumerate(orders):
            if o.get("id") == order_id:
                orders.pop(i)
                _save_orders(orders)
                return True
        return False

    found = _with_orders_lock(_delete)
    if not found:
        raise HTTPException(status_code=404, detail=f"Commande {order_id} introuvable.")

    return _with_cors(request, JSONResponse({"ok": True, "deleted": order_id}))


# ─────────────────────────────────────────
# TODO — Stripe (à ajouter après récupération des codes)
# ─────────────────────────────────────────
#
# @router.post("/checkout-session")
# def create_checkout_session(body: CheckoutBody, request: Request):
#     import stripe
#     stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
#     session = stripe.checkout.Session.create(
#         payment_method_types=["card"],
#         line_items=[...],
#         mode="payment",
#         success_url=os.getenv("STRIPE_SUCCESS_URL") + "?session_id={CHECKOUT_SESSION_ID}",
#         cancel_url=os.getenv("STRIPE_CANCEL_URL"),
#         metadata={"table": body.table, "name": body.name},
#     )
#     return _with_cors(request, JSONResponse({"checkout_url": session.url}))