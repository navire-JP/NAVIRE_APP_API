"""
app/services/mcp_oauth.py
=========================
Serveur d'autorisation OAuth 2.1 du connecteur NAVIRE (MCP).

Un élève abonné ajoute NAVIRE dans son Claude ou son ChatGPT. L'application
suit alors le parcours standard du protocole MCP :

  1. elle appelle /mcp sans jeton → 401 qui pointe vers
     /.well-known/oauth-protected-resource/mcp ;
  2. elle lit les métadonnées (/.well-known/oauth-authorization-server) ;
  3. elle s'inscrit d'elle-même (POST /oauth/register, RFC 7591) ;
  4. elle ouvre /oauth/authorize : l'élève se connecte avec son email et son
     mot de passe NAVIRE, et autorise l'accès (PKCE S256 obligatoire) ;
  5. elle échange le code contre un jeton (POST /oauth/token), qu'elle
     renouvelle ensuite elle-même (refresh_token, remplacé à chaque usage).

Jetons et codes sont aléatoires (opaques) et seule leur empreinte SHA-256 est
gardée en base : une fuite de la base ne donne accès à aucun compte.

Le connecteur est réservé aux abonnés (même règle que NAVIRE Lib+, voir
is_subscriber) : contrôlé à l'autorisation ET à chaque appel d'outil, pour
qu'un abonnement qui s'arrête coupe aussi le connecteur.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlsplit

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.core.config import PUBLIC_API_URL
from app.db.models import McpOAuthClient, McpOAuthCode, McpOAuthToken, User

SCOPE = "navire"
CODE_TTL = timedelta(minutes=10)
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)

AUTH_METHODS = ("none", "client_secret_post", "client_secret_basic")
GRANT_TYPES = ("authorization_code", "refresh_token")
MAX_REDIRECT_URIS = 10
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")


# ============================================================
# Adresses publiques
# ============================================================

def base_url() -> str:
    """URL publique de l'API (MCP_PUBLIC_URL, sinon API_BASE_URL / Render)."""
    return (os.getenv("MCP_PUBLIC_URL") or PUBLIC_API_URL).rstrip("/")


def resource_url() -> str:
    """Identifiant de la ressource protégée (RFC 8707) : l'adresse du serveur MCP."""
    return base_url() + "/mcp"


def protected_resource_metadata_url() -> str:
    return base_url() + "/.well-known/oauth-protected-resource/mcp"


def protected_resource_metadata() -> dict:
    """RFC 9728 : où obtenir un jeton pour /mcp."""
    return {
        "resource": resource_url(),
        "authorization_servers": [base_url()],
        "scopes_supported": [SCOPE],
        "bearer_methods_supported": ["header"],
        "resource_name": "NAVIRE",
        "resource_documentation": os.getenv("FRONTEND_URL", "https://navire-ai.com"),
    }


def authorization_server_metadata() -> dict:
    """RFC 8414."""
    b = base_url()
    return {
        "issuer": b,
        "authorization_endpoint": b + "/oauth/authorize",
        "token_endpoint": b + "/oauth/token",
        "registration_endpoint": b + "/oauth/register",
        "revocation_endpoint": b + "/oauth/revoke",
        "scopes_supported": [SCOPE],
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": list(GRANT_TYPES),
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": list(AUTH_METHODS),
        "revocation_endpoint_auth_methods_supported": list(AUTH_METHODS),
        "authorization_response_iss_parameter_supported": True,
    }


# ============================================================
# Outils
# ============================================================

class OAuthFlowError(Exception):
    """Erreur au format OAuth (error / error_description), code HTTP inclus."""

    def __init__(self, error: str, description: str = "", status_code: int = 400):
        super().__init__(description or error)
        self.error = error
        self.description = description
        self.status_code = status_code

    def as_dict(self) -> dict:
        out = {"error": self.error}
        if self.description:
            out["error_description"] = self.description
        return out


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _new_secret(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def is_subscriber(db: Session, user: User | None) -> bool:
    """Abonnés NAVIRE AI / NAVIRE AI+, inscrits AdJuris actifs, admins."""
    from app.routers.lib import has_plus

    return has_plus(db, user)


def valid_redirect_uri(uri: str) -> bool:
    """
    https obligatoire, sauf http vers la machine locale (clients de bureau,
    MCP Inspector). Pas de fragment. MCP_ALLOWED_REDIRECT_HOSTS (liste séparée
    par des virgules, ex. « claude.ai,chatgpt.com ») restreint en plus les
    domaines acceptés, si on veut fermer le connecteur aux autres clients.
    """
    if not isinstance(uri, str) or not uri or len(uri) > 2000:
        return False
    try:
        parts = urlsplit(uri)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.fragment or not host:
        return False
    if parts.scheme == "https":
        pass
    elif parts.scheme == "http" and host in LOOPBACK_HOSTS:
        pass
    else:
        return False
    allowed = [h.strip().lower() for h in os.getenv("MCP_ALLOWED_REDIRECT_HOSTS", "").split(",") if h.strip()]
    if allowed and host not in LOOPBACK_HOSTS:
        return any(host == h or host.endswith("." + h) for h in allowed)
    return True


def redirect_host(uri: str) -> str:
    try:
        return (urlsplit(uri).hostname or "").lower()
    except ValueError:
        return ""


def valid_resource(resource: str | None) -> bool:
    """
    RFC 8707 : la ressource demandée doit être ce serveur MCP. On compare le
    chemin seulement (« /mcp » ou la racine), pour ne pas dépendre du domaine
    exact par lequel l'application est passée (Render, domaine perso).
    """
    if not resource:
        return True
    try:
        path = urlsplit(resource).path.rstrip("/")
    except ValueError:
        return False
    return path in ("", "/mcp")


# ============================================================
# Inscription des applications (RFC 7591)
# ============================================================

def register_client(db: Session, payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise OAuthFlowError("invalid_client_metadata", "Corps JSON attendu.")

    uris = payload.get("redirect_uris")
    if not isinstance(uris, list) or not uris or len(uris) > MAX_REDIRECT_URIS:
        raise OAuthFlowError("invalid_redirect_uri", "redirect_uris : liste de 1 à 10 adresses attendue.")
    for uri in uris:
        if not valid_redirect_uri(uri):
            raise OAuthFlowError("invalid_redirect_uri", f"Adresse de retour refusée : {str(uri)[:200]}")

    # RFC 7591 : client_secret_basic par défaut si la méthode n'est pas précisée.
    method = payload.get("token_endpoint_auth_method") or "client_secret_basic"
    if method not in AUTH_METHODS:
        raise OAuthFlowError("invalid_client_metadata", f"token_endpoint_auth_method non pris en charge : {method}")

    grants = payload.get("grant_types") or ["authorization_code", "refresh_token"]
    if not isinstance(grants, list) or any(g not in GRANT_TYPES for g in grants) or "authorization_code" not in grants:
        raise OAuthFlowError("invalid_client_metadata", "grant_types : authorization_code et refresh_token seulement.")
    responses = payload.get("response_types") or ["code"]
    if responses != ["code"]:
        raise OAuthFlowError("invalid_client_metadata", "response_types : « code » seulement.")

    name = str(payload.get("client_name") or "").strip()[:200]

    client_id = "navire_" + secrets.token_urlsafe(18)
    secret = None if method == "none" else _new_secret("ncs")
    client = McpOAuthClient(
        client_id=client_id,
        client_secret_hash=_hash(secret) if secret else None,
        client_name=name,
        redirect_uris=list(uris),
        token_endpoint_auth_method=method,
    )
    db.add(client)
    db.commit()

    out = {
        "client_id": client_id,
        "client_id_issued_at": int(datetime.now(timezone.utc).timestamp()),
        "client_name": name,
        "redirect_uris": list(uris),
        "token_endpoint_auth_method": method,
        "grant_types": grants,
        "response_types": ["code"],
        "scope": SCOPE,
    }
    if secret:
        out["client_secret"] = secret
        out["client_secret_expires_at"] = 0
    return out


def get_client(db: Session, client_id: str | None) -> McpOAuthClient | None:
    if not client_id:
        return None
    return db.execute(
        select(McpOAuthClient).where(McpOAuthClient.client_id == client_id)
    ).scalar_one_or_none()


def authenticate_client(
    db: Session,
    authorization_header: str | None,
    form_client_id: str | None,
    form_client_secret: str | None,
) -> McpOAuthClient:
    """
    Identifie l'application au point /oauth/token : en-tête Basic
    (client_secret_basic), secret dans le formulaire (client_secret_post) ou
    client_id seul pour un client public. Un client qui a reçu un secret doit
    toujours le présenter.
    """
    client_id, secret = form_client_id, form_client_secret
    if authorization_header and authorization_header.lower().startswith("basic "):
        try:
            raw = base64.b64decode(authorization_header[6:].strip()).decode("utf-8")
            cid, _, sec = raw.partition(":")
            client_id, secret = unquote(cid), unquote(sec)
        except Exception:
            raise OAuthFlowError("invalid_client", "En-tête Basic illisible.", 401)

    client = get_client(db, client_id)
    if client is None:
        raise OAuthFlowError("invalid_client", "Application inconnue.", 401)
    if client.client_secret_hash:
        if not secret or not hmac.compare_digest(_hash(secret), client.client_secret_hash):
            raise OAuthFlowError("invalid_client", "Secret de l'application invalide.", 401)
    return client


# ============================================================
# Codes d'autorisation
# ============================================================

def check_authorize_params(db: Session, params: dict) -> McpOAuthClient:
    """
    Contrôles qui interdisent de renvoyer vers l'application (client ou
    adresse de retour inconnus) : lèvent OAuthFlowError, que la page affiche
    au lieu de rediriger.
    """
    client = get_client(db, params.get("client_id"))
    if client is None:
        raise OAuthFlowError("invalid_client", "Cette application n'est pas (ou plus) inscrite auprès de NAVIRE. Relance la connexion depuis Claude ou ChatGPT.")
    redirect_uri = params.get("redirect_uri") or ""
    if redirect_uri not in (client.redirect_uris or []):
        raise OAuthFlowError("invalid_request", "Adresse de retour non déclarée par cette application.")
    return client


def check_redirectable_params(params: dict) -> None:
    """Erreurs qu'on peut renvoyer à l'application (par l'adresse de retour)."""
    if params.get("response_type") != "code":
        raise OAuthFlowError("unsupported_response_type", "Seul response_type=code est pris en charge.")
    challenge = params.get("code_challenge") or ""
    if not challenge or not (43 <= len(challenge) <= 128):
        raise OAuthFlowError("invalid_request", "PKCE obligatoire (code_challenge).")
    if (params.get("code_challenge_method") or "") != "S256":
        raise OAuthFlowError("invalid_request", "PKCE : seule la méthode S256 est acceptée.")
    if not valid_resource(params.get("resource")):
        raise OAuthFlowError("invalid_target", "Ressource inconnue.")


def create_code(db: Session, client: McpOAuthClient, user: User, params: dict) -> str:
    code = _new_secret("nac")
    db.add(McpOAuthCode(
        code_hash=_hash(code),
        client_id=client.client_id,
        user_id=user.id,
        redirect_uri=params["redirect_uri"],
        code_challenge=params["code_challenge"],
        scope=SCOPE,
        resource=params.get("resource") or None,
        expires_at=_now() + CODE_TTL,
    ))
    db.commit()
    return code


def _pkce_ok(verifier: str | None, challenge: str) -> bool:
    if not verifier or not (43 <= len(verifier) <= 128):
        return False
    digest = hashlib.sha256(verifier.encode("ascii", "ignore")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return hmac.compare_digest(computed, challenge)


def _issue_tokens(db: Session, client_id: str, user_id: int, resource: str | None) -> dict:
    access = _new_secret("nat")
    refresh = _new_secret("nrt")
    now = _now()
    db.add(McpOAuthToken(
        access_token_hash=_hash(access),
        refresh_token_hash=_hash(refresh),
        client_id=client_id,
        user_id=user_id,
        scope=SCOPE,
        resource=resource,
        access_expires_at=now + ACCESS_TTL,
        refresh_expires_at=now + REFRESH_TTL,
    ))
    db.commit()
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": int(ACCESS_TTL.total_seconds()),
        "refresh_token": refresh,
        "scope": SCOPE,
    }


def exchange_code(db: Session, client: McpOAuthClient, form: dict) -> dict:
    code = form.get("code") or ""
    row = db.execute(
        select(McpOAuthCode).where(McpOAuthCode.code_hash == _hash(code))
    ).scalar_one_or_none() if code else None

    if row is None or row.client_id != client.client_id:
        raise OAuthFlowError("invalid_grant", "Code d'autorisation invalide.")
    if row.used_at is not None:
        raise OAuthFlowError("invalid_grant", "Code d'autorisation déjà utilisé.")
    if row.expires_at < _now():
        raise OAuthFlowError("invalid_grant", "Code d'autorisation expiré.")
    if (form.get("redirect_uri") or row.redirect_uri) != row.redirect_uri:
        raise OAuthFlowError("invalid_grant", "redirect_uri différente de celle de l'autorisation.")
    if not _pkce_ok(form.get("code_verifier"), row.code_challenge):
        raise OAuthFlowError("invalid_grant", "Vérification PKCE échouée.")
    if not valid_resource(form.get("resource")):
        raise OAuthFlowError("invalid_target", "Ressource inconnue.")

    row.used_at = _now()
    db.commit()
    return _issue_tokens(db, client.client_id, row.user_id, row.resource)


def refresh_tokens(db: Session, client: McpOAuthClient, form: dict) -> dict:
    token = form.get("refresh_token") or ""
    row = db.execute(
        select(McpOAuthToken).where(McpOAuthToken.refresh_token_hash == _hash(token))
    ).scalar_one_or_none() if token else None

    if row is None or row.client_id != client.client_id or row.revoked_at is not None:
        raise OAuthFlowError("invalid_grant", "Jeton de renouvellement invalide.")
    if row.refresh_expires_at is None or row.refresh_expires_at < _now():
        raise OAuthFlowError("invalid_grant", "Jeton de renouvellement expiré : reconnecte NAVIRE.")
    if not valid_resource(form.get("resource")):
        raise OAuthFlowError("invalid_target", "Ressource inconnue.")

    # Rotation : l'ancien couple de jetons ne vaut plus rien.
    row.revoked_at = _now()
    db.commit()
    return _issue_tokens(db, client.client_id, row.user_id, row.resource)


def revoke_token(db: Session, client: McpOAuthClient, token: str) -> None:
    """RFC 7009 : jeton d'accès ou de renouvellement ; inconnu = sans effet."""
    if not token:
        return
    h = _hash(token)
    row = db.execute(
        select(McpOAuthToken).where(
            or_(McpOAuthToken.access_token_hash == h, McpOAuthToken.refresh_token_hash == h)
        )
    ).scalar_one_or_none()
    if row is not None and row.client_id == client.client_id and row.revoked_at is None:
        row.revoked_at = _now()
        db.commit()


# ============================================================
# Jetons présentés à /mcp
# ============================================================

def resolve_access_token(db: Session, token: str | None) -> tuple[McpOAuthToken, User] | None:
    if not token:
        return None
    row = db.execute(
        select(McpOAuthToken).where(McpOAuthToken.access_token_hash == _hash(token))
    ).scalar_one_or_none()
    now = _now()
    if row is None or row.revoked_at is not None or row.access_expires_at < now:
        return None
    user = db.get(User, row.user_id)
    if user is None:
        return None
    # Pas d'écriture à chaque appel : une fois par minute suffit pour « vu le … ».
    if row.last_used_at is None or now - row.last_used_at > timedelta(minutes=1):
        row.last_used_at = now
        db.commit()
    return row, user


# ============================================================
# Connexions de l'élève (page « Applications connectées »)
# ============================================================

def list_connections(db: Session, user: User) -> list[dict]:
    """Une ligne par application ayant une connexion encore valable."""
    now = _now()
    rows = db.execute(
        select(McpOAuthToken, McpOAuthClient)
        .join(McpOAuthClient, McpOAuthClient.client_id == McpOAuthToken.client_id)
        .where(
            McpOAuthToken.user_id == user.id,
            McpOAuthToken.revoked_at.is_(None),
            McpOAuthToken.refresh_expires_at > now,
        )
        .order_by(McpOAuthToken.created_at.desc())
    ).all()
    seen: dict[str, dict] = {}
    for tok, client in rows:
        entry = seen.setdefault(client.client_id, {
            "client_id": client.client_id,
            "name": client.client_name or redirect_host((client.redirect_uris or [""])[0]) or "Application",
            "domain": redirect_host((client.redirect_uris or [""])[0]),
            "connected_at": tok.created_at.isoformat() + "Z",
            "last_used_at": None,
        })
        if tok.last_used_at and (entry["last_used_at"] is None or tok.last_used_at.isoformat() + "Z" > entry["last_used_at"]):
            entry["last_used_at"] = tok.last_used_at.isoformat() + "Z"
    return list(seen.values())


def revoke_connection(db: Session, user: User, client_id: str) -> int:
    """Coupe toutes les connexions de cette application au compte de l'élève."""
    rows = db.execute(
        select(McpOAuthToken).where(
            McpOAuthToken.user_id == user.id,
            McpOAuthToken.client_id == client_id,
            McpOAuthToken.revoked_at.is_(None),
        )
    ).scalars().all()
    now = _now()
    for r in rows:
        r.revoked_at = now
    db.commit()
    return len(rows)


# ============================================================
# Ménage (job quotidien)
# ============================================================

def purge_expired(db: Session) -> dict:
    """
    Supprime les codes périmés, les jetons morts depuis plus de 7 jours, et
    les applications inscrites il y a plus de 7 jours qui n'ont jamais servi
    (l'inscription est ouverte à tous : sans ce ménage, la table grossirait).
    """
    now = _now()
    week_ago = now - timedelta(days=7)
    codes = db.execute(delete(McpOAuthCode).where(McpOAuthCode.expires_at < now - timedelta(hours=1))).rowcount
    tokens = db.execute(
        delete(McpOAuthToken).where(
            or_(
                McpOAuthToken.revoked_at < week_ago,
                McpOAuthToken.refresh_expires_at < week_ago,
            )
        )
    ).rowcount
    used = select(McpOAuthToken.client_id).union(select(McpOAuthCode.client_id))
    clients = db.execute(
        delete(McpOAuthClient).where(
            McpOAuthClient.created_at < week_ago,
            McpOAuthClient.client_id.not_in(used),
        )
    ).rowcount
    db.commit()
    return {"codes": codes or 0, "tokens": tokens or 0, "clients": clients or 0}
