"""
Parcours complet Prép'AdJuris contre une base SQLite et un faux Stripe :
agenda → devis → checkout → webhook → échéancier Stripe → modification de
l'agenda (aperçu puis réel) → impayé → suspension → régularisation.

Lancer : pytest tests/test_prepa_adjuris_parcours.py
(variables d'environnement factices posées ci-dessous, aucune clé réelle.)
"""

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

for k, v in {
    "OPENAI_API_KEY": "x", "BREVO_API_KEY_MEOLES": "x",
    "STRIPE_SECRET_KEY_MEOLES": "x", "STRIPE_WEBHOOK_SECRET_MEOLES": "x",
    "STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_WEBHOOK_SECRET": "whsec_x",
    "DATABASE_URL": "sqlite:///./test_prepa_adjuris.db",
}.items():
    os.environ.setdefault(k, v)

from fastapi.testclient import TestClient  # noqa: E402

PARIS = ZoneInfo("Europe/Paris")
ADMIN = {"X-Admin-Code": "THORKISHERE"}


class FauxStripe:
    """Enregistre les appels Stripe utiles au parcours."""

    def __init__(self):
        self.sessions = []
        self.schedules = {}
        self.modifs = []
        self.credits = []
        self.factures_ouvertes = []

    # checkout.Session.create
    def creer_session(self, **params):
        self.sessions.append(params)
        return SimpleNamespace(url="https://checkout.stripe.test/s", id="cs_1",
                               expires_at=params.get("expires_at"))

    # SubscriptionSchedule
    def creer_schedule(self, from_subscription):
        params = self.sessions[-1]
        anchor = params["subscription_data"]["billing_cycle_anchor"]
        debut = int(datetime.now(timezone.utc).timestamp())
        sched = {
            "id": "sub_sched_1", "status": "active",
            "current_phase": {"start_date": debut, "end_date": anchor},
            "phases": [{
                "start_date": debut, "end_date": anchor,
                "items": [{"price": i["price"], "quantity": i["quantity"]}
                          for i in params["line_items"][1::2]],
                "billing_cycle_anchor": None,
            }],
        }
        self.schedules[sched["id"]] = sched
        return sched

    def retrieve_schedule(self, sid):
        return self.schedules[sid]

    def modify_schedule(self, sid, **params):
        self.modifs.append(params)
        sched = self.schedules[sid]
        premiere = params["phases"][0]
        phases, debut = [], premiere["start_date"]
        for p in params["phases"]:
            phases.append({**p, "start_date": p.get("start_date", debut)})
            debut = p["end_date"]
        sched["phases"] = phases
        return sched

    def retrieve_subscription(self, sid):
        return {"id": sid, "schedule": None}


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    import stripe

    db_path = "./test_prepa_adjuris.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    import app.main as main
    from app.db.database import Base, engine, SessionLocal
    Base.metadata.create_all(bind=engine)

    faux = FauxStripe()
    mp = pytest.MonkeyPatch()
    mp.setattr(stripe.checkout.Session, "create", staticmethod(faux.creer_session))
    mp.setattr(stripe.SubscriptionSchedule, "create", staticmethod(faux.creer_schedule))
    mp.setattr(stripe.SubscriptionSchedule, "retrieve", staticmethod(faux.retrieve_schedule))
    mp.setattr(stripe.SubscriptionSchedule, "modify", staticmethod(faux.modify_schedule))
    mp.setattr(stripe.Subscription, "retrieve", staticmethod(faux.retrieve_subscription))
    mp.setattr(stripe.Customer, "create_balance_transaction",
               staticmethod(lambda cid, **kw: faux.credits.append((cid, kw))))
    mp.setattr(stripe.Invoice, "list",
               staticmethod(lambda **kw: {"data": list(faux.factures_ouvertes)}))
    envois = []
    import app.services.email as email_mod
    mp.setattr(email_mod, "send_mail", lambda to, subject, html: envois.append((to, subject)) or True)
    import app.routers.subscriptions as subs
    mp.setattr(subs, "send_mail", lambda to, subject, html: envois.append((to, subject)) or True)

    # Le lifespan (bot Discord, scheduler) n'est pas démarré : TestClient sans `with`.
    client = TestClient(main.app)
    yield SimpleNamespace(client=client, faux=faux, envois=envois, Session=SessionLocal, subs=subs)
    mp.undo()
    if os.path.exists(db_path):
        os.remove(db_path)


def test_parcours_complet(env):
    c, faux = env.client, env.faux
    now = datetime.now(timezone.utc)

    # 1. Agenda : série L3 (mardi 21 h) sur les 10 prochaines semaines.
    debut = (now + timedelta(days=1)).date()
    fin = (now + timedelta(days=70)).date()
    serie = {"niveau": "L3", "jour_semaine": 1, "heure": "21:00", "du": debut.isoformat(),
             "au": fin.isoformat(), "matieres": ["L3_droit_des_societes"]}
    r = c.post("/prepa/adjuris/admin/agenda/serie?simulation=true", json=serie, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert r.json()["simulation"] is True and r.json()["crees"] == 10
    assert c.get("/prepa/adjuris/admin/agenda", headers=ADMIN).json()["total"] == 0  # rien écrit

    r = c.post("/prepa/adjuris/admin/agenda/serie", json=serie, headers=ADMIN)
    assert r.json()["crees"] == 10
    # Rejouer la série ne crée pas de doublon
    assert c.post("/prepa/adjuris/admin/agenda/serie", json=serie, headers=ADMIN).json()["ignores"] == 10

    agenda = c.get("/prepa/adjuris/admin/agenda", headers=ADMIN).json()
    seances = [s for s in agenda["items"] if s["matiere_key"] == "L3_droit_des_societes"]
    assert len(seances) == 10
    premiere = datetime.fromisoformat(seances[0]["date_debut"])
    local = (premiere if premiere.tzinfo else premiere.replace(tzinfo=timezone.utc)).astimezone(PARIS)
    assert (local.weekday(), local.hour) == (1, 21)

    # 2. Devis public
    r = c.get("/prepa/adjuris/echeancier?matieres=L3_droit_des_societes")
    assert r.status_code == 200, r.text
    devis = r.json()
    assert devis["inscription"]["total_cents"] == 2000
    assert sum(p["seances"] for p in devis["prelevements"]) == 9  # 10 séances - 1 prépayée

    # Agenda vide (L1 pas encore saisi) : le paiement reste possible, sur le
    # créneau habituel (jeudi 21 h), et seuls les 20 € sont encaissés.
    r = c.get("/prepa/adjuris/echeancier?matieres=L1_intro_au_droit")
    assert r.status_code == 200, r.text
    prep = datetime.fromisoformat(r.json()["inscription"]["seances_prepayees"]["L1_intro_au_droit"])
    assert prep.astimezone(PARIS).weekday() == 3 and prep.astimezone(PARIS).hour == 21
    assert r.json()["inscription"]["total_cents"] == 2000

    # M1 sans Prices Stripe : refus explicite
    assert c.get("/prepa/adjuris/echeancier?matieres=M1_distribution").status_code == 400

    # 3. Checkout public
    r = c.post("/prepa/adjuris/checkout", json={
        "prenom": "Ada", "nom": "L", "email": "ada@example.com",
        "niveau": "L3", "matieres": ["L3_droit_des_societes"],
    })
    assert r.status_code == 200, r.text
    params = faux.sessions[-1]
    assert params["subscription_data"]["proration_behavior"] == "none"
    assert "trial_end" not in params["subscription_data"]
    assert len(params["line_items"]) == 2  # inscription (20 €) + mensuel
    assert params["metadata"]["echeancier"]
    assert len(params["custom_text"]["submit"]["message"]) <= 1200

    # 4. Webhook checkout.session.completed
    session = {
        "metadata": params["metadata"], "subscription": "sub_1", "customer": "cus_1",
        "customer_email": "ada@example.com", "created": int(now.timestamp()),
    }
    env.subs._handle_prepa_adjuris_checkout(
        db := env.Session(), session, ["L3_droit_des_societes"]
    )
    db.close()
    assert faux.modifs, "l'échéancier Stripe doit être créé"
    phases = faux.modifs[-1]["phases"]
    assert faux.modifs[-1]["end_behavior"] == "cancel"
    assert all(p.get("billing_cycle_anchor") == "phase_start" for p in phases[1:])
    total_phases = sum(p["items"][0]["quantity"] for p in phases[1:])
    assert total_phases == 9

    fact = c.get("/prepa/adjuris/admin/facturation", headers=ADMIN).json()
    assert fact["total"] == 1 and fact["items"][0]["echeancier_statut"] == "ok"
    eid = fact["items"][0]["enrollment_id"]

    # 5. Annulation d'une séance future : aperçu, puis réel
    cible = seances[5]["id"]
    r = c.post(f"/prepa/adjuris/admin/agenda/seances/{cible}/annuler?simulation=true", headers=ADMIN)
    apercu = r.json()
    assert apercu["resume"]["nb_eleves"] == 1
    assert apercu["impacts"][0]["type"] == "ajustement" and apercu["impacts"][0]["delta_cents"] == -2000
    assert c.get("/prepa/adjuris/admin/agenda", headers=ADMIN).json()["items"][5]["statut"] == "prevue"

    nb_modifs = len(faux.modifs)
    r = c.post(f"/prepa/adjuris/admin/agenda/seances/{cible}/annuler", headers=ADMIN)
    assert r.json()["impacts"][0]["delta_cents"] == -2000
    assert len(faux.modifs) == nb_modifs + 1
    assert sum(p["items"][0]["quantity"] for p in faux.modifs[-1]["phases"][1:]) == 8

    # 6. Impayé : accès conservé, puis suspension à J+7, puis régularisation
    from app.services import prepa_adjuris_facturation as fa
    from app.db.models import PrepaAdjurisEnrollment
    db = env.Session()
    fa.marquer_impaye(db, "sub_1", {"hosted_invoice_url": "https://pay"})
    e = db.get(PrepaAdjurisEnrollment, eid)
    assert e.status == "payment_failed"
    assert any("n'a pas pu" in s for _, s in env.envois)

    faux.factures_ouvertes = [{"hosted_invoice_url": "https://pay"}]
    assert fa.traiter_impayes(db, datetime.now(timezone.utc) + timedelta(days=5, hours=1))["relances"] == 1
    assert fa.traiter_impayes(db, datetime.now(timezone.utc) + timedelta(days=7, hours=1))["suspensions"] == 1
    db.refresh(e)
    assert e.status == "suspendu"

    faux.factures_ouvertes = []
    fa.regulariser(db, "sub_1")
    db.refresh(e)
    assert e.status == "active" and e.impaye_depuis is None
    db.close()

    # 7. Alertes et lien de paiement manuel
    al = c.get("/prepa/adjuris/admin/alertes", headers=ADMIN).json()
    assert any(a["type"] == "prix_stripe" and a["matiere_key"] == "M1_distribution" for a in al["items"])

    # Lien manuel : inscription antidatée, 20 € déjà réglés → pas de one_time.
    veille = seances[0]["date_debut"]
    r = c.post("/prepa/adjuris/admin/lien-paiement", headers=ADMIN, json={
        "email": "bob@example.com", "matieres": ["L3_droit_des_societes"],
        "inscrit_le": (datetime.fromisoformat(veille).replace(tzinfo=timezone.utc) - timedelta(hours=1)).isoformat(),
        "inscription_deja_payee": True,
    })
    assert r.status_code == 200, r.text
    params = faux.sessions[-1]
    assert len(params["line_items"]) == 1                     # mensuel seulement
    assert params["metadata"]["inscription_deja_payee"] == "1"
    assert r.json()["inscription"]["total_cents"] == 0
    assert sum(p["seances"] for p in r.json()["prelevements"]) == 8   # 9 prévues - 1 prépayée
