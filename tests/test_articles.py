"""
Articles NAVIRE : publication réservée aux doctorants vérifiés et aux admins,
nettoyage du HTML, filtre dans Actus, enregistrement, profil.

Lancer : pytest tests/test_articles.py
"""

import os
from types import SimpleNamespace

import pytest

for k, v in {
    "OPENAI_API_KEY": "x", "BREVO_API_KEY_MEOLES": "x",
    "STRIPE_SECRET_KEY_MEOLES": "x", "STRIPE_WEBHOOK_SECRET_MEOLES": "x",
    "STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_WEBHOOK_SECRET": "whsec_x",
    "DATABASE_URL": "sqlite:///./test_articles.db",
}.items():
    os.environ.setdefault(k, v)

from fastapi.testclient import TestClient  # noqa: E402

BODY = (
    "<p>Le <b>Conseil d'État</b> précise <i>enfin</i> le régime 🎓 de la responsabilité "
    "sans faute.</p><script>alert(1)</script><p onclick=\"x()\">Deuxième paragraphe, "
    "<a href=\"javascript:alert(1)\">lien</a> et assez de texte pour passer le minimum.</p>"
)


@pytest.fixture(scope="module")
def env():
    import app.main as main
    from app.core.security import create_access_token
    from app.db.database import Base, engine, SessionLocal
    from app.db.models import StatusVerification, User, VeilleItem

    engine.dispose()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.query(VeilleItem).filter(VeilleItem.kind == "article").delete()

    def user(email, name, **kw):
        u = db.query(User).filter(User.email == email).one_or_none()
        if not u:
            u = User(email=email, username=name, **kw)
            db.add(u)
        return u

    doc = user("art-doc@test.fr", "doctorante")
    eleve = user("art-eleve@test.fr", "eleve")
    admin = user("art-admin@test.fr", "admin", is_admin=True)
    db.flush()
    if not db.query(StatusVerification).filter_by(user_id=doc.id, status="approved").first():
        db.add(StatusVerification(user_id=doc.id, label="Doctorant", badge="Doctorant", status="approved"))
    db.commit()
    tok = lambda u: {"Authorization": f"Bearer {create_access_token(str(u.id))}"}  # noqa: E731
    ns = SimpleNamespace(doc=doc.id, auth_doc=tok(doc), auth_eleve=tok(eleve), auth_admin=tok(admin))
    db.close()
    ns.c = TestClient(main.app)
    yield ns
    engine.dispose()
    db_path = engine.url.database if engine.url.get_backend_name() == "sqlite" else None
    if db_path and os.path.exists(db_path):
        os.remove(db_path)


def test_sanitize():
    from app.services.articles import sanitize_html
    out = sanitize_html(BODY)
    assert "<script" not in out and "alert" not in out and "onclick" not in out and "href" not in out
    assert "<strong>Conseil d'État</strong>" in out and "<em>enfin</em>" in out and "🎓" in out
    assert sanitize_html("<p>a<b>b</p>c") == "<p>a<strong>b</strong></p>c"


def test_publication_reservee(env):
    r = env.c.post("/veille/articles", headers=env.auth_eleve, json={"title": "Mon article", "body_html": BODY})
    assert r.status_code == 403
    me = env.c.get("/auth/me", headers=env.auth_doc).json()
    assert me["can_publish_articles"] is True
    assert env.c.get("/auth/me", headers=env.auth_eleve).json()["can_publish_articles"] is False


def test_cycle_complet(env):
    r = env.c.post("/veille/articles", headers=env.auth_doc, json={
        "title": "  La responsabilité   sans faute ", "category": "public", "body_html": BODY,
    })
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["kind"] == "article" and a["url"].endswith(f"/actus/NA{a['id']}")
    assert a["title"] == "La responsabilité sans faute"
    assert a["author"]["name"] == "doctorante" and a["author"]["role"] == "Doctorant"
    assert "<script" not in a["body_html"] and a["essentiel"].startswith("Le Conseil d'État")

    lst = env.c.get("/veille/actus", params={"kind": "article"}).json()
    assert [x["id"] for x in lst["items"]] == [a["id"]]
    assert env.c.get("/veille/actus", params={"kind": "nope"}).status_code == 400

    d = env.c.get(f"/veille/actus/{a['id']}", headers=env.auth_eleve).json()["item"]
    assert d["body_html"] and d["can_edit"] is False
    assert env.c.get(f"/veille/actus/{a['id']}", headers=env.auth_doc).json()["item"]["can_edit"] is True

    # Enregistrer
    assert env.c.put(f"/veille/actus/{a['id']}/save", headers=env.auth_eleve).json()["is_saved"] is True
    saved = env.c.get("/veille/actus", params={"status": "saved"}, headers=env.auth_eleve).json()
    assert saved["items"][0]["id"] == a["id"]

    # Modifier : l'élève non, l'autrice oui
    assert env.c.patch(f"/veille/articles/{a['id']}", headers=env.auth_eleve, json={"title": "Piraté !"}).status_code == 403
    r = env.c.patch(f"/veille/articles/{a['id']}", headers=env.auth_doc, json={"title": "Titre corrigé"})
    assert r.status_code == 200 and r.json()["updated_at"]

    # Profil : posts et nombre d'articles
    prof = env.c.get(f"/users/{env.doc}/profile").json()
    assert prof["user"]["is_doctorant"] is True and prof["user"]["articles_count"] == 1
    assert prof["posts"]["items"][0]["kind"] == "article"

    # Supprimer (admin)
    assert env.c.delete(f"/veille/articles/{a['id']}", headers=env.auth_admin).json()["ok"] is True
    assert env.c.get(f"/veille/actus/{a['id']}").status_code == 404


def test_article_trop_court(env):
    r = env.c.post("/veille/articles", headers=env.auth_doc, json={"title": "Court", "body_html": "<p>Trop court</p>"})
    assert r.status_code == 400
