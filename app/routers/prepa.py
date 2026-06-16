"""
app/routers/prepa.py
====================
Router PREPASSERELLE.

Deux publics :
  - ADMIN  (gate: verify_admin_code, header X-Admin-Code) : crée et publie les
    cours et exercices, uploade les PDF (leçons, sujets, corrigés), liste et
    note les copies, surveille le stockage.
  - ÉLÈVE  (gate: get_current_user + check_prepa_access) : consulte les cours
    publiés de SON année, télécharge les PDF, voit les exercices et leurs
    deadlines, dépose sa copie, consulte ses notes, alimente le calendrier.

Le filtrage par année (un L1 ne voit que L1) est appliqué ici, car il dépend de
l'objet cours/exercice (annee == user.prepa_annee).

Stockage disque délégué à app/core/prepa_storage.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    UploadFile,
    File as FastAPIFile,
    Form,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db import models
from app.routers.auth import get_current_user
from app.routers.admin import verify_admin_code
from app.core.limits import check_prepa_access, has_active_prepa_access
from app.core.prepa_storage import (
    save_prepa_pdf,
    delete_prepa_file,
    get_prepa_storage_status,
    PREPA_COURSES_DIR,
    PREPA_SUBJECTS_DIR,
    PREPA_CORRECTIONS_DIR,
    PREPA_SUBMISSIONS_DIR,
    PREPA_COURSE_MAX_BYTES,
)

router = APIRouter(prefix="/prepa", tags=["prepa"])

VALID_ANNEES = {"L1", "L2", "L3"}


# ============================================================
# Schémas (JSON) — les uploads passent par Form/UploadFile
# ============================================================

class CourseCreate(BaseModel):
    annee: str
    titre: str
    description: str = ""
    ordre: int = 0


class CourseUpdate(BaseModel):
    annee: str | None = None
    titre: str | None = None
    description: str | None = None
    ordre: int | None = None
    is_published: bool | None = None


class ExerciseCreate(BaseModel):
    annee: str
    week_number: int
    titre: str
    consigne: str = ""
    due_date: datetime | None = None


class ExerciseUpdate(BaseModel):
    annee: str | None = None
    week_number: int | None = None
    titre: str | None = None
    consigne: str | None = None
    due_date: datetime | None = None
    is_published: bool | None = None
    corrige_published: bool | None = None


class GradePayload(BaseModel):
    note: float | None = None
    feedback: str = ""


# ============================================================
# Utils internes
# ============================================================

def _validate_annee(annee: str) -> None:
    if annee not in VALID_ANNEES:
        raise HTTPException(status_code=400, detail="Année invalide. Utilise L1, L2 ou L3.")


def _course_or_404(db: Session, course_id: int) -> models.PrepaCourse:
    c = db.execute(
        select(models.PrepaCourse).where(models.PrepaCourse.id == course_id)
    ).scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Cours introuvable.")
    return c


def _course_file_or_404(db: Session, file_id: int) -> models.PrepaCourseFile:
    f = db.execute(
        select(models.PrepaCourseFile).where(models.PrepaCourseFile.id == file_id)
    ).scalar_one_or_none()
    if not f:
        raise HTTPException(status_code=404, detail="Fichier introuvable.")
    return f


def _exercise_or_404(db: Session, exercise_id: int) -> models.PrepaExercise:
    e = db.execute(
        select(models.PrepaExercise).where(models.PrepaExercise.id == exercise_id)
    ).scalar_one_or_none()
    if not e:
        raise HTTPException(status_code=404, detail="Exercice introuvable.")
    return e


def _submission_or_404(db: Session, submission_id: int) -> models.PrepaSubmission:
    s = db.execute(
        select(models.PrepaSubmission).where(models.PrepaSubmission.id == submission_id)
    ).scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Copie introuvable.")
    return s


def _file_response(path: str, download_name: str) -> FileResponse:
    """Sert un PDF depuis le disque, ou 404 si le fichier physique a disparu."""
    p = Path(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail="Fichier physique introuvable sur le disque.")
    return FileResponse(path, media_type="application/pdf", filename=download_name)


def _serialize_course(c: models.PrepaCourse, with_files: bool = False) -> dict:
    data = {
        "id": c.id,
        "annee": c.annee,
        "titre": c.titre,
        "description": c.description,
        "ordre": c.ordre,
        "is_published": c.is_published,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }
    if with_files:
        data["files"] = [_serialize_course_file(f) for f in c.files]
    return data


def _serialize_course_file(f: models.PrepaCourseFile) -> dict:
    return {
        "id": f.id,
        "course_id": f.course_id,
        "titre": f.titre,
        "filename_original": f.filename_original,
        "size_bytes": f.size_bytes,
        "ordre": f.ordre,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }


def _serialize_exercise_admin(e: models.PrepaExercise) -> dict:
    """Vue admin : tout est visible, y compris l'état du corrigé."""
    return {
        "id": e.id,
        "annee": e.annee,
        "week_number": e.week_number,
        "titre": e.titre,
        "consigne": e.consigne,
        "has_subject": bool(e.subject_path),
        "subject_filename": e.subject_filename_original,
        "has_corrige": bool(e.corrige_path),
        "corrige_filename": e.corrige_filename_original,
        "corrige_published": e.corrige_published,
        "due_date": e.due_date.isoformat() if e.due_date else None,
        "is_published": e.is_published,
        "created_at": e.created_at.isoformat() if e.created_at else None,
        "updated_at": e.updated_at.isoformat() if e.updated_at else None,
    }


def _serialize_exercise_student(
    e: models.PrepaExercise,
    submission: models.PrepaSubmission | None,
) -> dict:
    """Vue élève : le corrigé n'est signalé téléchargeable que s'il est publié."""
    now = datetime.now(timezone.utc)
    is_late = bool(e.due_date and now > e.due_date)
    return {
        "id": e.id,
        "annee": e.annee,
        "week_number": e.week_number,
        "titre": e.titre,
        "consigne": e.consigne,
        "has_subject": bool(e.subject_path),
        "corrige_available": bool(e.corrige_published and e.corrige_path),
        "due_date": e.due_date.isoformat() if e.due_date else None,
        "is_late": is_late,
        "submission": _serialize_submission(submission) if submission else None,
    }


def _serialize_submission(s: models.PrepaSubmission) -> dict:
    return {
        "id": s.id,
        "exercise_id": s.exercise_id,
        "filename_original": s.filename_original,
        "size_bytes": s.size_bytes,
        "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
        "status": s.status,
        "note": s.note,
        "feedback": s.feedback,
        "corrected_at": s.corrected_at.isoformat() if s.corrected_at else None,
    }


# ════════════════════════════════════════════════════════════
# ADMIN — COURS
# ════════════════════════════════════════════════════════════

@router.post("/admin/courses", dependencies=[Depends(verify_admin_code)])
def admin_create_course(payload: CourseCreate, db: Session = Depends(get_db)):
    _validate_annee(payload.annee)
    c = models.PrepaCourse(
        annee=payload.annee,
        titre=payload.titre,
        description=payload.description,
        ordre=payload.ordre,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return _serialize_course(c)


@router.get("/admin/courses", dependencies=[Depends(verify_admin_code)])
def admin_list_courses(db: Session = Depends(get_db)):
    rows = db.execute(
        select(models.PrepaCourse).order_by(
            models.PrepaCourse.annee, models.PrepaCourse.ordre, models.PrepaCourse.id
        )
    ).scalars().all()
    return {"items": [_serialize_course(c, with_files=True) for c in rows]}


@router.patch("/admin/courses/{course_id}", dependencies=[Depends(verify_admin_code)])
def admin_update_course(course_id: int, payload: CourseUpdate, db: Session = Depends(get_db)):
    c = _course_or_404(db, course_id)
    if payload.annee is not None:
        _validate_annee(payload.annee)
        c.annee = payload.annee
    if payload.titre is not None:
        c.titre = payload.titre
    if payload.description is not None:
        c.description = payload.description
    if payload.ordre is not None:
        c.ordre = payload.ordre
    if payload.is_published is not None:
        c.is_published = payload.is_published
    db.commit()
    db.refresh(c)
    return _serialize_course(c)


@router.delete("/admin/courses/{course_id}", dependencies=[Depends(verify_admin_code)])
def admin_delete_course(course_id: int, db: Session = Depends(get_db)):
    c = _course_or_404(db, course_id)
    # Supprime les fichiers disque des leçons avant de casser les lignes.
    for f in list(c.files):
        delete_prepa_file(f.path)
    db.delete(c)  # cascade supprime les PrepaCourseFile en base
    db.commit()
    return {"ok": True, "deleted_course_id": course_id}


@router.post("/admin/courses/{course_id}/files", dependencies=[Depends(verify_admin_code)])
async def admin_upload_course_file(
    course_id: int,
    file: UploadFile = FastAPIFile(...),
    titre: str = Form(...),
    ordre: int = Form(0),
    db: Session = Depends(get_db),
):
    c = _course_or_404(db, course_id)
    meta = await save_prepa_pdf(
        file,
        PREPA_COURSES_DIR / str(c.id),
        max_bytes=PREPA_COURSE_MAX_BYTES,
    )
    row = models.PrepaCourseFile(
        course_id=c.id,
        titre=titre,
        filename_original=meta["filename_original"],
        filename_stored=meta["filename_stored"],
        path=meta["path"],
        size_bytes=meta["size_bytes"],
        ordre=ordre,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _serialize_course_file(row)


@router.delete("/admin/course-files/{file_id}", dependencies=[Depends(verify_admin_code)])
def admin_delete_course_file(file_id: int, db: Session = Depends(get_db)):
    f = _course_file_or_404(db, file_id)
    delete_prepa_file(f.path)
    db.delete(f)
    db.commit()
    return {"ok": True, "deleted_file_id": file_id}


# ════════════════════════════════════════════════════════════
# ADMIN — EXERCICES
# ════════════════════════════════════════════════════════════

@router.post("/admin/exercises", dependencies=[Depends(verify_admin_code)])
def admin_create_exercise(payload: ExerciseCreate, db: Session = Depends(get_db)):
    _validate_annee(payload.annee)
    e = models.PrepaExercise(
        annee=payload.annee,
        week_number=payload.week_number,
        titre=payload.titre,
        consigne=payload.consigne,
        due_date=payload.due_date,
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return _serialize_exercise_admin(e)


@router.get("/admin/exercises", dependencies=[Depends(verify_admin_code)])
def admin_list_exercises(db: Session = Depends(get_db)):
    rows = db.execute(
        select(models.PrepaExercise).order_by(
            models.PrepaExercise.annee, models.PrepaExercise.week_number, models.PrepaExercise.id
        )
    ).scalars().all()
    return {"items": [_serialize_exercise_admin(e) for e in rows]}


@router.patch("/admin/exercises/{exercise_id}", dependencies=[Depends(verify_admin_code)])
def admin_update_exercise(exercise_id: int, payload: ExerciseUpdate, db: Session = Depends(get_db)):
    e = _exercise_or_404(db, exercise_id)
    if payload.annee is not None:
        _validate_annee(payload.annee)
        e.annee = payload.annee
    if payload.week_number is not None:
        e.week_number = payload.week_number
    if payload.titre is not None:
        e.titre = payload.titre
    if payload.consigne is not None:
        e.consigne = payload.consigne
    if payload.due_date is not None:
        e.due_date = payload.due_date
    if payload.is_published is not None:
        e.is_published = payload.is_published
    if payload.corrige_published is not None:
        e.corrige_published = payload.corrige_published
    db.commit()
    db.refresh(e)
    return _serialize_exercise_admin(e)


@router.delete("/admin/exercises/{exercise_id}", dependencies=[Depends(verify_admin_code)])
def admin_delete_exercise(exercise_id: int, db: Session = Depends(get_db)):
    e = _exercise_or_404(db, exercise_id)
    # Fichiers disque : sujet, corrigé, et toutes les copies déposées.
    if e.subject_path:
        delete_prepa_file(e.subject_path)
    if e.corrige_path:
        delete_prepa_file(e.corrige_path)
    for s in list(e.submissions):
        delete_prepa_file(s.path)
    db.delete(e)  # cascade supprime les PrepaSubmission en base
    db.commit()
    return {"ok": True, "deleted_exercise_id": exercise_id}


@router.post("/admin/exercises/{exercise_id}/subject", dependencies=[Depends(verify_admin_code)])
async def admin_upload_subject(
    exercise_id: int,
    file: UploadFile = FastAPIFile(...),
    db: Session = Depends(get_db),
):
    e = _exercise_or_404(db, exercise_id)
    # Remplace l'ancien sujet sur disque si présent.
    if e.subject_path:
        delete_prepa_file(e.subject_path)
    meta = await save_prepa_pdf(
        file,
        PREPA_SUBJECTS_DIR / str(e.id),
        max_bytes=PREPA_COURSE_MAX_BYTES,
    )
    e.subject_filename_original = meta["filename_original"]
    e.subject_filename_stored = meta["filename_stored"]
    e.subject_path = meta["path"]
    e.subject_size_bytes = meta["size_bytes"]
    db.commit()
    db.refresh(e)
    return _serialize_exercise_admin(e)


@router.post("/admin/exercises/{exercise_id}/corrige", dependencies=[Depends(verify_admin_code)])
async def admin_upload_corrige(
    exercise_id: int,
    file: UploadFile = FastAPIFile(...),
    db: Session = Depends(get_db),
):
    e = _exercise_or_404(db, exercise_id)
    if e.corrige_path:
        delete_prepa_file(e.corrige_path)
    meta = await save_prepa_pdf(
        file,
        PREPA_CORRECTIONS_DIR / str(e.id),
        max_bytes=PREPA_COURSE_MAX_BYTES,
    )
    e.corrige_filename_original = meta["filename_original"]
    e.corrige_filename_stored = meta["filename_stored"]
    e.corrige_path = meta["path"]
    e.corrige_size_bytes = meta["size_bytes"]
    db.commit()
    db.refresh(e)
    return _serialize_exercise_admin(e)


@router.get("/admin/exercises/{exercise_id}/subject/download", dependencies=[Depends(verify_admin_code)])
def admin_download_subject(exercise_id: int, db: Session = Depends(get_db)):
    e = _exercise_or_404(db, exercise_id)
    if not e.subject_path:
        raise HTTPException(status_code=404, detail="Aucun sujet.")
    return _file_response(e.subject_path, e.subject_filename_original or "sujet.pdf")


@router.get("/admin/exercises/{exercise_id}/corrige/download", dependencies=[Depends(verify_admin_code)])
def admin_download_corrige(exercise_id: int, db: Session = Depends(get_db)):
    e = _exercise_or_404(db, exercise_id)
    if not e.corrige_path:
        raise HTTPException(status_code=404, detail="Aucun corrigé.")
    return _file_response(e.corrige_path, e.corrige_filename_original or "corrige.pdf")


# ════════════════════════════════════════════════════════════
# ADMIN — COPIES / NOTATION
# ════════════════════════════════════════════════════════════

@router.get("/admin/exercises/{exercise_id}/submissions", dependencies=[Depends(verify_admin_code)])
def admin_list_submissions(exercise_id: int, db: Session = Depends(get_db)):
    _exercise_or_404(db, exercise_id)
    rows = db.execute(
        select(models.PrepaSubmission, models.User)
        .join(models.User, models.PrepaSubmission.user_id == models.User.id)
        .where(models.PrepaSubmission.exercise_id == exercise_id)
        .order_by(desc(models.PrepaSubmission.submitted_at))
    ).all()
    items = []
    for s, u in rows:
        data = _serialize_submission(s)
        data["user"] = {"id": u.id, "username": u.username, "email": u.email}
        items.append(data)
    return {"items": items, "total": len(items)}


@router.get("/admin/submissions/{submission_id}/download", dependencies=[Depends(verify_admin_code)])
def admin_download_submission(submission_id: int, db: Session = Depends(get_db)):
    s = _submission_or_404(db, submission_id)
    return _file_response(s.path, s.filename_original)


@router.post("/admin/submissions/{submission_id}/grade", dependencies=[Depends(verify_admin_code)])
def admin_grade_submission(submission_id: int, payload: GradePayload, db: Session = Depends(get_db)):
    s = _submission_or_404(db, submission_id)
    if payload.note is not None and not (0 <= payload.note <= 20):
        raise HTTPException(status_code=400, detail="La note doit être comprise entre 0 et 20.")
    s.note = payload.note
    s.feedback = payload.feedback
    s.status = "corrected"
    s.corrected_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(s)
    return _serialize_submission(s)


# ════════════════════════════════════════════════════════════
# ADMIN — STOCKAGE
# ════════════════════════════════════════════════════════════

@router.get("/admin/storage", dependencies=[Depends(verify_admin_code)])
def admin_storage_status():
    return get_prepa_storage_status()


# ════════════════════════════════════════════════════════════
# ÉLÈVE — STATUT
# ════════════════════════════════════════════════════════════

@router.get("/me")
def my_prepa_status(current_user: models.User = Depends(get_current_user)):
    """Statut prepa de l'élève (n'exige pas un accès actif : sert aussi à l'UI
    pour savoir s'il faut proposer l'achat)."""
    return {
        "plan": current_user.plan,
        "prepa_annee": current_user.prepa_annee,
        "prepa_expires_at": (
            current_user.prepa_expires_at.isoformat()
            if current_user.prepa_expires_at else None
        ),
        "has_access": has_active_prepa_access(current_user),
    }


def _require_annee(current_user: models.User) -> str:
    """Renvoie l'année de l'élève, ou 403 si absente (cas anormal)."""
    if not current_user.prepa_annee:
        raise HTTPException(
            status_code=403,
            detail={"code": "PREPA_NO_ANNEE", "message": "Aucune année associée à votre accès."},
        )
    return current_user.prepa_annee


# ════════════════════════════════════════════════════════════
# ÉLÈVE — COURS
# ════════════════════════════════════════════════════════════

@router.get("/courses")
def my_courses(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    check_prepa_access(current_user)
    annee = _require_annee(current_user)
    rows = db.execute(
        select(models.PrepaCourse)
        .where(
            models.PrepaCourse.annee == annee,
            models.PrepaCourse.is_published.is_(True),
        )
        .order_by(models.PrepaCourse.ordre, models.PrepaCourse.id)
    ).scalars().all()
    return {"annee": annee, "items": [_serialize_course(c) for c in rows]}


@router.get("/courses/{course_id}")
def my_course_detail(
    course_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    check_prepa_access(current_user)
    annee = _require_annee(current_user)
    c = _course_or_404(db, course_id)
    if c.annee != annee or not c.is_published:
        # On ne révèle pas l'existence d'un cours hors périmètre.
        raise HTTPException(status_code=404, detail="Cours introuvable.")
    return _serialize_course(c, with_files=True)


@router.get("/courses/files/{file_id}/download")
def my_download_course_file(
    file_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    check_prepa_access(current_user)
    annee = _require_annee(current_user)
    f = _course_file_or_404(db, file_id)
    course = _course_or_404(db, f.course_id)
    if course.annee != annee or not course.is_published:
        raise HTTPException(status_code=404, detail="Fichier introuvable.")
    return _file_response(f.path, f.filename_original)


# ════════════════════════════════════════════════════════════
# ÉLÈVE — EXERCICES
# ════════════════════════════════════════════════════════════

def _my_submission(db: Session, user_id: int, exercise_id: int) -> models.PrepaSubmission | None:
    return db.execute(
        select(models.PrepaSubmission).where(
            models.PrepaSubmission.user_id == user_id,
            models.PrepaSubmission.exercise_id == exercise_id,
        )
    ).scalar_one_or_none()


@router.get("/exercises")
def my_exercises(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    check_prepa_access(current_user)
    annee = _require_annee(current_user)
    rows = db.execute(
        select(models.PrepaExercise)
        .where(
            models.PrepaExercise.annee == annee,
            models.PrepaExercise.is_published.is_(True),
        )
        .order_by(models.PrepaExercise.week_number, models.PrepaExercise.id)
    ).scalars().all()
    items = []
    for e in rows:
        sub = _my_submission(db, current_user.id, e.id)
        items.append(_serialize_exercise_student(e, sub))
    return {"annee": annee, "items": items}


def _student_exercise_or_404(
    db: Session, exercise_id: int, annee: str
) -> models.PrepaExercise:
    e = _exercise_or_404(db, exercise_id)
    if e.annee != annee or not e.is_published:
        raise HTTPException(status_code=404, detail="Exercice introuvable.")
    return e


@router.get("/exercises/{exercise_id}/subject/download")
def my_download_subject(
    exercise_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    check_prepa_access(current_user)
    annee = _require_annee(current_user)
    e = _student_exercise_or_404(db, exercise_id, annee)
    if not e.subject_path:
        raise HTTPException(status_code=404, detail="Aucun sujet disponible.")
    return _file_response(e.subject_path, e.subject_filename_original or "sujet.pdf")


@router.get("/exercises/{exercise_id}/corrige/download")
def my_download_corrige(
    exercise_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    check_prepa_access(current_user)
    annee = _require_annee(current_user)
    e = _student_exercise_or_404(db, exercise_id, annee)
    if not (e.corrige_published and e.corrige_path):
        raise HTTPException(status_code=404, detail="Le corrigé n'est pas encore disponible.")
    return _file_response(e.corrige_path, e.corrige_filename_original or "corrige.pdf")


@router.post("/exercises/{exercise_id}/submit")
async def my_submit(
    exercise_id: int,
    file: UploadFile = FastAPIFile(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Dépose (ou remplace) ma copie pour un exercice. Upsert applicatif : un seul
    dépôt par (élève, exercice). Un nouveau dépôt écrase l'ancien fichier disque
    et remet la copie à l'état "submitted" (note/feedback effacés).
    Le dépôt reste possible après la deadline (is_late géré côté affichage).
    """
    check_prepa_access(current_user)
    annee = _require_annee(current_user)
    e = _student_exercise_or_404(db, exercise_id, annee)

    meta = await save_prepa_pdf(file, PREPA_SUBMISSIONS_DIR / str(current_user.id))

    existing = _my_submission(db, current_user.id, e.id)
    if existing:
        # Remplace : supprime l'ancien fichier, réinitialise la correction.
        delete_prepa_file(existing.path)
        existing.filename_original = meta["filename_original"]
        existing.filename_stored = meta["filename_stored"]
        existing.path = meta["path"]
        existing.size_bytes = meta["size_bytes"]
        existing.submitted_at = datetime.now(timezone.utc)
        existing.status = "submitted"
        existing.note = None
        existing.feedback = ""
        existing.corrected_at = None
        db.commit()
        db.refresh(existing)
        return _serialize_submission(existing)

    row = models.PrepaSubmission(
        user_id=current_user.id,
        exercise_id=e.id,
        filename_original=meta["filename_original"],
        filename_stored=meta["filename_stored"],
        path=meta["path"],
        size_bytes=meta["size_bytes"],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _serialize_submission(row)


# ════════════════════════════════════════════════════════════
# ÉLÈVE — COPIES / NOTES
# ════════════════════════════════════════════════════════════

@router.get("/submissions")
def my_submissions(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    check_prepa_access(current_user)
    rows = db.execute(
        select(models.PrepaSubmission)
        .where(models.PrepaSubmission.user_id == current_user.id)
        .order_by(desc(models.PrepaSubmission.submitted_at))
    ).scalars().all()
    return {"items": [_serialize_submission(s) for s in rows]}


@router.get("/submissions/{submission_id}/download")
def my_download_submission(
    submission_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    check_prepa_access(current_user)
    s = _submission_or_404(db, submission_id)
    if s.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Accès interdit.")
    return _file_response(s.path, s.filename_original)


# ════════════════════════════════════════════════════════════
# ÉLÈVE — CALENDRIER
# ════════════════════════════════════════════════════════════

@router.get("/calendar")
def my_calendar(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Données pour les carrés "devoir" du calendrier : une entrée par exercice
    publié de mon année, avec sa deadline et l'état de ma copie.
    """
    check_prepa_access(current_user)
    annee = _require_annee(current_user)
    rows = db.execute(
        select(models.PrepaExercise)
        .where(
            models.PrepaExercise.annee == annee,
            models.PrepaExercise.is_published.is_(True),
        )
        .order_by(models.PrepaExercise.week_number, models.PrepaExercise.id)
    ).scalars().all()

    now = datetime.now(timezone.utc)
    items = []
    for e in rows:
        sub = _my_submission(db, current_user.id, e.id)
        if sub and sub.status == "corrected":
            state = "corrected"
        elif sub:
            state = "submitted"
        elif e.due_date and now > e.due_date:
            state = "missed"
        else:
            state = "pending"
        items.append({
            "exercise_id": e.id,
            "week_number": e.week_number,
            "titre": e.titre,
            "due_date": e.due_date.isoformat() if e.due_date else None,
            "state": state,  # pending | submitted | corrected | missed
            "note": sub.note if sub else None,
        })
    return {"annee": annee, "items": items}