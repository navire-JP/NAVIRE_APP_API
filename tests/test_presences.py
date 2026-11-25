"""
Présence aux cours contre une base SQLite : cours lancé depuis Discord →
relevés du salon vocal par le bot → présent au-delà de 5 minutes → correction
par le professeur (48 h), par la console ensuite → mois versé figé → cours
oublié créé dans la console.

Lancer : pytest tests/test_presences.py
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

for k, v in {
    "OPENAI_API_KEY": "x", "BREVO_API_KEY_MEOLES": "x",
    "STRIPE_SECRET_KEY_MEOLES": "x", "STRIPE_WEBHOOK_SECRET_MEOLES": "x",
    "STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_WEBHOOK_SECRET": "whsec_x",
    "DATABASE_URL": "sqlite:///./test_presences.db",
}.items():
    os.environ.setdefault(k, v)

from fastapi.testclient import TestClient  # noqa: E402

MATIERE = "L2_droit_penal"
VOCAL = "1533885924121903154"


@pytest.fixture(scope="module")
def env():
    import app.main as main
    from app.db.database import Base, engine

    engine.dispose()
    Base.metadata.create_all(bind=engine)
    yield main
    engine.dispose()


def _headers(user_id):
    from app.core.security import create_access_token
    return {"Authorization": f"Bearer {create_access_token(str(user_id))}"}


def _bot():
    from app.core.config import BOT_SECRET
    return {"x-bot-secret": BOT_SECRET}


@pytest.fixture()
def monde(env):
    """Un groupe de pénal avec son salon vocal, un professeur lié à Discord,
    trois élèves : Alice et Bob reliés à Discord, Chloé sans compte NAVIRE
    (inscription payée par email)."""
    from app.db.database import SessionLocal
    from app.db.models import (
        PrepaAdjurisAffectation, PrepaAdjurisClasse, PrepaAdjurisCours, PrepaAdjurisEnrollment,
        PrepaAdjurisProfesseur, PrepaAdjurisRemuneration, PrepaPresence, User,
    )

    db = SessionLocal()
    for m in (PrepaPresence, PrepaAdjurisCours, PrepaAdjurisRemuneration, PrepaAdjurisAffectation,
              PrepaAdjurisProfesseur, PrepaAdjurisEnrollment, PrepaAdjurisClasse):
        db.query(m).delete()
    db.commit()

    tag = uuid.uuid4().hex[:6]

    def user(name, discord_id=None, admin=False):
        u = User(email=f"{name}-{tag}@test.fr", username=name, discord_id=discord_id, is_admin=admin)
        db.add(u)
        db.commit()
        return u

    admin = user("admin", admin=True)
    prof_u, alice, bob, inconnu = user("prof", f"9{tag}"), user("alice", f"1{tag}"), user("bob", f"2{tag}"), None
    old = datetime.now(timezone.utc) - timedelta(days=30)
    for u in (alice, bob):
        db.add(PrepaAdjurisEnrollment(user_id=u.id, email=u.email, matiere_key=MATIERE, status="active",
                                      inscrit_le=old, source="admin"))
    db.add(PrepaAdjurisEnrollment(email=f"chloe-{tag}@test.fr", matiere_key=MATIERE, status="active",
                                  inscrit_le=old, source="admin"))
    classe = PrepaAdjurisClasse(matiere_key=MATIERE, nom="Pénal A", discord_vocal_id=VOCAL)
    db.add(classe)
    db.commit()
    prof = PrepaAdjurisProfesseur(user_id=prof_u.id, prenom="Paul", nom="Prof")
    prof.affectations.append(PrepaAdjurisAffectation(classe_id=classe.id))
    db.add(prof)
    db.commit()
    out = {
        "admin": _headers(admin.id), "prof": _headers(prof_u.id), "eleve": _headers(alice.id),
        "prof_discord": prof_u.discord_id, "alice_discord": alice.discord_id, "bob_discord": bob.discord_id,
        "alice_id": alice.id, "classe_id": classe.id, "prof_id": prof.id,
        "affectation_id": prof.affectations[0].id,
    }
    db.close()
    del inconnu
    return out


def _lancer(c, m):
    r = c.post("/discord/cours/start", headers=_bot(), json={"discord_id": m["prof_discord"], "classe_id": m["classe_id"]})
    assert r.json()["ok"], r.text
    return r.json()["cours"]["id"]


def _releves(cours_id, membres, minutes):
    """`minutes` relevés espacés d'une minute, à partir du début du cours."""
    from app.db.database import SessionLocal
    from app.db.models import PrepaAdjurisCours
    from app.services import presences

    with SessionLocal() as db:
        c = db.get(PrepaAdjurisCours, cours_id)
        t0 = presences.aware(c.presence_releve_at) or presences.aware(c.started_at)
        for i in range(1, minutes + 1):
            presences.enregistrer_releve(db, c, membres, t0 + timedelta(minutes=i))


def _vieillir(cours_id, jours):
    """Recule le cours dans le temps (correction hors délai)."""
    from app.db.database import SessionLocal
    from app.db.models import PrepaAdjurisCours
    with SessionLocal() as db:
        c = db.get(PrepaAdjurisCours, cours_id)
        c.started_at = c.started_at - timedelta(days=jours)
        if c.ended_at:
            c.ended_at = c.ended_at - timedelta(days=jours)
        db.commit()


def test_releve_du_bot(env, monde):
    c = TestClient(env.app)
    m = monde
    # Pas de cours : rien à relever.
    assert c.get("/discord/cours/vocaux", headers=_bot()).json()["items"] == []
    cid = _lancer(c, m)
    assert c.get("/discord/cours/vocaux", headers=_bot()).json()["items"] == [{"cours_id": cid, "channel_id": VOCAL}]
    assert c.get("/discord/cours/vocaux").status_code == 422          # secret du bot exigé

    # Le bot envoie un relevé : la route crédite le temps écoulé (2 min au plus).
    r = c.post(f"/discord/cours/{cid}/presence", headers=_bot(), json={"membres": [
        {"discord_id": m["alice_discord"], "nom": "alice"}, {"discord_id": m["prof_discord"], "nom": "Paul"},
        {"discord_id": "555", "nom": "Un inconnu"},
    ]})
    assert r.json()["ok"] and 0 <= r.json()["secondes"] <= 120
    # Doublon immédiat : ignoré.
    r = c.post(f"/discord/cours/{cid}/presence", headers=_bot(), json={"membres": [{"discord_id": m["alice_discord"]}]})
    assert r.json()["secondes"] == 0

    # Alice reste 6 minutes, Bob 3 : seule Alice est présente.
    _releves(cid, [{"discord_id": m["alice_discord"], "nom": "alice"}, {"discord_id": m["bob_discord"], "nom": "bob"},
                   {"discord_id": "555", "nom": "Un inconnu"}], 3)
    _releves(cid, [{"discord_id": m["alice_discord"], "nom": "alice"}], 3)

    liste = c.get(f"/professeurs/cours/{cid}/presences", headers=m["prof"]).json()
    par_nom = {e["nom"]: e for e in liste["eleves"]}
    assert par_nom["alice"]["present"] and par_nom["alice"]["minutes"] >= 6
    assert not par_nom["bob"]["present"] and par_nom["bob"]["minutes"] == 3
    chloe = next(e for e in liste["eleves"] if e["nom"].startswith("chloe"))
    assert not chloe["present"] and not chloe["discord_relie"]
    # Le professeur n'est jamais compté ; l'inconnu est affiché, jamais compté.
    assert [a["nom"] for a in liste["autres"]] == ["Un inconnu"]
    assert liste["autres"][0]["non_relie"] and not liste["autres"][0]["present"]
    assert liste["modifiable"] and liste["seuil_minutes"] == 5

    # Fin du cours : plus aucun relevé.
    c.post(f"/discord/cours/{cid}/end", headers=_bot(), json={"discord_id": m["prof_discord"]})
    assert c.get("/discord/cours/vocaux", headers=_bot()).json()["items"] == []
    r = c.post(f"/discord/cours/{cid}/presence", headers=_bot(), json={"membres": [{"discord_id": m["bob_discord"]}]})
    assert r.json()["secondes"] == 0 and r.json()["en_cours"] is False


def test_correction_par_le_professeur_puis_la_console(env, monde):
    c = TestClient(env.app)
    m = monde
    cid = _lancer(c, m)
    _releves(cid, [{"discord_id": m["bob_discord"], "nom": "bob"}], 2)
    c.post(f"/discord/cours/{cid}/end", headers=_bot(), json={"discord_id": m["prof_discord"]})

    liste = c.get(f"/professeurs/cours/{cid}/presences", headers=m["prof"]).json()
    bob = next(e for e in liste["eleves"] if e["nom"] == "bob")
    chloe = next(e for e in liste["eleves"] if e["nom"].startswith("chloe"))

    # Bob était là (micro coupé, connexion lente…) : le prof le coche.
    r = c.patch(f"/professeurs/cours/{cid}/presences", headers=m["prof"], json={"presence_id": bob["presence_id"], "present": True})
    assert r.status_code == 200, r.text
    bob2 = next(e for e in r.json()["eleves"] if e["nom"] == "bob")
    assert bob2["present"] and bob2["source"] == "prof" and bob2["modifie_le"]
    # Chloé n'a pas de compte : cochée par son inscription.
    r = c.patch(f"/professeurs/cours/{cid}/presences", headers=m["prof"], json={"enrollment_id": chloe["enrollment_id"], "present": True})
    assert next(e for e in r.json()["eleves"] if e["nom"].startswith("chloe"))["present"]

    # Liste des cours du professeur.
    cours = c.get("/professeurs/me/cours", headers=m["prof"]).json()["items"]
    assert cours[0]["id"] == cid and cours[0]["nb_presents"] == 2 and cours[0]["vocal"]

    # Un élève ou un autre compte ne voit rien.
    assert c.get(f"/professeurs/cours/{cid}/presences", headers=m["eleve"]).status_code == 403

    # 3 jours plus tard : hors délai pour le prof, la console corrige encore.
    _vieillir(cid, 3)
    r = c.patch(f"/professeurs/cours/{cid}/presences", headers=m["prof"], json={"presence_id": bob["presence_id"], "present": False})
    assert r.status_code == 400 and "48 h" in r.json()["detail"]
    assert not c.get(f"/professeurs/cours/{cid}/presences", headers=m["prof"]).json()["modifiable"]
    r = c.patch(f"/professeurs/admin/cours/{cid}/presences", headers=m["admin"], json={"presence_id": bob["presence_id"], "present": False})
    assert r.status_code == 200
    assert next(e for e in r.json()["eleves"] if e["nom"] == "bob")["source"] == "admin"


def test_correction_figee_apres_relevés_et_mois_verse(env, monde):
    from app.db.database import SessionLocal
    from app.db.models import PrepaAdjurisCours, PrepaAdjurisRemuneration
    from app.services.prepa_adjuris_billing import cle_mois, mois_de

    c = TestClient(env.app)
    m = monde
    cid = _lancer(c, m)
    _releves(cid, [{"discord_id": m["alice_discord"], "nom": "alice"}], 6)
    alice = next(e for e in c.get(f"/professeurs/cours/{cid}/presences", headers=m["prof"]).json()["eleves"] if e["nom"] == "alice")
    assert alice["present"]
    # Le prof la décoche (elle n'a pas suivi) : les relevés suivants ne la recochent pas.
    c.patch(f"/professeurs/cours/{cid}/presences", headers=m["prof"], json={"presence_id": alice["presence_id"], "present": False})
    _releves(cid, [{"discord_id": m["alice_discord"], "nom": "alice"}], 3)
    alice = next(e for e in c.get(f"/professeurs/cours/{cid}/presences", headers=m["prof"]).json()["eleves"] if e["nom"] == "alice")
    assert not alice["present"] and alice["minutes"] >= 9

    # Mois versé : personne ne corrige plus.
    with SessionLocal() as db:
        cours = db.get(PrepaAdjurisCours, cid)
        db.add(PrepaAdjurisRemuneration(affectation_id=m["affectation_id"], mois=cle_mois(mois_de(cours.started_at)),
                                        montant_cents=0, verse_le=datetime.now(timezone.utc)))
        db.commit()
    for h, url in ((m["prof"], f"/professeurs/cours/{cid}/presences"), (m["admin"], f"/professeurs/admin/cours/{cid}/presences")):
        r = c.patch(url, headers=h, json={"presence_id": alice["presence_id"], "present": True})
        assert r.status_code == 400 and "versé" in r.json()["detail"]


def test_cours_oublie_dans_la_console(env, monde):
    c = TestClient(env.app)
    m = monde
    hier = (datetime.now(timezone.utc) - timedelta(days=1)).replace(microsecond=0)
    r = c.post("/professeurs/admin/cours", headers=m["admin"], json={
        "classe_id": m["classe_id"], "professeur_id": m["prof_id"], "debut": hier.isoformat(), "duree_minutes": 60,
    })
    assert r.status_code == 200, r.text
    cours = r.json()["cours"]
    assert cours["statut"] == "termine" and cours["cree_par_admin"] and cours["nb_presents"] == 0
    alice = next(e for e in r.json()["eleves"] if e["nom"] == "alice")
    r = c.patch(f"/professeurs/admin/cours/{cours['id']}/presences", headers=m["admin"],
                json={"enrollment_id": alice["enrollment_id"], "present": True})
    assert r.json()["cours"]["nb_presents"] == 1
    # Liste du mois dans la console.
    mois = c.get("/professeurs/admin/cours", headers=m["admin"], params={"mois": hier.strftime("%Y-%m")}).json()
    assert any(x["id"] == cours["id"] for x in mois["items"])
    # Cours dans le futur, ou professeur sans ce groupe : refusé.
    demain = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert c.post("/professeurs/admin/cours", headers=m["admin"], json={
        "classe_id": m["classe_id"], "professeur_id": m["prof_id"], "debut": demain}).status_code == 400
    assert c.post("/professeurs/admin/cours", headers=m["prof"], json={
        "classe_id": m["classe_id"], "professeur_id": m["prof_id"], "debut": hier.isoformat()}).status_code == 403


def test_salon_vocal_du_groupe(env, monde):
    c = TestClient(env.app)
    A = monde["admin"]
    url = f"/professeurs/admin/classes/{monde['classe_id']}"
    r = c.patch(url, headers=A, json={"discord_vocal_id": "https://discord.com/channels/1131156852265201715/1533885633846837398"})
    assert r.status_code == 200 and r.json()["discord_vocal_id"] == "1533885633846837398"
    assert c.patch(url, headers=A, json={"discord_vocal_id": "salon-pénal"}).status_code == 422
    assert c.patch(url, headers=A, json={"discord_vocal_id": ""}).json()["discord_vocal_id"] == ""
    # Sans salon vocal : rien à relever.
    _lancer(c, monde)
    assert c.get("/discord/cours/vocaux", headers=_bot()).json()["items"] == []
