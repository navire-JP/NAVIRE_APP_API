"""Rend le paquet `app` importable quand pytest est lancé depuis la racine."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os

# Les tests n'appellent jamais OpenAI : pas d'indexation NAVIRE AI en tâche de
# fond (tests/test_navire_index.py la réactive en mode synchrone, simulée).
os.environ.setdefault("NAVIRE_INDEX_DISABLED", "1")
