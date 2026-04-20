import uuid
from typing import Dict, List

# Catalogue produits — source unique de vérité
PRODUCTS = {
    "meoles_custom":    {"name": "MEOLES CUSTOM",      "price_id": "price_1TEZ9ULbFEfgkQPquMlHQqrv", "price": 2500},
    "bague_fluid":      {"name": "Bague Fluid",         "price_id": "price_1TKKRWLbFEfgkQPqVnkZBBUE", "price": 5500},
    "collier_polaris":  {"name": "Collier Polaris",     "price_id": "price_1SGc0kLbFEfgkQPqZqV6sbwe",  "price": 6500},
    "collier_silence":  {"name": "Collier Silence",     "price_id": "price_1TKKUaLbFEfgkQPqv2DUD4HS",  "price": 9000},
    "tee_s": {"name": "Silences Tee — S", "price_id": "price_1TMBmwLbFEfgkQPqNNbXpFVq", "price": 2700},
    "tee_m": {"name": "Silences Tee — M", "price_id": "price_1TMBnxLbFEfgkQPqCVsqdmGW", "price": 2700},
    "tee_l": {"name": "Silences Tee — L", "price_id": "price_1TMBnPLbFEfgkQPqo6qj57Qq", "price": 2700},
}

# Stockage in-memory des paniers
# { session_id: { product_key: quantity } }
_carts: Dict[str, Dict[str, int]] = {}


def create_session() -> str:
    session_id = str(uuid.uuid4())
    _carts[session_id] = {}
    return session_id


def get_cart(session_id: str) -> dict:
    items = _carts.get(session_id, {})
    cart_items = []
    total = 0
    for key, qty in items.items():
        product = PRODUCTS.get(key)
        if product:
            subtotal = product["price"] * qty
            total += subtotal
            cart_items.append({
                "key": key,
                "name": product["name"],
                "price": product["price"],
                "quantity": qty,
                "subtotal": subtotal,
            })
    return {
        "session_id": session_id,
        "items": cart_items,
        "total": total,
        "count": sum(items.values()),
    }


def add_to_cart(session_id: str, product_key: str, quantity: int = 1) -> dict:
    if session_id not in _carts:
        _carts[session_id] = {}
    if product_key not in PRODUCTS:
        raise ValueError(f"Produit inconnu : {product_key}")
    _carts[session_id][product_key] = _carts[session_id].get(product_key, 0) + quantity
    return get_cart(session_id)


def remove_from_cart(session_id: str, product_key: str) -> dict:
    if session_id in _carts and product_key in _carts[session_id]:
        del _carts[session_id][product_key]
    return get_cart(session_id)


def update_quantity(session_id: str, product_key: str, quantity: int) -> dict:
    if quantity <= 0:
        return remove_from_cart(session_id, product_key)
    if session_id not in _carts:
        _carts[session_id] = {}
    _carts[session_id][product_key] = quantity
    return get_cart(session_id)


def clear_cart(session_id: str):
    _carts[session_id] = {}


def get_line_items(session_id: str) -> List[dict]:
    """Retourne les line_items formatés pour Stripe Checkout."""
    items = _carts.get(session_id, {})
    line_items = []
    for key, qty in items.items():
        product = PRODUCTS.get(key)
        if product:
            line_items.append({
                "price": product["price_id"],
                "quantity": qty,
            })
    return line_items