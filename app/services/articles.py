"""
Articles NAVIRE : textes rédigés sur le site par un doctorant vérifié ou par
l'équipe, publiés dans Actus (et listés dans Lib et sur le profil).

Un article est une ligne de veille_items avec kind="article" : il profite
ainsi de l'enregistrement (« À relire »), des posts du profil et de l'onglet
Fichiers sans code en double.

Le texte arrive en HTML depuis l'éditeur (gras, italique, listes, emojis).
Il est nettoyé ici avant tout enregistrement : seule une courte liste de
balises survit, sans aucun attribut, pour qu'aucun script ni lien piégé ne
puisse être publié.
"""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser

from sqlalchemy.orm import Session

from app.core.config import FRONTEND_URL
from app.db.models import User

ARTICLE_KIND = "article"
SOURCE_NAME = "NAVIRE Articles"

MAX_BODY_CHARS = 60_000      # HTML nettoyé
MIN_TEXT_CHARS = 80          # texte brut minimal d'un article
EXCERPT_CHARS = 320

# Balise d'origine → balise conservée. Tout le reste disparaît (le texte reste).
_ALLOWED = {
    "p": "p", "div": "p", "br": "br",
    "strong": "strong", "b": "strong",
    "em": "em", "i": "em",
    "u": "u",
    "ul": "ul", "ol": "ol", "li": "li",
    "blockquote": "blockquote",
    "h2": "h3", "h3": "h3", "h4": "h3",
}
_VOID = {"br"}
_BLOCK = {"p", "ul", "ol", "li", "blockquote", "h3"}
# Contenu supprimé avec sa balise.
_DROP_CONTENT = {"script", "style", "iframe", "object", "embed", "template", "noscript", "svg", "math", "head", "title"}


class _Cleaner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _DROP_CONTENT:
            self.skip += 1
            return
        if self.skip:
            return
        mapped = _ALLOWED.get(tag)
        if not mapped:
            return
        if mapped in _VOID:
            self.out.append("<br>")
            return
        self.out.append(f"<{mapped}>")
        self.stack.append(mapped)

    def handle_startendtag(self, tag, attrs):
        if tag.lower() == "br" and not self.skip:
            self.out.append("<br>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _DROP_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        mapped = _ALLOWED.get(tag)
        if not mapped or mapped in _VOID or mapped not in self.stack:
            return
        # Ferme proprement tout ce qui a été ouvert depuis.
        while self.stack:
            top = self.stack.pop()
            self.out.append(f"</{top}>")
            if top == mapped:
                break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data, quote=False))

    def result(self) -> str:
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out)


def sanitize_html(raw: str) -> str:
    """HTML de l'éditeur → HTML sûr (balises de la liste blanche, aucun attribut)."""
    cleaner = _Cleaner()
    cleaner.feed(raw or "")
    cleaner.close()
    out = cleaner.result()
    # Blocs vides laissés par l'éditeur (<p><br></p> en fin de texte, etc.)
    out = re.sub(r"<(p|h3|blockquote|li)>(\s|<br>)*</\1>", "", out)
    out = re.sub(r"(<br>\s*){3,}", "<br><br>", out)
    return out.strip()


def html_to_text(clean_html: str) -> str:
    """Texte brut d'un HTML nettoyé : extrait, recherche, durée de lecture."""
    text = re.sub(r"<br>", "\n", clean_html or "")
    text = re.sub(r"</(p|li|h3|blockquote)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t ]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def excerpt(text: str, limit: int = EXCERPT_CHARS) -> str:
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    cut = flat[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:.") + "…"


def article_url(item_id: int) -> str:
    """Adresse publique d'un article : navire-ai.com/actus/NA<id>."""
    return f"{FRONTEND_URL}/actus/NA{item_id}"


def can_publish(db: Session, user: User | None) -> bool:
    """Seuls les admins et les doctorants vérifiés publient des articles."""
    if user is None:
        return False
    if user.is_admin:
        return True
    from app.services.user_categories import user_badges

    return "Doctorant" in user_badges(db, user)


def can_edit(db: Session, user: User | None, item) -> bool:
    """L'auteur (toujours autorisé à publier) ou un admin."""
    if user is None:
        return False
    if user.is_admin:
        return True
    return item.author_id == user.id and can_publish(db, user)
