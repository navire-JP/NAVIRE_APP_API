"""
app/services/mailing.py
=======================
Mails rédigés dans la console admin et envoyés par Brevo à des catégories de
destinataires, dans le gabarit NAVIRE, signés par l'admin qui envoie.

Trois briques :

  1. Les catégories (`segments`) : qui reçoit. Une clé par catégorie, union
     des catégories choisies, une seule fois par adresse.
  2. La mise en forme (`render_body`) : l'admin écrit du texte simple, rendu en
     HTML de mail (paragraphes, gras, liens, listes, boutons).
  3. L'envoi (`send_campaign`) : lots de BATCH_SIZE destinataires par appel
     Brevo (messageVersions), PJ transmises une fois par lot.

Variable de personnalisation : {prenom} dans le texte ou l'objet devient le
prénom du destinataire (prénom ambassadeur/professeur s'il est connu, sinon
son pseudo NAVIRE).
"""

from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from html import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.prepa_adjuris_config import (
    PREPA_PRICES,
    matiere_label,
    matiere_niveau,
)
from app.db.models import (
    Ambassador,
    MailCampaign,
    PrepaAdjurisClasse,
    PrepaAdjurisProfesseur,
    User,
)
from app.services import email as mail
from app.services import professeurs as prof_svc

BATCH_SIZE = 100
# Pièces jointes : Brevo refuse au-delà d'environ 10 Mo par message ; on garde
# de la marge pour l'encodage base64 et le corps du mail.
MAX_ATTACHMENTS_BYTES = 8 * 1024 * 1024
MAX_ATTACHMENTS = 10
# Extensions acceptées par Brevo (sous-ensemble courant de leur liste).
ALLOWED_EXTENSIONS = {
    "pdf", "doc", "docx", "odt", "rtf", "txt", "csv",
    "xls", "xlsx", "ods", "ppt", "pptx",
    "png", "jpg", "jpeg", "gif", "bmp",
    "zip", "ics",
}

NIVEAUX = ("L1", "L2", "L3", "M1")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ============================================================
# 1. Catégories de destinataires
# ============================================================
# Chaque fonction rend {email_minuscule: prenom}. Le prénom sert à {prenom}.

def _ajouter(out: dict[str, str], email: str | None, prenom: str | None) -> None:
    e = (email or "").strip().lower()
    if not e or "@" not in e:
        return
    p = (prenom or "").strip()
    # Un prénom réel (ambassadeur, professeur) l'emporte sur un pseudo.
    if e not in out or (p and not out[e]):
        out[e] = p


def _users(db: Session, *conds) -> dict[str, str]:
    out: dict[str, str] = {}
    q = select(User.email, User.username)
    for c in conds:
        q = q.where(c)
    for email, username in db.execute(q):
        _ajouter(out, email, username)
    return out


def _ambassadeurs(db: Session, status: str) -> dict[str, str]:
    out: dict[str, str] = {}
    rows = db.execute(
        select(User.email, Ambassador.prenom)
        .join(User, User.id == Ambassador.user_id)
        .where(Ambassador.status == status)
    )
    for email, prenom in rows:
        _ajouter(out, email, prenom)
    return out


def _professeurs(db: Session) -> dict[str, str]:
    out: dict[str, str] = {}
    rows = db.execute(
        select(User.email, PrepaAdjurisProfesseur.prenom, User.username)
        .join(User, User.id == PrepaAdjurisProfesseur.user_id)
        .where(PrepaAdjurisProfesseur.status == "active")
    )
    for email, prenom, username in rows:
        _ajouter(out, email, prenom or username)
    return out


def _emails_inscriptions(db: Session, lignes) -> dict[str, str]:
    """Adresse d'un étudiant : celle de son compte, sinon celle du paiement."""
    out: dict[str, str] = {}
    ids = {e.user_id for e in lignes if e.user_id}
    comptes = {
        uid: (email, username)
        for uid, email, username in db.execute(
            select(User.id, User.email, User.username).where(User.id.in_(ids))
        )
    } if ids else {}
    for e in lignes:
        if e.user_id and e.user_id in comptes:
            _ajouter(out, *comptes[e.user_id])
        else:
            _ajouter(out, e.email, "")
    return out


def _etudiants_matiere(db: Session, matiere_key: str, par_matiere: dict) -> dict[str, str]:
    lignes = [
        prof_svc.principale(l)
        for _, l in prof_svc.eleves_de_la_matiere(db, matiere_key, par_matiere)
    ]
    return _emails_inscriptions(db, lignes)


def _etudiants_groupe(db: Session, classe_id: int, par_matiere: dict) -> dict[str, str]:
    classe = db.get(PrepaAdjurisClasse, classe_id)
    if classe is None:
        return {}
    return _emails_inscriptions(db, prof_svc.inscriptions_du_groupe(db, classe, par_matiere))


def segment_label(db: Session, key: str) -> str | None:
    """Libellé d'une clé de catégorie, None si la clé est inconnue."""
    fixes = {k: v for k, v, _ in SEGMENTS_FIXES}
    if key in fixes:
        return fixes[key]
    kind, _, arg = key.partition(":")
    if kind == "niveau" and arg in NIVEAUX:
        return f"Prép'AdJuris — tous les {arg}"
    if kind == "matiere" and arg in PREPA_PRICES:
        return f"Prép'AdJuris — {matiere_label(arg)}"
    if kind == "groupe" and arg.isdigit():
        c = db.get(PrepaAdjurisClasse, int(arg))
        return f"Groupe — {c.nom}" if c else None
    return None


# (clé, libellé, fonction) — l'ordre est celui de la console.
SEGMENTS_FIXES = [
    ("tous", "Tous les comptes NAVIRE", lambda db: _users(db)),
    ("newsletter", "Inscrits à la newsletter", lambda db: _users(db, User.newsletter_opt_in == True)),  # noqa: E712
    ("abonnes", "Abonnés NAVIRE AI (membre, membre+)", lambda db: _users(db, User.plan.in_(("membre", "membre+")))),
    ("ambassadeurs", "Ambassadeurs", lambda db: _ambassadeurs(db, "approved")),
    ("ambassadeurs_candidats", "Candidats ambassadeurs (en attente)", lambda db: _ambassadeurs(db, "pending")),
    ("professeurs", "Professeurs", _professeurs),
    ("adjuris", "Prép'AdJuris — tous les étudiants", None),
    ("admins", "Admins", lambda db: _users(db, User.is_admin == True)),  # noqa: E712
]


def resolve_segment(db: Session, key: str, par_matiere: dict | None = None) -> dict[str, str]:
    """Destinataires d'une catégorie. Lève ValueError si la clé est inconnue."""
    par_matiere = par_matiere if par_matiere is not None else prof_svc.groupes_par_matiere(db)
    for k, _, fn in SEGMENTS_FIXES:
        if k == key and fn is not None:
            return fn(db)
    kind, _, arg = key.partition(":")
    matieres: list[str] = []
    if key == "adjuris":
        matieres = list(PREPA_PRICES)
    elif kind == "niveau" and arg in NIVEAUX:
        matieres = [m for m in PREPA_PRICES if matiere_niveau(m) == arg]
    elif kind == "matiere" and arg in PREPA_PRICES:
        matieres = [arg]
    elif kind == "groupe" and arg.isdigit():
        return _etudiants_groupe(db, int(arg), par_matiere)
    else:
        raise ValueError(f"Catégorie inconnue : {key}")
    out: dict[str, str] = {}
    for m in matieres:
        for e, p in _etudiants_matiere(db, m, par_matiere).items():
            _ajouter(out, e, p)
    return out


def resolve_recipients(db: Session, keys: list[str]) -> dict[str, str]:
    """Union des catégories, une fois par adresse : {email: prenom}."""
    par_matiere = prof_svc.groupes_par_matiere(db)
    out: dict[str, str] = {}
    for k in dict.fromkeys(keys):
        for e, p in resolve_segment(db, k, par_matiere).items():
            _ajouter(out, e, p)
    return out


def list_segments(db: Session) -> list[dict]:
    """Toutes les catégories proposées par la console, avec leur effectif."""
    par_matiere = prof_svc.groupes_par_matiere(db)
    rows: list[dict] = []

    def add(key: str, label: str, groupe: str):
        rows.append({
            "key": key,
            "label": label,
            "groupe": groupe,
            "count": len(resolve_segment(db, key, par_matiere)),
        })

    for k, label, _ in SEGMENTS_FIXES:
        add(k, label, "Général" if k not in ("adjuris",) else "Prép'AdJuris")
    for n in NIVEAUX:
        if any(matiere_niveau(m) == n for m in PREPA_PRICES):
            add(f"niveau:{n}", f"Tous les {n}", "Prép'AdJuris")
    for m in PREPA_PRICES:
        add(f"matiere:{m}", matiere_label(m), "Matières")
    for classes in par_matiere.values():
        for c in classes:
            add(f"groupe:{c.id}", c.nom, "Groupes")
    return rows


# ============================================================
# 2. Mise en forme
# ============================================================
# Syntaxe volontairement réduite, pensée pour une zone de texte :
#
#   ligne vide            → nouveau paragraphe
#   ## Titre              → intertitre
#   - élément             → liste à puces (lignes consécutives)
#   **gras**  *italique*  → gras, italique
#   [texte](https://…)    → lien
#   [bouton: Texte](https://…) → bouton rouge NAVIRE
#   https://…             → lien cliquable
#   {prenom}              → prénom du destinataire

PRENOM_TAG = "{{ params.prenom }}"
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_URL = re.compile(r"(?<![\"'=\w])(https?://[^\s<]+[^\s<.,;:!?)\]])")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITAL = re.compile(r"(?<![\*\w])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\*\w])")
LINK_STYLE = "color:#e63946; text-decoration:underline;"


def _neutraliser_gabarit(s: str) -> str:
    """Le texte de l'admin ne doit jamais être lu comme du gabarit Brevo
    ({{ … }}, {% … %}) : un « {{ » tapé par erreur casserait tout le lot."""
    return (s.replace("{{", "{ {").replace("}}", "} }")
             .replace("{%", "{ %").replace("%}", "% }"))


def _inline(text: str) -> str:
    """Échappe puis applique gras, italique, liens et boutons sur une ligne."""
    liens: list[str] = []

    def garder(html: str) -> str:
        liens.append(html)
        return f"\x00{len(liens) - 1}\x00"

    def lien(m: re.Match) -> str:
        label, url = m.group(1), m.group(2)
        if label.lower().startswith("bouton:"):
            return garder(mail._bouton(escape(url, quote=True), escape(label[7:].strip())))
        return garder(f'<a href="{escape(url, quote=True)}" style="{LINK_STYLE}">{escape(label)}</a>')

    text = _LINK.sub(lien, text)
    text = _URL.sub(lambda m: garder(
        f'<a href="{escape(m.group(1), quote=True)}" style="{LINK_STYLE}">{escape(m.group(1))}</a>'
    ), text)
    text = escape(text, quote=False)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _ITAL.sub(r"<em>\1</em>", text)
    text = re.sub("\x00(\\d+)\x00", lambda m: liens[int(m.group(1))], text)
    return text.replace("{prenom}", PRENOM_TAG)


def render_body(body: str) -> str:
    """Texte de la console → HTML du corps du mail (styles en ligne)."""
    text = _neutraliser_gabarit((body or "").replace("\r\n", "\n").strip())
    out: list[str] = []

    def marge() -> str:
        return ' style="margin-top:0;"' if not out else ""

    for bloc in (b for b in re.split(r"\n\s*\n", text) if b.strip()):
        para: list[str] = []
        puces: list[str] = []

        def fermer():
            if para:
                out.append(f"<p{marge()}>" + "<br>".join(para) + "</p>")
                para.clear()
            if puces:
                items = "".join(f'<li style="margin:4px 0;">{p}</li>' for p in puces)
                m = "margin-top:0; " if not out else ""
                out.append(f'<ul style="{m}padding-left:20px;">{items}</ul>')
                puces.clear()

        for ligne in (l.rstrip() for l in bloc.split("\n")):
            nue = ligne.lstrip()
            if nue.startswith("## "):
                fermer()
                m = "margin-top:0; " if not out else ""
                out.append(
                    f'<h2 style="{m}margin-bottom:8px; font-size:17px; font-weight:700; '
                    f'color:#f0f0f0;">{_inline(nue[3:].strip())}</h2>'
                )
            elif nue.startswith(("- ", "• ")):
                if para:
                    out.append(f"<p{marge()}>" + "<br>".join(para) + "</p>")
                    para.clear()
                puces.append(_inline(nue[2:].strip()))
            else:
                if puces:
                    fermer()
                para.append(_inline(ligne))
        fermer()
    return "\n".join(out)


def render_subject(subject: str) -> str:
    return _neutraliser_gabarit((subject or "").strip()).replace("{prenom}", PRENOM_TAG)


def _avatar(url: str | None) -> str | None:
    """Photo carrée, centrée sur le visage, quand elle vient de Cloudinary."""
    if not url:
        return None
    if "res.cloudinary.com" in url and "/upload/" in url:
        return url.replace("/upload/", "/upload/c_fill,g_face,w_96,h_96/", 1)
    return url


def signature_html(admin: User | None) -> str:
    """Signature en bas du mail : photo de profil + pseudo de l'admin."""
    if admin is None:
        return ""
    nom = escape(admin.username or "L'équipe NAVIRE")
    photo = _avatar(admin.avatar_url)
    if photo:
        visuel = (
            f'<img src="{escape(photo, quote=True)}" alt="{nom}" width="48" height="48" '
            'style="display:block; width:48px; height:48px; border-radius:50%; border:0; '
            'object-fit:cover;">'
        )
    else:
        initiale = escape((admin.username or "N")[:1].upper())
        visuel = (
            '<div style="width:48px; height:48px; border-radius:50%; background:#e63946; '
            'color:#fff; font-size:20px; font-weight:700; line-height:48px; '
            f'text-align:center;">{initiale}</div>'
        )
    return f"""
              <table role="presentation" cellpadding="0" cellspacing="0" border="0"
                     style="margin-top:28px; padding-top:20px; border-top:1px solid #262626; width:100%;">
                <tr>
                  <td width="60" valign="middle" style="padding-top:20px;">{visuel}</td>
                  <td valign="middle" style="padding-top:20px;">
                    <div style="font-size:15px; font-weight:700; color:#f0f0f0;">{nom}</div>
                    <div style="font-size:12px; color:#888;">L'équipe NAVIRE</div>
                  </td>
                </tr>
              </table>"""


def render_campaign(subject: str, body: str, admin: User | None) -> tuple[str, str]:
    """(objet, HTML complet) tels qu'envoyés à Brevo, {prenom} en paramètre."""
    html = mail.layout(render_body(body), signature_html=signature_html(admin))
    return render_subject(subject), html


def preview(subject: str, body: str, admin: User | None, prenom: str) -> tuple[str, str]:
    """Rendu final pour la console : {prenom} remplacé par un exemple."""
    s, h = render_campaign(subject, body, admin)
    p = escape(prenom)
    return s.replace(PRENOM_TAG, p), h.replace(PRENOM_TAG, p)


# ============================================================
# 3. Pièces jointes et envoi
# ============================================================

def clean_attachments(items: list[dict]) -> list[dict]:
    """Valide les PJ reçues de la console. Lève ValueError avec un message
    lisible (extension refusée, fichier illisible, taille dépassée)."""
    if len(items) > MAX_ATTACHMENTS:
        raise ValueError(f"{MAX_ATTACHMENTS} pièces jointes au maximum.")
    out, total = [], 0
    for a in items:
        name = re.sub(r"[\\/\x00-\x1f]", "_", str(a.get("name") or "").strip())[:150]
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in ALLOWED_EXTENSIONS:
            raise ValueError(f"« {name or 'sans nom'} » : type de fichier non accepté par Brevo.")
        raw_b64 = str(a.get("content_b64") or "")
        if "," in raw_b64 and raw_b64.startswith("data:"):
            raw_b64 = raw_b64.split(",", 1)[1]
        try:
            raw = base64.b64decode(raw_b64, validate=True)
        except Exception:
            raise ValueError(f"« {name} » : fichier illisible.")
        if not raw:
            raise ValueError(f"« {name} » est vide.")
        total += len(raw)
        out.append({"name": name, "size": len(raw), "content_b64": base64.b64encode(raw).decode()})
    if total > MAX_ATTACHMENTS_BYTES:
        raise ValueError(
            f"Pièces jointes trop lourdes : {total / 1048576:.1f} Mo "
            f"(maximum {MAX_ATTACHMENTS_BYTES // 1048576} Mo au total)."
        )
    return out


def _brevo_attachments(c: MailCampaign) -> list[dict]:
    return [{"name": a["name"], "content": a["content_b64"]} for a in (c.attachments or [])]


def _versions(destinataires: dict[str, str]) -> list[dict]:
    return [
        {"email": e, "params": {"prenom": p or "à toi"}}
        for e, p in sorted(destinataires.items())
    ]


def send_test(c: MailCampaign, admin: User) -> dict:
    """Envoie la campagne à l'admin seul, objet préfixé [TEST]."""
    subject, html = render_campaign(c.subject, c.body, admin)
    return mail.send_bulk_result(
        [{"email": admin.email, "params": {"prenom": admin.username}}],
        f"[TEST] {subject}",
        html,
        _brevo_attachments(c),
    )


def send_campaign(db: Session, c: MailCampaign, admin: User) -> MailCampaign:
    """
    Envoie la campagne à ses catégories, par lots. Le statut passe à
    « sending » et est enregistré AVANT le premier appel Brevo : un double clic
    ou un second onglet ne peut pas déclencher un second envoi.
    """
    destinataires = resolve_recipients(db, list(c.segments or []))
    c.status = "sending"
    c.sent_by_id = admin.id
    c.recipients_count = len(destinataires)
    c.sent_count = c.failed_count = 0
    c.error = None
    db.commit()

    subject, html = render_campaign(c.subject, c.body, admin)
    pj = _brevo_attachments(c)
    versions = _versions(destinataires)
    erreurs: list[str] = []
    for i in range(0, len(versions), BATCH_SIZE):
        lot = versions[i:i + BATCH_SIZE]
        r = mail.send_bulk_result(lot, subject, html, pj)
        if r["sent"]:
            c.sent_count += len(lot)
        else:
            c.failed_count += len(lot)
            erreurs.append(f"Lot {i // BATCH_SIZE + 1} ({len(lot)} adresses) : {r['detail']}")
        print(f"[mailing] campagne {c.id} lot {i // BATCH_SIZE + 1} : "
              f"{'OK' if r['sent'] else 'ÉCHEC'} — {r['detail']}")

    c.status = "sent" if not c.failed_count else ("partial" if c.sent_count else "failed")
    c.error = "\n".join(erreurs) or None
    c.sent_at = utcnow()
    db.commit()
    db.refresh(c)
    return c
