"""
Onglet Prépa : copie .docx mise en forme par NAVIRE, révisions et
commentaires du correcteur (visibles par l'étudiant une fois la correction
rendue), exports .docx (révisions Word) et .pdf ; cours Lib suivis ; séance
déplacée par la console.

Lancer : pytest tests/test_copies.py
"""

import io
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_lib_prepa import MATIERE, _headers, base_propre, env, monde  # noqa: F401


def _docx() -> bytes:
    import docx

    d = docx.Document()
    d.add_paragraph("La cause en droit des contrats")
    d.add_paragraph("Introduction").runs[0].bold = True
    p = d.add_paragraph("La cause a été supprimée par  l'ordonnance de 2016. ")
    p.add_run("Elle subsiste").italic = True
    p.add_run(" pourtant dans le Code civil.")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


@pytest.fixture()
def copie(monde):
    c, alice = monde["c"], _headers(monde["alice"])
    r = c.post("/lib/prepa/devoirs", headers=alice, data={"matiere_key": MATIERE, "titre": "Dissertation"},
               files={"file": ("copie.docx", _docx(), "application/octet-stream")})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_copie_mise_en_forme_et_corrigee(monde, copie):
    c, P, alice = monde["c"], _headers(monde["prof"]), _headers(monde["alice"])

    v = c.get(f"/professeurs/devoirs/{copie}/copie", headers=P).json()
    blocs = {b["t"]: b for b in v["blocs"]}
    assert blocs["titre"]["text"] == "La cause en droit des contrats"
    assert blocs["h"]["text"] == "Introduction"
    p = blocs["p"]
    assert p["text"] == "La cause a été supprimée par l'ordonnance de 2016. Elle subsiste pourtant dans le Code civil."
    assert "".join(r[0] for r in p["runs"]) == p["text"] and [r[2] for r in p["runs"]] == [False, True, False]

    t = p["text"]
    i = t.index("supprimée")
    post = lambda **k: c.post(f"/professeurs/devoirs/{copie}/annotations", headers=P, json=k)  # noqa: E731
    r = post(type="correction", bloc=p["id"], debut=i, fin=i + len("supprimée"), texte="abandonnée")
    assert r.status_code == 200 and r.json()["citation"] == "supprimée" and r.json()["auteur"] == "prof"
    assert "auteur_avatar" in r.json()        # photo de profil du correcteur, sur ses commentaires
    # Une seconde correction sur le même passage est refusée
    assert post(type="correction", bloc=p["id"], debut=i + 2, fin=i + 4, texte="x").status_code == 409
    j = t.index("Elle subsiste")
    com = post(type="commentaire", bloc=p["id"], debut=j, fin=j + 13, texte="Par l'objet et le but.").json()
    assert post(type="surligne", bloc=p["id"], debut=0, fin=8).status_code == 200
    k = t.index("2016") + 4
    assert post(type="correction", bloc=p["id"], debut=k, fin=k, texte=" (n° 2016-131)").status_code == 200
    assert post(type="commentaire", bloc=p["id"], debut=0, fin=4, texte="").status_code == 400
    assert post(type="commentaire", bloc=p["id"], debut=0, fin=999, texte="x").status_code == 400
    assert c.patch(f"/professeurs/annotations/{com['id']}", headers=P,
                   json={"texte": "Par l'objet, le but et le contenu."}).json()["texte"].endswith("contenu.")

    # Avant la correction rendue : l'étudiant ne voit que sa copie
    eleve = c.get(f"/lib/prepa/devoirs/{copie}/copie", headers=alice).json()
    assert eleve["annotations"] == [] and eleve["blocs"]
    assert c.get(f"/lib/prepa/devoirs/{copie}/copie", headers=_headers(monde["zoe"])).status_code == 404

    assert c.patch(f"/professeurs/devoirs/{copie}", headers=P,
                   json={"note": "15/20", "commentaire": "Bonne copie.", "statut": "corrige"}).status_code == 200
    eleve = c.get(f"/lib/prepa/devoirs/{copie}/copie", headers=alice).json()
    assert sorted(a["type"] for a in eleve["annotations"]) == ["commentaire", "correction", "correction", "surligne"]
    assert eleve["devoir"]["note"] == "15/20"

    # Export Word : révisions et commentaires natifs
    r = c.get(f"/lib/prepa/devoirs/{copie}/export?format=docx", headers=alice)
    assert r.status_code == 200 and "corrig" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    xml = z.read("word/document.xml").decode()
    assert "<w:del " in xml and "<w:delText" in xml and "supprimée" in xml
    assert "<w:ins " in xml and "abandonnée" in xml and "(n° 2016-131)" in xml
    assert "w:commentReference" in xml and 'w:highlight w:val="yellow"' in xml
    assert "15/20" in xml and "Bonne copie." in xml
    assert "contenu." in z.read("word/comments.xml").decode()

    # Export PDF
    r = c.get(f"/lib/prepa/devoirs/{copie}/export?format=pdf", headers=alice)
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    import fitz
    texte = "".join(pg.get_text() for pg in fitz.open(stream=r.content, filetype="pdf"))
    assert "abandonnée" in texte and "Commentaires du correcteur" in texte
    assert c.get(f"/lib/prepa/devoirs/{copie}/export?format=odt", headers=alice).status_code == 400

    # Retrait d'une annotation ; un autre étudiant ne peut rien
    assert c.delete(f"/professeurs/annotations/{com['id']}", headers=P).status_code == 200
    assert c.delete(f"/professeurs/annotations/{com['id']}", headers=P).status_code == 404
    assert c.post(f"/professeurs/devoirs/{copie}/annotations", headers=alice,
                  json={"type": "surligne", "bloc": p["id"], "debut": 0, "fin": 3}).status_code == 403


def test_cours_suivis_et_seance_deplacee(monde):
    from app.db.database import SessionLocal
    from app.db.models import LibDocument, PrepaAdjurisSeance

    c, alice, zoe = monde["c"], _headers(monde["alice"]), _headers(monde["zoe"])
    A = {"X-Admin-Code": "THORKISHERE"}
    db = SessionLocal()
    doc = db.query(LibDocument).filter_by(titre="Droit des obligations").one().id
    s = db.query(PrepaAdjurisSeance).filter_by(titre="Les sources").one()
    sid, avant = s.id, s.date_debut
    db.close()

    assert c.post(f"/lib/prepa/suivis/{doc}", headers=zoe).status_code == 403
    assert c.post(f"/lib/prepa/suivis/{doc}", headers=alice).json()["suivi"] is True
    assert c.post(f"/lib/prepa/suivis/{doc}", headers=alice).status_code == 200  # idempotent
    v = c.get("/lib/prepa", headers=alice).json()
    assert [d["titre"] for d in v["suivis"]] == ["Droit des obligations"]
    assert c.get(f"/lib/documents/{doc}?mode=lib", headers=alice).json()["suivi_prepa"] is True
    assert "suivi_prepa" not in c.get(f"/lib/documents/{doc}?mode=lib", headers=zoe).json()
    assert c.delete(f"/lib/prepa/suivis/{doc}", headers=alice).json()["suivi"] is False
    assert c.get("/lib/prepa", headers=alice).json()["suivis"] == []

    # Déplacée par la console : l'emploi du temps garde la date d'origine
    nouvelle = (datetime.now(timezone.utc) + timedelta(days=6)).replace(microsecond=0)
    r = c.patch(f"/prepa/adjuris/admin/agenda/seances/{sid}", headers=A, json={"date_debut": nouvelle.isoformat()})
    assert r.status_code == 200, r.text
    seance = next(x for x in c.get("/lib/prepa", headers=alice).json()["seances"] if x["id"] == sid)
    assert seance["date_initiale"] is not None and seance["en_cours"] is False
    assert datetime.fromisoformat(seance["date_initiale"]).replace(microsecond=0) == avant.replace(
        tzinfo=timezone.utc, microsecond=0)
    # Remise à sa date : plus de trace du déplacement
    c.patch(f"/prepa/adjuris/admin/agenda/seances/{sid}", headers=A,
            json={"date_debut": avant.replace(tzinfo=timezone.utc).isoformat()})
    seance = next(x for x in c.get("/lib/prepa", headers=alice).json()["seances"] if x["id"] == sid)
    assert seance["date_initiale"] is None
    assert v["facturation"][0]["matiere_key"] == MATIERE
