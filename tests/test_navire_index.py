"""
NAVIRE AI plus intelligent : indexation des cours NAVIRE Lib (découpés par
titres), des actus et des articles, recherche par références exactes, et
nouvelles consignes. Embeddings simulés (aucun appel OpenAI).

Lancer : pytest tests/test_navire_index.py
"""

import os
from types import SimpleNamespace

import pytest

for k, v in {
    "OPENAI_API_KEY": "x", "BREVO_API_KEY_MEOLES": "x",
    "STRIPE_SECRET_KEY_MEOLES": "x", "STRIPE_WEBHOOK_SECRET_MEOLES": "x",
    "STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_WEBHOOK_SECRET": "whsec_x",
    "DATABASE_URL": "sqlite:///./test_navire_index.db",
}.items():
    os.environ.setdefault(k, v)

BLOCKS = [
    {"t": "h1", "text": "Partie 1 — La responsabilité délictuelle"},
    {"t": "h2", "text": "Chapitre 1 — Le fait personnel"},
    {"t": "p", "text": "Selon l'article 1240 du Code civil, tout fait quelconque de l'homme qui cause à autrui un dommage oblige celui par la faute duquel il est arrivé à le réparer."},
    {"t": "p", "text": "La faute s'apprécie in abstracto, par référence au comportement d'une personne raisonnable."},
    {"t": "h2", "text": "Chapitre 2 — Le fait des choses"},
    {"t": "p", "text": "L'arrêt Jand'heur pose une présomption de responsabilité du gardien de la chose."},
]


@pytest.fixture
def db(monkeypatch):
    from app.db.database import Base, engine, SessionLocal
    import app.services.navire_ingest as ni

    engine.dispose()
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(ni, "embed_texts", lambda texts: [[0.0] * 1536 for _ in texts])
    s = SessionLocal()
    yield s
    s.close()
    engine.dispose()
    path = engine.url.database
    if path and os.path.exists(path):
        os.remove(path)


def _doc(db, **kw):
    from app.db.models import LibDocument
    d = LibDocument(kind="cours", niveau="L2", matiere="Droit des obligations", titre="Responsabilité civile",
                    content={"blocks": BLOCKS}, is_published=True, **kw)
    db.add(d)
    db.commit()
    return d


def test_cours_decoupe_par_titres(db):
    from app.services.navire_ingest import lib_sections
    d = _doc(db)
    secs = lib_sections(d)
    assert [t for t, _ in secs] == [
        "Partie 1 — La responsabilité délictuelle › Chapitre 1 — Le fait personnel",
        "Partie 1 — La responsabilité délictuelle › Chapitre 2 — Le fait des choses",
    ]
    assert "1240" in secs[0][1] and "Jand'heur" in secs[1][1]


def test_indexation_lib_plus_et_depublication(db):
    from app.db.models import NavireChunk
    from app.services.navire_ingest import ingest_lib_document
    d = _doc(db, is_plus=True)
    assert ingest_lib_document(db, d) == 2
    chunks = db.query(NavireChunk).filter(NavireChunk.source_id == d.id).all()
    assert {c.source_type for c in chunks} == {"lib_plus"}
    assert chunks[0].content.startswith("Cours NAVIRE Lib : Responsabilité civile")
    assert "Section : Partie 1" in chunks[0].content
    d.is_published = False
    db.commit()
    assert ingest_lib_document(db, d) == 0
    assert db.query(NavireChunk).filter(NavireChunk.source_id == d.id).count() == 0


def test_perso_et_prepa_jamais_indexes(db):
    from app.services.navire_ingest import ingest_lib_document
    assert ingest_lib_document(db, _doc(db, owner_id=None) ) == 2
    from app.db.models import LibDocument
    p = LibDocument(kind="prepa", titre="Prépa", content={"blocks": BLOCKS}, is_published=True)
    db.add(p); db.commit()
    assert ingest_lib_document(db, p) == 0


def test_article_et_actu(db):
    from app.db.models import NavireChunk, User, VeilleItem
    from app.services.navire_ingest import backfill_missing
    u = User(email="idx-doc@test.fr", username="Julie")
    db.add(u); db.flush()
    art = VeilleItem(external_id="article:x", title="La faute lourde", essentiel="Extrait", impact="", category="civil",
                     source_url="https://navire-ai.com/actus/NA1", source_name="NAVIRE Articles", kind="article",
                     body_html="<p>" + "La faute lourde suppose une négligence d'une extrême gravité. " * 60 + "</p>", author_id=u.id)
    actu = VeilleItem(external_id="manual:y", title="Réforme", essentiel="Ce qui change", impact="Pour les étudiants",
                      category="general", source_url="https://x.fr", source_name="Dalloz")
    db.add_all([art, actu]); db.commit()
    _doc(db)
    r = backfill_missing(db)
    assert r["lib"] == 1 and r["article"] == 1 and r["actu"] == 1
    a = db.query(NavireChunk).filter(NavireChunk.source_type == "article").all()
    assert len(a) >= 2 and a[0].content.startswith("Article NAVIRE : La faute lourde (par Julie)")
    assert a[0].extra["url"].endswith("/actus/NA%d" % art.id)
    assert backfill_missing(db)["chunks"] == 0   # idempotent


def test_references_exactes():
    from app.services.navire_retrieval import exact_terms
    assert exact_terms("Que dit l'article 1240 du Code civil ?") == ["1240"]
    assert "21-12.345" in exact_terms("pourvoi n° 21-12.345")
    assert exact_terms("C'est quoi la faute ?") == []


def test_sources_et_consignes(db):
    from app.db.models import NavireChunk
    from app.services import navire_chat as nc
    assert "MAJOR" not in nc.SYSTEM_PROMPT and "NAVIRE AI" in nc.SYSTEM_PROMPT
    assert "connaissances générales" in nc.SYSTEM_PROMPT
    assert nc._source_types(True, False) == ["lib", "article", "actu", "cours"]
    assert "lib_plus" in nc._source_types(True, True, plus=True)
    c = NavireChunk(source_type="lib_plus", source_id=12, content="x", extra={"titre": "Obligations", "section": "Partie 1"})
    label, url = nc._label(c)
    assert label == "NAVIRE Lib — Obligations › Partie 1" and url.endswith("/lib?doc=12")


def test_filtre_de_pertinence():
    from app.services.navire_retrieval import keep_relevant
    rows = [("dintilhac", 0.31), ("article", 0.36), ("actu", 0.41), ("bail", 0.47), ("suretes", 0.66)]
    assert keep_relevant(rows, max_distance=0.62, margin=0.12) == ["dintilhac", "article", "actu"]
    # Rien d'assez proche : aucune source plutôt que du hors sujet
    assert keep_relevant([("x", 0.7), ("y", 0.72)], max_distance=0.62, margin=0.12) == []


def test_une_source_par_document(monkeypatch):
    """Dix extraits de trois documents → trois sources sous la réponse."""
    from app.services import navire_chat as nc

    extraits = [
        {"source": f"NAVIRE Lib — Sûretés › Titre {i}", "kind": "lib", "url": "u", "contenu": "x",
         "doc": "lib:1", "doc_label": "NAVIRE Lib — Sûretés"} for i in range(8)
    ] + [
        {"source": "Article NAVIRE — Dintilhac (par JVLIEN)", "kind": "article", "url": "a", "contenu": "y",
         "doc": "article:7", "doc_label": "Article NAVIRE — Dintilhac (par JVLIEN)"},
        {"source": "Votre document — rapport.pdf", "kind": "pdf_user", "url": None, "contenu": "z",
         "doc": "pdf_user:3", "doc_label": "Votre document — rapport.pdf"},
    ]
    monkeypatch.setattr(nc, "_run_tool", lambda *a, **k: {"found": True, "extraits": extraits})
    calls = iter([
        SimpleNamespace(content="", tool_calls=[SimpleNamespace(id="t1", function=SimpleNamespace(name="search_legal_base", arguments='{"query": "préjudice"}'))]),
        SimpleNamespace(content="Réponse.", tool_calls=None),
    ])
    monkeypatch.setattr(nc._client.chat.completions, "create",
                        lambda **p: SimpleNamespace(choices=[SimpleNamespace(message=next(calls))]))
    monkeypatch.setattr(nc, "get_or_create_conversation", lambda *a, **k: SimpleNamespace(id=1, messages=[], title=""))

    class FakeDB:
        def add(self, x): pass
        def commit(self): pass
        def refresh(self, x): pass
    monkeypatch.setattr(nc, "_enforce_conversation_retention", lambda *a: None)
    user = SimpleNamespace(id=1, username="eleve", study_level="L2", university=None)
    r = nc.send_message(FakeDB(), user, None, "Qu'est-ce qu'un préjudice extrapatrimonial ?")
    assert [s["label"] for s in r["sources"]] == [
        "NAVIRE Lib — Sûretés", "Article NAVIRE — Dintilhac (par JVLIEN)", "Votre document — rapport.pdf",
    ]
