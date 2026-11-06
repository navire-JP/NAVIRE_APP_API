"""
app/services/copies.py
======================
Copies Prép'AdJuris mises en forme par NAVIRE, révisions et commentaires du
correcteur, exports .docx et .pdf.

1. Mise en forme : la copie déposée (.docx ou .pdf texte) est lue par le
   lecteur de NAVIRE Lib (app/services/lib_parser.py) et rangée en blocs
   simples, sans retouche du texte de l'étudiant :

     {"id": "c3", "t": "titre|h|p|quote|table", "runs": [[texte, gras, italique]],
      "text": "…"}

   `text` est exactement la concaténation des runs : les positions des
   annotations (debut, fin) sont des positions dans `text`, et l'embed
   (professeurs.html, prepa.html) affiche les runs tels quels, si bien que
   le texte affiché et le texte stocké sont identiques.

2. Annotations (PrepaAdjurisAnnotation) : commentaire, surligné ou
   correction (passage barré, éventuellement remplacé ; ou texte inséré).
   Deux corrections ne se chevauchent pas ; commentaires et surlignés
   peuvent se recouvrir.

3. Exports : .docx avec de vraies révisions Word (w:ins / w:del) et des
   commentaires Word dans la marge, et .pdf avec les révisions en couleur et
   les commentaires numérotés. La note et l'appréciation sont en tête.
"""

from __future__ import annotations

import html as _html
import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.prepa_adjuris_config import PREPA_MATIERE_NAMES
from app.db.models import PrepaAdjurisAnnotation, PrepaAdjurisDevoir, PrepaAdjurisProfesseur, User

TYPES = ("commentaire", "surligne", "correction")
TEXTE_MAX = 4000


# ============================================================
# 1. Mise en forme
# ============================================================

_WS = re.compile(r"\s")


def _runs_propres(runs) -> list[list]:
    """Runs (texte, gras, italique, …) → [[texte, gras, italique]] : espaces
    réduits à un seul (y compris d'un run à l'autre), pas d'espace en tête
    ni en fin, runs de même style fusionnés."""
    out: list[list] = []
    prev_space = True
    for r in runs:
        texte, gras, ital = r[0] or "", bool(r[1]), bool(r[2])
        buf = []
        for ch in texte:
            if ch == "\xa0" or _WS.match(ch):
                if prev_space:
                    continue
                buf.append(" ")
                prev_space = True
            else:
                buf.append(ch)
                prev_space = False
        t = "".join(buf)
        if not t:
            continue
        if out and out[-1][1] == gras and out[-1][2] == ital:
            out[-1][0] += t
        else:
            out.append([t, gras, ital])
    while out and out[-1][0].endswith(" "):
        out[-1][0] = out[-1][0][:-1]
        if not out[-1][0]:
            out.pop()
    return out


class _CopieBuilder:
    """Même interface que lib_parser._Builder, sans réécriture du texte."""

    def __init__(self) -> None:
        self.blocks: list[dict] = []
        self.meta: dict = {}

    def _add(self, b: dict) -> None:
        b["id"] = f"c{len(self.blocks)}"
        self.blocks.append(b)

    def paragraph(self, runs, *, centered=False, indented=False, size=None, body_size=None) -> None:
        from app.services.lib_parser import classify_heading

        rs = _runs_propres(runs)
        if not rs:
            return
        text = "".join(r[0] for r in rs)
        visibles = [r for r in rs if r[0].strip()]
        gras = bool(visibles) and all(r[1] for r in visibles)
        ital = bool(visibles) and all(r[2] for r in visibles)
        grand = bool(size and body_size and size >= body_size * 1.25)
        court = len(text) < 140
        if not self.blocks and (grand or (centered and court) or (len(text) < 100 and not text.endswith((".", ";")))):
            t = "titre"
        elif court and (classify_heading(text, bold=gras, short=True) or (gras and not text.endswith((".", ":", ";")))
                        or (grand and not text.endswith("."))):
            t = "h"
        elif ital and indented:
            t = "quote"
        else:
            t = "p"
        self._add({"t": t, "runs": rs, "text": text})

    def table(self, rows: list[list[str]]) -> None:
        rows = [[" ".join((c or "").split()) for c in r] for r in rows]
        rows = [r for r in rows if any(r)]
        if rows:
            self._add({"t": "table", "rows": rows, "runs": [], "text": " ".join(" ".join(r) for r in rows)})


def mettre_en_forme(data: bytes, filename: str) -> dict:
    """Octets d'une copie → {"blocs": [...], "mots": n}. ValueError si rien
    n'est lisible (format inconnu, PDF scanné)."""
    from app.services.lib_parser import parse_docx, parse_pdf

    name = (filename or "").lower()
    b = _CopieBuilder()
    if name.endswith(".docx"):
        parse_docx(data, builder=b)
    elif name.endswith(".pdf") or data[:4] == b"%PDF":
        parse_pdf(data, builder=b)
    else:
        raise ValueError("Format non pris en charge : dépose un .docx ou un .pdf.")
    if not b.blocks:
        raise ValueError("Aucun texte lisible dans ce fichier : dépose un .docx ou un PDF exporté depuis ton traitement de texte.")
    return {"blocs": b.blocks, "mots": sum(len(x["text"].split()) for x in b.blocks)}


def contenu(db: Session, d: PrepaAdjurisDevoir) -> dict:
    """La copie mise en forme ; lue au premier besoin pour les copies déposées
    avant la mise en forme (ou dont la lecture a échoué)."""
    if d.contenu and d.contenu.get("blocs"):
        return d.contenu
    p = Path(d.path or "")
    if not p.exists():
        raise HTTPException(404, detail="Fichier de la copie introuvable sur le disque.")
    try:
        c = mettre_en_forme(p.read_bytes(), d.filename_original or p.name)
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    d.contenu = c
    db.commit()
    return c


def _bloc(c: dict, bloc_id: str) -> dict | None:
    return next((b for b in c.get("blocs") or [] if b.get("id") == bloc_id), None)


# ============================================================
# 2. Annotations
# ============================================================

def _nom(db: Session, user_id: int | None) -> str:
    if not user_id:
        return "Correcteur"
    prof = db.query(PrepaAdjurisProfesseur).filter(PrepaAdjurisProfesseur.user_id == user_id).one_or_none()
    nom = " ".join(x for x in ((prof.prenom, prof.nom) if prof else ()) if x).strip()
    if nom:
        return nom
    u = db.get(User, user_id)
    return (u.username if u else None) or "Correcteur"


def annotation_row(db: Session, a: PrepaAdjurisAnnotation, noms: dict | None = None) -> dict:
    noms = noms if noms is not None else {}
    if a.auteur_id not in noms:
        noms[a.auteur_id] = _nom(db, a.auteur_id)
    return {
        "id": a.id,
        "type": a.type,
        "bloc": a.bloc,
        "debut": a.debut,
        "fin": a.fin,
        "citation": a.citation,
        "texte": a.texte,
        "auteur": noms[a.auteur_id],
        "created_at": (a.created_at.replace(tzinfo=timezone.utc) if a.created_at and a.created_at.tzinfo is None else a.created_at).isoformat() if a.created_at else None,
    }


def _chevauche(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """Deux corrections en conflit : plages qui se recouvrent, même point
    d'insertion, ou insertion à l'intérieur d'un passage barré."""
    (d1, f1), (d2, f2) = a, b
    if d1 == f1 and d2 == f2:
        return d1 == d2
    if d1 == f1:
        return d2 < d1 < f2
    if d2 == f2:
        return d1 < d2 < f1
    return d1 < f2 and d2 < f1


def valider(
    db: Session, d: PrepaAdjurisDevoir, *, type: str, bloc: str, debut: int, fin: int, texte: str,
    ignorer_id: int | None = None,
) -> str:
    """Vérifie une annotation ; renvoie la citation (le passage visé)."""
    if type not in TYPES:
        raise HTTPException(400, detail="Type d'annotation inconnu (commentaire | surligne | correction).")
    b = _bloc(contenu(db, d), bloc)
    if b is None or b.get("t") == "table":
        raise HTTPException(400, detail="Passage introuvable dans la copie.")
    n = len(b["text"])
    if not (0 <= debut <= fin <= n):
        raise HTTPException(400, detail="Positions hors du passage.")
    texte = (texte or "").strip()
    if len(texte) > TEXTE_MAX:
        raise HTTPException(400, detail=f"Texte trop long ({TEXTE_MAX} caractères au plus).")
    if type in ("commentaire", "surligne") and debut == fin:
        raise HTTPException(400, detail="Sélectionne un passage.")
    if type == "commentaire" and not texte:
        raise HTTPException(400, detail="Le commentaire est vide.")
    if type == "correction":
        if debut == fin and not texte:
            raise HTTPException(400, detail="Rien à insérer.")
        for a in d.annotations:
            if a.id != ignorer_id and a.type == "correction" and a.bloc == bloc and _chevauche((debut, fin), (a.debut, a.fin)):
                raise HTTPException(409, detail="Ce passage porte déjà une correction : modifie-la ou retire-la d'abord.")
    return b["text"][debut:fin]


def ajouter(db: Session, d: PrepaAdjurisDevoir, auteur: User, *, type: str, bloc: str, debut: int, fin: int, texte: str) -> PrepaAdjurisAnnotation:
    citation = valider(db, d, type=type, bloc=bloc, debut=debut, fin=fin, texte=texte)
    a = PrepaAdjurisAnnotation(
        devoir_id=d.id, auteur_id=auteur.id, type=type, bloc=bloc, debut=debut, fin=fin,
        citation=citation, texte=(texte or "").strip(),
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


# ============================================================
# Découpage d'un bloc avec ses annotations (commun aux exports)
# ============================================================

@dataclass
class Morceau:
    kind: str                 # "texte" | "ins" | "cdebut" | "cfin"
    texte: str = ""
    gras: bool = False
    ital: bool = False
    barre: int | None = None  # id de la correction qui barre ce texte
    surligne: bool = False
    ann: int | None = None    # id (ins, cdebut, cfin)
    comments: list[int] = field(default_factory=list)


def _commente(a: PrepaAdjurisAnnotation) -> bool:
    """Annotations qui donnent un commentaire dans la marge."""
    return a.type == "commentaire" or (a.type == "surligne" and bool(a.texte))


def morceaux(bloc: dict, anns: list[PrepaAdjurisAnnotation]) -> list[Morceau]:
    text = bloc["text"]
    n = len(text)
    bornes = {0, n}
    pos = 0
    styles = []  # (debut, fin, gras, ital)
    for t, g, i in bloc.get("runs") or [[text, False, False]]:
        styles.append((pos, pos + len(t), g, i))
        pos += len(t)
        bornes.add(pos)
    for a in anns:
        bornes.update((max(0, min(n, a.debut)), max(0, min(n, a.fin))))
    bornes = sorted(bornes)
    out: list[Morceau] = []
    for k, p in enumerate(bornes):
        for a in anns:
            if _commente(a) and a.fin == p and a.debut < a.fin:
                out.append(Morceau("cfin", ann=a.id))
        for a in anns:
            if a.type == "correction" and a.fin == p and a.texte:
                out.append(Morceau("ins", texte=a.texte, ann=a.id))
        for a in anns:
            if _commente(a) and a.debut == p and a.debut < a.fin:
                out.append(Morceau("cdebut", ann=a.id))
        if k + 1 >= len(bornes):
            break
        q = bornes[k + 1]
        if q <= p:
            continue
        g, i = next(((s[2], s[3]) for s in styles if s[0] <= p < s[1]), (False, False))
        barre = next((a.id for a in anns if a.type == "correction" and a.debut <= p < a.fin), None)
        out.append(Morceau(
            "texte", texte=text[p:q], gras=g, ital=i, barre=barre,
            surligne=any(a.type == "surligne" and a.debut <= p < a.fin for a in anns),
            comments=[a.id for a in anns if _commente(a) and a.debut <= p < a.fin],
        ))
    return out


def _par_bloc(anns: list[PrepaAdjurisAnnotation]) -> dict[str, list[PrepaAdjurisAnnotation]]:
    out: dict[str, list] = {}
    for a in anns:
        out.setdefault(a.bloc, []).append(a)
    return out


def _entete(db: Session, d: PrepaAdjurisDevoir) -> dict:
    etudiant = db.get(User, d.user_id)
    return {
        "titre": d.titre or "Copie",
        "matiere": PREPA_MATIERE_NAMES.get(d.matiere_key, d.matiere_key),
        "etudiant": (etudiant.username if etudiant else "") or "",
        "correcteur": _nom(db, d.corrige_par_id) if d.corrige_par_id else "",
        "note": d.note or "",
        "appreciation": d.commentaire or "",
        "corrige": d.statut == "corrige",
    }


def nom_export(d: PrepaAdjurisDevoir, ext: str, annotee: bool) -> str:
    base = re.sub(r"[^\w\- ]+", "", d.titre or "copie", flags=re.UNICODE).strip() or "copie"
    return f"{base[:80]}{' - corrigée' if annotee else ''}.{ext}"


# ============================================================
# 3a. Export .docx (révisions et commentaires Word)
# ============================================================

def export_docx(db: Session, d: PrepaAdjurisDevoir, anns: list[PrepaAdjurisAnnotation]) -> bytes:
    import docx
    from docx.opc.constants import CONTENT_TYPE as CT, RELATIONSHIP_TYPE as RT
    from docx.opc.packuri import PackURI
    from docx.opc.part import Part
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    c = contenu(db, d)
    e = _entete(db, d)
    noms: dict = {}
    auteur = {a.id: noms.setdefault(a.auteur_id, _nom(db, a.auteur_id)) for a in anns}
    quand = (d.corrige_le or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")

    doc = docx.Document()
    st = doc.styles["Normal"]
    st.font.name = "Georgia"
    st.font.size = Pt(11.5)

    doc.add_heading(e["titre"], level=0)
    info = doc.add_paragraph()
    r = info.add_run(" · ".join(x for x in (e["matiere"], e["etudiant"]) if x))
    r.font.color.rgb = RGBColor(0x55, 0x5B, 0x6E)
    if e["corrige"] and (e["note"] or e["appreciation"] or e["correcteur"]):
        box = doc.add_paragraph()
        if e["note"]:
            rn = box.add_run(f"Note : {e['note']}")
            rn.bold = True
        if e["correcteur"]:
            box.add_run(("   ·   " if e["note"] else "") + f"Corrigée par {e['correcteur']}")
        if e["appreciation"]:
            for ligne in e["appreciation"].splitlines():
                if ligne.strip():
                    pa = doc.add_paragraph(ligne.strip())
                    pa.runs[0].italic = True
    doc.add_paragraph()

    ids = iter(range(1, 10**6))
    comment_ids: dict[int, int] = {}
    commentaires: list[tuple[int, PrepaAdjurisAnnotation]] = []

    def run_el(texte: str, *, gras=False, ital=False, surligne=False, supprime=False):
        rr = OxmlElement("w:r")
        rpr = OxmlElement("w:rPr")
        if gras:
            rpr.append(OxmlElement("w:b"))
        if ital:
            rpr.append(OxmlElement("w:i"))
        if surligne:
            h = OxmlElement("w:highlight")
            h.set(qn("w:val"), "yellow")
            rpr.append(h)
        if len(rpr):
            rr.append(rpr)
        t = OxmlElement("w:delText" if supprime else "w:t")
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        t.text = texte
        rr.append(t)
        return rr

    def revision(tag: str, ann_id: int):
        el = OxmlElement(tag)
        el.set(qn("w:id"), str(next(ids)))
        el.set(qn("w:author"), auteur.get(ann_id, "Correcteur"))
        el.set(qn("w:date"), quand)
        return el

    par = _par_bloc(anns)
    for b in c.get("blocs") or []:
        if b["t"] == "table":
            rows = b.get("rows") or []
            if rows:
                t = doc.add_table(rows=len(rows), cols=max(len(r) for r in rows))
                t.style = "Table Grid"
                for i, row in enumerate(rows):
                    for j, cell in enumerate(row):
                        t.cell(i, j).text = cell
            continue
        if b["t"] == "titre":
            p = doc.add_heading("", level=1)
        elif b["t"] == "h":
            p = doc.add_heading("", level=2)
        else:
            p = doc.add_paragraph()
            if b["t"] == "quote":
                p.paragraph_format.left_indent = Pt(28)
        pel = p._p
        for m in morceaux(b, par.get(b["id"], [])):
            if m.kind == "texte":
                rr = run_el(m.texte, gras=m.gras, ital=m.ital or b["t"] == "quote", surligne=m.surligne, supprime=m.barre is not None)
                if m.barre is not None:
                    w = revision("w:del", m.barre)
                    w.append(rr)
                    pel.append(w)
                else:
                    pel.append(rr)
            elif m.kind == "ins":
                w = revision("w:ins", m.ann)
                w.append(run_el(m.texte))
                pel.append(w)
            elif m.kind == "cdebut":
                cid = comment_ids.setdefault(m.ann, len(comment_ids))
                el = OxmlElement("w:commentRangeStart")
                el.set(qn("w:id"), str(cid))
                pel.append(el)
            elif m.kind == "cfin":
                cid = comment_ids.setdefault(m.ann, len(comment_ids))
                el = OxmlElement("w:commentRangeEnd")
                el.set(qn("w:id"), str(cid))
                pel.append(el)
                ref = OxmlElement("w:r")
                cr = OxmlElement("w:commentReference")
                cr.set(qn("w:id"), str(cid))
                ref.append(cr)
                pel.append(ref)
                a = next(x for x in anns if x.id == m.ann)
                commentaires.append((cid, a))

    if commentaires:
        def x(s: str) -> str:
            return _html.escape(s, quote=True)

        corps = []
        for cid, a in commentaires:
            nom = auteur.get(a.id, "Correcteur")
            initiales = "".join(w[0] for w in nom.split()[:2]).upper() or "C"
            paras = "".join(
                f'<w:p><w:r><w:t xml:space="preserve">{x(l)}</w:t></w:r></w:p>'
                for l in (a.texte.splitlines() or [""])
            )
            corps.append(
                f'<w:comment w:id="{cid}" w:author="{x(nom)}" w:date="{quand}" w:initials="{x(initiales)}">{paras}</w:comment>'
            )
        xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            + "".join(corps) + "</w:comments>"
        ).encode("utf-8")
        part = Part(PackURI("/word/comments.xml"), CT.WML_COMMENTS, xml, doc.part.package)
        doc.part.relate_to(part, RT.COMMENTS)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ============================================================
# 3b. Export .pdf (révisions en couleur, commentaires numérotés)
# ============================================================

_CSS_PDF = """
body { font-family: serif; font-size: 11.5pt; line-height: 1.55; color: #111; }
h1 { font-family: sans-serif; font-size: 19pt; margin: 0 0 4pt 0; }
h2 { font-family: sans-serif; font-size: 14pt; margin: 14pt 0 6pt 0; }
h3 { font-family: sans-serif; font-size: 12pt; margin: 12pt 0 4pt 0; }
p { margin: 0 0 8pt 0; text-align: justify; }
.info { font-family: sans-serif; font-size: 9.5pt; color: #555b6e; margin-bottom: 10pt; }
.note { font-family: sans-serif; font-size: 10.5pt; margin: 0 0 4pt 0; }
.appr { font-style: italic; color: #333; }
.quote { margin-left: 22pt; font-style: italic; }
.del { color: #c0392b; text-decoration: line-through; }
.ins { color: #1e8449; text-decoration: underline; }
.hl { background-color: #fff3a3; }
.ref { color: #2c5bd8; font-family: sans-serif; font-size: 8pt; }
.notes { font-family: sans-serif; font-size: 9.5pt; color: #333; }
.cit { color: #777; font-style: italic; }
td { border: 1px solid #999; padding: 3pt; font-size: 10pt; }
"""


def export_html(db: Session, d: PrepaAdjurisDevoir, anns: list[PrepaAdjurisAnnotation]) -> str:
    c = contenu(db, d)
    e = _entete(db, d)
    esc = lambda s: _html.escape(s or "", quote=False)  # noqa: E731
    out = [f"<h1>{esc(e['titre'])}</h1>",
           f'<p class="info">{esc(" · ".join(x for x in (e["matiere"], e["etudiant"]) if x))}</p>']
    if e["corrige"]:
        if e["note"] or e["correcteur"]:
            note = f"<b>Note : {esc(e['note'])}</b>" if e["note"] else ""
            par = f"Corrigée par {esc(e['correcteur'])}" if e["correcteur"] else ""
            out.append(f'<p class="note">{note}{" · " if note and par else ""}{par}</p>')
        for l in (e["appreciation"] or "").splitlines():
            if l.strip():
                out.append(f'<p class="appr">{esc(l.strip())}</p>')
    numeros: dict[int, int] = {}
    notes: list[tuple[int, PrepaAdjurisAnnotation]] = []
    par = _par_bloc(anns)
    for b in c.get("blocs") or []:
        if b["t"] == "table":
            rows = "".join("<tr>" + "".join(f"<td>{esc(x)}</td>" for x in r) + "</tr>" for r in b.get("rows") or [])
            out.append(f"<table>{rows}</table>")
            continue
        h = []
        for m in morceaux(b, par.get(b["id"], [])):
            if m.kind == "texte":
                t = esc(m.texte)
                if m.gras:
                    t = f"<b>{t}</b>"
                if m.ital:
                    t = f"<i>{t}</i>"
                cls = " ".join(x for x in ("del" if m.barre is not None else "", "hl" if m.surligne else "") if x)
                h.append(f'<span class="{cls}">{t}</span>' if cls else t)
            elif m.kind == "ins":
                h.append(f'<span class="ins">{esc(m.texte)}</span>')
            elif m.kind == "cfin":
                n = numeros.setdefault(m.ann, len(numeros) + 1)
                notes.append((n, next(x for x in anns if x.id == m.ann)))
                h.append(f'<span class="ref">[{n}]</span>')
        inner = "".join(h)
        if b["t"] == "titre":
            out.append(f"<h2>{inner}</h2>")
        elif b["t"] == "h":
            out.append(f"<h3>{inner}</h3>")
        elif b["t"] == "quote":
            out.append(f'<p class="quote">{inner}</p>')
        else:
            out.append(f"<p>{inner}</p>")
    if notes:
        out.append("<h3>Commentaires du correcteur</h3>")
        for n, a in sorted(notes, key=lambda x: x[0]):
            cit = a.citation if len(a.citation) <= 90 else a.citation[:87] + "…"
            out.append(f'<p class="notes"><b>[{n}]</b> <span class="cit">« {esc(cit)} »</span><br/>{esc(a.texte)}</p>')
    return "".join(out)


def export_pdf(db: Session, d: PrepaAdjurisDevoir, anns: list[PrepaAdjurisAnnotation]) -> bytes:
    import fitz  # PyMuPDF

    story = fitz.Story(html=export_html(db, d, anns), user_css=_CSS_PDF)
    buf = io.BytesIO()
    writer = fitz.DocumentWriter(buf)
    page = fitz.paper_rect("a4")
    zone = page + (56, 56, -56, -56)
    more = True
    while more:
        dev = writer.begin_page(page)
        more, _ = story.place(zone)
        story.draw(dev)
        writer.end_page()
    writer.close()
    return buf.getvalue()


def annotations_visibles(d: PrepaAdjurisDevoir, *, pour_prof: bool) -> list[PrepaAdjurisAnnotation]:
    """Le professeur voit toujours son travail ; l'étudiant, une fois la
    correction rendue."""
    if pour_prof or d.statut == "corrige":
        return list(d.annotations)
    return []


def copie_row(db: Session, d: PrepaAdjurisDevoir, *, pour_prof: bool) -> dict:
    from app.services import devoirs as dsvc

    c = contenu(db, d)
    noms: dict = {}
    return {
        "devoir": dsvc.devoir_row(db, d, pour_prof=pour_prof),
        "blocs": c.get("blocs") or [],
        "mots": c.get("mots") or 0,
        "annotations": [annotation_row(db, a, noms) for a in annotations_visibles(d, pour_prof=pour_prof)],
    }
