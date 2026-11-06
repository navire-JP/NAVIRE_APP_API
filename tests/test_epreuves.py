"""
Épreuves chronométrées : le professeur planifie une épreuve pour son groupe
(ouverture, disponibilité, durée) ; l'étudiant la commence une fois, le
chrono est tenu par le serveur, la copie rendue devient un devoir ;
entraînement libre.

Lancer : pytest tests/test_epreuves.py
"""

from datetime import datetime, timedelta, timezone

from tests.test_copies import _docx
from tests.test_lib_prepa import MATIERE, _headers, base_propre, env, monde  # noqa: F401


def _nettoyer_epreuves():
    from app.db.database import SessionLocal
    from app.db.models import PrepaEpreuve, PrepaEpreuveSession

    db = SessionLocal()
    db.query(PrepaEpreuveSession).delete()
    db.query(PrepaEpreuve).delete()
    db.commit()
    db.close()


def test_epreuve_planifiee(monde, tmp_path, monkeypatch):
    import app.routers.prepa_epreuves as ep

    _nettoyer_epreuves()
    monkeypatch.setattr(ep, "SUJETS_DIR", tmp_path / "sujets")
    c, P, alice, zoe = monde["c"], _headers(monde["prof"]), _headers(monde["alice"]), _headers(monde["zoe"])
    now = datetime.now(timezone.utc)

    # Le professeur ne crée que pour ses groupes ; un sujet est requis
    base = {"type": "dissertation", "titre": "Galop n° 1", "ouvre_le": (now - timedelta(minutes=5)).isoformat(),
            "persistance_heures": "48"}
    assert c.post("/professeurs/epreuves", headers=P, data={**base, "classe_id": monde["g2"], "sujet": "x"}).status_code == 404
    assert c.post("/professeurs/epreuves", headers=P, data={**base, "classe_id": monde["g1"]}).status_code == 400
    r = c.post("/professeurs/epreuves", headers=P, data={**base, "classe_id": monde["g1"], "sujet": "La coutume est-elle encore une source du droit ?"})
    assert r.status_code == 200, r.text
    e = r.json()
    assert (e["duree_minutes"], e["etat"], e["commencees"]) == (180, "ouverte", 0)
    futur = c.post("/professeurs/epreuves", headers=P, data={**base, "classe_id": monde["g1"], "sujet": "x", "type": "fiche_arret",
                                                             "ouvre_le": (now + timedelta(days=2)).isoformat()}).json()
    assert futur["duree_minutes"] == 75 and futur["etat"] == "a_venir"

    # Côté étudiant : sujet caché tant que l'épreuve n'est pas commencée
    v = c.get("/lib/prepa/epreuves", headers=alice).json()
    assert [x["titre"] for x in v["planifiees"]] == ["Galop n° 1", "Galop n° 1"]
    assert all("sujet" not in x for x in v["planifiees"])
    assert c.get("/lib/prepa/epreuves", headers=zoe).json()["planifiees"] == []
    assert c.post("/lib/prepa/epreuves/sessions", headers=alice, json={"epreuve_id": futur["id"]}).status_code == 400
    assert c.post("/lib/prepa/epreuves/sessions", headers=zoe, json={"epreuve_id": e["id"]}).status_code == 404

    s = c.post("/lib/prepa/epreuves/sessions", headers=alice, json={"epreuve_id": e["id"]}).json()
    assert s["statut"] == "en_cours" and s["sujet"].startswith("La coutume") and s["duree_minutes"] == 180
    # Relancer rend la même session (le chrono ne repart pas)
    s2 = c.post("/lib/prepa/epreuves/sessions", headers=alice, json={"epreuve_id": e["id"]}).json()
    assert s2["id"] == s["id"] and s2["started_at"] == s["started_at"]

    r = c.post(f"/lib/prepa/epreuves/sessions/{s['id']}/rendre", headers=alice,
               files={"file": ("copie.docx", _docx(), "application/octet-stream")})
    assert r.status_code == 200, r.text
    rendu = r.json()
    assert rendu["session"]["statut"] == "rendue" and rendu["devoir"]["titre"] == "Galop n° 1"
    assert rendu["devoir"]["message"].startswith("Épreuve chronométrée · Dissertation")
    assert c.post(f"/lib/prepa/epreuves/sessions/{s['id']}/rendre", headers=alice,
                  files={"file": ("copie.docx", _docx(), "application/octet-stream")}).status_code == 400

    # Le professeur voit qui l'a commencée et rendue, et la copie arrive dans « À corriger »
    liste = c.get("/professeurs/epreuves", headers=P).json()["items"]
    g = next(x for x in liste if x["id"] == e["id"])
    assert (g["commencees"], g["rendues"], g["sessions"][0]["etudiant"]) == (1, 1, "alice")
    assert any(d["titre"] == "Galop n° 1" for d in c.get("/professeurs/me/devoirs", headers=P).json()["items"])

    # Modifier, supprimer
    assert c.patch(f"/professeurs/epreuves/{futur['id']}", headers=P, json={"duree_minutes": 90}).json()["duree_minutes"] == 90
    assert c.delete(f"/professeurs/epreuves/{futur['id']}", headers=P).status_code == 200
    _nettoyer_epreuves()


def test_entrainement_libre(monde):
    _nettoyer_epreuves()
    c, alice = monde["c"], _headers(monde["alice"])
    assert c.post("/lib/prepa/epreuves/sessions", headers=alice, json={"type": "cas_pratique", "matiere_key": "L1_droit_constit"}).status_code == 403
    s = c.post("/lib/prepa/epreuves/sessions", headers=alice,
               json={"type": "commentaire", "matiere_key": MATIERE, "sujet": "Cass. civ. 1re, 2016"}).json()
    assert s["libre"] and s["titre"] == "Commentaire d'arrêt" and s["duree_minutes"] == 180
    assert c.get(f"/lib/prepa/epreuves/sessions/{s['id']}", headers=alice).json()["sujet"] == "Cass. civ. 1re, 2016"
    assert c.post(f"/lib/prepa/epreuves/sessions/{s['id']}/abandonner", headers=alice).json()["statut"] == "abandonnee"
    assert c.get(f"/lib/prepa/epreuves/sessions/{s['id']}", headers=_headers(monde["zoe"])).status_code == 404
    _nettoyer_epreuves()
