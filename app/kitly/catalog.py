"""Référentiels Kitly : catégories, types d'offres, templates, blocs."""

from __future__ import annotations

CATEGORIES: list[dict] = [
    {"key": "design", "label": "Design et graphisme"},
    {"key": "marketing", "label": "Marketing et communication"},
    {"key": "dev", "label": "Développement web"},
    {"key": "photo_video", "label": "Photographie et vidéo"},
    {"key": "conseil", "label": "Conseil et stratégie"},
    {"key": "redaction", "label": "Rédaction"},
    {"key": "formation", "label": "Formation et coaching"},
    {"key": "artisanat", "label": "Artisanat et créations"},
    {"key": "autres", "label": "Autres services"},
]
CATEGORY_KEYS = {c["key"] for c in CATEGORIES}

OFFER_KINDS: list[dict] = [
    {"key": "physical", "label": "Produit physique", "default_cta": "order"},
    {"key": "digital", "label": "Produit numérique", "default_cta": "order"},
    {"key": "service", "label": "Prestation de service", "default_cta": "quote"},
    {"key": "appointment", "label": "Rendez-vous ou accompagnement", "default_cta": "book"},
]
OFFER_KIND_KEYS = {k["key"] for k in OFFER_KINDS}
DEFAULT_CTA = {k["key"]: k["default_cta"] for k in OFFER_KINDS}

CTA_KEYS = {"contact", "quote", "book", "order"}
PRICE_MODES = {"fixed", "range", "quote", "free"}

SOCIAL_NETWORKS = {
    "instagram", "tiktok", "youtube", "linkedin", "x", "facebook", "behance", "dribbble",
    "github", "pinterest", "twitch", "spotify", "soundcloud", "threads", "whatsapp", "malt", "website",
}

# Blocs libres de la vitrine (côté « Linktree ») et champs autorisés.
BLOCK_TYPES: dict[str, set[str]] = {
    "link": {"label", "url", "icon", "thumbnail_url"},
    "video": {"label", "url", "thumbnail_url"},  # YouTube, Vimeo, MP4
    "text": {"title", "body"},
    "header": {"title"},
    "image": {"url", "caption", "link_url"},
    "offer": {"offer_id"},  # met en avant une offre de la boutique
    "embed": {"label", "url"},  # Calendly, Spotify…
}

# Sections de la vitrine, dans l'ordre par défaut.
SECTION_KEYS = ["hero", "links", "about", "offers", "products", "portfolio", "contact"]
DEFAULT_SECTIONS = [{"key": k, "visible": True} for k in SECTION_KEYS]

# Templates : les « basiques » sont ouverts à tous, les autres exigent la
# personnalisation avancée (Custom et au-dessus).
TEMPLATES: list[dict] = [
    {"key": "blanc", "label": "Blanc", "advanced": False},
    {"key": "noir", "label": "Noir", "advanced": False},
    {"key": "cadre", "label": "Cadre", "advanced": False},
    {"key": "edito", "label": "Éditorial", "advanced": False},
    {"key": "bold", "label": "Bold (couleurs pleines)", "advanced": True},
    {"key": "studio", "label": "Studio", "advanced": True},
]
TEMPLATE_KEYS = {t["key"] for t in TEMPLATES}
BASIC_TEMPLATES = {t["key"] for t in TEMPLATES if not t["advanced"]}

FONTS = {"inter", "instrument-serif", "space-grotesk", "dm-sans", "playfair", "archivo-black", "syne", "manrope"}
BUTTON_SHAPES = {"pill", "rounded", "square"}
BACKGROUNDS = {"solid", "gradient", "blobs", "image"}

DEFAULT_THEME = {
    "template": "blanc",
    "colors": {"background": "#ffffff", "text": "#0a0a0a", "accent": "#0a0a0a", "button_text": "#ffffff"},
    "font_heading": "inter",
    "font_body": "inter",
    "button_shape": "pill",
    "background": {"type": "solid", "image_url": ""},
}

LEAD_KINDS = {"contact", "quote", "booking"}
LEAD_STATUSES = {"new", "read", "replied", "archived", "spam"}
REPORT_REASONS = {"illegal_content", "fraud", "impersonation", "ip_infringement", "inappropriate", "other"}
