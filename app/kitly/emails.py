"""
Emails transactionnels Kitly (Brevo), envoyés au nom de Kitly.

L'expéditeur KITLY_SENDER_EMAIL doit être validé dans Brevo (domaine kitly.fr
authentifié), sinon Brevo accepte l'appel puis ne délivre rien.
"""

from __future__ import annotations

import html as _html
import logging

import httpx

from app.kitly.config import (
    KITLY_BREVO_API_KEY,
    KITLY_FRONTEND_URL,
    KITLY_SENDER_EMAIL,
    KITLY_SENDER_NAME,
)

logger = logging.getLogger(__name__)
BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


def send(to: str, subject: str, html: str, reply_to: str | None = None) -> bool:
    """Envoie un email. Ne lève jamais : renvoie False en cas d'échec (loggué)."""
    if not KITLY_BREVO_API_KEY:
        logger.warning("[kitly] clé Brevo absente — email non envoyé à %s (%s)", to, subject)
        return False
    payload: dict = {
        "sender": {"email": KITLY_SENDER_EMAIL, "name": KITLY_SENDER_NAME},
        "to": [{"email": to}],
        "subject": subject,
        "htmlContent": html,
    }
    if reply_to:
        payload["replyTo"] = {"email": reply_to}
    try:
        r = httpx.post(
            BREVO_API_URL,
            json=payload,
            headers={"api-key": KITLY_BREVO_API_KEY, "Content-Type": "application/json"},
            timeout=10.0,
        )
        if r.status_code not in (200, 201, 202):
            logger.error("[kitly] Brevo HTTP %s pour %s : %s", r.status_code, to, r.text[:300])
            return False
        return True
    except Exception as exc:  # réseau, timeout…
        logger.error("[kitly] envoi Brevo impossible pour %s : %s", to, exc)
        return False


def _e(value: str) -> str:
    return _html.escape(value or "")


def layout(title: str, body_html: str) -> str:
    """Gabarit noir et blanc (tables + styles en ligne pour les clients mail)."""
    return f"""<!doctype html><html><body style="margin:0;background:#f5f5f4;font-family:Inter,Arial,sans-serif;color:#0a0a0a">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f5f5f4;padding:32px 12px">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:#ffffff;border:1px solid #e4e4e2;border-radius:16px">
<tr><td style="padding:28px 32px 0;font-size:20px;font-weight:900;letter-spacing:-0.5px">kitly.</td></tr>
<tr><td style="padding:20px 32px 8px;font-size:20px;font-weight:800">{_e(title)}</td></tr>
<tr><td style="padding:0 32px 28px;font-size:14px;line-height:1.6;color:#333">{body_html}</td></tr>
</table>
<p style="font-size:11px;color:#8a8a87;margin-top:16px">Kitly — Crée ta vitrine. Développe ton activité.</p>
</td></tr></table></body></html>"""


def _code_box(code: str) -> str:
    return (
        '<div style="font-size:30px;font-weight:900;letter-spacing:8px;border:1px solid #e4e4e2;'
        f'border-radius:12px;padding:14px 0;text-align:center;margin:18px 0">{_e(code)}</div>'
    )


def mail_verify_code(first_name: str, code: str, ttl: int) -> tuple[str, str]:
    body = (
        f"<p>Bonjour {_e(first_name) or ''},</p><p>Voici ton code pour confirmer ton adresse email :</p>"
        f"{_code_box(code)}<p>Il expire dans {ttl} minutes. Si tu n'as pas créé de compte Kitly, ignore cet email.</p>"
    )
    return "Ton code de vérification Kitly", layout("Confirme ton adresse", body)


def mail_reset_code(first_name: str, code: str, ttl: int) -> tuple[str, str]:
    body = (
        f"<p>Bonjour {_e(first_name) or ''},</p><p>Voici ton code pour choisir un nouveau mot de passe :</p>"
        f"{_code_box(code)}<p>Il expire dans {ttl} minutes. Si tu n'es pas à l'origine de cette demande, "
        "ton mot de passe actuel reste valable.</p>"
    )
    return "Réinitialisation de ton mot de passe Kitly", layout("Mot de passe oublié", body)


def mail_password_changed(first_name: str) -> tuple[str, str]:
    body = (
        f"<p>Bonjour {_e(first_name) or ''},</p><p>Le mot de passe de ton compte Kitly vient d'être modifié. "
        "Toutes tes autres sessions ont été déconnectées.</p>"
        "<p>Si ce n'est pas toi, réinitialise-le immédiatement depuis la page de connexion.</p>"
    )
    return "Ton mot de passe Kitly a été modifié", layout("Mot de passe modifié", body)


_LEAD_LABEL = {"contact": "Nouveau message", "quote": "Nouvelle demande de devis", "booking": "Nouvelle demande de rendez-vous"}


def mail_new_lead(
    seller_first_name: str, kind: str, name: str, email: str, message: str, offer_title: str | None
) -> tuple[str, str]:
    label = _LEAD_LABEL.get(kind, "Nouvelle demande")
    offer = f"<p><b>Offre :</b> {_e(offer_title)}</p>" if offer_title else ""
    body = (
        f"<p>Bonjour {_e(seller_first_name) or ''},</p><p>{label} reçue sur ta vitrine Kitly.</p>"
        f"<p><b>De :</b> {_e(name)} &lt;{_e(email)}&gt;</p>{offer}"
        f'<div style="border-left:3px solid #0a0a0a;padding:4px 0 4px 12px;margin:14px 0;white-space:pre-wrap">{_e(message)}</div>'
        f'<p>Réponds directement à cet email ou retrouve la demande dans ton '
        f'<a href="{KITLY_FRONTEND_URL}/dashboard" style="color:#0a0a0a;font-weight:700">tableau de bord</a>.</p>'
    )
    return f"{label} — {name}", layout(label, body)
