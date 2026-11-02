"""
app/routers/lib_prepa.py
========================
Vue « Prépa » de NAVIRE Lib (embeds/lib.html), qui remplace la page
Prép'AdJuris : elle n'apparaît que pour les étudiants inscrits à Prép'AdJuris
(inscription active), dans Lib comme dans Lib+.

  GET    /lib/prepa                          → niveau, matières (PDF, groupe,
                                               Discord), méthodo, emploi du
                                               temps, cours Lib de la classe,
                                               devoirs rendus
  POST   /lib/prepa/devoirs                  → dépôt d'une copie (PDF)
  GET    /lib/prepa/devoirs/{id}/fichier     → sa copie
  GET    /lib/prepa/devoirs/{id}/correction  → la copie corrigée par le professeur
  DELETE /lib/prepa/devoirs/{id}             → retrait (tant qu'elle n'est pas corrigée)

Les PDF des matières se lisent par GET /prepa/adjuris/ressources/{id}/download.

Aperçu admin : GET /lib/prepa?voir_comme=L1 (comptes admin seulement) montre
la vue d'un étudiant de L1 inscrit à toutes les matières du niveau, avec tous
les documents (y compris ceux réservés à un groupe, étiquetés). Sert à
vérifier que cours, dossiers et documents sont bien en place.

Cours Lib de la classe : documents Lib publiés dont la matière (ou le titre)
reprend le nom d'une matière Prép'AdJuris de l'étudiant, et ceux de son
niveau (LibDocument.niveau).
"""

from __future__ import annotations

import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File as FastAPIFile, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import MAX_UPLOAD_BYTES
from app.core.prepa_adjuris_config import (
    PREPA_MATIERE_NAMES,
    PREPA_NIVEAUX,
    matiere_niveau,
    matieres_du_niveau,
)
from app.core.prepa_storage import PREPA_SUBMISSIONS_DIR, delete_prepa_file, save_prepa_pdf
from app.db.database import get_db
from app.db.models import (
    LibDocument,
    PrepaAdjurisClasse,
    PrepaAdjurisDevoir,
    PrepaAdjurisEnrollment,
    PrepaAdjurisRessource,
    PrepaAdjurisSeance,
    User,
)
from app.routers.auth import get_current_user
from app.services import devoirs as dsvc

router = APIRouter(prefix="/lib/prepa", tags=["lib-prepa"])

DEVOIRS_DIR = PREPA_SUBMISSIONS_DIR / "adjuris"
SEANCES_PASSEES_JOURS = 7     # emploi du temps : la semaine écoulée…
SEANCES_A_VENIR_JOURS = 120   # … et les quatre prochains mois


# ============================================================
# Qui voit la vue Prépa
# ============================================================

def matieres_actives(db: Session, user: User) -> set[str]:
    return set(db.execute(
        select(PrepaAdjurisEnrollment.matiere_key).where(
            PrepaAdjurisEnrollment.user_id == user.id,
            PrepaAdjurisEnrollment.status == "active",
        )
    ).scalars().all())


def niveau_prepa(db: Session, user: User | None) -> str | None:
    """Niveau Prép'AdJuris de l'étudiant (inscription active), sinon None."""
    if not user:
        return None
    niveaux = sorted({matiere_niveau(k) for k in matieres_actives(db, user)})
    return niveaux[0] if niveaux else None


def apercu_valide(user: User | None, voir_comme: str | None) -> str | None:
    """Niveau d'aperçu demandé par un admin ; ignoré pour tout autre compte."""
    if not voir_comme or not user or not user.is_admin:
        return None
    v = voir_comme.strip().upper()
    if v not in PREPA_NIVEAUX:
        raise HTTPException(400, detail=f"Niveau inconnu : {voir_comme}.")
    return v


# ============================================================
# Outils
# ============================================================

def _norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return " ".join("".join(c if c.isalnum() else " " for c in s).split())


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _ressource(r: PrepaAdjurisRessource, classes: dict[int, PrepaAdjurisClasse]) -> dict:
    c = classes.get(r.classe_id) if r.classe_id else None
    return {
        "id": r.id,
        "titre": r.titre,
        "description": r.description,
        "size_bytes": r.size_bytes,
        "professeur": r.uploaded_by_id is not None,
        "groupe": c.nom if c else None,
        "created_at": _iso(r.created_at),
    }


def _seance(s: PrepaAdjurisSeance) -> dict:
    return {
        "id": s.id,
        "matiere_key": s.matiere_key,
        "matiere": PREPA_MATIERE_NAMES.get(s.matiere_key, s.matiere_key) if s.matiere_key else "Séance commune",
        "titre": s.titre,
        "date_debut": _iso(s.date_debut),
        "duree_minutes": s.duree_minutes,
        "lien": s.lien if (s.lien or "").startswith(("https://", "http://")) else "",
        "statut": s.statut,
    }


def _cours_lib(db: Session, user: User, niveau: str, matieres: list[str]) -> list[dict]:
    """Documents Lib publiés liés à la classe : par matière, puis du niveau,
    avec la progression de l'élève (comme le catalogue)."""
    from app.routers.lib import _elo, _progress_map, _summary

    noms = {k: _norm(PREPA_MATIERE_NAMES.get(k, k)) for k in matieres}
    docs = db.execute(
        select(LibDocument)
        .where(LibDocument.owner_id.is_(None), LibDocument.is_published.is_(True))
        .order_by(LibDocument.ordre, LibDocument.id)
    ).scalars().all()
    out = []
    for d in docs:
        dn = (d.niveau or "").strip().upper()
        if dn and dn != niveau:
            continue
        mat, titre = _norm(d.matiere), _norm(d.titre)
        cle = next(
            (k for k, n in noms.items() if n and ((mat and (n in mat or mat in n)) or n in titre)),
            None,
        )
        if cle is None and dn != niveau:
            continue
        out.append((d, cle))
    prog = _progress_map(db, user, [d.id for d, _ in out])
    elo = _elo(user)
    return [{**_summary(d, prog, elo), "matiere_key": cle} for d, cle in out]


# ============================================================
# Vue Prépa
# ============================================================

@router.get("")
def vue_prepa(
    voir_comme: str | None = Query(None, description="Aperçu admin : L1, L2, L3 ou M1"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.routers.prepa_adjuris_espace import _discord_url
    from app.services.professeurs import classe_row, groupes_de_l_etudiant, groupes_par_matiere

    apercu = apercu_valide(user, voir_comme)
    if apercu:
        niveau = apercu
        actives = set(matieres_du_niveau(niveau))
        groupes: set[int] | None = None          # aperçu : tous les documents
    else:
        actives = matieres_actives(db, user)
        niveaux = sorted({matiere_niveau(k) for k in actives})
        if not niveaux:
            return {"prepa": False, "apercu": None}
        niveau = niveaux[0]
        groupes = groupes_de_l_etudiant(db, user.id)

    par_matiere = groupes_par_matiere(db)
    classes = {c.id: c for cs in par_matiere.values() for c in cs}

    ressources = db.execute(
        select(PrepaAdjurisRessource)
        .where(PrepaAdjurisRessource.niveau == niveau, PrepaAdjurisRessource.is_published.is_(True))
        .order_by(PrepaAdjurisRessource.ordre, PrepaAdjurisRessource.created_at.desc(), PrepaAdjurisRessource.id)
    ).scalars().all()
    if groupes is not None:
        ressources = [r for r in ressources if r.classe_id is None or r.classe_id in groupes]
    for r in ressources:
        if r.classe_id and r.classe_id not in classes:
            c = db.get(PrepaAdjurisClasse, r.classe_id)
            if c:
                classes[c.id] = c

    cles = matieres_du_niveau(niveau)
    cours = _cours_lib(db, user, niveau, [k for k in cles if k in actives])

    matieres = []
    for key in cles:
        debloquee = key in actives
        # Le groupe de l'étudiant dans la matière (aperçu : le premier groupe actif).
        if apercu:
            mes_groupes = (par_matiere.get(key) or [])[:1]
        else:
            mes_groupes = [c for c in par_matiere.get(key) or [] if groupes and c.id in groupes][:1]
        matieres.append({
            "matiere_key": key,
            "label": PREPA_MATIERE_NAMES.get(key, key),
            "debloquee": debloquee,
            "ressources": [_ressource(r, classes) for r in ressources if r.matiere_key == key] if debloquee else [],
            "cours_lib": [d["id"] for d in cours if d["matiere_key"] == key] if debloquee else [],
            "groupe": classe_row(mes_groupes[0]) if (debloquee and mes_groupes) else None,
        })

    maintenant = datetime.now(timezone.utc)
    seances = db.execute(
        select(PrepaAdjurisSeance)
        .where(
            PrepaAdjurisSeance.niveau == niveau,
            PrepaAdjurisSeance.date_debut >= maintenant - timedelta(days=SEANCES_PASSEES_JOURS),
            PrepaAdjurisSeance.date_debut <= maintenant + timedelta(days=SEANCES_A_VENIR_JOURS),
        )
        .order_by(PrepaAdjurisSeance.date_debut)
    ).scalars().all()
    seances = [s for s in seances if s.matiere_key is None or s.matiere_key in actives]
    prochaine = next(
        (s for s in seances if s.statut == "prevue" and dsvc.aware(s.date_debut) >= maintenant - timedelta(hours=1)),
        None,
    )

    devoirs = [] if apercu else db.execute(
        select(PrepaAdjurisDevoir)
        .where(PrepaAdjurisDevoir.user_id == user.id)
        .order_by(PrepaAdjurisDevoir.created_at.desc(), PrepaAdjurisDevoir.id.desc())
    ).scalars().all()

    return {
        "prepa": True,
        "apercu": apercu,
        "niveau": niveau,
        "matieres": matieres,
        "methodo": [_ressource(r, classes) for r in ressources if r.matiere_key is None],
        "seances": [_seance(s) for s in seances],
        "prochaine_seance": _seance(prochaine) if prochaine else None,
        "cours_lib": cours,
        "devoirs": [dsvc.devoir_row(db, d) for d in devoirs],
        "devoir_max_bytes": MAX_UPLOAD_BYTES,
        "discord_url": _discord_url(),
    }


# ============================================================
# Devoirs (côté étudiant)
# ============================================================

def _mon_devoir(db: Session, user: User, devoir_id: int) -> PrepaAdjurisDevoir:
    d = db.get(PrepaAdjurisDevoir, devoir_id)
    if d is None or d.user_id != user.id:
        raise HTTPException(404, detail="Copie introuvable.")
    return d


@router.post("/devoirs")
async def deposer_devoir(
    background: BackgroundTasks,
    file: UploadFile = FastAPIFile(...),
    matiere_key: str = Form(...),
    titre: str = Form(""),
    message: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if matiere_key not in matieres_actives(db, user):
        raise HTTPException(403, detail="Tu n'es pas inscrit à cette matière.")
    meta = await save_prepa_pdf(file, DEVOIRS_DIR / str(user.id), max_bytes=MAX_UPLOAD_BYTES)
    d = PrepaAdjurisDevoir(
        user_id=user.id,
        matiere_key=matiere_key,
        classe_id=dsvc.groupe_pour(db, user.id, matiere_key),
        titre=(titre.strip() or Path(meta["filename_original"]).stem or "Copie")[:200],
        message=message.strip()[:4000],
        filename_original=meta["filename_original"][:255],
        filename_stored=meta["filename_stored"],
        path=meta["path"],
        size_bytes=meta["size_bytes"],
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    profs = dsvc.emails_professeurs(db, d.classe_id)
    if profs:
        background.add_task(
            dsvc.mail_nouvelle_copie, profs, user.username or user.email,
            PREPA_MATIERE_NAMES.get(matiere_key, matiere_key), d.titre,
        )
    return dsvc.devoir_row(db, d)


@router.get("/devoirs/{devoir_id}/fichier")
def ma_copie(devoir_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    d = _mon_devoir(db, user, devoir_id)
    if not Path(d.path).exists():
        raise HTTPException(404, detail="Fichier introuvable sur le disque.")
    return FileResponse(d.path, media_type="application/pdf", filename=d.filename_original,
                        content_disposition_type="inline")


@router.get("/devoirs/{devoir_id}/correction")
def ma_correction(devoir_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    d = _mon_devoir(db, user, devoir_id)
    if not d.correction_path or not Path(d.correction_path).exists():
        raise HTTPException(404, detail="Pas de copie corrigée.")
    return FileResponse(d.correction_path, media_type="application/pdf",
                        filename=d.correction_filename or "correction.pdf", content_disposition_type="inline")


@router.delete("/devoirs/{devoir_id}")
def retirer_devoir(devoir_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    d = _mon_devoir(db, user, devoir_id)
    if d.statut == "corrige":
        raise HTTPException(400, detail="Copie déjà corrigée : elle ne peut plus être retirée.")
    delete_prepa_file(d.path)
    db.delete(d)
    db.commit()
    return {"ok": True, "id": devoir_id}
