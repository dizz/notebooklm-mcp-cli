"""Tests for the OAuth 2.1 provider (mcp/oauth.py)."""

import hashlib
import base64
import secrets
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from notebooklm_tools.mcp.oauth import (
    OAuthProvider,
    OAuthMiddleware,
    AUTH_CODE_TTL,
    ACCESS_TOKEN_TTL,
)


@pytest.fixture
def provider():
    return OAuthProvider(
        client_id="test-client-id",
        client_secret="test-client-secret",
        server_url="https://example.com",
        mcp_path="/mcp",
    )


def _pkce_pair():
    """Generate a PKCE code_verifier and code_challenge (S256)."""
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# --- Metadata ---


class TestMetadata:
    def test_authorization_server_metadata(self, provider):
        meta = provider.authorization_server_metadata()
        assert meta["issuer"] == "https://example.com"
        assert meta["authorization_endpoint"] == "https://example.com/oauth/authorize"
        assert meta["token_endpoint"] == "https://example.com/oauth/token"
        assert "code" in meta["response_types_supported"]
        assert "S256" in meta["code_challenge_methods_supported"]

    def test_protected_resource_metadata(self, provider):
        meta = provider.protected_resource_metadata()
        assert meta["resource"] == "https://example.com/mcp"
        assert "https://example.com" in meta["authorization_servers"]


# --- Authorization ---


class TestAuthorize:
    def test_auto_approves_and_redirects(self, provider):
        verifier, challenge = _pkce_pair()
        params = {
            "response_type": "code",
            "client_id": "test-client-id",
            "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "abc123",
        }
        resp = provider.handle_authorize(params)
        assert resp.status_code == 302
        location = resp.headers["location"]
        assert "code=" in location
        assert "state=abc123" in location
        assert location.startswith("https://claude.ai/api/mcp/auth_callback")

    def test_rejects_wrong_client_id(self, provider):
        _, challenge = _pkce_pair()
        params = {
            "response_type": "code",
            "client_id": "wrong-id",
            "redirect_uri": "https://callback.example.com",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "x",
        }
        resp = provider.handle_authorize(params)
        assert resp.status_code == 302
        assert "invalid_client" in resp.headers["location"]

    def test_rejects_missing_pkce(self, provider):
        params = {
            "response_type": "code",
            "client_id": "test-client-id",
            "redirect_uri": "https://callback.example.com",
            "state": "x",
        }
        resp = provider.handle_authorize(params)
        assert resp.status_code == 302
        assert "invalid_request" in resp.headers["location"]

    def test_rejects_non_code_response_type(self, provider):
        _, challenge = _pkce_pair()
        params = {
            "response_type": "token",
            "client_id": "test-client-id",
            "redirect_uri": "https://callback.example.com",
            "code_challenge": challenge,
            "state": "x",
        }
        resp = provider.handle_authorize(params)
        assert resp.status_code == 302
        assert "unsupported_response_type" in resp.headers["location"]


# --- Token Exchange ---


class TestTokenExchange:
    def _get_auth_code(self, provider):
        """Helper: run authorize flow and return (code, verifier)."""
        verifier, challenge = _pkce_pair()
        params = {
            "response_type": "code",
            "client_id": "test-client-id",
            "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "s",
        }
        resp = provider.handle_authorize(params)
        location = resp.headers["location"]
        # Extract code from redirect URL
        from urllib.parse import urlparse, parse_qs

        qs = parse_qs(urlparse(location).query)
        code = qs["code"][0]
        return code, verifier

    def test_exchange_code_for_tokens(self, provider):
        code, verifier = self._get_auth_code(provider)
        resp = provider.handle_token({
            "grant_type": "authorization_code",
            "code": code,
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "code_verifier": verifier,
            "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
        })
        assert resp.status_code == 200
        body = resp.body.decode()
        import json

        data = json.loads(body)
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "Bearer"
        assert data["expires_in"] == ACCESS_TOKEN_TTL

    def test_rejects_wrong_client_secret(self, provider):
        code, verifier = self._get_auth_code(provider)
        resp = provider.handle_token({
            "grant_type": "authorization_code",
            "code": code,
            "client_id": "test-client-id",
            "client_secret": "wrong-secret",
            "code_verifier": verifier,
        })
        assert resp.status_code == 401

    def test_rejects_wrong_verifier(self, provider):
        code, _ = self._get_auth_code(provider)
        resp = provider.handle_token({
            "grant_type": "authorization_code",
            "code": code,
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "code_verifier": "totally-wrong-verifier",
        })
        assert resp.status_code == 400
        assert b"PKCE" in resp.body

    def test_code_is_single_use(self, provider):
        code, verifier = self._get_auth_code(provider)
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "code_verifier": verifier,
        }
        resp1 = provider.handle_token(form)
        assert resp1.status_code == 200
        resp2 = provider.handle_token(form)
        assert resp2.status_code == 400

    def test_rejects_expired_code(self, provider):
        code, verifier = self._get_auth_code(provider)
        # Expire the code
        provider._auth_codes[code].expires_at = time.time() - 1
        resp = provider.handle_token({
            "grant_type": "authorization_code",
            "code": code,
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "code_verifier": verifier,
        })
        assert resp.status_code == 400


# --- Refresh Token ---


class TestRefreshToken:
    def _get_tokens(self, provider):
        """Helper: full auth code flow, return parsed token response."""
        verifier, challenge = _pkce_pair()
        params = {
            "response_type": "code",
            "client_id": "test-client-id",
            "redirect_uri": "https://callback.example.com",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "s",
        }
        resp = provider.handle_authorize(params)
        from urllib.parse import urlparse, parse_qs

        code = parse_qs(urlparse(resp.headers["location"]).query)["code"][0]
        resp = provider.handle_token({
            "grant_type": "authorization_code",
            "code": code,
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "code_verifier": verifier,
            "redirect_uri": "https://callback.example.com",
        })
        import json

        return json.loads(resp.body.decode())

    def test_refresh_issues_new_tokens(self, provider):
        tokens = self._get_tokens(provider)
        resp = provider.handle_token({
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
        })
        assert resp.status_code == 200
        import json

        new_tokens = json.loads(resp.body.decode())
        assert new_tokens["access_token"] != tokens["access_token"]
        assert new_tokens["refresh_token"] != tokens["refresh_token"]

    def test_old_access_token_invalid_after_refresh(self, provider):
        tokens = self._get_tokens(provider)
        old_access = tokens["access_token"]
        provider.handle_token({
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
        })
        assert not provider.validate_token(f"Bearer {old_access}")

    def test_rejects_invalid_refresh_token(self, provider):
        resp = provider.handle_token({
            "grant_type": "refresh_token",
            "refresh_token": "bogus",
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
        })
        assert resp.status_code == 400


# --- Token Validation ---


class TestValidateToken:
    def test_validates_good_token(self, provider):
        tokens = TestRefreshToken()._get_tokens(provider)
        assert provider.validate_token(f"Bearer {tokens['access_token']}")

    def test_rejects_missing_bearer_prefix(self, provider):
        tokens = TestRefreshToken()._get_tokens(provider)
        assert not provider.validate_token(tokens["access_token"])

    def test_rejects_expired_token(self, provider):
        tokens = TestRefreshToken()._get_tokens(provider)
        entry = provider._tokens[tokens["access_token"]]
        entry.expires_at = time.time() - 1
        assert not provider.validate_token(f"Bearer {tokens['access_token']}")

    def test_rejects_unknown_token(self, provider):
        assert not provider.validate_token("Bearer unknown-token")


# --- PKCE ---


class TestPKCE:
    def test_s256_verification(self):
        verifier, challenge = _pkce_pair()
        assert OAuthProvider._verify_pkce(verifier, challenge)

    def test_wrong_verifier_fails(self):
        _, challenge = _pkce_pair()
        assert not OAuthProvider._verify_pkce("wrong-verifier", challenge)

    def test_empty_verifier_fails(self):
        _, challenge = _pkce_pair()
        assert not OAuthProvider._verify_pkce("", challenge)


# --- Middleware ---


class TestOAuthMiddleware:
    @pytest.mark.asyncio
    async def test_public_paths_pass_through(self):
        app = AsyncMock()
        provider = OAuthProvider(
            client_id="id", client_secret="secret",
            server_url="https://example.com",
        )
        mw = OAuthMiddleware(app, provider)

        for path in ["/oauth/authorize", "/.well-known/oauth-authorization-server", "/health"]:
            scope = {"type": "http", "path": path, "headers": []}
            await mw(scope, AsyncMock(), AsyncMock())
            app.assert_called()
            app.reset_mock()

    @pytest.mark.asyncio
    async def test_mcp_endpoint_requires_token(self):
        app = AsyncMock()
        provider = OAuthProvider(
            client_id="id", client_secret="secret",
            server_url="https://example.com",
        )
        mw = OAuthMiddleware(app, provider)

        # Mock send to capture the 401 response
        sent_messages = []

        async def mock_send(message):
            sent_messages.append(message)

        scope = {"type": "http", "path": "/mcp", "headers": []}
        await mw(scope, AsyncMock(), mock_send)
        app.assert_not_called()
        # Should have sent a response (start + body)
        assert len(sent_messages) >= 1
        # Check status code in the http.response.start message
        start_msg = sent_messages[0]
        assert start_msg.get("status") == 401

    @pytest.mark.asyncio
    async def test_non_http_passes_through(self):
        app = AsyncMock()
        provider = OAuthProvider(
            client_id="id", client_secret="secret",
            server_url="https://example.com",
        )
        mw = OAuthMiddleware(app, provider)

        scope = {"type": "websocket", "path": "/mcp"}
        await mw(scope, AsyncMock(), AsyncMock())
        app.assert_called()
