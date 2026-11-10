"""
Mot de passe oublié : code à 6 chiffres par email, nouveau mot de passe,
connexion dans la foulée. Contre une base SQLite, l'envoi d'email simulé.

Lancer : pytest tests/test_password_reset.py
"""

import os

import pytest

for k, v in {
    "OPENAI_API_KEY": "x", "BREVO_API_KEY_MEOLES": "x",
    "STRIPE_SECRET_KEY_MEOLES": "x", "STRIPE_WEBHOOK_SECRET_MEOLES": "x",
    "STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_WEBHOOK_SECRET": "whsec_x",
    "DATABASE_URL": "sqlite:///./test_password_reset.db",
}.items():
    os.environ.setdefault(k, v)

from fastapi.testclient import TestClient  # noqa: E402

EMAIL = "reset-eleve@test.fr"


@pytest.fixture(scope="module")
def c():
    import app.main as main
    from app.core.security import hash_password
    from app.db.database import Base, engine, SessionLocal
    from app.db.models import EmailVerification, User

    engine.dispose()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.query(EmailVerification).delete()
    u = db.query(User).filter(User.email == EMAIL).one_or_none()
    if not u:
        u = User(email=EMAIL, username="eleve")
        db.add(u)
    u.password_hash = hash_password("AncienMdp1")
    db.commit()
    db.close()
    yield TestClient(main.app)
    engine.dispose()
    path = engine.url.database
    if path and os.path.exists(path):
        os.remove(path)


@pytest.fixture
def sent(monkeypatch):
    """Intercepte l'email et le code envoyés."""
    import app.services.email_verification as ev
    box = []
    monkeypatch.setattr(ev, "generate_code", lambda: "123456")
    monkeypatch.setattr(ev, "send_mail_result", lambda to, subject, html: box.append((to, subject)) or {"sent": True, "detail": "", "message_id": "x"})
    return box


def test_adresse_inconnue_meme_reponse(c, sent):
    r = c.post("/auth/password/forgot", json={"email": "personne@test.fr"})
    assert r.status_code == 200 and r.json()["sent"] is True
    assert sent == []  # rien n'est parti


def test_parcours_complet(c, sent):
    r = c.post("/auth/password/forgot", json={"email": EMAIL.upper()})
    assert r.status_code == 200
    assert sent and sent[0][0] == EMAIL and "mot de passe" in sent[0][1]

    # Mot de passe trop faible : refusé sans consommer d'essai
    assert c.post("/auth/password/reset", json={"email": EMAIL, "code": "123456", "new_password": "faible12"}).status_code == 400
    # Mauvais code
    r = c.post("/auth/password/reset", json={"email": EMAIL, "code": "000000", "new_password": "NouveauMdp1"})
    assert r.status_code == 400 and "incorrect" in r.json()["detail"]
    # Bon code
    r = c.post("/auth/password/reset", json={"email": EMAIL, "code": "123456", "new_password": "NouveauMdp1"})
    assert r.status_code == 200, r.text
    assert r.json()["access_token"]

    # L'ancien mot de passe ne marche plus, le nouveau oui
    assert c.post("/auth/login", json={"email": EMAIL, "password": "AncienMdp1"}).status_code == 401
    assert c.post("/auth/login", json={"email": EMAIL, "password": "NouveauMdp1"}).status_code == 200
    # Le code ne sert qu'une fois
    assert c.post("/auth/password/reset", json={"email": EMAIL, "code": "123456", "new_password": "AutreMdp12"}).status_code == 400


def test_jeton_inscription_ne_change_pas_le_mdp():
    from app.services.email_verification import RESET_TOKEN_SCOPE, email_token_is_valid, issue_email_token
    tok = issue_email_token(EMAIL)  # portée « inscription »
    assert email_token_is_valid(tok, EMAIL)
    assert not email_token_is_valid(tok, EMAIL, scope=RESET_TOKEN_SCOPE)
