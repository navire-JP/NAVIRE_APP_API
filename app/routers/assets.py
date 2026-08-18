from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse


router = APIRouter(prefix="/assets", tags=["assets"])

# Dossier assets/ à la racine du dépôt (app/routers/assets.py → ../../assets).
ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"

# Liste blanche d'un seul fichier : le logo NAVIRE des emails, et rien d'autre.
# Le dossier assets/ contient aussi des documents (assets/DOCS), des photos de
# profil et les visuels MEOLES — rien de tout cela ne doit être joignable par
# cette route. Le nom exposé est stable : les templates d'email pointent
# dessus, ils ne doivent pas casser si le fichier source est renommé.
BRAND_FILES: dict[str, str] = {
    "logo-navire.png": "LOGO SEUL (3).png",   # logo NAVIRE, blanc sur transparent
}

# Une semaine : ces fichiers ne changent qu'à un changement d'identité visuelle,
# et les clients mail rechargent l'image à chaque ouverture sinon.
CACHE_CONTROL = "public, max-age=604800, immutable"


@router.get("/{name}")
def get_brand_asset(name: str):
    """
    Sert une image de marque NAVIRE sous un nom stable et sans authentification.

    Sert notamment le logo affiché en en-tête de tous les emails : un client
    mail télécharge l'image sans jeton, elle doit donc rester publique.
    """
    source = BRAND_FILES.get(name)
    if not source:
        raise HTTPException(status_code=404, detail="Asset introuvable.")

    path = ASSETS_DIR / source
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Fichier absent du serveur.")

    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": CACHE_CONTROL},
    )
