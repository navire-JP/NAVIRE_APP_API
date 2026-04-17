import asyncio
import httpx
import stripe

from datetime import datetime
from fastapi import APIRouter, Request, HTTPException, Cookie
from typing import Optional

from app.meoles_site.config import meoles_settings
from app.meoles_site.cart import get_line_items, get_cart, clear_cart

stripe.api_key = meoles_settings.STRIPE_SECRET_KEY_MEOLES

router = APIRouter(prefix="/meoles/checkout", tags=["meoles-checkout"])

# ─── Config Brevo ─────────────────────────────────────────────────────────────

BREVO_URL = "https://api.brevo.com/v3/smtp/email"
ADMIN_EMAIL = "contact.meoles@gmail.com"
TEMPLATE_CLIENT_ID = 2  # MEOLES - Confirmation commande

PRODUCT_IMAGES = {
    "bague_silence":   "https://i.imgur.com/6uVMxQ1.jpeg",
    "collier_silence": "https://i.imgur.com/6uVMxQ1.jpeg",
    "collier_polaris": "https://image.noelshack.com/fichiers/2026/13/5/1774638097-img-20251110-144538641-2.jpg",
    "meoles_custom":   "https://i.imgur.com/bLUkEs5.jpeg",
}

PRODUCT_LABELS = {
    "bague_silence":   "Bague Silence — Argent 925",
    "collier_silence": "Collier Silence — Argent 925",
    "collier_polaris": "Collier Polaris — Argent 925 (Précommande)",
    "meoles_custom":   "MEOLES CUSTOM — Inscription",
}


def _brevo_headers() -> dict:
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "api-key": meoles_settings.BREVO_API_KEY,
    }


def _get_address(addr: dict) -> dict:
    return {
        "line1":       addr.get("line1", ""),
        "line2":       addr.get("line2", ""),
        "postal_code": addr.get("postal_code", ""),
        "city":        addr.get("city", ""),
        "country":     addr.get("country", ""),
    }


def _build_item_params(items: list) -> dict:
    params = {}
    for i in range(3):
        idx = i + 1
        if i < len(items):
            item = items[i]
            key = item.get("product_key", "")
            params[f"item{idx}_name"]  = PRODUCT_LABELS.get(key, item.get("name", ""))
            params[f"item{idx}_image"] = PRODUCT_IMAGES.get(key, "")
            params[f"item{idx}_qty"]   = str(item.get("quantity", 1))
            params[f"item{idx}_price"] = f"{item.get('price', 0) * item.get('quantity', 1):.2f}"
        else:
            params[f"item{idx}_name"]  = ""
            params[f"item{idx}_image"] = ""
            params[f"item{idx}_qty"]   = ""
            params[f"item{idx}_price"] = ""
    return params


# ─── Mail admin (HTML inline) ─────────────────────────────────────────────────

def _admin_html(session: dict, items: list, total: float) -> str:
    customer = session.get("customer_details", {})
    name = customer.get("name", "—")
    email = customer.get("email", "—")
    session_id = session.get("id", "—")
    order_date = datetime.now().strftime("%d/%m/%Y à %H:%M")

    shipping = (session.get("shipping_details") or {}).get("address", {})
    billing = (customer.get("address") or {})

    def addr_block(a: dict) -> str:
        return "<br>".join(filter(None, [
            a.get("line1", ""), a.get("line2", ""),
            f"{a.get('postal_code', '')} {a.get('city', '')}".strip(),
            a.get("country", "")
        ])) or "—"

    rows = ""
    for item in items:
        key = item.get("product_key", "")
        label = PRODUCT_LABELS.get(key, item.get("name", "Article"))
        qty = item.get("quantity", 1)
        price = item.get("price", 0) * qty
        rows += (
            f"<tr>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #f0f0f0;font-size:13px;'>{label}</td>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #f0f0f0;text-align:center;font-size:13px;'>{qty}</td>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #f0f0f0;text-align:right;font-size:13px;'>{price:.2f} €</td>"
            f"</tr>"
        )

    return f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#1a1a1a;">
      <div style="background:#1a1a1a;padding:24px 32px;">
        <p style="color:#fff;font-size:20px;font-weight:900;font-style:italic;letter-spacing:4px;margin:0;">M E O L E S</p>
      </div>
      <div style="padding:32px;">
        <h2 style="margin-top:0;">🛍️ Nouvelle commande</h2>
        <p style="margin:4px 0;"><strong>Client :</strong> {name}</p>
        <p style="margin:4px 0;"><strong>Email :</strong> {email}</p>
        <p style="margin:4px 0;"><strong>Date :</strong> {order_date}</p>
        <p style="margin:4px 0;"><strong>Session :</strong> <code style="font-size:11px;">{session_id}</code></p>
        <table style="width:100%;border-collapse:collapse;margin-top:24px;background:#f9f9f9;">
          <tr>
            <td style="padding:16px;vertical-align:top;width:50%;">
              <p style="margin:0 0 8px;font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;">Livraison</p>
              <p style="margin:0;font-size:13px;line-height:1.6;">{addr_block(shipping)}</p>
            </td>
            <td style="padding:16px;vertical-align:top;border-left:1px solid #eee;">
              <p style="margin:0 0 8px;font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;">Facturation</p>
              <p style="margin:0;font-size:13px;line-height:1.6;">{addr_block(billing)}</p>
            </td>
          </tr>
        </table>
        <table style="width:100%;border-collapse:collapse;margin-top:24px;">
          <thead>
            <tr style="background:#f5f5f5;">
              <th style="padding:8px 12px;text-align:left;font-size:12px;">Produit</th>
              <th style="padding:8px 12px;text-align:center;font-size:12px;">Qté</th>
              <th style="padding:8px 12px;text-align:right;font-size:12px;">Prix</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
          <tfoot>
            <tr>
              <td colspan="2" style="padding:12px;font-weight:700;">Total</td>
              <td style="padding:12px;text-align:right;font-weight:700;font-size:16px;">{total:.2f} €</td>
            </tr>
          </tfoot>
        </table>
      </div>
      <div style="background:#f9f9f9;padding:16px 32px;font-size:11px;color:#aaa;">
        Email automatique MEOLES · contact.meoles@gmail.com
      </div>
    </div>
    """


async def _mail_admin(session: dict, items: list, total: float):
    customer = session.get("customer_details", {})
    name = customer.get("name", "—")

    async with httpx.AsyncClient() as client:
        r = await client.post(BREVO_URL, headers=_brevo_headers(), json={
            "sender": {"name": "MEOLES", "email": ADMIN_EMAIL},
            "to": [{"email": ADMIN_EMAIL, "name": "Jvlien"}],
            "subject": f"🛍️ Commande — {name} — {total:.2f} €",
            "htmlContent": _admin_html(session, items, total),
        }, timeout=10)
        r.raise_for_status()


# ─── Mail client (template Brevo #2) ─────────────────────────────────────────

async def _mail_client(session: dict, items: list, total: float, customer_name: str, customer_email: str):
    customer = session.get("customer_details", {})
    first_name = customer_name.split()[0] if customer_name else "cher client"
    order_date = datetime.now().strftime("%d %B %Y")
    session_id = session.get("id", "")
    order_id = session_id[-8:].upper() if session_id else "—"

    shipping = _get_address((session.get("shipping_details") or {}).get("address", {}))
    billing  = _get_address(customer.get("address") or {})

    params = {
        "first_name":           first_name,
        "total":                f"{total:.2f}",
        "order_id":             order_id,
        "order_date":           order_date,
        "shipping_name":        customer_name,
        "shipping_line1":       shipping["line1"],
        "shipping_line2":       shipping["line2"],
        "shipping_postal_code": shipping["postal_code"],
        "shipping_city":        shipping["city"],
        "shipping_country":     shipping["country"],
        "billing_name":         customer_name,
        "billing_line1":        billing["line1"],
        "billing_line2":        billing["line2"],
        "billing_postal_code":  billing["postal_code"],
        "billing_city":         billing["city"],
        "billing_country":      billing["country"],
        **_build_item_params(items),
    }

    async with httpx.AsyncClient() as client:
        r = await client.post(BREVO_URL, headers=_brevo_headers(), json={
            "sender":     {"name": "MEOLES", "email": ADMIN_EMAIL},
            "to":         [{"email": customer_email, "name": customer_name}],
            "replyTo":    {"email": ADMIN_EMAIL, "name": "Jvlien — MEOLES"},
            "templateId": TEMPLATE_CLIENT_ID,
            "params":     params,
        }, timeout=10)
        r.raise_for_status()


# ─── Routes ───────────────────────────────────────────────────────────────────

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
        customer_name  = session.get("customer_details", {}).get("name", "")

        if meoles_session_id:
            cart = get_cart(meoles_session_id)
            items = cart.get("items", [])
            total = cart.get("total", 0)

            tasks = [_mail_admin(session, items, total)]
            if customer_email and items:
                tasks.append(_mail_client(session, items, total, customer_name, customer_email))

            await asyncio.gather(*tasks, return_exceptions=True)
            clear_cart(meoles_session_id)

    return {"status": "ok"}