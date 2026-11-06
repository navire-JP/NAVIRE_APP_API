"""
app/routers/professeurs.py
===========================
Espace professeurs Prép'AdJuris (page navire-ai.com/professeurs, embed
embeds/professeurs.html). Règles : app/services/professeurs.py.

Professeur (auth NAVIRE, attaché par un admin) :
  GET    /professeurs/me                         → statut (none | active | suspendu), grade, admin
  Aperçu admin (lecture seule) : ?voir_comme=<id professeur> sur /me,
  /me/dashboard, /me/remuneration/detail, /me/devoirs et le téléchargement
  des documents : l'espace s'affiche comme pour ce professeur.
  GET    /professeurs/me/dashboard               → groupes, étudiants, Discord, liens,
                                                   documents, prévisionnel
  GET    /professeurs/me/remuneration/detail?mois= → séances du mois, élèves comptés,
                                                   extras, part du professeur
  POST   /professeurs/classes/{id}/documents     → dépôt d'un PDF pour le groupe
  DELETE /professeurs/documents/{id}             → retrait d'un document déposé
  GET    /professeurs/documents/{id}/download
  GET    /professeurs/me/devoirs                 → copies rendues par les étudiants de ses groupes
  GET    /professeurs/devoirs/{id}/download      → la copie
  PATCH  /professeurs/devoirs/{id}               → commentaire, note (copie « corrigée »)
  POST   /professeurs/devoirs/{id}/correction    → joindre une copie corrigée (PDF)
  GET    /professeurs/devoirs/{id}/correction
  DELETE /professeurs/devoirs/{id}/correction
  GET    /professeurs/embed                      → page d'embed servie par l'API

Un document déposé est une PrepaAdjurisRessource publiée de la matière,
limitée au groupe (classe_id) : les étudiants du groupe la voient dans leur
espace AdJuris et dans NAVIRE Lib (GET /lib/adjuris).

Console (auth NAVIRE, is_admin) :
  GET    /professeurs/admin/classes
  POST   /professeurs/admin/classes
  PATCH  /professeurs/admin/classes/{id}          (nom, Discord, liens, actif)
  DELETE /professeurs/admin/classes/{id}
  GET    /professeurs/admin/classes/{id}/etudiants
  PATCH  /professeurs/admin/inscriptions/{id}     (changer l'étudiant de groupe)
  GET    /professeurs/admin/list
  POST   /professeurs/admin                       (attacher un compte par email)
  PATCH  /professeurs/admin/{id}                  (grade, nom, statut)
  DELETE /professeurs/admin/{id}
  POST   /professeurs/admin/{id}/affectations     (ajouter un groupe)
  PATCH  /professeurs/admin/affectations/{id}     (taux de rémunération, extras compris)
  DELETE /professeurs/admin/affectations/{id}
  GET    /professeurs/admin/remunerations?mois=   (tableau de pilotage)
  GET    /professeurs/admin/remunerations/detail?affectation_id=&mois=
                                                  (séances, élèves, extras, parts prof / NAVIRE)
  PUT    /professeurs/admin/remunerations         (montant fixé d'un mois, note)
  POST   /professeurs/admin/remunerations/verser  (virement fait : montant figé)
  POST   /professeurs/admin/remunerations/annuler-versement

La rémunération ne passe pas par Stripe : la console calcule et pilote, le
virement est fait à la main puis marqué « versé ».
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File as FastAPIFile, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.prepa_storage import PREPA_ADJURIS_DIR, PREPA_COURSE_MAX_BYTES, delete_prepa_file, save_prepa_pdf
from app.db.database import get_db
from app.db.models import (
    PrepaAdjurisAffectation,
    PrepaAdjurisAnnotation,
    PrepaAdjurisClasse,
    PrepaAdjurisDevoir,
    PrepaAdjurisEnrollment,
    PrepaAdjurisProfesseur,
    PrepaAdjurisRemuneration,
    PrepaAdjurisRessource,
    User,
)
from app.core.prepa_adjuris_config import matiere_niveau
from app.routers.auth import get_current_user
from app.services import copies as csvc
from app.services import devoirs as dsvc
from app.services import professeurs as svc
from app.services.prepa_adjuris_billing import cle_mois, date_prelevement, mois_de, parse_mois

router = APIRouter(prefix="/professeurs", tags=["professeurs"])


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not getattr(user, "is_admin", False):
        raise HTTPException(status_code=403, detail="Admin only")
    return user


def require_professeur(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> PrepaAdjurisProfesseur:
    prof = svc.get_professeur(db, user.id)
    if prof is None or prof.status != "active":
        raise HTTPException(403, detail="Réservé aux professeurs NAVIRE.")
    return prof


def _prof_vu(db: Session, user: User, voir_comme: int | None) -> PrepaAdjurisProfesseur | None:
    """Aperçu admin : le professeur dont l'admin regarde l'espace (lecture
    seule : les routes d'écriture n'acceptent pas voir_comme). Ignoré pour
    tout autre compte."""
    if voir_comme is None or not getattr(user, "is_admin", False):
        return None
    p = db.get(PrepaAdjurisProfesseur, voir_comme)
    if p is None:
        raise HTTPException(404, detail="Professeur introuvable.")
    return p


VOIR_COMME = Query(None, description="Aperçu admin : id du professeur dont on voit l'espace")


def require_professeur_lecture(
    voir_comme: int | None = VOIR_COMME,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PrepaAdjurisProfesseur:
    """Le professeur connecté, ou celui qu'un admin regarde (voir_comme)."""
    return _prof_vu(db, user, voir_comme) or require_professeur(db, user)


def _iso(dt) -> str | None:
    dt = svc.aware(dt)
    return dt.isoformat() if dt else None


def _document_row(r: PrepaAdjurisRessource) -> dict:
    return {
        "id": r.id,
        "classe_id": r.classe_id,
        "titre": r.titre,
        "description": r.description,
        "filename_original": r.filename_original,
        "size_bytes": r.size_bytes,
        "uploaded_by_id": r.uploaded_by_id,
        "created_at": _iso(r.created_at),
    }


# ============================================================
# Page d'embed servie par l'API
# ============================================================

_EMBED = Path(__file__).resolve().parents[2] / "embeds" / "professeurs.html"


@router.get("/embed", response_class=HTMLResponse, include_in_schema=False)
def embed_page():
    contenu = _EMBED.read_text(encoding="utf-8")
    # Servie par l'API elle-même : appels relatifs, quel que soit le domaine.
    contenu = contenu.replace('API: "https://navire-app-api.onrender.com"', 'API: ""')
    return HTMLResponse(
        '<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>Professeurs NAVIRE</title></head>'
        f'<body style="margin:0;background:transparent">{contenu}</body></html>'
    )


# ============================================================
# Espace professeur
# ============================================================

@router.get("/me")
def get_me(
    voir_comme: int | None = VOIR_COMME,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    admin = bool(getattr(user, "is_admin", False))
    vu = _prof_vu(db, user, voir_comme)
    prof = vu or svc.get_professeur(db, user.id)
    if prof is None:
        return {"status": "none", "admin": admin, "apercu": None}
    return {
        "admin": admin,
        # Aperçu admin : l'espace s'affiche comme pour ce professeur, actif ou non.
        "apercu": {"professeur_id": prof.id, "nom": f"{prof.prenom} {prof.nom}".strip(), "status": prof.status} if vu else None,
        "status": "active" if vu else prof.status,
        "grade": prof.grade,
        "prenom": prof.prenom,
        "nom": prof.nom,
        "nb_classes": len(prof.affectations),
    }


@router.get("/me/dashboard")
def dashboard(db: Session = Depends(get_db), prof: PrepaAdjurisProfesseur = Depends(require_professeur_lecture)):
    par_matiere = svc.groupes_par_matiere(db)
    classes = []
    for a in sorted(prof.affectations, key=lambda x: x.classe_id):
        c = a.classe
        if not c.is_active:
            continue
        inscriptions = svc.inscriptions_du_groupe(db, c, par_matiere)
        documents = db.execute(
            select(PrepaAdjurisRessource)
            .where(PrepaAdjurisRessource.classe_id == c.id)
            .order_by(PrepaAdjurisRessource.created_at.desc(), PrepaAdjurisRessource.id.desc())
        ).scalars().all()
        a_corriger = db.execute(
            select(func.count(PrepaAdjurisDevoir.id)).where(
                PrepaAdjurisDevoir.classe_id == c.id, PrepaAdjurisDevoir.statut == "depose"
            )
        ).scalar_one()
        classes.append({
            **svc.classe_row(c),
            "affectation_id": a.id,
            "devoirs_a_corriger": a_corriger,
            "etudiants": [svc.etudiant_row(db, e) for e in inscriptions],
            "documents": [_document_row(r) for r in documents],
            "previsionnel": svc.previsionnel(db, a, inscriptions),
        })

    mois: dict[str, dict] = {}
    for c in classes:
        for m in c["previsionnel"]["mois"]:
            t = mois.setdefault(m["mois"], {
                "mois": m["mois"], "seances": 0, "seances_passees": 0, "montant_cents": 0,
                "realise_cents": 0, "extras": 0, "extras_cents": 0,
            })
            for k in ("seances", "seances_passees", "montant_cents", "realise_cents", "extras", "extras_cents"):
                t[k] += m[k]

    # Solde prévisionnel du mois en cours : déjà gagné + reste des séances du mois.
    cle_en_cours = cle_mois(mois_de(svc.utcnow()))
    en_cours = dict(mois.get(cle_en_cours) or {
        "mois": cle_en_cours, "seances": 0, "seances_passees": 0, "montant_cents": 0,
        "realise_cents": 0, "extras": 0, "extras_cents": 0,
    })
    en_cours["a_venir_cents"] = en_cours["montant_cents"] - en_cours["realise_cents"]

    return {
        "professeur": {
            "user_id": prof.user_id, "grade": prof.grade, "prenom": prof.prenom, "nom": prof.nom, "status": prof.status,
        },
        "classes": classes,
        "totaux": {
            "classes": len(classes),
            "etudiants": sum(len(c["etudiants"]) for c in classes),
            "documents": sum(len(c["documents"]) for c in classes),
            "devoirs_a_corriger": sum(c["devoirs_a_corriger"] for c in classes),
            "realise_cents": sum(c["previsionnel"]["realise_cents"] for c in classes),
            "verse_cents": sum(c["previsionnel"]["verse_cents"] for c in classes),
            "a_venir_cents": sum(c["previsionnel"]["a_venir_cents"] for c in classes),
            "total_cents": sum(c["previsionnel"]["total_cents"] for c in classes),
            "mois": sorted(mois.values(), key=lambda m: m["mois"]),
            "mois_en_cours": en_cours,
        },
    }


@router.get("/me/remuneration/detail")
def mon_detail_remuneration(
    mois: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    db: Session = Depends(get_db),
    prof: PrepaAdjurisProfesseur = Depends(require_professeur_lecture),
):
    """Détail d'un mois, groupe par groupe : séances, élèves comptés, extras et
    part du professeur (sans l'encaissé ni la part NAVIRE)."""
    par_matiere = svc.groupes_par_matiere(db)
    affectations = [a for a in sorted(prof.affectations, key=lambda x: x.classe_id) if a.classe.is_active]
    lignes = svc.pilotage(db, [a.id for a in affectations])
    groupes = [
        svc.detail_mois(db, a, svc.inscriptions_du_groupe(db, a.classe, par_matiere), mois, lignes=lignes, avec_navire=False)
        for a in affectations
    ]
    return {
        "mois": mois,
        "groupes": groupes,
        "totaux": {
            "seances": sum(g["totaux"]["seances"] for g in groupes),
            "extras": sum(g["totaux"]["extras"] for g in groupes),
            "part_prof_cents": sum(g["totaux"]["part_prof_cents"] for g in groupes),
        },
    }


def _ma_classe(db: Session, prof: PrepaAdjurisProfesseur, classe_id: int) -> PrepaAdjurisClasse:
    a = next((a for a in prof.affectations if a.classe_id == classe_id), None)
    if a is None or not a.classe.is_active:
        raise HTTPException(404, detail="Groupe introuvable.")
    return a.classe


@router.post("/classes/{classe_id}/documents")
async def deposer_document(
    classe_id: int,
    file: UploadFile = FastAPIFile(...),
    titre: str = Form(""),
    description: str = Form(""),
    db: Session = Depends(get_db),
    prof: PrepaAdjurisProfesseur = Depends(require_professeur),
):
    c = _ma_classe(db, prof, classe_id)
    niveau = matiere_niveau(c.matiere_key)
    meta = await save_prepa_pdf(file, PREPA_ADJURIS_DIR / niveau, max_bytes=PREPA_COURSE_MAX_BYTES)
    r = PrepaAdjurisRessource(
        niveau=niveau,
        matiere_key=c.matiere_key,
        classe_id=c.id,
        uploaded_by_id=prof.user_id,
        titre=(titre.strip() or meta["filename_original"])[:200],
        description=description.strip(),
        filename_original=meta["filename_original"],
        filename_stored=meta["filename_stored"],
        path=meta["path"],
        size_bytes=meta["size_bytes"],
        is_published=True,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return _document_row(r)


def _mon_document(db: Session, prof: PrepaAdjurisProfesseur, document_id: int) -> PrepaAdjurisRessource:
    r = db.get(PrepaAdjurisRessource, document_id)
    if r is None or r.classe_id is None or not any(a.classe_id == r.classe_id for a in prof.affectations):
        raise HTTPException(404, detail="Document introuvable.")
    return r


@router.delete("/documents/{document_id}")
def retirer_document(
    document_id: int,
    db: Session = Depends(get_db),
    prof: PrepaAdjurisProfesseur = Depends(require_professeur),
):
    r = _mon_document(db, prof, document_id)
    if r.uploaded_by_id != prof.user_id:
        raise HTTPException(403, detail="Seul le professeur qui l'a déposé peut retirer ce document.")
    from app.routers.lib_prepa import supprimer_doc_prepa

    supprimer_doc_prepa(db, r.id)   # sa version NAVIRE Lib (et la progression des élèves)
    delete_prepa_file(r.path)
    db.delete(r)
    db.commit()
    return {"ok": True, "id": document_id}


@router.get("/documents/{document_id}/download")
def telecharger_document(
    document_id: int,
    db: Session = Depends(get_db),
    prof: PrepaAdjurisProfesseur = Depends(require_professeur_lecture),
):
    r = _mon_document(db, prof, document_id)
    if not Path(r.path).exists():
        raise HTTPException(404, detail="Fichier introuvable sur le disque.")
    return FileResponse(
        r.path, media_type="application/pdf", filename=r.filename_original, content_disposition_type="inline"
    )


# ============================================================
# Devoirs rendus par les étudiants (vue Prépa de NAVIRE Lib)
# ============================================================

def require_correcteur(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> tuple[User, set[int] | None]:
    """Professeur actif (ses groupes) ou admin (tous les groupes : None)."""
    if getattr(user, "is_admin", False):
        return user, None
    prof = svc.get_professeur(db, user.id)
    if prof is None or prof.status != "active":
        raise HTTPException(403, detail="Réservé aux professeurs NAVIRE.")
    return user, {a.classe_id for a in prof.affectations}


def _devoir_a_corriger(db: Session, acces: tuple[User, set[int] | None], devoir_id: int) -> PrepaAdjurisDevoir:
    _, classes = acces
    d = db.get(PrepaAdjurisDevoir, devoir_id)
    if d is None or (classes is not None and d.classe_id not in classes):
        raise HTTPException(404, detail="Copie introuvable.")
    return d


class CorrectionIn(BaseModel):
    commentaire: str | None = Field(None, max_length=8000)
    note: str | None = Field(None, max_length=40)
    statut: Literal["depose", "corrige"] | None = None


def _marquer_corrige(db: Session, d: PrepaAdjurisDevoir, user: User, background: BackgroundTasks) -> None:
    deja = d.statut == "corrige"
    d.statut = "corrige"
    d.corrige_par_id = user.id
    d.corrige_le = svc.utcnow()
    if not deja:
        etudiant = db.get(User, d.user_id)
        if etudiant and etudiant.email:
            background.add_task(
                dsvc.mail_copie_corrigee, etudiant.email, etudiant.username or "",
                dsvc.devoir_row(db, d)["matiere"], d.titre,
            )


@router.get("/me/devoirs")
def mes_devoirs(
    voir_comme: int | None = VOIR_COMME,
    db: Session = Depends(get_db),
    acces=Depends(require_correcteur),
):
    user, classes = acces
    vu = _prof_vu(db, user, voir_comme)
    if vu is not None:
        classes = {a.classe_id for a in vu.affectations}
    q = select(PrepaAdjurisDevoir).order_by(PrepaAdjurisDevoir.created_at.desc(), PrepaAdjurisDevoir.id.desc())
    if classes is not None:
        if not classes:
            return {"items": [], "a_corriger": 0}
        q = q.where(PrepaAdjurisDevoir.classe_id.in_(classes))
    rows = db.execute(q).scalars().all()
    return {
        "items": [dsvc.devoir_row(db, d, pour_prof=True) for d in rows],
        "a_corriger": sum(1 for d in rows if d.statut == "depose"),
    }


@router.get("/devoirs/{devoir_id}/download")
def telecharger_copie(devoir_id: int, db: Session = Depends(get_db), acces=Depends(require_correcteur)):
    from app.routers.lib_prepa import media_type

    d = _devoir_a_corriger(db, acces, devoir_id)
    if not Path(d.path).exists():
        raise HTTPException(404, detail="Fichier introuvable sur le disque.")
    return FileResponse(d.path, media_type=media_type(d.path), filename=d.filename_original,
                        content_disposition_type="inline")


# ============================================================
# Mode correction : la copie mise en forme, révisions et commentaires
# ============================================================
# L'étudiant voit les annotations une fois la correction rendue
# (PATCH /devoirs/{id} avec statut « corrige »).

class AnnotationIn(BaseModel):
    type: Literal["commentaire", "surligne", "correction"]
    bloc: str = Field(..., max_length=16)
    debut: int = Field(..., ge=0)
    fin: int = Field(..., ge=0)
    texte: str = Field("", max_length=csvc.TEXTE_MAX)


class AnnotationModifIn(BaseModel):
    texte: str = Field(..., max_length=csvc.TEXTE_MAX)


def _annotation(db: Session, acces, annotation_id: int) -> PrepaAdjurisAnnotation:
    a = db.get(PrepaAdjurisAnnotation, annotation_id)
    if a is None:
        raise HTTPException(404, detail="Annotation introuvable.")
    _devoir_a_corriger(db, acces, a.devoir_id)
    return a


@router.get("/devoirs/{devoir_id}/copie")
def copie_a_corriger(devoir_id: int, db: Session = Depends(get_db), acces=Depends(require_correcteur)):
    return csvc.copie_row(db, _devoir_a_corriger(db, acces, devoir_id), pour_prof=True)


@router.post("/devoirs/{devoir_id}/annotations")
def annoter(devoir_id: int, payload: AnnotationIn, db: Session = Depends(get_db), acces=Depends(require_correcteur)):
    d = _devoir_a_corriger(db, acces, devoir_id)
    a = csvc.ajouter(db, d, acces[0], type=payload.type, bloc=payload.bloc, debut=payload.debut,
                     fin=payload.fin, texte=payload.texte)
    return csvc.annotation_row(db, a)


@router.patch("/annotations/{annotation_id}")
def modifier_annotation(annotation_id: int, payload: AnnotationModifIn, db: Session = Depends(get_db),
                        acces=Depends(require_correcteur)):
    a = _annotation(db, acces, annotation_id)
    csvc.valider(db, a.devoir, type=a.type, bloc=a.bloc, debut=a.debut, fin=a.fin, texte=payload.texte,
                 ignorer_id=a.id)
    a.texte = payload.texte.strip()
    db.commit()
    db.refresh(a)
    return csvc.annotation_row(db, a)


@router.delete("/annotations/{annotation_id}")
def retirer_annotation(annotation_id: int, db: Session = Depends(get_db), acces=Depends(require_correcteur)):
    a = _annotation(db, acces, annotation_id)
    db.delete(a)
    db.commit()
    return {"ok": True, "id": annotation_id}


@router.get("/devoirs/{devoir_id}/export")
def exporter_copie(
    devoir_id: int,
    format: str = Query("docx", description="docx | pdf"),
    db: Session = Depends(get_db),
    acces=Depends(require_correcteur),
):
    from app.routers.lib_prepa import exporter

    return exporter(db, _devoir_a_corriger(db, acces, devoir_id), format, pour_prof=True)


@router.patch("/devoirs/{devoir_id}")
def corriger_devoir(
    devoir_id: int,
    payload: CorrectionIn,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    acces=Depends(require_correcteur),
):
    d = _devoir_a_corriger(db, acces, devoir_id)
    if payload.commentaire is not None:
        d.commentaire = payload.commentaire.strip()
    if payload.note is not None:
        d.note = payload.note.strip()
    if payload.statut == "depose":
        d.statut = "depose"           # rouvrir la copie (correction à reprendre)
    elif payload.statut == "corrige" or d.commentaire or d.note or d.correction_path:
        _marquer_corrige(db, d, acces[0], background)
    db.commit()
    db.refresh(d)
    return dsvc.devoir_row(db, d, pour_prof=True)


@router.post("/devoirs/{devoir_id}/correction")
async def joindre_correction(
    devoir_id: int,
    background: BackgroundTasks,
    file: UploadFile = FastAPIFile(...),
    db: Session = Depends(get_db),
    acces=Depends(require_correcteur),
):
    d = _devoir_a_corriger(db, acces, devoir_id)
    meta = await save_prepa_pdf(file, PREPA_ADJURIS_DIR / "corrections" / str(d.user_id),
                                max_bytes=PREPA_COURSE_MAX_BYTES)
    if d.correction_path:
        delete_prepa_file(d.correction_path)
    d.correction_path = meta["path"]
    d.correction_filename = meta["filename_original"][:255]
    _marquer_corrige(db, d, acces[0], background)
    db.commit()
    db.refresh(d)
    return dsvc.devoir_row(db, d, pour_prof=True)


@router.get("/devoirs/{devoir_id}/correction")
def telecharger_correction(devoir_id: int, db: Session = Depends(get_db), acces=Depends(require_correcteur)):
    d = _devoir_a_corriger(db, acces, devoir_id)
    if not d.correction_path or not Path(d.correction_path).exists():
        raise HTTPException(404, detail="Pas de copie corrigée.")
    return FileResponse(d.correction_path, media_type="application/pdf",
                        filename=d.correction_filename or "correction.pdf", content_disposition_type="inline")


@router.delete("/devoirs/{devoir_id}/correction")
def retirer_correction(devoir_id: int, db: Session = Depends(get_db), acces=Depends(require_correcteur)):
    d = _devoir_a_corriger(db, acces, devoir_id)
    if d.correction_path:
        delete_prepa_file(d.correction_path)
    d.correction_path, d.correction_filename = "", ""
    db.commit()
    db.refresh(d)
    return dsvc.devoir_row(db, d, pour_prof=True)


# ============================================================
# Console — groupes
# ============================================================

class Lien(BaseModel):
    label: str = Field(..., min_length=1, max_length=60)
    url: str = Field(..., max_length=500)

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("https://", "http://")):
            raise ValueError("Le lien doit commencer par https://")
        return v


def _check_discord(v: str) -> str:
    v = v.strip()
    if v and not v.startswith("https://"):
        raise ValueError("Le lien Discord doit commencer par https://")
    return v


class ClasseIn(BaseModel):
    matiere_key: str
    nom: str = Field(..., min_length=2, max_length=120)
    discord_url: str = Field("", max_length=500)
    liens: list[Lien] = Field(default_factory=list, max_length=20)

    @field_validator("discord_url")
    @classmethod
    def _discord(cls, v: str) -> str:
        return _check_discord(v)


class ClasseUpdateIn(BaseModel):
    nom: str | None = Field(None, min_length=2, max_length=120)
    discord_url: str | None = Field(None, max_length=500)
    liens: list[Lien] | None = Field(None, max_length=20)
    is_active: bool | None = None

    @field_validator("discord_url")
    @classmethod
    def _discord(cls, v: str | None) -> str | None:
        return None if v is None else _check_discord(v)


def _classe_admin_row(db: Session, c: PrepaAdjurisClasse, par_matiere: dict) -> dict:
    profs = db.execute(
        select(PrepaAdjurisAffectation).where(PrepaAdjurisAffectation.classe_id == c.id)
    ).scalars().all()
    return {
        **svc.classe_row(c),
        "nb_etudiants": len(svc.inscriptions_du_groupe(db, c, par_matiere)) if c.is_active else 0,
        "professeurs": [
            {"affectation_id": a.id, "professeur_id": a.professeur_id,
             "nom": f"{a.professeur.prenom} {a.professeur.nom}".strip() or a.professeur.user.username}
            for a in profs
        ],
    }


def _classe_or_404(db: Session, classe_id: int) -> PrepaAdjurisClasse:
    c = db.get(PrepaAdjurisClasse, classe_id)
    if c is None:
        raise HTTPException(404, detail="Groupe introuvable.")
    return c


@router.get("/admin/classes")
def admin_classes(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    par_matiere = svc.groupes_par_matiere(db)
    rows = db.execute(
        select(PrepaAdjurisClasse).order_by(PrepaAdjurisClasse.matiere_key, PrepaAdjurisClasse.id)
    ).scalars().all()
    return {"items": [_classe_admin_row(db, c, par_matiere) for c in rows]}


@router.post("/admin/classes")
def admin_create_classe(payload: ClasseIn, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    if not svc.matiere_valide(payload.matiere_key):
        raise HTTPException(400, detail=f"Matière inconnue : {payload.matiere_key}")
    c = PrepaAdjurisClasse(
        matiere_key=payload.matiere_key,
        nom=payload.nom.strip(),
        discord_url=payload.discord_url,
        liens=[l.model_dump() for l in payload.liens],
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return _classe_admin_row(db, c, svc.groupes_par_matiere(db))


@router.patch("/admin/classes/{classe_id}")
def admin_update_classe(
    classe_id: int, payload: ClasseUpdateIn, db: Session = Depends(get_db), _: User = Depends(require_admin)
):
    c = _classe_or_404(db, classe_id)
    if payload.nom is not None:
        c.nom = payload.nom.strip()
    if payload.discord_url is not None:
        c.discord_url = payload.discord_url
    if payload.liens is not None:
        c.liens = [l.model_dump() for l in payload.liens]
    if payload.is_active is not None:
        c.is_active = payload.is_active
    db.commit()
    return _classe_admin_row(db, c, svc.groupes_par_matiere(db))


@router.delete("/admin/classes/{classe_id}")
def admin_delete_classe(classe_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """Refusé tant que le groupe a des documents : le désactiver à la place.
    Ses étudiants rejoignent le premier groupe restant de la matière."""
    c = _classe_or_404(db, classe_id)
    nb_docs = db.execute(
        select(func.count(PrepaAdjurisRessource.id)).where(PrepaAdjurisRessource.classe_id == c.id)
    ).scalar_one()
    if nb_docs:
        raise HTTPException(400, detail="Ce groupe a des documents : désactive-le plutôt que de le supprimer.")
    for e in db.execute(
        select(PrepaAdjurisEnrollment).where(PrepaAdjurisEnrollment.classe_id == c.id)
    ).scalars():
        e.classe_id = None
    db.delete(c)
    db.commit()
    return {"ok": True, "id": classe_id}


@router.get("/admin/classes/{classe_id}/etudiants")
def admin_classe_etudiants(classe_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    c = _classe_or_404(db, classe_id)
    par_matiere = svc.groupes_par_matiere(db)
    return {
        "classe": svc.classe_row(c),
        "groupes": [{"id": g.id, "nom": g.nom} for g in par_matiere.get(c.matiere_key, [])],
        "items": [svc.etudiant_row(db, e) for e in svc.inscriptions_du_groupe(db, c, par_matiere)],
    }


class InscriptionGroupeIn(BaseModel):
    classe_id: int | None = Field(None, description="NULL : premier groupe de la matière")


@router.patch("/admin/inscriptions/{enrollment_id}")
def admin_changer_groupe(
    enrollment_id: int, payload: InscriptionGroupeIn, db: Session = Depends(get_db), _: User = Depends(require_admin)
):
    e = db.get(PrepaAdjurisEnrollment, enrollment_id)
    if e is None:
        raise HTTPException(404, detail="Inscription introuvable.")
    if payload.classe_id is not None:
        c = _classe_or_404(db, payload.classe_id)
        if c.matiere_key != e.matiere_key:
            raise HTTPException(400, detail="Ce groupe n'est pas dans la matière de l'inscription.")
    # Toutes les lignes de l'étudiant dans la matière : sinon une ligne restée
    # sans groupe le laisserait aussi dans le premier groupe.
    lignes = svc.lignes_de_l_etudiant(db, e)
    for x in lignes:
        x.classe_id = payload.classe_id
    db.commit()
    return {
        "ok": True,
        "enrollment_id": e.id,
        "lignes": len(lignes),
        "classe_id": svc.groupe_de(e, svc.groupes_par_matiere(db)),
    }


# ============================================================
# Console — professeurs
# ============================================================

class ProfesseurIn(BaseModel):
    email: str = Field(..., max_length=320)
    prenom: str = Field("", max_length=80)
    nom: str = Field("", max_length=80)
    grade: str = Field(svc.GRADE_PROFESSEUR, min_length=1, max_length=64)
    classe_ids: list[int] = Field(default_factory=list, max_length=30)


class ProfesseurUpdateIn(BaseModel):
    prenom: str | None = Field(None, max_length=80)
    nom: str | None = Field(None, max_length=80)
    grade: str | None = Field(None, min_length=1, max_length=64)
    status: Literal["active", "suspendu"] | None = None


class AffectationIn(BaseModel):
    classe_id: int
    remu_seance_cents: int | None = Field(None, ge=0, le=1_000_000)
    remu_etudiant_cents: int | None = Field(None, ge=0, le=1_000_000)
    remu_extra_etudiant_cents: int | None = Field(None, ge=0, le=1_000_000)


class AffectationUpdateIn(BaseModel):
    remu_seance_cents: int | None = Field(None, ge=0, le=1_000_000)
    remu_etudiant_cents: int | None = Field(None, ge=0, le=1_000_000)
    remu_extra_etudiant_cents: int | None = Field(None, ge=0, le=1_000_000)


def _prof_admin_row(db: Session, p: PrepaAdjurisProfesseur, par_matiere: dict | None = None) -> dict:
    par_matiere = par_matiere if par_matiere is not None else svc.groupes_par_matiere(db)
    u = p.user
    affectations = []
    for a in sorted(p.affectations, key=lambda x: x.classe_id):
        inscriptions = svc.inscriptions_du_groupe(db, a.classe, par_matiere) if a.classe.is_active else []
        affectations.append({
            "id": a.id,
            "classe": svc.classe_row(a.classe),
            "nb_etudiants": len(inscriptions),
            "previsionnel": svc.previsionnel(db, a, inscriptions),
        })
    return {
        "id": p.id,
        "user_id": p.user_id,
        "username": u.username if u else None,
        "email": u.email if u else None,
        "prenom": p.prenom,
        "nom": p.nom,
        "grade": p.grade,
        "status": p.status,
        "created_at": _iso(p.created_at),
        "affectations": affectations,
    }


def _prof_or_404(db: Session, professeur_id: int) -> PrepaAdjurisProfesseur:
    p = db.get(PrepaAdjurisProfesseur, professeur_id)
    if p is None:
        raise HTTPException(404, detail="Professeur introuvable.")
    return p


def _affecter(db: Session, p: PrepaAdjurisProfesseur, payload: AffectationIn) -> PrepaAdjurisAffectation:
    _classe_or_404(db, payload.classe_id)
    if any(a.classe_id == payload.classe_id for a in p.affectations):
        raise HTTPException(400, detail="Ce professeur a déjà ce groupe.")
    a = PrepaAdjurisAffectation(
        classe_id=payload.classe_id,
        remu_seance_cents=svc.REMU_SEANCE_CENTS if payload.remu_seance_cents is None else payload.remu_seance_cents,
        remu_etudiant_cents=svc.REMU_ETUDIANT_CENTS if payload.remu_etudiant_cents is None else payload.remu_etudiant_cents,
        remu_extra_etudiant_cents=(
            svc.REMU_EXTRA_ETUDIANT_CENTS if payload.remu_extra_etudiant_cents is None
            else payload.remu_extra_etudiant_cents
        ),
    )
    p.affectations.append(a)
    return a


def _refuser_si_verse(db: Session, affectation_ids: list[int]) -> None:
    """Les versements sont l'historique des virements : on ne les efface pas."""
    if affectation_ids and db.execute(
        select(PrepaAdjurisRemuneration.id).where(
            PrepaAdjurisRemuneration.affectation_id.in_(affectation_ids),
            PrepaAdjurisRemuneration.verse_le.is_not(None),
        ).limit(1)
    ).first():
        raise HTTPException(
            400,
            detail="Des mois ont déjà été versés : suspends le professeur ou mets ses taux à 0 plutôt que de supprimer.",
        )


@router.get("/admin/list")
def admin_list(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    par_matiere = svc.groupes_par_matiere(db)
    rows = db.execute(select(PrepaAdjurisProfesseur).order_by(PrepaAdjurisProfesseur.id)).scalars().all()
    return {
        "items": [_prof_admin_row(db, p, par_matiere) for p in rows],
        "remu_defaut": {
            "seance_cents": svc.REMU_SEANCE_CENTS,
            "etudiant_cents": svc.REMU_ETUDIANT_CENTS,
            "extra_etudiant_cents": svc.REMU_EXTRA_ETUDIANT_CENTS,
        },
    }


@router.post("/admin")
def admin_create(payload: ProfesseurIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Attache un compte NAVIRE existant comme professeur. Son grade
    (« ChargéTD » par défaut) devient aussi celui du compte."""
    u = db.execute(select(User).where(func.lower(User.email) == payload.email.strip().lower())).scalar_one_or_none()
    if u is None:
        raise HTTPException(404, detail="Aucun compte NAVIRE avec cet email : le professeur doit d'abord créer son compte.")
    if svc.get_professeur(db, u.id):
        raise HTTPException(400, detail="Ce compte est déjà professeur.")
    p = PrepaAdjurisProfesseur(
        user_id=u.id,
        grade=payload.grade.strip(),
        prenom=payload.prenom.strip(),
        nom=payload.nom.strip(),
        created_by_id=admin.id,
    )
    db.add(p)
    for cid in dict.fromkeys(payload.classe_ids):
        _affecter(db, p, AffectationIn(classe_id=cid))
    u.grade = p.grade
    db.commit()
    db.refresh(p)
    return _prof_admin_row(db, p)


@router.patch("/admin/affectations/{affectation_id}")
def admin_update_affectation(
    affectation_id: int, payload: AffectationUpdateIn, db: Session = Depends(get_db), _: User = Depends(require_admin)
):
    a = db.get(PrepaAdjurisAffectation, affectation_id)
    if a is None:
        raise HTTPException(404, detail="Affectation introuvable.")
    if payload.remu_seance_cents is not None:
        a.remu_seance_cents = payload.remu_seance_cents
    if payload.remu_etudiant_cents is not None:
        a.remu_etudiant_cents = payload.remu_etudiant_cents
    if payload.remu_extra_etudiant_cents is not None:
        a.remu_extra_etudiant_cents = payload.remu_extra_etudiant_cents
    db.commit()
    return _prof_admin_row(db, a.professeur)


@router.delete("/admin/affectations/{affectation_id}")
def admin_delete_affectation(affectation_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    a = db.get(PrepaAdjurisAffectation, affectation_id)
    if a is None:
        raise HTTPException(404, detail="Affectation introuvable.")
    _refuser_si_verse(db, [a.id])
    p = a.professeur
    p.affectations.remove(a)
    db.commit()
    return _prof_admin_row(db, p)


# ============================================================
# Console — pilotage des rémunérations (virements manuels)
# ============================================================

def _tableau(db: Session) -> list[dict]:
    """Une ligne par affectation et par mois, avec le calculé, le fixé et le
    montant retenu."""
    par_matiere = svc.groupes_par_matiere(db)
    affectations = db.execute(
        select(PrepaAdjurisAffectation).order_by(PrepaAdjurisAffectation.professeur_id, PrepaAdjurisAffectation.classe_id)
    ).scalars().all()
    lignes = svc.pilotage(db, [a.id for a in affectations])
    maintenant = svc.utcnow()
    out = []
    for a in affectations:
        p, c = a.professeur, a.classe
        inscriptions = svc.inscriptions_du_groupe(db, c, par_matiere) if c.is_active else []
        prev = svc.previsionnel(db, a, inscriptions, lignes=lignes)
        for m in prev["mois"]:
            out.append({
                "affectation_id": a.id,
                "professeur_id": p.id,
                "professeur": f"{p.prenom} {p.nom}".strip() or (p.user.username if p.user else ""),
                "email": p.user.email if p.user else None,
                "professeur_status": p.status,
                "classe_id": c.id,
                "classe": c.nom,
                "matiere_key": c.matiere_key,
                "nb_etudiants": len(inscriptions),
                **m,
                # Prévu = montant du mois ; acquis = séances faites ; à verser =
                # acquis d'un mois clos (prélèvement des élèves passé), non versé.
                "prevu_cents": m["montant_cents"],
                "acquis_cents": m["realise_cents"],
                "clos": date_prelevement(parse_mois(m["mois"])) <= maintenant,
            })
            l = out[-1]
            l["a_verser_cents"] = l["acquis_cents"] if l["clos"] and not l["verse_le"] else 0
    return out


@router.get("/admin/remunerations")
def admin_remunerations(
    mois: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    tout = _tableau(db)
    items = [l for l in tout if mois is None or l["mois"] == mois]
    return {
        "mois": sorted({l["mois"] for l in tout}),
        "items": items,
        "totaux": {
            "calcule_cents": sum(l["calcule_cents"] for l in items),
            "montant_cents": sum(l["montant_cents"] for l in items),
            "verse_cents": sum(l["montant_cents"] for l in items if l["verse_le"]),
            "prevu_cents": sum(l["prevu_cents"] for l in items),
            "acquis_cents": sum(l["acquis_cents"] for l in items),
            "a_verser_cents": sum(l["a_verser_cents"] for l in items),
            # Non versé, tous mois confondus (futurs compris) : l'ancien « à verser ».
            "reste_cents": sum(l["montant_cents"] for l in items if not l["verse_le"]),
        },
    }


@router.get("/admin/remunerations/detail")
def admin_detail_remuneration(
    affectation_id: int,
    mois: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Détail au centime d'une ligne du pilotage : séances, élèves, extras,
    part professeur, encaissé et part NAVIRE."""
    a = db.get(PrepaAdjurisAffectation, affectation_id)
    if a is None:
        raise HTTPException(404, detail="Affectation introuvable.")
    inscriptions = svc.inscriptions_du_groupe(db, a.classe) if a.classe.is_active else []
    d = svc.detail_mois(db, a, inscriptions, mois)
    p = a.professeur
    d["professeur"] = f"{p.prenom} {p.nom}".strip() or (p.user.username if p.user else "")
    return d


class RemunerationIn(BaseModel):
    affectation_id: int
    mois: str = Field(..., pattern=r"^\d{4}-\d{2}$")
    montant_cents: int | None = Field(None, ge=0, le=100_000_000, description="NULL : revenir au montant calculé")
    note: str | None = Field(None, max_length=300)


class LigneIn(BaseModel):
    affectation_id: int
    mois: str = Field(..., pattern=r"^\d{4}-\d{2}$")


class VerserIn(BaseModel):
    lignes: list[LigneIn] = Field(..., min_length=1, max_length=500)
    reference: str | None = Field(None, max_length=120)


def _ligne(db: Session, affectation_id: int, mois: str, creer: bool = True) -> PrepaAdjurisRemuneration | None:
    if db.get(PrepaAdjurisAffectation, affectation_id) is None:
        raise HTTPException(404, detail="Affectation introuvable.")
    r = db.execute(
        select(PrepaAdjurisRemuneration).where(
            PrepaAdjurisRemuneration.affectation_id == affectation_id,
            PrepaAdjurisRemuneration.mois == mois,
        )
    ).scalar_one_or_none()
    if r is None and creer:
        r = PrepaAdjurisRemuneration(affectation_id=affectation_id, mois=mois)
        db.add(r)
    return r


def _ligne_du_tableau(db: Session, affectation_id: int, mois: str) -> dict | None:
    return next((l for l in _tableau(db) if l["affectation_id"] == affectation_id and l["mois"] == mois), None)


@router.put("/admin/remunerations")
def admin_fixer_remuneration(payload: RemunerationIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Fixe le montant d'un mois (remplace le calcul) et/ou sa note.
    montant_cents: null revient au montant calculé."""
    r = _ligne(db, payload.affectation_id, payload.mois)
    if "montant_cents" in payload.model_fields_set:
        if r.verse_le:
            raise HTTPException(400, detail="Mois déjà versé : annule le versement avant de changer le montant.")
        r.montant_cents = payload.montant_cents
    if payload.note is not None:
        r.note = payload.note.strip()
    r.updated_by_id = admin.id
    db.commit()
    return _ligne_du_tableau(db, payload.affectation_id, payload.mois)


@router.post("/admin/remunerations/verser")
def admin_verser(payload: VerserIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """À appeler après le virement : le montant retenu est figé et le mois
    marqué versé. Les mois déjà versés sont ignorés."""
    tableau = {(l["affectation_id"], l["mois"]): l for l in _tableau(db)}
    now = svc.utcnow()
    verses, ignores = [], []
    for l in payload.lignes:
        cle = (l.affectation_id, l.mois)
        t = tableau.get(cle)
        if t is None or t["verse_le"]:
            ignores.append({"affectation_id": l.affectation_id, "mois": l.mois})
            continue
        r = _ligne(db, *cle)
        r.montant_cents = t["montant_cents"]
        r.verse_le = now
        r.reference = (payload.reference or "").strip() or None
        r.updated_by_id = admin.id
        verses.append({"affectation_id": l.affectation_id, "mois": l.mois, "montant_cents": t["montant_cents"]})
    db.commit()
    return {"verses": verses, "ignores": ignores, "total_cents": sum(v["montant_cents"] for v in verses)}


@router.post("/admin/remunerations/annuler-versement")
def admin_annuler_versement(payload: LigneIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Le mois redevient à verser ; le montant reste fixé à ce qui avait été versé."""
    r = _ligne(db, payload.affectation_id, payload.mois, creer=False)
    if r is None or not r.verse_le:
        raise HTTPException(400, detail="Ce mois n'est pas marqué versé.")
    r.verse_le = None
    r.reference = None
    r.updated_by_id = admin.id
    db.commit()
    return _ligne_du_tableau(db, payload.affectation_id, payload.mois)


@router.patch("/admin/{professeur_id}")
def admin_update(
    professeur_id: int, payload: ProfesseurUpdateIn, db: Session = Depends(get_db), _: User = Depends(require_admin)
):
    p = _prof_or_404(db, professeur_id)
    if payload.prenom is not None:
        p.prenom = payload.prenom.strip()
    if payload.nom is not None:
        p.nom = payload.nom.strip()
    if payload.grade is not None:
        p.grade = payload.grade.strip()
        p.user.grade = p.grade
    if payload.status is not None:
        p.status = payload.status
    db.commit()
    return _prof_admin_row(db, p)


@router.delete("/admin/{professeur_id}")
def admin_delete(professeur_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """Détache le professeur. Ses documents restent aux étudiants ; le compte
    reprend le grade de départ."""
    p = _prof_or_404(db, professeur_id)
    _refuser_si_verse(db, [a.id for a in p.affectations])
    if p.user and p.user.grade == p.grade:
        p.user.grade = "Primo"
    db.delete(p)
    db.commit()
    return {"ok": True, "id": professeur_id}


@router.post("/admin/{professeur_id}/affectations")
def admin_add_affectation(
    professeur_id: int, payload: AffectationIn, db: Session = Depends(get_db), _: User = Depends(require_admin)
):
    p = _prof_or_404(db, professeur_id)
    _affecter(db, p, payload)
    db.commit()
    db.refresh(p)
    return _prof_admin_row(db, p)
