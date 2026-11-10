"""
Crédits NAVIRE AI : choix du modèle, solde mensuel selon le plan, débit
après réponse, refus sans crédits, bonus admin. L'appel au modèle est simulé.

Lancer : pytest tests/test_navire_credits.py
"""

import os
from types import SimpleNamespace

import pytest

for k, v in {
    "OPENAI_API_KEY": "x", "BREVO_API_KEY_MEOLES": "x",
    "STRIPE_SECRET_KEY_MEOLES": "x", "STRIPE_WEBHOOK_SECRET_MEOLES": "x",
    "STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_WEBHOOK_SECRET": "whsec_x",
    "DATABASE_URL": "sqlite:///./test_navire_credits.db",
}.items():
    os.environ.setdefault(k, v)

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="module")
def env():
    import app.main as main
    from app.core.security import create_access_token
    from app.db.database import Base, engine, SessionLocal
    from app.db.models import NavireConversation, NavireCreditEvent, User

    engine.dispose()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.query(NavireCreditEvent).delete()
    db.query(NavireConversation).delete()

    def user(email, name, **kw):
        u = db.query(User).filter(User.email == email).one_or_none()
        if not u:
            u = User(email=email, username=name, **kw)
            db.add(u)
        return u

    free = user("cred-free@test.fr", "gratuit", plan="free")
    admin = user("cred-admin@test.fr", "admin", is_admin=True, plan="membre")
    db.commit()
    tok = lambda u: {"Authorization": f"Bearer {create_access_token(str(u.id))}"}  # noqa: E731
    ns = SimpleNamespace(free=free.id, auth=tok(free), admin=tok(admin))
    db.close()
    ns.c = TestClient(main.app)
    yield ns
    engine.dispose()
    path = engine.url.database
    if path and os.path.exists(path):
        os.remove(path)


@pytest.fixture
def fake_openai(monkeypatch):
    """Remplace l'appel OpenAI : renvoie une réponse et garde les paramètres."""
    import app.services.navire_chat as nc
    calls = []

    def create(**params):
        calls.append(params)
        msg = SimpleNamespace(content="Réponse juridique.", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    monkeypatch.setattr(nc._client.chat.completions, "create", create)
    return calls


def test_quota_expose_niveaux_et_credits(env):
    q = env.c.get("/navire/quota", headers=env.auth).json()
    assert [t["id"] for t in q["tiers"]] == ["rapide", "avance", "expert"]
    assert q["credits"]["allowance"] == 10 and q["credits"]["balance"] == 10
    assert "model" not in q["tiers"][0]


def test_avance_debite_un_credit_sans_toucher_au_quota(env, fake_openai):
    r = env.c.post("/navire/chat", headers=env.auth, json={"message": "Qu'est-ce que la cause ?", "tier": "avance"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["tier"] == "avance" and d["credits_spent"] == 1 and d["credits"]["balance"] == 9
    from app.core.navire_models import TIERS
    assert fake_openai[-1]["model"] == TIERS["avance"]["model"]
    q = env.c.get("/navire/quota", headers=env.auth).json()
    assert q["used"] == 0  # le quota quotidien du modèle Rapide est intact


def test_expert_modele_a_raisonnement(env, fake_openai, monkeypatch):
    from app.core import navire_models
    monkeypatch.setitem(navire_models.TIERS["expert"], "model", "gpt-5")
    monkeypatch.setattr(navire_models, "_WORKING", {})
    r = env.c.post("/navire/chat", headers=env.auth, json={"message": "Plan de dissertation", "tier": "expert"})
    assert r.status_code == 200
    p = fake_openai[-1]
    assert p["model"] == "gpt-5"
    assert "temperature" not in p and p["max_completion_tokens"] == navire_models.TIERS["expert"]["reasoning_tokens"]
    assert r.json()["credits"]["balance"] == 5


def test_pas_assez_de_credits(env, fake_openai):
    env.c.post("/navire/chat", headers=env.auth, json={"message": "Q", "tier": "expert"})  # 5 → 1
    r = env.c.post("/navire/chat", headers=env.auth, json={"message": "Q", "tier": "expert"})
    assert r.status_code == 402 and r.json()["detail"]["code"] == "NAVIRE_CREDITS_EXHAUSTED"
    # Le modèle Rapide reste disponible
    assert env.c.post("/navire/chat", headers=env.auth, json={"message": "Q"}).status_code == 200


def test_pas_de_debit_si_erreur(env, monkeypatch):
    import app.services.navire_chat as nc

    def boom(**params):
        raise RuntimeError("modèle indisponible")

    monkeypatch.setattr(nc._client.chat.completions, "create", boom)
    before = env.c.get("/navire/quota", headers=env.auth).json()["credits"]["balance"]
    r = env.c.post("/navire/chat", headers=env.auth, json={"message": "Q", "tier": "avance"})
    assert r.status_code == 502
    assert env.c.get("/navire/quota", headers=env.auth).json()["credits"]["balance"] == before


def test_bonus_admin(env):
    assert env.c.post(f"/admin/users/{env.free}/credits", headers=env.auth, json={"amount": 5}).status_code == 403
    r = env.c.post(f"/admin/users/{env.free}/credits", headers=env.admin, json={"amount": 20, "reason": "ambassadeur"})
    assert r.status_code == 200 and r.json()["bonus"] == 20
    d = env.c.get(f"/admin/users/{env.free}/credits", headers=env.admin).json()
    assert d["events"][0]["kind"] == "bonus" and d["balance"] == r.json()["balance"]


def test_modele_retire_repli_automatique(env, monkeypatch):
    """(Après le bonus admin : il reste des crédits.) o4-mini retiré par OpenAI : NAVIRE passe au modèle suivant du niveau."""
    import app.services.navire_chat as nc
    from app.core import navire_models

    monkeypatch.setitem(navire_models.TIERS["avance"], "model", "o4-mini")
    monkeypatch.setattr(navire_models, "_WORKING", {})
    monkeypatch.setattr(navire_models, "_DEAD", set())
    tried = []

    def create(**params):
        tried.append(params["model"])
        if params["model"] == "o4-mini":
            raise RuntimeError("Error code: 404 - The model o4-mini has been deprecated (model_not_found)")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Réponse.", tool_calls=None))])

    monkeypatch.setattr(nc._client.chat.completions, "create", create)
    r = env.c.post("/navire/chat", headers=env.auth, json={"message": "Préjudice extrapatrimonial ?", "tier": "avance"})
    assert r.status_code == 200, r.text
    assert tried[:2] == ["o4-mini", "gpt-5-mini"]
    assert r.json()["credits_spent"] == 1
    # La question suivante va directement au modèle qui marche
    env.c.post("/navire/chat", headers=env.auth, json={"message": "Encore", "tier": "avance"})
    assert tried[-1] == "gpt-5-mini" and tried.count("o4-mini") == 1


def test_modele_inconnu(env):
    assert env.c.post("/navire/chat", headers=env.auth, json={"message": "Q", "tier": "ultra"}).status_code == 400
