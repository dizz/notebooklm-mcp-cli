"""Minimal OAuth 2.1 provider for remote MCP server access.

Implements just enough OAuth to satisfy claude.ai's remote MCP connector:
- Authorization Code flow with PKCE (S256)
- Hardcoded Client ID / Client Secret (no Dynamic Client Registration)
- Auto-approve authorization (personal server — no login page)
- In-memory token store with expiry

Configure via environment variables or CLI args:
  NOTEBOOKLM_OAUTH_CLIENT_ID      Client ID (entered in claude.ai)
  NOTEBOOKLM_OAUTH_CLIENT_SECRET  Client Secret (entered in claude.ai)

The OAuth layer protects the MCP HTTP endpoint. It does NOT replace the
underlying Google/NotebookLM cookie-based auth which remains server-side.
"""

from __future__ import annotations

import hashlib
import base64
import logging
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

# Auth codes expire after 60 seconds (OAuth spec recommends short-lived)
AUTH_CODE_TTL = 60
# Access tokens expire after 1 hour
ACCESS_TOKEN_TTL = 3600
# Refresh tokens expire after 30 days
REFRESH_TOKEN_TTL = 30 * 24 * 3600


@dataclass
class AuthCode:
    code: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    scope: str
    expires_at: float


@dataclass
class TokenEntry:
    access_token: str
    refresh_token: str
    scope: str
    expires_at: float
    refresh_expires_at: float


@dataclass
class OAuthProvider:
    """Minimal OAuth 2.1 authorization server.

    Stores auth codes and tokens in memory. Tokens survive until server
    restart or expiry — suitable for a personal/single-user deployment.
    """

    client_id: str
    client_secret: str
    server_url: str  # Public base URL, e.g. https://my-server.example.com
    mcp_path: str = "/mcp"

    # In-memory stores
    _auth_codes: dict[str, AuthCode] = field(default_factory=dict)
    _tokens: dict[str, TokenEntry] = field(default_factory=dict)  # access_token -> entry
    _refresh_tokens: dict[str, TokenEntry] = field(default_factory=dict)  # refresh_token -> entry

    # --- Metadata endpoints ---

    def authorization_server_metadata(self) -> dict:
        """RFC 8414 — OAuth Authorization Server Metadata."""
        base = self.server_url.rstrip("/")
        return {
            "issuer": base,
            "authorization_endpoint": f"{base}/oauth/authorize",
            "token_endpoint": f"{base}/oauth/token",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["client_secret_post"],
            "scopes_supported": ["notebooklm"],
        }

    def protected_resource_metadata(self) -> dict:
        """RFC 9728 — OAuth Protected Resource Metadata."""
        base = self.server_url.rstrip("/")
        return {
            "resource": f"{base}{self.mcp_path}",
            "authorization_servers": [base],
            "scopes_supported": ["notebooklm"],
            "bearer_methods_supported": ["header"],
        }

    # --- Authorization endpoint ---

    def handle_authorize(self, params: dict[str, str]) -> Response:
        """Process GET /oauth/authorize.

        Auto-approves and redirects back with an authorization code.
        """
        client_id = params.get("client_id", "")
        redirect_uri = params.get("redirect_uri", "")
        state = params.get("state", "")
        code_challenge = params.get("code_challenge", "")
        code_challenge_method = params.get("code_challenge_method", "S256")
        scope = params.get("scope", "notebooklm")
        response_type = params.get("response_type", "")

        # Validate
        if response_type != "code":
            return self._error_redirect(
                redirect_uri, "unsupported_response_type", "Only code is supported", state
            )

        if client_id != self.client_id:
            return self._error_redirect(
                redirect_uri, "invalid_client", "Unknown client_id", state
            )

        if not code_challenge:
            return self._error_redirect(
                redirect_uri, "invalid_request", "code_challenge is required (PKCE)", state
            )

        if code_challenge_method != "S256":
            return self._error_redirect(
                redirect_uri, "invalid_request", "Only S256 code_challenge_method is supported", state
            )

        # Generate auth code
        code = secrets.token_urlsafe(32)
        self._auth_codes[code] = AuthCode(
            code=code,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            scope=scope,
            expires_at=time.time() + AUTH_CODE_TTL,
        )

        self._purge_expired_codes()

        # Auto-approve: redirect back with code
        qs = urlencode({"code": code, "state": state})
        return RedirectResponse(f"{redirect_uri}?{qs}", status_code=302)

    # --- Token endpoint ---

    def handle_token(self, form: dict[str, str]) -> JSONResponse:
        """Process POST /oauth/token."""
        grant_type = form.get("grant_type", "")

        if grant_type == "authorization_code":
            return self._exchange_code(form)
        elif grant_type == "refresh_token":
            return self._refresh(form)
        else:
            return JSONResponse(
                {"error": "unsupported_grant_type"}, status_code=400
            )

    def _exchange_code(self, form: dict[str, str]) -> JSONResponse:
        """Exchange authorization code for tokens."""
        code_str = form.get("code", "")
        client_id = form.get("client_id", "")
        client_secret = form.get("client_secret", "")
        code_verifier = form.get("code_verifier", "")
        redirect_uri = form.get("redirect_uri", "")

        # Validate client credentials
        if client_id != self.client_id or client_secret != self.client_secret:
            return JSONResponse(
                {"error": "invalid_client"}, status_code=401
            )

        # Look up and consume the auth code
        auth_code = self._auth_codes.pop(code_str, None)
        if auth_code is None:
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "Unknown or expired code"},
                status_code=400,
            )

        if time.time() > auth_code.expires_at:
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "Code expired"},
                status_code=400,
            )

        # Verify PKCE
        if not self._verify_pkce(code_verifier, auth_code.code_challenge):
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "PKCE verification failed"},
                status_code=400,
            )

        # Verify redirect_uri matches
        if redirect_uri and redirect_uri != auth_code.redirect_uri:
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "redirect_uri mismatch"},
                status_code=400,
            )

        return self._issue_tokens(auth_code.scope)

    def _refresh(self, form: dict[str, str]) -> JSONResponse:
        """Refresh an access token."""
        refresh_token = form.get("refresh_token", "")
        client_id = form.get("client_id", "")
        client_secret = form.get("client_secret", "")

        if client_id != self.client_id or client_secret != self.client_secret:
            return JSONResponse({"error": "invalid_client"}, status_code=401)

        entry = self._refresh_tokens.pop(refresh_token, None)
        if entry is None or time.time() > entry.refresh_expires_at:
            return JSONResponse(
                {"error": "invalid_grant", "error_description": "Invalid or expired refresh token"},
                status_code=400,
            )

        # Remove old access token
        self._tokens.pop(entry.access_token, None)

        return self._issue_tokens(entry.scope)

    def _issue_tokens(self, scope: str) -> JSONResponse:
        """Generate and store a new access + refresh token pair."""
        now = time.time()
        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(32)

        entry = TokenEntry(
            access_token=access_token,
            refresh_token=refresh_token,
            scope=scope,
            expires_at=now + ACCESS_TOKEN_TTL,
            refresh_expires_at=now + REFRESH_TOKEN_TTL,
        )
        self._tokens[access_token] = entry
        self._refresh_tokens[refresh_token] = entry

        self._purge_expired_tokens()

        return JSONResponse({
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": ACCESS_TOKEN_TTL,
            "refresh_token": refresh_token,
            "scope": scope,
        })

    # --- Token validation ---

    def validate_token(self, authorization: str) -> bool:
        """Validate a Bearer token from the Authorization header."""
        if not authorization.startswith("Bearer "):
            return False
        token = authorization[7:]
        entry = self._tokens.get(token)
        if entry is None:
            return False
        if time.time() > entry.expires_at:
            self._tokens.pop(token, None)
            return False
        return True

    # --- PKCE ---

    @staticmethod
    def _verify_pkce(code_verifier: str, code_challenge: str) -> bool:
        """Verify S256 PKCE challenge."""
        if not code_verifier:
            return False
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return secrets.compare_digest(computed, code_challenge)

    # --- Helpers ---

    @staticmethod
    def _error_redirect(
        redirect_uri: str, error: str, description: str, state: str
    ) -> Response:
        """Redirect back with an OAuth error."""
        if not redirect_uri:
            return JSONResponse(
                {"error": error, "error_description": description}, status_code=400
            )
        qs = urlencode({"error": error, "error_description": description, "state": state})
        return RedirectResponse(f"{redirect_uri}?{qs}", status_code=302)

    def _purge_expired_codes(self) -> None:
        now = time.time()
        expired = [k for k, v in self._auth_codes.items() if now > v.expires_at]
        for k in expired:
            del self._auth_codes[k]

    def _purge_expired_tokens(self) -> None:
        now = time.time()
        expired_access = [k for k, v in self._tokens.items() if now > v.expires_at]
        for k in expired_access:
            entry = self._tokens.pop(k)
            self._refresh_tokens.pop(entry.refresh_token, None)
        expired_refresh = [
            k for k, v in self._refresh_tokens.items() if now > v.refresh_expires_at
        ]
        for k in expired_refresh:
            self._refresh_tokens.pop(k, None)


class OAuthMiddleware:
    """ASGI middleware that enforces Bearer token auth on MCP endpoints.

    Passes through:
    - OAuth endpoints (/oauth/*, /.well-known/*)
    - Health check (/health)

    Returns 401 on MCP endpoints when OAuth is enabled and no valid token
    is present. The 401 includes resource_metadata in WWW-Authenticate
    per RFC 9728 so claude.ai can discover the auth server.
    """

    # Paths that never require auth
    PUBLIC_PREFIXES = ("/oauth/", "/.well-known/", "/health")

    def __init__(self, app: ASGIApp, provider: OAuthProvider) -> None:
        self.app = app
        self.provider = provider

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # Public paths pass through
        if any(path.startswith(prefix) for prefix in self.PUBLIC_PREFIXES):
            await self.app(scope, receive, send)
            return

        # Check Authorization header
        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode("utf-8", errors="ignore")

        if self.provider.validate_token(auth_header):
            await self.app(scope, receive, send)
            return

        # Return 401 with resource metadata pointer
        base = self.provider.server_url.rstrip("/")
        resource_metadata_url = f"{base}/.well-known/oauth-protected-resource"
        www_auth = f'Bearer resource_metadata="{resource_metadata_url}"'

        response = JSONResponse(
            {"error": "unauthorized", "error_description": "Valid Bearer token required"},
            status_code=401,
            headers={"WWW-Authenticate": www_auth},
        )
        await response(scope, receive, send)
