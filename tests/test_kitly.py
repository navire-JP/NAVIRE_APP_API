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
    # Limite par profil et par IP
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
    email, h = register(client)
    assert client.get("/kitly/admin/plans", headers=h).status_code == 403
    from app.db.database import SessionLocal
    from app.kitly.models import KitlyUser
    with SessionLocal() as db:
        db.query(KitlyUser).filter_by(email=email).one().is_admin = True
        db.commit()
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
    assert client.get("/kitly/static/../config.py").status_code == 404
    assert client.get("/kitly/static/config.py").status_code == 404


def test_vitrine_page_meta_and_escaping(client):
    email, h = register(client)
    evil = '</script><script>alert(1)</script>'
    slug = make_vitrine(client, h, display_name=f'Léa "Studio" {evil}'[:80], bio="Design & <b>identité</b>")
    r = client.get(f"/kitly/p/{slug}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    page = r.text
    assert '<meta property="og:title" content="Léa &quot;Studio&quot; &lt;/script&gt;' in page
    assert f'<link rel="canonical" href="https://kitly.fr/{slug}">' in page
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
