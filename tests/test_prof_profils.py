"""
Fiches publiques des professeurs contre une base SQLite : le professeur
remplit et soumet sa fiche, la console la publie, le public la voit sur
/professeurs#slug ; l'inscription Prép'AdJuris partie d'une fiche rejoint le
groupe du professeur.

Lancer : pytest tests/test_prof_profils.py
(variables d'environnement factices posées ci-dessous, aucune clé réelle.)
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

for k, v in {
    "OPENAI_API_KEY": "x", "BREVO_API_KEY_MEOLES": "x",
    "STRIPE_SECRET_KEY_MEOLES": "x", "STRIPE_WEBHOOK_SECRET_MEOLES": "x",
    "STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_WEBHOOK_SECRET": "whsec_x",
    "DATABASE_URL": "sqlite:///./test_prof_profils.db",
}.items():
    os.environ.setdefault(k, v)

from fastapi.testclient import TestClient  # noqa: E402

PENAL, ADMIN_L2 = "L2_droit_penal", "L2_droit_administratif"

FICHE = {
    "nom_complet_public": True,
    "statut_pro": "juriste",
    "poste": "Juriste en droit pénal des affaires",
    "experience_annees": 3,
    "formation": [{"diplome": "M2 Droit pénal", "etablissement": "Paris 1", "annee": "2022"}],
    "description": "Juriste depuis trois ans, j'accompagne les étudiants de L2 en droit pénal "
                   "avec des cas pratiques tirés de dossiers réels.",
    "linkedin_url": "linkedin.com/in/camille-martin",
}


@pytest.fixture(scope="module")
def env():
    import app.main as main
    from app.db.database import Base, engine

    engine.dispose()
    Base.metadata.create_all(bind=engine)
    yield main
    engine.dispose()


@pytest.fixture(autouse=True)
def base_propre(env, monkeypatch):
    from app.db.database import SessionLocal
    from app.db.models import (
        PrepaAdjurisAffectation, PrepaAdjurisClasse, PrepaAdjurisEnrollment,
        PrepaAdjurisProfesseur, PrepaAdjurisSeance, ProfProfil,
    )
    import app.routers.prof_profils as r

    db = SessionLocal()
    for m in (ProfProfil, PrepaAdjurisAffectation, PrepaAdjurisEnrollment, PrepaAdjurisProfesseur,
              PrepaAdjurisSeance, PrepaAdjurisClasse):
        db.query(m).delete()
    db.commit()
    db.close()
    envoyes = []
    monkeypatch.setattr(r, "send_mail", lambda to, subject, html: envoyes.append((to, subject)) or True)
    monkeypatch.setattr(r, "upload_photo_prof", lambda content, pid, cle: f"https://res.cloudinary.com/x/prof_{pid}_{cle}.jpg")
    return envoyes


def _user(db, username, admin=False):
    from app.db.models import User
    u = User(email=f"{username}-{uuid.uuid4().hex[:8]}@test.fr", username=username, is_admin=admin)
    db.add(u)
    db.commit()
    db.refresh(u)
    db.expunge(u)
    return u


def _headers(user):
    from app.core.security import create_access_token
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


@pytest.fixture()
def monde(env):
    """Un groupe de pénal et un d'administratif ; Camille Martin enseigne le
    pénal, avec deux séances à venir et une passée."""
    from app.db.database import SessionLocal
    from app.db.models import PrepaAdjurisSeance

    db = SessionLocal()
    admin, cam, eleve = _user(db, "admin", admin=True), _user(db, "camille"), _user(db, "eleve")
    now = datetime.now(timezone.utc)
    for jours in (-3, 4, 11):
        db.add(PrepaAdjurisSeance(niveau="L2", matiere_key=PENAL, date_debut=now + timedelta(days=jours),
                                  lien="https://meet.secret/xyz"))
    db.commit()
    db.close()

    c = TestClient(env.app)
    A = _headers(admin)
    g_penal = c.post("/professeurs/admin/classes", headers=A, json={"matiere_key": PENAL, "nom": "Pénal"}).json()["id"]
    g_adm = c.post("/professeurs/admin/classes", headers=A, json={"matiere_key": ADMIN_L2, "nom": "Adm."}).json()["id"]
    r = c.post("/professeurs/admin", headers=A, json={
        "email": cam.email, "prenom": "Camille", "nom": "Martin", "classe_ids": [g_penal],
    })
    assert r.status_code == 200, r.text
    return {"c": c, "A": A, "P": _headers(cam), "E": _headers(eleve), "pid": r.json()["id"],
            "cam": cam, "g_penal": g_penal, "g_adm": g_adm}


def _remplir_et_publier(m, fiche=FICHE):
    c, P, A = m["c"], m["P"], m["A"]
    assert c.post("/professeurs/me/profil/photo", headers=P,
                  files={"file": ("p.jpg", b"\xff\xd8\xff", "image/jpeg")}).status_code == 200
    r = c.put("/professeurs/me/profil", headers=P, json=fiche)
    assert r.status_code == 200, r.text
    assert c.post("/professeurs/me/profil/soumettre", headers=P).status_code == 200
    r = c.post(f"/professeurs/admin/profils/{m['pid']}/publier", headers=A)
    assert r.status_code == 200, r.text
    return r.json()


# ============================================================
# Parcours complet
# ============================================================

def test_parcours_prof_console_public(monde, base_propre):
    c, P, A = monde["c"], monde["P"], monde["A"]

    # Fiche vide : rien de public, soumission refusée avec la liste des manques.
    r = c.get("/professeurs/me/profil", headers=P).json()
    assert r["etat"] == "brouillon" and not r["visible"] and "une photo" in r["manques"]
    r = c.post("/professeurs/me/profil/soumettre", headers=P)
    assert r.status_code == 400 and "photo" in r.json()["detail"]
    assert c.get("/professeurs/public").json()["items"] == []

    c.post("/professeurs/me/profil/photo", headers=P, files={"file": ("p.jpg", b"\xff\xd8\xff", "image/jpeg")})
    r = c.put("/professeurs/me/profil", headers=P, json=FICHE).json()
    assert r["manques"] == [] and r["contenu"]["linkedin_url"] == "https://linkedin.com/in/camille-martin"
    assert r["apercu"]["slug_complet"] == "camille-martin" and r["apercu"]["slug_court"] == "camille-m"
    r = c.post("/professeurs/me/profil/soumettre", headers=P).json()
    assert r["etat"] == "en_attente"

    # Console : la fiche à valider passe en premier ; publication.
    liste = c.get("/professeurs/admin/profils", headers=A).json()
    assert liste["a_valider"] == 1 and liste["items"][0]["etat"] == "en_attente"
    pub = c.post(f"/professeurs/admin/profils/{monde['pid']}/publier", headers=A).json()
    assert pub["slug"] == "camille-martin" and pub["visible"] and pub["etat"] == "publie"
    assert base_propre and "en ligne" in base_propre[-1][1]

    # Public : carte puis fiche, sans aucune donnée privée.
    items = c.get("/professeurs/public").json()["items"]
    assert [i["slug"] for i in items] == ["camille-martin"]
    carte = items[0]
    assert carte["nom"] == "Camille Martin" and carte["statut_label"] == "Juriste"
    assert carte["matieres"] == [{"key": PENAL, "niveau": "L2", "nom": "Droit pénal"}]
    fiche = c.get("/professeurs/public/camille-martin").json()
    assert fiche["lien"].endswith("/professeurs#camille-martin") and fiche["lien_partage"].endswith("/p/camille-martin")
    cours = fiche["cours"][0]
    assert cours["key"] == PENAL and cours["creneau"] == "Lundi, 21 h"
    assert len(cours["prochaines_seances"]) == 2                       # la séance passée est exclue
    assert cours["abonnement"]["prix_seance_cents"] == 2000
    brut = str(fiche)
    assert monde["cam"].email not in brut and "meet.secret" not in brut and "discord" not in brut.lower()
    assert "remu" not in brut and "user_id" not in brut

    # Filtres du catalogue.
    assert c.get("/professeurs/public?niveau=L1").json()["items"] == []
    assert len(c.get(f"/professeurs/public?matiere={PENAL}").json()["items"]) == 1


def test_brouillon_ne_change_pas_la_fiche_en_ligne(monde):
    c, P, A = monde["c"], monde["P"], monde["A"]
    _remplir_et_publier(monde)
    c.put("/professeurs/me/profil", headers=P, json={**FICHE, "poste": "Avocate pénaliste"})
    assert c.get("/professeurs/public/camille-martin").json()["poste"] == FICHE["poste"]
    r = c.get("/professeurs/me/profil", headers=P).json()
    assert r["brouillon_en_cours"] and r["contenu"]["poste"] == "Avocate pénaliste" and r["visible"]

    c.post("/professeurs/me/profil/soumettre", headers=P)
    c.post(f"/professeurs/admin/profils/{monde['pid']}/publier", headers=A)
    assert c.get("/professeurs/public/camille-martin").json()["poste"] == "Avocate pénaliste"
    # Plus rien à soumettre ni à publier.
    assert c.post("/professeurs/me/profil/soumettre", headers=P).status_code == 400
    assert c.post(f"/professeurs/admin/profils/{monde['pid']}/publier", headers=A).status_code == 400


def test_refus_avec_motif(monde, base_propre):
    c, P, A = monde["c"], monde["P"], monde["A"]
    c.post("/professeurs/me/profil/photo", headers=P, files={"file": ("p.jpg", b"\xff\xd8\xff", "image/jpeg")})
    c.put("/professeurs/me/profil", headers=P, json=FICHE)
    c.post("/professeurs/me/profil/soumettre", headers=P)
    assert c.post(f"/professeurs/admin/profils/{monde['pid']}/refuser", headers=A, json={"motif": ""}).status_code == 400
    r = c.post(f"/professeurs/admin/profils/{monde['pid']}/refuser", headers=A, json={"motif": "Photo floue"}).json()
    assert r["etat"] == "refuse" and not r["visible"]
    me = c.get("/professeurs/me/profil", headers=P).json()
    assert me["etat"] == "refuse" and me["motif_refus"] == "Photo floue"
    assert "reprendre" in base_propre[-1][1]
    # Le prof corrige : le refus s'efface, il doit soumettre à nouveau.
    me = c.put("/professeurs/me/profil", headers=P, json=FICHE).json()
    assert me["etat"] == "brouillon" and me["motif_refus"] == ""


# ============================================================
# Nom, slug, visibilité
# ============================================================

def test_nom_court_sans_consentement_et_homonymes(monde, env):
    from app.db.database import SessionLocal
    from app.db.models import ProfProfil

    _remplir_et_publier(monde, {**FICHE, "nom_complet_public": False})
    c = monde["c"]
    carte = c.get("/professeurs/public").json()["items"][0]
    assert carte["nom"] == "Camille M." and carte["slug"] == "camille-m"

    # Un homonyme reçoit un suffixe.
    with SessionLocal() as db:
        db.add(ProfProfil(professeur_id=monde["pid"] + 999, slug="camille-martin", formation=[]))
        db.commit()
    from app.services import prof_profils as svc
    with SessionLocal() as db:
        assert svc.slug_unique(db, "camille-martin", None) == "camille-martin-2"
        db.query(ProfProfil).filter(ProfProfil.professeur_id == monde["pid"] + 999).delete()
        db.commit()

    # Le prof accepte son nom complet : le slug suit à la publication suivante.
    P, A = monde["P"], monde["A"]
    c.put("/professeurs/me/profil", headers=P, json=FICHE)
    c.post("/professeurs/me/profil/soumettre", headers=P)
    assert c.post(f"/professeurs/admin/profils/{monde['pid']}/publier", headers=A).json()["slug"] == "camille-martin"
    assert c.get("/professeurs/public/camille-m").status_code == 404


def test_masquee_ou_suspendu_absent(monde):
    c, A = monde["c"], monde["A"]
    _remplir_et_publier(monde)
    c.post(f"/professeurs/admin/profils/{monde['pid']}/masquer", headers=A, json={"masque": True})
    assert c.get("/professeurs/public").json()["items"] == []
    assert c.get("/professeurs/public/camille-martin").status_code == 404
    c.post(f"/professeurs/admin/profils/{monde['pid']}/masquer", headers=A, json={"masque": False})
    assert len(c.get("/professeurs/public").json()["items"]) == 1

    assert c.patch(f"/professeurs/admin/{monde['pid']}", headers=A, json={"status": "suspendu"}).status_code == 200
    assert c.get("/professeurs/public").json()["items"] == []
    r = c.get("/p/camille-martin", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].endswith("/professeurs")


def test_badge_verifie_et_ordre(monde):
    c, A = monde["c"], monde["A"]
    _remplir_et_publier(monde)
    assert not c.get("/professeurs/public").json()["items"][0]["verifie"]
    c.post(f"/professeurs/admin/profils/{monde['pid']}/verifier", headers=A, json={"verifie": True})
    assert c.get("/professeurs/public").json()["items"][0]["verifie"]
    assert c.post(f"/professeurs/admin/profils/{monde['pid']}/ordre", headers=A, json={"ordre": 3}).json()["ordre"] == 3


def test_page_de_partage(monde):
    c = monde["c"]
    _remplir_et_publier(monde)
    robot = c.get("/p/camille-martin", headers={"User-Agent": "WhatsApp/2.23"})
    assert robot.status_code == 200
    assert 'og:title" content="Camille Martin · Professeur NAVIRE"' in robot.text
    assert "prof_" in robot.text and "refresh" not in robot.text            # photo, pas de redirection
    humain = c.get("/p/camille-martin", headers={"User-Agent": "Mozilla/5.0"})
    assert "location.replace" in humain.text and "#camille-martin" in humain.text
    assert c.get("/p/inconnu", follow_redirects=False).status_code == 302


# ============================================================
# Droits et validation
# ============================================================

def test_droits(monde):
    c, E, A = monde["c"], monde["E"], monde["A"]
    assert c.get("/professeurs/me/profil", headers=E).status_code == 403
    assert c.put("/professeurs/me/profil", headers=E, json=FICHE).status_code == 403
    assert c.get("/professeurs/admin/profils", headers=E).status_code == 403
    assert c.post(f"/professeurs/admin/profils/{monde['pid']}/publier", headers=E).status_code == 403
    # Aperçu admin : lecture de la fiche d'un prof.
    assert c.get(f"/professeurs/me/profil?voir_comme={monde['pid']}", headers=A).status_code == 200


def test_validation(monde):
    c, P = monde["c"], monde["P"]
    assert c.put("/professeurs/me/profil", headers=P, json={**FICHE, "statut_pro": "magicien"}).status_code == 422
    assert c.put("/professeurs/me/profil", headers=P, json={**FICHE, "linkedin_url": "https://evil.com/x"}).status_code == 422
    assert c.put("/professeurs/me/profil", headers=P, json={**FICHE, "description": "x" * 1201}).status_code == 422
    r = c.post("/professeurs/me/profil/photo", headers=P, files={"file": ("p.gif", b"GIF89a", "image/gif")})
    assert r.status_code == 400
    # Lignes de formation vides ignorées.
    r = c.put("/professeurs/me/profil", headers=P, json={**FICHE, "formation": [
        {"diplome": "", "etablissement": "", "annee": ""}, FICHE["formation"][0]]}).json()
    assert len(r["contenu"]["formation"]) == 1


# ============================================================
# Inscription Prép'AdJuris depuis une fiche
# ============================================================

def test_inscription_depuis_une_fiche(monde, monkeypatch):
    import app.routers.prepa_adjuris as pa
    from app.db.database import SessionLocal
    from app.services import prof_profils as svc

    _remplir_et_publier(monde)
    with SessionLocal() as db:
        pid, groupes = svc.groupes_pour_inscription(db, "camille-martin", [PENAL, ADMIN_L2])
        assert pid == monde["pid"] and groupes == {PENAL: monde["g_penal"]}   # elle n'enseigne pas l'administratif
        assert svc.groupes_pour_inscription(db, "inconnu", [PENAL]) == (None, {})
        assert svc.groupes_pour_inscription(db, None, [PENAL]) == (None, {})
    meta = svc.metadata_prof(pid, groupes)
    assert svc.lire_metadata_prof(meta) == (monde["pid"], {PENAL: monde["g_penal"]})
    assert svc.lire_metadata_prof({}) == (None, {})

    # Le checkout connecté porte la fiche dans la metadata Stripe.
    vu = {}

    class FausseSession:
        url = "https://checkout.stripe.test/x"

    def faux_checkout(db, a_payer, email, **kw):
        vu.update(kw.get("metadata_extra") or {})
        return FausseSession()

    monkeypatch.setattr(pa, "_creer_checkout_session", faux_checkout)
    r = monde["c"].post("/prepa/adjuris/checkout-session", headers=monde["E"],
                        json={"matieres": [PENAL], "prof": "camille-martin"})
    assert r.status_code == 200, r.text
    assert vu["prof_id"] == str(monde["pid"]) and vu["prof_classes"] == f"{PENAL}:{monde['g_penal']}"


def test_webhook_rattache_le_groupe_du_prof(monde, monkeypatch):
    """checkout.session.completed : l'inscription prend le groupe et le
    professeur de la fiche (Stripe simulé)."""
    import app.routers.subscriptions as subs
    from app.db.database import SessionLocal
    from app.db.models import PrepaAdjurisEnrollment

    _remplir_et_publier(monde)
    monkeypatch.setattr(subs, "_creer_abonnement_adjuris", lambda session, devis, meta: "sub_test_fiche")
    for nom in ("_enregistrer_identite_adjuris", "send_adjuris_discord_invite", "_commission_ambassadeur", "send_mail"):
        monkeypatch.setattr(subs, nom, lambda *a, **k: None)
    monkeypatch.setattr(subs.facturation_adjuris, "pousser_echeancier_stripe", lambda db, sub: None)

    session = {
        "id": "cs_test_fiche", "mode": "payment", "customer": "cus_fiche", "created": 0,
        "metadata": {"email": "nouvel.eleve@test.fr", "prof_id": str(monde["pid"]),
                     "prof_classes": f"{PENAL}:{monde['g_penal']}"},
    }
    with SessionLocal() as db:
        subs._handle_prepa_adjuris_checkout(db, session, [PENAL])
        e = db.query(PrepaAdjurisEnrollment).filter_by(stripe_customer_id="cus_fiche").one()
        assert e.classe_id == monde["g_penal"] and e.professeur_ref_id == monde["pid"]


def test_landing_utilise_la_fiche(monde):
    c = monde["c"]
    avant = c.get("/landing/professeurs").json()["items"][0]
    assert avant["nom"] == "Camille M." and avant["slug"] is None
    _remplir_et_publier(monde)
    apres = c.get("/landing/professeurs").json()["items"][0]
    assert apres["nom"] == "Camille Martin" and apres["slug"] == "camille-martin"
    assert apres["grade"] == "Juriste" and apres["lien"].endswith("#camille-martin")
