"""
app/core/prepa_storage.py
=========================
Couche de stockage disque pour le contenu PREPASSERELLE.

Calquée sur le mécanisme de routers/files.py : écriture en streaming sur le
disque persistant Render (STORAGE_PATH), nom de fichier uuid, contrôle de la
taille max. AUCUNE dépendance au modèle SQLAlchemy : ce module ne fait que
manipuler des octets sur disque et renvoyer des métadonnées. Il est donc
réutilisable tel quel par le router prepa, aussi bien pour :
  - les PDF de cours et de sujets/corrigés déposés par l'admin,
  - les dépôts d'exercices rendus par les élèves.

Le contenu vit sous STORAGE_PATH/Prepa/ , à côté de UserFiles/ , sur le même
disque persistant. Les sous-dossiers sont créés à la volée (comme files.py).
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile, HTTPException

from app.core.config import STORAGE_PATH, MAX_UPLOAD_BYTES


# ============================================================
# Arborescence disque
# ============================================================
# Racine du contenu PREPASSERELLE sur le disque persistant.
# En prod : /var/data/storage/Prepa/...
PREPA_FILES_DIR = Path(STORAGE_PATH) / "Prepa"

# Sous-dossiers logiques. On sépare les contenus admin des dépôts élèves
# pour garder le disque lisible et pouvoir purger une catégorie sans risque.
PREPA_COURSES_DIR     = PREPA_FILES_DIR / "courses"      # PDF de leçons (admin)
PREPA_SUBJECTS_DIR    = PREPA_FILES_DIR / "subjects"     # sujets d'exercices (admin)
PREPA_CORRECTIONS_DIR = PREPA_FILES_DIR / "corrections"  # corrigés-types (admin)
PREPA_SUBMISSIONS_DIR = PREPA_FILES_DIR / "submissions"  # dépôts des élèves


# Taille max spécifique aux PDF de cours (admin) : un poly de cours complet
# peut dépasser les 20 Mo des uploads utilisateurs classiques. 50 Mo par défaut.
# Surchargé par le router au moment de l'appel si besoin.
PREPA_COURSE_MAX_BYTES = 50 * 1024 * 1024


# ============================================================
# Budget disque PREPASSERELLE
# ============================================================
# Plafond TOTAL du contenu prepa sur le disque (cours + sujets + corrigés +
# dépôts élèves). Vérifié avant chaque écriture : un upload qui ferait dépasser
# ce budget est rejeté en 413.
#
# ⚠️ Le disque persistant Render est PARTAGÉ avec UserFiles/. Si le disque
# total fait 10 Go, ce budget devrait être plus bas (~7-8 Go) pour laisser de
# la marge aux fichiers utilisateurs. Surchargeable par variable d'env sans
# redéploiement de code : PREPA_STORAGE_BUDGET_BYTES.
PREPA_STORAGE_BUDGET_BYTES = int(
    os.getenv("PREPA_STORAGE_BUDGET_BYTES", str(10 * 1024 * 1024 * 1024))  # 10 Go
)


# ============================================================
# Utils
# ============================================================

def _is_pdf(upload: UploadFile) -> bool:
    """Même tolérance que files.py : extension .pdf ou content-type PDF/octet-stream."""
    name = (upload.filename or "").lower()
    ct = (upload.content_type or "").lower()
    return name.endswith(".pdf") or ct in (
        "application/pdf",
        "application/x-pdf",
        "application/octet-stream",
    )


# ============================================================
# Mesure d'usage disque
# ============================================================

def get_prepa_disk_usage() -> int:
    """
    Somme en octets de tout le contenu présent sous PREPA_FILES_DIR.
    Lit le disque réel (rglob), pas la base : reflète exactement ce qui est
    stocké, y compris d'éventuels orphelins. Coût négligeable au volume prepa.
    """
    if not PREPA_FILES_DIR.exists():
        return 0
    total = 0
    for p in PREPA_FILES_DIR.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def get_prepa_storage_status() -> dict:
    """
    État du budget disque prepa, prêt pour un affichage admin.
    Renvoie : {used_bytes, budget_bytes, remaining_bytes, percent}
    """
    used = get_prepa_disk_usage()
    budget = PREPA_STORAGE_BUDGET_BYTES
    remaining = max(0, budget - used)
    percent = round((used / budget) * 100, 1) if budget > 0 else 0.0
    return {
        "used_bytes": used,
        "budget_bytes": budget,
        "remaining_bytes": remaining,
        "percent": percent,
    }


# ============================================================
# Écriture
# ============================================================

async def save_prepa_pdf(
    upload: UploadFile,
    dest_dir: Path,
    *,
    max_bytes: int = MAX_UPLOAD_BYTES,
    enforce_budget: bool = True,
) -> dict:
    """
    Écrit un PDF sur le disque persistant et renvoie ses métadonnées.

    Le router passe `dest_dir` selon le contexte :
      - PREPA_COURSES_DIR / str(course_id)        pour une leçon de cours
      - PREPA_SUBJECTS_DIR / str(exercise_id)      pour un sujet
      - PREPA_CORRECTIONS_DIR / str(exercise_id)   pour un corrigé-type
      - PREPA_SUBMISSIONS_DIR / str(user_id)       pour un dépôt élève

    `max_bytes`       : plafond du fichier courant.
    `enforce_budget`  : si True, refuse l'écriture qui ferait dépasser le
                        budget disque global PREPA_STORAGE_BUDGET_BYTES (10 Go).

    Renvoie un dict prêt à mapper sur les colonnes du modèle :
        {
          "filename_original": str,   # nom d'origine (affichage)
          "filename_stored":   str,   # nom uuid sur disque
          "path":              str,   # chemin absolu complet
          "size_bytes":        int,
        }

    Lève :
      - HTTPException 400 si fichier manquant ou non-PDF
      - HTTPException 413 si dépassement de `max_bytes` (fichier partiel supprimé)
      - HTTPException 507 si dépassement du budget disque global (idem)
    """
    if not upload or not upload.filename:
        raise HTTPException(status_code=400, detail="Fichier manquant.")

    if not _is_pdf(upload):
        raise HTTPException(status_code=400, detail="Seuls les fichiers PDF sont acceptés.")

    dest_dir.mkdir(parents=True, exist_ok=True)

    stored_name = f"{uuid4().hex}.pdf"
    stored_path = dest_dir / stored_name

    # Usage disque prepa avant écriture (pour le contrôle de budget incrémental).
    budget_used = get_prepa_disk_usage() if enforce_budget else 0

    def _abort(out, code: int, detail: str):
        """Ferme proprement, supprime le fichier partiel, lève l'exception."""
        try:
            out.close()
        except Exception:
            pass
        stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=code, detail=detail)

    total = 0
    try:
        with open(stored_path, "wb") as out:
            while True:
                chunk = await upload.read(1024 * 1024)  # 1 Mo
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    _abort(out, 413, f"Fichier trop volumineux (max {max_bytes} bytes).")
                if enforce_budget and (budget_used + total) > PREPA_STORAGE_BUDGET_BYTES:
                    _abort(
                        out,
                        507,
                        "Espace de stockage PREPASSERELLE saturé "
                        f"(budget {PREPA_STORAGE_BUDGET_BYTES} bytes atteint).",
                    )
                out.write(chunk)
    finally:
        await upload.close()

    return {
        "filename_original": upload.filename,
        "filename_stored": stored_name,
        "path": str(stored_path),
        "size_bytes": total,
    }


# ============================================================
# Suppression
# ============================================================

def delete_prepa_file(path: str) -> bool:
    """
    Supprime un fichier prepa du disque. Idempotent : ne lève jamais si le
    fichier est déjà absent (même logique défensive que files.py).
    Renvoie True si un fichier a réellement été supprimé.
    """
    try:
        p = Path(path)
        if p.exists():
            p.unlink()
            return True
    except Exception:
        pass
    return False