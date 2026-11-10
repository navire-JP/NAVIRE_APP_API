"""
Catégories de profil gérées depuis la console, badge Doctorant à valider et
parcours d'accueil, contre une base SQLite.

Lancer : pytest tests/test_categories.py
"""

import os
from types import SimpleNamespace

import pytest

for k, v in {
    "OPENAI_API_KEY": "x", "BREVO_API_KEY_MEOLES": "x",
    "STRIPE_SECRET_KEY_MEOLES": "x", "STRIPE_WEBHOOK_SECRET_MEOLES": "x",
    "STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_WEBHOOK_SECRET": "whsec_x",
    "DATABASE_URL": "sqlite:///./test_categories.db",
}.items():
    os.environ.setdefault(k, v)

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="module")
def env():
    import app.main as main
    from app.core.security import create_access_token
    from app.db.database import Base, engine, SessionLocal
    from app.db.models import MetaOption, StatusVerification, User

    engine.dispose()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.query(StatusVerification).delete()
    db.query(MetaOption).delete()

    def user(email, name, **kw):
        u = db.query(User).filter(User.email == email).one_or_none()
        if not u:
            u = User(email=email, username=name, **kw)
            db.add(u)
        u.study_level = "M2"
        return u

    eleve = user("cat-eleve@test.fr", "eleve")
    admin = user("cat-admin@test.fr", "admin", is_admin=True)
    db.commit()
    tok = lambda u: {"Authorization": f"Bearer {create_access_token(str(u.id))}"}  # noqa: E731
    ns = SimpleNamespace(eleve=eleve.id, auth=tok(eleve), admin=tok(admin))
    db.close()
    ns.c = TestClient(main.app)
    ns.db = SessionLocal
    yield ns
    engine.dispose()
    db_path = engine.url.database if engine.url.get_backend_name() == "sqlite" else None
    if db_path and os.path.exists(db_path):
        os.remove(db_path)


def test_options_prereglees_avec_doctorant(env):
    d = env.c.get("/meta/options").json()
    assert "Université de Bordeaux" in d["universities"]
    assert d["study_levels"][:6] == ["L1", "L2", "L3", "M1", "M2", "CRFPA"]
    doc = next(o for o in d["study_level_options"] if o["label"] == "Doctorant")
    assert doc["requires_verification"] is True and doc["badge"] == "Doctorant"


def test_console_reservee_aux_admins(env):
    assert env.c.get("/admin/options", headers=env.auth).status_code == 403


def test_ajout_renommage_desactivation(env):
    r = env.c.post("/admin/options", headers=env.admin, json={
        "kind": "university", "label": "  Université   de Test ",
    })
    assert r.status_code == 200, r.text
    opt = r.json()
    assert opt["label"] == "Université de Test"
    assert env.c.post("/admin/options", headers=env.admin, json={
        "kind": "university", "label": "Université de Test",
    }).status_code == 409

    env.c.patch("/users/me", headers=env.auth, json={"university": "Université de Test"})
    r = env.c.patch(f"/admin/options/{opt['id']}", headers=env.admin, json={"label": "Univ. Test"})
    assert r.status_code == 200
    me = env.c.get("/auth/me", headers=env.auth).json()
    assert me["university"] == "Univ. Test"  # le compte suit le renommage

    env.c.patch(f"/admin/options/{opt['id']}", headers=env.admin, json={"active": False})
    assert "Univ. Test" not in env.c.get("/meta/options").json()["universities"]
    items = env.c.get("/admin/options", headers=env.admin, params={"kind": "university"}).json()["items"]
    row = next(o for o in items if o["id"] == opt["id"])
    assert row["active"] is False and row["users_count"] == 1


def test_doctorant_attend_la_validation(env):
    r = env.c.patch("/users/me", headers=env.auth, json={"study_level": "Doctorant"})
    assert r.status_code == 200
    assert r.json()["study_level_status"] == "pending"
    assert r.json()["study_level"] == "M2"  # niveau inchangé tant que non validé

    me = env.c.get("/auth/me", headers=env.auth).json()
    assert me["is_doctorant"] is False and me["pending_verifications"] == ["Doctorant"]

    r = env.c.post("/users/me/verifications", headers=env.auth, data={
        "label": "Doctorant", "message": "ED 101, thèse en droit des contrats",
    })
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending"

    # Une seule demande ouverte, complétée par le message
    lst = env.c.get("/admin/verifications", headers=env.admin).json()
    assert lst["pending_count"] == 1
    v = lst["items"][0]
    assert v["message"].startswith("ED 101") and v["user"]["id"] == env.eleve

    r = env.c.post(f"/admin/verifications/{v['id']}/approve", headers=env.admin)
    assert r.status_code == 200 and r.json()["status"] == "approved"
    me = env.c.get("/auth/me", headers=env.auth).json()
    assert me["study_level"] == "Doctorant" and me["badges"] == ["Doctorant"]
    assert me["is_doctorant"] is True
    prof = env.c.get(f"/users/{env.eleve}/profile").json()
    assert prof["user"]["badges"] == ["Doctorant"]

    r = env.c.post(f"/admin/verifications/{v['id']}/revoke", headers=env.admin, json={"reason": "test"})
    assert r.status_code == 200
    me = env.c.get("/auth/me", headers=env.auth).json()
    assert me["badges"] == [] and me["study_level"] is None


def test_refus_puis_nouvelle_demande(env):
    r = env.c.post("/users/me/verifications", headers=env.auth, data={"label": "Doctorant"})
    assert r.status_code == 400  # ni message ni justificatif
    r = env.c.post("/users/me/verifications", headers=env.auth, data={
        "label": "Doctorant", "message": "Inscrit en 1re année",
    })
    vid = r.json()["id"]
    r = env.c.post(f"/admin/verifications/{vid}/reject", headers=env.admin, json={"reason": "Justificatif manquant"})
    assert r.json()["status"] == "rejected"
    mine = env.c.get("/users/me/verifications", headers=env.auth).json()
    assert mine["items"][0]["rejection_reason"] == "Justificatif manquant"
    r = env.c.post("/users/me/verifications", headers=env.auth, data={"label": "Doctorant", "message": "Attestation jointe"})
    assert r.json()["id"] != vid and r.json()["status"] == "pending"


def test_niveau_sans_verification_refuse_la_demande(env):
    r = env.c.post("/users/me/verifications", headers=env.auth, data={"label": "L1", "message": "x"})
    assert r.status_code == 400


def test_inscription_doctorant_cree_la_demande(env, monkeypatch):
    import app.routers.auth as auth
    monkeypatch.setattr(auth, "REQUIRE_EMAIL_VERIFICATION", False)
    r = env.c.post("/auth/register", json={
        "username": "nouveau", "email": "cat-nouveau@test.fr", "password": "Motdepasse1",
        "university": "Université de Bordeaux", "study_level": "Doctorant",
    })
    assert r.status_code == 200, r.text
    tok = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = env.c.get("/auth/me", headers=tok).json()
    assert me["study_level"] is None and me["pending_verifications"] == ["Doctorant"]
    assert me["onboarding_done"] is False

    assert env.c.post("/users/me/onboarding", headers=tok, json={}).json() == {"onboarding_done": True}
    assert env.c.get("/auth/me", headers=tok).json()["onboarding_done"] is True
