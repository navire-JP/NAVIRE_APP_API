"""
Hub « Actualités » (/veille/rubriques, /veille/actus…) contre une base SQLite.

Lancer : pytest tests/test_veille_hub.py
(variables d'environnement factices posées ci-dessous, aucune clé réelle.)
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

    # Un autre module de tests a pu supprimer le fichier SQLite pendant qu'une
    # connexion du pool restait ouverte dessus : on repart de connexions neuves.
    engine.dispose()
    Base.metadata.create_all(bind=engine)
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    db.query(VeilleItem).delete()
    user = db.query(User).filter(User.email == "veille-hub@test.fr").one_or_none()
    if not user:
        user = User(email="veille-hub@test.fr", username="lecteur")
        db.add(user)

    def actu(n, title, category, days_ago, essentiel="Résumé.", published=True):
        when = now - timedelta(days=days_ago)
        return VeilleItem(
            external_id=f"test:hub:{n}", title=title, essentiel=essentiel,
            impact="Impact.", category=category,
            source_url=f"https://exemple.fr/{n}", source_name="Dalloz",
            published_at=when if published else None, created_at=when,
        )

    items = [
        actu(1, "Réforme de la responsabilité civile adoptée", "civil", 1,
             essentiel="La responsabilité civile extracontractuelle est réécrite."),
        actu(2, "Responsabilité civile : la Cour de cassation précise le préjudice", "civil", 40,
             essentiel="Le préjudice d'anxiété et la responsabilité civile."),
        actu(3, "Garde à vue : nouvelles garanties", "penal", 3),
        actu(4, "Contrats publics : seuils relevés", "public", 400),
        actu(5, "Actu ancienne sans date de publication", "affaires", 20, published=False),
        actu(6, "Taux d'intérêt 100% fixe", "affaires", 2),
    ]
    db.add_all(items)
    db.commit()
    ids = {i: it.id for i, it in enumerate(items, start=1)}
    token = create_access_token(str(user.id))
    db.close()

    # Le lifespan (bot Discord, scheduler) n'est pas démarré : TestClient sans `with`.
    client = TestClient(main.app)
    yield SimpleNamespace(c=client, ids=ids, auth={"Authorization": f"Bearer {token}"})
    engine.dispose()
    db_path = engine.url.database if engine.url.get_backend_name() == "sqlite" else None
    if db_path and os.path.exists(db_path):
        os.remove(db_path)


def test_archive_complete_sans_limite_de_14_jours(env):
    r = env.c.get("/veille/actus", params={"limit": 50})
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 6
    # Plus récente d'abord, y compris l'actu de plus d'un an.
    assert [it["id"] for it in data["items"]] == [env.ids[n] for n in (1, 6, 3, 5, 2, 4)]
    first = data["items"][0]
    assert first["category_label"] == "Droit civil"
    assert first["reading_minutes"] == 1
    assert first["is_read"] is False and first["is_saved"] is False
    # Sans date de publication : on se rabat sur la date d'ajout.
    sans_date = next(it for it in data["items"] if it["id"] == env.ids[5])
    assert sans_date["published_at"] is not None


def test_pagination(env):
    page1 = env.c.get("/veille/actus", params={"limit": 4}).json()
    page2 = env.c.get("/veille/actus", params={"limit": 4, "offset": 4}).json()
    assert page1["has_more"] is True and page2["has_more"] is False
    assert len(page1["items"]) == 4 and len(page2["items"]) == 2


def test_filtres_rubrique_periode_recherche(env):
    civ = env.c.get("/veille/actus", params={"category": "civil"}).json()
    assert {it["id"] for it in civ["items"]} == {env.ids[1], env.ids[2]}

    week = env.c.get("/veille/actus", params={"period": "7d"}).json()
    assert {it["id"] for it in week["items"]} == {env.ids[1], env.ids[3], env.ids[6]}

    # Tous les mots doivent apparaître, sans tenir compte de la casse.
    found = env.c.get("/veille/actus", params={"q": "CIVILE préjudice"}).json()
    assert [it["id"] for it in found["items"]] == [env.ids[2]]

    # % et _ sont cherchés tels quels, pas comme jokers SQL.
    pct = env.c.get("/veille/actus", params={"q": "100%"}).json()
    assert [it["id"] for it in pct["items"]] == [env.ids[6]]

    assert env.c.get("/veille/actus", params={"period": "2d"}).status_code == 400
    assert env.c.get("/veille/actus", params={"status": "lu"}).status_code == 400


def test_rubriques(env):
    data = env.c.get("/veille/rubriques").json()
    assert data["total"] == 6
    assert data["week_total"] == 3
    by_key = {r["key"]: r for r in data["rubriques"]}
    assert [r["key"] for r in data["rubriques"]][:5] == ["civil", "penal", "public", "affaires", "general"]
    assert by_key["civil"]["count"] == 2 and by_key["civil"]["week_count"] == 1
    assert by_key["general"]["count"] == 0 and by_key["general"]["last_at"] is None


def test_detail_et_a_lire_aussi(env):
    r = env.c.get(f"/veille/actus/{env.ids[1]}")
    assert r.status_code == 200
    data = r.json()
    assert data["item"]["id"] == env.ids[1]
    # L'autre actu sur la responsabilité civile arrive en tête.
    assert data["related"][0]["id"] == env.ids[2]
    assert env.ids[1] not in [it["id"] for it in data["related"]]
    assert len(data["related"]) <= 4
    assert env.c.get("/veille/actus/999999").status_code == 404


def test_lu_et_a_relire_pour_un_utilisateur_connecte(env):
    c, auth, ids = env.c, env.auth, env.ids

    # Filtres personnels : connexion requise.
    assert c.get("/veille/actus", params={"status": "saved"}).status_code == 401

    assert c.post(f"/veille/read/{ids[3]}", headers=auth).status_code == 200
    assert c.put(f"/veille/actus/{ids[2]}/save", headers=auth).json()["is_saved"] is True
    # Deux fois : pas de doublon.
    assert c.put(f"/veille/actus/{ids[2]}/save", headers=auth).status_code == 200

    saved = c.get("/veille/actus", params={"status": "saved"}, headers=auth).json()
    assert [it["id"] for it in saved["items"]] == [ids[2]]
    assert saved["items"][0]["is_saved"] is True

    unread = c.get("/veille/actus", params={"status": "unread", "limit": 50}, headers=auth).json()
    assert ids[3] not in [it["id"] for it in unread["items"]]
    assert unread["total"] == 5

    detail = c.get(f"/veille/actus/{ids[3]}", headers=auth).json()
    assert detail["item"]["is_read"] is True

    assert c.delete(f"/veille/actus/{ids[2]}/save", headers=auth).json()["is_saved"] is False
    assert c.delete(f"/veille/actus/{ids[2]}/save", headers=auth).status_code == 200
    assert c.get("/veille/actus", params={"status": "saved"}, headers=auth).json()["total"] == 0

    assert c.put("/veille/actus/999999/save", headers=auth).status_code == 404
