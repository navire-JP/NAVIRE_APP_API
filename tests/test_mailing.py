"""
Mails de la console contre une base SQLite : catégories → brouillon avec PJ →
aperçu signé → envoi par lots Brevo (appel simulé, aucune clé réelle).

Lancer : pytest tests/test_mailing.py
"""

import base64
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

MATIERE = "L1_intro_au_droit"
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.fixture(scope="module")
def env():
    import app.main as main
    from app.db.database import Base, engine

    engine.dispose()
    Base.metadata.create_all(bind=engine)
    yield main
    engine.dispose()


@pytest.fixture()
def envois(monkeypatch):
    """Remplace l'appel Brevo : chaque lot envoyé est noté ici."""
    import app.services.email as mail
    lots = []

    def faux(recipients, subject, html, attachments=None):
        lots.append({"to": [r["email"] for r in recipients], "params": [r.get("params") for r in recipients],
                     "subject": subject, "html": html, "attachments": attachments or []})
        return {"sent": True, "detail": "ok", "message_ids": []}

    monkeypatch.setattr(mail, "send_bulk_result", faux)
    return lots


def _user(db, username, admin=False, avatar=None):
    from app.db.models import User
    u = User(email=f"{username}-{uuid.uuid4().hex[:8]}@test.fr", username=username,
             is_admin=admin, avatar_url=avatar)
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
    from app.db.database import SessionLocal
    from app.db.models import (
        Ambassador, MailCampaign, PrepaAdjurisClasse, PrepaAdjurisEnrollment, PrepaAdjurisProfesseur,
    )

    db = SessionLocal()
    db.query(MailCampaign).delete()
    db.query(Ambassador).delete()
    db.query(PrepaAdjurisProfesseur).delete()
    db.query(PrepaAdjurisEnrollment).filter(PrepaAdjurisEnrollment.matiere_key == MATIERE).delete()
    db.query(PrepaAdjurisClasse).filter(PrepaAdjurisClasse.matiere_key == MATIERE).delete()
    db.commit()

    admin = _user(db, "thork", admin=True,
                  avatar="https://res.cloudinary.com/navire/image/upload/v1/avatars/thork.png")
    amb, prof, etu, autre = _user(db, "julie_l2"), _user(db, "paul"), _user(db, "marc_l1"), _user(db, "x")
    db.add(Ambassador(user_id=amb.id, display_name="Julie", prenom="Julie", nom="M", status="approved"))
    db.add(PrepaAdjurisProfesseur(user_id=prof.id, prenom="Paul", nom="P", status="active"))
    now = datetime.now(timezone.utc)
    db.add(PrepaAdjurisEnrollment(user_id=etu.id, email=etu.email, matiere_key=MATIERE,
                                  status="active", source="admin", inscrit_le=now - timedelta(days=3)))
    # Paiement sans compte : l'adresse du paiement sert.
    db.add(PrepaAdjurisEnrollment(user_id=None, email="sanscompte@test.fr", matiere_key=MATIERE,
                                  status="active", source="admin", inscrit_le=now))
    # Inscription annulée : exclue.
    db.add(PrepaAdjurisEnrollment(user_id=autre.id, email=autre.email, matiere_key=MATIERE,
                                  status="cancelled", source="admin", inscrit_le=now))
    db.commit()
    db.close()
    return {"c": TestClient(env.app), "A": _headers(admin), "admin": admin,
            "amb": amb, "prof": prof, "etu": etu, "autre": autre}


def test_reserve_aux_admins(monde):
    c = monde["c"]
    assert c.get("/admin/mailing/segments", headers=_headers(monde["etu"])).status_code == 403
    assert c.get("/admin/mailing/segments").status_code in (401, 403)


def test_categories_et_destinataires(monde):
    c, A = monde["c"], monde["A"]
    seg = {s["key"]: s for s in c.get("/admin/mailing/segments", headers=A).json()["items"]}
    assert seg["ambassadeurs"]["count"] == 1
    assert seg["professeurs"]["count"] == 1
    assert seg[f"matiere:{MATIERE}"]["count"] == 2
    assert seg["niveau:L1"]["count"] >= 2

    r = c.post("/admin/mailing/recipients", headers=A,
               json={"segments": ["ambassadeurs", "professeurs", f"matiere:{MATIERE}", "ambassadeurs"]}).json()
    emails = {i["email"]: i["prenom"] for i in r["items"]}
    assert r["count"] == 4
    assert emails[monde["amb"].email] == "Julie"
    assert emails[monde["prof"].email] == "Paul"
    assert "sanscompte@test.fr" in emails
    assert monde["autre"].email not in emails

    assert c.post("/admin/mailing/recipients", headers=A, json={"segments": ["inconnu"]}).status_code == 400


def test_mise_en_forme_et_signature(monde):
    c, A = monde["c"], monde["A"]
    body = ("Bonjour {prenom},\n\n## Planning\n- **jeudi** 21h\n- *samedi*\n\n"
            "Voir [le site](https://navire.fr) ou https://navire.fr/lib.\n\n"
            "[bouton: Mon espace](https://navire.fr/adjuris)\n\n<script>x</script> {{ piege }}")
    r = c.post("/admin/mailing/preview", headers=A, json={"subject": "Salut {prenom}", "body": body}).json()
    h = r["html"]
    assert r["subject"] == "Salut thork"
    assert "Bonjour thork," in h
    assert "<strong>jeudi</strong>" in h and "<em>samedi</em>" in h and "<h2" in h and "<ul" in h
    assert 'href="https://navire.fr"' in h and 'href="https://navire.fr/lib"' in h
    assert "Mon espace</a>" in h and "#e63946" in h
    assert "<script>" not in h and "&lt;script&gt;" in h
    assert "{{ piege" not in h
    # Signature : photo recadrée Cloudinary + pseudo de l'admin.
    assert "/upload/c_fill,g_face,w_96,h_96/v1/avatars/thork.png" in h
    assert "thork</div>" in h


def test_brouillon_pj_test_et_envoi(monde, envois):
    c, A = monde["c"], monde["A"]
    pj = [{"name": "planning.pdf", "content_b64": base64.b64encode(PDF).decode()}]
    r = c.post("/admin/mailing/campaigns", headers=A, json={
        "subject": "Rentrée {prenom}", "body": "Bonjour {prenom}", "segments": ["professeurs"], "attachments": pj,
    })
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    assert r.json()["attachments"] == [{"name": "planning.pdf", "size": len(PDF)}]

    # Mise à jour sans renvoyer le contenu de la PJ : elle est conservée.
    r = c.put(f"/admin/mailing/campaigns/{cid}", headers=A, json={
        "subject": "Rentrée {prenom}", "body": "Bonjour {prenom}",
        "segments": ["professeurs", f"matiere:{MATIERE}"], "attachments": [{"name": "planning.pdf"}],
    })
    assert r.status_code == 200, r.text
    assert len(r.json()["attachments"]) == 1

    bad = c.put(f"/admin/mailing/campaigns/{cid}", headers=A, json={
        "subject": "x", "body": "y", "segments": [], "attachments": [{"name": "virus.exe", "content_b64": "eA=="}],
    })
    assert bad.status_code == 400

    t = c.post(f"/admin/mailing/campaigns/{cid}/test", headers=A)
    assert t.status_code == 200, t.text
    assert envois[-1]["to"] == [monde["admin"].email]
    assert envois[-1]["subject"].startswith("[TEST]")

    s = c.post(f"/admin/mailing/campaigns/{cid}/send", headers=A)
    assert s.status_code == 200, s.text
    out = s.json()
    assert out["status"] == "sent" and out["sent_count"] == 3 and out["author"] == "thork"
    lot = envois[-1]
    assert sorted(lot["to"]) == sorted([monde["prof"].email, monde["etu"].email, "sanscompte@test.fr"])
    assert {"prenom": "Paul"} in lot["params"]
    assert "{{ params.prenom }}" in lot["subject"] and "{{ params.prenom }}" in lot["html"]
    assert lot["attachments"][0]["name"] == "planning.pdf"
    assert base64.b64decode(lot["attachments"][0]["content"]) == PDF

    # Déjà envoyé : ni modifiable ni renvoyable ; la copie l'est.
    assert c.post(f"/admin/mailing/campaigns/{cid}/send", headers=A).status_code == 409
    d = c.post(f"/admin/mailing/campaigns/{cid}/duplicate", headers=A).json()
    assert d["status"] == "draft" and len(d["attachments"]) == 1


def test_envoi_par_lots_et_echec_partiel(monde, monkeypatch):
    import app.services.email as mail
    import app.services.mailing as svc
    from app.db.database import SessionLocal

    db = SessionLocal()
    for i in range(5):
        _user(db, f"bulk{i}", admin=True)
    db.close()
    monkeypatch.setattr(svc, "BATCH_SIZE", 2)
    appels = []

    def faux(recipients, subject, html, attachments=None):
        appels.append(len(recipients))
        ok = len(appels) != 2
        return {"sent": ok, "detail": "ok" if ok else "Brevo HTTP 400", "message_ids": []}

    monkeypatch.setattr(mail, "send_bulk_result", faux)
    c, A = monde["c"], monde["A"]
    cid = c.post("/admin/mailing/campaigns", headers=A,
                 json={"subject": "s", "body": "b", "segments": ["admins"]}).json()["id"]
    out = c.post(f"/admin/mailing/campaigns/{cid}/send", headers=A).json()
    assert all(n <= 2 for n in appels)
    assert out["status"] == "partial"
    assert out["failed_count"] == 2 and out["sent_count"] == out["recipients_count"] - 2
    assert "Lot 2" in out["error"]
