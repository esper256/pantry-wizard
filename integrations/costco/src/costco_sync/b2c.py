"""Costco's Azure AD B2C policy name.

costco-mcp-server 0.1.2 hardcodes signup_signin_201. Costco now issues
signup_signin_214, and the token endpoint rejects the other policy with
AADB2C90088. The expected policy is in that error, and also in the
localStorage key name the person copies the secret from.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

DEFAULT_POLICY = "b2c_1a_sso_wcs_signup_signin_214"
_POLICY_FILE = Path.home() / ".costco-sync" / "b2c-policy"
_INSTALLED = False
_POLICY_RE = re.compile(r"(b2c_1a_[a-z0-9_]+)", re.IGNORECASE)
_EXPECTED_RE = re.compile(r"Expected Value\s*:\s*(B2C_1A_[A-Za-z0-9_]+)")


def policy_from_storage_key(key: str) -> str:
    match = _POLICY_RE.search(key or "")
    return match.group(1).lower() if match else ""


def policy_from_exception(exc: BaseException) -> str:
    parts = [str(exc)]
    response = getattr(exc, "response", None)
    if response is not None:
        parts.append(str(getattr(response, "text", "") or ""))
    text = "\n".join(parts)
    match = _EXPECTED_RE.search(text)
    if match:
        return match.group(1).lower()
    return policy_from_storage_key(text)


def saved_policy() -> str:
    try:
        text = _POLICY_FILE.read_text().strip().lower()
    except OSError:
        return ""
    return text if _POLICY_RE.fullmatch(text) else ""


def remember_policy(policy: str) -> None:
    policy = (policy or "").strip().lower()
    if not _POLICY_RE.fullmatch(policy):
        return
    _POLICY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _POLICY_FILE.write_text(policy + "\n")
    os.chmod(_POLICY_FILE.parent, 0o700)
    os.chmod(_POLICY_FILE, 0o600)


def apply_policy(policy: str) -> None:
    import costco_mcp_server.auth as auth

    auth.POLICY_NAME = policy
    auth.TOKEN_ENDPOINT = f"https://signin.costco.com/{auth.TENANT_ID}/{policy}/oauth2/v2.0/token"


def install_policy(policy: str | None = None) -> str:
    """Point token refresh at a policy, and retry once if Costco names another."""
    global _INSTALLED
    import costco_mcp_server.auth as auth

    chosen = (policy or saved_policy() or DEFAULT_POLICY).strip().lower()
    apply_policy(chosen)
    if _INSTALLED:
        return chosen
    original = auth._refresh_tokens

    def wrapped(refresh_token: str) -> dict:
        try:
            return original(refresh_token)
        except Exception as exc:
            expected = policy_from_exception(exc)
            if not expected or expected == auth.POLICY_NAME:
                raise
            apply_policy(expected)
            remember_policy(expected)
            return original(refresh_token)

    auth._refresh_tokens = wrapped
    _INSTALLED = True
    return chosen
