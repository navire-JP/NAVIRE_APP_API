"""
Page d'accueil : 4 premiers du classement pour le hero, avis saisis dans la
console et reliés à de vrais comptes.

Lancer : pytest tests/test_landing.py
"""

import os
import uuid

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

    engine.dispose()
    Base.metadata.create_all(bind=engine)
    yield TestClient(main.app)
    engine.dispose()


def _compte(nom, elo=0, admin=False, **extra):
    from app.db.database import SessionLocal
    from app.db.models import User
    from app.core.security import create_access_token

    db = SessionLocal()
    u = User(email=f"{nom}-{uuid.uuid4().hex[:8]}@t.fr", username=f"{nom}{uuid.uuid4().hex[:4]}",
             elo=elo, is_admin=admin, **extra)
    db.add(u)
    db.commit()
    out = (u.id, u.username, {"Authorization": f"Bearer {create_access_token(str(u.id))}"})
    db.close()
    return out


def test_hero_top4(client):
    # La base de test est gardée entre deux lancements : on passe au-dessus de tout le monde.
    from app.db.database import SessionLocal
    from app.db.models import User
    from sqlalchemy import func, select
    with SessionLocal() as db:
        base = (db.execute(select(func.max(User.elo))).scalar_one() or 0) + 1
    hauts = [_compte(f"top{i}", elo=base + i) for i in range(5)]
    r = client.get("/landing/hero").json()
    assert [u["username"] for u in r["top"]] == [h[1] for h in reversed(hauts)][:4]
    assert set(r["top"][0]) == {"username", "avatar_url"}   # pas d'email ni d'id
    assert r["membres"] >= 5


def test_avis(client):
    admin = _compte("admin", admin=True)
    eleve = _compte("eleve", study_level="L2", university="Paris 1")
    autre = _compte("autre")

    # Réservé aux admins
    assert client.get("/admin/avis", headers=eleve[2]).status_code == 403
    assert client.post("/admin/avis", json={"user_id": eleve[0], "texte": "x"}, headers=eleve[2]).status_code == 403

    # Recherche du compte à relier : pseudo ou email, sans tenir compte des majuscules
    assert client.get("/admin/avis/comptes", params={"q": "x"}, headers=eleve[2]).status_code == 403
    r = client.get("/admin/avis/comptes", params={"q": eleve[1].upper()}, headers=admin[2]).json()
    assert r["items"][0]["id"] == eleve[0] and r["items"][0]["email"].startswith("eleve-")
    r = client.get("/admin/avis/comptes", params={"q": r["items"][0]["email"].upper()}, headers=admin[2]).json()
    assert [c["id"] for c in r["items"]] == [eleve[0]]
    assert client.get("/admin/avis/comptes", params={"q": "e"}, headers=admin[2]).json()["items"] == []

    # Compte inexistant, avis vide
    assert client.post("/admin/avis", json={"user_id": 99_999_999, "texte": "Top"}, headers=admin[2]).status_code == 404
    assert client.post("/admin/avis", json={"user_id": eleve[0], "texte": "   "}, headers=admin[2]).status_code == 400

    a = client.post("/admin/avis", json={"user_id": eleve[0], "texte": "  Super   prépa\nmerci ", "note": 5},
                    headers=admin[2]).json()
    assert a["texte"] == "Super prépa\nmerci" and a["mention_affichee"] == "L2 · Paris 1"
    b = client.post("/admin/avis", json={"user_id": autre[0], "texte": "Bien", "note": 4, "mention": "M1"},
                    headers=admin[2]).json()

    pub = [i for i in client.get("/landing/avis").json()["items"] if i["id"] in (a["id"], b["id"])]
    assert [i["id"] for i in pub] == [a["id"], b["id"]]
    assert pub[0]["user"]["username"] == eleve[1] and pub[0]["mention"] == "L2 · Paris 1"
    assert "email" not in pub[0]["user"] and pub[1]["mention"] == "M1"

    # Réordonner, masquer, supprimer
    client.patch(f"/admin/avis/{b['id']}", json={"position": a["position"] - 1}, headers=admin[2])
    ids = [i["id"] for i in client.get("/landing/avis").json()["items"]]
    assert ids.index(b["id"]) < ids.index(a["id"])
    client.patch(f"/admin/avis/{a['id']}", json={"active": False}, headers=admin[2])
    assert a["id"] not in [i["id"] for i in client.get("/landing/avis").json()["items"]]
    assert a["id"] in [i["id"] for i in client.get("/admin/avis", headers=admin[2]).json()["items"]]
    assert client.delete(f"/admin/avis/{b['id']}", headers=admin[2]).json()["ok"]
    assert b["id"] not in [i["id"] for i in client.get("/admin/avis", headers=admin[2]).json()["items"]]


def test_embeds(client):
    for p in ("/landing/embed/hero", "/landing/embed/avis", "/landing/embed/features", "/landing/embed/professeurs"):
        r = client.get(p)
        assert r.status_code == 200 and 'API: ""' in r.text


def test_captures_absentes(client):
    # Capture prévue mais pas encore déposée : 404 propre, l'embed affiche un visuel provisoire.
    assert client.get("/assets/screen-actus.png").status_code in (200, 404)
    # Captures fournies : servies telles quelles
    for n in ("lib", "lib-lecture", "fichiers", "prepa", "copie"):
        r = client.get(f"/assets/screen-{n}.png")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert client.get("/assets/screen-inconnue.png").status_code == 404


def test_professeurs(client):
    from app.db.database import SessionLocal
    from app.db.models import PrepaAdjurisAffectation, PrepaAdjurisClasse, PrepaAdjurisProfesseur

    a = _compte("prof", bio="Doctorante en droit public")
    b = _compte("suspendu")
    with SessionLocal() as db:
        c1 = PrepaAdjurisClasse(matiere_key="L2_droit_administratif", nom="L2 admin")
        c2 = PrepaAdjurisClasse(matiere_key="L1_droit_constit", nom="L1 constit")
        c3 = PrepaAdjurisClasse(matiere_key="L3_droit_des_societes", nom="vieux", is_active=False)
        db.add_all([c1, c2, c3]); db.flush()
        pa = PrepaAdjurisProfesseur(user_id=a[0], prenom="Julie", nom="martin", grade="ChargéTD")
        pb = PrepaAdjurisProfesseur(user_id=b[0], prenom="Paul", nom="Durand", status="suspendu")
        db.add_all([pa, pb]); db.flush()
        for c in (c1, c2, c3):
            db.add(PrepaAdjurisAffectation(professeur_id=pa.id, classe_id=c.id, remu_seance_cents=5000))
        db.commit()

    items = client.get("/landing/professeurs").json()["items"]
    julie = next(i for i in items if i["nom"] == "Julie M.")
    assert julie["grade"] == "Chargé de TD" and julie["bio"] == "Doctorante en droit public"
    # Matières des groupes actifs seulement, dans l'ordre des niveaux
    assert julie["matieres"] == [
        {"niveau": "L1", "nom": "Droit constitutionnel"},
        {"niveau": "L2", "nom": "Droit administratif"},
    ]
    assert set(julie) == {"nom", "avatar_url", "grade", "bio", "matieres"}   # ni email ni rémunération
    assert not any(i["nom"].startswith("Paul") for i in items)               # suspendu : absent
