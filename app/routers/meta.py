from fastapi import APIRouter
from pathlib import Path
import json

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


def ensure_options_file():
    # crée le dossier app/data si inexistant
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # crée le fichier options.json s'il n'existe pas
    if not OPTIONS_PATH.exists():
        with open(OPTIONS_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_OPTIONS, f, indent=2, ensure_ascii=False)


@router.get("/options")
def get_options():
    ensure_options_file()

    with open(OPTIONS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)
