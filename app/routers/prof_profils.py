"""
app/routers/prof_profils.py
============================
Fiches publiques des professeurs. Règles : app/services/prof_profils.py.

Public (sans connexion) :
  GET  /professeurs/public?matiere=&niveau=   → cartes du catalogue
  GET  /professeurs/public/{slug}             → fiche complète (cours, créneaux,
                                                prochaines séances, prix)
  GET  /p/{slug}                              → lien de partage : aperçu (og:*)
                                                puis redirection vers /professeurs#slug

Professeur (auth NAVIRE, professeur actif) :
  GET  /professeurs/me/profil                 → brouillon, état, aperçu
  PUT  /professeurs/me/profil                 → enregistre le brouillon
  POST /professeurs/me/profil/photo           → photo de la fiche (Cloudinary)
  POST /professeurs/me/profil/soumettre       → envoie le brouillon en validation
  Aperçu admin : ?voir_comme=<id professeur> sur le GET (lecture seule).

Console (auth NAVIRE, is_admin) :
  GET  /professeurs/admin/profils?etat=       → toutes les fiches (une par professeur)
  POST /professeurs/admin/profils/{professeur_id}/publier
  POST /professeurs/admin/profils/{professeur_id}/refuser   {motif}
  POST /professeurs/admin/profils/{professeur_id}/verifier  {verifie}
  POST /professeurs/admin/profils/{professeur_id}/masquer   {masque}
  POST /professeurs/admin/profils/{professeur_id}/ordre     {ordre}
"""

from __future__ import annotations

import html
import json
import os
import re
import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, File as FastAPIFile, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.cloudinary_client import MAX_AVATAR_BYTES, is_allowed_image, upload_photo_prof
from app.core.config import FRONTEND_URL, PUBLIC_API_URL
from app.core.prepa_adjuris_config import PREPA_NIVEAUX
from app.db.database import get_db
from app.db.models import PrepaAdjurisProfesseur, ProfProfil, User
from app.routers.auth import get_current_user
from app.routers.professeurs import VOIR_COMME, require_admin, require_professeur, require_professeur_lecture
from app.services import prof_profils as svc
from app.services.email import mail_prof_profil_publie, mail_prof_profil_refuse, send_mail

router = APIRouter(tags=["professeurs-profils"])

# Page /professeurs du site (embed embeds/professeurs.html).
PROFESSEURS_PAGE_URL = os.getenv("PROFESSEURS_PAGE_URL", f"{FRONTEND_URL}/professeurs").rstrip("/")

_PREVIEW_BOTS = re.compile(
    r"facebookexternalhit|facebot|whatsapp|twitterbot|slackbot|telegrambot|discordbot|"
    r"linkedinbot|skypeuripreview|snapchat|pinterest|redditbot|embedly|applebot|"
    r"googlebot|bingbot|iframely|vkshare|viber|line-poker|kakaotalk",
    re.I,
)


def lien_fiche(slug: str) -> str:
    return f"{PROFESSEURS_PAGE_URL}#{slug}"


def lien_partage(slug: str) -> str:
    return f"{PUBLIC_API_URL}/p/{slug}"


# ============================================================
# Public
# ============================================================

@router.get("/professeurs/public")
def catalogue(
    matiere: str | None = Query(None, max_length=64),
    niveau: str | None = Query(None, max_length=4),
    db: Session = Depends(get_db),
):
    items = [svc.carte_publique(f) for f in svc.profils_publics(db)]
    if niveau:
        items = [i for i in items if any(m["niveau"] == niveau for m in i["matieres"])]
    if matiere:
        items = [i for i in items if any(m["key"] == matiere for m in i["matieres"])]
    return {"items": items, "niveaux": list(PREPA_NIVEAUX)}


@router.get("/professeurs/public/{slug}")
def fiche(slug: str, db: Session = Depends(get_db)):
    f = svc.profil_public(db, slug)
    if f is None:
        raise HTTPException(404, detail="Professeur introuvable.")
    out = svc.fiche_publique(db, f)
    out["lien"] = lien_fiche(f.slug)
    out["lien_partage"] = lien_partage(f.slug)
    return out


@router.get("/p/{slug}", response_class=HTMLResponse, include_in_schema=False)
def page_partage(slug: str, request: Request, db: Session = Depends(get_db)):
    """
    Lien de partage d'une fiche. Les robots d'aperçu (WhatsApp, LinkedIn,
    iMessage…) lisent les balises og:* : nom, statut et photo du professeur.
    Un visiteur est aussitôt redirigé vers la fiche sur le site. Fiche
    inconnue, masquée ou professeur suspendu : la page Professeurs.
    """
    f = svc.profil_public(db, slug)
    if f is None:
        return RedirectResponse(PROFESSEURS_PAGE_URL, status_code=302)
    carte = svc.carte_publique(f)
    cible = lien_fiche(f.slug)
    titre = f"{carte['nom']} · Professeur NAVIRE"
    sous_titre = " · ".join(x for x in (carte["statut_label"], carte["poste"]) if x)
    matieres = ", ".join(f"{m['niveau']} {m['nom']}" for m in carte["matieres"])
    desc = " — ".join(x for x in (sous_titre, matieres) if x) or "Cours de droit Prép'AdJuris"
    e = lambda v: html.escape(v or "", quote=True)  # noqa: E731
    cible_js = json.dumps(cible).replace("</", "<\\/")
    robot = bool(_PREVIEW_BOTS.search(request.headers.get("user-agent", "")))
    redirection = "" if robot else f'<meta http-equiv="refresh" content="0;url={e(cible)}">\n'
    script = "" if robot else f"<script>location.replace({cible_js});</script>\n"
    page = f"""<!doctype html>
<html lang="fr"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(titre)}</title>
<meta name="description" content="{e(desc)}">
<meta property="og:type" content="profile">
<meta property="og:site_name" content="NAVIRE">
<meta property="og:title" content="{e(titre)}">
<meta property="og:description" content="{e(desc)}">
<meta property="og:url" content="{e(lien_partage(f.slug))}">
<meta property="og:image" content="{e(carte['photo_url'])}">
<meta property="og:image:alt" content="{e(carte['nom'])}">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{e(titre)}">
<meta name="twitter:image" content="{e(carte['photo_url'])}">
{redirection}</head><body style="margin:0;background:#090914;color:#fff;font-family:Inter,Arial,sans-serif">
{script}<p style="text-align:center;padding:40px 16px"><a style="color:#7AA7FF" href="{e(cible)}">{e(titre)}</a></p>
</body></html>"""
    return HTMLResponse(page, headers={"Cache-Control": "private, max-age=300", "Vary": "User-Agent"})


# ============================================================
# Professeur
# ============================================================

class FormationLigneIn(BaseModel):
    diplome: str = Field("", max_length=svc.CHAMP_FORMATION_MAX)
    etablissement: str = Field("", max_length=svc.CHAMP_FORMATION_MAX)
    annee: str = Field("", max_length=20)


class ProfilIn(BaseModel):
    nom_complet_public: bool = False
    statut_pro: str = Field("", max_length=32)
    poste: str = Field("", max_length=160)
    experience_annees: int | None = Field(None, ge=0, le=60)
    formation: list[FormationLigneIn] = Field(default_factory=list, max_length=svc.FORMATION_MAX)
    description: str = Field("", max_length=svc.DESCRIPTION_MAX)
    linkedin_url: str | None = Field(None, max_length=300)

    @field_validator("statut_pro")
    @classmethod
    def _statut(cls, v: str) -> str:
        v = (v or "").strip()
        if v and v not in svc.STATUTS_PRO:
            raise ValueError("Statut inconnu.")
        return v

    @field_validator("linkedin_url")
    @classmethod
    def _linkedin(cls, v: str | None) -> str | None:
        return svc.valider_linkedin(v)


def _vue_prof(f: ProfProfil) -> dict:
    out = svc.vue_prof(f)
    out["lien"] = lien_fiche(f.slug) if f.slug else None
    out["lien_partage"] = lien_partage(f.slug) if f.slug else None
    out["page_url"] = PROFESSEURS_PAGE_URL
    return out


@router.get("/professeurs/me/profil")
def mon_profil(
    voir_comme: int | None = VOIR_COMME,
    db: Session = Depends(get_db),
    prof: PrepaAdjurisProfesseur = Depends(require_professeur_lecture),
):
    f = svc.profil_de(db, prof)
    db.commit()
    return _vue_prof(f)


@router.put("/professeurs/me/profil")
def enregistrer_profil(
    payload: ProfilIn,
    db: Session = Depends(get_db),
    prof: PrepaAdjurisProfesseur = Depends(require_professeur),
):
    f = svc.profil_de(db, prof)
    valeurs = payload.model_dump()
    valeurs["poste"] = valeurs["poste"].strip()
    valeurs["description"] = valeurs["description"].strip()
    try:
        valeurs["formation"] = svc.nettoyer_formation(valeurs["formation"])
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    svc.enregistrer_brouillon(f, valeurs)
    db.commit()
    return _vue_prof(f)


@router.post("/professeurs/me/profil/photo")
async def photo_profil(
    file: UploadFile = FastAPIFile(...),
    db: Session = Depends(get_db),
    prof: PrepaAdjurisProfesseur = Depends(require_professeur),
):
    if not file or not file.filename:
        raise HTTPException(400, detail="Fichier manquant.")
    if not is_allowed_image(file.content_type):
        raise HTTPException(400, detail="Format non supporté. Utilise JPG, PNG ou WEBP.")
    content = await file.read()
    if len(content) > MAX_AVATAR_BYTES:
        raise HTTPException(413, detail=f"Image trop volumineuse (max {MAX_AVATAR_BYTES // (1024 * 1024)} Mo).")
    try:
        url = upload_photo_prof(content, prof.id, secrets.token_hex(4))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, detail=f"Échec de l'envoi de la photo : {e}")
    f = svc.profil_de(db, prof)
    svc.enregistrer_brouillon(f, {"photo_url": url})
    db.commit()
    return _vue_prof(f)


@router.post("/professeurs/me/profil/soumettre")
def soumettre_profil(db: Session = Depends(get_db), prof: PrepaAdjurisProfesseur = Depends(require_professeur)):
    f = svc.profil_de(db, prof)
    if f.etat == "en_attente":
        return _vue_prof(f)
    if f.publie_le and f.brouillon is None:
        raise HTTPException(400, detail="Aucune modification à soumettre : ta fiche en ligne est à jour.")
    try:
        svc.soumettre(f)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    db.commit()
    return _vue_prof(f)


# ============================================================
# Console
# ============================================================

class MotifIn(BaseModel):
    motif: str = Field("", max_length=300)


class VerifieIn(BaseModel):
    verifie: bool


class MasqueIn(BaseModel):
    masque: bool


class OrdreIn(BaseModel):
    ordre: int = Field(..., ge=-1000, le=1000)


def _vue_admin(f: ProfProfil) -> dict:
    out = svc.vue_admin(f)
    out["lien"] = lien_fiche(f.slug) if f.slug else None
    return out


def _fiche_admin(db: Session, professeur_id: int) -> ProfProfil:
    p = db.get(PrepaAdjurisProfesseur, professeur_id)
    if p is None:
        raise HTTPException(404, detail="Professeur introuvable.")
    return svc.profil_de(db, p)


@router.get("/professeurs/admin/profils")
def admin_profils(
    etat: str | None = Query(None, max_length=16),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    profs = db.execute(select(PrepaAdjurisProfesseur).order_by(PrepaAdjurisProfesseur.id)).scalars().all()
    fiches = [svc.profil_de(db, p) for p in profs]
    db.commit()
    items = [_vue_admin(f) for f in fiches]
    if etat:
        items = [i for i in items if i["etat"] == etat]
    # À valider d'abord (le plus ancien en premier), puis l'ordre du catalogue.
    items.sort(key=lambda i: (i["etat"] != "en_attente", i["soumis_le"] or "", i["ordre"], i["professeur_id"]))
    return {"items": items, "a_valider": sum(1 for f in fiches if f.etat == "en_attente"), "page_url": PROFESSEURS_PAGE_URL}


@router.post("/professeurs/admin/profils/{professeur_id}/publier")
def admin_publier(
    professeur_id: int,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    f = _fiche_admin(db, professeur_id)
    if f.publie_le and f.brouillon is None:
        raise HTTPException(400, detail="Rien à publier : la fiche en ligne est déjà à jour.")
    try:
        svc.publier(db, f)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    db.commit()
    u = f.professeur.user
    if u and u.email:
        subject, corps = mail_prof_profil_publie(svc.prenom_de(f.professeur), lien_fiche(f.slug))
        background.add_task(send_mail, u.email, subject, corps)
    return _vue_admin(f)


@router.post("/professeurs/admin/profils/{professeur_id}/refuser")
def admin_refuser(
    professeur_id: int,
    payload: MotifIn,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    if not payload.motif.strip():
        raise HTTPException(400, detail="Indique au professeur ce qu'il doit modifier.")
    f = _fiche_admin(db, professeur_id)
    svc.refuser(f, payload.motif)
    db.commit()
    u = f.professeur.user
    if u and u.email:
        subject, corps = mail_prof_profil_refuse(svc.prenom_de(f.professeur), f.motif_refus)
        background.add_task(send_mail, u.email, subject, corps)
    return _vue_admin(f)


@router.post("/professeurs/admin/profils/{professeur_id}/verifier")
def admin_verifier(
    professeur_id: int,
    payload: VerifieIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    f = _fiche_admin(db, professeur_id)
    f.verifie_le = svc.utcnow() if payload.verifie else None
    f.verifie_par_id = admin.id if payload.verifie else None
    db.commit()
    return _vue_admin(f)


@router.post("/professeurs/admin/profils/{professeur_id}/masquer")
def admin_masquer(
    professeur_id: int,
    payload: MasqueIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    f = _fiche_admin(db, professeur_id)
    f.masque = payload.masque
    db.commit()
    return _vue_admin(f)


@router.post("/professeurs/admin/profils/{professeur_id}/ordre")
def admin_ordre(
    professeur_id: int,
    payload: OrdreIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    f = _fiche_admin(db, professeur_id)
    f.ordre = payload.ordre
    db.commit()
    return _vue_admin(f)

