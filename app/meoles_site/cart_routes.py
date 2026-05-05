from fastapi import APIRouter, Cookie, Response, Depends
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


def _cart_response(session_id: str, data: dict, response: Response):
    response.set_cookie(
        key=COOKIE_NAME,
        value=session_id,
        max_age=COOKIE_MAX_AGE,
        httponly=False,
        samesite="none",
        secure=True,
    )
    return data


@router.get("")
async def get_cart_route(
    response: Response,
    meoles_session: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
):
    if not meoles_session:
        meoles_session = create_session(db)

    cart = get_cart(meoles_session, db)
    return _cart_response(meoles_session, cart, response)


@router.post("/add")
async def add_item(
    body: AddItemRequest,
    response: Response,
    meoles_session: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
):
    if not meoles_session:
        meoles_session = create_session(db)

    try:
        cart = add_to_cart(meoles_session, body.product_key, db, body.quantity)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    return _cart_response(meoles_session, cart, response)


@router.post("/update")
async def update_item(
    body: UpdateItemRequest,
    response: Response,
    meoles_session: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
):
    if not meoles_session:
        meoles_session = create_session(db)

    cart = update_quantity(meoles_session, body.product_key, body.quantity, db)
    return _cart_response(meoles_session, cart, response)


@router.delete("/remove/{product_key}")
async def remove_item(
    product_key: str,
    response: Response,
    meoles_session: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
):
    if not meoles_session:
        meoles_session = create_session(db)

    cart = remove_from_cart(meoles_session, product_key, db)
    return _cart_response(meoles_session, cart, response)