"""
Limites par plan : un admin n'en a aucune, quel que soit son plan.

Lancer : pytest tests/test_limits.py
"""

import os

for k, v in {
    "OPENAI_API_KEY": "x", "BREVO_API_KEY_MEOLES": "x",
    "STRIPE_SECRET_KEY_MEOLES": "x", "STRIPE_WEBHOOK_SECRET_MEOLES": "x",
    "STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_WEBHOOK_SECRET": "whsec_x",
    "DATABASE_URL": "sqlite:///./test_prepa_adjuris.db",
}.items():
    os.environ.setdefault(k, v)

from app.core.limits import (  # noqa: E402
    check_file_limit, get_file_ttl, get_user_limit, get_user_limits,
)
from app.db.models import User  # noqa: E402


def test_admin_free_est_illimite():
    admin = User(plan="free", is_admin=True)
    assert all(v is None for v in get_user_limits(admin).values())
    assert get_file_ttl(admin) is None
    # Aucune requête : la base n'est pas consultée quand la limite est None
    check_file_limit(admin, db=None)


def test_non_admin_garde_les_limites_du_plan():
    user = User(plan="free", is_admin=False)
    assert get_user_limit(user, "files_total") == 1
    assert get_file_ttl(user) == 24
    assert get_user_limit(User(plan="membre+", is_admin=False), "files_total") == 24
