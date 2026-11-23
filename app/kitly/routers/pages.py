"""
Pages HTML servies par l'API Kitly.

  GET /kitly/static/vitrine.js      moteur de rendu des vitrines (page publique, embed, builder)
  GET /kitly/static/marketplace.js  marketplace (embeds marketplace et accueil)
  GET /kitly/p/{slug}            vitrine complète, prête à être servie sur kitly.fr/<slug>

Pourquoi une page rendue par le serveur, en plus de l'embed Framer : quand un
lien de vitrine est collé sur WhatsApp, LinkedIn, Instagram ou iMessage, l'aperçu
(photo, nom, accroche) est lu dans les balises <meta> de la réponse HTML, sans
exécuter de JavaScript. Seule une page servie avec ces balises donne un aperçu
correct — et elle est aussi la mieux référencée.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.kitly.config import KITLY_FRONTEND_URL, KITLY_PRIVACY_URL
from app.kitly.queries import get_public_profile
from app.kitly.routers.public import vitrine_payload

router = APIRouter(tags=["kitly · pages"])

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
STATIC_FILES = {  # liste blanche
    "vitrine.js": "application/javascript; charset=utf-8",
    "marketplace.js": "application/javascript; charset=utf-8",
    "cart.js": "application/javascript; charset=utf-8",
    "checkout.js": "application/javascript; charset=utf-8",
    "logo.png": "image/png",   # logo Kitly : déposer le fichier dans app/kitly/static/logo.png
    "hero-home.webp": "image/webp",   # photo du hero de l'accueil (home.html, visiteur)
}


@router.get("/static/{name}", include_in_schema=False)
def static_file(name: str):
    if name not in STATIC_FILES or not (STATIC_DIR / name).is_file():
        return HTMLResponse("Not found", status_code=404)
    return FileResponse(
        STATIC_DIR / name,
        media_type=STATIC_FILES[name],
        headers={"Cache-Control": "public, max-age=300", "Access-Control-Allow-Origin": "*"},
    )


def _e(value: str) -> str:
    return html.escape(value or "", quote=True)


def _json_for_script(data: dict) -> str:
    """JSON sûr à l'intérieur d'une balise <script> (pas de </script> ni de séparateurs JS)."""
    raw = json.dumps(data, ensure_ascii=False)
    return (raw.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
               .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def _noscript(data: dict) -> str:
    """Contenu lisible sans JavaScript (robots, lecteurs d'écran très anciens)."""
    links = "".join(
        f'<li><a href="{_e(b.get("url", ""))}" rel="nofollow">{_e(b.get("label") or b.get("url", ""))}</a></li>'
        for b in data.get("blocks", []) if b.get("type") == "link"
    )
    offers = "".join(f"<li>{_e(o['title'])} — {_e(o['price_label'])}</li>" for o in data.get("offers", []))
    return (
        f"<h1>{_e(data['display_name'])}</h1><p>{_e(data['headline'])} · {_e(data['location'])}</p>"
        f"<p>{_e(data['bio'])}</p>" + (f"<ul>{links}</ul>" if links else "") + (f"<h2>Offres</h2><ul>{offers}</ul>" if offers else "")
    )


# Mêmes règles que vitrine.js : couleurs fixes pour les templates de base,
# couleurs du freelance pour Bold et Studio.
_PRESET_BG = {"blanc": "#ffffff", "noir": "#0a0a0a", "cadre": "#ffffff", "edito": "#ffffff",
              "bold": "#8B1E1E", "studio": "#F2EFE9"}


def background_color(theme: dict) -> str:
    tpl = theme.get("template") if theme.get("template") in _PRESET_BG else "blanc"
    if tpl in ("bold", "studio"):
        color = (theme.get("colors") or {}).get("background") or _PRESET_BG[tpl]
        if isinstance(color, str) and len(color) == 7 and color.startswith("#"):
            return color
    return _PRESET_BG[tpl]


def _not_found() -> HTMLResponse:
    body = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>Vitrine introuvable · Kitly</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700;900&family=Instrument+Serif:ital@1&display=swap" rel="stylesheet">
<style>body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;font-family:Inter,Arial,sans-serif;background:#fff;color:#0a0a0a;text-align:center;padding:24px}}
h1{{font-size:30px;font-weight:900;letter-spacing:-.03em;margin:0 0 8px}}em{{font-family:'Instrument Serif',serif;font-weight:400}}p{{color:#6f6f6c}}
a{{display:inline-block;margin-top:14px;background:#0a0a0a;color:#fff;text-decoration:none;font-weight:700;padding:12px 22px;border-radius:99px}}</style></head>
<body><div><h1>Cette vitrine <em>n'existe pas</em></h1><p>Elle a peut-être changé d'adresse ou n'est pas encore publiée.</p>
<a href="{_e(KITLY_FRONTEND_URL)}">Découvrir Kitly</a></div></body></html>"""
    return HTMLResponse(body, status_code=404)


@router.get("/p/{slug}", response_class=HTMLResponse, include_in_schema=False)
def vitrine_page(slug: str, db: Session = Depends(get_db)):
    profile = get_public_profile(db, slug)
    if not profile:
        return _not_found()
    data = vitrine_payload(db, profile)
    title = data["display_name"] + (f" — {data['headline']}" if data["headline"] else "")
    desc = data["bio"] or data["headline"] or f"La vitrine de {data['display_name']} sur Kitly."
    image = data["banner_url"] or data["avatar_url"]
    bg = background_color(data["theme"])
    # Une adresse en /profile#<adresse> ne désigne pas une page à part pour les moteurs
    # ni pour les aperçus de partage : on ne la déclare alors ni canonique ni og:url.
    url = data["url"]
    canonical = f'<link rel="canonical" href="{_e(url)}">\n' if "#" not in url else ""
    og_url = f'<meta property="og:url" content="{_e(url)}">\n' if "#" not in url else ""
    page = f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)} | Kitly</title>
<meta name="description" content="{_e(desc[:300])}">
{canonical}<meta name="theme-color" content="{_e(bg)}">
<meta property="og:type" content="profile">
<meta property="og:site_name" content="Kitly">
<meta property="og:title" content="{_e(title)}">
<meta property="og:description" content="{_e(desc[:300])}">
{og_url}{f'<meta property="og:image" content="{_e(image)}">' if image else ''}
<meta name="twitter:card" content="{'summary_large_image' if data['banner_url'] else 'summary'}">
<link rel="icon" href="/kitly/static/logo.png" type="image/png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<style>html,body{{margin:0;padding:0;background:{_e(bg)};min-height:100%}}#kv-root{{min-height:100vh}}#kv-root>.kv{{min-height:100vh}}</style>
</head>
<body>
<div id="kv-root"><noscript>{_noscript(data)}</noscript></div>
<script id="kv-data" type="application/json">{_json_for_script(data)}</script>
<script src="/kitly/static/vitrine.js"></script>
<script>
(function () {{
  var data = JSON.parse(document.getElementById("kv-data").textContent);
  var src = "direct";
  try {{
    var q = new URLSearchParams(location.search).get("src");
    if (q === "marketplace" || /\\/marketplace|\\/freelances/.test(document.referrer)) src = "marketplace";
  }} catch (e) {{}}
  KitlyVitrine.mount(document.getElementById("kv-root"), data, {{
    apiBase: "", source: src, privacyUrl: {json.dumps(KITLY_PRIVACY_URL)}, kitlyUrl: {json.dumps(KITLY_FRONTEND_URL)},
    floatingCart: true, checkoutUrl: {json.dumps(KITLY_FRONTEND_URL + "/checkout")}
  }});
}})();
</script>
</body>
</html>"""
    return HTMLResponse(page, headers={"Cache-Control": "public, max-age=60"})
