"""
Programme ambassadeur contre une base SQLite, sans Stripe ni Brevo :
candidature → validation console → page /rejoindre → inscription avec le
code → paiement (webhook) → commission → remboursement → virement.

Lancer : pytest tests/test_ambassadors.py
(variables d'environnement factices posées ci-dessous, aucune clé réelle.)
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

IBAN_OK = "FR76 3000 6000 0112 3456 7890 189"


@pytest.fixture(scope="module")
def env():
    import app.main as main
    from app.db.database import Base, engine

    engine.dispose()
    Base.metadata.create_all(bind=engine)
    yield main
    # Le module suivant supprime le fichier SQLite partagé : une connexion
    # restée ouverte dans le pool le verrait en lecture seule.
    engine.dispose()


@pytest.fixture(autouse=True)
def mails(monkeypatch):
    """Capture les emails au lieu de les envoyer."""
    envoyes = []

    def faux_send_mail(to, subject, html):
        envoyes.append({"to": to, "subject": subject})
        return True

    import app.routers.ambassadors as r
    import app.routers.subscriptions as s
    monkeypatch.setattr(r, "send_mail", faux_send_mail)
    monkeypatch.setattr(s, "send_mail", faux_send_mail)
    return envoyes


def _user(db, username, admin=False):
    from app.db.models import User
    u = User(email=f"{username}-{uuid.uuid4().hex[:8]}@test.fr", username=username, is_admin=admin)
    db.add(u)
    db.commit()
    db.refresh(u)
    # Détaché avec ses attributs chargés : utilisable après la fermeture de la
    # session et à travers les commits des appels testés.
    db.expunge(u)
    return u


def _headers(user):
    from app.core.security import create_access_token
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def _session_payee(session_id, customer, email, matieres, **extra):
    return {
        "id": session_id,
        "mode": "payment",
        "payment_status": "paid",
        "amount_total": 2000 * len(matieres),
        "customer": customer,
        "payment_intent": f"pi_{session_id}",
        "metadata": {"matiere_keys": ",".join(matieres), "email": email, **extra.pop("metadata", {})},
        **extra,
    }


@pytest.fixture()
def monde(env):
    """Un admin et un ambassadeur validé, prêt à partager son lien."""
    from app.db.database import SessionLocal
    db = SessionLocal()
    admin = _user(db, "admin", admin=True)
    julie = _user(db, "julie")
    db.close()
    client = TestClient(env.app)

    r = client.post("/ambassadors/apply", headers=_headers(julie), json={
        "display_name": "Julie", "prenom": "Julie", "nom": "Martin",
        "motivation": "Je connais toute ma promo de L2.", "iban": IBAN_OK,
        "accept_terms": True,
    })
    assert r.status_code == 200, r.text
    amb_id = client.get("/ambassadors/admin/list?status=pending", headers=_headers(admin)).json()["items"]
    amb_id = next(a["id"] for a in amb_id if a["user_id"] == julie.id)
    r = client.post(f"/ambassadors/admin/{amb_id}/approve", headers=_headers(admin))
    assert r.status_code == 200, r.text
    return {"client": client, "admin": admin, "julie": julie, "amb_id": amb_id, "code": r.json()["code"]}


# ============================================================
# Candidature et console
# ============================================================

def test_candidature_puis_validation(env, mails):
    from app.db.database import SessionLocal
    db = SessionLocal()
    admin, marc = _user(db, "admin2", admin=True), _user(db, "marc")
    db.close()
    client = TestClient(env.app)

    assert client.get("/ambassadors/me", headers=_headers(marc)).json()["status"] == "none"

    sans_cgu = client.post("/ambassadors/apply", headers=_headers(marc), json={
        "display_name": "Marc", "prenom": "Marc", "nom": "L", "motivation": "Motivé par le droit.",
        "accept_terms": False,
    })
    assert sans_cgu.status_code == 400

    mauvais_iban = client.post("/ambassadors/apply", headers=_headers(marc), json={
        "display_name": "Marc", "prenom": "Marc", "nom": "L", "motivation": "Motivé par le droit.",
        "accept_terms": True, "iban": "FR7630006000011234567890188",
    })
    assert mauvais_iban.status_code == 422

    r = client.post("/ambassadors/apply", headers=_headers(marc), json={
        "display_name": "Marc", "prenom": "Marc", "nom": "L", "motivation": "Motivé par le droit.",
        "accept_terms": True, "iban": IBAN_OK,
    })
    me = r.json()
    assert me["status"] == "pending" and "code" not in me
    assert me["iban_masked"] == "•••• 0189"
    assert any("candidature" in m["subject"] for m in mails)

    # Pas de tableau de bord ni de seconde candidature tant que c'est en attente.
    assert client.get("/ambassadors/me/dashboard", headers=_headers(marc)).status_code == 403
    assert client.post("/ambassadors/apply", headers=_headers(marc), json={
        "display_name": "Marc", "prenom": "Marc", "nom": "L", "motivation": "Motivé par le droit.",
        "accept_terms": True,
    }).status_code == 400

    # La console est réservée aux admins.
    assert client.get("/ambassadors/admin/list", headers=_headers(marc)).status_code == 403

    amb_id = next(
        a["id"] for a in client.get("/ambassadors/admin/list", headers=_headers(admin)).json()["items"]
        if a["user_id"] == marc.id
    )
    r = client.post(f"/ambassadors/admin/{amb_id}/reject", headers=_headers(admin), json={"reason": "Trop tôt."})
    assert r.json()["status"] == "rejected"
    me = client.get("/ambassadors/me", headers=_headers(marc)).json()
    assert me["status"] == "rejected" and me["rejection_reason"] == "Trop tôt."

    # Après un refus, on peut recandidater.
    r = client.post("/ambassadors/apply", headers=_headers(marc), json={
        "display_name": "Marc", "prenom": "Marc", "nom": "L", "motivation": "Je réessaie, plus motivé.",
        "accept_terms": True,
    })
    assert r.json()["status"] == "pending"
    r = client.post(f"/ambassadors/admin/{amb_id}/approve", headers=_headers(admin))
    code = r.json()["code"]
    assert code.startswith("MARC-") and len(code) == 8
    me = client.get("/ambassadors/me", headers=_headers(marc)).json()
    assert me["link"].endswith(f"/rejoindre?ref={code}")
    assert any("Bienvenue" in m["subject"] for m in mails)


# ============================================================
# Page /rejoindre et rattachement
# ============================================================

def test_page_rejoindre_affiche_le_nom(monde):
    client, code = monde["client"], monde["code"]
    r = client.get(f"/ambassadors/ref/{code.lower()}")
    assert r.json() == {"valid": True, "code": code, "display_name": "Julie", "university": None}
    assert client.get("/ambassadors/ref/INCONNU-123").json() == {"valid": False}
    assert client.get("/ambassadors/ref/<script>").json() == {"valid": False}


def test_inscription_avec_code_puis_paiement(monde, mails):
    from app.db.database import SessionLocal
    from app.db.models import User
    from app.routers.subscriptions import _commission_ambassadeur

    client, code = monde["client"], monde["code"]
    email = f"prospect-{uuid.uuid4().hex[:6]}@test.fr"
    r = client.post("/auth/register", json={
        "username": "prospect_l2", "email": email, "password": "MotDePasse!42",
        "university": "Paris 2", "study_level": "L2", "referral_code": code,
    })
    assert r.status_code == 200, r.text

    dash = client.get("/ambassadors/me/dashboard", headers=_headers(monde["julie"])).json()
    assert dash["stats"]["signups"] == 1 and dash["stats"]["paying"] == 0
    assert dash["referrals"][0]["username"] == "prospect_l2"
    assert dash["referrals"][0]["status"] == "signed_up"
    assert "email" not in dash["referrals"][0]  # l'email du filleul reste privé

    # Paiement de 2 matières : 2 × 2 €.
    db = SessionLocal()
    prospect = db.query(User).filter(User.email == email).one()
    session = _session_payee(f"cs_{uuid.uuid4().hex[:6]}", "cus_a", email, ["l2_civil", "l2_admin"])
    _commission_ambassadeur(db, session, prospect, email, ["l2_civil", "l2_admin"])
    _commission_ambassadeur(db, session, prospect, email, ["l2_civil", "l2_admin"])  # event relivré
    db.close()
    assert sum(1 for m in mails if m["to"] == monde["julie"].email and "+4,00" in m["subject"]) == 1

    dash = client.get("/ambassadors/me/dashboard", headers=_headers(monde["julie"])).json()
    assert dash["stats"]["paying"] == 1 and dash["stats"]["matieres"] == 2
    assert dash["earnings"]["pending_cents"] == 400 and dash["earnings"]["available_cents"] == 0
    assert dash["referrals"][0]["status"] == "paid"


def test_paiement_sans_compte_par_le_formulaire(monde):
    """Formulaire public : pas de compte, le code voyage dans la metadata."""
    from app.db.database import SessionLocal
    from app.db.models import AmbassadorReferral
    from app.services import ambassadors as svc

    email = f"form-{uuid.uuid4().hex[:6]}@test.fr"
    db = SessionLocal()
    c = svc.record_commission(
        db, _session_payee("cs_form_1", "cus_form", email, ["l1_civil"],
                           metadata={"ambassador_code": monde["code"]}),
        None, email, ["l1_civil"],
    )
    assert c is not None and c.amount_cents == 200
    ref = db.query(AmbassadorReferral).filter_by(referred_email=email).one()
    assert ref.referred_user_id is None and ref.source == "checkout"
    db.close()

    # Le compte créé ensuite avec la même adresse est rattaché au parrainage.
    r = monde["client"].post("/auth/register", json={
        "username": "form_user", "email": email, "password": "MotDePasse!42",
    })
    assert r.status_code == 200
    db = SessionLocal()
    assert db.query(AmbassadorReferral).filter_by(referred_email=email).one().referred_user_id is not None
    db.close()


def test_garde_fous(monde):
    from app.db.database import SessionLocal
    from app.db.models import AmbassadorReferral, PrepaAdjurisEnrollment
    from app.services import ambassadors as svc

    client, code = monde["client"], monde["code"]
    db = SessionLocal()

    # Auto-parrainage : refusé.
    assert svc.attach_referral(db, code, user=monde["julie"]) is None

    # Élève déjà inscrit à Prép'AdJuris : ce n'est pas un prospect.
    ancien = _user(db, "ancien")
    db.add(PrepaAdjurisEnrollment(user_id=ancien.id, email=ancien.email, matiere_key="l1_civil", status="active"))
    db.commit()
    assert svc.attach_referral(db, code, user=ancien) is None

    # Premier code utilisé = seul code retenu.
    autre = db.query(svc.Ambassador).filter(svc.Ambassador.id != monde["amb_id"],
                                             svc.Ambassador.status == "approved").first()
    neuf = _user(db, "neuf")
    premier = svc.attach_referral(db, code, user=neuf)
    if autre is not None:
        assert svc.attach_referral(db, autre.code, user=neuf).id == premier.id
    assert db.query(AmbassadorReferral).filter_by(referred_user_id=neuf.id).count() == 1

    # Inscription déjà payée / rien encaissé : pas de commission.
    for session in (
        _session_payee("cs_g1", "cus_g1", neuf.email, ["l1_civil"], metadata={"inscription_deja_payee": "1"}),
        {**_session_payee("cs_g2", "cus_g2", neuf.email, ["l1_civil"]), "mode": "setup", "amount_total": 0},
    ):
        assert svc.record_commission(db, session, neuf, neuf.email, ["l1_civil"]) is None

    # Lien suspendu : plus de nouveau filleul ni de commission.
    r = client.post(f"/ambassadors/admin/{monde['amb_id']}/suspend", headers=_headers(monde["admin"]))
    assert r.json()["status"] == "suspended"
    assert client.get(f"/ambassadors/ref/{code}").json() == {"valid": False}
    db.expire_all()
    assert svc.record_commission(
        db, _session_payee("cs_g3", "cus_g3", neuf.email, ["l1_civil"]), neuf, neuf.email, ["l1_civil"]
    ) is None
    db.close()


def test_claim_depuis_un_compte_existant(monde):
    from app.db.database import SessionLocal
    db = SessionLocal()
    existant = _user(db, "existant")
    db.close()
    r = monde["client"].post("/ambassadors/claim", headers=_headers(existant), json={"code": monde["code"]})
    assert r.json() == {"attached": True, "display_name": "Julie"}


# ============================================================
# Remboursement et virement
# ============================================================

def test_remboursement_puis_virement(monde):
    from app.db.database import SessionLocal
    from app.services import ambassadors as svc

    client, admin = monde["client"], _headers(monde["admin"])
    db = SessionLocal()
    p1, p2 = _user(db, "p1"), _user(db, "p2")
    for p in (p1, p2):
        svc.attach_referral(db, monde["code"], user=p)
    c1 = svc.record_commission(db, _session_payee("cs_r1", "cus_r1", p1.email, ["l3_civil"]), p1, p1.email, ["l3_civil"])
    c2 = svc.record_commission(db, _session_payee("cs_r2", "cus_r2", p2.email, ["l3_civil"]), p2, p2.email, ["l3_civil"])

    # Remboursement : la commission est annulée.
    assert svc.cancel_for_refund(db, "pi_cs_r1") == 1
    db.refresh(c1)
    assert c1.status == "cancelled"

    # Encore dans le délai de rétractation : pas versable sans force.
    r = client.post("/ambassadors/admin/commissions/mark-paid", headers=admin, json={"ids": [c2.id]})
    assert r.json()["paid"] == [] and r.json()["skipped"][0]["status"] == "pending"

    # Délai écoulé : la commission apparaît dans « à verser ».
    c2.available_at = svc.utcnow() - timedelta(minutes=1)
    db.commit()
    a_verser = client.get(
        f"/ambassadors/admin/commissions?status=available&ambassador_id={monde['amb_id']}", headers=admin
    ).json()
    assert [i["id"] for i in a_verser["items"]] == [c2.id]
    assert a_verser["items"][0]["iban"] == IBAN_OK.replace(" ", "")

    r = client.post("/ambassadors/admin/commissions/mark-paid", headers=admin,
                    json={"ids": [c2.id], "reference": "VIR-2026-11"})
    assert r.json() == {"paid": [c2.id], "skipped": [], "total_cents": 200}
    db.refresh(c2)
    assert c2.status == "paid" and c2.paid_reference == "VIR-2026-11"

    # Une commission versée n'est plus annulée par un remboursement.
    assert svc.cancel_for_refund(db, "pi_cs_r2") == 0
    gains = svc.earnings(db, monde["amb_id"])
    assert gains["paid_cents"] == 200 and gains["cancelled_cents"] == 200
    db.close()

    resume = client.get("/ambassadors/admin/summary", headers=admin).json()
    assert resume["commissions"]["paid_cents"] >= 200


def test_pages_embed_servies_par_l_api(env):
    client = TestClient(env.app)
    for page in ("rejoindre", "ambassadeur"):
        r = client.get(f"/ambassadors/embed/{page}")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
        # Appels relatifs : la page parle à l'API qui la sert.
        assert 'API: ""' in r.text and "onrender.com" not in r.text.split("CONFIG")[1][:200]
    assert client.get("/ambassadors/embed/console_ambassadeurs").status_code == 404
    assert client.get("/ambassadors/embed/..%2Fapp").status_code == 404
