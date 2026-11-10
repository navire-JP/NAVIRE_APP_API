from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse


router = APIRouter(prefix="/assets", tags=["assets"])

# Dossier assets/ à la racine du dépôt (app/routers/assets.py → ../../assets).
ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"

# Liste blanche des images de marque NAVIRE, et rien d'autre.
# Le dossier assets/ contient aussi des documents (assets/DOCS), des photos de
# profil et les visuels MEOLES — rien de tout cela ne doit être joignable par
# cette route. Le nom exposé est stable : les templates d'email pointent
# dessus, ils ne doivent pas casser si le fichier source est renommé.
BRAND_FILES: dict[str, str] = {
    "logo-navire.png": "LOGO SEUL (3).png",   # logo NAVIRE, blanc sur transparent
    "logo-navire-app.png": "logoNAVIRE.png",   # icône d'app (carré arrondi blanc), interface NAVIRE AI
    # Même icône en noir (128 px), pour les boutons blancs (sélecteur de rubrique de la Veille sur téléphone).
    "logo-navire-app-noir.png": "logoNAVIRE-noir.png",
    # Visuels des trois accès de l'accueil (embeds/home.html), recadrés en
    # 256 px depuis « NAVIRE_<Cours|Fichiers|Veille>_embed asset.png ».
    "home-veilles.png": "home-veilles.png",
    "home-cours.png": "home-cours.png",
    "home-fichiers.png": "home-fichiers.png",
    # Visuels des éléments de l'onglet Fichiers (embeds/fichiers.html) :
    # PDF et copies (cristal), actus enregistrées (diamant), recadrés en 256 px
    # depuis « NAVIRE_<Cristal|Diamant>_embed asset.png ». Dossiers et cours
    # Lib reprennent home-fichiers / home-cours.
    "files-cristal.png": "files-cristal.png",
    "files-diamant.png": "files-diamant.png",
    # Visuels des statistiques du profil (embeds/profil.html), recadrés en
    # 256 px : rang (trophée), réussite QCM (cible), deep work (orbe).
    # Documents reprend home-fichiers.
    "profil-rang.png": "profil-rang.png",
    "profil-qcm.png": "profil-qcm.png",
    "profil-deepwork.png": "profil-deepwork.png",
    # Icône des articles (format publié par les doctorants et l'équipe), 256 px
    # sur fond transparent, depuis « NAVIRE_Articles_icon (source).png ».
    "NAVIRE_Articles_icon.png": "NAVIRE_Articles_icon.png",
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
