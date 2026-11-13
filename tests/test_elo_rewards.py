"""
Elo gagné par l'activité (app/services/elo_rewards.py) contre une base SQLite.

Lancer : pytest tests/test_elo_rewards.py
"""

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

for k, v in {
    "OPENAI_API_KEY": "x", "BREVO_API_KEY_MEOLES": "x",
    "STRIPE_SECRET_KEY_MEOLES": "x", "STRIPE_WEBHOOK_SECRET_MEOLES": "x",
    "STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_WEBHOOK_SECRET": "whsec_x",
    "DATABASE_URL": "sqlite:///./test_elo_rewards.db",
}.items():
    os.environ.setdefault(k, v)

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="module")
def env():
    import app.main as main
    import app.routers.social as social
    from app.core.security import create_access_token
    from app.db.database import Base, engine, SessionLocal
    from app.db.models import EloEvent, User, UserPost, VeilleEvent, VeilleItem

    social.upload_post_image = lambda b, uid, key: f"https://res.cloudinary.com/x/image/{uid}_{key}.png"
    social.upload_post_doc = lambda b, uid, key, ext: f"https://res.cloudinary.com/x/raw/{uid}_{key}.{ext}"

    engine.dispose()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    for m in (EloEvent, UserPost, VeilleEvent):
        db.query(m).delete()
    db.query(VeilleItem).filter(VeilleItem.external_id.like("test:elo:%")).delete()
    users = {}
    for name in ("poster", "lecteur", "service"):
        u = db.query(User).filter(User.email == f"elo-{name}@test.fr").one_or_none()
        if not u:
            u = User(email=f"elo-{name}@test.fr", username=name)
            db.add(u)
        u.elo = 0
        users[name] = u
    now = datetime.now(timezone.utc)
    items = [
        VeilleItem(external_id=f"test:elo:{i}", title=f"Actu {i}", essentiel="E.", impact="I.",
                   category="civil", source_url=f"https://exemple.fr/elo/{i}", source_name="X",
                   published_at=now, created_at=now)
        for i in range(6)
    ]
    db.add_all(items)
    db.commit()
    ns = SimpleNamespace(
        ids={k: u.id for k, u in users.items()},
        items=[it.id for it in items],
        auth={k: {"Authorization": f"Bearer {create_access_token(str(u.id))}"} for k, u in users.items()},
        Session=SessionLocal,
    )
    db.close()
    ns.c = TestClient(main.app)
    yield ns
    engine.dispose()
    path = engine.url.database if engine.url.get_backend_name() == "sqlite" else None
    if path and os.path.exists(path):
        os.remove(path)


def _elo(env, name):
    from app.db.models import User
    db = env.Session()
    try:
        return int(db.get(User, env.ids[name]).elo)
    finally:
        db.close()


def test_bareme_connexion():
    from app.services.elo_rewards import login_streak_reward
    assert [login_streak_reward(n) for n in range(1, 9)] == [0, 5, 5, 3, 4, 4, 10, 0]
    assert login_streak_reward(9) == 5


def test_serie_de_connexions(env):
    from app.db.models import User
    from app.services import elo_rewards as er

    db = env.Session()
    u = db.get(User, env.ids["service"])
    base = datetime(2026, 3, 2, 10, tzinfo=timezone.utc)
    gains = []
    for d in range(8):
        er._presence_seen.pop(u.id, None)
        gains.append(er.register_daily_presence(db, u, now=base + timedelta(days=d)))
    assert gains == [0, 5, 5, 3, 4, 4, 10, 0]
    # Deuxième passage le même jour : rien
    er._presence_seen.pop(u.id, None)
    assert er.register_daily_presence(db, u, now=base + timedelta(days=7, hours=5)) == 0
    # Un jour sauté : la série repart à 1
    er._presence_seen.pop(u.id, None)
    assert er.register_daily_presence(db, u, now=base + timedelta(days=9)) == 0
    er._presence_seen.pop(u.id, None)
    assert er.register_daily_presence(db, u, now=base + timedelta(days=10)) == 5
    assert er.login_streak(db, u.id, now=base + timedelta(days=10)) == 2
    db.close()


def test_profil_premiere_fois(env):
    from app.db.models import User
    from app.services import elo_rewards as er

    db = env.Session()
    u = db.get(User, env.ids["service"])
    assert er.reward_avatar(db, u) == 10
    assert er.reward_avatar(db, u) == 0
    assert er.reward_banner(db, u) == 10
    assert er.reward_banner(db, u) == 0
    db.close()


def test_devoir_prepa_et_retrait(env):
    from app.db.models import User
    from app.services import elo_rewards as er

    db = env.Session()
    u = db.get(User, env.ids["service"])
    avant = int(u.elo)
    assert er.reward_prepa_devoir(db, u, "devoir:999") == 25
    assert er.reward_prepa_devoir(db, u, "devoir:999") == 0
    assert er.revoke_prepa_devoir(db, u.id, "devoir:999") == 25
    assert er.revoke_prepa_devoir(db, u.id, "devoir:999") == 0
    db.refresh(u)
    assert int(u.elo) == avant
    db.close()


def test_posts(env):
    c, auth = env.c, env.auth["poster"]
    base = _elo(env, "poster")   # la connexion du jour a pu compter

    r = c.post("/social/posts", data={"text": "Premier post"}, headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["elo_gagne"] == 15            # 10 (premier post) + 5
    first = r.json()["id"]

    r = c.post("/social/posts", data={"text": "Mon devoir"}, headers=auth,
               files={"document": ("devoir.pdf", b"%PDF-1.4 x", "application/pdf")})
    assert r.status_code == 200, r.text
    assert r.json()["elo_gagne"] == 7

    r = c.post("/social/posts", data={"text": "Encore"}, headers=auth)
    assert r.json()["elo_gagne"] == 5
    assert _elo(env, "poster") == base + 27

    # Supprimer un post reprend ses points, pas le bonus du premier post
    assert c.delete(f"/social/posts/{first}", headers=auth).status_code == 200
    assert _elo(env, "poster") == base + 22


def test_lecture_actu(env):
    from app.db.models import VeilleEvent

    c, auth = env.c, env.auth["lecteur"]
    item = env.items[0]

    # Sans ouverture préalable : refusé
    assert c.post(f"/veille/actus/{item}/lecture", headers=auth).status_code == 400

    # Ouverte à l'instant : trop court
    assert c.get(f"/veille/actus/{item}", headers=auth).status_code == 200
    assert c.post(f"/veille/actus/{item}/lecture", headers=auth).status_code == 400

    def ouvrir_il_y_a_40s(item_id):
        db = env.Session()
        db.add(VeilleEvent(user_id=env.ids["lecteur"], item_id=item_id, event_type="view",
                           created_at=datetime.now(timezone.utc) - timedelta(seconds=40)))
        db.commit()
        db.close()

    base = _elo(env, "lecteur")
    gains = []
    for it in env.items[:6]:
        ouvrir_il_y_a_40s(it)
        r = c.post(f"/veille/actus/{it}/lecture", headers=auth)
        assert r.status_code == 200, r.text
        gains.append(r.json()["elo_gagne"])
    assert gains == [2, 2, 2, 2, 12, 2]           # +10 à la 5e actu du jour

    # Relire la même actu : rien
    ouvrir_il_y_a_40s(item)
    assert c.post(f"/veille/actus/{item}/lecture", headers=auth).json()["elo_gagne"] == 0
    assert _elo(env, "lecteur") == base + 22
