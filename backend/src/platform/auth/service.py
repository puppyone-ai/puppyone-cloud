"""
Authentication Service
Handles JWT token verification and user information extraction

Supports two verification methods:
1. Supabase JWKS verification (preferred, via supabase.auth.get_claims)
2. Local JWT_SECRET verification (fallback, auto-switches when JWKS is empty)
"""

import os
import time
from contextlib import suppress

import jwt as pyjwt
from supabase import Client

from src.config import settings
from src.exceptions import AuthException, ErrorCode
from src.platform.auth.models import CurrentUser, TokenClaims
from src.utils.logger import log_debug, log_error, log_info


class AuthService:
    """Authentication service class"""

    def __init__(self, supabase_client: Client):
        """
        Initialize the authentication service

        Args:
            supabase_client: Supabase client instance
        """
        self.supabase = supabase_client

    def _verify_token_local(self, token: str) -> dict:
        """
        Verify token using local JWT_SECRET (fallback method)

        Used when the Supabase JWKS endpoint returns empty.
        Applicable for Supabase Branch instances where JWKS is not yet initialized.

        Args:
            token: JWT token string

        Returns:
            dict: Decoded claims

        Raises:
            AuthException: Verification failed
        """
        jwt_secret = settings.JWT_SECRET
        if not jwt_secret or jwt_secret == "ContextBase-256-bit-secret":
            log_error("JWT_SECRET not configured for local token verification")
            raise AuthException(
                message="JWT_SECRET not configured for token verification",
                code=ErrorCode.INVALID_TOKEN,
            )

        try:
            claims = pyjwt.decode(
                token,
                jwt_secret,
                algorithms=[settings.JWT_ALGORITHM],
                audience=["authenticated", "anon"],
                options={"verify_exp": True},
            )
            log_debug("Token verified using local JWT_SECRET fallback")
            return claims
        except pyjwt.ExpiredSignatureError:
            raise AuthException(
                message="Token expired",
                code=ErrorCode.TOKEN_EXPIRED,
            )
        except pyjwt.InvalidTokenError as e:
            # Local verification fails when algorithm/key doesn't match (expected fallback)
            log_debug(f"Local JWT verification not applicable: {e}")
            raise AuthException(
                message=f"Token verification failed: {e!s}",
                code=ErrorCode.INVALID_TOKEN,
            )

    def verify_token(self, token: str) -> TokenClaims:
        """
        Verify JWT token and return claims

        Prefers Supabase JWKS verification; automatically falls back to local JWT_SECRET when JWKS is empty.

        Args:
            token: JWT token string

        Returns:
            TokenClaims: Claims information from the token

        Raises:
            AuthException: Token is invalid or verification failed
        """
        claims_dict = None

        # Method 1: Local JWT_SECRET verification (fast, no network call).
        # Supabase JWTs are standard HS256 tokens — verify locally first.
        with suppress(AuthException):
            claims_dict = self._verify_token_local(token)

        # Method 2: Supabase JWKS verification — only if local failed.
        if not claims_dict:
            try:
                response = self.supabase.auth.get_claims(jwt=token)
                if response:
                    claims_dict = response.get("claims")
            except Exception as e:
                error_msg = str(e)
                if "JWKS is empty" in error_msg or "JWKS" in error_msg:
                    log_info(f"Supabase JWKS unavailable ({error_msg})")
                else:
                    log_error(f"JWKS token verification error: {e}")

        # If both verification paths failed, ``claims_dict`` is None.
        # A naive ``TokenClaims(**None)`` would crash with a confusing
        # "argument after ** must be a mapping" TypeError that ends up
        # in user-facing error toasts. Raise a clean "expired/invalid"
        # error instead so the frontend can route users to a
        # re-authentication flow without leaking the internal failure.
        if not claims_dict:
            log_debug("Token verification failed (local + JWKS exhausted)")
            raise AuthException(
                message="Invalid or expired token — please sign in again",
                code=ErrorCode.TOKEN_EXPIRED,
            )

        # Parse claims
        try:
            claims = TokenClaims(**claims_dict)
        except Exception as e:
            log_error(f"Failed to parse claims: {e}")
            raise AuthException(
                message=f"Invalid token format: {e!s}",
                code=ErrorCode.INVALID_TOKEN,
            )

        # Check if token is expired
        if self._is_token_expired(claims):
            log_debug(f"Token expired for user {claims.user_id}")
            raise AuthException(
                message="Token expired",
                code=ErrorCode.TOKEN_EXPIRED,
            )

        if claims.aud not in ["authenticated", "anon"]:
            log_error(f"Invalid audience: {claims.aud}")
            raise AuthException(
                message="Invalid token audience",
                code=ErrorCode.INVALID_TOKEN,
            )

        expected_issuer = (
            settings.SUPABASE_PUBLIC_URL or os.environ.get("SUPABASE_URL", "")
        ).rstrip("/") + "/auth/v1"
        if claims.iss != expected_issuer:
            raise AuthException(message="Invalid token issuer", code=ErrorCode.INVALID_TOKEN)

        log_debug(f"Token verified successfully for user {claims.user_id}")
        return claims

    def get_current_user(self, token: str) -> CurrentUser:
        """
        Get current user information from token

        Args:
            token: JWT token string

        Returns:
            CurrentUser: Current user information

        Raises:
            AuthException: Token is invalid or verification failed
        """
        claims = self.verify_token(token)
        return CurrentUser.from_claims(claims)

    @staticmethod
    def _is_token_expired(claims: TokenClaims) -> bool:
        """
        Check if token is expired

        Args:
            claims: token claims

        Returns:
            bool: True means expired
        """
        if not claims.exp:
            return True
        # Add 5-second buffer to avoid issues caused by network latency
        return time.time() > (claims.exp - 5)

    def verify_user_permission(self, user: CurrentUser, required_role: str | None = None) -> bool:
        """
        Verify user permissions

        Args:
            user: Current user
            required_role: Required role (optional)

        Returns:
            bool: True means authorized

        Raises:
            AuthException: No permission
        """
        # Check if user is anonymous
        if user.is_anonymous:
            raise AuthException(
                message="Anonymous users are not allowed",
                code=ErrorCode.FORBIDDEN,
            )

        # Check role (if required)
        if required_role and user.role != required_role:
            raise AuthException(
                message=f"Required role: {required_role}",
                code=ErrorCode.FORBIDDEN,
            )

        return True
