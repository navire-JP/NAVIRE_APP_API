"""
Cours lancés depuis Discord (#cours-start) et extras, contre une base SQLite :
lancement → extra refusé avant 1 h 10 → extra ajouté → un élève retiré →
fin du cours → 10 € facturés aux élèves restés (Stripe simulé) → rémunération
du professeur.

Lancer : pytest tests/test_cours_live.py
"""

import os
import uuid
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

MATIERE = "L2_droit_administratif"


@pytest.fixture(scope="module")
def env():
    import app.main as main
    from app.db.database import Base, engine

    engine.dispose()
    Base.metadata.create_all(bind=engine)
    yield main
    engine.dispose()


class _FauxStripe:
    def __init__(self):
        self.items = []

    def create(self, **kw):
        self.items.append(kw)
        return type("Item", (), {"id": f"ii_{len(self.items)}"})()


@pytest.fixture()
def monde(env, monkeypatch):
    """Un groupe d'administratif, un professeur lié à Discord, trois élèves :
    deux abonnés Stripe (dont un avec son nom saisi à l'inscription), un
    accès accordé à la main."""
    import stripe
    from app.db.database import SessionLocal
    from app.db.models import (
        PrepaAdjurisAffectation, PrepaAdjurisClasse, PrepaAdjurisCours, PrepaAdjurisCoursEleve,
        PrepaAdjurisEnrollment, PrepaAdjurisInscription, PrepaAdjurisProfesseur, User,
    )

    faux = _FauxStripe()
    monkeypatch.setattr(stripe.InvoiceItem, "create", faux.create)

    db = SessionLocal()
    for m in (PrepaAdjurisCoursEleve, PrepaAdjurisCours, PrepaAdjurisAffectation, PrepaAdjurisProfesseur):
        db.query(m).delete()
    db.query(PrepaAdjurisEnrollment).filter(PrepaAdjurisEnrollment.matiere_key == MATIERE).delete()
    db.query(PrepaAdjurisClasse).delete()
    db.commit()

    def user(name, discord_id=None):
        u = User(email=f"{name}-{uuid.uuid4().hex[:8]}@test.fr", username=name, discord_id=discord_id)
        db.add(u)
        db.commit()
        return u

    tag = uuid.uuid4().hex[:6]
    prof_u = user("prof", f"9{tag}")
    alice, bob, chloe = user("alice", f"1{tag}"), user("bob"), user("chloe")
    db.add(PrepaAdjurisInscription(prenom="Alice", nom="Martin", email=alice.email, niveau="L2", matieres=[MATIERE]))
    old = datetime.now(timezone.utc) - timedelta(days=30)
    for u, abonne in ((alice, True), (bob, True), (chloe, False)):
        db.add(PrepaAdjurisEnrollment(
            user_id=u.id, email=u.email, matiere_key=MATIERE, status="active", inscrit_le=old,
            source="stripe" if abonne else "admin",
            stripe_customer_id=f"cus_{u.username}" if abonne else None,
            stripe_subscription_id=f"sub_{u.username}" if abonne else None,
        ))
    classe = PrepaAdjurisClasse(matiere_key=MATIERE, nom="Groupe A")
    db.add(classe)
    db.commit()
    prof = PrepaAdjurisProfesseur(user_id=prof_u.id, prenom="Paul", nom="Prof")
    prof.affectations.append(PrepaAdjurisAffectation(
        classe_id=classe.id, remu_seance_cents=0, remu_etudiant_cents=0, remu_extra_etudiant_cents=500,
    ))
    db.add(prof)
    db.commit()
    out = {"prof_discord": prof_u.discord_id, "alice_discord": alice.discord_id,
           "classe_id": classe.id, "stripe": faux}
    db.close()
    return out


def _bot():
    from app.core.config import BOT_SECRET
    return {"x-bot-secret": BOT_SECRET}


def test_reserve_aux_professeurs(env, monde):
    c = TestClient(env.app)
    r = c.get(f"/discord/cours/professeur/{monde['alice_discord']}", headers=_bot()).json()
    assert r["ok"] is False and "professeurs" in r["message"]
    r = c.get(f"/discord/cours/professeur/{monde['prof_discord']}", headers=_bot()).json()
    assert r["ok"] is True and r["cours"] is None
    assert [g["classe_id"] for g in r["groupes"]] == [monde["classe_id"]]
    assert c.get(f"/discord/cours/professeur/{monde['prof_discord']}").status_code == 422


def test_parcours_extra(env, monde):
    from app.db.database import SessionLocal
    from app.db.models import PrepaAdjurisCours
    from app.services import cours_live, professeurs

    c = TestClient(env.app)
    d = {"discord_id": monde["prof_discord"]}

    r = c.post("/discord/cours/start", headers=_bot(), json={**d, "classe_id": monde["classe_id"]}).json()
    assert r["ok"], r
    cours = r["cours"]
    assert cours["phase"] == "heure"
    assert cours["fin_heure_ts"] - cours["started_ts"] == 3600
    assert cours["extra_possible_ts"] - cours["started_ts"] == 4200

    # Relancer rouvre le même cours.
    r2 = c.post("/discord/cours/start", headers=_bot(), json={**d, "classe_id": monde["classe_id"]}).json()
    assert r2["cours"]["id"] == cours["id"]

    # Extra refusé tant que l'extra n'a pas dépassé 10 minutes.
    r = c.post(f"/discord/cours/{cours['id']}/extra", headers=_bot(), json=d).json()
    assert r["ok"] is False

    # On recule le début : 1 h 05 écoulée → encore trop tôt ; 1 h 12 → possible.
    db = SessionLocal()
    row = db.get(PrepaAdjurisCours, cours["id"])
    row.started_at = datetime.now(timezone.utc) - timedelta(minutes=65)
    db.commit()
    assert cours_live.phase(row) == "extra"
    row.started_at = datetime.now(timezone.utc) - timedelta(minutes=72)
    db.commit()
    db.close()

    r = c.post(f"/discord/cours/{cours['id']}/state", headers=_bot(), json=d).json()
    assert r["cours"]["phase"] == "extra_disponible"
    r = c.post(f"/discord/cours/{cours['id']}/extra", headers=_bot(), json=d).json()
    assert r["ok"], r
    eleves = r["cours"]["eleves"]
    assert [e["nom"] for e in eleves] == ["Alice Martin", "bob", "chloe"]
    assert eleves[0]["discord_id"] == monde["alice_discord"]

    # Bob part : retiré, puis remis par erreur, puis retiré à nouveau.
    bob = eleves[1]["id"]
    for _ in range(3):
        r = c.post(f"/discord/cours/{cours['id']}/eleves/{bob}/toggle", headers=_bot(), json=d).json()
    assert r["cours"]["nb_restants"] == 2

    # Un autre compte ne peut pas toucher au cours.
    assert c.post(f"/discord/cours/{cours['id']}/end", headers=_bot(),
                  json={"discord_id": monde["alice_discord"]}).json()["ok"] is False

    r = c.post(f"/discord/cours/{cours['id']}/end", headers=_bot(), json=d).json()
    fin = r["cours"]
    assert fin["phase"] == "termine" and fin["duree_comptee_minutes"] == 90
    facturation = {e["nom"]: e["facturation"] for e in fin["eleves"]}
    assert facturation == {"Alice Martin": "facture", "bob": None, "chloe": "sans_paiement"}

    # Seule Alice (abonnée, restée) est facturée 10 € sur son abonnement.
    items = monde["stripe"].items
    assert len(items) == 1
    assert items[0]["customer"] == "cus_alice" and items[0]["subscription"] == "sub_alice"
    assert items[0]["amount"] == 1000

    # Terminer deux fois ne facture pas deux fois.
    c.post(f"/discord/cours/{cours['id']}/end", headers=_bot(), json=d)
    assert len(items) == 1

    # Professeur : 5 € par élève facturé (Alice et Chloé restées) ce mois-ci.
    db = SessionLocal()
    from app.db.models import PrepaAdjurisAffectation
    aff = db.query(PrepaAdjurisAffectation).one()
    prev = professeurs.previsionnel(db, aff, [])
    db.close()
    assert prev["extras"] == 1
    assert sum(m["extras_cents"] for m in prev["mois"]) == 1000
    assert prev["realise_cents"] == 1000


def test_sans_extra_rien_n_est_facture(env, monde):
    c = TestClient(env.app)
    d = {"discord_id": monde["prof_discord"]}
    cours = c.post("/discord/cours/start", headers=_bot(), json={**d, "classe_id": monde["classe_id"]}).json()["cours"]
    fin = c.post(f"/discord/cours/{cours['id']}/end", headers=_bot(), json=d).json()["cours"]
    assert fin["duree_comptee_minutes"] == 60 and fin["eleves"] == []
    assert monde["stripe"].items == []


def test_cours_oublie_clos_par_le_job(env, monde):
    from app.db.database import SessionLocal
    from app.db.models import PrepaAdjurisCours
    from app.services import cours_live

    c = TestClient(env.app)
    d = {"discord_id": monde["prof_discord"]}
    cours = c.post("/discord/cours/start", headers=_bot(), json={**d, "classe_id": monde["classe_id"]}).json()["cours"]
    db = SessionLocal()
    row = db.get(PrepaAdjurisCours, cours["id"])
    row.started_at = datetime.now(timezone.utc) - timedelta(hours=5)
    db.commit()
    db.close()

    cours_live.job_cours_live(SessionLocal)

    db = SessionLocal()
    row = db.get(PrepaAdjurisCours, cours["id"])
    assert row.statut == "termine" and row.cloture_auto and row.duree_comptee_minutes == 60
    db.close()
