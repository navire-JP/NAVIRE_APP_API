"""
Page classement : joueurs classés (général, 30 jours, 7 jours, par fac),
profil d'un joueur, stats masquées à la demande.

Lancer : pytest tests/test_classement.py
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

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
def client():
    import app.main as main
    from app.db.database import Base, engine
    from app.db.lib_migration import run_lib_migration

    engine.dispose()
    Base.metadata.create_all(bind=engine)
    run_lib_migration()  # base de test d'une version précédente
    yield TestClient(main.app)
    engine.dispose()


FAC = f"Fac {uuid.uuid4().hex[:6]}"


def _joueur(nom, elo, gains=(), fac=FAC):
    """Joueur avec son Elo et des gains datés : [(il y a n jours, delta)]."""
    from app.db.database import SessionLocal
    from app.db.models import EloEvent, User
    from app.core.security import create_access_token

    db = SessionLocal()
    u = User(email=f"{nom}-{uuid.uuid4().hex[:8]}@t.fr", username=f"{nom}{uuid.uuid4().hex[:4]}",
             elo=elo, university=fac)
    db.add(u)
    db.commit()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for jours, delta in gains:
        db.add(EloEvent(user_id=u.id, source="qcm", delta=delta, created_at=now - timedelta(days=jours, hours=1)))
    db.commit()
    out = (u.id, u.username, {"Authorization": f"Bearer {create_access_token(str(u.id))}"})
    db.close()
    return out


def test_classement(client):
    a = _joueur("alice", 900, [(1, 20), (10, 30)])
    b = _joueur("bruno", 900, [(2, 40)])
    c = _joueur("chloe", 300, [(20, 15)])
    d = _joueur("dora", 0)

    # Général, dans la fac : à l'Elo, ex aequo au même rang, Elo nul exclu
    r = client.get("/leaderboard/classement", params={"fac": FAC}, headers=c[2]).json()
    assert [i["id"] for i in r["items"]] == [a[0], b[0], c[0]]   # ex aequo : le plus ancien d'abord
    assert [i["rank"] for i in r["items"]] == [1, 1, 3] and r["total_joueurs"] == 3
    assert r["me"]["username"] == c[1] and r["me"]["rank"] == 3 and r["me"]["is_current_user"]
    alice = next(i for i in r["items"] if i["id"] == a[0])
    assert alice["gain_7j"] == 20 and alice["tier"] == "partner" and "email" not in alice

    # 7 jours : à l'Elo gagné
    r = client.get("/leaderboard/classement", params={"fac": FAC, "periode": "7j"}).json()
    assert [(i["id"], i["score"]) for i in r["items"]] == [(b[0], 40), (a[0], 20)]
    # 30 jours
    r = client.get("/leaderboard/classement", params={"fac": FAC, "periode": "30j"}).json()
    assert [(i["id"], i["score"]) for i in r["items"]] == [(a[0], 50), (b[0], 40), (c[0], 15)]

    # Joueur sans Elo : pas classé, mais sa carte « moi » existe
    r = client.get("/leaderboard/classement", params={"fac": FAC}, headers=d[2]).json()
    assert r["me"] is None and r["moi"]["stats_public"] is True


def test_profil_et_visibilite(client):
    a = _joueur("eva", 120, [(0, 5), (3, 10)])
    b = _joueur("felix", 50)

    p = client.get(f"/leaderboard/joueurs/{a[0]}", headers=b[2]).json()
    assert p["username"] == a[1] and p["stats_public"] and p["rank"] >= 1
    s = p["stats"]
    assert s["gain_7j"] == 15 and len(s["elo_30j"]) == 30
    assert s["elo_30j"][-1]["elo"] == 120 and s["elo_30j"][-5]["elo"] == 105   # avant les 2 gains
    assert s["elo_par_source"] == [{"source": "qcm", "elo": 15}]
    assert "success_rate_pct" in s and "current_streak" in s

    # Stats masquées : nom, Elo et rang restent, le détail disparaît
    assert client.patch("/leaderboard/me/visibilite", json={"stats_public": False}).status_code == 401
    assert client.patch("/leaderboard/me/visibilite", headers=a[2], json={"stats_public": False}).json() == {"stats_public": False}
    p = client.get(f"/leaderboard/joueurs/{a[0]}", headers=b[2]).json()
    assert p["stats"] is None and p["elo"] == 120 and p["stats_public"] is False
    assert client.get(f"/leaderboard/joueurs/{a[0]}", headers=a[2]).json()["stats"] is not None   # moi : toujours
    r = client.get("/leaderboard/classement", params={"fac": FAC}).json()
    assert next(i for i in r["items"] if i["id"] == a[0])["stats_public"] is False

    assert client.get("/leaderboard/joueurs/999999").status_code == 404


def test_embed(client):
    r = client.get("/leaderboard/embed")
    assert r.status_code == 200 and 'API: ""' in r.text
