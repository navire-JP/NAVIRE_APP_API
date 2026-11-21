"""
Connecteur NAVIRE (MCP) pour Claude / ChatGPT : découverte OAuth, inscription
d'application, page de connexion, PKCE, jetons et renouvellement, réservation
aux abonnés, outils MCP, coupure de l'accès par l'élève.

Lancer : pytest tests/test_mcp_connector.py
"""

import base64
import hashlib
import os
import secrets
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

for k, v in {
    "OPENAI_API_KEY": "x", "BREVO_API_KEY_MEOLES": "x",
    "STRIPE_SECRET_KEY_MEOLES": "x", "STRIPE_WEBHOOK_SECRET_MEOLES": "x",
    "STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_WEBHOOK_SECRET": "whsec_x",
    "DATABASE_URL": "sqlite:///./test_mcp_connector.db",
}.items():
    os.environ.setdefault(k, v)

from fastapi.testclient import TestClient  # noqa: E402

REDIRECT = "https://claude.ai/api/mcp/auth_callback"
PASSWORD = "MotDePasse123!"


@pytest.fixture(scope="module")
def env():
    import app.main as main
    from app.core.security import create_access_token, hash_password
    from app.db.database import Base, engine, SessionLocal
    from app.db.models import McpOAuthClient, McpOAuthCode, McpOAuthToken, User

    engine.dispose()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    for model in (McpOAuthToken, McpOAuthCode, McpOAuthClient):
        db.query(model).delete()

    def user(email, name, **kw):
        u = db.query(User).filter(User.email == email).one_or_none()
        if not u:
            u = User(email=email, username=name, password_hash=hash_password(PASSWORD), **kw)
            db.add(u)
        return u

    abonne = user("mcp-abonne@test.fr", "abonne", plan="membre")
    gratuit = user("mcp-free@test.fr", "gratuit", plan="free")
    google = User(email="mcp-google@test.fr", username="google", plan="membre", oauth_provider="google")
    if not db.query(User).filter(User.email == google.email).one_or_none():
        db.add(google)
    db.commit()
    ns = SimpleNamespace(
        abonne=abonne.id, gratuit=gratuit.id,
        auth={"Authorization": f"Bearer {create_access_token(str(abonne.id))}"},
        SessionLocal=SessionLocal,
    )
    db.close()
    # Les redirections vers claude.ai ne doivent pas être suivies par le client de test.
    ns.c = TestClient(main.app, follow_redirects=False)
    yield ns
    engine.dispose()
    path = engine.url.database
    if path and os.path.exists(path):
        os.remove(path)


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def register(c, **extra):
    body = {"client_name": "Claude", "redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], **extra}
    r = c.post("/oauth/register", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def authorize_params(client_id, challenge, **extra):
    return {
        "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": "etat-123",
        "scope": "navire", "resource": "https://navire-app-api.onrender.com/mcp", **extra,
    }


def login(c, client_id, challenge, email="mcp-abonne@test.fr", password=PASSWORD, action="allow"):
    data = {**authorize_params(client_id, challenge), "email": email, "password": password, "action": action}
    return c.post("/oauth/authorize", data=data)


def full_flow(c, client=None):
    client = client or register(c)
    verifier, challenge = pkce()
    r = login(c, client["client_id"], challenge)
    assert r.status_code == 303, r.text
    code = parse_qs(urlsplit(r.headers["location"]).query)["code"][0]
    r = c.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
        "client_id": client["client_id"], "code_verifier": verifier,
    })
    assert r.status_code == 200, r.text
    return client, r.json()


def rpc(c, token, method, params=None, msg_id=1):
    body = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    return c.post("/mcp", json=body, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream",
    })


# ============================================================
# Découverte
# ============================================================

def test_mcp_sans_jeton_renvoie_401_vers_la_decouverte(env):
    r = env.c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert r.status_code == 401
    www = r.headers["www-authenticate"]
    assert www.startswith("Bearer ")
    assert "/.well-known/oauth-protected-resource/mcp" in www

    r = env.c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                   headers={"Authorization": "Bearer faux"})
    assert r.status_code == 401
    assert 'error="invalid_token"' in r.headers["www-authenticate"]


def test_metadonnees(env):
    pr = env.c.get("/.well-known/oauth-protected-resource/mcp").json()
    assert pr["resource"].endswith("/mcp")
    issuer = pr["authorization_servers"][0]
    asm = env.c.get("/.well-known/oauth-authorization-server").json()
    assert asm["issuer"] == issuer
    assert asm["code_challenge_methods_supported"] == ["S256"]
    assert asm["registration_endpoint"].endswith("/oauth/register")
    assert "none" in asm["token_endpoint_auth_methods_supported"]


# ============================================================
# Inscription
# ============================================================

def test_inscription_refuse_les_adresses_non_https(env):
    r = env.c.post("/oauth/register", json={"redirect_uris": ["http://pirate.example/cb"]})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_redirect_uri"
    # http vers la machine locale : accepté (clients de bureau, MCP Inspector)
    r = env.c.post("/oauth/register", json={"redirect_uris": ["http://localhost:6274/cb"],
                                            "token_endpoint_auth_method": "none"})
    assert r.status_code == 201


def test_inscription_par_defaut_donne_un_secret(env):
    out = env.c.post("/oauth/register", json={"redirect_uris": [REDIRECT]}).json()
    assert out["token_endpoint_auth_method"] == "client_secret_basic"
    assert out["client_secret"]


# ============================================================
# Page d'autorisation
# ============================================================

def test_page_de_connexion(env):
    client = register(env.c)
    _, challenge = pkce()
    r = env.c.get("/oauth/authorize", params=authorize_params(client["client_id"], challenge))
    assert r.status_code == 200
    assert "Claude" in r.text and "claude.ai" in r.text
    assert 'name="password"' in r.text
    assert r.headers["x-frame-options"] == "DENY"


def test_application_inconnue_ou_retour_non_declare_ne_redirige_pas(env):
    _, challenge = pkce()
    r = env.c.get("/oauth/authorize", params=authorize_params("inconnu", challenge))
    assert r.status_code == 400 and "location" not in r.headers

    client = register(env.c)
    r = env.c.get("/oauth/authorize", params=authorize_params(
        client["client_id"], challenge, redirect_uri="https://pirate.example/cb"))
    assert r.status_code == 400 and "location" not in r.headers


def test_pkce_obligatoire(env):
    client = register(env.c)
    r = env.c.get("/oauth/authorize", params=authorize_params(client["client_id"], ""))
    assert r.status_code == 303
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert q["error"] == ["invalid_request"] and q["state"] == ["etat-123"]


def test_mauvais_mot_de_passe(env):
    client = register(env.c)
    _, challenge = pkce()
    r = login(env.c, client["client_id"], challenge, password="mauvais-mdp")
    assert r.status_code == 400
    assert "incorrect" in r.text


def test_compte_google_sans_mot_de_passe(env):
    client = register(env.c)
    _, challenge = pkce()
    r = login(env.c, client["client_id"], challenge, email="mcp-google@test.fr")
    assert r.status_code == 400
    assert "Mot de passe oublié" in r.text


def test_non_abonne_refuse_sans_code(env):
    client = register(env.c)
    _, challenge = pkce()
    r = login(env.c, client["client_id"], challenge, email="mcp-free@test.fr")
    assert r.status_code == 403
    assert "réservé aux abonnés" in r.text.lower()
    assert "code=" not in r.text


def test_refus_par_l_eleve(env):
    client = register(env.c)
    _, challenge = pkce()
    r = login(env.c, client["client_id"], challenge, password="", action="deny")
    assert r.status_code == 303
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert q["error"] == ["access_denied"] and q["state"] == ["etat-123"]


# ============================================================
# Jetons
# ============================================================

def test_parcours_complet_et_code_a_usage_unique(env):
    client = register(env.c)
    verifier, challenge = pkce()
    r = login(env.c, client["client_id"], challenge)
    assert r.status_code == 303
    loc = urlsplit(r.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == REDIRECT
    q = parse_qs(loc.query)
    assert q["state"] == ["etat-123"] and q["iss"]
    code = q["code"][0]

    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
            "client_id": client["client_id"]}
    # Mauvais vérificateur PKCE : refusé
    r = env.c.post("/oauth/token", data={**form, "code_verifier": pkce()[0]})
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"

    r = env.c.post("/oauth/token", data={**form, "code_verifier": verifier})
    assert r.status_code == 200, r.text
    tok = r.json()
    assert tok["token_type"] == "Bearer" and tok["refresh_token"] and tok["expires_in"] == 3600
    assert r.headers["cache-control"] == "no-store"

    # Le code ne sert qu'une fois
    r = env.c.post("/oauth/token", data={**form, "code_verifier": verifier})
    assert r.status_code == 400


def test_client_confidentiel_avec_secret(env):
    client = env.c.post("/oauth/register", json={
        "client_name": "ChatGPT", "redirect_uris": [REDIRECT],
        "token_endpoint_auth_method": "client_secret_basic"}).json()
    verifier, challenge = pkce()
    r = login(env.c, client["client_id"], challenge)
    code = parse_qs(urlsplit(r.headers["location"]).query)["code"][0]
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT, "code_verifier": verifier}

    r = env.c.post("/oauth/token", data={**form, "client_id": client["client_id"]})
    assert r.status_code == 401 and r.json()["error"] == "invalid_client"

    basic = base64.b64encode(f"{client['client_id']}:{client['client_secret']}".encode()).decode()
    r = env.c.post("/oauth/token", data=form, headers={"Authorization": f"Basic {basic}"})
    assert r.status_code == 200, r.text


def test_renouvellement_avec_rotation(env):
    client, tok = full_flow(env.c)
    r = env.c.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": client["client_id"]})
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["access_token"] != tok["access_token"]

    # L'ancien couple ne vaut plus rien
    assert rpc(env.c, tok["access_token"], "ping").status_code == 401
    r = env.c.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": client["client_id"]})
    assert r.status_code == 400
    assert rpc(env.c, new["access_token"], "ping").status_code == 200


# ============================================================
# Serveur MCP
# ============================================================

def test_initialize_et_liste_des_outils(env):
    _, tok = full_flow(env.c)
    r = rpc(env.c, tok["access_token"], "initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}})
    assert r.status_code == 200
    res = r.json()["result"]
    assert res["protocolVersion"] == "2025-06-18"
    assert res["serverInfo"]["name"] == "navire"
    assert "tools" in res["capabilities"]

    # Version inconnue : le serveur propose la sienne
    r = rpc(env.c, tok["access_token"], "initialize", {"protocolVersion": "1999-01-01"})
    assert r.json()["result"]["protocolVersion"] == "2025-11-25"

    r = env.c.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                   headers={"Authorization": f"Bearer {tok['access_token']}"})
    assert r.status_code == 202

    tools = rpc(env.c, tok["access_token"], "tools/list").json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == {"search_legal_base", "get_user_stats", "list_user_files", "get_latest_actus"}
    assert all(t["annotations"]["readOnlyHint"] for t in tools)

    r = rpc(env.c, tok["access_token"], "methode/inconnue")
    assert r.json()["error"]["code"] == -32601


def test_appel_d_outils(env, monkeypatch):
    from app.services import navire_chat

    vus = {}

    def fake_search(db, user, query, **kw):
        vus["user"], vus["query"] = user.id, query
        return {"found": True, "extraits": [{"source": "Cours NAVIRE Lib — Obligations", "contenu": "Art. 1240…"}]}

    monkeypatch.setattr(navire_chat, "_tool_search_legal_base", fake_search)
    _, tok = full_flow(env.c)

    r = rpc(env.c, tok["access_token"], "tools/call",
            {"name": "search_legal_base", "arguments": {"query": "responsabilité du fait personnel"}})
    res = r.json()["result"]
    assert res["isError"] is False
    assert "Art. 1240" in res["content"][0]["text"]
    assert vus == {"user": env.abonne, "query": "responsabilité du fait personnel"}

    res = rpc(env.c, tok["access_token"], "tools/call", {"name": "list_user_files", "arguments": {}}).json()["result"]
    assert res["isError"] is False and '"files"' in res["content"][0]["text"]

    r = rpc(env.c, tok["access_token"], "tools/call", {"name": "search_legal_base", "arguments": {}})
    assert r.json()["result"]["isError"] is True


def test_abonnement_arrete_coupe_les_outils(env):
    from app.db.models import User

    _, tok = full_flow(env.c)
    db = env.SessionLocal()
    u = db.get(User, env.abonne)
    u.plan = "free"
    db.commit()
    try:
        res = rpc(env.c, tok["access_token"], "tools/call",
                  {"name": "list_user_files", "arguments": {}}).json()["result"]
        assert res["isError"] is True
        assert "réservé aux abonnés" in res["content"][0]["text"]
    finally:
        u.plan = "membre"
        db.commit()
        db.close()


# ============================================================
# Côté élève : voir et couper les connexions
# ============================================================

def test_connexions_et_deconnexion(env):
    client, tok = full_flow(env.c)
    info = env.c.get("/navire/connector", headers=env.auth).json()
    assert info["allowed"] is True and info["mcp_url"].endswith("/mcp")
    assert client["client_id"] in {c["client_id"] for c in info["connections"]}

    r = env.c.delete(f"/navire/connector/connections/{client['client_id']}", headers=env.auth)
    assert r.status_code == 200
    assert rpc(env.c, tok["access_token"], "ping").status_code == 401


def test_revocation_par_l_application(env):
    client, tok = full_flow(env.c)
    r = env.c.post("/oauth/revoke", data={"token": tok["access_token"], "client_id": client["client_id"]})
    assert r.status_code == 200
    assert rpc(env.c, tok["access_token"], "ping").status_code == 401


def test_menage(env):
    from datetime import datetime, timedelta

    from app.db.models import McpOAuthClient
    from app.services.mcp_oauth import purge_expired

    old = register(env.c)
    db = env.SessionLocal()
    row = db.query(McpOAuthClient).filter_by(client_id=old["client_id"]).one()
    row.created_at = datetime.utcnow() - timedelta(days=30)
    db.commit()
    out = purge_expired(db)
    assert out["clients"] >= 1
    assert db.query(McpOAuthClient).filter_by(client_id=old["client_id"]).one_or_none() is None
    db.close()
