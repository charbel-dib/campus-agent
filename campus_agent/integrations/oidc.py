"""OpenID Connect: sign-in with the school's identity provider, and bearer tokens for the remote MCP server.

Tokens are JSON Web Tokens signed by the provider (Keycloak in the compose file; any OIDC provider
works). They are verified against the provider's published keys: signature, issuer, audience and
expiry. The person is then looked up in the school by e-mail; a token for an unknown e-mail is refused.
Nothing the model says takes part in this.

Needs ``pip install ".[sso]"`` (PyJWT with cryptography).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import urlencode

TIMEOUT = 10
ALGORITHMS = ["RS256", "ES256"]


class AuthError(Exception):
    pass


class Verifier:
    def __init__(self, issuer: str, audience: str, jwks_client=None, metadata: dict | None = None):
        self.issuer, self.audience = issuer.rstrip("/"), audience
        self._jwks, self._metadata = jwks_client, metadata

    def metadata(self) -> dict:
        if self._metadata is None:
            import requests

            r = requests.get(f"{self.issuer}/.well-known/openid-configuration", timeout=TIMEOUT)
            r.raise_for_status()
            self._metadata = r.json()
        return self._metadata

    def claims(self, token: str, audience: str | None = None) -> dict:
        """Verified claims of a token, or AuthError. ``audience`` binds the token to one resource (RFC 8707)."""
        import jwt

        try:
            if self._jwks is None:
                self._jwks = jwt.PyJWKClient(self.metadata()["jwks_uri"])
            key = self._jwks.get_signing_key_from_jwt(token).key
            return jwt.decode(token, key, algorithms=ALGORITHMS, issuer=self.issuer, audience=audience or self.audience,
                              options={"require": ["exp", "iss", "aud"]})
        except Exception as exc:  # noqa: BLE001 - expired, wrong audience, bad signature, unknown key…
            raise AuthError(str(exc)) from exc


def person_for(conn, claims: dict) -> str:
    """The school person a verified token stands for (matched on e-mail), or AuthError."""
    email = str(claims.get("email", "")).lower()
    row = conn.execute("SELECT id FROM people WHERE lower(email) = ?", (email,)).fetchone() if email else None
    if row is None:
        raise AuthError("this account is not a member of the school")
    return row["id"]


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def authorization_url(metadata: dict, client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    return metadata["authorization_endpoint"] + "?" + urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "scope": "openid email profile",
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})


def exchange_code(metadata: dict, client_id: str, client_secret: str, redirect_uri: str, code: str, verifier: str) -> dict:
    import requests

    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri, "client_id": client_id,
            "code_verifier": verifier}
    if client_secret:
        data["client_secret"] = client_secret
    r = requests.post(metadata["token_endpoint"], data=data, timeout=TIMEOUT)
    if r.status_code != 200:
        raise AuthError("the identity provider refused the sign-in")
    return r.json()
