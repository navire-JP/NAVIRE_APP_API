"""
Kitly — comptes, vitrine, offres, public, marketplace, juridique, Stripe.

Lancer : pytest tests/test_kitly.py
"""

import hashlib
import hmac
import json
import os
import time
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

PWD = "motdepasse1"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh) Safari/605"}


@pytest.fixture(scope="module")
def client():
    import app.main as main
    from app.db.database import Base, engine, SessionLocal
    from app.kitly.plans import seed_plans

    engine.dispose()
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_plans(db)
    yield TestClient(main.app)
    engine.dispose()


@pytest.fixture(autouse=True)
def _isolation(monkeypatch):
    """Pas d'email réel, code prévisible, journal de limitation vidé, cache populaire vidé."""
    from app.db.database import SessionLocal
    from app.kitly import codes, emails
    from app.kitly.models import KitlyRateEvent
    from app.kitly.queries import clear_popular_cache

    sent = []
    monkeypatch.setattr(emails, "send", lambda to, subject, html, reply_to=None: sent.append((to, subject)) or True)
    monkeypatch.setattr(codes.secrets, "randbelow", lambda n: 123456)
    with SessionLocal() as db:
        db.query(KitlyRateEvent).delete()
        db.commit()
    clear_popular_cache()
    yield sent


def _email(tag="u"):
    return f"{tag}-{uuid.uuid4().hex[:10]}@kitly-test.fr"


def register(client, email=None, verify=True, **extra):
    email = email or _email()
    body = {"first_name": "Léa", "last_name": "Martin", "email": email, "password": PWD,
            "password_confirm": PWD, "accept_cgu": True, "ack_privacy": True, **extra}
    r = client.post("/kitly/auth/register", json=body)
    assert r.status_code == 201, r.text
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    if verify:
        assert client.post("/kitly/auth/verify-email", json={"code": "123456"}, headers=h).status_code == 200
    return email, h


def set_plan(email, plan):
    from app.db.database import SessionLocal
    from app.kitly.models import KitlyUser

    with SessionLocal() as db:
        u = db.query(KitlyUser).filter_by(email=email).one()
        u.plan_code = plan
        db.commit()


def age_account(email, days=40):
    """Recule la date d'inscription (au-delà du mois de bienvenue marketplace)."""
    from datetime import timedelta
    from app.db.database import SessionLocal
    from app.kitly.models import KitlyUser
    from app.kitly.utils import now

    with SessionLocal() as db:
        db.query(KitlyUser).filter_by(email=email).one().created_at = now() - timedelta(days=days)
        db.commit()


def make_vitrine(client, h, slug=None, publish=True, **fields):
    slug = slug or f"lea-{uuid.uuid4().hex[:8]}"
    body = {"slug": slug, "display_name": "Léa Martin", "headline": "Designer graphique", "category": "design",
            "bio": "J'aide les marques à exister.", "location": "Lyon", **fields}
    r = client.post("/kitly/me/profile", json=body, headers=h)
    assert r.status_code == 201, r.text
    if publish:
        r = client.post("/kitly/me/profile/publish", headers=h)
        assert r.status_code == 200, r.text
    return slug


# ============================================================
# Comptes
# ============================================================

def test_register_login_me(client, _isolation):
    email, h = register(client, verify=False)
    assert any("vérification" in s.lower() for _, s in _isolation)
    me = client.get("/kitly/auth/me", headers=h).json()
    assert me["user"]["email_verified"] is False
    assert me["plan"]["code"] == "free"
    assert me["onboarding"][1]["key"] == "verify_email" and me["onboarding"][1]["done"] is False

    assert client.post("/kitly/auth/verify-email", json={"code": "000000"}, headers=h).status_code == 400
    assert client.post("/kitly/auth/verify-email", json={"code": "123456"}, headers=h).status_code == 200

    r = client.post("/kitly/auth/login", json={"email": email.upper(), "password": PWD})
    assert r.status_code == 200 and r.json()["user"]["email_verified"] is True


def test_register_validation(client):
    base = {"first_name": "A", "last_name": "B", "email": _email(), "password": PWD, "password_confirm": PWD,
            "accept_cgu": True, "ack_privacy": True}
    assert client.post("/kitly/auth/register", json={**base, "accept_cgu": False}).status_code == 422
    assert client.post("/kitly/auth/register", json={**base, "password_confirm": "autre1234"}).status_code == 422
    assert client.post("/kitly/auth/register", json={**base, "password": "court", "password_confirm": "court"}).status_code == 422
    assert client.post("/kitly/auth/register", json=base).status_code == 201
    assert client.post("/kitly/auth/register", json=base).status_code == 409


def test_login_bruteforce_lock(client):
    email, _ = register(client)
    for _ in range(5):
        assert client.post("/kitly/auth/login", json={"email": email, "password": "mauvais123"}).status_code == 401
    # Même le bon mot de passe est refusé pendant le verrouillage.
    assert client.post("/kitly/auth/login", json={"email": email, "password": PWD}).status_code == 429


def test_unknown_email_same_error(client):
    r = client.post("/kitly/auth/login", json={"email": _email(), "password": PWD})
    assert r.status_code == 401 and r.json()["detail"] == "Email ou mot de passe incorrect."


def test_password_reset_revokes_sessions(client):
    email, h = register(client)
    assert client.post("/kitly/auth/password/forgot", json={"email": email}).status_code == 200
    # Adresse inconnue : même réponse.
    assert client.post("/kitly/auth/password/forgot", json={"email": _email()}).json()["ok"] is True
    r = client.post("/kitly/auth/password/reset", json={"email": email, "code": "123456", "new_password": "nouveau123"})
    assert r.status_code == 200
    assert client.get("/kitly/auth/me", headers=h).status_code == 401  # ancienne session révoquée
    assert client.post("/kitly/auth/login", json={"email": email, "password": "nouveau123"}).status_code == 200


def test_change_password_and_logout_all(client):
    _, h = register(client)
    assert client.post("/kitly/account/password", json={"current_password": "faux1234", "new_password": "nouveau123"},
                       headers=h).status_code == 400
    r = client.post("/kitly/account/password", json={"current_password": PWD, "new_password": "nouveau123"}, headers=h)
    h2 = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.get("/kitly/auth/me", headers=h).status_code == 401
    assert client.get("/kitly/auth/me", headers=h2).status_code == 200
    r = client.post("/kitly/auth/logout-all", headers=h2)
    assert client.get("/kitly/auth/me", headers=h2).status_code == 401
    assert client.get("/kitly/auth/me", headers={"Authorization": f"Bearer {r.json()['token']}"}).status_code == 200


def test_tokens_isolated_from_navire(client):
    """Un jeton NAVIRE n'ouvre rien chez Kitly, et inversement — même avec le même identifiant numérique."""
    from app.core.security import create_access_token

    _, h = register(client)
    kitly_token = h["Authorization"].split()[1]
    me_id = client.get("/kitly/auth/me", headers=h).json()["user"]["id"]

    navire_token = create_access_token(str(me_id))
    assert client.get("/kitly/auth/me", headers={"Authorization": f"Bearer {navire_token}"}).status_code == 401
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {kitly_token}"}).status_code == 401


def test_delete_account(client):
    email, h = register(client)
    slug = make_vitrine(client, h)
    assert client.request("DELETE", "/kitly/account", json={"password": "faux1234", "confirm": "SUPPRIMER"},
                          headers=h).status_code == 400
    assert client.request("DELETE", "/kitly/account", json={"password": PWD, "confirm": "SUPPRIMER"},
                          headers=h).status_code == 200
    assert client.get(f"/kitly/public/profiles/{slug}").status_code == 404
    assert client.post("/kitly/auth/login", json={"email": email, "password": PWD}).status_code == 401


def test_export(client):
    _, h = register(client)
    make_vitrine(client, h)
    data = client.get("/kitly/account/export", headers=h).json()
    assert data["profile"]["slug"] and data["account"]["consents"]["cgu_version"]


# ============================================================
# Vitrine
# ============================================================

def test_slug_rules(client):
    _, h = register(client)
    assert client.get("/kitly/slugs/check", params={"slug": "admin"}, headers=h).json()["available"] is False
    assert client.get("/kitly/slugs/check", params={"slug": "-ab"}, headers=h).json()["available"] is False
    slug = make_vitrine(client, h, publish=False)
    _, h2 = register(client)
    r = client.get("/kitly/slugs/check", params={"slug": slug}, headers=h2).json()
    assert r["available"] is False and r["suggestion"] and r["suggestion"] != slug
    r = client.post("/kitly/me/profile", json={"slug": slug, "display_name": "X"}, headers=h2)
    assert r.status_code == 409


def test_publish_requires_verified_email(client):
    _, h = register(client, verify=False)
    make_vitrine(client, h, publish=False)
    r = client.post("/kitly/me/profile/publish", headers=h)
    assert r.status_code == 422
    assert "verify_email" in [m["key"] for m in r.json()["detail"]["missing"]]


def test_theme_and_plan_gates(client):
    email, h = register(client)
    make_vitrine(client, h, publish=False)
    # Free : templates basiques seulement, pas de couleurs personnalisées, pas de domaine.
    assert client.patch("/kitly/me/profile", json={"theme": {"template": "noir"}}, headers=h).status_code == 200
    assert client.patch("/kitly/me/profile", json={"theme": {"template": "bold"}}, headers=h).status_code == 403
    assert client.patch("/kitly/me/profile", json={"theme": {"colors": {"background": "#8B1E1E"}}},
                        headers=h).status_code == 403
    assert client.patch("/kitly/me/profile", json={"custom_domain": "www.lea.fr"}, headers=h).status_code == 403

    set_plan(email, "custom")
    domain = f"www.lea-{uuid.uuid4().hex[:8]}.fr"
    r = client.patch("/kitly/me/profile", json={"theme": {"template": "bold", "colors": {"background": "#8B1E1E"},
                                                          "button_shape": "pill"}, "custom_domain": domain},
                     headers=h)
    assert r.status_code == 200, r.text
    theme = r.json()["profile"]["theme"]
    assert theme["template"] == "bold" and theme["colors"]["background"] == "#8B1E1E"
    assert theme["colors"]["text"] == "#0a0a0a"  # complété par les valeurs par défaut
    assert client.get(f"/kitly/public/domains/{domain}").status_code == 404  # vitrine non publiée
    client.post("/kitly/me/profile/publish", headers=h)
    assert client.get(f"/kitly/public/domains/{domain}").json()["slug"]
    set_plan(email, "free")  # formule perdue : le domaine ne résout plus
    assert client.get(f"/kitly/public/domains/{domain}").status_code == 404
    set_plan(email, "custom")
    assert client.patch("/kitly/me/profile", json={"theme": {"colors": {"background": "rouge"}}},
                        headers=h).status_code == 422


def test_layout_site_or_links(client):
    """Mise en page : « site » (mini-site, par défaut) ou « links » (page de liens), dispo dès Free."""
    _, h = register(client)
    slug = make_vitrine(client, h)
    assert client.get(f"/kitly/public/profiles/{slug}").json()["theme"]["layout"] == "site"
    r = client.patch("/kitly/me/profile", json={"theme": {"layout": "links"}}, headers=h)
    assert r.status_code == 200 and r.json()["profile"]["theme"]["layout"] == "links"
    assert client.get(f"/kitly/public/profiles/{slug}").json()["theme"]["layout"] == "links"
    assert client.patch("/kitly/me/profile", json={"theme": {"layout": "magazine"}}, headers=h).status_code == 422
    assert "site" in client.get("/kitly/catalog").json()["layouts"]


def test_blocks_linktree(client):
    _, h = register(client)
    slug = make_vitrine(client, h)
    offer = client.post("/kitly/me/offers", json={"kind": "digital", "title": "Kit Branding", "price_cents": 4900,
                                                  "status": "published"}, headers=h).json()["item"]
    blocks = [
        {"type": "link", "label": "Fall reading list", "url": "lea.fr/lecture", "icon": "book", "hack": "x"},
        {"type": "video", "label": "On film", "url": "https://youtu.be/abc"},
        {"type": "offer", "offer_id": offer["id"]},
        {"type": "link", "label": "Caché", "url": "https://x.fr", "visible": False},
    ]
    r = client.patch("/kitly/me/profile", json={"blocks": blocks}, headers=h)
    assert r.status_code == 200, r.text
    saved = r.json()["profile"]["blocks"]
    assert saved[0]["url"] == "https://lea.fr/lecture" and "hack" not in saved[0] and saved[0]["id"]
    pub = client.get(f"/kitly/public/profiles/{slug}").json()
    assert [b["type"] for b in pub["blocks"]] == ["link", "video", "offer"]
    assert pub["blocks"][2]["offer"]["title"] == "Kit Branding"
    assert client.patch("/kitly/me/profile", json={"blocks": [{"type": "script", "url": "x"}]},
                        headers=h).status_code == 422


# ============================================================
# Offres
# ============================================================

def test_offers_by_kind(client):
    _, h = register(client)
    make_vitrine(client, h, publish=False)
    r = client.post("/kitly/me/offers", json={"kind": "physical", "title": "Bague", "price_cents": 8900,
                                              "details": {"stock": 3, "variants": [{"label": "T52", "stock": 0}],
                                                          "delivery_zones": ["fr_metro"]}}, headers=h)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["item"]["cta"] == "order" and body["item"]["needs_terms"] is True
    assert body["suggest_terms_questionnaire"] is True and body["questionnaire_scope"] == "physical"
    assert body["item"]["details"]["variants"][0]["id"]

    # Champ d'une autre catégorie refusé
    r = client.post("/kitly/me/offers", json={"kind": "service", "title": "Logo",
                                              "details": {"stock": 3}}, headers=h)
    assert r.status_code == 422
    # Service sans prix : sur devis par défaut
    r = client.post("/kitly/me/offers", json={"kind": "service", "title": "Identité visuelle"}, headers=h)
    assert r.json()["item"]["price_mode"] == "quote" and r.json()["item"]["price_label"] == "Sur devis"
    # Fourchette incohérente
    r = client.post("/kitly/me/offers", json={"kind": "service", "title": "Charte", "price_mode": "range",
                                              "price_cents": 50000, "price_max_cents": 10000}, headers=h)
    assert r.status_code == 422
    # Rendez-vous en Free : pas de réservation en ligne, repli sur contact
    r = client.post("/kitly/me/offers", json={"kind": "appointment", "title": "Coaching", "price_cents": 6000},
                    headers=h)
    assert r.json()["item"]["cta"] == "contact"
    r = client.post("/kitly/me/offers", json={"kind": "appointment", "title": "Coaching 2", "price_cents": 6000,
                                              "cta": "book"}, headers=h)
    assert r.status_code == 403


def test_offer_limit_and_ownership(client):
    _, h = register(client)
    make_vitrine(client, h, publish=False)
    ids = []
    for i in range(5):
        r = client.post("/kitly/me/offers", json={"kind": "digital", "title": f"E-book {i}", "price_cents": 900},
                        headers=h)
        assert r.status_code == 201
        ids.append(r.json()["item"]["id"])
    r = client.post("/kitly/me/offers", json={"kind": "digital", "title": "Trop", "price_cents": 900}, headers=h)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "plan_required"

    _, other = register(client)
    make_vitrine(client, other, publish=False)
    assert client.patch(f"/kitly/me/offers/{ids[0]}", json={"title": "Volé"}, headers=other).status_code == 404
    assert client.request("DELETE", f"/kitly/me/offers/{ids[0]}", headers=other).status_code == 404

    r = client.post("/kitly/me/offers/reorder", json={"ids": list(reversed(ids))}, headers=h)
    assert [o["id"] for o in r.json()["items"]] == list(reversed(ids))
    assert client.post("/kitly/me/offers/reorder", json={"ids": ids[:2]}, headers=h).status_code == 422


# ============================================================
# Vitrine publique
# ============================================================

def test_public_vitrine_hides_private_data(client):
    email, h = register(client)
    slug = make_vitrine(client, h)
    client.post("/kitly/me/offers", json={"kind": "physical", "title": "Brouillon", "price_cents": 100}, headers=h)
    client.post("/kitly/me/offers", json={"kind": "physical", "title": "Collier", "price_cents": 4500,
                                          "status": "published", "details": {"stock": 2}}, headers=h)
    pub = client.get(f"/kitly/public/profiles/{slug}").json()
    assert [o["title"] for o in pub["offers"]] == ["Collier"]
    assert pub["offers"][0]["details"]["in_stock"] is True and "stock" not in pub["offers"][0]["details"]
    assert pub["show_kitly_branding"] is True
    raw = json.dumps(pub)
    assert "password" not in raw and "stripe" not in raw.lower() and email not in raw.replace(f"\"email\": \"{email}\"", "")

    client.post("/kitly/me/profile/unpublish", headers=h)
    assert client.get(f"/kitly/public/profiles/{slug}").status_code == 404


def test_views_unique_per_day_and_owner_excluded(client):
    _, h = register(client)
    slug = make_vitrine(client, h)
    other_ua = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0) Firefox/130"}
    assert client.post(f"/kitly/public/profiles/{slug}/view", json={}, headers=UA).json()["counted"] is True
    assert client.post(f"/kitly/public/profiles/{slug}/view", json={}, headers=UA).json()["counted"] is False
    assert client.post(f"/kitly/public/profiles/{slug}/view", json={"source": "marketplace"},
                       headers=other_ua).json()["counted"] is True
    assert client.post(f"/kitly/public/profiles/{slug}/view", json={},
                       headers={"User-Agent": "Googlebot/2.1"}).json()["counted"] is False
    assert client.post(f"/kitly/public/profiles/{slug}/view", json={}, headers={**h, **UA}).json()["counted"] is False
    stats = client.get("/kitly/me/stats", headers=h).json()
    assert stats["visits"] == 2 and stats["series"] is None and "series" in stats["locked"]


def test_lead_flow(client, _isolation):
    _, h = register(client)
    slug = make_vitrine(client, h)
    body = {"kind": "quote", "name": "Client", "email": "client@exemple.fr", "message": "Un logo ?", "consent": True}
    assert client.post(f"/kitly/public/profiles/{slug}/leads", json={**body, "consent": False}).status_code == 422
    assert client.post(f"/kitly/public/profiles/{slug}/leads", json=body).status_code == 201
    assert any("devis" in s for _, s in _isolation)
    # Pot de miel : réponse identique, rien d'enregistré
    assert client.post(f"/kitly/public/profiles/{slug}/leads", json={**body, "website": "spam.ru"}).status_code == 201
    leads = client.get("/kitly/me/leads", headers=h).json()
    assert leads["total"] == 1 and leads["unread"] == 1
    lid = leads["items"][0]["id"]
    assert client.patch(f"/kitly/me/leads/{lid}", json={"status": "replied"}, headers=h).json()["item"]["status"] == "replied"
    # Limite par profil et par IP : 3 demandes par heure (le pot de miel ne compte pas)
    client.post(f"/kitly/public/profiles/{slug}/leads", json=body)
    client.post(f"/kitly/public/profiles/{slug}/leads", json=body)
    assert client.post(f"/kitly/public/profiles/{slug}/leads", json=body).status_code == 429


def test_report(client):
    _, h = register(client)
    slug = make_vitrine(client, h)
    assert client.post(f"/kitly/public/profiles/{slug}/report", json={"reason": "fraud"}).status_code == 201
    assert client.post(f"/kitly/public/profiles/{slug}/report", json={"reason": "nope"}).status_code == 422


# ============================================================
# Marketplace
# ============================================================

def test_marketplace_eligibility_new_popular_search(client):
    tag = uuid.uuid4().hex[:6]
    free_email, free_h = register(client)
    make_vitrine(client, free_h, headline=f"Développeuse {tag}")
    age_account(free_email)  # passé le mois de bienvenue

    prem_email, prem_h = register(client)
    set_plan(prem_email, "premium")
    slug_a = make_vitrine(client, prem_h, headline=f"Développeur web {tag}", location="Paris")
    client.post("/kitly/me/offers", json={"kind": "service", "title": "Site vitrine", "price_cents": 90000,
                                          "status": "published"}, headers=prem_h)

    p2_email, p2_h = register(client)
    set_plan(p2_email, "premium")
    slug_b = make_vitrine(client, p2_h, headline=f"Photographe {tag}", location="Lyon")
    client.post("/kitly/me/offers", json={"kind": "service", "title": "Shooting développement produit",
                                          "price_cents": 30000, "status": "published"}, headers=p2_h)

    # Recherche sans accent ni majuscule ; le profil Free n'apparaît pas.
    r = client.get("/kitly/marketplace/search", params={"q": f"developpeur {tag}"}).json()
    assert [c["slug"] for c in r["items"]] == [slug_a]
    assert r["items"][0]["badge"] == "Premium+" and r["items"][0]["top_offers"][0]["title"] == "Site vitrine"
    assert "banner_url" in r["items"][0] and "email" not in r["items"][0]

    r = client.get("/kitly/marketplace/search", params={"q": tag, "sort": "price_asc"}).json()
    assert [c["slug"] for c in r["items"]] == [slug_b, slug_a]
    r = client.get("/kitly/marketplace/search", params={"q": tag, "budget_max_cents": 50000}).json()
    assert [c["slug"] for c in r["items"]] == [slug_b]
    r = client.get("/kitly/marketplace/search", params={"q": tag, "location": "paris"}).json()
    assert [c["slug"] for c in r["items"]] == [slug_a]

    new = [c["slug"] for c in client.get("/kitly/marketplace/new", params={"limit": 48}).json()["items"]]
    assert slug_a in new and slug_b in new and all(c["is_new"] for c in client.get("/kitly/marketplace/new").json()["items"])

    # Popularité : visites uniques (B : 2 visiteurs, A : 1 visiteur venu 3 fois)
    for _ in range(3):
        client.post(f"/kitly/public/profiles/{slug_a}/view", json={}, headers=UA)
    client.post(f"/kitly/public/profiles/{slug_b}/view", json={}, headers=UA)
    client.post(f"/kitly/public/profiles/{slug_b}/view", json={}, headers={"User-Agent": "Mozilla/5.0 Firefox/130"})
    r = client.get("/kitly/marketplace/search", params={"q": tag, "sort": "popular"}).json()
    assert [c["slug"] for c in r["items"]] == [slug_b, slug_a]

    # Mise en avant payante : à part, jamais dans le classement organique
    assert client.patch("/kitly/me/profile", json={"promoted": True}, headers=free_h).status_code == 403
    client.patch("/kitly/me/profile", json={"promoted": True}, headers=prem_h)
    r = client.get("/kitly/marketplace/search", params={"q": tag, "sort": "popular"}).json()
    assert [c["slug"] for c in r["sponsored"]] == [slug_a] and r["sponsored"][0]["sponsored"] is True
    assert [c["slug"] for c in r["items"]] == [slug_b, slug_a]

    # Le freelance peut se retirer de la marketplace
    client.patch("/kitly/me/profile", json={"marketplace_opt_in": False}, headers=p2_h)
    r = client.get("/kitly/marketplace/search", params={"q": tag}).json()
    assert slug_b not in [c["slug"] for c in r["items"]]


def test_marketplace_offers(client):
    tag = uuid.uuid4().hex[:6]
    prem_email, h = register(client)
    set_plan(prem_email, "premium")
    slug = make_vitrine(client, h, headline=f"Boulangère {tag}")
    for title, price in ((f"Pain au levain {tag}", 800), (f"Atelier pâtisserie {tag}", 4500)):
        client.post("/kitly/me/offers", json={"kind": "service", "title": title, "price_cents": price,
                                              "status": "published"}, headers=h)
    client.post("/kitly/me/offers", json={"kind": "service", "title": f"Brouillon {tag}", "price_cents": 100}, headers=h)
    free_email, fh = register(client)
    make_vitrine(client, fh)
    age_account(free_email)  # hors marketplace
    client.post("/kitly/me/offers", json={"kind": "service", "title": f"Caché {tag}", "price_cents": 100,
                                          "status": "published"}, headers=fh)

    r = client.get("/kitly/marketplace/offers", params={"q": f"levain {tag}"}).json()
    assert [o["title"] for o in r["items"]] == [f"Pain au levain {tag}"] and r["total"] == 1
    card = r["items"][0]
    assert card["price_label"] == "8 €" and card["profile"]["slug"] == slug and "email" not in card["profile"]
    # Vendeur sans paiements en ligne : pas d'ajout au panier depuis la carte.
    assert card["buyable"] is False and card["needs_options"] is False
    # La recherche porte aussi sur la vitrine ; brouillons et vitrines hors marketplace exclus.
    r = client.get("/kitly/marketplace/offers", params={"q": tag, "sort": "price_asc"}).json()
    assert [o["price_label"] for o in r["items"]] == ["8 €", "45 €"]
    assert client.get("/kitly/marketplace/offers", params={"q": tag, "kind": "physical"}).json()["total"] == 0


# ============================================================
# Juridique
# ============================================================

def test_legal_answers_guardrails(client):
    _, h = register(client)
    make_vitrine(client, h, publish=False)
    q = client.get("/kitly/legal/questionnaire").json()
    assert set(q["scopes"]) == {"general", "physical", "digital", "service", "appointment"}

    r = client.put("/kitly/me/legal-answers/physical", json={"answers": {"returns_accepted": False}}, headers=h)
    assert r.status_code == 422 and "returns_accepted" in r.json()["detail"]["errors"]
    r = client.put("/kitly/me/legal-answers/physical", json={"answers": {"retraction_days": 7}}, headers=h)
    assert "retraction_days" in r.json()["detail"]["errors"]
    r = client.put("/kitly/me/legal-answers/physical", json={"answers": {"lost_parcel_risk": "buyer"}}, headers=h)
    assert r.status_code == 422
    # Produits sur mesure : l'exception légale autorise le refus des retours
    r = client.put("/kitly/me/legal-answers/physical",
                   json={"answers": {"custom_made_only": True, "returns_accepted": False}}, headers=h)
    assert r.status_code == 200, r.text
    # Vente entre professionnels uniquement : pas de droit de rétractation imposé
    _, h2 = register(client)
    make_vitrine(client, h2, publish=False)
    client.put("/kitly/me/legal-answers/general", json={"answers": {"sells_to_consumers": False}}, headers=h2)
    assert client.put("/kitly/me/legal-answers/physical", json={"answers": {"returns_accepted": False}},
                      headers=h2).status_code == 200
    # Numérique : renonciation sans accord exprès refusée
    r = client.put("/kitly/me/legal-answers/digital",
                   json={"answers": {"waive_withdrawal": True, "require_express_consent": False}}, headers=h)
    assert r.status_code == 422
    r = client.put("/kitly/me/legal-answers/digital", json={"answers": {"waive_withdrawal": True}}, headers=h)
    assert r.json()["answers"]["require_waiver_ack"] is True


def test_terms_versioning(client):
    _, h = register(client)
    slug = make_vitrine(client, h)
    offer = client.post("/kitly/me/offers", json={"kind": "digital", "title": "Template", "price_cents": 1900},
                        headers=h).json()["item"]
    assert offer["needs_terms"] is True
    assert client.get(f"/kitly/public/profiles/{slug}/terms").status_code == 404

    v1 = [{"key": "objet", "title": "Article 1 — Objet", "body": "Vente de templates."}]
    d = client.put("/kitly/me/terms/draft", json={"articles": v1}, headers=h).json()["draft"]
    assert d["number"] == 1 and d["status"] == "draft" and len(d["content_hash"]) == 64
    assert client.post("/kitly/me/terms/draft/publish", json={"accept": False}, headers=h).status_code == 422
    cur = client.post("/kitly/me/terms/draft/publish", json={"accept": True}, headers=h).json()["current"]
    assert cur["status"] == "published" and "modèle" in cur["disclaimer"]

    v2 = [{"key": "objet", "title": "Article 1 — Objet", "body": "Vente de templates Notion."}]
    client.put("/kitly/me/terms/draft", json={"articles": v2}, headers=h)
    client.post("/kitly/me/terms/draft/publish", json={"accept": True}, headers=h)

    t = client.get("/kitly/me/terms", headers=h).json()
    assert t["current"]["number"] == 2 and [v["status"] for v in t["versions"]] == ["published", "superseded"]
    pub = client.get(f"/kitly/public/profiles/{slug}/terms").json()
    assert pub["terms"]["articles"][0]["body"] == "Vente de templates Notion."
    old = client.get(f"/kitly/public/profiles/{slug}/terms", params={"version": 1}).json()
    assert old["terms"]["articles"][0]["body"] == "Vente de templates."
    assert client.get(f"/kitly/me/offers/{offer['id']}", headers=h).json()["item"]["needs_terms"] is False
    assert client.get(f"/kitly/public/profiles/{slug}").json()["legal"]["has_terms"] is True


def test_legal_profile_footer(client):
    _, h = register(client)
    slug = make_vitrine(client, h)
    assert client.put("/kitly/me/legal-profile", json={"siren": "12345"}, headers=h).status_code == 422
    r = client.put("/kitly/me/legal-profile", json={"legal_name": "Léa Martin EI", "siren": "123 456 789",
                                                    "city": "Lyon", "address_line": "1 rue X", "postal_code": "69001"},
                   headers=h)
    assert r.json()["legal_profile"]["siren"] == "123456789"
    seller = client.get(f"/kitly/public/profiles/{slug}").json()["legal"]["seller"]
    assert seller["legal_name"] == "Léa Martin EI" and "69001 Lyon" in seller["address"]


# ============================================================
# Stripe (aucun appel réseau : client simulé)
# ============================================================

class _FakeSubs:
    def __init__(self, store):
        self.store = store

    def retrieve(self, sub_id):
        return self.store[sub_id]

    def update(self, sub_id, params=None):
        sub = self.store[sub_id]
        if "cancel_at_period_end" in (params or {}):
            sub["cancel_at_period_end"] = params["cancel_at_period_end"]
        if params and "items" in params:
            sub["items"]["data"][0]["price"]["id"] = params["items"][0]["price"]
        return sub


class _FakeStripe:
    def __init__(self):
        self.subs = {}
        self.subscriptions = _FakeSubs(self.subs)
        outer = self

        class _Customers:
            def create(self, params=None):
                return {"id": "cus_test"}

        class _Sessions:
            def create(self, params=None):
                outer.last_checkout = params
                return {"url": "https://checkout.stripe.test/s"}

        class _Checkout:
            sessions = _Sessions()

        self.customers = _Customers()
        self.checkout = _Checkout()


def _sub(sub_id, user_id, price, status="active"):
    return {"id": sub_id, "customer": "cus_test", "status": status, "cancel_at_period_end": False,
            "metadata": {"app": "kitly", "kitly_user_id": str(user_id)},
            "items": {"data": [{"id": "si_1", "price": {"id": price}, "current_period_end": int(time.time()) + 86400 * 30}]}}


def _signed(payload: dict, secret: str) -> tuple[bytes, dict]:
    body = json.dumps(payload).encode()
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.{body.decode()}".encode(), hashlib.sha256).hexdigest()
    return body, {"stripe-signature": f"t={ts},v1={sig}", "content-type": "application/json"}


def test_billing_checkout_and_webhook(client, monkeypatch):
    from app.kitly import config, plans
    from app.kitly.routers import billing

    fake = _FakeStripe()
    monkeypatch.setattr(billing, "stripe_client", lambda: fake)
    monkeypatch.setitem(config.KITLY_STRIPE_PRICES, "custom", "price_kitly_custom")
    monkeypatch.setitem(config.KITLY_STRIPE_PRICES, "premium", "price_kitly_premium")
    monkeypatch.setattr(plans, "KITLY_STRIPE_PRICES", config.KITLY_STRIPE_PRICES)
    monkeypatch.setattr(billing, "KITLY_STRIPE_WEBHOOK_SECRET", "whsec_kitly_test")

    email, h = register(client)
    uid = client.get("/kitly/auth/me", headers=h).json()["user"]["id"]

    r = client.post("/kitly/billing/checkout", json={"plan": "custom"}, headers=h).json()
    assert r["checkout_url"].startswith("https://checkout.stripe.test")
    assert fake.last_checkout["metadata"]["app"] == "kitly"
    assert fake.last_checkout["line_items"][0]["price"] == "price_kitly_custom"
    # Le retour navigateur ne change rien : toujours Free tant que Stripe n'a pas confirmé.
    assert client.get("/kitly/auth/me", headers=h).json()["plan"]["code"] == "free"

    sub_id = f"sub_{uuid.uuid4().hex[:10]}"
    sub = _sub(sub_id, uid, "price_kitly_custom")
    fake.subs[sub_id] = sub
    event = {"id": f"evt_{uuid.uuid4().hex}", "type": "customer.subscription.created", "data": {"object": sub}}

    body, headers = _signed(event, "whsec_mauvais")
    assert client.post("/kitly/stripe/webhook", content=body, headers=headers).status_code == 400
    body, headers = _signed(event, "whsec_kitly_test")
    assert client.post("/kitly/stripe/webhook", content=body, headers=headers).status_code == 200
    assert client.post("/kitly/stripe/webhook", content=body, headers=headers).json()["duplicate"] is True
    assert client.get("/kitly/auth/me", headers=h).json()["plan"]["code"] == "custom"

    # Montée en gamme : modification sur place, pas de nouveau checkout
    r = client.post("/kitly/billing/checkout", json={"plan": "premium"}, headers=h).json()
    assert r["changed"] is True and r["plan"]["code"] == "premium"

    # Résiliation fin de période, puis suppression côté Stripe → Free
    r = client.post("/kitly/billing/cancel", headers=h).json()
    assert r["subscription"]["cancel_at_period_end"] is True and r["subscription"]["ends_at"]
    sub["status"] = "canceled"
    event = {"id": f"evt_{uuid.uuid4().hex}", "type": "customer.subscription.deleted", "data": {"object": sub}}
    body, headers = _signed(event, "whsec_kitly_test")
    assert client.post("/kitly/stripe/webhook", content=body, headers=headers).status_code == 200
    assert client.get("/kitly/auth/me", headers=h).json()["plan"]["code"] == "free"


def test_connect_status_from_stripe_only(client):
    from app.db.database import SessionLocal
    from app.kitly.models import KitlyConnectedAccount, KitlyUser
    from app.kitly.routers.billing import handle_connect_event

    email, h = register(client)
    assert client.get("/kitly/connect/status", headers=h).json()["status"] == "not_started"
    with SessionLocal() as db:
        u = db.query(KitlyUser).filter_by(email=email).one()
        db.add(KitlyConnectedAccount(user_id=u.id, stripe_account_id=f"acct_{uuid.uuid4().hex[:10]}"))
        db.commit()
        acct = u.connected_account.stripe_account_id
    assert client.get("/kitly/connect/status", headers=h).json()["status"] == "onboarding_incomplete"

    def push(**account):
        with SessionLocal() as db:
            handle_connect_event(db, {"type": "account.updated", "data": {"object": {"id": acct, **account}}})
            db.commit()

    push(details_submitted=True, charges_enabled=False, payouts_enabled=False,
         requirements={"currently_due": [], "disabled_reason": "requirements.pending_verification"})
    assert client.get("/kitly/connect/status", headers=h).json()["status"] == "pending_verification"
    push(details_submitted=True, charges_enabled=False, payouts_enabled=False,
         requirements={"currently_due": ["individual.verification.document"]})
    assert client.get("/kitly/connect/status", headers=h).json()["status"] == "action_required"
    push(details_submitted=True, charges_enabled=True, payouts_enabled=True, requirements={"currently_due": []})
    assert client.get("/kitly/connect/status", headers=h).json()["status"] == "active"


def test_kitly_never_touches_global_stripe_key():
    """Le client Kitly utilise sa propre clé, sans lire ni écrire stripe.api_key (NAVIRE / MEOLES)."""
    import stripe
    from app.kitly import config, stripe_client as sc

    before = stripe.api_key
    original = config.KITLY_STRIPE_SECRET_KEY
    try:
        sc.KITLY_STRIPE_SECRET_KEY = "sk_test_kitly_only"
        c = sc.stripe_client()
        assert c._requestor.api_key == "sk_test_kitly_only"
        assert stripe.api_key == before
    finally:
        sc.KITLY_STRIPE_SECRET_KEY = original
        sc._client = None


# ============================================================
# Admin
# ============================================================

def test_admin_plans_configurable(client):
    _, h0 = register(client)
    assert client.get("/kitly/admin/plans", headers=h0).status_code == 403
    _, h = _make_admin(client)
    r = client.patch("/kitly/admin/plans/free", json={"limits": {"offers_max": 7}}, headers=h)
    assert r.status_code == 200 and r.json()["plan"]["limits"]["offers_max"] == 7
    assert r.json()["plan"]["limits"]["portfolio_max"] == 6  # les autres limites sont conservées
    assert client.patch("/kitly/admin/plans/free", json={"limits": {"inconnue": 1}}, headers=h).status_code == 422
    client.patch("/kitly/admin/plans/free", json={"limits": {"offers_max": 5}}, headers=h)


def test_catalog_and_plans_public(client):
    assert {c["key"] for c in client.get("/kitly/catalog").json()["offer_kinds"]} == {
        "physical", "digital", "service", "appointment"}
    plans = client.get("/kitly/plans").json()["items"]
    assert [p["code"] for p in plans] == ["free", "custom", "premium"]
    assert plans[2]["price_label"] == "14,90 € / mois"


# ============================================================
# Pages servies par l'API
# ============================================================

def test_static_vitrine_js(client):
    r = client.get("/kitly/static/vitrine.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    assert "KitlyVitrine" in r.text
    r = client.get("/kitly/static/marketplace.js")
    assert r.status_code == 200 and "KitlyMarket" in r.text
    assert client.get("/kitly/static/../config.py").status_code == 404
    assert client.get("/kitly/static/config.py").status_code == 404
    logo = client.get("/kitly/static/logo.png")
    assert logo.status_code == 200 and logo.headers["content-type"] == "image/png"
    hero = client.get("/kitly/static/hero-home.webp")
    assert hero.status_code == 200 and hero.headers["content-type"] == "image/webp"


def test_vitrine_page_meta_and_escaping(client):
    email, h = register(client)
    evil = '</script><script>alert(1)</script>'
    slug = make_vitrine(client, h, display_name=f'Léa "Studio" {evil}'[:80], bio="Design & <b>identité</b>")
    r = client.get(f"/kitly/p/{slug}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    page = r.text
    assert '<meta property="og:title" content="Léa &quot;Studio&quot; &lt;/script&gt;' in page
    # Adresse publique par défaut /profile#<adresse> : ni canonical ni og:url (le hash n'est pas une page à part).
    assert 'rel="canonical"' not in page and 'property="og:url"' not in page
    assert '<script src="/kitly/static/vitrine.js"></script>' in page
    # Le nom piégé n'ouvre jamais de balise : ni dans les <meta>, ni dans le JSON embarqué.
    assert page.count("<script>alert(1)") == 0
    assert "\\u003c/script\\u003e" in page
    assert 'content="#ffffff"' in page  # theme-color du template Blanc

    set_plan(email, "custom")
    client.patch("/kitly/me/profile", json={"theme": {"template": "bold", "colors": {"background": "#123456"}}}, headers=h)
    assert 'name="theme-color" content="#123456"' in client.get(f"/kitly/p/{slug}").text

    client.post("/kitly/me/profile/unpublish", headers=h)
    r = client.get(f"/kitly/p/{slug}")
    assert r.status_code == 404 and 'noindex' in r.text
    assert client.get("/kitly/p/inconnu-total-xyz").status_code == 404


# ============================================================
# Retours de revue (Codex, PR #67)
# ============================================================

def test_rate_keys_never_store_ip_or_email(client):
    from app.db.database import SessionLocal
    from app.kitly.models import KitlyRateEvent

    email, _ = register(client)
    client.post("/kitly/auth/login", json={"email": email, "password": "mauvais123"})
    with SessionLocal() as db:
        keys = [k for (k,) in db.query(KitlyRateEvent.key).all()]
    assert keys and all(email not in k and "testclient" not in k for k in keys)
    assert {k.split(":")[0] for k in keys} >= {"register", "login"}


def test_honeypot_does_not_consume_lead_quota(client):
    _, h = register(client)
    slug = make_vitrine(client, h)
    body = {"kind": "contact", "name": "Robot", "email": "bot@exemple.fr", "message": "spam", "consent": True,
            "website": "spam.ru"}
    for _ in range(5):
        assert client.post(f"/kitly/public/profiles/{slug}/leads", json=body).status_code == 201
    real = {**body, "name": "Camille", "website": ""}
    assert client.post(f"/kitly/public/profiles/{slug}/leads", json=real).status_code == 201
    assert client.get("/kitly/me/leads", headers=h).json()["total"] == 1


def test_oversized_upload_rejected_without_full_read(client, monkeypatch):
    from app.kitly import media

    _, h = register(client)
    monkeypatch.setattr(media, "KITLY_MAX_IMAGE_BYTES", 1024)
    called = []
    monkeypatch.setattr("app.kitly.routers.vitrine.upload_image", lambda *a: called.append(1) or "x")
    r = client.post("/kitly/me/media", headers=h, data={"purpose": "avatar"},
                    files={"file": ("big.png", b"0" * 5000, "image/png")})
    assert r.status_code == 413 and not called
    r = client.post("/kitly/me/media", headers=h, data={"purpose": "avatar"},
                    files={"file": ("doc.pdf", b"0" * 10, "application/pdf")})
    assert r.status_code == 415
    r = client.post("/kitly/me/media", headers=h, data={"purpose": "avatar"},
                    files={"file": ("ok.png", b"0" * 500, "image/png")})
    assert r.status_code == 200 and called


def test_export_includes_legal_records(client):
    _, h = register(client)
    make_vitrine(client, h)
    client.put("/kitly/me/legal-profile", json={"legal_name": "Léa Martin EI", "siren": "123456789"}, headers=h)
    client.put("/kitly/me/legal-answers/general", json={"answers": {"sells_to_consumers": True}}, headers=h)
    client.put("/kitly/me/terms/draft", json={"articles": [{"key": "objet", "title": "Objet", "body": "Texte."}]}, headers=h)
    client.post("/kitly/me/terms/draft/publish", json={"accept": True}, headers=h)
    legal = client.get("/kitly/account/export", headers=h).json()["legal"]
    assert legal["legal_profile"]["legal_name"] == "Léa Martin EI"
    assert legal["questionnaire_answers"]["general"]["answers"]["sells_to_consumers"] is True
    assert legal["terms_versions"][0]["articles"][0]["body"] == "Texte."


def test_cors_split_between_navire_and_kitly(client):
    from app.core.config import CORS_ORIGINS

    pre = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type,authorization"}
    kitly_origin = "https://kitly.framer.website"
    r = client.options("/kitly/auth/login", headers={"Origin": kitly_origin, **pre})
    assert r.status_code == 200 and r.headers.get("access-control-allow-origin") == kitly_origin
    assert "access-control-allow-credentials" not in r.headers
    # Le site Kitly n'est pas autorisé sur les routes NAVIRE…
    r = client.options("/auth/login", headers={"Origin": kitly_origin, **pre})
    assert r.headers.get("access-control-allow-origin") != kitly_origin
    # … ni une origine inconnue sur Kitly.
    r = client.options("/kitly/auth/login", headers={"Origin": "https://evil.example", **pre})
    assert r.headers.get("access-control-allow-origin") is None
    # Une origine NAVIRE n'ouvre pas les routes Kitly.
    for origin in CORS_ORIGINS:
        if origin not in ("*",):
            r = client.options("/kitly/auth/login", headers={"Origin": origin, **pre})
            assert r.headers.get("access-control-allow-origin") != origin or origin in (
                "https://kitly.fr", "https://www.kitly.fr", "https://kitly.framer.website")



def test_stripe_errors_become_readable_502(client, monkeypatch):
    import stripe
    from app.kitly import config
    from app.kitly.routers import billing

    class Boom:
        class billing_portal:
            class sessions:
                @staticmethod
                def create(params=None):
                    raise stripe.InvalidRequestError("No configuration provided", param=None)

    email, h = register(client)
    from app.db.database import SessionLocal
    from app.kitly.models import KitlyUser
    with SessionLocal() as db:
        db.query(KitlyUser).filter_by(email=email).one().stripe_customer_id = "cus_x"
        db.commit()
    monkeypatch.setattr(billing, "stripe_client", lambda: Boom)
    r = client.post("/kitly/billing/portal", headers=h)
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "stripe_error" and "portail" in r.json()["detail"]["message"]


def test_stripe_returns_point_to_subscribe_page():
    from app.kitly import config

    for url in (config.KITLY_BILLING_SUCCESS_URL, config.KITLY_BILLING_CANCEL_URL, config.KITLY_BILLING_RETURN_URL,
                config.KITLY_CONNECT_RETURN_URL, config.KITLY_CONNECT_REFRESH_URL):
        assert "/subscribe" in url



def test_cors_origins_are_normalized():
    from app.kitly.config import _origins

    assert _origins(" https://Kitly.framer.website/ , https://kitly.fr/page,'https://www.kitly.fr' ,https://kitly.fr") == [
        "https://kitly.framer.website", "https://kitly.fr", "https://www.kitly.fr"]


def test_health_exposes_config_diagnostic_without_secrets(client):
    r = client.get("/kitly/health").json()
    assert r["ok"] and isinstance(r["cors_origins"], list) and isinstance(r["missing_env"], list)
    assert not any(k in str(r).lower() for k in ("sk_", "whsec", "secret_key="))


def test_sender_diagnostic(monkeypatch):
    from app.kitly import emails

    class R:
        def __init__(self, data, code=200):
            self.status_code, self._d = code, data

        def json(self):
            return self._d

    def fake_get(senders, domains):
        return lambda url, headers=None, timeout=None: R({"domains": domains}) if url.endswith("/domains") else R({"senders": senders})

    monkeypatch.setattr(emails, "KITLY_BREVO_API_KEY", "xkeysib-test")
    monkeypatch.setattr(emails, "KITLY_SENDER_EMAIL", "no-reply@kitly.fr")
    # Domaine authentifié : toute adresse du domaine est acceptée.
    monkeypatch.setattr(emails.httpx, "get", fake_get([], [{"domain_name": "kitly.fr", "authenticated": True}]))
    assert emails.check_sender()["sender_ok"] is True
    # Expéditeur validé individuellement.
    monkeypatch.setattr(emails.httpx, "get", fake_get([{"email": "No-Reply@kitly.fr", "active": True}], []))
    assert emails.check_sender()["sender_ok"] is True
    # Ni l'un ni l'autre : diagnostic explicite et liste des adresses utilisables (logs uniquement).
    monkeypatch.setattr(emails.httpx, "get", fake_get([{"email": "no-reply@navire-ai.com", "active": True}], []))
    st = emails.check_sender()
    assert st["sender_ok"] is False and "no-reply@navire-ai.com" in st["usable"]
    pub = emails.public_status()
    assert pub["sender_ok"] is False and "usable" not in pub
    monkeypatch.setattr(emails, "KITLY_BREVO_API_KEY", "")
    assert emails.check_sender()["sender_ok"] is False



def test_stripe_returns_follow_visitor_domain(client, monkeypatch):
    from app.kitly import config, plans
    from app.kitly.routers import billing

    fake = _FakeStripe()
    monkeypatch.setattr(billing, "stripe_client", lambda: fake)
    monkeypatch.setitem(config.KITLY_STRIPE_PRICES, "custom", "price_kitly_custom")
    monkeypatch.setattr(plans, "KITLY_STRIPE_PRICES", config.KITLY_STRIPE_PRICES)
    _, h = register(client)
    for origin, expected in [("https://kitly.fr", "https://kitly.fr/subscribe"),
                             ("https://kitly.framer.website", "https://kitly.framer.website/subscribe"),
                             ("https://evil.example", config.KITLY_FRONTEND_URL + "/subscribe")]:
        r = client.post("/kitly/billing/checkout", json={"plan": "custom"}, headers={**h, "Origin": origin})
        assert r.status_code == 200, r.text
        assert fake.last_checkout["success_url"].startswith(expected + "?subscription=success&session_id=")
        assert fake.last_checkout["cancel_url"] == expected + "?subscription=cancelled"
    # Une valeur fixée par variable d'environnement reste prioritaire.
    monkeypatch.setitem(billing.KITLY_RETURN_URL_OVERRIDES, "KITLY_BILLING_CANCEL_URL", "https://kitly.fr/tarifs")
    client.post("/kitly/billing/checkout", json={"plan": "custom"}, headers={**h, "Origin": "https://kitly.framer.website"})
    assert fake.last_checkout["cancel_url"] == "https://kitly.fr/tarifs"


def test_default_site_is_framer_until_kitly_fr_is_linked():
    from app.kitly import config
    from app.kitly.emails import mail_new_lead

    assert config.KITLY_FRONTEND_URL == "https://kitly.framer.website"
    _, html = mail_new_lead("Léa", "contact", "Camille", "c@x.fr", "Bonjour", None)
    assert "https://kitly.framer.website/builder" in html and "/dashboard" not in html


# ============================================================
# Back-office /kitly/admin
# ============================================================

def _make_admin(client):
    """Adresse ajoutée à KITLY_ADMIN_EMAILS, puis connexion à la console par mot de passe."""
    from app.kitly.config import KITLY_ADMIN_EMAILS

    email, _ = register(client)
    KITLY_ADMIN_EMAILS.add(email)
    r = client.post("/kitly/auth/admin-login", json={"email": email, "password": PWD})
    assert r.status_code == 200, r.text
    return email, {"Authorization": f"Bearer {r.json()['token']}"}


def test_admin_console_requires_password_session(client, capsys):
    from app.db.database import SessionLocal
    from app.kitly.config import KITLY_ADMIN_EMAILS
    from app.kitly.models import KitlyUser

    # Compte « admin » en base mais absent de KITLY_ADMIN_EMAILS : refusé partout.
    email, h = register(client)
    with SessionLocal() as db:
        db.query(KitlyUser).filter_by(email=email).one().is_admin = True
        db.commit()
    assert client.get("/kitly/admin/overview", headers=h).status_code == 403
    r = client.post("/kitly/auth/admin-login", json={"email": email, "password": PWD})
    assert r.status_code == 401 and r.json()["detail"] == "Email ou mot de passe incorrect."

    # Diagnostic : la raison du refus est écrite dans les journaux du serveur, jamais renvoyée.
    out = capsys.readouterr().out
    assert "Connexion admin refusée" in out and "absente de KITLY_ADMIN_EMAILS" in out and email not in out
    from app.kitly.config import _emails
    assert _emails('"Chef@Kitly.fr"; autre@kitly.fr') == {"chef@kitly.fr", "autre@kitly.fr"}
    assert "admin_emails_configured" in client.get("/kitly/health").json()

    # Adresse autorisée : la session ordinaire du site ne suffit pas, il faut la connexion admin.
    KITLY_ADMIN_EMAILS.add(email)
    r = client.get("/kitly/admin/overview", headers=h)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "admin_login_required"
    assert client.post("/kitly/auth/admin-login", json={"email": email, "password": "mauvais-mdp"}).status_code == 401
    r = client.post("/kitly/auth/admin-login", json={"email": email, "password": PWD})
    assert r.status_code == 200 and r.json()["expires_in_minutes"] == 480
    ah = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.get("/kitly/admin/overview", headers=ah).status_code == 200
    assert client.get("/kitly/admin/actions", headers=ah).json()["items"][0]["action"] == "admin_login"

    # Adresse retirée de la liste : la session admin est coupée aussitôt.
    KITLY_ADMIN_EMAILS.discard(email)
    assert client.get("/kitly/admin/overview", headers=ah).status_code == 403

    # Mode support : le jeton émis pour agir à la place d'un compte n'ouvre jamais la console.
    _, ah2 = _make_admin(client)
    uid = _uid(client, register(client)[1])
    imp = client.post(f"/kitly/admin/users/{uid}/impersonate", headers=ah2).json()["token"]
    assert client.get("/kitly/admin/overview", headers={"Authorization": f"Bearer {imp}"}).status_code == 403


def _uid(client, h):
    return client.get("/kitly/auth/me", headers=h).json()["user"]["id"]


def test_admin_routes_are_forbidden_to_regular_accounts(client):
    _, h = register(client)
    for method, path in [("GET", "/kitly/admin/overview"), ("GET", "/kitly/admin/users"), ("GET", "/kitly/admin/system"),
                         ("POST", "/kitly/admin/users/1/impersonate"), ("GET", "/kitly/admin/actions")]:
        assert client.request(method, path, headers=h).status_code == 403


def test_admin_grant_plan_and_marketplace(client):
    _, ah = _make_admin(client)
    email, h = register(client)
    age_account(email)  # passé le mois de bienvenue : un compte Free n'est plus référencé
    tag = uuid.uuid4().hex[:8]
    slug = make_vitrine(client, h, headline=f"Grant {tag}")
    uid = _uid(client, h)
    assert client.get("/kitly/marketplace/search", params={"q": tag}).json()["total"] == 0  # Free : absent
    r = client.post(f"/kitly/admin/users/{uid}/grant", json={"plan": "premium", "reason": "Partenaire", "days": 30}, headers=ah)
    assert r.status_code == 200 and r.json()["plan"] == "premium" and r.json()["grant"]["expires_at"]
    assert client.get("/kitly/auth/me", headers=h).json()["plan"]["code"] == "premium"
    found = client.get("/kitly/marketplace/search", params={"q": tag}).json()
    assert [c["slug"] for c in found["items"]] == [slug]  # Premium+ offert : présent
    detail = client.get(f"/kitly/admin/users/{uid}", headers=ah).json()
    assert detail["grant"]["plan"] == "premium" and detail["actions"][0]["action"] == "grant_plan"
    assert client.request("DELETE", f"/kitly/admin/users/{uid}/grant", headers=ah).json()["plan"] == "free"
    # Formule offerte expirée : la tâche quotidienne remet le compte en Free.
    client.post(f"/kitly/admin/users/{uid}/grant", json={"plan": "custom", "reason": "Test"}, headers=ah)
    from app.db.database import SessionLocal
    from app.kitly.models import KitlyPlanGrant
    from app.kitly.router import kitly_purge_job
    from app.kitly.utils import now
    from datetime import timedelta
    with SessionLocal() as db:
        g = db.query(KitlyPlanGrant).filter_by(user_id=uid, revoked_at=None).one()
        g.expires_at = now() - timedelta(minutes=1)
        db.commit()
    kitly_purge_job()
    assert client.get("/kitly/auth/me", headers=h).json()["plan"]["code"] == "free"


def test_admin_search_verify_deactivate(client):
    _, ah = _make_admin(client)
    email, h = register(client, verify=False)
    slug = make_vitrine(client, h, publish=False)
    uid = _uid(client, h)
    items = client.get("/kitly/admin/users", params={"q": email.split("@")[0]}, headers=ah).json()["items"]
    assert [i["email"] for i in items] == [email] and items[0]["profile"]["slug"] == slug
    assert client.post(f"/kitly/admin/users/{uid}/verify-email", headers=ah).json()["user"]["email_verified"] is True
    assert client.post("/kitly/me/profile/publish", headers=h).status_code == 200
    assert client.get(f"/kitly/public/profiles/{slug}").status_code == 200
    assert client.post(f"/kitly/admin/users/{uid}/deactivate", json={"reason": "Fraude"}, headers=ah).status_code == 200
    assert client.get("/kitly/auth/me", headers=h).status_code == 401           # sessions coupées
    assert client.post("/kitly/auth/login", json={"email": email, "password": PWD}).status_code == 401
    assert client.get(f"/kitly/public/profiles/{slug}").status_code == 404      # vitrine masquée
    client.post(f"/kitly/admin/users/{uid}/reactivate", headers=ah)
    assert client.post("/kitly/auth/login", json={"email": email, "password": PWD}).status_code == 200


def test_admin_impersonation_is_limited(client, monkeypatch):
    admin_email, ah = _make_admin(client)
    _, h = register(client)
    make_vitrine(client, h, publish=False)
    uid = _uid(client, h)
    r = client.post(f"/kitly/admin/users/{uid}/impersonate", headers=ah).json()
    ih = {"Authorization": f"Bearer {r['token']}"}
    me = client.get("/kitly/auth/me", headers=ih).json()
    assert me["user"]["id"] == uid and me["impersonated_by"] == admin_email
    assert client.patch("/kitly/me/profile", json={"bio": "Corrigé par le support"}, headers=ih).status_code == 200
    # Interdit en session « à sa place » : mot de passe, suppression, paiements.
    assert client.post("/kitly/account/password", json={"current_password": PWD, "new_password": "nouveau123"},
                       headers=ih).status_code == 403
    assert client.request("DELETE", "/kitly/account", json={"password": PWD, "confirm": "SUPPRIMER"}, headers=ih).status_code == 403
    assert client.post("/kitly/billing/checkout", json={"plan": "custom"}, headers=ih).status_code == 403
    assert client.post("/kitly/billing/cancel", headers=ih).status_code == 403
    assert client.get("/kitly/auth/me", headers=h).json()["impersonated_by"] is None
    log_ = client.get("/kitly/admin/actions", headers=ah).json()["items"]
    assert any(a["action"] == "impersonate" and a["target_id"] == str(uid) for a in log_)


def test_admin_suspend_notifies_owner(client, _isolation):
    _, ah = _make_admin(client)
    email, h = register(client)
    slug = make_vitrine(client, h)
    pid = client.get("/kitly/me/profile", headers=h).json()["profile"]["id"]
    r = client.post(f"/kitly/admin/profiles/{pid}/suspend", json={"reason": "Contenu trompeur"}, headers=ah)
    assert r.status_code == 200
    assert any(to == email and "suspendue" in subj for to, subj in _isolation)
    assert client.get(f"/kitly/public/profiles/{slug}").status_code == 404
    listed = client.get("/kitly/admin/profiles", params={"status": "suspended", "q": slug}, headers=ah).json()["items"]
    assert listed[0]["slug"] == slug and listed[0]["suspended_reason"] == "Contenu trompeur"
    client.post(f"/kitly/admin/profiles/{pid}/unsuspend", headers=ah)
    assert client.get(f"/kitly/public/profiles/{slug}").status_code == 200


def test_admin_delete_user_and_system(client, monkeypatch):
    from app.kitly import emails

    _, ah = _make_admin(client)
    email, h = register(client)
    uid = _uid(client, h)
    assert client.request("DELETE", f"/kitly/admin/users/{uid}", json={"confirm": "OUI", "reason": "x"}, headers=ah).status_code == 422
    assert client.request("DELETE", f"/kitly/admin/users/{uid}", json={"confirm": "SUPPRIMER", "reason": "Demande RGPD"},
                          headers=ah).status_code == 200
    assert client.post("/kitly/auth/login", json={"email": email, "password": PWD}).status_code == 401
    ov = client.get("/kitly/admin/overview", headers=ah).json()
    assert {"users", "mrr_cents", "open_reports", "views_30d"} <= set(ov)
    sysinfo = client.get("/kitly/admin/system", headers=ah).json()
    assert "stripe" in sysinfo and not any(k in str(sysinfo) for k in ("sk_live_", "sk_test_", "whsec_"))
    monkeypatch.setattr(emails, "send_result", lambda to, s, html, reply_to=None: {"sent": True, "detail": "ok"})
    assert client.post("/kitly/admin/system/email-test", json={}, headers=ah).json()["sent"] is True


# ============================================================
# Achat sur une vitrine (Stripe Checkout + Connect)
# ============================================================

class _FakeShopStripe:
    def __init__(self):
        outer = self
        self.calls = []

        class _Sessions:
            def create(self, params=None):
                outer.calls.append(params)
                return {"id": f"cs_{uuid.uuid4().hex[:12]}", "url": "https://checkout.stripe.test/pay"}

        class _Checkout:
            sessions = _Sessions()

        self.checkout = _Checkout()


def _seller_ready(client, h, connect=True):
    """Vitrine publiée + CGV publiées (+ paiements Stripe actifs si `connect`)."""
    from app.db.database import SessionLocal
    from app.kitly.models import KitlyConnectedAccount

    slug = make_vitrine(client, h)
    client.put("/kitly/me/terms/draft", json={"articles": [{"key": "objet", "title": "Objet", "body": "Vente des produits."}]}, headers=h)
    assert client.post("/kitly/me/terms/draft/publish", json={"accept": True}, headers=h).status_code == 200
    if connect:
        uid = _uid(client, h)
        with SessionLocal() as db:
            db.add(KitlyConnectedAccount(user_id=uid, stripe_account_id=f"acct_{uuid.uuid4().hex[:10]}",
                                         details_submitted=True, charges_enabled=True, payouts_enabled=True))
            db.commit()
    return slug


def test_shop_checkout_paid_by_webhook(client, monkeypatch, _isolation):
    from app.kitly.routers import billing, shop

    fake = _FakeShopStripe()
    monkeypatch.setattr(shop, "stripe_client", lambda: fake)
    monkeypatch.setattr(billing, "KITLY_STRIPE_WEBHOOK_SECRET", "whsec_kitly_test")

    email, h = register(client)
    acct = f"acct_{uuid.uuid4().hex[:10]}"
    slug = _seller_ready(client, h, connect=False)
    tee = client.post("/kitly/me/offers", json={
        "kind": "physical", "title": "Tee-shirt", "price_cents": 3000, "status": "published",
        "details": {"variants": [{"label": "M", "stock": 2}, {"label": "L", "stock": 0}],
                    "shipping_options": [{"label": "Colissimo", "price_cents": 500}], "pickup_available": True,
                    "delivery_zones": ["fr_metro", "eu"]}}, headers=h).json()["item"]
    ebook = client.post("/kitly/me/offers", json={
        "kind": "digital", "title": "E-book", "price_cents": 1500, "status": "published",
        "details": {"delivery_url": "drive.example.com/ebook-secret"}}, headers=h).json()["item"]
    devis = client.post("/kitly/me/offers", json={"kind": "service", "title": "Site", "price_mode": "quote",
                                                  "cta": "quote", "status": "published"}, headers=h).json()["item"]
    pub = lambda: {o["id"]: o for o in client.get(f"/kitly/public/profiles/{slug}").json()["offers"]}

    # Paiements pas encore activés : « Commander » reste une demande, le paiement est refusé.
    assert pub()[tee["id"]]["buyable"] is False
    body = {"email": "client@exemple.fr", "accept_terms": True, "variant_id": tee["details"]["variants"][0]["id"], "delivery": "0"}
    rr = client.post(f"/kitly/public/profiles/{slug}/offers/{tee["id"]}/checkout", json=body); assert rr.status_code == 409, rr.text

    from app.db.database import SessionLocal
    from app.kitly.models import KitlyConnectedAccount
    with SessionLocal() as db:
        db.add(KitlyConnectedAccount(user_id=_uid(client, h), stripe_account_id=acct,
                                     details_submitted=True, charges_enabled=True, payouts_enabled=True))
        db.commit()
    offers = pub()
    assert offers[tee["id"]]["buyable"] and offers[ebook["id"]]["buyable"] and not offers[devis["id"]]["buyable"]
    assert "delivery_url" not in offers[ebook["id"]]["details"]  # lien de livraison jamais public
    url = f"/kitly/public/profiles/{slug}/offers/{tee['id']}/checkout"

    # Garde-fous : CGV à accepter, variante épuisée, renonciation obligatoire pour le numérique.
    assert client.post(url, json={**body, "accept_terms": False}).status_code == 422
    assert client.post(url, json={**body, "variant_id": tee["details"]["variants"][1]["id"]}).status_code == 409
    assert client.post(url, json={**body, "quantity": 3}).status_code == 409
    eurl = f"/kitly/public/profiles/{slug}/offers/{ebook['id']}/checkout"
    assert client.post(eurl, json={"email": "client@exemple.fr", "accept_terms": True}).status_code == 422

    # Achat venu de la marketplace : commission Kitly, argent versé au vendeur, retour sur la page d'origine.
    r = client.post(url, json={**body, "quantity": 2, "source": "marketplace",
                               "return_url": f"https://kitly.framer.website/profile?u={slug}&commande=ok&ref=VIEUX"})
    assert r.status_code == 200 and r.json()["url"].startswith("https://checkout.stripe.test")
    p = fake.calls[-1]
    assert p["payment_intent_data"]["transfer_data"]["destination"] == acct
    assert p["payment_intent_data"]["application_fee_amount"] == 650  # 10 % de (2 × 30 € + 5 €)
    assert [li["price_data"]["unit_amount"] for li in p["line_items"]] == [3000, 500]
    assert p["success_url"] == f"https://kitly.framer.website/profile?u={slug}&commande=ok&ref={r.json()['ref']}"
    assert "FR" in p["shipping_address_collection"]["allowed_countries"] and p["metadata"]["app"] == "kitly"
    # Vente directe : pas de commission ; page de retour inconnue → adresse publique de la vitrine.
    client.post(url, json={**body, "delivery": "pickup", "return_url": "https://evil.example/x"})
    assert "application_fee_amount" not in fake.calls[-1]["payment_intent_data"]
    assert "shipping_address_collection" not in fake.calls[-1]
    assert not fake.calls[-1]["success_url"].startswith("https://evil.example")
    # Vitrine ouverte en /profile#<adresse> : le retour de paiement garde l'adresse dans le hash,
    # mais jamais autre chose (fiche offre, contenu arbitraire).
    client.post(url, json={**body, "delivery": "pickup", "return_url": f"https://kitly.framer.website/profile?src=marketplace#{slug}"})
    ok_url = fake.calls[-1]["success_url"]
    assert ok_url.startswith("https://kitly.framer.website/profile?src=marketplace&commande=ok&ref=") and ok_url.endswith(f"#{slug}")
    assert fake.calls[-1]["cancel_url"] == f"https://kitly.framer.website/profile?src=marketplace&commande=annulee#{slug}"
    client.post(url, json={**body, "delivery": "pickup", "return_url": f"https://kitly.framer.website/profile#{slug}/<script>"})
    assert fake.calls[-1]["cancel_url"] == "https://kitly.framer.website/profile?commande=annulee"

    # Le paiement n'est confirmé que par le webhook Stripe.
    ref = r.json()["ref"]
    pi = f"pi_{uuid.uuid4().hex[:10]}"
    assert client.get("/kitly/me/orders", headers=h).json()["items"] == []
    session = {"id": "cs_x", "object": "checkout.session", "mode": "payment", "payment_status": "paid",
               "payment_intent": pi, "metadata": {**p["metadata"]},
               "customer_details": {"email": "client@exemple.fr", "name": "Camille Client", "phone": "+33600000000"},
               "collected_information": {"shipping_details": {"name": "Camille Client", "address": {
                   "line1": "3 rue des Lilas", "postal_code": "75011", "city": "Paris", "country": "FR"}}}}
    _isolation.clear()
    for _ in range(2):  # deux événements distincts pour la même session : traitée une seule fois
        event = {"id": f"evt_{uuid.uuid4().hex}", "type": "checkout.session.completed", "data": {"object": session}}
        b, hd = _signed(event, "whsec_kitly_test")
        assert client.post("/kitly/stripe/webhook", content=b, headers=hd).status_code == 200
    orders = client.get("/kitly/me/orders", headers=h).json()["items"]
    assert len(orders) == 1 and orders[0]["ref"] == ref and orders[0]["status"] == "paid"
    assert orders[0]["item"]["shipping_address"]["city"] == "Paris" and orders[0]["commission_cents"] == 650
    assert sorted(to for to, _ in _isolation) == sorted([email, "client@exemple.fr"])
    variants = client.get("/kitly/me/offers", headers=h).json()["items"]
    assert [v for v in variants if v["id"] == tee["id"]][0]["details"]["variants"][0]["stock"] == 0  # 2 - 2
    assert pub()[tee["id"]]["buyable"] is False  # épuisé

    # Remboursement total signalé par Stripe.
    event = {"id": f"evt_{uuid.uuid4().hex}", "type": "charge.refunded",
             "data": {"object": {"id": "ch_1", "payment_intent": pi, "refunded": True}}}
    b, hd = _signed(event, "whsec_kitly_test")
    client.post("/kitly/stripe/webhook", content=b, headers=hd)
    assert client.get("/kitly/me/orders", headers=h).json()["items"][0]["status"] == "refunded"


def test_shop_digital_delivery_link_sent_after_payment(client, monkeypatch):
    from app.kitly import emails
    from app.kitly.routers import billing, shop

    fake = _FakeShopStripe()
    monkeypatch.setattr(shop, "stripe_client", lambda: fake)
    monkeypatch.setattr(billing, "KITLY_STRIPE_WEBHOOK_SECRET", "whsec_kitly_test")
    mails = []
    monkeypatch.setattr(emails, "send", lambda to, subject, html, reply_to=None: mails.append((to, html)) or True)
    _, h = register(client)
    slug = _seller_ready(client, h)
    ebook = client.post("/kitly/me/offers", json={"kind": "digital", "title": "E-book", "price_cents": 1500, "status": "published",
                                                  "details": {"delivery_url": "https://drive.example.com/secret"}}, headers=h).json()["item"]
    buyer = f"lecteur-{uuid.uuid4().hex[:6]}@exemple.fr"
    r = client.post(f"/kitly/public/profiles/{slug}/offers/{ebook['id']}/checkout",
                    json={"email": buyer, "accept_terms": True, "waive_withdrawal": True})
    assert r.status_code == 200
    from app.db.database import SessionLocal
    from app.kitly.models import KitlyTermsAcceptance
    with SessionLocal() as db:
        acc = db.query(KitlyTermsAcceptance).filter_by(buyer_email=buyer).one()
        assert acc.consents["waive_withdrawal"] is True and len(acc.content_hash) == 64
    session = {"id": "cs_y", "mode": "payment", "payment_status": "paid", "payment_intent": f"pi_{uuid.uuid4().hex[:10]}",
               "metadata": fake.calls[-1]["metadata"], "customer_details": {"email": buyer}}
    event = {"id": f"evt_{uuid.uuid4().hex}", "type": "checkout.session.completed", "data": {"object": session}}
    b, hd = _signed(event, "whsec_kitly_test")
    assert client.post("/kitly/stripe/webhook", content=b, headers=hd).status_code == 200
    buyer_mail = [html for to, html in mails if to == buyer][0]
    assert "https://drive.example.com/secret" in buyer_mail


def test_terms_template_prefilled_and_publish_guard(client):
    _, h = register(client)
    make_vitrine(client, h)
    client.post("/kitly/me/offers", json={"kind": "digital", "title": "Guide", "price_cents": 900, "status": "published"}, headers=h)
    t = client.get("/kitly/me/terms/template", headers=h).json()
    keys = [a["key"] for a in t["articles"]]
    assert "numerique" in keys and "retractation" in keys and "livraison" not in keys
    assert "legal_name" in t["missing"] and "[à compléter]" in t["articles"][0]["body"]
    # Un brouillon avec des trous ne peut pas être publié.
    client.put("/kitly/me/terms/draft", json={"articles": [{k: a[k] for k in ("key", "title", "body")} for a in t["articles"]]}, headers=h)
    r = client.post("/kitly/me/terms/draft/publish", json={"accept": True}, headers=h)
    assert r.status_code == 422 and "à compléter" in r.json()["detail"]
    # Identité complétée : le modèle se remplit, plus aucun trou, publication acceptée.
    client.put("/kitly/me/legal-profile", json={"is_professional": True, "legal_name": "Léa Martin EI", "legal_form": "Micro-entreprise",
                                               "siren": "123456789", "address_line": "1 rue X", "postal_code": "69001", "city": "Lyon",
                                               "professional_email": "lea@exemple.fr", "mediator_name": "CM2C", "vat_exempt": True}, headers=h)
    t = client.get("/kitly/me/terms/template", headers=h).json()
    assert t["missing"] == [] and not any("[à compléter]" in a["body"] for a in t["articles"])
    assert "Léa Martin EI" in t["articles"][0]["body"] and "293 B" in t["articles"][1]["body"] and "CM2C" in t["articles"][-1]["body"]
    client.put("/kitly/me/terms/draft", json={"articles": [{k: a[k] for k in ("key", "title", "body")} for a in t["articles"]]}, headers=h)
    assert client.post("/kitly/me/terms/draft/publish", json={"accept": True}, headers=h).status_code == 200


def test_seller_sales_tab_summary_and_fulfillment(client, monkeypatch):
    from app.kitly import emails
    from app.kitly.routers import billing, shop

    fake = _FakeShopStripe()
    monkeypatch.setattr(shop, "stripe_client", lambda: fake)
    monkeypatch.setattr(billing, "KITLY_STRIPE_WEBHOOK_SECRET", "whsec_kitly_test")
    mails = []
    monkeypatch.setattr(emails, "send", lambda to, subject, html, reply_to=None: mails.append((to, subject, html)) or True)
    _, h = register(client)
    slug = _seller_ready(client, h)
    offer = client.post("/kitly/me/offers", json={"kind": "physical", "title": "Vase", "price_cents": 4000, "status": "published",
                                                  "details": {"shipping_options": [{"label": "Colissimo", "price_cents": 600}]}},
                        headers=h).json()["item"]
    buyer = f"acheteur-{uuid.uuid4().hex[:6]}@exemple.fr"
    r = client.post(f"/kitly/public/profiles/{slug}/offers/{offer['id']}/checkout",
                    json={"email": buyer, "accept_terms": True, "delivery": "0", "source": "marketplace"})
    ref = r.json()["ref"]
    session = {"id": "cs_v", "mode": "payment", "payment_status": "paid", "payment_intent": f"pi_{uuid.uuid4().hex[:10]}",
               "metadata": fake.calls[-1]["metadata"], "customer_details": {"email": buyer, "name": "Alex"}}
    event = {"id": f"evt_{uuid.uuid4().hex}", "type": "checkout.session.completed", "data": {"object": session}}
    b, hd = _signed(event, "whsec_kitly_test")
    assert client.post("/kitly/stripe/webhook", content=b, headers=hd).status_code == 200

    r = client.get("/kitly/me/orders", headers=h).json()
    assert r["summary"]["to_fulfill"] == 1 and r["summary"]["orders_30d"] == 1
    assert r["summary"]["net_30d_cents"] == 4600 - 460  # commission marketplace déduite
    assert [o["ref"] for o in client.get("/kitly/me/orders?status=todo", headers=h).json()["items"]] == [ref]

    # Un autre vendeur ne voit ni ne modifie cette vente.
    _, h2 = register(client)
    assert client.patch(f"/kitly/me/orders/{ref}", json={"fulfilled": True}, headers=h2).status_code == 404

    mails.clear()
    r = client.patch(f"/kitly/me/orders/{ref}", json={"fulfilled": True, "tracking": "6A12345678901"}, headers=h).json()
    assert r["buyer_notified"] and r["order"]["fulfilled_at"] and r["order"]["tracking"] == "6A12345678901"
    assert mails[0][0] == buyer and "expédiée" in mails[0][1] and "6A12345678901" in mails[0][2]
    assert client.get("/kitly/me/orders?status=todo", headers=h).json()["items"] == []
    # Re-marquer ne renvoie pas d'email ; annuler le marquage la remet « à traiter ».
    mails.clear()
    client.patch(f"/kitly/me/orders/{ref}", json={"fulfilled": True, "tracking": "6A12345678901"}, headers=h)
    assert mails == []
    client.patch(f"/kitly/me/orders/{ref}", json={"fulfilled": False}, headers=h)
    assert client.get("/kitly/me/orders", headers=h).json()["summary"]["to_fulfill"] == 1



def test_new_members_listed_in_marketplace_for_one_month(client):
    tag = uuid.uuid4().hex[:8]
    email, h = register(client)
    slug = make_vitrine(client, h, headline=f"Bienvenue {tag}")
    me = client.get("/kitly/auth/me", headers=h).json()
    assert me["plan"]["code"] == "free" and me["marketplace_welcome_until"]
    found = client.get("/kitly/marketplace/search", params={"q": tag}).json()
    assert [c["slug"] for c in found["items"]] == [slug]  # nouveau compte Free : référencé
    age_account(email, days=31)
    assert client.get("/kitly/marketplace/search", params={"q": tag}).json()["total"] == 0
    assert client.get("/kitly/auth/me", headers=h).json()["marketplace_welcome_until"] is None
    # Le freelance peut toujours refuser d'apparaître dans la marketplace.
    email2, h2 = register(client)
    slug2 = make_vitrine(client, h2, headline=f"Discret {tag}")
    client.patch("/kitly/me/profile", json={"marketplace_opt_in": False}, headers=h2)
    assert slug2 not in [c["slug"] for c in client.get("/kitly/marketplace/search", params={"q": tag}).json()["items"]]


def test_admin_creates_pro_account_for_a_set_time(client, _isolation):
    from datetime import timedelta
    from app.db.database import SessionLocal
    from app.kitly.models import KitlyPlanGrant
    from app.kitly.router import kitly_grants_job
    from app.kitly.utils import now

    _, ah = _make_admin(client)
    email = _email("invite")
    r = client.post("/kitly/admin/users", json={"email": email, "first_name": "Inès", "plan": "premium"}, headers=ah)
    assert r.status_code == 422  # motif obligatoire pour une formule offerte
    _isolation.clear()
    r = client.post("/kitly/admin/users", json={"email": email, "first_name": "Inès", "last_name": "Doe", "plan": "premium",
                                                "days": 30, "reason": "Partenaire de lancement"}, headers=ah)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["user"]["plan"] == "premium" and body["grant"]["expires_at"] and body["invite_sent"]
    assert [to for to, _ in _isolation] == [email]
    assert client.post("/kitly/admin/users", json={"email": email, "first_name": "X", "plan": "free"}, headers=ah).status_code == 409
    uid = body["user"]["id"]
    assert client.get(f"/kitly/admin/users/{uid}", headers=ah).json()["actions"][0]["action"] == "create_user"

    # Activation : code reçu par email, mot de passe choisi, CGU acceptées à ce moment-là.
    client.post("/kitly/auth/password/forgot", json={"email": email})
    reset = {"email": email, "code": "123456", "new_password": "nouveau-mdp-1"}
    r = client.post("/kitly/auth/password/reset", json=reset)
    assert r.status_code == 422 and "CGU" in r.json()["detail"]
    r = client.post("/kitly/auth/password/reset", json={**reset, "accept_cgu": True, "ack_privacy": True})
    assert r.status_code == 200
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    me = client.get("/kitly/auth/me", headers=h).json()
    assert me["plan"]["code"] == "premium" and me["user"]["email_verified"]

    # Fin de la période : la tâche de 30 minutes repasse le compte en Free, sans attendre la nuit.
    with SessionLocal() as db:
        g = db.query(KitlyPlanGrant).filter_by(user_id=uid, revoked_at=None).one()
        g.expires_at = now() - timedelta(minutes=1)
        db.commit()
    kitly_grants_job()
    assert client.get("/kitly/auth/me", headers=h).json()["plan"]["code"] == "free"


def test_site_sections_and_gallery_blocks(client):
    _, h = register(client)
    slug = make_vitrine(client, h)
    blocks = [
        {"type": "section", "title": "Mon atelier", "body": "Texte long " * 300, "image_url": "res.cloudinary.com/x/a.jpg",
         "image_side": "gauche", "button_label": "Voir", "button_url": "exemple.fr", "intrus": "x"},
        {"type": "gallery", "title": "Galerie", "images": [f"https://img.exemple.fr/{i}.jpg" for i in range(20)] + ["", 42]},
    ]
    r = client.patch("/kitly/me/profile", json={"blocks": blocks}, headers=h)
    assert r.status_code == 200, r.text
    pub = client.get(f"/kitly/public/profiles/{slug}").json()
    sec, gal = pub["blocks"]
    assert sec["image_side"] == "right" and sec["image_url"].startswith("https://") and sec["button_url"] == "https://exemple.fr"
    assert len(sec["body"]) == 3300 and "intrus" not in sec
    assert len(gal["images"]) == 12 and all(u.startswith("https://") for u in gal["images"])
    keys = [s["key"] for s in pub["sections"]]
    assert keys.index("content") == keys.index("about") + 1  # nouvelle section placée à sa place par défaut


def test_brevo_key_never_displayed_even_if_pasted_as_sender(client, monkeypatch):
    from app.kitly import emails

    key = "xkeysib-0123456789abcdef0123456789abcdef-AbCdEfGh"
    monkeypatch.setattr(emails, "KITLY_BREVO_API_KEY", key)
    monkeypatch.setattr(emails, "KITLY_SENDER_EMAIL", key)   # erreur de saisie sur Render
    calls = []
    monkeypatch.setattr(emails.httpx, "post", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(emails.httpx, "get", lambda *a, **k: calls.append(1))
    st = emails.check_sender()
    assert st["sender_ok"] is False and "KITLY_SENDER_EMAIL" in st["detail"]
    res = emails.send_result("a@b.fr", "Test", "<p>x</p>")
    assert res["sent"] is False and calls == []  # pas d'appel à Brevo avec un expéditeur invalide
    public = json.dumps(client.get("/kitly/health").json())
    for blob in (json.dumps(st), json.dumps(res), public, json.dumps(emails.mail_test())):
        assert "0123456789abcdef" not in blob
    assert emails.scrub(f"erreur {key} fin") == "erreur xkeysib-••• fin"


# ============================================================
# Panier multi-vendeurs (un paiement, un virement par vendeur)
# ============================================================

class _FakeCartStripe:
    def __init__(self):
        outer = self
        self.intents, self.made_transfers, self.reversals, self.canceled = [], [], [], []

        class _Intents:
            def create(self, params=None, options=None):
                pi = {"id": f"pi_{uuid.uuid4().hex[:10]}", "client_secret": "pi_secret_test", **params}
                outer.intents.append(pi)
                return pi

            def cancel(self, pid, params=None, options=None):
                outer.canceled.append(pid)
                return {"id": pid, "status": "canceled"}

        class _Transfers:
            def create(self, params=None, options=None):
                t = {"id": f"tr_{uuid.uuid4().hex[:10]}", **params}
                outer.made_transfers.append(t)
                return t

            def create_reversal(self, tid, params=None, options=None):
                outer.reversals.append(tid)
                return {"id": f"trr_{uuid.uuid4().hex[:8]}"}

        self.payment_intents = _Intents()
        self.transfers = _Transfers()


def test_cart_multi_seller_checkout(client, monkeypatch):
    from app.kitly import emails
    from app.kitly.routers import billing, cart, shop

    fake = _FakeCartStripe()
    monkeypatch.setattr(cart, "stripe_client", lambda: fake)
    monkeypatch.setattr(cart, "KITLY_STRIPE_PUBLISHABLE_KEY", "pk_test_kitly")
    monkeypatch.setattr(billing, "KITLY_STRIPE_WEBHOOK_SECRET", "whsec_kitly_test")
    mails = []
    monkeypatch.setattr(emails, "send", lambda to, subject, html, reply_to=None: mails.append((to, subject, html)) or True)

    # Vendeur A : tee-shirt (option, stock, livraison payante) ; vendeur B : e-book.
    email_a, ha = register(client)
    slug_a = _seller_ready(client, ha)
    tee = client.post("/kitly/me/offers", json={
        "kind": "physical", "title": "Tee-shirt", "price_cents": 2500, "status": "published",
        "details": {"variants": [{"label": "M", "stock": 3}], "shipping_options": [{"label": "Colissimo", "price_cents": 500}],
                    "delivery_zones": ["fr_metro"]}}, headers=ha).json()["item"]
    email_b, hb = register(client)
    slug_b = _seller_ready(client, hb)
    ebook = client.post("/kitly/me/offers", json={"kind": "digital", "title": "E-book", "price_cents": 1200, "status": "published",
                                                  "details": {"delivery_url": "https://drive.example.com/ebook"}}, headers=hb).json()["item"]
    variant = tee["details"]["variants"][0]["id"]
    items = [{"offer_id": tee["id"], "variant_id": variant, "quantity": 2, "delivery": "0"},
             {"offer_id": ebook["id"], "source": "marketplace"}]

    # Devis : prix du serveur, groupé par vendeur, adresse et renonciation requises.
    q = client.post("/kitly/cart/quote", json={"items": items}).json()
    assert [s["slug"] for s in q["sellers"]] == [slug_a, slug_b]
    assert q["total_cents"] == 2 * 2500 + 500 + 1200 and q["needs_address"] and "FR" in q["countries"]
    assert q["has_digital"] and not q["has_errors"] and q["sellers"][0]["lines"][0]["max_quantity"] == 3
    # Une seule livraison par vendeur : un 2e article expédié du même vendeur ne repaie pas la livraison.
    mug = client.post("/kitly/me/offers", json={"kind": "physical", "title": "Mug", "price_cents": 1000, "status": "published",
                                                "details": {"shipping_options": [{"label": "Lettre", "price_cents": 300}]}},
                      headers=ha).json()["item"]
    two = client.post("/kitly/cart/quote", json={"items": [items[0], {"offer_id": mug["id"], "delivery": "0"}]}).json()
    assert two["total_cents"] == 2 * 2500 + 500 + 1000 and [l["shipping_cents"] for l in two["sellers"][0]["lines"]] == [500, 0]
    # Une ligne trop gourmande est signalée, pas facturée.
    bad = client.post("/kitly/cart/quote", json={"items": [dict(items[0], quantity=5)]}).json()
    assert bad["has_errors"] and "stock insuffisant" in bad["sellers"][0]["lines"][0]["error"] and bad["total_cents"] == 0

    buyer = f"panier-{uuid.uuid4().hex[:6]}@exemple.fr"
    body = {"items": items, "email": buyer, "name": "Alex Client", "phone": "0600000000", "accept_terms": True,
            "waive_withdrawal": True,
            "address": {"name": "Alex Client", "line1": "1 rue de Paris", "postal_code": "75001", "city": "Paris", "country": "FR"}}
    assert client.post("/kitly/checkout", json={**body, "address": None}).status_code == 422          # adresse manquante
    assert client.post("/kitly/checkout", json={**body, "waive_withdrawal": False}).status_code == 422  # numérique
    assert client.post("/kitly/checkout", json={**body, "address": {**body["address"], "country": "US"}}).status_code == 422
    r = client.post("/kitly/checkout", json=body)
    assert r.status_code == 200, r.text
    co = r.json()
    assert co["client_secret"] == "pi_secret_test" and co["publishable_key"] == "pk_test_kitly" and co["amount_cents"] == 6700
    pi = fake.intents[-1]
    assert pi["amount"] == 6700 and pi["transfer_group"] == co["ref"] and pi["metadata"]["app"] == "kitly"
    assert pi["shipping"]["address"]["city"] == "Paris"
    assert client.get(f"/kitly/checkout/{co['ref']}").json()["status"] == "pending"
    assert client.get("/kitly/me/orders", headers=ha).json()["items"] == []  # rien avant le webhook

    # Paiement confirmé par Stripe : commandes payées, un virement net par vendeur.
    event = {"id": f"evt_{uuid.uuid4().hex}", "type": "payment_intent.succeeded",
             "data": {"object": {"id": pi["id"], "object": "payment_intent", "latest_charge": "ch_cart", "metadata": pi["metadata"]}}}
    b, hd = _signed(event, "whsec_kitly_test")
    assert client.post("/kitly/stripe/webhook", content=b, headers=hd).status_code == 200
    st = client.get(f"/kitly/checkout/{co['ref']}").json()
    assert st["status"] == "paid" and len(st["items"]) == 2 and "email" not in st
    oa = client.get("/kitly/me/orders", headers=ha).json()["items"]
    ob = client.get("/kitly/me/orders", headers=hb).json()["items"]
    assert len(oa) == 1 and oa[0]["status"] == "paid" and oa[0]["amount_cents"] == 5500 and oa[0]["item"]["shipping_address"]["city"] == "Paris"
    assert len(ob) == 1 and ob[0]["commission_cents"] == 120 and ob[0]["channel"] == "marketplace"
    amounts = sorted((t["amount"], t["source_transaction"], t["transfer_group"]) for t in fake.made_transfers)
    assert amounts == [(1080, "ch_cart", co["ref"]), (5500, "ch_cart", co["ref"])]
    stock = [o for o in client.get("/kitly/me/offers", headers=ha).json()["items"] if o["id"] == tee["id"]][0]
    assert stock["details"]["variants"][0]["stock"] == 1
    buyer_mail = [h for to, _, h in mails if to == buyer]
    assert len(buyer_mail) == 1 and "https://drive.example.com/ebook" in buyer_mail[0] and co["ref"] in buyer_mail[0]
    assert {to for to, _, _ in mails} >= {email_a, email_b, buyer}

    # Événement relivré : rien n'est refait.
    b, hd = _signed({**event, "id": f"evt_{uuid.uuid4().hex}"}, "whsec_kitly_test")
    client.post("/kitly/stripe/webhook", content=b, headers=hd)
    assert len(fake.made_transfers) == 2

    # Remboursement total : commandes remboursées, virements repris aux vendeurs.
    event = {"id": f"evt_{uuid.uuid4().hex}", "type": "charge.refunded",
             "data": {"object": {"id": "ch_cart", "object": "charge", "payment_intent": pi["id"], "refunded": True}}}
    b, hd = _signed(event, "whsec_kitly_test")
    assert client.post("/kitly/stripe/webhook", content=b, headers=hd).status_code == 200
    assert client.get(f"/kitly/checkout/{co['ref']}").json()["status"] == "refunded"
    assert client.get("/kitly/me/orders", headers=hb).json()["items"][0]["status"] == "refunded"
    assert sorted(fake.reversals) == sorted(t["id"] for t in fake.made_transfers)


def test_cart_replaces_previous_and_needs_key(client, monkeypatch):
    from app.kitly.routers import cart

    fake = _FakeCartStripe()
    monkeypatch.setattr(cart, "stripe_client", lambda: fake)
    _, h = register(client)
    _seller_ready(client, h)
    ebook = client.post("/kitly/me/offers", json={"kind": "service", "title": "Audit", "price_cents": 9000, "status": "published", "cta": "order"},
                        headers=h).json()["item"]
    body = {"items": [{"offer_id": ebook["id"]}], "email": "a@exemple.fr", "name": "A", "accept_terms": True}
    monkeypatch.setattr(cart, "KITLY_STRIPE_PUBLISHABLE_KEY", "")
    assert client.post("/kitly/checkout", json=body).status_code == 503
    monkeypatch.setattr(cart, "KITLY_STRIPE_PUBLISHABLE_KEY", "pk_test_kitly")
    assert client.post("/kitly/checkout", json={**body, "accept_terms": False}).status_code == 422
    first = client.post("/kitly/checkout", json=body).json()
    assert "ref" in first, first
    second = client.post("/kitly/checkout", json={**body, "replaces": first["ref"]}).json()
    assert fake.canceled == [fake.intents[0]["id"]]
    assert client.get(f"/kitly/checkout/{first['ref']}").json()["status"] == "canceled"
    assert client.get(f"/kitly/checkout/{second['ref']}").json()["status"] == "pending"


# ============================================================
# Fiche produit détaillée et police personnalisée
# ============================================================

def test_offer_detailed_sheet(client):
    _, h = register(client)
    slug = make_vitrine(client, h)
    details = {"highlights": ["Coton bio", "  ", "Fabriqué en France"],
               "specs": [{"label": "Matière", "value": "Coton 180 g/m²"}],
               "faq": [{"question": "Délais ?", "answer": "48 h"}],
               "guarantee": "Retours sous 14 jours.", "sku": "TSH-01", "weight_grams": 250, "dimensions": "30 × 20 cm",
               "delivery_zones": ["fr_metro", "eu"], "shipping_options": [{"label": "Colissimo", "price_cents": 600, "delay": "2 j"}]}
    r = client.post("/kitly/me/offers", json={"kind": "physical", "title": "T-shirt", "price_cents": 2900,
                                              "status": "published", "details": details}, headers=h)
    assert r.status_code == 201, r.text
    d = client.get(f"/kitly/public/profiles/{slug}").json()["offers"][0]["details"]
    assert d["highlights"] == ["Coton bio", "Fabriqué en France"]
    assert d["specs"][0]["label"] == "Matière" and d["faq"][0]["answer"] == "48 h"
    assert d["guarantee"] == "Retours sous 14 jours." and d["shipping_options"][0]["delay"] == "2 j"
    # Toutes les catégories ont la fiche détaillée.
    r = client.post("/kitly/me/offers", json={"kind": "service", "title": "Logo", "price_mode": "quote",
                                              "details": {"highlights": ["Fichiers sources"], "deliverables": ["Logo"]}}, headers=h)
    assert r.status_code == 201, r.text
    bad = client.post("/kitly/me/offers", json={"kind": "digital", "title": "E-book", "price_cents": 900,
                                                "details": {"highlights": ["x" * 201]}}, headers=h)
    assert bad.status_code == 422
    bad = client.post("/kitly/me/offers", json={"kind": "digital", "title": "E-book", "price_cents": 900,
                                                "details": {"specs": [{"label": "Pages"}]}}, headers=h)
    assert bad.status_code == 422


WOFF2 = b"wOF2" + b"\0" * 60


def test_custom_font_premium_only(client, monkeypatch):
    email, h = register(client)
    slug = make_vitrine(client, h)
    font = {"url": "https://res.cloudinary.com/demo/raw/upload/kitly/u1/font/abc.woff2", "name": "Gilroy", "format": "woff2"}
    calls = []
    monkeypatch.setattr("app.kitly.routers.vitrine.upload_font", lambda data, uid, fmt: calls.append(fmt) or font["url"])

    # Free et Custom : refusé (import et thème).
    for plan in ("free", "custom"):
        set_plan(email, plan)
        r = client.post("/kitly/me/font", headers=h, files={"file": ("Gilroy-Bold.woff2", WOFF2, "font/woff2")})
        assert r.status_code == 403 and r.json()["detail"]["code"] == "plan_required"
        r = client.patch("/kitly/me/profile", json={"theme": {"custom_font": font}}, headers=h)
        assert r.status_code == 403
    assert not calls

    set_plan(email, "premium")
    r = client.post("/kitly/me/font", headers=h, files={"file": ("Gilroy-Bold.woff2", WOFF2, "application/octet-stream")})
    assert r.status_code == 200, r.text
    assert r.json() == {"url": font["url"], "format": "woff2", "name": "Gilroy Bold"} and calls == ["woff2"]
    r = client.post("/kitly/me/font", headers=h, files={"file": ("virus.woff2", b"MZ" + b"\0" * 40, "font/woff2")})
    assert r.status_code == 415
    r = client.patch("/kitly/me/profile", json={"theme": {"custom_font": {**font, "url": "javascript:alert(1)"}}}, headers=h)
    assert r.status_code == 422
    r = client.patch("/kitly/me/profile", json={"theme": {"custom_font": {**font, "name": "Gil'roy</style>", "apply_to": "headings"}}}, headers=h)
    assert r.status_code == 200, r.text
    cf = client.get(f"/kitly/public/profiles/{slug}").json()["theme"]["custom_font"]
    assert cf["url"] == font["url"] and cf["name"] == "Gilroystyle" and cf["apply_to"] == "headings"

    # Retour à Custom : la police n'est plus affichée (mais reste enregistrée pour un retour à Premium+).
    set_plan(email, "custom")
    assert "custom_font" not in client.get(f"/kitly/public/profiles/{slug}").json()["theme"]
    set_plan(email, "premium")
    assert client.get(f"/kitly/public/profiles/{slug}").json()["theme"]["custom_font"]["url"] == font["url"]

    # Retrait : adresse vide.
    r = client.patch("/kitly/me/profile", json={"theme": {"custom_font": {"url": ""}}}, headers=h)
    assert r.status_code == 200 and "custom_font" not in r.json()["profile"]["theme"]
