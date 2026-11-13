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
  POST   /lib/prepa/devoirs/texte            → copie saisie dans NAVIRE (écran blanc)
  GET    /lib/prepa/devoirs/{id}/fichier     → sa copie
  GET    /lib/prepa/devoirs/{id}/correction  → la copie corrigée par le professeur
  DELETE /lib/prepa/devoirs/{id}             → retrait (tant qu'elle n'est pas corrigée)
  GET    /lib/prepa/devoirs/{id}/copie       → la copie mise en forme par NAVIRE, avec
                                               les révisions et commentaires du
                                               correcteur une fois la correction rendue
  GET    /lib/prepa/devoirs/{id}/export?format=docx|pdf
                                             → la copie à télécharger : .docx avec les
                                               révisions et commentaires Word, ou .pdf
  POST   /lib/prepa/suivis/{document_id}     → suivre un cours Lib dans l'espace Prépa
  DELETE /lib/prepa/suivis/{document_id}     → ne plus le suivre

Ces routes servent l'onglet Prépa (embeds/prepa.html, page /prepa), sorti de
NAVIRE Lib : Lib ne garde que les documents à pastille « Prépa » dans sa
bibliothèque et le bouton « Suivre dans Prépa ».

Copies : .docx ou .pdf texte. NAVIRE les met en forme au dépôt
(app/services/copies.py) ; un fichier illisible (PDF scanné) est refusé.

Documents Prépa : chaque PDF de l'espace (matières, méthodo, dépôts des
chargés de TD) est un document NAVIRE Lib (kind "prepa"), lu comme un cours
dans la bibliothèque et dans la vue Prépa, avec la pastille Prépa. Pas de PDF
à ouvrir. Accès : les règles de l'espace (matière souscrite, groupe pour un
dépôt de chargé de TD, niveau pour la méthodo).

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
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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
    PrepaAdjurisCours,
    PrepaAdjurisClasse,
    PrepaAdjurisDevoir,
    PrepaAdjurisEnrollment,
    PrepaAdjurisRessource,
    PrepaAdjurisSeance,
    PrepaSuivi,
    User,
)
from app.routers.auth import get_current_user
from app.services import copies as csvc
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


# ============================================================
# Documents Prépa = documents NAVIRE Lib
# ============================================================

def ressources_visibles(
    db: Session, user: User, apercu: str | None = None
) -> tuple[str | None, set[str], list[PrepaAdjurisRessource]]:
    """
    (niveau, matières souscrites, ressources publiées visibles) de l'étudiant.
    Aperçu admin : toutes les matières du niveau, tous les groupes.
    """
    from app.services.professeurs import groupes_de_l_etudiant

    if apercu:
        niveau, actives, groupes = apercu, set(matieres_du_niveau(apercu)), None
    else:
        actives = matieres_actives(db, user)
        niveaux = sorted({matiere_niveau(k) for k in actives})
        if not niveaux:
            return None, set(), []
        niveau, groupes = niveaux[0], groupes_de_l_etudiant(db, user.id)
    rows = db.execute(
        select(PrepaAdjurisRessource)
        .where(PrepaAdjurisRessource.niveau == niveau, PrepaAdjurisRessource.is_published.is_(True))
        .order_by(PrepaAdjurisRessource.ordre, PrepaAdjurisRessource.created_at.desc(), PrepaAdjurisRessource.id)
    ).scalars().all()
    rows = [
        r for r in rows
        if (r.matiere_key is None or r.matiere_key in actives)
        and (groupes is None or r.classe_id is None or r.classe_id in groupes)
    ]
    return niveau, actives, rows


def ressource_accessible(db: Session, user: User, r: PrepaAdjurisRessource) -> bool:
    """Mêmes règles que le téléchargement de l'espace ; un admin voit tout."""
    from app.services.professeurs import groupes_de_l_etudiant, ressource_visible

    if not r.is_published:
        return False
    if user.is_admin:
        return True
    actives = matieres_actives(db, user)
    if r.matiere_key:
        return r.matiere_key in actives and ressource_visible(r, groupes_de_l_etudiant(db, user.id))
    return r.niveau in {matiere_niveau(k) for k in actives}


def _kicker(r: PrepaAdjurisRessource) -> str:
    nom = PREPA_MATIERE_NAMES.get(r.matiere_key, r.matiere_key) if r.matiere_key else "Méthodologie"
    return f"Prép'AdJuris · {nom}"[:120]


def sync_docs_prepa(db: Session, ressources: list[PrepaAdjurisRessource]) -> dict[int, LibDocument]:
    """
    Un document Lib par ressource : créé vide (lu à la première ouverture),
    tenu à jour (titre, description), supprimé avec la ressource.
    Renvoie {ressource_id: document} pour les ressources données.
    """
    from app.routers.lib import _delete_doc

    docs = db.execute(select(LibDocument).where(LibDocument.prepa_ressource_id.is_not(None))).scalars().all()
    existantes = set(db.execute(select(PrepaAdjurisRessource.id)).scalars().all())
    change = False
    par_res: dict[int, LibDocument] = {}
    for d in docs:
        if d.prepa_ressource_id not in existantes:
            _delete_doc(db, d)
            change = True
        else:
            par_res[d.prepa_ressource_id] = d
    for r in ressources:
        titre = (r.titre or "Document")[:200]
        description = r.description or ("Document de ton chargé de TD." if r.uploaded_by_id else "")
        matiere = PREPA_MATIERE_NAMES.get(r.matiere_key, "") if r.matiere_key else "Méthodologie"
        d = par_res.get(r.id)
        if d is None:
            d = LibDocument(
                kind="prepa", titre=titre, description=description, niveau=r.niveau, matiere=matiere,
                kicker=_kicker(r), prepa_ressource_id=r.id, source_filename=(r.filename_original or "")[:255],
                size_bytes=int(r.size_bytes or 0), content={}, is_published=False,
            )
            db.add(d)
            par_res[r.id] = d
            change = True
        elif (d.titre, d.description, d.matiere, d.kicker) != (titre, description, matiere, _kicker(r)):
            d.titre, d.description, d.matiere, d.kicker = titre, description, matiere, _kicker(r)
            change = True
    if change:
        try:
            db.commit()
        except IntegrityError:  # deux synchronisations en même temps
            db.rollback()
            par_res = {
                d.prepa_ressource_id: d for d in db.execute(
                    select(LibDocument).where(LibDocument.prepa_ressource_id.is_not(None))
                ).scalars().all()
            }
    return {r.id: par_res[r.id] for r in ressources if r.id in par_res}


def docs_prepa_visibles(db: Session, user: User | None, apercu: str | None = None) -> list[LibDocument]:
    """Documents Prépa de l'étudiant (catalogue Lib), dans l'ordre de l'espace."""
    if not user:
        return []
    _, _, ressources = ressources_visibles(db, user, apercu)
    if not ressources:
        return []
    par_res = sync_docs_prepa(db, ressources)
    return [par_res[r.id] for r in ressources if r.id in par_res]


def supprimer_doc_prepa(db: Session, ressource_id: int) -> None:
    """À appeler avant de supprimer une ressource (SQLite ne cascade pas)."""
    from app.routers.lib import _delete_doc

    d = db.execute(
        select(LibDocument).where(LibDocument.prepa_ressource_id == ressource_id)
    ).scalar_one_or_none()
    if d is not None:
        _delete_doc(db, d)


def _seance(s: PrepaAdjurisSeance, en_cours: set[int] | None = None) -> dict:
    return {
        "id": s.id,
        "matiere_key": s.matiere_key,
        "matiere": PREPA_MATIERE_NAMES.get(s.matiere_key, s.matiere_key) if s.matiere_key else "Séance commune",
        "titre": s.titre,
        "date_debut": _iso(s.date_debut),
        # Déplacée par la console : la date prévue à l'origine.
        "date_initiale": _iso(s.date_initiale),
        "duree_minutes": s.duree_minutes,
        "lien": s.lien if (s.lien or "").startswith(("https://", "http://")) else "",
        "statut": s.statut,
        # Le professeur a lancé le cours sur Discord.
        "en_cours": bool(en_cours and s.id in en_cours),
    }


def _facturation(db: Session, user: User) -> list[dict]:
    """Par matière : statut de l'inscription et prochain prélèvement (fin de
    mois) lu dans l'échéancier."""
    rows = db.execute(
        select(PrepaAdjurisEnrollment).where(
            PrepaAdjurisEnrollment.user_id == user.id,
            PrepaAdjurisEnrollment.status.in_(("active", "payment_failed", "suspendu")),
        )
    ).scalars().all()
    from app.core.prepa_adjuris_config import PREPA_PRIX_SEANCE_CENTS

    mois_courant = datetime.now(timezone.utc).strftime("%Y-%m")
    out = []
    for e in rows:
        mois = (e.echeancier or {}).get("mois") or {}
        prochain = next(
            ({"mois": k, "seances": n, "montant_cents": n * PREPA_PRIX_SEANCE_CENTS}
             for k, n in sorted(mois.items()) if k >= mois_courant and n),
            None,
        )
        out.append({
            "matiere_key": e.matiere_key,
            "matiere": PREPA_MATIERE_NAMES.get(e.matiere_key, e.matiere_key),
            "statut": e.status,
            "impaye": e.status == "payment_failed" or bool(e.impaye_depuis),
            "prochain": prochain,
        })
    return out


def _suivis(db: Session, user: User) -> list[dict]:
    """Cours Lib suivis depuis Lib (« Suivre dans Prépa »), avec la progression."""
    from app.routers.lib import _elo, _progress_map, _summary

    docs = db.execute(
        select(LibDocument)
        .join(PrepaSuivi, PrepaSuivi.document_id == LibDocument.id)
        .where(PrepaSuivi.user_id == user.id, LibDocument.is_published.is_(True))
        .order_by(PrepaSuivi.created_at.desc(), PrepaSuivi.id.desc())
    ).scalars().all()
    prog = _progress_map(db, user, [d.id for d in docs])
    elo = _elo(user)
    return [_summary(d, prog, elo) for d in docs]


def _cours_lib(db: Session, user: User, niveau: str, matieres: list[str]) -> list[dict]:
    """Documents Lib publiés liés à la classe : par matière, puis du niveau,
    avec la progression de l'élève (comme le catalogue)."""
    from app.routers.lib import _elo, _progress_map, _summary

    noms = {k: _norm(PREPA_MATIERE_NAMES.get(k, k)) for k in matieres}
    docs = db.execute(
        select(LibDocument)
        .where(
            LibDocument.owner_id.is_(None), LibDocument.prepa_ressource_id.is_(None),
            LibDocument.is_published.is_(True),
        )
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

    from app.routers.lib import _elo, _progress_map, _summary

    apercu = apercu_valide(user, voir_comme)
    niveau, actives, ressources = ressources_visibles(db, user, apercu)
    if not niveau:
        return {"prepa": False, "apercu": None}
    groupes = None if apercu else groupes_de_l_etudiant(db, user.id)

    par_matiere = groupes_par_matiere(db)
    classes = {c.id: c for cs in par_matiere.values() for c in cs}
    for r in ressources:
        if r.classe_id and r.classe_id not in classes:
            c = db.get(PrepaAdjurisClasse, r.classe_id)
            if c:
                classes[c.id] = c

    # Documents Prépa (lus comme des cours Lib), puis les cours Lib de la classe.
    par_res = sync_docs_prepa(db, ressources)
    docs_prepa = [(r, par_res[r.id]) for r in ressources if r.id in par_res]
    prog = _progress_map(db, user, [d.id for _, d in docs_prepa])
    elo = _elo(user)
    prepa = [
        {
            **_summary(d, prog, elo),
            "matiere_key": r.matiere_key,
            "methodo": r.matiere_key is None,
            "groupe": classes[r.classe_id].nom if r.classe_id in classes else None,
            "professeur": r.uploaded_by_id is not None,
        }
        for r, d in docs_prepa
    ]
    cles = matieres_du_niveau(niveau)
    cours = prepa + _cours_lib(db, user, niveau, [k for k in cles if k in actives])

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
    en_cours = set(db.execute(
        select(PrepaAdjurisCours.seance_id).where(
            PrepaAdjurisCours.statut == "en_cours", PrepaAdjurisCours.seance_id.is_not(None),
        )
    ).scalars().all())

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
        "seances": [_seance(s, en_cours) for s in seances],
        "prochaine_seance": _seance(prochaine, en_cours) if prochaine else None,
        "cours_lib": cours,
        "suivis": [] if apercu else _suivis(db, user),
        "devoirs": [dsvc.devoir_row(db, d) for d in devoirs],
        "devoir_max_bytes": MAX_UPLOAD_BYTES,
        "devoir_formats": [".docx", ".pdf"],
        "facturation": [] if apercu else _facturation(db, user),
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
    meta = await save_prepa_pdf(file, DEVOIRS_DIR / str(user.id), max_bytes=MAX_UPLOAD_BYTES, allow_docx=True)
    try:
        contenu = csvc.mettre_en_forme(Path(meta["path"]).read_bytes(), meta["filename_original"])
    except Exception as e:  # noqa: BLE001 — fichier illisible : refusé, rien n'est gardé
        delete_prepa_file(meta["path"])
        raise HTTPException(422, detail=str(e) if isinstance(e, ValueError) else
                            "Ce fichier n'a pas pu être lu : dépose un .docx ou un PDF texte.")
    return creer_devoir(db, user, background, matiere_key=matiere_key,
                        titre=(titre.strip() or Path(meta["filename_original"]).stem or "Copie"),
                        message=message, meta=meta, contenu=contenu)


def creer_devoir(db: Session, user: User, background: BackgroundTasks, *, matiere_key: str, titre: str,
                 message: str, meta: dict, contenu: dict) -> dict:
    """Enregistre la copie (fichier déjà écrit, contenu mis en forme) et
    prévient les professeurs du groupe."""
    d = PrepaAdjurisDevoir(
        user_id=user.id,
        matiere_key=matiere_key,
        classe_id=dsvc.groupe_pour(db, user.id, matiere_key),
        titre=(titre.strip() or "Copie")[:200],
        message=(message or "").strip()[:4000],
        filename_original=meta["filename_original"][:255],
        filename_stored=meta["filename_stored"],
        path=meta["path"],
        size_bytes=meta["size_bytes"],
        contenu=contenu,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    from app.services.elo_rewards import reward_prepa_devoir
    reward_prepa_devoir(db, user, f"devoir:{d.id}")   # +25 elo
    db.refresh(d)
    profs = dsvc.emails_professeurs(db, d.classe_id)
    if profs:
        background.add_task(
            dsvc.mail_nouvelle_copie, profs, user.username or user.email,
            PREPA_MATIERE_NAMES.get(matiere_key, matiere_key), d.titre,
        )
    return dsvc.devoir_row(db, d)


def ecrire_saisie(user: User, titre: str, blocs) -> tuple[dict, dict]:
    """Copie saisie dans NAVIRE → (meta du .docx écrit sur le disque, contenu)."""
    from uuid import uuid4

    try:
        contenu = csvc.blocs_saisis(blocs)
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    data = csvc.docx_saisie(titre, contenu)
    dest = DEVOIRS_DIR / str(user.id)
    dest.mkdir(parents=True, exist_ok=True)
    nom = f"{uuid4().hex}.docx"
    (dest / nom).write_bytes(data)
    base = "".join(ch for ch in (titre or "copie") if ch.isalnum() or ch in " -_").strip()[:80] or "copie"
    return {"filename_original": f"{base}.docx", "filename_stored": nom, "path": str(dest / nom),
            "size_bytes": len(data)}, contenu


class CopieSaisieIn(BaseModel):
    matiere_key: str = Field(..., max_length=64)
    titre: str = Field("", max_length=200)
    message: str = Field("", max_length=4000)
    blocs: list = Field(default_factory=list)


@router.post("/devoirs/texte")
def deposer_copie_saisie(payload: CopieSaisieIn, background: BackgroundTasks,
                         db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Copie rédigée directement dans NAVIRE (écran blanc, sans fichier)."""
    if payload.matiere_key not in matieres_actives(db, user):
        raise HTTPException(403, detail="Tu n'es pas inscrit à cette matière.")
    titre = payload.titre.strip() or "Copie"
    meta, contenu = ecrire_saisie(user, titre, payload.blocs)
    return creer_devoir(db, user, background, matiere_key=payload.matiere_key, titre=titre,
                        message=payload.message, meta=meta, contenu=contenu)


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def media_type(path: str) -> str:
    return DOCX_MIME if (path or "").lower().endswith(".docx") else "application/pdf"


@router.get("/devoirs/{devoir_id}/fichier")
def ma_copie(devoir_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    d = _mon_devoir(db, user, devoir_id)
    if not Path(d.path).exists():
        raise HTTPException(404, detail="Fichier introuvable sur le disque.")
    return FileResponse(d.path, media_type=media_type(d.path), filename=d.filename_original,
                        content_disposition_type="inline")


@router.get("/devoirs/{devoir_id}/copie")
def ma_copie_en_ligne(devoir_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """La copie mise en forme ; révisions et commentaires une fois corrigée."""
    return csvc.copie_row(db, _mon_devoir(db, user, devoir_id), pour_prof=False)


def exporter(db: Session, d: PrepaAdjurisDevoir, format: str, *, pour_prof: bool) -> Response:
    anns = csvc.annotations_visibles(d, pour_prof=pour_prof)
    if format == "docx":
        data, mime = csvc.export_docx(db, d, anns), DOCX_MIME
    elif format == "pdf":
        data, mime = csvc.export_pdf(db, d, anns), "application/pdf"
    else:
        raise HTTPException(400, detail="Format : docx ou pdf.")
    from urllib.parse import quote

    nom = csvc.nom_export(d, format, bool(anns) or d.statut == "corrige")
    return Response(data, media_type=mime, headers={
        "Content-Disposition": f"attachment; filename=\"copie.{format}\"; filename*=UTF-8''{quote(nom)}",
    })


@router.get("/devoirs/{devoir_id}/export")
def exporter_ma_copie(
    devoir_id: int,
    format: str = Query("docx", description="docx | pdf"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return exporter(db, _mon_devoir(db, user, devoir_id), format, pour_prof=False)


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
    from app.services.elo_rewards import revoke_prepa_devoir
    revoke_prepa_devoir(db, user.id, f"devoir:{devoir_id}")   # copie retirée : elo repris
    return {"ok": True, "id": devoir_id}


# ============================================================
# Cours Lib suivis dans l'espace Prépa
# ============================================================

@router.post("/suivis/{document_id}")
def suivre(document_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not niveau_prepa(db, user):
        raise HTTPException(403, detail="Réservé aux inscrits Prép'AdJuris.")
    d = db.get(LibDocument, document_id)
    if d is None or not d.is_published or d.owner_id is not None:
        raise HTTPException(404, detail="Cours introuvable.")
    deja = db.execute(
        select(PrepaSuivi).where(PrepaSuivi.user_id == user.id, PrepaSuivi.document_id == document_id)
    ).scalar_one_or_none()
    if deja is None:
        db.add(PrepaSuivi(user_id=user.id, document_id=document_id))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    return {"ok": True, "document_id": document_id, "suivi": True}


@router.delete("/suivis/{document_id}")
def ne_plus_suivre(document_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    db.query(PrepaSuivi).filter(
        PrepaSuivi.user_id == user.id, PrepaSuivi.document_id == document_id
    ).delete(synchronize_session=False)
    db.commit()
    return {"ok": True, "document_id": document_id, "suivi": False}


def suivis_ids(db: Session, user: User | None) -> set[int]:
    if not user:
        return set()
    return set(db.execute(select(PrepaSuivi.document_id).where(PrepaSuivi.user_id == user.id)).scalars().all())
