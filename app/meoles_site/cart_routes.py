from fastapi import APIRouter, Cookie, Response, Depends, Header
from fastapi.responses import JSONResponse
from typing import Optional
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.meoles_site.cart import create_session, get_cart, add_to_cart, remove_from_cart, update_quantity

router = APIRouter(prefix="/meoles/cart", tags=["meoles-cart"])

COOKIE_NAME = "meoles_session"
COOKIE_MAX_AGE = 60 * 60 * 24  # 24h


class AddItemRequest(BaseModel):
    product_key: str
    quantity: int = 1


class UpdateItemRequest(BaseModel):
    product_key: str
    quantity: int


def _resolve_session(
    meoles_session: Optional[str],
    x_meoles_session: Optional[str],
    db: Session,
) -> str:
    """Cookie en priorité, sinon header, sinon nouvelle session."""
    sid = meoles_session or x_meoles_session
    if not sid:
        sid = create_session(db)
    return sid


def _cart_response(session_id: str, data: dict, response: Response):
    response.set_cookie(
        key=COOKIE_NAME,
        value=session_id,
        max_age=COOKIE_MAX_AGE,
        httponly=False,
        samesite="none",
        secure=True,
    )
    # Toujours renvoyer le session_id dans la réponse JSON
    data["session_id"] = session_id
    return data


@router.get("")
async def get_cart_route(
    response: Response,
    meoles_session: Optional[str] = Cookie(default=None),
    x_meoles_session: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    sid = _resolve_session(meoles_session, x_meoles_session, db)
    cart = get_cart(sid, db)
    return _cart_response(sid, cart, response)


@router.post("/add")
async def add_item(
    body: AddItemRequest,
    response: Response,
    meoles_session: Optional[str] = Cookie(default=None),
    x_meoles_session: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    sid = _resolve_session(meoles_session, x_meoles_session, db)
    try:
        cart = add_to_cart(sid, body.product_key, db, body.quantity)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    return _cart_response(sid, cart, response)


@router.post("/update")
async def update_item(
    body: UpdateItemRequest,
    response: Response,
    meoles_session: Optional[str] = Cookie(default=None),
    x_meoles_session: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    sid = _resolve_session(meoles_session, x_meoles_session, db)
    cart = update_quantity(sid, body.product_key, body.quantity, db)
    return _cart_response(sid, cart, response)


@router.delete("/remove/{product_key}")
async def remove_item(
    product_key: str,
    response: Response,
    meoles_session: Optional[str] = Cookie(default=None),
    x_meoles_session: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    sid = _resolve_session(meoles_session, x_meoles_session, db)
    cart = remove_from_cart(sid, product_key, db)
    return _cart_response(sid, cart, response)