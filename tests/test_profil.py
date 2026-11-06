"""
Page profil façon X : champs de profil (bio, LinkedIn, mode privé), profil vu
par un autre élève et posts de veille, contre une base SQLite.

Lancer : pytest tests/test_profil.py
"""

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

for k, v in {
    "OPENAI_API_KEY": "x", "BREVO_API_KEY_MEOLES": "x",
    "STRIPE_SECRET_KEY_MEOLES": "x", "STRIPE_WEBHOOK_SECRET_MEOLES": "x",
    "STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_WEBHOOK_SECRET": "whsec_x",
    "DATABASE_URL": "sqlite:///./test_prepa_adjuris.db",
}.items():
    os.environ.setdefault(k, v)

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="module")
def env():
    import app.main as main
    from app.core.security import create_access_token
    from app.db.database import Base, engine, SessionLocal
    from app.db.models import User, VeilleItem

    engine.dispose()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.query(VeilleItem).filter(VeilleItem.external_id.like("test:profil:%")).delete(synchronize_session=False)

    def user(email, name, **kw):
        u = db.query(User).filter(User.email == email).one_or_none()
        if not u:
            u = User(email=email, username=name, **kw)
            db.add(u)
        return u

    moi = user("profil-moi@test.fr", "moi", elo=1200)
    autre = user("profil-autre@test.fr", "autre", elo=900)
    db.flush()
    moi.stats_public, autre.stats_public = True, True

    now = datetime.now(timezone.utc)
    for n in range(12):
        when = now - timedelta(days=n)
        db.add(VeilleItem(
            external_id=f"test:profil:{n}", title=f"Actu {n}", essentiel="Résumé.",
            impact="Impact.", category="civil", source_url=f"https://exemple.fr/p{n}",
            source_name="Dalloz", published_at=when, created_at=when,
            author_id=moi.id,
        ))
    db.commit()
    ns = SimpleNamespace(
        moi=moi.id, autre=autre.id,
        auth_moi={"Authorization": f"Bearer {create_access_token(str(moi.id))}"},
        auth_autre={"Authorization": f"Bearer {create_access_token(str(autre.id))}"},
    )
    db.close()
    ns.c = TestClient(main.app)
    yield ns
    engine.dispose()
    db_path = engine.url.database if engine.url.get_backend_name() == "sqlite" else None
    if db_path and os.path.exists(db_path):
        os.remove(db_path)


def test_bio_linkedin_et_prive(env):
    r = env.c.patch("/users/me", headers=env.auth_moi, json={
        "bio": "  L2   droit,  passionné de pénal ",
        "linkedin_url": "fr.linkedin.com/in/jean-dupont-42/",
        "stats_public": False,
    })
    assert r.status_code == 200, r.text
    u = r.json()
    assert u["bio"] == "L2 droit, passionné de pénal"
    assert u["linkedin_url"] == "https://www.linkedin.com/in/jean-dupont-42"
    assert u["stats_public"] is False
    assert u["created_at"]


@pytest.mark.parametrize("bad", [
    "https://evil.com/in/jean", "https://linkedin.com.evil.com/in/jean",
    "https://www.linkedin.com/company/navire", "javascript:alert(1)",
])
def test_linkedin_refuse_les_liens_arbitraires(env, bad):
    r = env.c.patch("/users/me", headers=env.auth_moi, json={"linkedin_url": bad})
    assert r.status_code == 400


def test_delier_linkedin(env):
    r = env.c.patch("/users/me", headers=env.auth_moi, json={"linkedin_url": ""})
    assert r.status_code == 200 and r.json()["linkedin_url"] is None


def test_profile_summary_sans_classement_avec_posts(env):
    r = env.c.get("/users/me/profile-summary", headers=env.auth_moi, params={"ranking": "false"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ranking_above"] == [] and d["ranking_below"] == []
    assert d["posts"]["total"] == 12 and d["posts"]["has_more"] is True
    assert [p["title"] for p in d["posts"]["items"][:2]] == ["Actu 0", "Actu 1"]
    assert "banner_url" in d["user"] and "stats_public" in d["user"]


def test_posts_pagines(env):
    r = env.c.get(f"/users/{env.moi}/posts", params={"offset": 10})
    d = r.json()
    assert len(d["items"]) == 2 and d["has_more"] is False
    assert env.c.get("/users/999999/posts").status_code == 404


def test_profil_prive_masque_les_stats_aux_autres(env):
    env.c.patch("/users/me", headers=env.auth_moi, json={"stats_public": False})

    vu = env.c.get(f"/users/{env.moi}/profile", headers=env.auth_autre).json()
    assert vu["is_me"] is False
    assert vu["stats"] is None
    assert vu["elo"]["value"] == 1200          # l'Elo reste visible
    assert vu["posts"]["total"] == 12           # les posts aussi
    assert "email" not in vu["user"]

    soi = env.c.get(f"/users/{env.moi}/profile", headers=env.auth_moi).json()
    assert soi["is_me"] is True and soi["stats"] is not None


def test_profil_public_visible_sans_connexion(env):
    env.c.patch("/users/me", headers=env.auth_moi, json={"stats_public": True})
    vu = env.c.get(f"/users/{env.moi}/profile").json()
    assert vu["stats"]["rank"] >= 1
    assert "deepwork" in vu["stats"]
