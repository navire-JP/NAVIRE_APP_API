from fastapi import APIRouter, Depends
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.database import get_db

router = APIRouter(prefix="/meta", tags=["meta"])

DATA_DIR = Path("app/data")
OPTIONS_PATH = DATA_DIR / "options.json"

DEFAULT_OPTIONS = {
    "universities": [
        "Aix-Marseille Université",
        "Avignon Université",
        "CY Cergy Paris Université",
        "Institut Catholique de Paris (ICP)",
        "Institut Catholique de Rennes (ICR)",
        "Institut Catholique de Toulouse (ICT)",
        "Institut Catholique de Vendée (ICES)",
        "Le Mans Université",
        "Nantes Université",
        "Université Catholique de Lille",
        "Université Catholique de Lyon (UCLy)",
        "Université Clermont Auvergne",
        "Université Côte d'Azur (Nice)",
        "Université d'Angers",
        "Université d'Artois",
        "Université d'Évry Val d'Essonne",
        "Université d'Orléans",
        "Université de Bordeaux",
        "Université de Bourgogne (Dijon)",
        "Université de Bretagne Occidentale (Brest)",
        "Université de Bretagne Sud (Vannes/Lorient)",
        "Université de Caen Normandie",
        "Université de Corse Pasquale Paoli",
        "Université de Franche-Comté (Besançon)",
        "Université de la Guyane",
        "Université de la Nouvelle-Calédonie",
        "Université de la Polynésie Française",
        "Université de La Réunion",
        "Université de La Rochelle",
        "Université de Lille",
        "Université de Limoges",
        "Université de Lorraine (Nancy/Metz)",
        "Université de Montpellier",
        "Université de Nîmes",
        "Université de Pau et des Pays de l'Adour",
        "Université de Perpignan Via Domitia",
        "Université de Picardie Jules Verne (Amiens)",
        "Université de Poitiers",
        "Université de Reims Champagne-Ardenne",
        "Université de Rennes",
        "Université de Rouen Normandie",
        "Université de Strasbourg",
        "Université de Toulon",
        "Université de Tours",
        "Université de Versailles Saint-Quentin-en-Yvelines",
        "Université des Antilles",
        "Université du Havre Normandie",
        "Université Grenoble Alpes",
        "Université Jean Monnet (Saint-Étienne)",
        "Université Jean Moulin Lyon 3",
        "Université Lumière Lyon 2",
        "Université Paris 1 Panthéon-Sorbonne",
        "Université Paris 13 (Sorbonne Paris Nord)",
        "Université Paris 8 Vincennes-Saint-Denis",
        "Université Paris Cité",
        "Université Paris Nanterre",
        "Université Paris-Est Créteil (UPEC)",
        "Université Paris-Panthéon-Assas",
        "Université Paris-Saclay",
        "Université Polytechnique Hauts-de-France",
        "Université Savoie Mont Blanc (Chambéry)",
        "Université Toulouse 1 Capitole"
    ],
    "study_levels": [
        "L1",
        "L2",
        "L3",
        "M1",
        "M2",
        "CRFPA",
    ],
}


# Préréglages historiques : ils ne servent plus qu'à remplir la table
# meta_options au premier démarrage (app/services/user_categories.py). Les
# listes se gèrent ensuite depuis la console (/admin/options).


@router.get("/options")
def get_options(db: Session = Depends(get_db)):
    """
    Listes de l'inscription et du profil.

    `universities` et `study_levels` restent des listes de libellés pour les
    embeds existants ; `study_level_options` précise quels niveaux demandent
    une vérification (ex. Doctorant) et ce qu'il faut fournir.
    """
    from app.services.user_categories import public_options

    return public_options(db)
