import asyncio
import httpx
import stripe

from datetime import datetime
from fastapi import APIRouter, Request, HTTPException
from sqlalchemy.orm import Session

from app.meoles_site.config import meoles_settings

stripe.api_key = meoles_settings.STRIPE_SECRET_KEY_MEOLES

router = APIRouter(prefix="/meoles/checkout", tags=["meoles-checkout"])

BREVO_URL  = "https://api.brevo.com/v3/smtp/email"
ADMIN_EMAIL = "contact.meoles@gmail.com"
TEMPLATE_CLIENT_ID = 2

# ─── Catalogue local (price_id → label / image) ───────────────────────────────
# Utilisé pour enrichir les mails à partir des line_items Stripe

PRICE_META = {
    "price_1TKKRWLbFEfgkQPqVnkZBBUE": {
        "label": "Bague Silences — Argent 925",
        "image": "https://cdn.phototourl.com/free/2026-04-10-dcaf837b-e17f-4a24-8152-7a81c85d59b8.jpg",
        "key":   "bague_fluid",
    },
    "price_1TKKUaLbFEfgkQPqv2DUD4HS": {
        "label": "Collier Silences — Argent 925",
        "image": "https://cdn.phototourl.com/free/2026-04-10-7cbb53e3-179d-436f-83cf-ed18300595bf.jpg",
        "key":   "collier_silence",
    },
    "price_1SGc0kLbFEfgkQPqZqV6sbwe": {
        "label": "Collier Polaris — Argent 925 (Edition limitée)",
        "image": "https://image.noelshack.com/fichiers/2026/13/5/1774638097-img-20251110-144538641-2.jpg",
        "key":   "collier_polaris",
    },
    "price_1TMBmwLbFEfgkQPqNNbXpFVq": {
        "label": "T-Shirt Silences — S",
        "image": "https://image.noelshack.com/fichiers/2026/16/2/1776178739-img-20250915-124611456-1.jpg",
        "key":   "tee_s",
    },
    "price_1TMBnxLbFEfgkQPqCVsqdmGW": {
        "label": "T-Shirt Silences — M",
        "image": "https://image.noelshack.com/fichiers/2026/16/2/1776178739-img-20250915-124611456-1.jpg",
        "key":   "tee_m",
    },
    "price_1TMBnPLbFEfgkQPqo6qj57Qq": {
        "label": "T-Shirt Silences — L",
        "image": "https://image.noelshack.com/fichiers/2026/16/2/1776178739-img-20250915-124611456-1.jpg",
        "key":   "tee_l",
    },
}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _brevo_headers() -> dict:
    return {
        "accept":       "application/json",
        "content-type": "application/json",
        "api-key":      meoles_settings.BREVO_API_KEY_MEOLES,
    }


def _get_address(addr: dict) -> dict:
    return {
        "line1":       addr.get("line1", ""),
        "line2":       addr.get("line2", ""),
        "postal_code": addr.get("postal_code", ""),
        "city":        addr.get("city", ""),
        "country":     addr.get("country", ""),
    }


def _items_from_stripe(stripe_session_id: str) -> list[dict]:
    """
    Récupère les line_items depuis l'API Stripe et les normalise.
    Retourne une liste de dicts : {label, image, key, quantity, unit_price, subtotal}
    """
    try:
        li = stripe.checkout.Session.list_line_items(stripe_session_id, limit=10)
        items = []
        for line in li.data:
            price_id = line.price.id if line.price else ""
            meta = PRICE_META.get(price_id, {})
            qty  = line.quantity or 1
            unit = (line.price.unit_amount or 0)        # centimes
            items.append({
                "label":      meta.get("label", line.description or "Article"),
                "image":      meta.get("image", ""),
                "key":        meta.get("key", ""),
                "quantity":   qty,
                "unit_price": unit / 100,
                "subtotal":   unit * qty / 100,
            })
        return items
    except Exception as e:
        print(f"[webhook] Erreur list_line_items : {e}")
        return []


def _build_item_params(items: list) -> dict:
    """Formate les items pour le template Brevo (3 slots max)."""
    params = {}
    for i in range(3):
        idx = i + 1
        if i < len(items):
            item = items[i]
            params[f"item{idx}_name"]  = item["label"]
            params[f"item{idx}_image"] = item["image"]
            params[f"item{idx}_qty"]   = str(item["quantity"])
            params[f"item{idx}_price"] = f"{item['subtotal']:.2f}"
        else:
            params[f"item{idx}_name"]  = ""
            params[f"item{idx}_image"] = ""
            params[f"item{idx}_qty"]   = ""
            params[f"item{idx}_price"] = ""
    return params


def _addr_block(a: dict) -> str:
    return "<br>".join(filter(None, [
        a.get("line1", ""), a.get("line2", ""),
        f"{a.get('postal_code', '')} {a.get('city', '')}".strip(),
        a.get("country", ""),
    ])) or "—"


def _admin_html(session: dict, items: list, total: float) -> str:
    customer   = session.get("customer_details", {})
    name       = customer.get("name", "—")
    email      = customer.get("email", "—")
    session_id = session.get("id", "—")
    order_date = datetime.now().strftime("%d/%m/%Y à %H:%M")
    shipping   = (session.get("shipping_details") or {}).get("address", {})
    billing    = customer.get("address") or {}

    rows = ""
    for item in items:
        rows += (
            f"<tr>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #f0f0f0;font-size:13px;'>{item['label']}</td>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #f0f0f0;text-align:center;font-size:13px;'>{item['quantity']}</td>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #f0f0f0;text-align:right;font-size:13px;'>{item['subtotal']:.2f} €</td>"
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
        <p style="margin:4px 0;"><strong>Session Stripe :</strong> <code style="font-size:11px;">{session_id}</code></p>
        <table style="width:100%;border-collapse:collapse;margin-top:24px;background:#f9f9f9;">
          <tr>
            <td style="padding:16px;vertical-align:top;width:50%;">
              <p style="margin:0 0 8px;font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;">Livraison</p>
              <p style="margin:0;font-size:13px;line-height:1.6;">{_addr_block(shipping)}</p>
            </td>
            <td style="padding:16px;vertical-align:top;border-left:1px solid #eee;">
              <p style="margin:0 0 8px;font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;">Facturation</p>
              <p style="margin:0;font-size:13px;line-height:1.6;">{_addr_block(billing)}</p>
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
    name = session.get("customer_details", {}).get("name", "—")
    async with httpx.AsyncClient() as client:
        r = await client.post(BREVO_URL, headers=_brevo_headers(), json={
            "sender":      {"name": "MEOLES", "email": ADMIN_EMAIL},
            "to":          [{"email": ADMIN_EMAIL, "name": "Jvlien"}],
            "subject":     f"🛍️ Commande — {name} — {total:.2f} €",
            "htmlContent": _admin_html(session, items, total),
        }, timeout=10)
        r.raise_for_status()


async def _mail_client(session: dict, items: list, total: float, customer_name: str, customer_email: str):
    customer   = session.get("customer_details", {})
    first_name = customer_name.split()[0] if customer_name else "cher client"
    order_date = datetime.now().strftime("%d %B %Y")
    session_id = session.get("id", "")
    order_id   = session_id[-8:].upper() if session_id else "—"
    shipping   = _get_address((session.get("shipping_details") or {}).get("address", {}))
    billing    = _get_address(customer.get("address") or {})

    async with httpx.AsyncClient() as client:
        r = await client.post(BREVO_URL, headers=_brevo_headers(), json={
            "sender":     {"name": "MEOLES", "email": ADMIN_EMAIL},
            "to":         [{"email": customer_email, "name": customer_name}],
            "replyTo":    {"email": ADMIN_EMAIL, "name": "Jvlien — MEOLES"},
            "templateId": TEMPLATE_CLIENT_ID,
            "params": {
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
            },
        }, timeout=10)
        r.raise_for_status()


# ─── Webhook ──────────────────────────────────────────────────────────────────

@router.post("/webhook")
async def stripe_webhook(request: Request):
    payload    = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, meoles_settings.STRIPE_WEBHOOK_SECRET_MEOLES
        )
    except Exception:
        raise HTTPException(400, "Signature invalide")

    if event["type"] == "checkout.session.completed":
        session        = event["data"]["object"]
        stripe_sid     = session.get("id", "")
        customer_email = session.get("customer_details", {}).get("email")
        customer_name  = session.get("customer_details", {}).get("name", "")
        amount_total   = (session.get("amount_total") or 0) / 100   # centimes → euros

        # Récupération des produits achetés depuis l'API Stripe
        items = _items_from_stripe(stripe_sid)

        tasks = [_mail_admin(session, items, amount_total)]
        if customer_email:
            tasks.append(_mail_client(session, items, amount_total, customer_name, customer_email))

        await asyncio.gather(*tasks, return_exceptions=True)

    return {"status": "ok"}