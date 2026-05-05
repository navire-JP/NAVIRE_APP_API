import uuid
from datetime import datetime, timezone
from typing import List

from sqlalchemy.orm import Session

from app.meoles_site.meoles_models import CartSession, CartItem

# ─── Catalogue produits — source unique de vérité ─────────────────────────────

PRODUCTS = {
    "meoles_custom":   {"name": "MEOLES CUSTOM",      "price_id": "price_1TEZ9ULbFEfgkQPquMlHQqrv", "price": 2500},
    "bague_fluid":     {"name": "Bague Fluid",         "price_id": "price_1TKKRWLbFEfgkQPqVnkZBBUE", "price": 5500},
    "collier_polaris": {"name": "Collier Polaris",     "price_id": "price_1SGc0kLbFEfgkQPqZqV6sbwe",  "price": 6500},
    "collier_silence": {"name": "Collier Silence",     "price_id": "price_1TKKUaLbFEfgkQPqv2DUD4HS",  "price": 9000},
    "tee_s":           {"name": "Silences Tee — S",    "price_id": "price_1TMBmwLbFEfgkQPqNNbXpFVq", "price": 2700},
    "tee_m":           {"name": "Silences Tee — M",    "price_id": "price_1TMBnxLbFEfgkQPqCVsqdmGW", "price": 2700},
    "tee_l":           {"name": "Silences Tee — L",    "price_id": "price_1TMBnPLbFEfgkQPqo6qj57Qq", "price": 2700},
}


# ─── Helpers internes ─────────────────────────────────────────────────────────

def _touch(session_row: CartSession, db: Session) -> None:
    """Met à jour updated_at manuellement (onupdate ne se déclenche pas sur flush partiel)."""
    session_row.updated_at = datetime.now(timezone.utc)
    db.add(session_row)


def _build_cart_response(session_row: CartSession) -> dict:
    cart_items = []
    total = 0
    for item in session_row.items:
        product = PRODUCTS.get(item.product_key)
        if not product:
            continue
        subtotal = product["price"] * item.quantity
        total += subtotal
        cart_items.append({
            "key":      item.product_key,
            "name":     product["name"],
            "price":    product["price"],
            "quantity": item.quantity,
            "subtotal": subtotal,
        })
    return {
        "session_id": session_row.session_id,
        "items":      cart_items,
        "total":      total,
        "count":      sum(i.quantity for i in session_row.items),
    }


def _get_or_none(session_id: str, db: Session) -> CartSession | None:
    return db.query(CartSession).filter(CartSession.session_id == session_id).first()


# ─── API publique ─────────────────────────────────────────────────────────────

def create_session(db: Session) -> str:
    session_id = str(uuid.uuid4())
    row = CartSession(session_id=session_id, status="active")
    db.add(row)
    db.commit()
    return session_id


def get_cart(session_id: str, db: Session) -> dict:
    row = _get_or_none(session_id, db)
    if not row:
        # Session inconnue : on en crée une vide plutôt que de planter
        row = CartSession(session_id=session_id, status="active")
        db.add(row)
        db.commit()
        db.refresh(row)
    return _build_cart_response(row)


def add_to_cart(session_id: str, product_key: str, db: Session, quantity: int = 1) -> dict:
    if product_key not in PRODUCTS:
        raise ValueError(f"Produit inconnu : {product_key}")

    row = _get_or_none(session_id, db)
    if not row:
        row = CartSession(session_id=session_id, status="active")
        db.add(row)
        db.flush()

    existing = next((i for i in row.items if i.product_key == product_key), None)
    if existing:
        existing.quantity += quantity
    else:
        db.add(CartItem(session_id=session_id, product_key=product_key, quantity=quantity))

    _touch(row, db)
    db.commit()
    db.refresh(row)
    return _build_cart_response(row)


def remove_from_cart(session_id: str, product_key: str, db: Session) -> dict:
    row = _get_or_none(session_id, db)
    if row:
        item = next((i for i in row.items if i.product_key == product_key), None)
        if item:
            db.delete(item)
            _touch(row, db)
            db.commit()
            db.refresh(row)
    return get_cart(session_id, db)


def update_quantity(session_id: str, product_key: str, quantity: int, db: Session) -> dict:
    if quantity <= 0:
        return remove_from_cart(session_id, product_key, db)

    row = _get_or_none(session_id, db)
    if not row:
        row = CartSession(session_id=session_id, status="active")
        db.add(row)
        db.flush()

    existing = next((i for i in row.items if i.product_key == product_key), None)
    if existing:
        existing.quantity = quantity
    else:
        db.add(CartItem(session_id=session_id, product_key=product_key, quantity=quantity))

    _touch(row, db)
    db.commit()
    db.refresh(row)
    return _build_cart_response(row)


def clear_cart(session_id: str, db: Session) -> None:
    row = _get_or_none(session_id, db)
    if row:
        row.status = "converted"
        for item in row.items:
            db.delete(item)
        _touch(row, db)
        db.commit()


def get_line_items(session_id: str, db: Session) -> List[dict]:
    """Retourne les line_items formatés pour Stripe Checkout."""
    row = _get_or_none(session_id, db)
    if not row:
        return []
    line_items = []
    for item in row.items:
        product = PRODUCTS.get(item.product_key)
        if product:
            line_items.append({"price": product["price_id"], "quantity": item.quantity})
    return line_items