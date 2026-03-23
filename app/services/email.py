"""
app/services/email.py
=====================
Wrapper Brevo (anciennement Sendinblue) pour l'envoi d'emails transactionnels.

Usage :
    from app.services.email import send_mail
    send_mail(
        to="user@example.com",
        subject="Bienvenue sur NAVIRE",
        html="<p>Bonjour !</p>",
    )

Variables d'environnement requises (Render) :
    BREVO_API_KEY       — clé API Brevo (Transactional > API Keys)
    BREVO_SENDER_EMAIL  — adresse expéditeur vérifiée sur Brevo (ex: no-reply@navire.fr)
    BREVO_SENDER_NAME   — nom affiché (ex: NAVIRE)
"""

from __future__ import annotations

import logging
import httpx

from app.core.config import BREVO_API_KEY, BREVO_SENDER_EMAIL, BREVO_SENDER_NAME

logger = logging.getLogger(__name__)

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


def send_mail(to: str, subject: str, html: str) -> bool:
    """
    Envoie un email via l'API Brevo.
    Retourne True si l'envoi a réussi, False sinon (sans lever d'exception).
    Les erreurs sont loggées mais ne font jamais planter l'appelant.
    """
    if not BREVO_API_KEY:
        logger.warning("BREVO_API_KEY manquant — email non envoyé à %s", to)
        return False

    payload = {
        "sender": {
            "email": BREVO_SENDER_EMAIL,
            "name": BREVO_SENDER_NAME,
        },
        "to": [{"email": to}],
        "subject": subject,
        "htmlContent": html,
    }

    try:
        response = httpx.post(
            BREVO_API_URL,
            json=payload,
            headers={
                "api-key": BREVO_API_KEY,
                "Content-Type": "application/json",
            },
            timeout=10.0,
        )
        if response.status_code not in (200, 201):
            logger.error(
                "Brevo error %s for %s : %s",
                response.status_code,
                to,
                response.text,
            )
            return False
        return True

    except Exception as exc:
        logger.error("Brevo send failed for %s : %s", to, exc)
        return False


# ============================================================
# Templates
# ============================================================

def mail_pending_subscription(email: str, plan: str, frontend_url: str) -> tuple[str, str]:
    """
    Retourne (subject, html) pour notifier un paiement en attente
    d'un compte non encore créé.
    """
    plan_label = "NAVIRE_AI+" if plan == "membre+" else "NAVIRE_AI"
    register_url = f"{frontend_url}/register?email={email}&pending_plan={plan}"

    subject = f"Votre abonnement {plan_label} est prêt — créez votre compte NAVIRE"
    html = f"""
<!DOCTYPE html>
<html lang="fr">
<head><meta charset="UTF-8"></head>
<body style="font-family: sans-serif; background: #0a0a0a; color: #f0f0f0; padding: 32px;">
  <div style="max-width: 520px; margin: auto; background: #141414; border-radius: 12px; padding: 32px;">
    <h1 style="color: #e63946; margin-top: 0;">NAVIRE</h1>
    <p>Bonjour,</p>
    <p>
      Votre paiement pour l'abonnement <strong>{plan_label}</strong> a bien été enregistré.
      Il ne vous reste plus qu'à créer votre compte NAVIRE pour activer votre accès.
    </p>
    <a href="{register_url}"
       style="display: inline-block; background: #e63946; color: #fff;
              padding: 12px 24px; border-radius: 8px; text-decoration: none;
              font-weight: bold; margin: 16px 0;">
      Créer mon compte NAVIRE
    </a>
    <p style="font-size: 0.85em; color: #888;">
      Ce lien est associé à l'adresse email utilisée lors du paiement ({email}).
      Utilisez la même adresse pour vous inscrire.
    </p>
    <p style="font-size: 0.85em; color: #888;">
      Si vous n'êtes pas à l'origine de ce paiement, contactez-nous.
    </p>
  </div>
</body>
</html>
"""
    return subject, html