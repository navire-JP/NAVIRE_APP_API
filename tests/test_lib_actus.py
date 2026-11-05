"""
Passerelles NAVIRE Lib ↔ Actus contre une base SQLite : un cours propose les
actus récentes de sa matière (et rien si aucune ne colle), une actu propose
les cours à réviser. Lancer : pytest tests/test_lib_actus.py
"""

import os
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

TAG = "zz-test-lib-actus"


def _plan(*titres):
    return {"blocks": [], "plan": [{"id": f"b{i}", "level": 1, "text": t} for i, t in enumerate(titres)]}


@pytest.fixture(scope="module")
def monde():
    import app.main as main
    from app.db.database import Base, SessionLocal, engine
    from app.db.models import LibDocument, VeilleItem

    engine.dispose()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.query(LibDocument).filter(LibDocument.kicker == TAG).delete(synchronize_session=False)
    db.query(VeilleItem).filter(VeilleItem.external_id.like("test:libactus:%")).delete(synchronize_session=False)
    now = datetime.now(timezone.utc)

    def actu(n, title, category, essentiel, days=2):
        return VeilleItem(external_id=f"test:libactus:{n}", title=title, essentiel=essentiel, impact="Impact.",
                          category=category, source_url=f"https://exemple.fr/{n}", source_name="Dalloz",
                          published_at=now - timedelta(days=days), created_at=now - timedelta(days=days))

    actus = [
        actu(1, "Cautionnement : la mention manuscrite assouplie", "civil",
             "La Cour de cassation précise la sanction de la mention manuscrite du cautionnement souscrit par une caution personne physique."),
        actu(2, "Divorce par consentement mutuel : nouvelle procédure", "civil",
             "Le divorce sans juge évolue pour les époux."),
        actu(3, "Cautionnement disproportionné : sanction réaffirmée", "penal",
             "Une caution, un cautionnement disproportionné, la mention manuscrite."),   # mauvaise rubrique
        actu(4, "Hypothèque et cautionnement : réforme des sûretés, cinq ans après", "civil",
             "Le cautionnement et l'hypothèque depuis la réforme des sûretés ; la mention manuscrite.", days=300),  # trop ancienne
        actu(5, "Hypothèque rechargeable : retour et cautionnement réel", "civil",
             "L'hypothèque rechargeable revient ; le cautionnement réel est clarifié."),
    ]
    docs = [
        LibDocument(titre="Droit des sûretés", matiere="Droit des sûretés", niveau="L3", kicker=TAG, is_published=True,
                    content=_plan("Le cautionnement", "La mention manuscrite et la caution", "L'hypothèque rechargeable")),
        LibDocument(titre="Droit pénal général", matiere="Droit pénal", niveau="L2", kicker=TAG, is_published=True,
                    content=_plan("L'élément moral", "La tentative")),
        LibDocument(titre="Brouillon cautionnement", matiere="Droit des sûretés", niveau="L3", kicker=TAG,
                    is_published=False, content=_plan("Le cautionnement", "La mention manuscrite")),
    ]
    db.add_all(actus + docs)
    db.commit()
    ids = {"a": [a.id for a in actus], "suretes": docs[0].id, "penal": docs[1].id, "brouillon": docs[2].id}
    db.close()
    yield TestClient(main.app), ids
    db = SessionLocal()
    db.query(LibDocument).filter(LibDocument.kicker == TAG).delete(synchronize_session=False)
    db.query(VeilleItem).filter(VeilleItem.external_id.like("test:libactus:%")).delete(synchronize_session=False)
    db.commit()
    db.close()
    engine.dispose()


def test_rubrique_d_une_matiere():
    from app.services.lib_actus import rubriques_de

    assert rubriques_de("Droit des sûretés") == {"civil"}
    assert rubriques_de("Droit constitutionnel") == {"public"}
    assert rubriques_de("Droit pénal") == {"penal"}
    assert rubriques_de("Droit des sociétés") == {"affaires"}
    assert rubriques_de("Histoire des idées") == set()


def test_un_cours_propose_les_actus_de_sa_matiere(monde):
    c, ids = monde
    a = ids["a"]
    r = c.get(f"/lib/documents/{ids['suretes']}/actus")
    assert r.status_code == 200
    got = [it["id"] for it in r.json()["items"]]
    # Cautionnement (civil, récent) et hypothèque rechargeable ; pas le divorce,
    # pas l'actu pénale, pas celle de plus de 120 jours.
    assert set(got) == {a[0], a[4]} and got[0] == a[0]
    assert {"essentiel", "impact", "is_read", "category_label"} <= set(r.json()["items"][0])


def test_pas_d_actu_si_rien_ne_colle(monde):
    c, ids = monde
    assert c.get(f"/lib/documents/{ids['penal']}/actus").json()["items"] == []
    assert c.get(f"/lib/documents/{ids['brouillon']}/actus").status_code == 404


def test_une_actu_propose_les_cours_a_reviser(monde):
    c, ids = monde
    a = ids["a"]
    cours = c.get(f"/veille/actus/{a[0]}").json()["cours"]
    assert [x["id"] for x in cours] == [ids["suretes"]]          # pas le brouillon
    assert cours[0]["kind_label"] == "Cours" and cours[0]["plus"] is False
    assert c.get(f"/veille/actus/{a[1]}").json()["cours"] == []   # divorce : aucun cours
    assert c.get(f"/veille/actus/{a[2]}").json()["cours"] == []   # rubrique pénale ≠ sûretés
