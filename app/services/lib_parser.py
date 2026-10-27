"""
app/services/lib_parser.py
==========================
NAVIRE Lib : transforme un cours (.docx ou .pdf) en blocs affichables par
l'embed, reconstruit le plan et place les points de passage.

Aucune dépendance à la base : le router stocke ce que renvoie
`parse_document()` dans LibDocument.content et crée les LibCheckpoint à partir
de `detect_checkpoints()`.

Blocs
-----
Chaque bloc est un dict sérialisable :

  {"id": "b12", "t": "p", "html": "...", "text": "..."}

  t = h1      ÉTAPE / PARTIE / AVANT-PROPOS / INTRODUCTION / SOURCES
      h2      TITRE 1 / I.
      h3      Chapitre 1 / A.
      h4      Section 1 / § 1 / 1°
      p       paragraphe (lead = « 3. Distribution directe.- » en tête)
      quote   texte cité (article de code reproduit, en retrait ou italique)
      goal    « Objectif de l'étape : … »
      pdp     marqueur « POINTS DE PASSAGE »
      table   tableau (rows : liste de listes de cellules texte)

Le HTML d'un bloc ne contient que des balises produites ici (strong, em,
mark, span) : le texte du document est toujours échappé.

Surlignage
----------
Le texte est surligné automatiquement par catégorie : articles de code
(nl-art), décisions de justice (nl-jp), lois, règlements et directives
(nl-loi). Le surlignage déjà présent dans un .docx est repris (nl-hl).

Points de passage
-----------------
1. Le document contient des marqueurs « POINTS DE PASSAGE » (cas du modèle
   NAVIRE Lib) : un point de passage par marqueur, placé après le tableau
   récapitulatif qui le suit. Ses questions viennent du tableau.
2. Sinon : un point de passage à la fin de chaque grande partie (h1, à
   défaut h2) assez longue, sauf avant les parties libres (sources…).
3. Sinon (texte sans plan) : tous les ~1 200 mots, à la fin d'un paragraphe.
"""

from __future__ import annotations

import html as _html
import io
import random
import re
import unicodedata
from statistics import median

# ============================================================
# Reconnaissance des titres
# ============================================================

_RE_H1 = re.compile(
    r"^(?:[ÉE]TAPE|PARTIE|PREMI[EÈ]RE\s+PARTIE|DEUXI[EÈ]ME\s+PARTIE|SECONDE\s+PARTIE|"
    r"TROISI[EÈ]ME\s+PARTIE|LIVRE|MODULE|LE[ÇC]ON)\b",
    re.I,
)
_RE_H2 = re.compile(r"^(?:TITRE|Titre)\s+(?:[0-9IVXL]+|PR[EÉ]LIMINAIRE|pr[ée]liminaire)\b")
_RE_H3 = re.compile(r"^(?:CHAPITRE|Chapitre)\s+(?:[0-9IVXL]+|pr[ée]liminaire|PR[EÉ]LIMINAIRE)\b")
_RE_H4 = re.compile(r"^(?:SECTION|Section|Sous-section|SOUS-SECTION|§)\s*[0-9IVXL]+\b")
# Plan de dissertation : « I. », « A. », « 1° »
_RE_ROMAN = re.compile(r"^[IVX]{1,4}\s?[.\-–)]\s+\S")
_RE_LETTER = re.compile(r"^[A-H]\s?[.\-–)]\s+\S")

# Parties « libres » : affichées au plan, jamais verrouillées derrière un
# point de passage.
_FREE_TITLES = (
    "AVANT-PROPOS", "AVANT PROPOS", "INTRODUCTION", "CONCLUSION", "SOURCES",
    "BIBLIOGRAPHIE", "ANNEXE", "ANNEXES", "GLOSSAIRE", "INDEX",
)
_TOC_TITLES = ("PLAN SOMMAIRE", "SOMMAIRE", "TABLE DES MATIÈRES", "TABLE DES MATIERES", "PLAN")
_PDP_TITLES = ("POINTS DE PASSAGE", "POINT DE PASSAGE", "À RETENIR", "A RETENIR", "L'ESSENTIEL")

# Ligne de sommaire : « TITRE 1. LES MODES DE DISTRIBUTION<tab>6 »
_RE_TOC_LINE = re.compile(r"(?:\t|\.{3,}|\s{3,})\s*\d{1,4}\s*$")
# Tête de paragraphe numéroté : « 3. Distribution directe.- »
_RE_LEAD = re.compile(r"^\s*(\d{1,4})\s*[.)]\s+(.{2,140}?)\s*[.]?\s*[-–—]\s+")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()


def _upper_key(s: str) -> str:
    return _norm(s).upper().rstrip(" .:")


def is_free_title(text: str) -> bool:
    k = _upper_key(text)
    return any(k == t or k.startswith(t + " ") for t in _FREE_TITLES)


def classify_heading(text: str, *, bold: bool, short: bool) -> str | None:
    """Niveau de titre d'un paragraphe, ou None si c'est du texte courant."""
    t = _norm(text)
    if not t or len(t) > 220:
        return None
    if _RE_H1.match(t):
        return "h1"
    if _RE_H2.match(t):
        return "h2"
    if _RE_H3.match(t):
        return "h3"
    if _RE_H4.match(t):
        return "h4"
    if bold and short:
        k = _upper_key(t)
        if any(k == x or k.startswith(x + " ") for x in _FREE_TITLES):
            return "h1"
        if _RE_ROMAN.match(t):
            return "h2"
        if _RE_LETTER.match(t):
            return "h3"
    return None


# ============================================================
# Surlignage
# ============================================================

_MOIS = r"(?:janv(?:ier|\.)?|f[ée]vr(?:ier|\.)?|mars|avr(?:il|\.)?|mai|juin|juil(?:l(?:et)?)?\.?|ao[uû]t|sept(?:embre|\.)?|oct(?:obre|\.)?|nov(?:embre|\.)?|d[ée]c(?:embre|\.)?)"
_DATE = rf"\d{{1,2}}(?:er)?\s+{_MOIS}\s+\d{{4}}"

_HL_PATTERNS = [
    # Décisions : « Com., 25 janv. 2017 », « CJUE, 13 oct. 2011 », « Ass. plén., 1er déc. 1995 »,
    # « Cons. const., déc. n° 2010-85 QPC », « arrêt Metro du 25 octobre 1977 »
    ("jp", rf"(?:\b(?:Cass\.\s*)?(?:Com|Civ\.\s*\d(?:re|e)?|Soc|Crim|Ass\.\s*pl[ée]n|Ch\.\s*mixte)\.?,\s*{_DATE}"
           rf"|\b(?:CJUE|CJCE|CEDH|Trib\.\s*UE|TUE|CE|Cons\.\s*const\.|CA\s+[A-ZÉ][\wéè-]+|T\.\s*com\.\s+[A-ZÉ][\wéè-]+),\s*(?:{_DATE}|d[ée]c\.\s*n°\s*[\d-]+(?:\s*QPC)?)"
           rf"|\barr[êe]t\s+[A-ZÉ][\w'’éè-]+(?:\s+[A-ZÉ][\w'’éè-]+){{0,3}}\s+du\s+{_DATE}"
           rf"|\bn°\s*\d{{2}}-\d{{2}}\.\d{{3}})"),
    # Textes : « loi n° 89-1008 du 31 décembre 1989 », « règlement (UE) 2022/720 », « directive 2014/104/UE »
    ("loi", rf"(?:\b(?:loi|ordonnance|d[ée]cret)\s+n°\s*[\d-]+(?:\s+du\s+{_DATE})?"
            r"|\br[èe]glement\s+\((?:UE|CE)\)\s*(?:n°\s*)?\d{2,4}/\d{1,4}"
            r"|\bdirective\s+(?:\((?:UE|CE)\)\s*)?\d{2,4}/\d{1,4}(?:/(?:CEE|UE|CE))?)"),
    # Articles : « article L. 330-3 », « art. 1164 », « articles L. 134-1 à L. 134-17 »
    ("art", r"(?:\b[Aa]rt(?:icles?|\.)\s+(?:[LRDA]\.?\s?)?\d+(?:[-‑]\d+)*(?:\s+(?:à|et)\s+(?:[LRDA]\.?\s?)?\d+(?:[-‑]\d+)*)?"
            r"(?:,\s*(?:paragraphe|§|al(?:in[ée]a|\.)?)\s*\d+(?:er)?)?"
            r"|\bTFUE\b)"),
]
_HL_RE = re.compile("|".join(f"(?P<{k}>{p})" for k, p in _HL_PATTERNS))


def highlight(text: str) -> str:
    """Échappe `text` et entoure les références juridiques d'un <mark>."""
    out, last = [], 0
    for m in _HL_RE.finditer(text):
        out.append(_html.escape(text[last:m.start()], quote=False))
        kind = m.lastgroup
        out.append(f'<mark class="nl-{kind}">{_html.escape(m.group(0), quote=False)}</mark>')
        last = m.end()
    out.append(_html.escape(text[last:], quote=False))
    return "".join(out)


def _run_html(text: str, *, bold=False, italic=False, marked=False) -> str:
    h = highlight(text)
    if marked:
        h = f'<mark class="nl-hl">{h}</mark>'
    if italic:
        h = f"<em>{h}</em>"
    if bold:
        h = f"<strong>{h}</strong>"
    return h


def _paragraph_html(runs: list[tuple[str, bool, bool, bool]]) -> str:
    """Runs (texte, gras, italique, surligné) → HTML, tête de paragraphe repérée."""
    # Fusionne les runs de même style : moins de balises, surlignage d'un seul
    # tenant quand Word a coupé un article en deux runs.
    merged: list[list] = []
    for text, b, i, m in runs:
        if not text:
            continue
        if merged and merged[-1][1:] == [b, i, m]:
            merged[-1][0] += text
        else:
            merged.append([text, b, i, m])
    if not merged:
        return ""
    full = "".join(r[0] for r in merged)
    lead = _RE_LEAD.match(full)
    if lead and merged[0][1]:
        # Le lead en gras du modèle NAVIRE : « 3. Distribution directe.- »
        n = lead.end()
        head, rest_runs, consumed = full[:n], [], 0
        for text, b, i, m in merged:
            if consumed + len(text) <= n:
                consumed += len(text)
                continue
            cut = max(0, n - consumed)
            rest_runs.append([text[cut:], b, i, m])
            consumed += len(text)
        body = "".join(_run_html(t, bold=b, italic=i, marked=m) for t, b, i, m in rest_runs)
        return f'<span class="nl-lead">{_html.escape(head.strip(), quote=False)}</span> {body}'
    return "".join(_run_html(t, bold=b, italic=i, marked=m) for t, b, i, m in merged)


# ============================================================
# Construction des blocs (commun docx / pdf)
# ============================================================

class _Builder:
    def __init__(self) -> None:
        self.blocks: list[dict] = []
        self.meta: dict = {"kicker": "", "titre": "", "sous_titre": "", "maj": ""}
        self._in_toc = False
        self._seen_heading = False

    def _add(self, block: dict) -> None:
        block["id"] = f"b{len(self.blocks)}"
        self.blocks.append(block)

    def paragraph(self, runs, *, centered=False, indented=False, size=None, body_size=None) -> None:
        text = _norm("".join(r[0] for r in runs))
        if not text:
            return
        visible = [r for r in runs if r[0].strip()]
        bold = bool(visible) and all(r[1] for r in visible)
        italic = bool(visible) and all(r[2] for r in visible)
        key = _upper_key(text)

        # ── En-tête du document (avant le premier titre) ──
        if not self._seen_heading and not self.blocks:
            big = size and body_size and size >= body_size * 1.5
            if re.match(r"^[ÀA]\s+jour\b", text, re.I) and not self.meta["maj"]:
                self.meta["maj"] = text
                return
            if (big or (bold and centered)) and not self.meta["titre"] and len(text) < 160:
                self.meta["titre"] = text
                return
            if centered and not self.meta["titre"] and len(text) < 80 and not self.meta["kicker"]:
                self.meta["kicker"] = text
                return
            if centered and self.meta["titre"] and not self.meta["sous_titre"] and len(text) < 300:
                self.meta["sous_titre"] = text
                return

        # ── Sommaire : reconstruit à partir des titres, on l'ignore ──
        if bold and key in _TOC_TITLES:
            self._in_toc = True
            return
        if _RE_TOC_LINE.search(text.replace("\xa0", " ")) or "\t" in "".join(r[0] for r in runs).rstrip()[-6:]:
            if self._in_toc or not self._seen_heading:
                return
        if self._in_toc:
            # Sortie du sommaire : premier paragraphe qui n'est plus une ligne
            # de sommaire (le titre de la première partie).
            self._in_toc = False

        if bold and key in _PDP_TITLES:
            self._add({"t": "pdp", "html": _html.escape(text, quote=False), "text": text})
            return

        level = classify_heading(text, bold=bold, short=len(text) < 140)
        if level:
            self._seen_heading = True
            self._add({"t": level, "html": _html.escape(text, quote=False), "text": text})
            return

        if re.match(r"^Objectifs?\s+(?:de\s+l['’][ée]tape|du\s+chapitre|de\s+la\s+partie)\s*:", text, re.I):
            self._add({"t": "goal", "html": _paragraph_html(runs), "text": text})
            return

        is_quote = (indented or italic) and (
            italic or text.startswith(("«", "\"", "Article", "Art."))
        )
        block = {"t": "quote" if is_quote else "p", "html": _paragraph_html(runs), "text": text}
        lead = _RE_LEAD.match(text)
        if lead and not is_quote:
            block["num"] = int(lead.group(1))
            block["lead"] = _norm(lead.group(2)).rstrip(".")
        self._add(block)

    def table(self, rows: list[list[str]]) -> None:
        rows = [[_norm(c) for c in r] for r in rows if any(_norm(c) for c in r)]
        if not rows:
            return
        html_rows = "".join(
            "<tr>" + "".join(
                (f"<th>{_html.escape(c, quote=False)}</th>" if i == 0 else f"<td>{highlight(c)}</td>")
                for c in r
            ) + "</tr>"
            for i, r in enumerate(rows)
        )
        self._add({
            "t": "table",
            "rows": rows,
            "html": f"<table>{html_rows}</table>",
            "text": " ".join(" ".join(r) for r in rows),
        })


# ============================================================
# DOCX
# ============================================================

def _docx_runs(p) -> list[tuple[str, bool, bool, bool]]:
    runs = []
    for r in p.runs:
        hl = False
        try:
            hl = r.font.highlight_color is not None
        except Exception:
            hl = False
        if not hl:
            rpr = r._r.rPr
            if rpr is not None:
                shd = rpr.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}shd")
                fill = shd.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}fill") if shd is not None else None
                hl = bool(fill and fill.lower() not in ("auto", "ffffff", "000000"))
        runs.append((r.text, bool(r.bold), bool(r.italic), hl))
    return runs


def parse_docx(data: bytes) -> dict:
    import docx  # python-docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = docx.Document(io.BytesIO(data))
    sizes = [r.font.size.pt for p in d.paragraphs for r in p.runs if r.font.size and r.text.strip()]
    body = median(sizes) if sizes else 12.0
    b = _Builder()
    for child in d.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, d)
            runs = _docx_runs(p)
            psizes = [r.font.size.pt for r in p.runs if r.font.size and r.text.strip()]
            fmt = p.paragraph_format
            b.paragraph(
                runs,
                centered=p.alignment == WD_ALIGN_PARAGRAPH.CENTER,
                indented=bool(fmt.left_indent and fmt.left_indent > 200000),
                size=max(psizes) if psizes else None,
                body_size=body,
            )
        elif tag == "tbl":
            t = Table(child, d)
            b.table([[c.text for c in row.cells] for row in t.rows])
    return {"blocks": b.blocks, "meta": b.meta}


# ============================================================
# PDF
# ============================================================

def parse_pdf(data: bytes) -> dict:
    import fitz  # PyMuPDF

    doc = fitz.open(stream=data, filetype="pdf")
    lines = []  # (page, y0, y1, x0, x1, page_width, spans[(text,bold,italic,size)])
    for pno, page in enumerate(doc):
        w = page.rect.width
        h = page.rect.height
        for blk in page.get_text("dict").get("blocks", []):
            for ln in blk.get("lines", []):
                spans = [
                    (s["text"], bool(s["flags"] & 16) or "Bold" in s.get("font", ""),
                     bool(s["flags"] & 2) or "Italic" in s.get("font", ""), round(s["size"], 1))
                    for s in ln.get("spans", []) if s["text"]
                ]
                txt = "".join(s[0] for s in spans).strip()
                if not txt:
                    continue
                x0, y0, x1, y1 = ln["bbox"]
                # En-têtes / pieds de page : numéro de page seul, marges haute et basse.
                if re.fullmatch(r"[-–\s]*\d{1,4}[-–\s]*(?:/\s*\d+)?", txt) and (y0 < 60 or y1 > h - 60):
                    continue
                lines.append((pno, y0, y1, x0, x1, w, spans))
    doc.close()

    sizes = [s[3] for l in lines for s in l[6] if s[0].strip()]
    body = median(sizes) if sizes else 11.0
    lefts = [l[3] for l in lines]
    margin = median(lefts) if lefts else 0

    # Lignes → paragraphes : même page, interligne normal, même style de titre.
    b = _Builder()
    cur: list = []

    def flush():
        if not cur:
            return
        runs, size = [], 0.0
        for i, l in enumerate(cur):
            for t, bo, it, sz in l[6]:
                runs.append((t, bo, it, False))
                size = max(size, sz)
            if i < len(cur) - 1:
                last = runs[-1]
                txt = last[0]
                if txt.endswith("-") and not txt.endswith(" -"):
                    runs[-1] = (txt[:-1], *last[1:])  # césure
                else:
                    runs.append((" ", last[1], last[2], False))
        first = cur[0]
        center = abs(((first[3] + first[4]) / 2) - first[5] / 2) < first[5] * 0.08 and first[3] > margin + 30
        b.paragraph(runs, centered=center, indented=first[3] > margin + 25, size=size, body_size=body)
        cur.clear()

    for l in lines:
        txt = "".join(s[0] for s in l[6]).strip()
        heading = classify_heading(txt, bold=all(s[1] for s in l[6] if s[0].strip()), short=len(txt) < 140)
        if cur:
            prev = cur[-1]
            gap = l[1] - prev[2]
            same_page = l[0] == prev[0]
            prev_size = max(s[3] for s in prev[6])
            size = max(s[3] for s in l[6])
            prev_txt = "".join(s[0] for s in prev[6]).strip()
            new_para = (
                not same_page and prev_txt.endswith((".", ":", "»"))
                or same_page and gap > body * 0.6
                or abs(size - prev_size) > 0.6
                or heading is not None
                or _RE_LEAD.match(txt) is not None
                or (l[3] > prev[3] + 12 and prev_txt.endswith((".", ":", "»")))
            )
            if new_para:
                flush()
        cur.append(l)
        if heading:
            flush()
    flush()
    return {"blocks": b.blocks, "meta": b.meta}


# ============================================================
# Entrée principale
# ============================================================

def parse_document(data: bytes, filename: str) -> dict:
    """
    Renvoie {"blocks", "plan", "meta", "stats"}. Lève ValueError si le format
    n'est pas reconnu ou si rien n'a pu être extrait.
    """
    name = (filename or "").lower()
    if name.endswith(".docx"):
        parsed = parse_docx(data)
    elif name.endswith(".pdf") or data[:4] == b"%PDF":
        parsed = parse_pdf(data)
    else:
        raise ValueError("Format non pris en charge : dépose un .docx ou un .pdf.")
    blocks = parsed["blocks"]
    if not blocks:
        raise ValueError("Aucun texte exploitable dans ce fichier (PDF scanné ?).")
    words = sum(len(bl.get("text", "").split()) for bl in blocks)
    return {
        "blocks": blocks,
        "plan": build_plan(blocks),
        "meta": parsed["meta"],
        "stats": {"blocks": len(blocks), "words": words, "minutes": max(1, round(words / 230))},
    }


def build_plan(blocks: list[dict]) -> list[dict]:
    """Plan à plat : [{id, level, text}] pour les titres h1 à h3."""
    return [
        {"id": bl["id"], "level": int(bl["t"][1]), "text": bl["text"]}
        for bl in blocks
        if bl["t"] in ("h1", "h2", "h3")
    ]


# ============================================================
# Points de passage
# ============================================================

def _section_title(blocks: list[dict], upto: int) -> str:
    for bl in reversed(blocks[: upto + 1]):
        if bl["t"] == "h1":
            return bl["text"]
    return ""


def detect_checkpoints(blocks: list[dict], *, seed: int = 0) -> list[dict]:
    """
    Points de passage proposés : [{anchor, titre, questions, source}].
    `anchor` = index du dernier bloc lisible avant le point de passage :
    la suite (anchor + 1 …) reste floutée tant qu'il n'est pas validé.
    """
    out: list[dict] = []
    n = len(blocks)

    # 1. Marqueurs « POINTS DE PASSAGE » + tableau récapitulatif
    marks = [i for i, bl in enumerate(blocks) if bl["t"] == "pdp"]
    if marks:
        all_rows = [r for bl in blocks if bl["t"] == "table" for r in _table_pairs(bl)]
        for k, i in enumerate(marks):
            anchor = i
            table = None
            for j in range(i + 1, min(n, i + 4)):
                if blocks[j]["t"] == "table":
                    anchor, table = j, blocks[j]
                    break
                if blocks[j]["t"].startswith("h"):
                    break
            section = _section_title(blocks, i)
            pairs = _table_pairs(table) if table else []
            questions = questions_from_pairs(pairs, all_rows, seed=seed * 1000 + k) if pairs else []
            if not questions:
                questions = questions_from_paragraphs(_section_blocks(blocks, i), seed=seed * 1000 + k)
            out.append({
                "anchor": anchor,
                "titre": f"Point de passage · {_short(section) or f'étape {k + 1}'}",
                "questions": questions,
                "source": "tableau" if pairs else "paragraphes",
            })
        return [c for c in out if c["questions"]]

    # 2. Fin de chaque grande partie
    for level in ("h1", "h2"):
        heads = [i for i, bl in enumerate(blocks) if bl["t"] == level]
        if len(heads) < 2:
            continue
        for k, h in enumerate(heads):
            end = heads[k + 1] - 1 if k + 1 < len(heads) else n - 1
            if k + 1 >= len(heads):
                break  # rien à verrouiller après la dernière partie
            nxt = blocks[heads[k + 1]]
            if is_free_title(blocks[h]["text"]) or is_free_title(nxt["text"]):
                continue
            sec = blocks[h: end + 1]
            words = sum(len(bl.get("text", "").split()) for bl in sec)
            if words < 250:
                continue
            questions = questions_from_paragraphs(sec, seed=seed * 1000 + k)
            if questions:
                out.append({
                    "anchor": end,
                    "titre": f"Point de passage · {_short(blocks[h]['text'])}",
                    "questions": questions,
                    "source": "paragraphes",
                })
        if out:
            return out

    # 3. Texte sans plan : tous les ~1 200 mots
    count, start, k = 0, 0, 0
    for i, bl in enumerate(blocks):
        count += len(bl.get("text", "").split())
        if count >= 1200 and bl["t"] == "p" and i < n - 3:
            questions = questions_from_paragraphs(blocks[start: i + 1], seed=seed * 1000 + k)
            if questions:
                out.append({
                    "anchor": i,
                    "titre": f"Point de passage {k + 1}",
                    "questions": questions,
                    "source": "paragraphes",
                })
                k += 1
            count, start = 0, i + 1
    return out


def anchors_perso(blocks: list[dict], *, mots: int = 1200, max_n: int = 12) -> list[tuple[int, str]]:
    """
    Où couper un PDF quelconque (fichier d'un élève) quand detect_checkpoints
    ne trouve rien : fin de chaque grande partie si le document en a, sinon
    tous les `mots` mots, à la fin d'un paragraphe. Sans questions : elles
    sont rédigées plus tard (IA). [(anchor, titre)]
    """
    n = len(blocks)
    for level in ("h1", "h2"):
        heads = [i for i, bl in enumerate(blocks) if bl["t"] == level]
        if len(heads) < 2:
            continue
        out = []
        for k, h in enumerate(heads[:-1]):
            end = heads[k + 1] - 1
            if is_free_title(blocks[h]["text"]) or is_free_title(blocks[heads[k + 1]]["text"]):
                continue
            if sum(len(bl.get("text", "").split()) for bl in blocks[h: end + 1]) >= 250:
                out.append((end, f"Point de passage · {_short(blocks[h]['text'])}"))
        if out:
            return out[:max_n]
    out, count = [], 0
    for i, bl in enumerate(blocks):
        count += len(bl.get("text", "").split())
        if count >= mots and bl["t"] == "p" and i < n - 3:
            out.append((i, f"Point de passage {len(out) + 1}"))
            count = 0
    return out[:max_n]


def locks_content(blocks: list[dict], anchor: int) -> bool:
    """
    False quand il ne reste après `anchor` que des parties libres (sources,
    bibliographie…) : le point de passage est alors le quiz final du cours,
    il ne floute rien.
    """
    free = False
    for bl in blocks[anchor + 1:]:
        if bl["t"] == "h1":
            free = is_free_title(bl["text"])
            if not free:
                return True
        elif not free and bl["t"] in ("p", "quote", "table", "h2", "h3", "h4", "goal"):
            return True
    return False


def _section_blocks(blocks: list[dict], i: int) -> list[dict]:
    start = 0
    for j in range(i, -1, -1):
        if blocks[j]["t"] == "h1":
            start = j
            break
    return blocks[start: i]


def _short(title: str, n: int = 70) -> str:
    t = re.sub(r"^(?:[ÉE]TAPE|PARTIE|TITRE)\s+\d+\.?\s*", "", _norm(title), flags=re.I)
    t = t[:1].upper() + t[1:].lower() if t.isupper() else t
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _table_pairs(table: dict | None) -> list[tuple[str, str]]:
    """Lignes (notion, à retenir) d'un tableau à deux colonnes, sans l'en-tête."""
    if not table:
        return []
    rows = table.get("rows") or []
    pairs = [(r[0], r[1]) for r in rows if len(r) >= 2 and r[0] and r[1]]
    if pairs and _fold(pairs[0][0]) in ("notion", "notions", "theme", "point"):
        pairs = pairs[1:]
    return pairs


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _mask(text: str, term: str) -> str:
    """Masque le terme recherché dans l'énoncé (sinon la réponse est dans la question)."""
    out = text
    mots = [x for x in re.split(r"[\s/'’.,()-]+", term) if len(x) > 4 and x.isalpha()]
    for w in sorted({term, *mots}, key=len, reverse=True):
        out = re.sub(re.escape(w), "▢▢▢", out, flags=re.I)
    return out


def questions_from_pairs(pairs, pool, *, seed: int = 0, n_qcm: int = 5, n_short: int = 2) -> list[dict]:
    """
    QCM : « ce qu'il faut retenir » → quelle notion ? (3 leurres pris dans le
    même tableau, puis dans les autres).
    Questions à points : la notion à retrouver sans choix proposés.
    """
    rng = random.Random(seed)
    pairs = list(pairs)
    notions = [p[0] for p in pairs]
    others = [p[0] for p in pool if p[0] not in notions]
    order = pairs[:]
    rng.shuffle(order)
    qs: list[dict] = []
    for k, (notion, retenir) in enumerate(order[: n_qcm + n_short]):
        enonce = _mask(retenir, notion)
        if k < n_qcm or len(order) <= n_qcm:
            leurres = [x for x in notions if x != notion]
            rng.shuffle(leurres)
            if len(leurres) < 3:
                extra = others[:]
                rng.shuffle(extra)
                leurres += [x for x in extra if x not in leurres]
            choices = [notion] + leurres[:3]
            if len(choices) < 2:
                continue
            rng.shuffle(choices)
            qs.append({
                "id": f"q{k + 1}",
                "type": "qcm",
                "question": f"« {enonce} » : de quelle notion s'agit-il ?",
                "choices": choices,
                "answer": choices.index(notion),
                "points": 1,
                "explication": f"{notion} : {retenir}",
            })
        else:
            qs.append({
                "id": f"q{k + 1}",
                "type": "courte",
                "question": f"Quelle notion correspond à : « {enonce} » ?",
                "accepted": [notion],
                "points": 3,
                "explication": f"{notion} : {retenir}",
            })
    return qs


def questions_from_paragraphs(section: list[dict], *, seed: int = 0, n: int = 5) -> list[dict]:
    """
    Sans tableau : on interroge sur les paragraphes numérotés (« 3. Distribution
    directe.- ») — retrouver l'intitulé d'un extrait. Un seul paragraphe, une
    question à points sur un article cité.
    """
    rng = random.Random(seed)
    paras = [bl for bl in section if bl.get("t") == "p" and bl.get("lead") and len(bl["text"]) > 120]
    qs: list[dict] = []
    if len(paras) >= 2:
        leads = [p["lead"] for p in paras]
        picks = paras[:]
        rng.shuffle(picks)
        for k, p in enumerate(picks[:n]):
            corps = _RE_LEAD.sub("", p["text"], count=1)
            extrait = corps[:260].rsplit(" ", 1)[0] + "…" if len(corps) > 260 else corps
            leurres = [x for x in leads if x != p["lead"]]
            rng.shuffle(leurres)
            choices = [p["lead"]] + leurres[:3]
            rng.shuffle(choices)
            qs.append({
                "id": f"q{k + 1}",
                "type": "qcm",
                "question": f"« {_mask(extrait, p['lead'])} » : quel est l'intitulé de ce paragraphe ?",
                "choices": choices,
                "answer": choices.index(p["lead"]),
                "points": 1,
                "explication": f"§ {p.get('num', '')} {p['lead']}".strip(),
            })
    # Question à points : l'article cité dans la partie.
    for bl in section:
        m = re.search(r"\b[Aa]rt(?:icle|\.)\s+((?:[LRD]\.\s?)?\d+(?:-\d+)*)", bl.get("text", ""))
        if m and bl.get("t") in ("p", "quote"):
            phrase = re.split(r"(?<=[.;])\s", bl["text"][max(0, m.start() - 200): m.end() + 200])
            ctx = next((s for s in phrase if m.group(1) in s), bl["text"][:240])
            if len(ctx) > 40:
                qs.append({
                    "id": f"q{len(qs) + 1}",
                    "type": "courte",
                    "question": f"Complète la référence : « {ctx.replace(m.group(1), '▢▢▢', 1)} »",
                    "accepted": [m.group(1)],
                    "points": 3,
                    "explication": f"Article {m.group(1)}.",
                })
                break
    return qs


# ============================================================
# Correction
# ============================================================

def check_short_answer(given: str, accepted: list[str]) -> bool:
    """Tolère casse, accents, articles et l'ordre « loi Doubin » / « Doubin »."""
    g = _fold(given)
    if not g:
        return False
    for a in accepted or []:
        fa = _fold(a)
        if not fa:
            continue
        if g == fa:
            return True
        core = [w for w in fa.split() if w not in ("le", "la", "les", "l", "de", "du", "des", "d", "art", "article",
                                                "loi", "arret", "reglement", "directive", "code")]
        gw = set(g.split())
        if core and all(w in gw for w in core):
            return True
        # Faute de frappe légère sur une réponse d'un mot assez long
        if len(fa) >= 6 and len(g) >= 5 and _lev(g, fa) <= 1:
            return True
    return False


def _lev(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 1:
        return 2
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def public_question(q: dict) -> dict:
    """Question sans la réponse, pour l'étudiant."""
    out = {k: q[k] for k in ("id", "type", "question", "points") if k in q}
    if q.get("type") == "qcm":
        out["choices"] = q.get("choices", [])
    return out


def grade(questions: list[dict], answers: dict) -> dict:
    total = sum(int(q.get("points", 1)) for q in questions)
    got, corrections = 0, []
    for q in questions:
        a = (answers or {}).get(q["id"])
        if q.get("type") == "qcm":
            try:
                ok = a is not None and int(a) == int(q.get("answer"))
            except (TypeError, ValueError):
                ok = False
            expected = q["choices"][int(q["answer"])] if q.get("choices") else ""
        else:
            ok = check_short_answer(str(a or ""), q.get("accepted", []))
            expected = (q.get("accepted") or [""])[0]
        pts = int(q.get("points", 1)) if ok else 0
        got += pts
        corrections.append({
            "id": q["id"], "correct": ok, "points": pts, "max": int(q.get("points", 1)),
            "expected": expected, "answer_index": q.get("answer"), "explication": q.get("explication", ""),
        })
    return {
        "points": got,
        "total": total,
        "score_pct": round(100 * got / total) if total else 0,
        "corrections": corrections,
    }
