"""
Espace professeurs contre une base SQLite : groupes et professeur créés dans la
console → étudiants du groupe → prévisionnel → dépôt d'un PDF, visible des
seuls étudiants du groupe (espace AdJuris et NAVIRE Lib).

Lancer : pytest tests/test_professeurs.py
(variables d'environnement factices posées ci-dessous, aucune clé réelle.)
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

MATIERE = "L2_droit_penal"
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.fixture(scope="module")
def env():
    import app.main as main
    from app.db.database import Base, engine

    engine.dispose()
    Base.metadata.create_all(bind=engine)
    yield main
    engine.dispose()


@pytest.fixture(autouse=True)
def base_propre(env, tmp_path, monkeypatch):
    """Tables des professeurs vides, PDF écrits dans un dossier temporaire,
    programme sans date de fin."""
    from app.db.database import SessionLocal
    from app.db.models import (
        PrepaAdjurisAffectation, PrepaAdjurisClasse, PrepaAdjurisEnrollment,
        PrepaAdjurisProfesseur, PrepaAdjurisRemuneration, PrepaAdjurisRessource, PrepaAdjurisSeance,
    )
    import app.routers.professeurs as r
    import app.services.professeurs as svc

    db = SessionLocal()
    for m in (PrepaAdjurisRemuneration, PrepaAdjurisAffectation, PrepaAdjurisProfesseur, PrepaAdjurisRessource, PrepaAdjurisSeance):
        db.query(m).delete()
    db.query(PrepaAdjurisEnrollment).filter(PrepaAdjurisEnrollment.matiere_key == MATIERE).delete()
    db.query(PrepaAdjurisClasse).delete()
    db.commit()
    db.close()
    monkeypatch.setattr(r, "PREPA_ADJURIS_DIR", tmp_path)
    monkeypatch.setattr(svc, "fin_programme", lambda: (2100, 12))


def _user(db, username, admin=False):
    from app.db.models import User
    u = User(email=f"{username}-{uuid.uuid4().hex[:8]}@test.fr", username=username, is_admin=admin)
    db.add(u)
    db.commit()
    db.refresh(u)
    db.expunge(u)
    return u


def _headers(user):
    from app.core.security import create_access_token
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def _inscrire(db, user, inscrit_le):
    from app.db.models import PrepaAdjurisEnrollment
    e = PrepaAdjurisEnrollment(user_id=user.id, email=user.email, matiere_key=MATIERE,
                               status="active", source="admin", inscrit_le=inscrit_le)
    db.add(e)
    db.commit()
    return e.id


@pytest.fixture()
def monde(env):
    """Deux groupes de pénal, un professeur sur le premier, un étudiant dans
    chaque groupe, et trois séances (une passée, deux à venir)."""
    from app.db.database import SessionLocal
    from app.db.models import PrepaAdjurisSeance

    db = SessionLocal()
    admin, prof, alice, bob = (_user(db, "admin", admin=True), _user(db, "prof"),
                               _user(db, "alice"), _user(db, "bob"))
    now = datetime.now(timezone.utc)
    ea = _inscrire(db, alice, now - timedelta(days=60))
    eb = _inscrire(db, bob, now - timedelta(days=60))
    for jours in (-7, 7, 14):
        db.add(PrepaAdjurisSeance(niveau="L2", matiere_key=MATIERE, date_debut=now + timedelta(days=jours)))
    db.commit()
    db.close()

    c = TestClient(env.app)
    A = _headers(admin)
    g1 = c.post("/professeurs/admin/classes", headers=A, json={
        "matiere_key": MATIERE, "nom": "L2 Pénal — groupe 1",
        "discord_url": "https://discord.com/channels/1131156852265201715/1533885924121903154",
        "liens": [{"label": "Drive", "url": "https://drive.google.com/x"}],
    })
    assert g1.status_code == 200, g1.text
    g2 = c.post("/professeurs/admin/classes", headers=A, json={"matiere_key": MATIERE, "nom": "L2 Pénal — groupe 2"})
    g1, g2 = g1.json()["id"], g2.json()["id"]

    # Bob passe dans le groupe 2 ; Alice reste dans le premier groupe.
    assert c.patch(f"/professeurs/admin/inscriptions/{eb}", headers=A, json={"classe_id": g2}).status_code == 200

    r = c.post("/professeurs/admin", headers=A, json={
        "email": prof.email.upper(), "prenom": "Paul", "nom": "Prof", "classe_ids": [g1],
    })
    assert r.status_code == 200, r.text
    p = r.json()
    return {"c": c, "A": A, "admin": admin, "prof": prof, "alice": alice, "bob": bob,
            "g1": g1, "g2": g2, "ea": ea, "eb": eb, "p": p}


def test_console_reservee_aux_admins(monde):
    c = monde["c"]
    assert c.get("/professeurs/admin/list", headers=_headers(monde["alice"])).status_code == 403
    assert c.post("/professeurs/admin/classes", headers=monde["A"],
                  json={"matiere_key": "L9_inconnue", "nom": "X Y"}).status_code == 400
    assert c.post("/professeurs/admin/classes", headers=monde["A"],
                  json={"matiere_key": MATIERE, "nom": "X Y", "discord_url": "discord.gg/x"}).status_code == 422


def test_professeur_attache_avec_grade(monde):
    from app.db.database import SessionLocal
    from app.db.models import User
    c, p = monde["c"], monde["p"]
    assert p["grade"] == "ChargéTD"
    assert [a["classe"]["id"] for a in p["affectations"]] == [monde["g1"]]

    me = c.get("/professeurs/me", headers=_headers(monde["prof"])).json()
    assert me == {"status": "active", "grade": "ChargéTD", "prenom": "Paul", "nom": "Prof", "nb_classes": 1}
    assert c.get("/professeurs/me", headers=_headers(monde["alice"])).json() == {"status": "none"}

    db = SessionLocal()
    assert db.get(User, monde["prof"].id).grade == "ChargéTD"
    db.close()

    # Déjà professeur, compte inconnu
    assert c.post("/professeurs/admin", headers=monde["A"], json={"email": monde["prof"].email}).status_code == 400
    assert c.post("/professeurs/admin", headers=monde["A"], json={"email": "personne@test.fr"}).status_code == 404

    # Détaché : le compte reprend son grade de départ.
    assert c.delete(f"/professeurs/admin/{p['id']}", headers=monde["A"]).status_code == 200
    db = SessionLocal()
    assert db.get(User, monde["prof"].id).grade == "Primo"
    db.close()


def test_tableau_de_bord_etudiants_et_previsionnel(monde):
    c, P = monde["c"], _headers(monde["prof"])
    aff = monde["p"]["affectations"][0]["id"]
    r = c.patch(f"/professeurs/admin/affectations/{aff}", headers=monde["A"],
                json={"remu_seance_cents": 500, "remu_etudiant_cents": 1000})
    assert r.status_code == 200, r.text

    d = c.get("/professeurs/me/dashboard", headers=P).json()
    assert len(d["classes"]) == 1
    g = d["classes"][0]
    assert g["discord_url"].endswith("/1533885924121903154")
    assert g["liens"] == [{"label": "Drive", "url": "https://drive.google.com/x"}]
    assert [e["user_id"] for e in g["etudiants"]] == [monde["alice"].id]   # Bob est dans le groupe 2

    prev = g["previsionnel"]
    assert prev["par_seance_cents"] == 1500
    assert (prev["seances_passees"], prev["seances_a_venir"]) == (1, 2)
    assert (prev["realise_cents"], prev["a_venir_cents"]) == (1500, 3000)
    assert d["totaux"]["total_cents"] == 4500
    assert sum(m["montant_cents"] for m in d["totaux"]["mois"]) == 4500

    # Un second groupe pour le même professeur
    c.post(f"/professeurs/admin/{monde['p']['id']}/affectations", headers=monde["A"], json={"classe_id": monde["g2"]})
    d = c.get("/professeurs/me/dashboard", headers=P).json()
    assert [len(g["etudiants"]) for g in d["classes"]] == [1, 1]
    assert d["totaux"]["etudiants"] == 2

    # Non-professeur, professeur suspendu
    assert c.get("/professeurs/me/dashboard", headers=_headers(monde["alice"])).status_code == 403
    c.patch(f"/professeurs/admin/{monde['p']['id']}", headers=monde["A"], json={"status": "suspendu"})
    assert c.get("/professeurs/me/dashboard", headers=P).status_code == 403


def test_document_visible_du_seul_groupe(monde):
    c, P = monde["c"], _headers(monde["prof"])
    r = c.post(f"/professeurs/classes/{monde['g1']}/documents", headers=P,
               files={"file": ("td1.pdf", PDF, "application/pdf")}, data={"titre": "TD 1 — l'élément moral"})
    assert r.status_code == 200, r.text
    doc = r.json()["id"]

    # Pas de dépôt dans un groupe qui n'est pas le sien
    assert c.post(f"/professeurs/classes/{monde['g2']}/documents", headers=P,
                  files={"file": ("x.pdf", PDF, "application/pdf")}).status_code == 404

    alice, bob = _headers(monde["alice"]), _headers(monde["bob"])
    lib_alice = c.get("/lib/adjuris", headers=alice).json()["items"]
    assert [(i["id"], i["professeur"]) for i in lib_alice] == [(doc, True)]
    assert c.get("/lib/adjuris", headers=bob).json()["items"] == []

    espace = c.get("/prepa/adjuris/espace", headers=alice).json()
    penal = next(m for m in espace["matieres"] if m["matiere_key"] == MATIERE)
    assert [x["id"] for x in penal["ressources"]] == [doc]
    espace_bob = c.get("/prepa/adjuris/espace", headers=bob).json()
    assert next(m for m in espace_bob["matieres"] if m["matiere_key"] == MATIERE)["ressources"] == []

    assert c.get(f"/prepa/adjuris/ressources/{doc}/download", headers=alice).content == PDF
    assert c.get(f"/prepa/adjuris/ressources/{doc}/download", headers=bob).status_code == 403

    d = c.get("/professeurs/me/dashboard", headers=P).json()
    assert [x["titre"] for x in d["classes"][0]["documents"]] == ["TD 1 — l'élément moral"]
    assert c.get(f"/professeurs/documents/{doc}/download", headers=P).content == PDF

    # Groupe avec documents : suppression refusée
    assert c.delete(f"/professeurs/admin/classes/{monde['g1']}", headers=monde["A"]).status_code == 400
    assert c.delete(f"/professeurs/documents/{doc}", headers=P).status_code == 200
    assert c.get("/lib/adjuris", headers=alice).json()["items"] == []


def test_groupe_supprime_ses_etudiants_rejoignent_le_premier(monde):
    c, A = monde["c"], monde["A"]
    assert c.delete(f"/professeurs/admin/classes/{monde['g2']}", headers=A).status_code == 200
    r = c.get(f"/professeurs/admin/classes/{monde['g1']}/etudiants", headers=A).json()
    assert sorted(e["user_id"] for e in r["items"]) == sorted([monde["alice"].id, monde["bob"].id])


def test_groupes_par_defaut(env):
    from app.db.database import SessionLocal
    from app.db.models import PrepaAdjurisClasse
    from app.services.professeurs import creer_groupes_par_defaut

    db = SessionLocal()
    assert creer_groupes_par_defaut(db) == 9
    assert creer_groupes_par_defaut(db) == 0
    societes = db.query(PrepaAdjurisClasse).filter_by(matiere_key="L3_droit_des_societes").one()
    assert societes.discord_url == "https://discord.com/channels/1131156852265201715/1533886138119356416"
    assert societes.nom == "L3 – Droit des sociétés"
    db.close()


def test_pilotage_des_remunerations(monde):
    """Montant calculé, montant fixé à la main, versement (figé), annulation."""
    from app.services.prepa_adjuris_billing import cle_mois, mois_de

    c, A, P = monde["c"], monde["A"], _headers(monde["prof"])
    aff = monde["p"]["affectations"][0]["id"]
    c.patch(f"/professeurs/admin/affectations/{aff}", headers=A, json={"remu_seance_cents": 0, "remu_etudiant_cents": 1000})
    assert c.get("/professeurs/admin/remunerations", headers=_headers(monde["alice"])).status_code == 403

    t = c.get("/professeurs/admin/remunerations", headers=A).json()
    assert sum(l["calcule_cents"] for l in t["items"]) == 3000          # 3 séances × 1 étudiant × 10 €
    assert t["totaux"]["montant_cents"] == t["totaux"]["a_verser_cents"] == 3000
    m = t["items"][0]["mois"]

    # Montant fixé : remplace le calcul, côté console et côté professeur.
    r = c.put("/professeurs/admin/remunerations", headers=A,
              json={"affectation_id": aff, "mois": m, "montant_cents": 5000, "note": "prime TD"})
    assert r.status_code == 200, r.text
    assert (r.json()["fixe_cents"], r.json()["montant_cents"], r.json()["note"]) == (5000, 5000, "prime TD")
    calc_m = r.json()["calcule_cents"]
    d = c.get("/professeurs/me/dashboard", headers=P).json()
    assert d["totaux"]["total_cents"] == 3000 - calc_m + 5000

    # Un mois sans séance peut aussi être fixé (prime, rattrapage).
    c.put("/professeurs/admin/remunerations", headers=A, json={"affectation_id": aff, "mois": "2099-01", "montant_cents": 700})
    assert c.get("/professeurs/admin/remunerations?mois=2099-01", headers=A).json()["totaux"]["montant_cents"] == 700

    # Versé : montant figé, plus modifiable ; l'affectation ne se supprime plus.
    v = c.post("/professeurs/admin/remunerations/verser", headers=A,
               json={"lignes": [{"affectation_id": aff, "mois": m}], "reference": "VIR-1"}).json()
    assert v["total_cents"] == 5000
    assert c.post("/professeurs/admin/remunerations/verser", headers=A,
                  json={"lignes": [{"affectation_id": aff, "mois": m}]}).json()["ignores"]
    assert c.put("/professeurs/admin/remunerations", headers=A,
                 json={"affectation_id": aff, "mois": m, "montant_cents": 1}).status_code == 400
    assert c.delete(f"/professeurs/admin/affectations/{aff}", headers=A).status_code == 400
    assert c.delete(f"/professeurs/admin/{monde['p']['id']}", headers=A).status_code == 400
    d = c.get("/professeurs/me/dashboard", headers=P).json()
    assert d["totaux"]["verse_cents"] == 5000
    assert next(x for x in d["classes"][0]["previsionnel"]["mois"] if x["mois"] == m)["verse_le"]

    # Annulation du versement, puis retour au montant calculé.
    assert c.post("/professeurs/admin/remunerations/annuler-versement", headers=A,
                  json={"affectation_id": aff, "mois": m}).json()["verse_le"] is None
    r = c.put("/professeurs/admin/remunerations", headers=A, json={"affectation_id": aff, "mois": m, "montant_cents": None})
    assert (r.json()["fixe_cents"], r.json()["montant_cents"]) == (None, calc_m)
    assert r.json()["note"] == "prime TD"
