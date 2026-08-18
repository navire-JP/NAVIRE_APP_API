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
    EMAIL_LOGO_URL,
    FRONTEND_URL,
)

logger = logging.getLogger(__name__)

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


def mail_config() -> dict:
    """
    État de la configuration d'envoi, sans jamais exposer la clé elle-même.
    Sert au diagnostic depuis la console admin : un envoi qui ne part pas vient
    presque toujours d'ici (clé absente, expéditeur non validé chez Brevo).
    """
    return {
        "brevo_key_present": bool(BREVO_API_KEY),
        "sender_email": BREVO_SENDER_EMAIL,
        "sender_name": BREVO_SENDER_NAME,
        "logo_url": EMAIL_LOGO_URL,
    }


def send_mail_result(to: str, subject: str, html: str) -> dict:
    """
    Envoie un email via l'API Brevo et rend le détail de ce qui s'est passé :

        {"sent": bool, "detail": str, "message_id": str | None}

    C'est la version diagnostiquable de send_mail() : le booléen seul ne dit
    pas *pourquoi* rien n'est parti, ce qui rend un envoi manquant impossible
    à expliquer depuis la console. Ne lève jamais.

    Attention : `sent` signifie « Brevo a accepté le message », pas « la boîte
    du destinataire l'a affiché ». Un message accepté peut encore finir en
    indésirables, ou être rejeté par le serveur d'en face — message_id sert
    alors à le retrouver dans les logs Brevo.
    """
    if not BREVO_API_KEY:
        logger.warning("BREVO_API_KEY manquant — email non envoyé à %s", to)
        return {
            "sent": False,
            "detail": "BREVO_API_KEY absente de l'environnement du serveur.",
            "message_id": None,
        }

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
            return {
                "sent": False,
                "detail": f"Brevo HTTP {response.status_code} — {response.text[:300]}",
                "message_id": None,
            }

        message_id = None
        try:
            message_id = (response.json() or {}).get("messageId")
        except Exception:
            pass

        return {
            "sent": True,
            "detail": f"Accepté par Brevo (expéditeur {BREVO_SENDER_EMAIL}).",
            "message_id": message_id,
        }

    except Exception as exc:
        logger.error("Brevo send failed for %s : %s", to, exc)
        return {
            "sent": False,
            "detail": f"Appel Brevo impossible — {exc}",
            "message_id": None,
        }


def send_mail(to: str, subject: str, html: str) -> bool:
    """
    Envoie un email via l'API Brevo.
    Retourne True si l'envoi a réussi, False sinon (sans lever d'exception).
    Les erreurs sont loggées mais ne font jamais planter l'appelant.
    """
    return send_mail_result(to, subject, html)["sent"]



# ============================================================
# Gabarit commun — en-tête au logo NAVIRE
# ============================================================
# Tous les emails passent par layout() : un seul endroit à toucher pour
# changer l'identité visuelle, et surtout un logo présent partout au lieu du
# titre texte « NAVIRE » que chaque template redéfinissait dans son coin.
#
# Contraintes propres au mail, qui expliquent le HTML daté ci-dessous :
#   - tableaux plutôt que flex/grid : Outlook ne sait pas faire autrement ;
#   - styles en ligne uniquement : les <style> sont retirés par Gmail ;
#   - l'image porte un alt « NAVIRE » : la plupart des clients bloquent les
#     images par défaut, l'en-tête doit rester lisible sans elle.

def logo_url() -> str:
    """URL absolue du logo. Publique et sans jeton : c'est le client mail qui
    la télécharge, pas l'application."""
    return EMAIL_LOGO_URL


def layout(content_html: str, preheader: str = "") -> str:
    """
    Enveloppe un contenu dans le gabarit NAVIRE (en-tête logo + pied de page).

    `preheader` est le texte d'aperçu affiché par la boîte de réception à côté
    de l'objet ; masqué dans le corps du message.
    """
    preheader_block = ""
    if preheader:
        preheader_block = (
            '<div style="display:none;max-height:0;overflow:hidden;opacity:0;'
            'mso-hide:all;font-size:1px;line-height:1px;color:#0a0a0a;">'
            f"{preheader}</div>"
        )

    return f"""
<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="dark">
</head>
<body style="margin:0; padding:0; background:#0a0a0a; color:#f0f0f0;
             font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;">
  {preheader_block}
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background:#0a0a0a; padding:32px 16px;">
    <tr>
      <td align="center">
        <table role="presentation" width="520" cellpadding="0" cellspacing="0" border="0"
               style="max-width:520px; width:100%;">

          <!-- En-tête : logo + nom -->
          <tr>
            <td align="center" style="padding-bottom:20px;">
              <img src="{EMAIL_LOGO_URL}" alt="NAVIRE" width="56" height="56"
                   style="display:block; width:56px; height:56px; border:0; outline:none;
                          text-decoration:none; margin:0 auto 10px;">
              <div style="font-size:15px; font-weight:700; letter-spacing:5px;
                          color:#f0f0f0; text-transform:uppercase;">NAVIRE</div>
            </td>
          </tr>

          <!-- Corps -->
          <tr>
            <td style="background:#141414; border-radius:12px; padding:32px;
                       font-size:15px; line-height:1.6; color:#f0f0f0;">
              {content_html}
            </td>
          </tr>

          <!-- Pied de page -->
          <tr>
            <td align="center" style="padding-top:20px; font-size:11px; color:#666;
                                      line-height:1.6;">
              <a href="{FRONTEND_URL}" style="color:#888; text-decoration:none;">{FRONTEND_URL}</a><br>
              Email automatique — merci de ne pas y répondre.
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


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
    html = layout(f"""
    <p style="margin-top:0;">Bonjour,</p>
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
""", preheader=f"Votre abonnement {plan_label} vous attend.")
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
    html = layout(f"""
    <p style="margin-top:0;">Bonjour,</p>
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
""", preheader=f"Ton code de liaison Discord : {code}")
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
    html = layout(f"""
    <p style="margin-top:0;">Bonjour,</p>
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
""", preheader=f"Ton code de liaison Discord : {code}")
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
    html = layout(f"""
    <p style="margin-top:0;">Bonjour {username},</p>
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
""", preheader=f"{discord_name} est désormais lié à ton compte NAVIRE.")
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
    html = layout(f"""
    <p style="margin-top:0;">Bonjour {username},</p>
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
""", preheader=f"Ton mot de passe a été {action}.")
    return subject, html


def mail_verification_code(code: str, username: str, ttl_minutes: int) -> tuple[str, str]:
    """
    (subject, html) du code à 6 chiffres qui prouve que l'adresse appartient
    bien à la personne en train de s'inscrire.
    """
    hello = f"Bonjour {username}," if username else "Bonjour,"

    subject = f"{code} est ton code de confirmation NAVIRE"
    html = layout(f"""
    <p style="margin-top:0;">{hello}</p>
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
""", preheader=f"{code} — valable {ttl_minutes} minutes.")
    return subject, html


# ============================================================
# Catalogue des emails — aperçu et envoi de test
# ============================================================
# Sert la commande `/mails` de la console admin : lister ce que l'application
# sait envoyer, et se l'envoyer à soi-même pour vérifier le rendu réel dans une
# boîte (le logo, surtout, qui dépend d'une URL publique joignable depuis le
# client mail). Les données sont fictives et n'écrivent rien en base.

EMAIL_CATALOG: dict[str, dict] = {
    "verification_code": {
        "label": "Code de confirmation (inscription)",
        "trigger": "POST /auth/request-code",
        "render": lambda: mail_verification_code("428913", "Jean", 15),
    },
    "pending_subscription": {
        "label": "Paiement sans compte — invitation à s'inscrire",
        "trigger": "Webhook Stripe, aucun compte pour cette adresse",
        "render": lambda: mail_pending_subscription(
            "eleve@example.com", "membre+", FRONTEND_URL
        ),
    },
    "discord_link_code": {
        "label": "Code de liaison Discord (après achat)",
        "trigger": "Achat NAVIRE AI / AI+ / PREPASSERELLE",
        "render": lambda: mail_discord_link_code(
            user_id=42,
            email="eleve@example.com",
            code="A7K2P9",
            validity_label="7 jours",
            context_label="NAVIRE AI+",
        ),
    },
    "prepa_adjuris_link_code": {
        "label": "Code de liaison Discord — Prép'AdJuris",
        "trigger": "Achat Prép'AdJuris",
        "render": lambda: mail_prepa_adjuris_link_code(
            "eleve@example.com", "A7K2P9", "L2 — Droit administratif", user_id=42
        ),
    },
    "discord_linked": {
        "label": "Confirmation de liaison Discord",
        "trigger": "Le bot valide le code de liaison",
        "render": lambda: mail_discord_linked("Jean", "jean#4021", "NAVIRE AI+"),
    },
    "password_changed": {
        "label": "Mot de passe modifié",
        "trigger": "POST /users/me/password (compte avec mot de passe)",
        "render": lambda: mail_password_changed("Jean"),
    },
    "password_set": {
        "label": "Mot de passe défini (compte Google/Facebook)",
        "trigger": "POST /users/me/password (compte sans mot de passe)",
        "render": lambda: mail_password_changed("Jean", was_set=True),
    },
}


def catalog() -> list[dict]:
    """Liste des emails connus, avec leur objet réel (données d'exemple)."""
    rows = []
    for key, entry in EMAIL_CATALOG.items():
        subject, _ = entry["render"]()
        rows.append({
            "key": key,
            "label": entry["label"],
            "trigger": entry["trigger"],
            "subject": subject,
        })
    return rows


def render_sample(key: str) -> tuple[str, str]:
    """(subject, html) d'un email du catalogue, rempli de données fictives."""
    entry = EMAIL_CATALOG.get(key)
    if not entry:
        raise KeyError(key)
    return entry["render"]()
