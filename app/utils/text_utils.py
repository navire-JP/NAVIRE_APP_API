import re
import unicodedata


def normalize_text(text: str) -> str:
    """
    Nettoie une chaîne : trim, unicodes normalisés, espaces réduits.
    """
    if not text:
        return ""
    text = text.strip()
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", " ", text)
    return text


def extract_keywords(text: str, limit: int = 5) -> list[str]:
    """
    Découpe un texte en mots-clés simples (très basique).
    """
    words = re.findall(r"[a-zA-ZÀ-ÖØ-öø-ÿ]+", text.lower())
    uniq = []
    for w in words:
        if w not in uniq:
            uniq.append(w)
        if len(uniq) >= limit:
            break
    return uniq
