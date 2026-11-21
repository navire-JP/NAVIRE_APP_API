"""
app/routers/mcp_oauth.py
========================
Routes OAuth du connecteur NAVIRE (Claude, ChatGPT…). Logique dans
app/services/mcp_oauth.py, serveur MCP dans app/routers/mcp.py.

  GET  /.well-known/oauth-protected-resource[/mcp]   RFC 9728
  GET  /.well-known/oauth-authorization-server       RFC 8414
  POST /oauth/register                               RFC 7591
  GET  /oauth/authorize    page de connexion NAVIRE (email + mot de passe)
  POST /oauth/authorize    vérifie, contrôle l'abonnement, renvoie le code
  POST /oauth/token        code → jetons ; renouvellement
  POST /oauth/revoke       RFC 7009

  GET    /navire/connector                         infos pour la page profil
  DELETE /navire/connector/connections/{client_id} l'élève coupe une application
"""

from __future__ import annotations

import html
import json
import logging
from datetime import datetime, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import EMAIL_LOGO_URL, FRONTEND_URL
from app.core.security import verify_password
from app.db.database import SessionLocal, get_db
from app.db.models import User
from app.routers.auth import get_current_user
from app.services import mcp_oauth as svc

logger = logging.getLogger(__name__)

router = APIRouter(tags=["connecteur-mcp"])

AUTHORIZE_FIELDS = (
    "response_type", "client_id", "redirect_uri", "code_challenge",
    "code_challenge_method", "state", "scope", "resource",
)

NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
PAGE_HEADERS = {
    **NO_STORE,
    # La page reçoit un mot de passe : jamais dans un cadre (clickjacking).
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
}


# ============================================================
# Découverte
# ============================================================

@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource_metadata():
    return JSONResponse(svc.protected_resource_metadata())


@router.get("/.well-known/oauth-authorization-server")
@router.get("/.well-known/oauth-authorization-server/mcp")
def authorization_server_metadata():
    return JSONResponse(svc.authorization_server_metadata())


# ============================================================
# Inscription d'une application
# ============================================================

def _oauth_error(err: svc.OAuthFlowError) -> JSONResponse:
    headers = dict(NO_STORE)
    if err.status_code == 401:
        headers["WWW-Authenticate"] = 'Basic realm="navire"'
    return JSONResponse(err.as_dict(), status_code=err.status_code, headers=headers)


def _register(payload) -> dict:
    db = SessionLocal()
    try:
        return svc.register_client(db, payload)
    finally:
        db.close()


@router.post("/oauth/register")
async def register(request: Request):
    try:
        payload = json.loads(await request.body() or b"null")
    except ValueError:
        return _oauth_error(svc.OAuthFlowError("invalid_client_metadata", "JSON invalide."))
    try:
        out = await run_in_threadpool(_register, payload)
    except svc.OAuthFlowError as e:
        return _oauth_error(e)
    logger.info("Connecteur NAVIRE : application inscrite (%s)", out.get("client_name") or "sans nom")
    return JSONResponse(out, status_code=201, headers=NO_STORE)


# ============================================================
# Page d'autorisation
# ============================================================

def _with_query(uri: str, params: dict) -> str:
    params = {k: v for k, v in params.items() if v not in (None, "")}
    return uri + ("&" if "?" in uri else "?") + urlencode(params)


def _redirect_error(params: dict, error: str, description: str = "") -> RedirectResponse:
    return RedirectResponse(
        _with_query(params["redirect_uri"], {
            "error": error,
            "error_description": description,
            "state": params.get("state"),
            "iss": svc.base_url(),
        }),
        status_code=303,
    )


_STYLE = """
  :root { --navy:#0B1B3A; --ink:#14213d; --muted:#5b6478; --line:#dfe3ec; --bg:#f4f6fb; --accent:#2f5bea; --err:#b42318; }
  * { box-sizing:border-box; }
  body { margin:0; min-height:100vh; background:var(--bg); color:var(--ink);
         font-family:-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
         display:flex; align-items:center; justify-content:center; padding:24px 16px; }
  .card { width:100%; max-width:420px; background:#fff; border:1px solid var(--line); border-radius:16px;
          padding:28px 24px; box-shadow:0 10px 30px rgba(11,27,58,.08); }
  .brand { background:var(--navy); border-radius:12px; padding:14px; margin:0 0 18px; text-align:center; }
  .logo { display:inline-block; height:34px; vertical-align:middle; }
  h1 { font-size:20px; margin:0 0 8px; text-align:center; color:var(--navy); }
  p { margin:0 0 14px; line-height:1.5; font-size:14px; color:var(--muted); }
  .app { color:var(--ink); font-weight:600; }
  ul { margin:0 0 18px; padding-left:18px; font-size:14px; color:var(--ink); line-height:1.6; }
  label { display:block; font-size:13px; font-weight:600; margin:12px 0 6px; }
  input[type=email], input[type=password] { width:100%; padding:11px 12px; font-size:15px;
          border:1px solid var(--line); border-radius:10px; }
  input:focus { outline:2px solid var(--accent); outline-offset:1px; }
  .actions { display:flex; gap:10px; margin-top:20px; }
  button, .btn { flex:1; padding:12px; font-size:15px; font-weight:600; border-radius:10px; cursor:pointer;
          border:1px solid var(--navy); text-align:center; text-decoration:none; }
  .primary { background:var(--navy); color:#fff; }
  .secondary { background:#fff; color:var(--navy); }
  .error { background:#fef3f2; color:var(--err); border:1px solid #fecdca; padding:10px 12px;
           border-radius:10px; font-size:14px; margin:0 0 14px; }
  .small { font-size:12px; margin-top:16px; text-align:center; }
  a { color:var(--accent); }
"""


def _page(title: str, body: str, status_code: int = 200) -> HTMLResponse:
    doc = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} · NAVIRE</title>
<style>{_STYLE}</style>
</head>
<body><main class="card">
<div class="brand"><img class="logo" src="{html.escape(EMAIL_LOGO_URL)}" alt="NAVIRE"></div>
{body}
</main></body>
</html>"""
    return HTMLResponse(doc, status_code=status_code, headers=PAGE_HEADERS)


def _app_name(client) -> str:
    return client.client_name or svc.redirect_host(client.redirect_uris[0]) or "Une application"


def _login_page(client, params: dict, error: str = "", email: str = "") -> HTMLResponse:
    name = html.escape(_app_name(client))
    domain = html.escape(svc.redirect_host(params.get("redirect_uri") or ""))
    hidden = "\n".join(
        f'<input type="hidden" name="{f}" value="{html.escape(params.get(f) or "")}">'
        for f in AUTHORIZE_FIELDS
    )
    err = f'<div class="error" role="alert">{html.escape(error)}</div>' if error else ""
    front = html.escape(FRONTEND_URL)
    body = f"""
<h1>Connecter NAVIRE</h1>
<p><span class="app">{name}</span> demande l'accès à ton compte NAVIRE. Après ta connexion,
tu seras renvoyé vers <span class="app">{domain}</span>.</p>
<ul>
  <li>Rechercher dans les cours NAVIRE Lib, les articles, les actus et tes PDF</li>
  <li>Voir tes statistiques et la liste de tes fichiers</li>
</ul>
<p>Lecture seule : rien n'est modifié sur ton compte. Tu peux couper l'accès à tout moment depuis ton profil NAVIRE.</p>
{err}
<form method="post" action="/oauth/authorize" autocomplete="on">
{hidden}
<label for="email">Email</label>
<input id="email" name="email" type="email" required autocomplete="username" value="{html.escape(email)}">
<label for="password">Mot de passe</label>
<input id="password" name="password" type="password" required autocomplete="current-password">
<div class="actions">
  <button class="secondary" type="submit" name="action" value="deny" formnovalidate>Refuser</button>
  <button class="primary" type="submit" name="action" value="allow">Autoriser</button>
</div>
</form>
<p class="small">Compte créé avec Google ou Facebook ? Crée-toi d'abord un mot de passe avec
« Mot de passe oublié » sur <a href="{front}" target="_blank" rel="noopener">navire-ai.com</a>.</p>
"""
    return _page("Connecter NAVIRE", body, status_code=400 if error else 200)


def _error_page(message: str) -> HTMLResponse:
    body = f"""
<h1>Connexion impossible</h1>
<div class="error" role="alert">{html.escape(message)}</div>
<p>Ferme cette page et relance l'ajout du connecteur NAVIRE depuis Claude ou ChatGPT.</p>
"""
    return _page("Connexion impossible", body, status_code=400)


def _not_subscriber_page(client, params: dict) -> HTMLResponse:
    back = _with_query(params["redirect_uri"], {
        "error": "access_denied",
        "error_description": "Le connecteur NAVIRE est réservé aux abonnés.",
        "state": params.get("state"),
        "iss": svc.base_url(),
    })
    subscribe = FRONTEND_URL.rstrip("/") + "/subscribe"
    body = f"""
<h1>Réservé aux abonnés</h1>
<p>Le connecteur NAVIRE pour <span class="app">{html.escape(_app_name(client))}</span> est inclus dans
les abonnements NAVIRE AI et NAVIRE AI+, et dans la prépa AdJuris.</p>
<div class="actions">
  <a class="btn secondary" href="{html.escape(back)}">Retour</a>
  <a class="btn primary" href="{html.escape(subscribe)}" target="_blank" rel="noopener">Voir les abonnements</a>
</div>
<p class="small">Une fois abonné, relance la connexion depuis {html.escape(_app_name(client))}.</p>
"""
    return _page("Réservé aux abonnés", body, status_code=403)


@router.get("/oauth/authorize")
def authorize_page(request: Request, db: Session = Depends(get_db)):
    params = {f: request.query_params.get(f, "") for f in AUTHORIZE_FIELDS}
    try:
        client = svc.check_authorize_params(db, params)
    except svc.OAuthFlowError as e:
        return _error_page(e.description)
    try:
        svc.check_redirectable_params(params)
    except svc.OAuthFlowError as e:
        return _redirect_error(params, e.error, e.description)
    return _login_page(client, params)


@router.post("/oauth/authorize")
def authorize_submit(
    action: str = Form("allow"),
    email: str = Form(""),
    password: str = Form(""),
    response_type: str = Form(""),
    client_id: str = Form(""),
    redirect_uri: str = Form(""),
    code_challenge: str = Form(""),
    code_challenge_method: str = Form(""),
    state: str = Form(""),
    scope: str = Form(""),
    resource: str = Form(""),
    db: Session = Depends(get_db),
):
    params = {
        "response_type": response_type, "client_id": client_id, "redirect_uri": redirect_uri,
        "code_challenge": code_challenge, "code_challenge_method": code_challenge_method,
        "state": state, "scope": scope, "resource": resource,
    }
    try:
        client = svc.check_authorize_params(db, params)
    except svc.OAuthFlowError as e:
        return _error_page(e.description)
    try:
        svc.check_redirectable_params(params)
    except svc.OAuthFlowError as e:
        return _redirect_error(params, e.error, e.description)

    if action == "deny":
        return _redirect_error(params, "access_denied", "Accès refusé par l'utilisateur.")

    email_n = (email or "").strip().lower()
    user = db.execute(select(User).where(User.email == email_n)).scalar_one_or_none() if email_n else None
    if user is not None and not user.password_hash:
        return _login_page(
            client, params, email=email_n,
            error=f"Ce compte se connecte avec {user.oauth_provider or 'Google ou Facebook'}. "
                  "Crée-toi un mot de passe avec « Mot de passe oublié » sur navire-ai.com, puis reviens ici.",
        )
    if user is None or not password or not verify_password(password, user.password_hash):
        return _login_page(client, params, email=email_n, error="Email ou mot de passe incorrect.")

    if not svc.is_subscriber(db, user):
        return _not_subscriber_page(client, params)

    code = svc.create_code(db, client, user, params)
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    logger.info("Connecteur NAVIRE : user %s autorise %s", user.id, _app_name(client))
    return RedirectResponse(
        _with_query(redirect_uri, {"code": code, "state": state, "iss": svc.base_url()}),
        status_code=303,
    )


# ============================================================
# Jetons
# ============================================================

@router.post("/oauth/token")
def token(
    grant_type: str = Form(""),
    code: str | None = Form(None),
    redirect_uri: str | None = Form(None),
    code_verifier: str | None = Form(None),
    refresh_token: str | None = Form(None),
    client_id: str | None = Form(None),
    client_secret: str | None = Form(None),
    resource: str | None = Form(None),
    scope: str | None = Form(None),
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    form = {
        "code": code, "redirect_uri": redirect_uri, "code_verifier": code_verifier,
        "refresh_token": refresh_token, "resource": resource, "scope": scope,
    }
    try:
        client = svc.authenticate_client(db, authorization, client_id, client_secret)
        if grant_type == "authorization_code":
            out = svc.exchange_code(db, client, form)
        elif grant_type == "refresh_token":
            out = svc.refresh_tokens(db, client, form)
        else:
            raise svc.OAuthFlowError("unsupported_grant_type", f"grant_type non pris en charge : {grant_type or '(vide)'}")
    except svc.OAuthFlowError as e:
        return _oauth_error(e)
    return JSONResponse(out, headers=NO_STORE)


@router.post("/oauth/revoke")
def revoke(
    token: str = Form(""),
    token_type_hint: str | None = Form(None),
    client_id: str | None = Form(None),
    client_secret: str | None = Form(None),
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    try:
        client = svc.authenticate_client(db, authorization, client_id, client_secret)
    except svc.OAuthFlowError as e:
        return _oauth_error(e)
    svc.revoke_token(db, client, token)
    return JSONResponse({}, headers=NO_STORE)


# ============================================================
# Côté élève (page profil NAVIRE)
# ============================================================

@router.get("/navire/connector")
def connector_info(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Adresse à coller dans Claude / ChatGPT, droit d'accès, applications connectées."""
    return {
        "mcp_url": svc.resource_url(),
        "allowed": svc.is_subscriber(db, current_user),
        "connections": svc.list_connections(db, current_user),
    }


@router.delete("/navire/connector/connections/{client_id}")
def connector_disconnect(
    client_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    n = svc.revoke_connection(db, current_user, client_id)
    if not n:
        raise HTTPException(status_code=404, detail="Aucune connexion active pour cette application.")
    return {"ok": True, "revoked": n}
