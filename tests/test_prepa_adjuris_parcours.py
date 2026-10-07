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
        self.abonnements = []
        self.clients = []
        self.cles = {}

    # checkout.Session.create
    def creer_session(self, **params):
        self.sessions.append(params)
        return SimpleNamespace(url="https://checkout.stripe.test/s", id="cs_1",
                               expires_at=params.get("expires_at"))

    # Subscription.create : même clé d'idempotence → même abonnement
    def creer_abonnement(self, idempotency_key=None, **params):
        if idempotency_key in self.cles:
            return self.cles[idempotency_key]
        sub = {"id": f"sub_{len(self.abonnements) + 1}", **params}
        self.abonnements.append(sub)
        self.cles[idempotency_key] = sub
        return sub

    # SubscriptionSchedule
    def creer_schedule(self, from_subscription):
        sub = next(s for s in self.abonnements if s["id"] == from_subscription)
        anchor = sub["billing_cycle_anchor"]
        debut = int(datetime.now(timezone.utc).timestamp())
        sched = {
            "id": f"sub_sched_{len(self.schedules) + 1}", "status": "active",
            "current_phase": {"start_date": debut, "end_date": anchor},
            "phases": [{
                "start_date": debut, "end_date": anchor,
                "items": [{"price": i["price"], "quantity": i["quantity"]} for i in sub["items"]],
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
    mp.setattr(stripe.Subscription, "create", staticmethod(faux.creer_abonnement))
    mp.setattr(stripe.Customer, "create",
               staticmethod(lambda **kw: faux.clients.append(kw) or {"id": f"cus_setup_{len(faux.clients)}"}))
    mp.setattr(stripe.Customer, "modify", staticmethod(lambda cid, **kw: {"id": cid, **kw}))
    mp.setattr(stripe.PaymentIntent, "retrieve", staticmethod(lambda i: {"id": i, "payment_method": "pm_carte"}))
    mp.setattr(stripe.SetupIntent, "retrieve", staticmethod(lambda i: {"id": i, "payment_method": "pm_setup"}))
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
    # Paiement simple : 20 €, carte enregistrée, pas de « puis X € par mois »
    assert params["mode"] == "payment"
    assert "subscription_data" not in params
    assert params["line_items"] == [{"price": params["line_items"][0]["price"], "quantity": 1}]
    assert params["payment_intent_data"]["setup_future_usage"] == "off_session"
    assert params["customer_creation"] == "always"
    assert params["metadata"]["echeancier"]
    assert len(params["custom_text"]["submit"]["message"]) <= 1200
    # Identité : prénom et nom pré-remplis depuis le formulaire, téléphone demandé
    champs = {c["key"]: c for c in params["custom_fields"]}
    assert champs["prenom"]["text"]["default_value"] == "Ada"
    assert champs["nom"]["text"]["default_value"] == "L"
    assert params["phone_number_collection"] == {"enabled": True}

    # 4. Webhook checkout.session.completed : crée l'abonnement sur la carte
    session = {
        "id": "cs_ada", "mode": "payment", "payment_intent": "pi_1",
        "metadata": params["metadata"], "subscription": None, "customer": "cus_1",
        "customer_email": "ada@example.com", "created": int(now.timestamp()),
        "customer_details": {"email": "ada@example.com", "phone": "+33612345678"},
        "custom_fields": [{"key": "prenom", "text": {"value": "Ada"}},
                          {"key": "nom", "text": {"value": "Lovelace"}}],
    }
    env.subs._handle_prepa_adjuris_checkout(
        db := env.Session(), session, ["L3_droit_des_societes"]
    )
    db.close()
    assert len(faux.abonnements) == 1
    sub = faux.abonnements[0]
    assert sub["id"] == "sub_1" and sub["customer"] == "cus_1"
    assert sub["default_payment_method"] == "pm_carte"
    assert sub["proration_behavior"] == "none"
    assert sub["billing_cycle_anchor"] > now.timestamp()
    assert "trial_end" not in sub
    assert faux.modifs, "l'échéancier Stripe doit être créé"
    fiche = next(i for i in c.get("/prepa/adjuris/admin/inscriptions", headers=ADMIN).json()["items"]
                 if i["email"] == "ada@example.com")
    assert (fiche["prenom"], fiche["nom"], fiche["telephone"]) == ("Ada", "Lovelace", "+33612345678")

    # Relivraison du même event : ni second abonnement, ni doublon en base
    nb_modifs = len(faux.modifs)
    env.subs._handle_prepa_adjuris_checkout(
        db := env.Session(), dict(session), ["L3_droit_des_societes"]
    )
    db.close()
    assert len(faux.abonnements) == 1 and len(faux.modifs) == nb_modifs
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
    assert params["mode"] == "setup"                          # rien encaissé, carte enregistrée
    # Lien manuel : pas de formulaire avant, prénom et nom à saisir sur Stripe
    assert all("default_value" not in c["text"] for c in params["custom_fields"])
    assert params["phone_number_collection"] == {"enabled": True}
    assert "line_items" not in params and "customer_email" not in params
    assert params["customer"] == "cus_setup_1" and params["currency"] == "eur"
    assert params["metadata"]["inscription_deja_payee"] == "1"
    assert r.json()["inscription"]["total_cents"] == 0
    assert sum(p["seances"] for p in r.json()["prelevements"]) == 8   # 9 prévues - 1 prépayée

    # Webhook du lien manuel : abonnement sur la carte du SetupIntent
    env.subs._handle_prepa_adjuris_checkout(db := env.Session(), {
        "id": "cs_bob", "mode": "setup", "setup_intent": "seti_1", "subscription": None,
        "customer": "cus_setup_1", "metadata": params["metadata"],
        "customer_email": None, "created": int(now.timestamp()),
        "customer_details": {"email": "bob@example.com", "phone": "+33700000000"},
        "custom_fields": [{"key": "prenom", "text": {"value": "Bob"}},
                          {"key": "nom", "text": {"value": "Martin"}}],
    }, ["L3_droit_des_societes"])
    db.close()
    sub = faux.abonnements[-1]
    assert sub["customer"] == "cus_setup_1" and sub["default_payment_method"] == "pm_setup"
    fact = c.get("/prepa/adjuris/admin/facturation", headers=ADMIN).json()
    bob = [i for i in fact["items"] if i["email"] == "bob@example.com"]
    assert len(bob) == 1 and bob[0]["echeancier_statut"] == "ok"
    # La fiche est créée depuis la page Stripe : la console affiche son nom
    etu = c.get("/prepa/adjuris/admin/etudiants", headers=ADMIN).json()["items"]
    b = next(e for e in etu if e["email"] == "bob@example.com")
    assert (b["prenom"], b["nom"], b["telephone"]) == ("Bob", "Martin", "+33700000000")


def test_recurrence(env):
    c = env.client
    now = datetime.now(timezone.utc)
    debut = (now + timedelta(days=1)).date()
    fin = (now + timedelta(days=28)).date()
    # L2, lundi 21 h, les 3 matières du niveau
    r = c.post("/prepa/adjuris/admin/agenda/serie", headers=ADMIN, json={
        "niveau": "L2", "jour_semaine": 0, "heure": "21:00",
        "du": debut.isoformat(), "au": fin.isoformat(),
    })
    assert r.status_code == 200, r.text
    items = c.get("/prepa/adjuris/admin/agenda?niveau=L2", headers=ADMIN).json()["items"]
    penal = [s for s in items if s["matiere_key"] == "L2_droit_penal"]
    assert len(penal) == 4
    ref = penal[1]
    ref_local = datetime.fromisoformat(ref["date_debut"]).astimezone(PARIS)
    cible = (ref_local + timedelta(days=1)).replace(hour=20, minute=0, tzinfo=None)

    corps = {"action": "deplacer", "portee": "suivantes", "nouvelle_date": cible.isoformat()}
    url = f"/prepa/adjuris/admin/agenda/seances/{ref['id']}/recurrence"
    r = c.post(url + "?simulation=true", headers=ADMIN, json=corps)
    assert r.status_code == 200, r.text
    assert r.json()["concernees"] == 3 and r.json()["simulation"] is True
    avant = c.get("/prepa/adjuris/admin/agenda?matiere=L2_droit_penal", headers=ADMIN).json()["items"]
    assert [s["date_debut"] for s in avant] == [s["date_debut"] for s in penal]  # rien écrit

    r = c.post(url, headers=ADMIN, json=corps)
    assert r.json()["concernees"] == 3
    apres = c.get("/prepa/adjuris/admin/agenda?matiere=L2_droit_penal", headers=ADMIN).json()["items"]
    locales = [datetime.fromisoformat(s["date_debut"]).astimezone(PARIS) for s in apres]
    assert (locales[0].weekday(), locales[0].hour) == (0, 21)            # la première ne bouge pas
    assert all((d.weekday(), d.hour) == (1, 20) for d in locales[1:])     # les suivantes : mardi 20 h
    # Les autres matières du niveau n'ont pas bougé
    admin_ = c.get("/prepa/adjuris/admin/agenda?matiere=L2_droit_administratif", headers=ADMIN).json()["items"]
    assert all(datetime.fromisoformat(s["date_debut"]).astimezone(PARIS).weekday() == 0 for s in admin_)

    # Annuler toute la récurrence du niveau (lundi 21 h) depuis une séance d'une autre matière
    r = c.post(f"/prepa/adjuris/admin/agenda/seances/{admin_[0]['id']}/recurrence", headers=ADMIN,
               json={"action": "annuler", "portee": "toutes", "tout_le_niveau": True})
    assert r.json()["concernees"] == 4 * 2 + 1   # administratif + obligations (4 chacun) + 1ère de pénal
    admin_ = c.get("/prepa/adjuris/admin/agenda?matiere=L2_droit_administratif", headers=ADMIN).json()["items"]
    assert all(s["statut"] == "annulee" for s in admin_)


def test_webhook_tardif(env, monkeypatch):
    """Webhook relivré après le prélèvement du premier mois : l'abonnement
    démarre au mois suivant au lieu d'échouer (date d'ancrage passée)."""
    from app.services import prepa_adjuris_facturation as fa
    from app.services.prepa_adjuris_billing import date_prelevement, parse_mois

    now = datetime.now(timezone.utc)
    db = env.Session()
    devis = fa.calculer_devis(db, ["L3_droit_des_societes"], now, facturable_apres=now)
    db.close()
    mois = sorted(devis[0].mois)
    assert len(mois) >= 2
    apres_premier = date_prelevement(parse_mois(mois[0])) + timedelta(minutes=1)
    monkeypatch.setattr(env.subs, "utcnow", lambda: apres_premier)

    sub_id = env.subs._creer_abonnement_adjuris(
        {"id": "cs_tard", "mode": "payment", "payment_intent": "pi_t", "customer": "cus_t"},
        devis, {"matiere_keys": "L3_droit_des_societes"},
    )
    sub = next(s for s in env.faux.abonnements if s["id"] == sub_id)
    assert sub["billing_cycle_anchor"] == int(date_prelevement(parse_mois(mois[1])).timestamp())
    assert sub["items"][0]["quantity"] == max(1, devis[0].mois[mois[1]])


def test_agenda_et_paiement_simple(env):
    """Un cours déplacé d'un mois à l'autre :
      1. pendant que le lien de paiement est ouvert → le webhook facture
         l'agenda réel, pas le devis affiché ;
      2. après l'inscription → l'échéancier Stripe est mis à jour."""
    from app.db.models import PrepaAdjurisEnrollment
    from app.services.prepa_adjuris_billing import Echeancier, date_prelevement, parse_mois
    from sqlalchemy import select

    c, faux = env.client, env.faux
    now = datetime.now(timezone.utc)
    cle = "L1_droit_ijae"
    r = c.post("/prepa/adjuris/admin/agenda/serie", headers=ADMIN, json={
        "niveau": "L1", "jour_semaine": 3, "heure": "21:00",
        "du": (now + timedelta(days=1)).date().isoformat(),
        "au": (now + timedelta(days=100)).date().isoformat(), "matieres": [cle],
    })
    assert r.status_code == 200, r.text

    r = c.post("/prepa/adjuris/checkout", json={
        "prenom": "Carl", "nom": "D", "email": "carl@example.com",
        "niveau": "L1", "matieres": [cle],
    })
    assert r.status_code == 200, r.text
    params = faux.sessions[-1]
    assert params["mode"] == "payment"
    from app.services.prepa_adjuris_billing import devis_depuis_metadata
    devis = devis_depuis_metadata(params["metadata"]["echeancier"],
                                  datetime.fromisoformat(params["metadata"]["inscrit_le"]))[0]

    # Un mois avec des séances, suivi d'un autre mois couvert
    mois = sorted(devis.mois)
    m = next(k for k, suiv in zip(mois, mois[1:]) if devis.mois[k] > 0)
    suivant = mois[mois.index(m) + 1]
    seances = c.get(f"/prepa/adjuris/admin/agenda?matiere={cle}", headers=ADMIN).json()["items"]
    dans_m = [s for s in seances
              if datetime.fromisoformat(s["date_debut"]).astimezone(PARIS).strftime("%Y-%m") == m
              and datetime.fromisoformat(s["date_debut"]) > devis.seance_prepayee]
    cours = dans_m[-1]
    a, mo = parse_mois(suivant)
    nouvelle = datetime(a, mo, 1, 21, 0)  # heure de Paris

    # 1. Déplacé pendant que le lien est ouvert
    r = c.patch(f"/prepa/adjuris/admin/agenda/seances/{cours['id']}", headers=ADMIN,
                json={"date_debut": nouvelle.isoformat()})
    assert r.status_code == 200, r.text
    env.subs._handle_prepa_adjuris_checkout(db := env.Session(), {
        "id": "cs_carl", "mode": "payment", "payment_intent": "pi_c", "subscription": None,
        "customer": "cus_carl", "metadata": params["metadata"],
        "customer_email": "carl@example.com", "created": int(now.timestamp()),
    }, [cle])
    e = db.execute(select(PrepaAdjurisEnrollment).where(
        PrepaAdjurisEnrollment.stripe_customer_id == "cus_carl")).scalar_one()
    stocke = Echeancier.from_dict(e.echeancier)
    assert stocke.mois[m] == devis.mois[m] - 1
    assert stocke.mois[suivant] == devis.mois[suivant] + 1
    assert e.echeancier_statut == "ok"
    sched = faux.schedules[e.stripe_schedule_id]
    db.close()

    # Échéancier Stripe poussé : la phase ouverte à la fin de m porte m.
    def par_mois(sched):
        out = {}
        for p in sched["phases"][1:]:
            for k in mois:
                if int(date_prelevement(parse_mois(k)).timestamp()) == p["start_date"]:
                    out[k] = p["items"][0]["quantity"]
        return out
    assert par_mois(sched)[m] == devis.mois[m] - 1

    # 2. Remis à sa place après l'inscription : Stripe suit
    r = c.patch(f"/prepa/adjuris/admin/agenda/seances/{cours['id']}", headers=ADMIN,
                json={"date_debut": cours["date_debut"]})
    assert r.status_code == 200, r.text
    assert par_mois(sched)[m] == devis.mois[m]
    assert par_mois(sched)[suivant] == devis.mois[suivant]


def test_checkout_sans_champs_identite_si_stripe_refuse(env, monkeypatch):
    """Si Stripe refusait les champs prénom/nom/téléphone, la page de paiement
    s'ouvre quand même, sans eux."""
    import stripe
    appels = []

    def creer(**params):
        appels.append(params)
        if "custom_fields" in params:
            raise stripe.InvalidRequestError("custom_fields refusé", "custom_fields")
        return env.faux.creer_session(**params)

    monkeypatch.setattr(stripe.checkout.Session, "create", staticmethod(creer))
    r = env.client.post("/prepa/adjuris/checkout", json={
        "prenom": "Eve", "nom": "R", "email": "eve@example.com",
        "niveau": "L3", "matieres": ["L3_droit_des_suretes"],
    })
    assert r.status_code == 200, r.text
    assert len(appels) == 2 and "custom_fields" not in appels[1]
    assert appels[1]["mode"] == "payment"
