import uuid
import httpx
import asyncio

from datetime import datetime
from fastapi import APIRouter, Cookie, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from typing import Optional

from app.meoles_site.config import meoles_settings
from app.meoles_site.cart import create_session, add_to_cart

router = APIRouter(prefix="/meoles/custom", tags=["meoles-custom"])

# ─── Stockage in-memory des demandes CUSTOM ───────────────────────────────────
# { custom_id: { prenom, nom, email, telephone, type, matiere, description } }
_custom_store: dict = {}

BREVO_URL = "https://api.brevo.com/v3/smtp/email"
ADMIN_EMAIL = "contact.meoles@gmail.com"
TEMPLATE_CUSTOM_CLIENT_ID = 3  # MEOLES CUSTOM - Confirmation d'inscription


def _brevo_headers() -> dict:
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "api-key": meoles_settings.BREVO_API_KEY_MEOLES,
    }


def _fourchette(type_bijou: str) -> str:
    if type_bijou == "Autre":
        return "Sur devis"
    return "180 – 220 €"


# ─── Schéma ───────────────────────────────────────────────────────────────────

class CustomSubmitRequest(BaseModel):
    prenom: str
    nom: str
    email: str
    telephone: Optional[str] = ""
    type: str
    matiere: str
    description: str


# ─── Mails ────────────────────────────────────────────────────────────────────

def _admin_custom_html(data: dict, custom_id: str) -> str:
    submitted_at = datetime.now().strftime("%d/%m/%Y à %H:%M")
    fourchette = _fourchette(data["type"])

    return f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;color:#1a1a1a;">
      <div style="background:#1a1a1a;padding:24px 32px;">
        <p style="color:#fff;font-size:20px;font-weight:900;font-style:italic;letter-spacing:4px;margin:0;">M E O L E S</p>
      </div>
      <div style="padding:32px;">
        <h2 style="margin-top:0;">✦ Nouvelle demande CUSTOM</h2>
        <p style="margin:4px 0;"><strong>Nom :</strong> {data['prenom']} {data['nom']}</p>
        <p style="margin:4px 0;"><strong>Email :</strong> {data['email']}</p>
        <p style="margin:4px 0;"><strong>Téléphone :</strong> {data.get('telephone') or '—'}</p>
        <p style="margin:4px 0;"><strong>Date :</strong> {submitted_at}</p>
        <p style="margin:4px 0;"><strong>Réf. demande :</strong> <code style="font-size:11px;">{custom_id}</code></p>

        <table style="width:100%;border-collapse:collapse;margin-top:24px;">
          <tr style="border-top:1px solid #e8e8e8;">
            <td style="padding:14px 0;width:35%;font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#aaa;">Type</td>
            <td style="padding:14px 0;font-size:14px;font-family:'Courier New',monospace;">{data['type']}</td>
          </tr>
          <tr style="border-top:1px solid #e8e8e8;">
            <td style="padding:14px 0;font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#aaa;">Matière</td>
            <td style="padding:14px 0;font-size:14px;font-family:'Courier New',monospace;">{data['matiere']}</td>
          </tr>
          <tr style="border-top:1px solid #e8e8e8;">
            <td style="padding:14px 0;font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#aaa;vertical-align:top;">Description</td>
            <td style="padding:14px 0;font-size:14px;font-family:'Courier New',monospace;line-height:1.7;">{data['description']}</td>
          </tr>
          <tr style="border-top:2px solid #1a1a1a;">
            <td style="padding:14px 0;font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;">Fourchette</td>
            <td style="padding:14px 0;font-size:16px;font-weight:900;">{fourchette}</td>
          </tr>
        </table>
      </div>
      <div style="background:#f9f9f9;padding:16px 32px;font-size:11px;color:#aaa;">
        Email automatique MEOLES · contact.meoles@gmail.com
      </div>
    </div>
    """


async def _mail_admin_custom(data: dict, custom_id: str):
    html = _admin_custom_html(data, custom_id)
    async with httpx.AsyncClient() as client:
        r = await client.post(BREVO_URL, headers=_brevo_headers(), json={
            "sender": {"name": "MEOLES", "email": ADMIN_EMAIL},
            "to": [{"email": ADMIN_EMAIL, "name": "Jvlien"}],
            "subject": f"✦ CUSTOM — {data['prenom']} {data['nom']} — {data['type']} {data['matiere']}",
            "htmlContent": html,
        }, timeout=10)
        r.raise_for_status()


async def _mail_client_custom(data: dict):
    fourchette = _fourchette(data["type"])
    async with httpx.AsyncClient() as client:
        r = await client.post(BREVO_URL, headers=_brevo_headers(), json={
            "sender":     {"name": "MEOLES", "email": ADMIN_EMAIL},
            "to":         [{"email": data["email"], "name": f"{data['prenom']} {data['nom']}"}],
            "replyTo":    {"email": ADMIN_EMAIL, "name": "Jvlien — MEOLES"},
            "templateId": TEMPLATE_CUSTOM_CLIENT_ID,
            "params": {
                "first_name":  data["prenom"],
                "type":        data["type"],
                "matiere":     data["matiere"],
                "telephone":   data.get("telephone") or "—",
                "description": data["description"],
                "fourchette":  fourchette,
            },
        }, timeout=10)
        r.raise_for_status()


# ─── Route ────────────────────────────────────────────────────────────────────

@router.post("/submit")
async def custom_submit(
    body: CustomSubmitRequest,
    response: Response,
    meoles_session: Optional[str] = Cookie(default=None),
):
    # Valider
    if not all([body.prenom, body.nom, body.email, body.type, body.matiere, body.description]):
        return JSONResponse(status_code=400, content={"error": "Champs manquants"})

    if len(body.description.strip()) < 20:
        return JSONResponse(status_code=400, content={"error": "Description trop courte"})

    # Créer un ID unique pour cette demande
    custom_id = str(uuid.uuid4())[:12].upper()

    # Stocker les données
    data = {
        "prenom":      body.prenom,
        "nom":         body.nom,
        "email":       body.email,
        "telephone":   body.telephone or "",
        "type":        body.type,
        "matiere":     body.matiere,
        "description": body.description,
        "custom_id":   custom_id,
        "created_at":  datetime.now().isoformat(),
    }
    _custom_store[custom_id] = data

    # Envoyer les deux mails en parallèle (sans bloquer la réponse)
    async def _send_mails():
        await asyncio.gather(
            _mail_admin_custom(data, custom_id),
            _mail_client_custom(data),
            return_exceptions=True,
        )
    asyncio.create_task(_send_mails())

    # Créer/récupérer la session panier et ajouter le produit
    if not meoles_session:
        meoles_session = create_session()

    try:
        add_to_cart(meoles_session, "meoles_custom", 1)
    except ValueError:
        pass

    # Poser le cookie session
    response.set_cookie(
        key="meoles_session",
        value=meoles_session,
        max_age=60 * 60 * 24,
        httponly=False,
        samesite="none",
        secure=True,
    )

    return {
        "status":     "ok",
        "custom_id":  custom_id,
        "session_id": meoles_session,
    }


# ─── Utilitaire : récupérer une demande par custom_id (usage interne webhook) ─

def get_custom_data(custom_id: str) -> Optional[dict]:
    return _custom_store.get(custom_id)