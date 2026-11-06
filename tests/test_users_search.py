"""Recherche d'élèves (page profil) : pseudo ou fac, mieux classés d'abord."""

import uuid

from tests.test_lib_prepa import _headers, _user, env  # noqa: F401


def test_recherche_eleves(env):
    from fastapi.testclient import TestClient
    from app.db.database import SessionLocal

    db = SessionLocal()
    tag = uuid.uuid4().hex[:6]
    a = _user(db, f"camille{tag}")
    b = _user(db, f"camilla{tag}")
    from app.db.models import User
    for u, elo, fac in ((a, 900, "Paris 2"), (b, 1300, "Lyon 3")):
        x = db.get(User, u.id); x.elo = elo; x.university = fac
    db.commit(); db.close()

    c = TestClient(env.app)
    assert c.get(f"/users/search?q=camil{tag[:2]}").status_code == 401
    r = c.get(f"/users/search?q=camil", headers=_headers(a)).json()["items"]
    mine = [x for x in r if x["username"].endswith(tag)]
    assert [x["username"] for x in mine] == [f"camilla{tag}", f"camille{tag}"]       # mieux classé d'abord
    assert next(x for x in mine if x["id"] == a.id)["is_me"] is True
    assert c.get("/users/search?q=c", headers=_headers(a)).json() == {"items": []}   # 2 caractères au moins
    assert any(x["id"] == b.id for x in c.get("/users/search?q=Lyon 3", headers=_headers(a)).json()["items"])
