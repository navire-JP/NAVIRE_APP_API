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
    from app.db.models import LibProgress
    for d in db.query(LibDocument).filter(LibDocument.prepa_ressource_id.is_not(None)).all():
        db.query(LibProgress).filter(LibProgress.document_id == d.id).delete(synchronize_session=False)
        db.delete(d)
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
    # Le PDF du chargé de TD est un document Lib (pastille Prépa), en tête de la matière.
    prepa = [d for d in v["cours_lib"] if d["prepa"]]
    assert [(d["titre"], d["kind"], d["professeur"], d["groupe"]) for d in prepa] == [("TD 1", "prepa", True, "Intro G1")]
    assert intro["cours_lib"][0] == prepa[0]["id"]
    assert all(not m["debloquee"] and m["cours_lib"] == [] for m in v["matieres"] if m["matiere_key"] != MATIERE)

    # Emploi du temps : sa matière + séances communes de son niveau
    assert [s["matiere"] for s in v["seances"]] == ["Introduction au droit", "Séance commune"]
    assert v["prochaine_seance"]["titre"] == "Les sources"

    # Cours Lib de la classe : par matière, puis ceux du niveau ; pas de L2, pas de brouillon
    titres = {d["titre"]: d["matiere_key"] for d in v["cours_lib"] if not d["prepa"]}
    assert titres == {"Introduction au droit : les sources": MATIERE, "Fiche de méthode": None}
    assert len(intro["cours_lib"]) == 2


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
    assert [d["groupe"] for d in v["cours_lib"] if d["prepa"]] == ["Intro G1"]
    assert c.get(f"/prepa/adjuris/ressources/{doc}/download", headers=A).content == PDF

    cat = c.get("/lib/catalogue?voir_comme=L1", headers=A).json()
    assert cat["user"]["prepa"] == "L1" and cat["user"]["apercu"] == "L1" and cat["user"]["admin"]
    assert [d["titre"] for d in cat["items"] if d["prepa"]] == ["TD 1"]
    # Hors aperçu, l'admin ne voit pas les documents Prépa dans sa bibliothèque
    assert not [d for d in c.get("/lib/catalogue", headers=A).json()["items"] if d["prepa"]]
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


def _pdf_texte() -> bytes:
    import fitz

    pdf = fitz.open()
    page, y = pdf.new_page(), 60
    phrase = "La coutume est une source du droit, faite d'un usage répété et du sentiment d'obligation. "
    for k in range(10):
        for ligne in range(3):
            page.insert_text((60, y), f"{phrase[:80]} ({k}.{ligne})", fontname="helv", fontsize=9)
            y += 13
        y += 10
    return pdf.tobytes()


def test_document_prepa_lu_comme_un_cours_lib(monde):
    """Le PDF d'un chargé de TD se lit dans NAVIRE Lib comme un cours : blocs,
    plan, pastille Prépa ; réservé aux étudiants du groupe ; retiré avec lui."""
    from app.db.database import SessionLocal
    from app.db.models import LibDocument, PrepaAdjurisEnrollment

    c, P, alice = monde["c"], _headers(monde["prof"]), _headers(monde["alice"])
    res = c.post(f"/professeurs/classes/{monde['g1']}/documents", headers=P,
                 files={"file": ("td.pdf", _pdf_texte(), "application/pdf")},
                 data={"titre": "TD 2 · La coutume"}).json()["id"]

    items = c.get("/lib/catalogue", headers=alice).json()["items"]
    d = next(i for i in items if i["prepa"])
    assert (d["titre"], d["kind"], d["niveau"], d["matiere"]) == ("TD 2 · La coutume", "prepa", "L1", "Introduction au droit")
    assert d["description"] == "Document de ton chargé de TD."

    # Lib : le document entier, en blocs (pas de PDF à ouvrir)
    lu = c.get(f"/lib/documents/{d['id']}?mode=lib", headers=alice).json()
    assert lu["blocks"] and lu["document"]["prepa"] is True
    assert lu["document"]["kicker"] == "Prép'AdJuris · Introduction au droit"
    # Lib+ : points de passage posés tout seuls, sans Elo
    plus = c.get(f"/lib/documents/{d['id']}?mode=plus", headers=alice).json()
    assert all(cp["elo_requis"] == 0 and cp["recompense_elo"] == 0 for cp in plus["checkpoints"])

    # Un étudiant d'un autre groupe (ou non inscrit) ne le voit pas
    db = SessionLocal()
    db.add(PrepaAdjurisEnrollment(user_id=monde["zoe"].id, email=monde["zoe"].email, matiere_key=MATIERE,
                                  status="active", source="admin"))
    db.commit()
    e = db.query(PrepaAdjurisEnrollment).filter_by(user_id=monde["zoe"].id).one()
    eid = e.id
    db.close()
    assert c.patch(f"/professeurs/admin/inscriptions/{eid}", headers=monde["A"],
                   json={"classe_id": monde["g2"]}).status_code == 200
    zoe = _headers(monde["zoe"])
    assert not [i for i in c.get("/lib/catalogue", headers=zoe).json()["items"] if i["prepa"]]
    assert c.get(f"/lib/documents/{d['id']}?mode=lib", headers=zoe).status_code == 404

    # Retiré par le professeur : il disparaît de Lib
    assert c.delete(f"/professeurs/documents/{res}", headers=P).status_code == 200
    assert not [i for i in c.get("/lib/catalogue", headers=alice).json()["items"] if i["prepa"]]
    db = SessionLocal()
    assert db.query(LibDocument).filter(LibDocument.prepa_ressource_id == res).count() == 0
    db.close()


def test_onglet_fichiers_cloisonne(monde, tmp_path):
    """GET /files/explorer : chacun ne voit que ses PDF, ses documents Prépa et ses copies."""
    from app.db.database import SessionLocal
    from app.db.models import File

    c, P = monde["c"], _headers(monde["prof"])
    alice, zoe = _headers(monde["alice"]), _headers(monde["zoe"])
    c.post(f"/professeurs/classes/{monde['g1']}/documents", headers=P,
           files={"file": ("td.pdf", PDF, "application/pdf")}, data={"titre": "TD 1"})
    dv = c.post("/lib/prepa/devoirs", headers=alice, data={"matiere_key": MATIERE, "titre": "Dissert"},
                files={"file": ("copie.pdf", PDF, "application/pdf")}).json()["id"]

    chemin = tmp_path / "perso.pdf"
    chemin.write_bytes(PDF)
    db = SessionLocal()
    f = File(user_id=monde["alice"].id, filename_original="Ma fiche.pdf", filename_stored="perso.pdf",
             path=str(chemin), size_bytes=len(PDF))
    db.add(f)
    db.commit()
    fid = f.id
    db.close()

    def plat(nodes):
        for n in nodes:
            yield n
            yield from plat(n.get("children") or [])

    a = c.get("/files/explorer", headers=alice).json()
    secs = {s["id"]: s for s in a["sections"]}
    assert list(secs) == ["mes-pdf", "copies", "enregistres"]
    mon_pdf = secs["mes-pdf"]["items"][0]
    assert mon_pdf["label"] == "Ma fiche.pdf" and mon_pdf["delete"] == f"/files/{fid}"
    assert mon_pdf["study"].startswith("/lib?doc=")          # son double dans la Lib
    copies = [n for n in plat(secs["copies"]["items"]) if n["type"] == "pdf"]
    assert [(n["label"], n["matiere"]) for n in copies] == [("Dissert (copie)", "Introduction au droit")]
    # Le contenu NAVIRE n'est plus recopié ici : pas de documents Prépa, cours Lib en nœuds fantômes
    assert "TD 1" not in [n["label"] for s in a["sections"] for n in plat(s["items"])]
    lib = [n["label"] for n in a["liens"]["lib"]]
    assert "Introduction au droit : les sources" in lib and "Brouillon intro" not in lib
    # Seuls les PDF de « Mes PDF » se suppriment
    assert all("delete" not in n for s in a["sections"] if s["id"] != "mes-pdf" for n in plat(s["items"]))
    secs["mes-fichiers"] = secs["mes-pdf"]

    # Les liens servent bien le PDF à Alice, en ligne
    r = c.get(secs["mes-fichiers"]["items"][0]["src"], headers=alice)
    assert r.content == PDF and r.headers["content-disposition"].startswith("inline")
    assert c.get(f"/lib/prepa/devoirs/{dv}/fichier", headers=alice).content == PDF

    # Zoé : rien d'Alice, ni Prépa ni devoirs, et les liens lui sont refusés
    z = c.get("/files/explorer", headers=zoe).json()
    zs = {s["id"]: s for s in z["sections"]}
    assert zs["mes-pdf"]["items"] == [] and zs["copies"]["items"] == []
    assert c.get(f"/files/{fid}/download?inline=1", headers=zoe).status_code == 403
    assert c.get(f"/lib/prepa/devoirs/{dv}/fichier", headers=zoe).status_code == 404
    assert c.delete(f"/files/{fid}", headers=zoe).status_code == 403
    assert c.get("/files/explorer").status_code == 401
    assert "nv-files" in c.get("/files/embed").text
