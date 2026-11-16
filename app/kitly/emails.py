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
    return send_result(to, subject, html, reply_to)["sent"]


def send_result(to: str, subject: str, html: str, reply_to: str | None = None) -> dict:
    """Comme send(), avec le détail de la réponse de Brevo (diagnostic du back-office)."""
    if not KITLY_BREVO_API_KEY:
        print(f"[kitly] ⚠️  Email non envoyé ({subject}) : aucune clé Brevo (KITLY_BREVO_API_KEY ou BREVO_API_KEY).")
        return {"sent": False, "detail": "Aucune clé Brevo configurée."}
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
            return {"sent": False, "detail": f"Brevo HTTP {r.status_code} : {r.text[:300]}"}
        return {"sent": True, "detail": f"Accepté par Brevo (expéditeur {KITLY_SENDER_EMAIL})."}
    except Exception as exc:  # réseau, timeout…
        print(f"[kitly] ⚠️  Envoi Brevo impossible (« {subject} ») : {exc}")
        return {"sent": False, "detail": f"Brevo injoignable : {exc}"}


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


def _order_lines(snap: dict) -> str:
    rows = [f"<b>{_e(snap.get('title'))}</b>" + (f" — {_e(snap.get('variant'))}" if snap.get("variant") else "")
            + (f" × {snap.get('quantity')}" if (snap.get("quantity") or 1) > 1 else "")]
    if snap.get("pickup"):
        rows.append("Retrait sur place" + (f" : {_e(snap.get('pickup_location'))}" if snap.get("pickup_location") else ""))
    elif snap.get("shipping_label"):
        rows.append(f"Livraison : {_e(snap['shipping_label'])} ({_money(snap.get('shipping_cents') or 0)})")
    addr = snap.get("shipping_address") or {}
    if addr:
        parts = [addr.get("name"), addr.get("line1"), addr.get("line2"),
                 " ".join(x for x in [addr.get("postal_code"), addr.get("city")] if x), addr.get("country")]
        rows.append("Adresse : " + ", ".join(_e(x) for x in parts if x))
    if snap.get("phone"):
        rows.append(f"Téléphone : {_e(snap['phone'])}")
    return "".join(f"<p style='margin:4px 0'>{r}</p>" for r in rows)


def _money(cents: int) -> str:
    return f"{cents / 100:.2f}".replace(".", ",").replace(",00", "") + " €"


def mail_order_seller(first_name: str, ref: str, snap: dict, amount_cents: int, commission_cents: int,
                      buyer_name: str, buyer_email: str) -> tuple[str, str]:
    fee = (f"<p>Commission Kitly (vente issue de la marketplace) : {_money(commission_cents)}. "
           f"Tu recevras {_money(amount_cents - commission_cents)}, hors frais Stripe.</p>") if commission_cents else ""
    todo = {"physical": "Prépare l'envoi (ou le retrait) et préviens ton client.",
            "digital": "Le lien de livraison lui a été envoyé automatiquement s'il est renseigné sur l'offre ; sinon, "
                       "envoie-lui le fichier en répondant à cet email.",
            "service": "Contacte ton client pour lancer la prestation."}.get(snap.get("kind"), "")
    body = (
        f"<p>Bonjour {_e(first_name) or ''},</p><p>Nouvelle commande payée sur ta vitrine Kitly 🎉</p>"
        f"<p><b>Référence :</b> {_e(ref)} · <b>Montant :</b> {_money(amount_cents)}</p>{_order_lines(snap)}"
        f"<p><b>Client :</b> {_e(buyer_name)} &lt;{_e(buyer_email)}&gt;</p>{fee}<p>{todo}</p>"
        "<p>Le paiement est versé sur ton compte Stripe ; le détail est dans ton espace de paiements.</p>"
    )
    return f"Nouvelle commande {ref} — {_money(amount_cents)}", layout("Nouvelle commande", body)


def mail_order_buyer(seller_name: str, ref: str, snap: dict, amount_cents: int, delivery_url: str,
                     seller_email: str) -> tuple[str, str]:
    deliver = ""
    if snap.get("kind") == "digital":
        deliver = (f'<p><a href="{_e(delivery_url)}" style="display:inline-block;background:#0a0a0a;color:#fff;'
                   f'padding:12px 20px;border-radius:99px;text-decoration:none;font-weight:700">Accéder à mon achat</a></p>'
                   if delivery_url else f"<p>{_e(seller_name)} va t'envoyer ton fichier par email.</p>")
    elif snap.get("kind") == "physical":
        deliver = f"<p>{_e(seller_name)} prépare ta commande et te tiendra informé(e) de l'envoi.</p>"
    elif snap.get("kind") == "service":
        deliver = f"<p>{_e(seller_name)} va te contacter pour démarrer la prestation.</p>"
    contact = f"<p>Une question ? Écris à {_e(seller_email)} (réponds simplement à cet email).</p>" if seller_email else ""
    body = (
        f"<p>Bonjour,</p><p>Merci pour ta commande auprès de <b>{_e(seller_name)}</b> : ton paiement est confirmé.</p>"
        f"<p><b>Référence :</b> {_e(ref)} · <b>Total payé :</b> {_money(amount_cents)}</p>{_order_lines(snap)}"
        f"{deliver}{contact}<p style='color:#6f6f6c;font-size:12px'>Vente conclue avec {_e(seller_name)}, "
        "selon ses conditions générales de vente acceptées lors de la commande. Kitly fournit la vitrine et le "
        "paiement sécurisé.</p>"
    )
    return f"Ta commande {ref} est confirmée", layout("Commande confirmée", body)


def mail_order_fulfilled(seller_name: str, ref: str, snap: dict, tracking: str, seller_email: str) -> tuple[str, str]:
    kind = snap.get("kind")
    if kind == "physical" and snap.get("pickup"):
        what, title = "Ta commande est prête : tu peux venir la retirer" + (
            f" ({_e(snap.get('pickup_location'))})" if snap.get("pickup_location") else "") + ".", "Commande prête"
    elif kind == "physical":
        what, title = "Ta commande a été expédiée.", "Commande expédiée"
    elif kind == "digital":
        what, title = "Ta commande a été livrée.", "Commande livrée"
    else:
        what, title = "Ta commande a été traitée.", "Commande traitée"
    track = ""
    if tracking:
        track = (f'<p><b>Suivi :</b> <a href="{_e(tracking)}" style="color:#0a0a0a">{_e(tracking)}</a></p>'
                 if tracking.startswith(("http://", "https://")) else f"<p><b>Numéro de suivi :</b> {_e(tracking)}</p>")
    body = (f"<p>Bonjour,</p><p>{what}</p><p><b>Référence :</b> {_e(ref)}</p>{_order_lines(snap)}{track}"
            f"<p>Une question ? Écris à {_e(seller_name)} : {_e(seller_email)} (ou réponds à cet email).</p>")
    return f"{title} — {ref}", layout(title, body)


def mail_profile_suspended(first_name: str, slug: str, reason: str, contact: str) -> tuple[str, str]:
    """Exposé des motifs d'une suspension (article 17 du règlement sur les services numériques)."""
    body = (
        f"<p>Bonjour {_e(first_name) or ''},</p>"
        f"<p>Ta vitrine <b>{_e(slug)}</b> a été suspendue par l'équipe Kitly : elle n'est plus visible publiquement "
        "ni dans la marketplace.</p>"
        f'<div style="border-left:3px solid #0a0a0a;padding:4px 0 4px 12px;margin:14px 0;white-space:pre-wrap"><b>Motif :</b> {_e(reason)}</div>'
        "<p>Tes données ne sont pas supprimées. Tu peux contester cette décision dans un délai de 6 mois en répondant "
        f"à cet email ou en écrivant à {_e(contact)}. Ta demande sera examinée par une personne de l'équipe.</p>"
    )
    return "Ta vitrine Kitly a été suspendue", layout("Vitrine suspendue", body)


def mail_profile_restored(first_name: str, slug: str) -> tuple[str, str]:
    body = (f"<p>Bonjour {_e(first_name) or ''},</p><p>La suspension de ta vitrine <b>{_e(slug)}</b> est levée. "
            "Elle est de nouveau visible si elle est publiée.</p>")
    return "Ta vitrine Kitly est rétablie", layout("Vitrine rétablie", body)


def mail_test() -> tuple[str, str]:
    body = ("<p>Cet email de test confirme que l'envoi des emails Kitly fonctionne : clé Brevo valide et expéditeur "
            f"accepté (<b>{_e(KITLY_SENDER_NAME)}</b> &lt;{_e(KITLY_SENDER_EMAIL)}&gt;).</p>")
    return "Test d'envoi Kitly", layout("Les emails Kitly fonctionnent", body)
