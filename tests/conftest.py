"""Rend le paquet `app` importable quand pytest est lancé depuis la racine."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
