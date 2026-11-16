"""
Emails transactionnels Kitly (Brevo), envoyés au nom de Kitly.

L'expéditeur KITLY_SENDER_EMAIL doit être validé dans Brevo (domaine kitly.fr
authentifié), sinon Brevo accepte l'appel puis ne délivre rien.
"""

from __future__ import annotations

import html as _html
import httpx

from app.kitly.config import (
    KITLY_BREVO_API_KEY,
    KITLY_FRONTEND_URL,
    KITLY_SENDER_EMAIL,
    KITLY_SENDER_NAME,
)

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


def send(to: str, subject: str, html: str, reply_to: str | None = None) -> bool:
    """Envoie un email. Ne lève jamais : renvoie False en cas d'échec (journalisé)."""
    if not KITLY_BREVO_API_KEY:
        print(f"[kitly] ⚠️  Email non envoyé ({subject}) : aucune clé Brevo (KITLY_BREVO_API_KEY ou BREVO_API_KEY).")
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
            # print : visible dans les logs Render (le logger n'y est pas branché).
            print(f"[kitly] ⚠️  Brevo a refusé l'email « {subject} » (HTTP {r.status_code}) : {r.text[:300]}")
            return False
        return True
    except Exception as exc:  # réseau, timeout…
        print(f"[kitly] ⚠️  Envoi Brevo impossible (« {subject} ») : {exc}")
        return False


# ============================================================
# Diagnostic de l'expéditeur
# ============================================================
# Piège classique : un expéditeur non validé chez Brevo. Brevo refuse l'envoi
# (ou l'accepte puis ne délivre rien) et l'inscription reste bloquée faute de
# code. Le diagnostic est fait au démarrage (en tâche de fond) et son résultat,
# sans aucun secret, est exposé par /kitly/health.

EMAIL_STATUS: dict = {"checked": False}


def check_sender() -> dict:
    """
    L'expéditeur Kitly est utilisable s'il figure parmi les expéditeurs actifs
    Brevo, ou si son domaine est authentifié chez Brevo. Ne lève jamais.
    """
    out = {"checked": True, "api_key_present": bool(KITLY_BREVO_API_KEY), "sender": KITLY_SENDER_EMAIL,
           "sender_name": KITLY_SENDER_NAME, "sender_ok": None, "detail": "", "usable": []}
    if not KITLY_BREVO_API_KEY:
        out.update(sender_ok=False, detail="Aucune clé Brevo configurée.")
        EMAIL_STATUS.clear(); EMAIL_STATUS.update(out)
        return out
    headers = {"api-key": KITLY_BREVO_API_KEY, "Accept": "application/json"}
    sender = (KITLY_SENDER_EMAIL or "").lower()
    domain = sender.split("@")[-1]
    try:
        r = httpx.get("https://api.brevo.com/v3/senders", headers=headers, timeout=10.0)
        if r.status_code != 200:
            out.update(sender_ok=False, detail=f"Clé Brevo refusée (HTTP {r.status_code}).")
        else:
            active = [s.get("email", "").lower() for s in (r.json() or {}).get("senders") or [] if s.get("active")]
            domains = []
            try:
                d = httpx.get("https://api.brevo.com/v3/senders/domains", headers=headers, timeout=10.0)
                if d.status_code == 200:
                    domains = [x.get("domain_name", "").lower() for x in (d.json() or {}).get("domains") or []
                               if x.get("authenticated")]
            except Exception:
                pass
            out["usable"] = active + [f"*@{x}" for x in domains]
            ok = sender in active or domain in domains
            out["sender_ok"] = ok
            out["detail"] = ("Expéditeur accepté par Brevo." if ok else
                             f"{KITLY_SENDER_EMAIL} n'est ni un expéditeur validé ni sur un domaine authentifié chez Brevo : "
                             "les emails Kitly (codes, demandes de contact) ne partent pas.")
    except Exception as exc:
        out["detail"] = f"Brevo injoignable : {exc}"
    EMAIL_STATUS.clear(); EMAIL_STATUS.update(out)
    return out


def public_status() -> dict:
    """Version publique (sans la liste des expéditeurs du compte Brevo)."""
    if not EMAIL_STATUS.get("checked"):
        return {"checked": False}
    return {k: EMAIL_STATUS.get(k) for k in ("checked", "api_key_present", "sender", "sender_ok", "detail")}


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
        f'<a href="{KITLY_FRONTEND_URL}/builder" style="color:#0a0a0a;font-weight:700">tableau de bord</a>.</p>'
    )
    return f"{label} — {name}", layout(label, body)
