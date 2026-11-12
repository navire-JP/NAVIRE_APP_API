"""
Avatars prédéfinis NAVIRE (illustrations d'avocats en robe).

Comme sur Discord : à la création du compte, l'utilisateur reçoit l'un de ces
avatars au hasard. Il est enregistré dans users.avatar_url et ne change plus
tant que l'utilisateur n'uploade pas sa propre photo (POST /users/me/avatar).

Les images vivent dans assets/ et sont servies publiquement par
GET /assets/<nom> (voir app/routers/assets.py).
"""
from __future__ import annotations

import random

from app.core.config import PUBLIC_API_URL

# Nom exposé par /assets → fichier dans assets/ (512 px carré, JPEG).
DEFAULT_AVATAR_FILES: dict[str, str] = {
    f"avatar-default-{i}.jpg": f"avatar-default-{i}.jpg" for i in range(1, 5)
}

DEFAULT_AVATAR_URLS: list[str] = [
    f"{PUBLIC_API_URL}/assets/{name}" for name in DEFAULT_AVATAR_FILES
]


def random_default_avatar_url() -> str:
    """Un avatar prédéfini tiré au hasard, à enregistrer sur le compte."""
    return random.choice(DEFAULT_AVATAR_URLS)


def is_default_avatar(url: str | None) -> bool:
    return bool(url) and any(url.endswith(f"/assets/{n}") for n in DEFAULT_AVATAR_FILES)
