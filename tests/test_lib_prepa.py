"""
Vue Prépa de NAVIRE Lib contre une base SQLite : un étudiant inscrit voit son
niveau, ses matières (PDF du groupe), l'emploi du temps, les cours Lib de sa
classe ; il dépose une copie, que le professeur de son groupe corrige depuis
son espace. Aperçu admin « voir comme un L1 ».

Lancer : pytest tests/test_lib_prepa.py
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

MATIERE = "L1_intro_au_droit"
AUTRE = "L1_droit_constit"
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
TAG = "zz-test-prepa"


@pytest.fixture(scope="module")
def env():
    import app.main as main
    from app.db.database import Base, engine

    engine.dispose()
    Base.metadata.create_all(bind=engine)
    yield main
    engine.dispose()


def _nettoyer():
    from app.db.database import SessionLocal
    from app.db.models import (
        LibDocument, PrepaAdjurisAffectation, PrepaAdjurisClasse, PrepaAdjurisDevoir, PrepaAdjurisEnrollment,
        PrepaAdjurisProfesseur, PrepaAdjurisRemuneration, PrepaAdjurisRessource, PrepaAdjurisSeance,
    )
    db = SessionLocal()
    for m in (PrepaAdjurisDevoir, PrepaAdjurisRemuneration, PrepaAdjurisAffectation, PrepaAdjurisProfesseur,
              PrepaAdjurisRessource, PrepaAdjurisSeance):
        db.query(m).delete()
    db.query(PrepaAdjurisEnrollment).filter(PrepaAdjurisEnrollment.matiere_key.like("L1_%")).delete(
        synchronize_session=False)
    db.query(PrepaAdjurisClasse).delete()
    db.query(LibDocument).filter(LibDocument.kicker == TAG).delete(synchronize_session=False)
    db.commit()
    db.close()


@pytest.fixture(autouse=True)
def base_propre(env, tmp_path, monkeypatch):
    import app.routers.lib_prepa as lp
    import app.routers.professeurs as r
    import app.services.devoirs as dsvc
    import app.services.professeurs as svc

    _nettoyer()
    monkeypatch.setattr(r, "PREPA_ADJURIS_DIR", tmp_path)
    monkeypatch.setattr(lp, "DEVOIRS_DIR", tmp_path / "devoirs")
    monkeypatch.setattr(svc, "fin_programme", lambda: (2100, 12))
    mails = []
    monkeypatch.setattr(dsvc, "mail_nouvelle_copie", lambda *a: mails.append(("prof", a)))
    monkeypatch.setattr(dsvc, "mail_copie_corrigee", lambda *a: mails.append(("eleve", a)))
    yield mails
    _nettoyer()


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


@pytest.fixture()
def monde(env, base_propre):
    from app.db.database import SessionLocal
    from app.db.models import LibDocument, PrepaAdjurisEnrollment, PrepaAdjurisSeance

    db = SessionLocal()
    admin, prof, alice, zoe = (_user(db, "admin", admin=True), _user(db, "prof"),
                               _user(db, "alice"), _user(db, "zoe"))
    db.add(PrepaAdjurisEnrollment(user_id=alice.id, email=alice.email, matiere_key=MATIERE,
                                  status="active", source="admin",
                                  inscrit_le=datetime.now(timezone.utc) - timedelta(days=30)))
    now = datetime.now(timezone.utc)
    db.add_all([
        PrepaAdjurisSeance(niveau="L1", matiere_key=MATIERE, titre="Les sources", date_debut=now + timedelta(days=3)),
        PrepaAdjurisSeance(niveau="L1", matiere_key=AUTRE, date_debut=now + timedelta(days=4)),       # non souscrite
        PrepaAdjurisSeance(niveau="L1", matiere_key=None, titre="Méthodo", date_debut=now + timedelta(days=5)),
        PrepaAdjurisSeance(niveau="L2", matiere_key=None, date_debut=now + timedelta(days=2)),        # autre niveau
    ])
    db.add_all([
        LibDocument(titre="Introduction au droit : les sources", matiere="Introduction au droit", niveau="L1",
                    kicker=TAG, is_published=True, content={"blocks": []}),
        LibDocument(titre="Fiche de méthode", matiere="", niveau="L1", kicker=TAG, is_published=True,
                    content={"blocks": []}),
        LibDocument(titre="Droit des obligations", matiere="Droit des obligations", niveau="L2", kicker=TAG,
                    is_published=True, content={"blocks": []}),
        LibDocument(titre="Brouillon intro", matiere="Introduction au droit", niveau="L1", kicker=TAG,
                    is_published=False, content={"blocks": []}),
    ])
    db.commit()
    db.close()

    c = TestClient(env.app)
    A = _headers(admin)
    g1 = c.post("/professeurs/admin/classes", headers=A, json={"matiere_key": MATIERE, "nom": "Intro G1"}).json()["id"]
    g2 = c.post("/professeurs/admin/classes", headers=A, json={"matiere_key": MATIERE, "nom": "Intro G2"}).json()["id"]
    p = c.post("/professeurs/admin", headers=A, json={"email": prof.email, "classe_ids": [g1]})
    assert p.status_code == 200, p.text
    return {"c": c, "A": A, "admin": admin, "prof": prof, "alice": alice, "zoe": zoe, "g1": g1, "g2": g2}


def test_encart_prepa_reserve_aux_inscrits(monde):
    c = monde["c"]
    assert c.get("/lib/catalogue", headers=_headers(monde["alice"])).json()["user"]["prepa"] == "L1"
    assert c.get("/lib/catalogue", headers=_headers(monde["zoe"])).json()["user"]["prepa"] is None
    assert c.get("/lib/prepa", headers=_headers(monde["zoe"])).json() == {"prepa": False, "apercu": None}
    # voir_comme est ignoré pour un compte qui n'est pas admin
    assert c.get("/lib/prepa?voir_comme=L1", headers=_headers(monde["zoe"])).json()["prepa"] is False


def test_vue_prepa_etudiant(monde):
    c, P, alice = monde["c"], _headers(monde["prof"]), _headers(monde["alice"])
    doc = c.post(f"/professeurs/classes/{monde['g1']}/documents", headers=P,
                 files={"file": ("td.pdf", PDF, "application/pdf")}, data={"titre": "TD 1"}).json()["id"]

    v = c.get("/lib/prepa", headers=alice).json()
    assert v["prepa"] is True and v["niveau"] == "L1" and v["apercu"] is None
    intro = next(m for m in v["matieres"] if m["matiere_key"] == MATIERE)
    assert intro["debloquee"] and intro["groupe"]["nom"] == "Intro G1"
    assert [(r["id"], r["professeur"]) for r in intro["ressources"]] == [(doc, True)]
    assert all(not m["debloquee"] and m["ressources"] == [] for m in v["matieres"] if m["matiere_key"] != MATIERE)

    # Emploi du temps : sa matière + séances communes de son niveau
    assert [s["matiere"] for s in v["seances"]] == ["Introduction au droit", "Séance commune"]
    assert v["prochaine_seance"]["titre"] == "Les sources"

    # Cours Lib de la classe : par matière, puis ceux du niveau ; pas de L2, pas de brouillon
    titres = {d["titre"]: d["matiere_key"] for d in v["cours_lib"]}
    assert titres == {"Introduction au droit : les sources": MATIERE, "Fiche de méthode": None}
    assert len(intro["cours_lib"]) == 1


def test_apercu_admin(monde):
    c, A, P = monde["c"], monde["A"], _headers(monde["prof"])
    doc = c.post(f"/professeurs/classes/{monde['g2']}/documents", headers=_headers(monde["prof"]),
                 files={"file": ("x.pdf", PDF, "application/pdf")})
    assert doc.status_code == 404          # le professeur n'est pas sur G2
    doc = c.post(f"/professeurs/classes/{monde['g1']}/documents", headers=P,
                 files={"file": ("td.pdf", PDF, "application/pdf")}, data={"titre": "TD 1"}).json()["id"]

    v = c.get("/lib/prepa?voir_comme=l1", headers=A).json()
    assert v["apercu"] == "L1" and all(m["debloquee"] for m in v["matieres"])
    assert {s["matiere_key"] for s in v["seances"]} == {MATIERE, AUTRE, None}
    intro = next(m for m in v["matieres"] if m["matiere_key"] == MATIERE)
    assert intro["ressources"][0]["groupe"] == "Intro G1"
    assert c.get(f"/prepa/adjuris/ressources/{doc}/download", headers=A).content == PDF

    cat = c.get("/lib/catalogue?voir_comme=L1", headers=A).json()
    assert cat["user"]["prepa"] == "L1" and cat["user"]["apercu"] == "L1" and cat["user"]["admin"]
    assert "Brouillon intro" not in [d["titre"] for d in cat["items"]]
    assert "Brouillon intro" in [d["titre"] for d in c.get("/lib/catalogue", headers=A).json()["items"]]
    assert c.get("/lib/prepa?voir_comme=L9", headers=A).status_code == 400


def test_devoir_depose_puis_corrige(monde, base_propre):
    mails = base_propre
    c, P, alice = monde["c"], _headers(monde["prof"]), _headers(monde["alice"])

    # Matière non souscrite, fichier non PDF
    assert c.post("/lib/prepa/devoirs", headers=alice, data={"matiere_key": AUTRE},
                  files={"file": ("c.pdf", PDF, "application/pdf")}).status_code == 403
    assert c.post("/lib/prepa/devoirs", headers=alice, data={"matiere_key": MATIERE},
                  files={"file": ("c.txt", b"x", "text/plain")}).status_code == 400

    r = c.post("/lib/prepa/devoirs", headers=alice, data={"matiere_key": MATIERE, "titre": "Dissertation 1",
                                                          "message": "Merci !"},
               files={"file": ("copie.pdf", PDF, "application/pdf")})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["statut"] == "depose" and d["classe_id"] == monde["g1"]
    assert mails and mails[0][0] == "prof" and mails[0][1][0] == [monde["prof"].email]

    assert c.get(f"/lib/prepa/devoirs/{d['id']}/fichier", headers=alice).content == PDF
    assert c.get(f"/lib/prepa/devoirs/{d['id']}/fichier", headers=_headers(monde["zoe"])).status_code == 404
    assert [x["id"] for x in c.get("/lib/prepa", headers=alice).json()["devoirs"]] == [d["id"]]

    # Côté professeur
    dash = c.get("/professeurs/me/dashboard", headers=P).json()
    assert dash["totaux"]["devoirs_a_corriger"] == 1
    liste = c.get("/professeurs/me/devoirs", headers=P).json()
    assert liste["a_corriger"] == 1
    assert liste["items"][0]["etudiant"]["username"] == "alice" and liste["items"][0]["classe_nom"] == "Intro G1"
    assert c.get(f"/professeurs/devoirs/{d['id']}/download", headers=P).content == PDF
    assert c.get("/professeurs/me/devoirs", headers=alice).status_code == 403

    r = c.patch(f"/professeurs/devoirs/{d['id']}", headers=P, json={"commentaire": "Bon plan.", "note": "14/20"})
    assert r.status_code == 200, r.text
    assert (r.json()["statut"], r.json()["note"], r.json()["corrige_par"]) == ("corrige", "14/20", "prof")
    assert [m[0] for m in mails] == ["prof", "eleve"]

    r = c.post(f"/professeurs/devoirs/{d['id']}/correction", headers=P,
               files={"file": ("corrige.pdf", PDF, "application/pdf")})
    assert r.status_code == 200 and r.json()["correction"] is True
    assert len(mails) == 2                     # déjà corrigée : pas de second mail
    assert c.get(f"/lib/prepa/devoirs/{d['id']}/correction", headers=alice).content == PDF

    vue = c.get("/lib/prepa", headers=alice).json()["devoirs"][0]
    assert (vue["commentaire"], vue["note"], vue["correction"]) == ("Bon plan.", "14/20", True)

    # Corrigée : l'étudiant ne peut plus la retirer ; l'admin la voit aussi
    assert c.delete(f"/lib/prepa/devoirs/{d['id']}", headers=alice).status_code == 400
    assert c.get("/professeurs/me/devoirs", headers=monde["A"]).json()["a_corriger"] == 0


def test_devoir_hors_groupe_et_retrait(monde):
    c, P, A, alice = monde["c"], _headers(monde["prof"]), monde["A"], _headers(monde["alice"])
    from app.db.database import SessionLocal
    from app.db.models import PrepaAdjurisEnrollment

    # Alice passe dans le groupe 2 : sa copie part vers G2, le professeur de G1 ne la voit pas.
    db = SessionLocal()
    e = db.query(PrepaAdjurisEnrollment).filter_by(user_id=monde["alice"].id).one()
    eid = e.id
    db.close()
    assert c.patch(f"/professeurs/admin/inscriptions/{eid}", headers=A, json={"classe_id": monde["g2"]}).status_code == 200

    d = c.post("/lib/prepa/devoirs", headers=alice, data={"matiere_key": MATIERE},
               files={"file": ("copie.pdf", PDF, "application/pdf")}).json()
    assert d["classe_id"] == monde["g2"] and d["titre"] == "copie"
    assert c.get("/professeurs/me/devoirs", headers=P).json()["items"] == []
    assert c.patch(f"/professeurs/devoirs/{d['id']}", headers=P, json={"note": "1"}).status_code == 404

    assert c.delete(f"/lib/prepa/devoirs/{d['id']}", headers=alice).status_code == 200
    assert c.get("/lib/prepa", headers=alice).json()["devoirs"] == []
