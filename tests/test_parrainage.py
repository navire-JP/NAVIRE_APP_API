"""
Parrainage étudiant contre une base SQLite, Stripe simulé :
inscription au parrainage (élève Prép'AdJuris) → lien /rejoindre → ami qui
crée son compte avec le code → paiement (webhook) → récompense → crédit
automatique sur le solde client Stripe du parrain → remboursement.

Lancer : pytest tests/test_parrainage.py
"""

import os
import uuid
from datetime import timedelta

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
def env():
    import app.main as main
    from app.db.database import Base, engine

    engine.dispose()
    Base.metadata.create_all(bind=engine)
    yield main
    engine.dispose()


@pytest.fixture(autouse=True)
def mails(monkeypatch):
    envoyes = []

    def faux_send_mail(to, subject, html):
        envoyes.append({"to": to, "subject": subject})
        return True

    import app.routers.ambassadors as a
    import app.routers.parrainage as r
    import app.routers.subscriptions as s
    import app.services.email as e
    for mod in (a, r, s, e):
        monkeypatch.setattr(mod, "send_mail", faux_send_mail)
    return envoyes


@pytest.fixture()
def stripe_calls(monkeypatch):
    """Simule stripe.Customer.create_balance_transaction."""
    appels = []

    def faux(customer, **kwargs):
        appels.append({"customer": customer, **kwargs})
        return {"id": f"cbtxn_{len(appels)}", "amount": kwargs["amount"]}

    import app.services.parrainage as svc
    monkeypatch.setattr(svc.stripe.Customer, "create_balance_transaction", faux)
    return appels


def _uid():
    return uuid.uuid4().hex[:8]


def _user(db, username, admin=False):
    from app.db.models import User
    u = User(email=f"{username}-{_uid()}@test.fr", username=f"{username}_{_uid()}", is_admin=admin)
    db.add(u)
    db.commit()
    db.refresh(u)
    db.expunge(u)
    return u


def _eleve(db, username, customer=None):
    """Élève Prép'AdJuris en cours (abonnement Stripe actif)."""
    from app.db.models import PrepaAdjurisEnrollment
    u = _user(db, username)
    db.add(PrepaAdjurisEnrollment(
        user_id=u.id, email=u.email, matiere_key="l2_civil", status="active",
        stripe_customer_id=customer or f"cus_{_uid()}", stripe_subscription_id=f"sub_{_uid()}",
    ))
    db.commit()
    return u


def _headers(user):
    from app.core.security import create_access_token
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def _session_payee(customer, email, matieres, **extra):
    sid = f"cs_{_uid()}"
    return {
        "id": sid,
        "mode": "payment",
        "payment_status": "paid",
        "amount_total": 2000 * len(matieres),
        "customer": customer,
        "payment_intent": f"pi_{sid}",
        "metadata": {"matiere_keys": ",".join(matieres), "email": email, **extra.pop("metadata", {})},
        **extra,
    }


@pytest.fixture()
def monde(env):
    """Un admin et une élève Prép'AdJuris inscrite au parrainage."""
    from app.db.database import SessionLocal
    db = SessionLocal()
    admin = _user(db, "admin", admin=True)
    customer = f"cus_julie_{_uid()}"
    julie = _eleve(db, "julie", customer)
    db.close()
    client = TestClient(env.app)
    r = client.post("/parrainage/join", headers=_headers(julie), json={"display_name": "Julie", "accept_terms": True})
    assert r.status_code == 200, r.text
    me = r.json()
    return {"client": client, "admin": admin, "julie": julie, "customer": customer, "code": me["code"], "me": me}


# ============================================================
# Inscription au parrainage
# ============================================================

def test_reserve_aux_eleves_prepa(env, mails):
    from app.db.database import SessionLocal
    db = SessionLocal()
    marc = _user(db, "marc")
    db.close()
    client = TestClient(env.app)

    me = client.get("/parrainage/me", headers=_headers(marc)).json()
    assert me == {"status": "none", "eligible": False, "terms_version": me["terms_version"]}
    r = client.post("/parrainage/join", headers=_headers(marc), json={"display_name": "Marc", "accept_terms": True})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "NOT_ELIGIBLE"
    assert client.get("/parrainage/me/dashboard", headers=_headers(marc)).status_code == 403

    terms = client.get("/parrainage/terms").json()
    assert terms["credit_parrain_cents"] == 500 and terms["credit_filleul_cents"] == 500
    assert len(terms["conditions"]) >= 5


def test_inscription_active_le_lien(env, mails):
    from app.db.database import SessionLocal
    db = SessionLocal()
    lea = _eleve(db, "lea")
    db.close()
    client = TestClient(env.app)

    assert client.get("/parrainage/me", headers=_headers(lea)).json()["eligible"] is True
    sans_cgu = client.post("/parrainage/join", headers=_headers(lea), json={"display_name": "Léa", "accept_terms": False})
    assert sans_cgu.status_code == 400

    me = client.post("/parrainage/join", headers=_headers(lea), json={"display_name": "Léa", "accept_terms": True}).json()
    assert me["status"] == "active" and me["code"].startswith("LEA-") and len(me["code"]) == 7
    assert me["link"].endswith(f"/rejoindre?ref={me['code']}")
    assert me["share_link"].endswith(f"/ambassadors/invite/{me['code']}")
    assert any(m["to"] == lea.email and "parrainage" in m["subject"] for m in mails)

    # Seconde inscription : même lien, pas de doublon.
    again = client.post("/parrainage/join", headers=_headers(lea), json={"display_name": "Autre", "accept_terms": True}).json()
    assert again["code"] == me["code"]

    # Nom affiché modifiable.
    assert client.patch("/parrainage/me", headers=_headers(lea), json={"display_name": "Léa M."}).json()["display_name"] == "Léa M."
    assert client.get("/auth/me", headers=_headers(lea)).json()["is_parrain"] is True


# ============================================================
# Page /rejoindre (partagée avec les ambassadeurs)
# ============================================================

def test_page_rejoindre_reconnait_le_parrain(monde):
    client, code = monde["client"], monde["code"]
    r = client.get(f"/ambassadors/ref/{code.lower()}").json()
    assert r["valid"] and r["program"] == "parrainage" and r["display_name"] == "Julie" and r["code"] == code
    assert r["filleul_credit_cents"] == 500

    bot = client.get(f"/ambassadors/invite/{code}", headers={"User-Agent": "WhatsApp/2.23"})
    assert "Julie t&#x27;invite sur NAVIRE" in bot.text
    humain = client.get(f"/ambassadors/invite/{code}", headers={"User-Agent": "Mozilla/5.0"})
    assert f"/rejoindre?ref={code}" in humain.text

    dash = client.get("/parrainage/me/dashboard", headers=_headers(monde["julie"])).json()
    assert dash["stats"]["clicks"] >= 1


def test_ami_inscrit_paie_puis_credit_stripe(monde, mails, stripe_calls):
    from app.db.database import SessionLocal
    from app.db.models import AmbassadorCommission, User
    from app.routers.subscriptions import _commission_ambassadeur, _recompense_parrainage
    from app.services import parrainage as svc

    client, code, julie = monde["client"], monde["code"], monde["julie"]
    email = f"ami-{_uid()}@test.fr"
    r = client.post("/auth/register", json={
        "username": f"ami_{_uid()}", "email": email, "password": "MotDePasse!42", "referral_code": code,
    })
    assert r.status_code == 200, r.text

    dash = client.get("/parrainage/me/dashboard", headers=_headers(julie)).json()
    assert dash["stats"]["signups"] == 1 and dash["referrals"][0]["status"] == "signed_up"
    assert "email" not in dash["referrals"][0]

    # Paiement de 2 matières (le webhook appelle les deux programmes).
    db = SessionLocal()
    ami = db.query(User).filter(User.email == email).one()
    session = _session_payee("cus_ami", email, ["l2_civil", "l2_admin"])
    for _ in range(2):  # event relivré
        _commission_ambassadeur(db, session, ami, email, ["l2_civil", "l2_admin"])
        _recompense_parrainage(db, session, ami, email, ["l2_civil", "l2_admin"])
    assert db.query(AmbassadorCommission).filter_by(stripe_checkout_session_id=session["id"]).count() == 0
    assert sum(1 for m in mails if m["to"] == julie.email and "+5,00" in m["subject"]) == 1

    # L'ami : 5 € crédités tout de suite sur le client Stripe de son paiement, une seule fois.
    assert len(stripe_calls) == 1
    assert stripe_calls[0]["customer"] == "cus_ami" and stripe_calls[0]["amount"] == -500
    assert stripe_calls[0]["idempotency_key"].startswith("navire-parrainage-filleul-")
    assert sum(1 for m in mails if m["to"] == email and "offerts" in m["subject"]) == 1
    red = db.query(svc.ParrainageReductionFilleul).filter_by(stripe_checkout_session_id=session["id"]).one()
    assert red.status == "credited"
    stripe_calls.clear()

    # Montant fixe, une fois par ami, quel que soit le nombre de matières.
    dash = client.get("/parrainage/me/dashboard", headers=_headers(julie)).json()
    assert dash["stats"]["paying"] == 1 and dash["stats"]["matieres"] == 2
    assert dash["earnings"]["pending_cents"] == 500 and dash["earnings"]["credited_cents"] == 0

    # Un second paiement du même ami (matière ajoutée) ne donne rien de plus.
    session2 = _session_payee("cus_ami2", email, ["l2_penal"])
    _recompense_parrainage(db, session2, ami, email, ["l2_penal"])
    assert db.query(svc.ParrainageRecompense).filter_by(stripe_checkout_session_id=session2["id"]).count() == 0
    assert stripe_calls == []

    # Avant le délai : le job ne crédite rien.
    assert svc.crediter_recompenses(db) == [] and stripe_calls == []

    # Délai passé : crédit automatique sur le client Stripe de Julie.
    rec = db.query(svc.ParrainageRecompense).filter_by(stripe_checkout_session_id=session["id"]).one()
    rec.available_at = svc.utcnow() - timedelta(minutes=1)
    db.commit()
    faites = svc.crediter_recompenses(db)
    assert [x.id for x in faites] == [rec.id]
    assert stripe_calls == [{
        "customer": monde["customer"], "amount": -500, "currency": "eur",
        "description": stripe_calls[0]["description"],
        "metadata": {"parrainage_recompense_id": str(rec.id), "parrain_code": code},
        "idempotency_key": f"navire-parrainage-{rec.id}",
    }]
    db.refresh(rec)
    assert rec.status == "credited" and rec.stripe_balance_transaction_id == "cbtxn_1"

    # Rejouer le job ne crédite pas deux fois ; un remboursement tardif ne
    # retire pas une récompense déjà créditée.
    assert svc.crediter_recompenses(db) == [] and len(stripe_calls) == 1
    assert svc.cancel_for_refund(db, session["payment_intent"]) == 0
    db.close()

    dash = client.get("/parrainage/me/dashboard", headers=_headers(julie)).json()
    assert dash["earnings"]["credited_cents"] == 500
    assert dash["referrals"][0]["reward_status"] == "credited"


def test_job_horaire_envoie_l_email(monde, mails, stripe_calls):
    from app.db.database import SessionLocal
    from app.services import parrainage as svc

    db = SessionLocal()
    ami = _user(db, "ami_job")
    svc.attach_referral(db, monde["code"], user=ami)
    rec = svc.record_reward(db, _session_payee("cus_job", ami.email, ["l1_civil"]), ami, ami.email, ["l1_civil"])
    rec.available_at = svc.utcnow() - timedelta(minutes=1)
    db.commit()
    db.close()

    svc.job_parrainage(SessionLocal)
    assert any(m["to"] == monde["julie"].email and "crédités" in m["subject"] for m in mails)
    assert stripe_calls and stripe_calls[-1]["amount"] == -500


def test_remboursement_avant_credit(monde, stripe_calls):
    from app.db.database import SessionLocal
    from app.services import parrainage as svc

    db = SessionLocal()
    ami = _user(db, "rembourse")
    svc.attach_referral(db, monde["code"], user=ami)
    session = _session_payee("cus_r", ami.email, ["l3_civil"])
    rec = svc.record_reward(db, session, ami, ami.email, ["l3_civil"])
    assert svc.cancel_for_refund(db, session["payment_intent"]) == 1
    rec.available_at = svc.utcnow() - timedelta(minutes=1)
    db.commit()
    assert svc.crediter_recompenses(db) == [] and stripe_calls == []
    db.refresh(rec)
    assert rec.status == "cancelled"
    db.close()


def test_parrain_sans_abonnement_en_cours(monde, stripe_calls):
    """Plus d'inscription en cours au moment du crédit : la récompense reste
    à créditer (last_error), le job réessaiera."""
    from app.db.database import SessionLocal
    from app.db.models import PrepaAdjurisEnrollment
    from app.services import parrainage as svc

    db = SessionLocal()
    ami = _user(db, "ami_fin")
    svc.attach_referral(db, monde["code"], user=ami)
    rec = svc.record_reward(db, _session_payee("cus_f", ami.email, ["l1_civil"]), ami, ami.email, ["l1_civil"])
    rec.available_at = svc.utcnow() - timedelta(minutes=1)
    for e in db.query(PrepaAdjurisEnrollment).filter_by(user_id=monde["julie"].id):
        e.status = "cancelled"
    db.commit()
    assert svc.crediter_recompenses(db) == [] and stripe_calls == []
    db.refresh(rec)
    assert rec.status == "pending" and "Aucune inscription" in rec.last_error
    assert svc.reward_state(rec) == "available"
    db.close()


def test_paiement_sans_compte(monde):
    """Formulaire public : pas de compte, le code voyage dans la metadata."""
    from app.db.database import SessionLocal
    from app.db.models import ParrainageFilleul
    from app.services import parrainage as svc

    email = f"form-{_uid()}@test.fr"
    db = SessionLocal()
    rec = svc.record_reward(
        db, _session_payee("cus_form", email, ["l1_civil"], metadata={"parrain_code": monde["code"]}),
        None, email, ["l1_civil"],
    )
    assert rec is not None and rec.amount_cents == 500
    assert db.query(ParrainageFilleul).filter_by(referred_email=email).one().referred_user_id is None
    db.close()

    r = monde["client"].post("/auth/register", json={
        "username": f"form_{_uid()}", "email": email, "password": "MotDePasse!42",
    })
    assert r.status_code == 200
    db = SessionLocal()
    assert db.query(ParrainageFilleul).filter_by(referred_email=email).one().referred_user_id is not None
    db.close()


def test_metadata_checkout(monde):
    from app.db.database import SessionLocal
    from app.routers.prepa_adjuris import _metadata_parrainage

    db = SessionLocal()
    assert _metadata_parrainage(db, monde["code"]) == {"parrain_code": monde["code"]}
    connecte = _user(db, "connecte")
    assert _metadata_parrainage(db, monde["code"], user=connecte) == {"parrain_code": monde["code"]}
    assert _metadata_parrainage(db, "INCONNU-123") == {}
    db.close()


def test_garde_fous(monde):
    from app.db.database import SessionLocal
    from app.db.models import Ambassador, ParrainageFilleul
    from app.services import ambassadors, parrainage as svc

    client, code = monde["client"], monde["code"]
    db = SessionLocal()

    # Auto-parrainage.
    assert svc.attach_referral(db, code, user=monde["julie"]) is None

    # Élève déjà inscrit : pas un filleul.
    ancien = _eleve(db, "ancien")
    assert svc.attach_referral(db, code, user=ancien) is None

    # Un seul programme par filleul : premier lien retenu.
    proprio = _user(db, "amb")
    amb = Ambassador(user_id=proprio.id, display_name="Amb", prenom="A", nom="B",
                     status="approved", code=f"AMB-{_uid()[:3].upper()}")
    db.add(amb)
    db.commit()
    filleul_amb = _user(db, "filleul_amb")
    assert ambassadors.attach_referral(db, amb.code, user=filleul_amb) is not None
    assert svc.attach_referral(db, code, user=filleul_amb) is None
    assert isinstance(svc.attach_any(db, code, user=filleul_amb), ambassadors.AmbassadorReferral)

    filleul_p = _user(db, "filleul_p")
    assert svc.attach_any(db, code, user=filleul_p) is not None
    assert ambassadors.attach_referral(db, amb.code, user=filleul_p) is None
    assert isinstance(svc.attach_any(db, amb.code, user=filleul_p), ParrainageFilleul)

    # Claim depuis un compte existant.
    existant = _user(db, "existant")
    r = client.post("/ambassadors/claim", headers=_headers(existant), json={"code": code})
    assert r.json() == {"attached": True, "display_name": "Julie"}

    # Pas de frais encaissés : pas de récompense.
    neuf = _user(db, "neuf")
    svc.attach_referral(db, code, user=neuf)
    for session in (
        _session_payee("cus_g1", neuf.email, ["l1_civil"], metadata={"inscription_deja_payee": "1"}),
        {**_session_payee("cus_g2", neuf.email, ["l1_civil"]), "mode": "setup", "amount_total": 0},
    ):
        assert svc.record_reward(db, session, neuf, neuf.email, ["l1_civil"]) is None
    db.close()


# ============================================================
# Console
# ============================================================

def test_console(monde, stripe_calls):
    from app.db.database import SessionLocal
    from app.services import parrainage as svc

    client, admin = monde["client"], _headers(monde["admin"])
    assert client.get("/parrainage/admin/list", headers=_headers(monde["julie"])).status_code == 403

    pid = next(p["id"] for p in client.get("/parrainage/admin/list", headers=admin).json()["items"]
               if p["user_id"] == monde["julie"].id)
    detail = client.get(f"/parrainage/admin/{pid}", headers=admin).json()
    assert detail["stripe_customer_id"] == monde["customer"]

    db = SessionLocal()
    ami = _user(db, "ami_console")
    svc.attach_referral(db, monde["code"], user=ami)
    rec = svc.record_reward(db, _session_payee("cus_c", ami.email, ["l1_civil"]), ami, ami.email, ["l1_civil"])
    db.close()

    items = client.get(f"/parrainage/admin/rewards?status=pending&parrain_id={pid}", headers=admin).json()["items"]
    assert [i["id"] for i in items] == [rec.id]

    # Crédit immédiat depuis la console.
    r = client.post(f"/parrainage/admin/rewards/{rec.id}/credit", headers=admin)
    assert r.status_code == 200 and r.json()["status"] == "credited"
    assert stripe_calls[-1]["amount"] == -500
    assert client.post(f"/parrainage/admin/rewards/{rec.id}/cancel", headers=admin,
                       json={"reason": "test"}).status_code == 400

    resume = client.get("/parrainage/admin/summary", headers=admin).json()
    assert resume["rewards"]["credited_cents"] >= 500

    # Suspension : le lien ne fonctionne plus.
    assert client.post(f"/parrainage/admin/{pid}/suspend", headers=admin).json()["status"] == "suspended"
    assert client.get(f"/ambassadors/ref/{monde['code']}").json() == {"valid": False}
    me = client.get("/parrainage/me", headers=_headers(monde["julie"])).json()
    assert me["status"] == "suspended" and "code" not in me
    assert client.post(f"/parrainage/admin/{pid}/reactivate", headers=admin).json()["status"] == "active"


def test_page_embed(env):
    client = TestClient(env.app)
    for path in ("/parrainage/embed", "/ambassadors/embed/parrainage"):
        r = client.get(path)
        assert r.status_code == 200 and 'API: ""' in r.text and "nv-parr" in r.text


def test_reduction_filleul_en_echec_puis_remboursement(monde, monkeypatch):
    """Crédit Stripe de l'ami en échec : la réduction reste à créditer
    (le job réessaie) ; un remboursement l'annule."""
    import stripe
    from app.db.database import SessionLocal
    from app.routers.subscriptions import _recompense_parrainage
    from app.services import parrainage as svc

    def boom(*a, **k):
        raise stripe.StripeError("Stripe indisponible")
    monkeypatch.setattr(svc.stripe.Customer, "create_balance_transaction", boom)

    db = SessionLocal()
    ami = _user(db, "ami_echec")
    svc.attach_referral(db, monde["code"], user=ami)
    session = _session_payee("cus_echec", ami.email, ["l1_civil"])
    _recompense_parrainage(db, session, ami, ami.email, ["l1_civil"])
    red = db.query(svc.ParrainageReductionFilleul).filter_by(stripe_checkout_session_id=session["id"]).one()
    assert red.status == "pending" and "indisponible" in red.last_error

    assert svc.cancel_for_refund(db, session["payment_intent"]) == 1
    db.refresh(red)
    assert red.status == "cancelled"
    db.close()
