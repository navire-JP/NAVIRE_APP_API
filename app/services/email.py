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

from app.core.config import (
    BREVO_API_KEY,
    BREVO_SENDER_EMAIL,
    BREVO_SENDER_NAME,
    DISCORD_GUILD_ID,
    DISCORD_PREPA_ADJURIS_CHANNEL_ID,
    DISCORD_SYNC_CHANNEL_ID,
    FRONTEND_URL,
)

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


def mail_prepa_adjuris_link_code(
    email: str, code: str, matiere_label: str, user_id: int | None = None
) -> tuple[str, str]:
    """
    Retourne (subject, html) pour envoyer le code de liaison Discord après
    une inscription Prép'AdJuris (utilisateur pas encore lié en discord_id).
    """
    identifiant_line = (
        f"<li>ton identifiant NAVIRE : <strong>{user_id}</strong></li>"
        if user_id is not None
        else ""
    )
    channel_url = None
    if DISCORD_GUILD_ID and DISCORD_PREPA_ADJURIS_CHANNEL_ID:
        channel_url = f"https://discord.com/channels/{DISCORD_GUILD_ID}/{DISCORD_PREPA_ADJURIS_CHANNEL_ID}"

    channel_block = ""
    if channel_url:
        channel_block = f"""
    <a href="{channel_url}"
       style="display: inline-block; background: #5865F2; color: #fff;
              padding: 12px 24px; border-radius: 8px; text-decoration: none;
              font-weight: bold; margin: 16px 0;">
      Rejoindre le channel Discord
    </a>"""

    subject = "Débloque ton accès Discord Prép'AdJuris"
    html = f"""
<!DOCTYPE html>
<html lang="fr">
<head><meta charset="UTF-8"></head>
<body style="font-family: sans-serif; background: #0a0a0a; color: #f0f0f0; padding: 32px;">
  <div style="max-width: 520px; margin: auto; background: #141414; border-radius: 12px; padding: 32px;">
    <h1 style="color: #e63946; margin-top: 0;">NAVIRE</h1>
    <p>Bonjour,</p>
    <p>
      Ton inscription à <strong>Prép'AdJuris — {matiere_label}</strong> est confirmée.
      Il ne reste plus qu'à lier ton compte NAVIRE sur Discord pour accéder au channel.
    </p>
    <p style="font-size: 1.4em; letter-spacing: 2px; font-weight: bold; text-align: center;
              background: #1f1f1f; border-radius: 8px; padding: 16px; margin: 16px 0;">
      {code}
    </p>
    <p>
      Rejoins le serveur Discord, clique sur le bouton <strong>« Lier mes comptes »</strong>,
      puis renseigne :
    </p>
    <ul style="line-height: 1.7;">
      {identifiant_line}
      <li>ton email : <strong>{email}</strong></li>
      <li>ce code : <strong>{code}</strong></li>
    </ul>{channel_block}
    <p style="font-size: 0.85em; color: #888;">
      Ce code est valable 7 jours et à usage unique. Tu peux aussi en générer un
      nouveau depuis ton profil, bouton « Connecter mes comptes ».
    </p>
  </div>
</body>
</html>
"""
    return subject, html

# ============================================================
# Liaison Discord — code d'authentification et confirmation
# ============================================================

def discord_sync_channel_url() -> str | None:
    """Lien profond vers #🔹connecter-mon-compte-navire, si configuré."""
    if DISCORD_GUILD_ID and DISCORD_SYNC_CHANNEL_ID:
        return f"https://discord.com/channels/{DISCORD_GUILD_ID}/{DISCORD_SYNC_CHANNEL_ID}"
    return None


def mail_discord_link_code(
    user_id: int,
    email: str,
    code: str,
    validity_label: str,
    context_label: str = "",
) -> tuple[str, str]:
    """
    (subject, html) du mail contenant un code de liaison Discord.
    Envoyé à l'achat ; le même code peut aussi être obtenu à tout moment
    depuis la page profil.
    """
    channel_url = discord_sync_channel_url()
    channel_block = ""
    if channel_url:
        channel_block = f"""
    <a href="{channel_url}"
       style="display: inline-block; background: #5865F2; color: #fff;
              padding: 12px 24px; border-radius: 8px; text-decoration: none;
              font-weight: bold; margin: 16px 0;">
      Ouvrir le salon Discord
    </a>"""

    intro = (
        f"Ton accès <strong>{context_label}</strong> est confirmé."
        if context_label
        else "Ton achat est confirmé."
    )

    subject = "Ton code pour lier ton compte NAVIRE à Discord"
    html = f"""
<!DOCTYPE html>
<html lang="fr">
<head><meta charset="UTF-8"></head>
<body style="font-family: sans-serif; background: #0a0a0a; color: #f0f0f0; padding: 32px;">
  <div style="max-width: 520px; margin: auto; background: #141414; border-radius: 12px; padding: 32px;">
    <h1 style="color: #e63946; margin-top: 0;">NAVIRE</h1>
    <p>Bonjour,</p>
    <p>
      {intro} Pour recevoir tes accès sur le serveur Discord, lie ton compte
      NAVIRE à ton compte Discord avec le code ci-dessous.
    </p>
    <p style="font-size: 1.6em; letter-spacing: 4px; font-weight: bold; text-align: center;
              background: #1f1f1f; border-radius: 8px; padding: 16px; margin: 16px 0;">
      {code}
    </p>
    <p>
      Dans le salon <strong>#connecter-mon-compte-navire</strong>, clique sur
      <strong>« Lier mes comptes »</strong> puis renseigne :
    </p>
    <ul style="line-height: 1.7;">
      <li>ton identifiant NAVIRE : <strong>{user_id}</strong></li>
      <li>ton email : <strong>{email}</strong></li>
      <li>ce code : <strong>{code}</strong></li>
    </ul>{channel_block}
    <p style="font-size: 0.85em; color: #888;">
      Ce code est valable {validity_label} et à usage unique. Tu peux aussi en
      générer un nouveau à tout moment depuis ton profil sur {FRONTEND_URL},
      bouton « Connecter mes comptes ».
    </p>
    <p style="font-size: 0.85em; color: #888;">
      Ne transmets ce code à personne : il permet de rattacher un compte Discord au tien.
    </p>
  </div>
</body>
</html>
"""
    return subject, html


def mail_discord_linked(
    username: str,
    discord_name: str,
    plan_label: str,
) -> tuple[str, str]:
    """
    (subject, html) de la confirmation envoyée une fois les comptes liés.
    Sert aussi d'alerte de sécurité : c'est par ce mail que l'utilisateur
    apprend qu'un compte Discord a été rattaché au sien.
    """
    subject = "Ton compte Discord est lié à NAVIRE"
    html = f"""
<!DOCTYPE html>
<html lang="fr">
<head><meta charset="UTF-8"></head>
<body style="font-family: sans-serif; background: #0a0a0a; color: #f0f0f0; padding: 32px;">
  <div style="max-width: 520px; margin: auto; background: #141414; border-radius: 12px; padding: 32px;">
    <h1 style="color: #e63946; margin-top: 0;">NAVIRE</h1>
    <p>Bonjour {username},</p>
    <p>
      Le compte Discord <strong>{discord_name}</strong> vient d'être lié à ton
      compte NAVIRE. Ton rôle sur le serveur correspond désormais à ton
      abonnement : <strong>{plan_label}</strong>.
    </p>
    <p>
      Il suivra automatiquement tes changements d'abonnement — rien à refaire
      lors d'un renouvellement, d'un changement de formule ou d'une résiliation.
    </p>
    <p style="font-size: 0.85em; color: #888;">
      Tu n'es pas à l'origine de cette liaison ? Change ton mot de passe sur
      {FRONTEND_URL} et préviens l'équipe : nous délierons le compte Discord.
    </p>
  </div>
</body>
</html>
"""
    return subject, html


def mail_password_changed(username: str, was_set: bool = False) -> tuple[str, str]:
    """
    (subject, html) de l'alerte envoyée après un changement de mot de passe
    depuis la page profil.

    was_set=True quand le compte n'avait pas de mot de passe jusque-là (compte
    Google/Facebook qui vient d'en définir un) : le message parle alors de
    création, pas de modification.
    """
    action = "défini" if was_set else "modifié"
    subject = (
        "Un mot de passe a été défini sur ton compte NAVIRE"
        if was_set
        else "Ton mot de passe NAVIRE a été modifié"
    )
    html = f"""
<!DOCTYPE html>
<html lang="fr">
<head><meta charset="UTF-8"></head>
<body style="font-family: sans-serif; background: #0a0a0a; color: #f0f0f0; padding: 32px;">
  <div style="max-width: 520px; margin: auto; background: #141414; border-radius: 12px; padding: 32px;">
    <h1 style="color: #e63946; margin-top: 0;">NAVIRE</h1>
    <p>Bonjour {username},</p>
    <p>
      Le mot de passe de ton compte NAVIRE vient d'être <strong>{action}</strong>
      depuis ta page profil.
    </p>
    <p>
      Tu peux désormais te connecter sur {FRONTEND_URL} avec ton adresse email
      et ce nouveau mot de passe.
    </p>
    <p style="font-size: 0.85em; color: #888;">
      Tu n'es pas à l'origine de ce changement ? Préviens l'équipe
      immédiatement : ton compte a probablement été compromis.
    </p>
  </div>
</body>
</html>
"""
    return subject, html


def mail_verification_code(code: str, username: str, ttl_minutes: int) -> tuple[str, str]:
    """
    (subject, html) du code à 6 chiffres qui prouve que l'adresse appartient
    bien à la personne en train de s'inscrire.
    """
    hello = f"Bonjour {username}," if username else "Bonjour,"

    subject = f"{code} est ton code de confirmation NAVIRE"
    html = f"""
<!DOCTYPE html>
<html lang="fr">
<head><meta charset="UTF-8"></head>
<body style="font-family: sans-serif; background: #0a0a0a; color: #f0f0f0; padding: 32px;">
  <div style="max-width: 520px; margin: auto; background: #141414; border-radius: 12px; padding: 32px;">
    <h1 style="color: #e63946; margin-top: 0;">NAVIRE</h1>
    <p>{hello}</p>
    <p>Voici le code qui confirme ton adresse email et termine ton inscription :</p>
    <p style="font-size: 2em; letter-spacing: 10px; font-weight: bold; text-align: center;
              background: #1f1f1f; border-radius: 8px; padding: 20px; margin: 20px 0;">
      {code}
    </p>
    <p style="font-size: 0.85em; color: #888;">
      Ce code est valable {ttl_minutes} minutes et ne sert qu'une fois.
    </p>
    <p style="font-size: 0.85em; color: #888;">
      Tu n'es pas à l'origine de cette inscription ? Ignore ce message :
      sans ce code, aucun compte ne sera créé avec ton adresse.
    </p>
  </div>
</body>
</html>
"""
    return subject, html
