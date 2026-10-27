"""
Paramètres de facturation du profil contre une base SQLite et un faux Stripe :
résumé du profil → GET /subscriptions/billing (NAVIRE AI et Prép'AdJuris) →
changement de carte (Checkout en mode setup) → webhook qui pose la carte et
représente la facture impayée. Et la route mot de passe, restaurée.

Lancer : pytest tests/test_billing_settings.py
(variables d'environnement factices posées ci-dessous, aucune clé réelle.)
"""

import os
import uuid
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

MATIERE = "L3_droit_des_societes"


def _carte(last4, exp_year=2031):
    return {"id": f"pm_{last4}", "type": "card",
            "card": {"brand": "visa", "last4": last4, "exp_month": 4, "exp_year": exp_year}}


class FauxStripe:
    def __init__(self):
        self.sessions = []
        self.clients = {}
        self.abonnements = {}
        self.schedules = []
        self.factures = []
        self.payees = []

    def creer_session(self, **params):
        self.sessions.append(params)
        return SimpleNamespace(url="https://checkout.stripe.test/setup", id=f"cs_{len(self.sessions)}")

    def retrieve_subscription(self, sid, expand=None):
        return self.abonnements[sid]

    def modify_subscription(self, sid, **params):
        self.abonnements[sid].update(params)
        return self.abonnements[sid]

    def modify_customer(self, cid, **params):
        self.clients.setdefault(cid, {}).update(params)
        return {"id": cid, **params}

    def list_invoices(self, **kw):
        data = [
            f for f in self.factures
            if all(f.get(k) == v for k, v in kw.items() if k in ("customer", "subscription", "status"))
        ]
        return {"data": data[: kw.get("limit", 10)]}

    def pay_invoice(self, iid, payment_method=None):
        self.payees.append((iid, payment_method))
        return {"id": iid, "status": "paid"}


@pytest.fixture(scope="module")
def env():
    import stripe
    import app.main as main
    from app.db.database import Base, engine, SessionLocal

    engine.dispose()
    Base.metadata.create_all(bind=engine)

    faux = FauxStripe()
    mp = pytest.MonkeyPatch()
    mp.setattr(stripe.checkout.Session, "create", staticmethod(faux.creer_session))
    mp.setattr(stripe.Subscription, "retrieve", staticmethod(faux.retrieve_subscription))
    mp.setattr(stripe.Subscription, "modify", staticmethod(faux.modify_subscription))
    mp.setattr(stripe.Customer, "modify", staticmethod(faux.modify_customer))
    mp.setattr(stripe.SubscriptionSchedule, "modify",
               staticmethod(lambda sid, **kw: faux.schedules.append((sid, kw))))
    mp.setattr(stripe.Invoice, "list", staticmethod(faux.list_invoices))
    mp.setattr(stripe.Invoice, "pay", staticmethod(faux.pay_invoice))
    mp.setattr(stripe.Invoice, "create_preview", staticmethod(lambda **kw: {"amount_due": 799}))
    mp.setattr(stripe.SetupIntent, "retrieve", staticmethod(lambda i: {"id": i, "payment_method": "pm_neuve"}))
    import app.routers.users as users
    mp.setattr(users, "send_mail", lambda *a, **k: True)

    yield SimpleNamespace(client=TestClient(main.app), faux=faux, Session=SessionLocal, stripe=stripe, mp=mp)
    mp.undo()
    engine.dispose()


def _user(db, username, **champs):
    from app.db.models import User
    u = User(email=f"{username}-{uuid.uuid4().hex[:8]}@test.fr", username=username, **champs)
    db.add(u)
    db.commit()
    db.refresh(u)
    db.expunge(u)
    return u


def _headers(user):
    from app.core.security import create_access_token
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def _membre(env, past_due=False):
    """Élève Membre mensuel, abonnement Stripe sub_m sur le client cus_m."""
    from app.db.models import Subscription
    db = env.Session()
    tag = uuid.uuid4().hex[:6]
    u = _user(db, f"membre{tag}", plan="membre")
    fin = datetime.now(timezone.utc) + timedelta(days=20)
    db.add(Subscription(
        user_id=u.id, plan="membre", billing_cycle="monthly",
        status="past_due" if past_due else "active",
        stripe_customer_id=f"cus_{tag}", stripe_subscription_id=f"sub_{tag}",
        current_period_end=fin,
    ))
    db.commit()
    db.close()
    env.faux.abonnements[f"sub_{tag}"] = {
        "id": f"sub_{tag}", "status": "past_due" if past_due else "active",
        "cancel_at_period_end": False, "current_period_end": int(fin.timestamp()),
        "current_period_start": int((fin - timedelta(days=30)).timestamp()),
        "currency": "eur", "default_payment_method": None, "schedule": None,
        "customer": {"id": f"cus_{tag}", "invoice_settings": {"default_payment_method": _carte("4242")}},
    }
    return u, tag


def test_profil_et_billing_membre(env):
    u, tag = _membre(env)
    env.faux.factures.append({
        "id": f"in_{tag}", "customer": f"cus_{tag}", "subscription": f"sub_{tag}",
        "status": "paid", "number": "NAV-0001", "created": 1760000000,
        "amount_paid": 799, "amount_due": 799, "currency": "eur",
        "invoice_pdf": "https://pay.stripe.test/pdf", "hosted_invoice_url": "https://pay.stripe.test/i",
    })
    c = env.client

    # Résumé du profil : état lu en base, mot de passe et deep work présents
    r = c.get("/users/me/profile-summary", headers=_headers(u))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["user"]["has_password"] is False
    assert "deepwork" in d
    assert d["subscription"]["navire"]["plan"] == "membre"
    assert d["subscription"]["navire"]["managed_by"] == "stripe"
    assert d["subscription"]["payment_issue"] is False

    # Détail : carte du client (l'abonnement n'en porte pas), prochain prélèvement, facture
    r = c.get("/subscriptions/billing", headers=_headers(u))
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["stripe_ok"] is True
    assert b["navire"]["card"]["last4"] == "4242"
    assert b["navire"]["card"]["expired"] is False
    assert b["navire"]["next_payment"]["amount_cents"] == 799
    assert b["navire"]["actions"] == {
        "update_card": True, "cancel": True, "reactivate": False, "portal": True, "subscribe": False,
    }
    assert [f["number"] for f in b["invoices"]] == ["NAV-0001"]
    assert b["invoices"][0]["label"] == "NAVIRE AI"


def test_compte_gratuit(env):
    db = env.Session()
    u = _user(db, "gratuit")
    db.close()
    b = env.client.get("/subscriptions/billing", headers=_headers(u)).json()
    assert b["navire"]["managed_by"] is None
    assert b["navire"]["actions"]["subscribe"] is True
    assert b["navire"]["actions"]["update_card"] is False
    assert b["invoices"] == [] and b["adjuris"] == []

    r = env.client.post("/subscriptions/payment-method", headers=_headers(u), json={"target": "navire"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "NO_STRIPE_SUBSCRIPTION"


def test_changement_de_carte_navire_regle_l_impaye(env):
    u, tag = _membre(env, past_due=True)
    env.faux.factures.append({
        "id": f"in_open_{tag}", "customer": f"cus_{tag}", "subscription": f"sub_{tag}",
        "status": "open", "number": "NAV-0002", "created": 1760100000,
        "amount_paid": 0, "amount_due": 799, "currency": "eur",
        "hosted_invoice_url": "https://pay.stripe.test/open",
    })
    c = env.client

    assert c.get("/users/me/profile-summary", headers=_headers(u)).json()["subscription"]["payment_issue"] is True
    b = c.get("/subscriptions/billing", headers=_headers(u)).json()
    assert b["navire"]["unpaid_invoice_url"] == "https://pay.stripe.test/open"

    # Retour proposé par le front : retenu sur le domaine du site, refusé ailleurs
    r = c.post("/subscriptions/payment-method", headers=_headers(u), json={
        "target": "navire", "return_url": "https://navire-ai.com/mon-profil?onglet=1&billing=old",
    })
    assert r.status_code == 200, r.text
    assert r.json()["checkout_url"].startswith("https://checkout.stripe.test")
    params = env.faux.sessions[-1]
    assert params["mode"] == "setup" and params["customer"] == f"cus_{tag}"
    assert params["success_url"] == "https://navire-ai.com/mon-profil?onglet=1&billing=card_updated"
    assert params["cancel_url"].endswith("billing=card_cancelled")
    assert params["metadata"]["subscription_ids"] == f"sub_{tag}"

    c.post("/subscriptions/payment-method", headers=_headers(u),
           json={"target": "navire", "return_url": "https://evil.example/x"})
    assert env.faux.sessions[-1]["success_url"] == "https://navire-ai.com/profil?billing=card_updated"

    # Webhook : carte posée sur le client et l'abonnement, facture représentée
    event = {"type": "checkout.session.completed", "data": {"object": {
        "id": "cs_setup", "mode": "setup", "setup_intent": "seti_1",
        "customer": f"cus_{tag}", "metadata": params["metadata"],
    }}}
    env.mp.setattr(env.stripe.Webhook, "construct_event", staticmethod(lambda *a, **k: event))
    r = c.post("/subscriptions/webhook", content=b"{}", headers={"stripe-signature": "t"})
    assert r.status_code == 200, r.text
    assert env.faux.clients[f"cus_{tag}"]["invoice_settings"] == {"default_payment_method": "pm_neuve"}
    assert env.faux.abonnements[f"sub_{tag}"]["default_payment_method"] == "pm_neuve"
    assert (f"in_open_{tag}", "pm_neuve") in env.faux.payees


def test_carte_adjuris_et_abonnement_d_un_autre(env):
    from app.db.models import PrepaAdjurisEnrollment
    from app.services.prepa_adjuris_billing import Echeancier, cle_mois, mois_suivant, mois_de

    db = env.Session()
    eleve = _user(db, "adjuris", plan="prepa")
    autre = _user(db, "autre")
    maintenant = datetime.now(timezone.utc)
    m1 = mois_de(maintenant)
    m2 = mois_suivant(m1)
    ech = Echeancier(matiere_key=MATIERE, inscrit_le=maintenant, seance_prepayee=None,
                     mois={cle_mois(m1): 0, cle_mois(m2): 3}, depuis=cle_mois(m1))
    db.add(PrepaAdjurisEnrollment(
        user_id=eleve.id, email=eleve.email, matiere_key=MATIERE,
        stripe_subscription_id="sub_adj", stripe_customer_id="cus_adj",
        status="payment_failed", source="stripe", inscrit_le=maintenant,
        echeancier=ech.to_dict(), impaye_depuis=maintenant,
    ))
    db.commit()
    db.close()
    env.faux.abonnements["sub_adj"] = {
        "id": "sub_adj", "status": "past_due", "schedule": "sub_sched_adj",
        "default_payment_method": _carte("0005", exp_year=2020),
        "customer": {"id": "cus_adj", "invoice_settings": {"default_payment_method": None}},
    }
    c = env.client

    b = c.get("/subscriptions/billing", headers=_headers(eleve)).json()
    assert len(b["adjuris"]) == 1
    bloc = b["adjuris"][0]
    assert bloc["status"] == "payment_failed"
    assert bloc["matieres"][0]["key"] == MATIERE
    assert bloc["card"]["last4"] == "0005" and bloc["card"]["expired"] is True
    assert bloc["next_payment"]["sessions"] == 3
    assert b["payment_issue"] is True

    # Un autre compte ne peut pas viser cet abonnement
    r = c.post("/subscriptions/payment-method", headers=_headers(autre), json={"target": "sub_adj"})
    assert r.status_code == 404

    r = c.post("/subscriptions/payment-method", headers=_headers(eleve), json={"target": "sub_adj"})
    assert r.status_code == 200, r.text
    params = env.faux.sessions[-1]
    assert params["customer"] == "cus_adj"

    event = {"type": "checkout.session.completed", "data": {"object": {
        "id": "cs_setup_adj", "mode": "setup", "setup_intent": "seti_2",
        "customer": "cus_adj", "metadata": params["metadata"],
    }}}
    env.mp.setattr(env.stripe.Webhook, "construct_event", staticmethod(lambda *a, **k: event))
    assert c.post("/subscriptions/webhook", content=b"{}", headers={"stripe-signature": "t"}).status_code == 200
    assert env.faux.abonnements["sub_adj"]["default_payment_method"] == "pm_neuve"
    assert ("sub_sched_adj", {"default_settings": {"default_payment_method": "pm_neuve"}}) in env.faux.schedules


def test_mot_de_passe_restaure(env):
    db = env.Session()
    u = _user(db, "oauth", oauth_provider="google")
    db.close()
    c = env.client
    # Compte Google : définit un premier mot de passe sans ancien
    r = c.post("/users/me/password", headers=_headers(u), json={"new_password": "Nouveau123"})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["has_password"] is True
    # Ensuite l'ancien est exigé
    r = c.post("/users/me/password", headers=_headers(u), json={"new_password": "Autre1234"})
    assert r.status_code == 400
    r = c.post("/users/me/password", headers=_headers(u),
               json={"current_password": "Nouveau123", "new_password": "Autre1234"})
    assert r.status_code == 200, r.text
