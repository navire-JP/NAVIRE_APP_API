import stripe
from fastapi import APIRouter, Request, HTTPException, Cookie
from typing import Optional

from app.meoles_site.config import meoles_settings
from app.meoles_site.cart import get_line_items, get_cart, clear_cart
from app.meoles_site.email_utils import send_order_confirmation

stripe.api_key = meoles_settings.STRIPE_SECRET_KEY_MEOLES

router = APIRouter(prefix="/meoles/checkout", tags=["meoles-checkout"])


@router.post("/create-session")
async def create_checkout_session(
    request: Request,
    meoles_session: Optional[str] = Cookie(default=None)
):
    if not meoles_session:
        raise HTTPException(400, "Panier introuvable")

    line_items = get_line_items(meoles_session)
    if not line_items:
        raise HTTPException(400, "Panier vide")

    shipping_rate = None
    try:
        body = await request.json()
        shipping_rate = body.get("shipping_rate")
    except Exception:
        pass

    try:
        session_params = dict(
            payment_method_types=["card"],
            line_items=line_items,
            mode="payment",
            success_url=f"{meoles_settings.MEOLES_FRONTEND_URL}?commande=success",
            cancel_url=f"{meoles_settings.MEOLES_FRONTEND_URL}?commande=cancel",
            metadata={"meoles_session_id": meoles_session},
            billing_address_collection="required",
            locale="fr",
        )
        if shipping_rate:
            session_params["shipping_options"] = [{"shipping_rate": shipping_rate}]
        else:
            session_params["shipping_address_collection"] = {"allowed_countries": ["FR", "BE", "CH", "LU"]}

        session = stripe.checkout.Session.create(**session_params)
        return {"checkout_url": session.url}
    except stripe.error.StripeError as e:
        raise HTTPException(400, str(e))


@router.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, meoles_settings.STRIPE_WEBHOOK_SECRET_MEOLES
        )
    except Exception:
        raise HTTPException(400, "Signature invalide")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        meoles_session_id = session.get("metadata", {}).get("meoles_session_id")
        customer_email = session.get("customer_details", {}).get("email")
        customer_name = session.get("customer_details", {}).get("name", "")

        if meoles_session_id:
            cart = get_cart(meoles_session_id)
            if customer_email and cart["items"]:
                send_order_confirmation(
                    to_email=customer_email,
                    customer_name=customer_name,
                    items=cart["items"],
                    total=cart["total"]
                )
            clear_cart(meoles_session_id)

    return {"status": "ok"}