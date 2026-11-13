"""
Posts des élèves, abonnements et posts enregistrés (app/routers/social.py),
contre une base SQLite. Cloudinary est remplacé par des faux.

Lancer : pytest tests/test_social.py
"""

import os
from types import SimpleNamespace

import pytest

for k, v in {
    "OPENAI_API_KEY": "x", "BREVO_API_KEY_MEOLES": "x",
    "STRIPE_SECRET_KEY_MEOLES": "x", "STRIPE_WEBHOOK_SECRET_MEOLES": "x",
    "STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_WEBHOOK_SECRET": "whsec_x",
    "DATABASE_URL": "sqlite:///./test_social.db",
}.items():
    os.environ.setdefault(k, v)

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="module")
def env():
    import app.main as main
    import app.routers.social as social
    from app.core.security import create_access_token
    from app.db.database import Base, engine, SessionLocal
    from app.db.models import User, UserFollow, UserPost, UserPostSave

    social.upload_post_image = lambda b, uid, key: f"https://res.cloudinary.com/x/image/{uid}_{key}.png"
    social.upload_post_doc = lambda b, uid, key, ext: f"https://res.cloudinary.com/x/raw/{uid}_{key}.{ext}"

    engine.dispose()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    for m in (UserPostSave, UserFollow, UserPost):
        db.query(m).delete()

    def user(email, name):
        u = db.query(User).filter(User.email == email).one_or_none()
        if not u:
            u = User(email=email, username=name)
            db.add(u)
        return u

    a, b = user("social-a@test.fr", "alice"), user("social-b@test.fr", "bob")
    db.commit()
    ns = SimpleNamespace(
        a=a.id, b=b.id,
        auth_a={"Authorization": f"Bearer {create_access_token(str(a.id))}"},
        auth_b={"Authorization": f"Bearer {create_access_token(str(b.id))}"},
    )
    db.close()
    ns.c = TestClient(main.app)
    yield ns
    engine.dispose()
    path = engine.url.database if engine.url.get_backend_name() == "sqlite" else None
    if path and os.path.exists(path):
        os.remove(path)


def test_post_texte_limite_a_100(env):
    r = env.c.post("/social/posts", headers=env.auth_a, data={"text": "x" * 101})
    assert r.status_code == 400
    r = env.c.post("/social/posts", headers=env.auth_a, data={"text": "  Mon   premier post  "})
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "Mon premier post"
    assert r.json()["can_delete"] is True


def test_post_vide_refuse(env):
    assert env.c.post("/social/posts", headers=env.auth_a, data={"text": "   "}).status_code == 400
    assert env.c.post("/social/posts", data={"text": "hello"}).status_code in (401, 403)


def test_post_avec_image_et_devoir(env):
    r = env.c.post(
        "/social/posts", headers=env.auth_a, data={"text": "Mon devoir de civil"},
        files={
            "image": ("photo.png", b"\x89PNG....", "image/png"),
            "document": ("Dissertation.pdf", b"%PDF-1.4 ...", "application/pdf"),
        },
    )
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["image_url"].endswith(".png")
    assert p["document"]["name"] == "Dissertation.pdf" and p["document"]["url"].endswith(".pdf")


def test_document_refuse_hors_pdf_word(env):
    r = env.c.post("/social/posts", headers=env.auth_a, data={"text": "x"},
                   files={"document": ("virus.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 400
    r = env.c.post("/social/posts", headers=env.auth_a, data={"text": "x"},
                   files={"image": ("a.gif", b"GIF", "image/gif")})
    assert r.status_code == 400


def test_suivre_et_profil(env):
    assert env.c.put(f"/social/users/{env.a}/follow", headers=env.auth_a).status_code == 400
    r = env.c.put(f"/social/users/{env.a}/follow", headers=env.auth_b)
    assert r.json() == {"ok": True, "is_following": True, "followers": 1}
    env.c.put(f"/social/users/{env.a}/follow", headers=env.auth_b)          # pas de doublon

    vu = env.c.get(f"/users/{env.a}/profile", headers=env.auth_b).json()["social"]
    assert vu["followers"] == 1 and vu["is_following"] is True
    assert vu["posts"]["total"] == 2
    assert vu["posts"]["items"][0]["can_delete"] is False

    mine = env.c.get("/users/me/profile-summary?ranking=false", headers=env.auth_b).json()["social"]
    assert mine["following"] == 1

    lst = env.c.get(f"/social/users/{env.a}/followers").json()
    assert [u["username"] for u in lst["items"]] == ["bob"]

    r = env.c.delete(f"/social/users/{env.a}/follow", headers=env.auth_b)
    assert r.json()["followers"] == 0


def test_enregistrer_un_post(env):
    pid = env.c.get(f"/social/users/{env.a}/posts").json()["items"][0]["id"]
    assert env.c.put(f"/social/posts/{pid}/save", headers=env.auth_b).json()["is_saved"] is True
    env.c.put(f"/social/posts/{pid}/save", headers=env.auth_b)
    saved = env.c.get("/social/me/saved", headers=env.auth_b).json()
    assert saved["total"] == 1 and saved["items"][0]["is_saved"] is True
    assert saved["items"][0]["saves_count"] == 1
    env.c.delete(f"/social/posts/{pid}/save", headers=env.auth_b)
    assert env.c.get("/social/me/saved", headers=env.auth_b).json()["total"] == 0


def test_supprimer_reserve_a_l_auteur(env):
    pid = env.c.get(f"/social/users/{env.a}/posts").json()["items"][0]["id"]
    env.c.put(f"/social/posts/{pid}/save", headers=env.auth_b)
    assert env.c.delete(f"/social/posts/{pid}", headers=env.auth_b).status_code == 403
    assert env.c.delete(f"/social/posts/{pid}", headers=env.auth_a).status_code == 200
    assert env.c.get("/social/me/saved", headers=env.auth_b).json()["total"] == 0
    assert env.c.get(f"/social/users/{env.a}/posts").json()["total"] == 1
