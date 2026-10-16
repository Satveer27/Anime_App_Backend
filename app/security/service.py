from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog

from app.config import settings
from app.exceptions import AuthenticationError
from app.security.exceptions import AlreadyLoggedInError, InvalidTokenError
from app.security.models import RefreshToken
from app.security.repository import RefreshTokenRepository
from app.security.schemas import RefreshResponse
from app.security.utils.jwt import decode_token, generate_access_token, generate_refresh_token
from app.security.utils.password import check_password
from app.security.utils.redis_util import revoke_access_to_all_tokens, revoke_single_access_token
from app.users.repository import UserRepository

logger = structlog.get_logger()


class JWTService:
    def __init__(
        self, refresh_token_repository: RefreshTokenRepository, user_repository: UserRepository
    ):
        self.refresh_token_repository = refresh_token_repository
        self.user_repository = user_repository

    async def refresh_access_token_service(self, refresh_token: str) -> RefreshResponse:
        logger.info("refresh_attempt")
        payload = decode_token(refresh_token, "refresh")

        jti = payload.get("jti")
        sub = payload.get("sub")
        exp = payload.get("exp")

        if jti is None or sub is None or exp is None:
            logger.warning("refresh_token_missing_fields")
            raise InvalidTokenError()

        result = await self.refresh_token_repository.get_refresh_token_by_jti(UUID(jti))
        if result is None:
            logger.warning("refresh_token_not_found", jti=jti)
            raise AuthenticationError("Session invalid, please login again.")

        row_count = await self.refresh_token_repository.revoke_refresh_token(UUID(jti))
        if row_count != 1:
            fresh = await self.refresh_token_repository.get_refresh_token_by_jti(UUID(jti))
            grace = timedelta(seconds=settings.refresh_grace_seconds)
            if (
                fresh is not None
                and fresh.revoked_at is not None
                and datetime.now(UTC) - fresh.revoked_at <= grace
            ):
                logger.info("refresh_token_superseded", user_id=sub, jti=jti)
                raise AuthenticationError("Token was just rotated, retry with the latest cookie.")

            logger.warning("refresh_token_reuse_detected", user_id=sub, jti=jti)
            await self.refresh_token_repository.delete_all_refresh_tokens_for_user(UUID(sub))
            await self.refresh_token_repository.commit()
            await revoke_access_to_all_tokens(UUID(sub))
            logger.warning("all_sessions_revoked_due_to_reuse", user_id=sub)

            raise AuthenticationError("Session invalid, please login again.")

        original_expiry = datetime.fromtimestamp(exp, tz=UTC)
        new_refresh_token = generate_refresh_token(result.user_id, original_expiry)
        new_refresh_token_decoded = decode_token(new_refresh_token, "refresh")
        expires_at = datetime.fromtimestamp(new_refresh_token_decoded["exp"], tz=UTC)
        new_token = RefreshToken(
            jti=new_refresh_token_decoded["jti"],
            user_id=UUID(new_refresh_token_decoded["sub"]),
            expires_at=expires_at,
        )

        await self.refresh_token_repository.create_refresh_token(new_token)
        final_access_token = await generate_access_token(result.user_id)

        logger.info("access_token_refreshed", user_id=str(result.user_id))

        return RefreshResponse(refresh_token=new_refresh_token, access_token=final_access_token)

    async def login_service(
        self, email: str, password: str, refresh_token: str | None = None
    ) -> RefreshResponse:
        logger.info("login_attempt", email=email)
        if refresh_token is not None:
            try:
                decoded_token = decode_token(refresh_token, "refresh")
                jti = decoded_token.get("jti")
                sub = decoded_token.get("sub")

                if jti is None or sub is None:
                    logger.warning("refresh_token_missing_fields")
                    raise InvalidTokenError()

                current_token = await self.refresh_token_repository.get_refresh_token_by_jti(jti)

                if current_token is not None and not current_token.revoke:
                    logger.warning("already_logged_in", user_id=sub)
                    raise AlreadyLoggedInError("User is already logged in. Please logout first.")
            except InvalidTokenError:
                pass

        user = await self.user_repository.get_user_by_email(email)
        if user is None or not check_password(password, user.password):
            logger.warning("login_failed", email=email)
            raise AuthenticationError("Incorrect password and username")

        if user.is_verified is False:
            logger.warning("login_failed_unverified_email", email=email)
            raise AuthenticationError(
                "Email is not verified. Please verify your email before logging in."
            )

        new_refresh_token = generate_refresh_token(user.id)

        new_refresh_token_decoded = decode_token(new_refresh_token, "refresh")
        expires_at = datetime.fromtimestamp(new_refresh_token_decoded["exp"], tz=UTC)
        new_token = RefreshToken(
            jti=new_refresh_token_decoded["jti"],
            user_id=UUID(new_refresh_token_decoded["sub"]),
            expires_at=expires_at,
        )

        await self.refresh_token_repository.create_refresh_token(new_token)
        new_access_token = await generate_access_token(user.id)

        logger.info("user_logged_in", user_id=str(user.id))

        return RefreshResponse(refresh_token=new_refresh_token, access_token=new_access_token)

    async def logout_service(
        self, refresh_token: str | None, access_token: str | None = None
    ) -> bool:
        logger.info("logout_attempt")

        if access_token is not None:
            try:
                verified_access = decode_token(access_token, "access")
                jti_access = verified_access.get("jti")
                exp_access = verified_access.get("exp")
                sub_access = verified_access.get("sub")
                if jti_access is not None and exp_access is not None and sub_access is not None:
                    await revoke_single_access_token(jti_access, exp_access, UUID(sub_access))
            except InvalidTokenError:
                pass

        if refresh_token is None:
            logger.info("logout_no_refresh_token")
            return False

        try:
            verified_token = decode_token(refresh_token, "refresh")
        except InvalidTokenError:
            logger.info("logout_refresh_token_unusable")
            return False

        sub = verified_token.get("sub")
        jti = verified_token.get("jti")

        if sub is None or jti is None:
            logger.warning("refresh_token_missing_fields")
            return False

        current_token = await self.refresh_token_repository.get_refresh_token_by_jti(jti)

        if current_token is None or current_token.revoke:
            logger.info("logout_user_already_logged_out", user_id=sub, jti=jti)
            return False

        current_token.revoke = True
        await self.refresh_token_repository.update_refresh_token(current_token)

        logger.info("user_logged_out", user_id=sub, jti=jti)
        return True

    async def logout_all_accounts(self, refresh_token: str | None) -> bool:
        logger.info("logout_all_attempt")

        if refresh_token is None:
            logger.info("logout_all_no_refresh_token")
            return False

        try:
            verified_token = decode_token(refresh_token, "refresh")
        except InvalidTokenError:
            logger.info("logout_all_refresh_token_unusable")
            return False

        sub = verified_token.get("sub")
        jti = verified_token.get("jti")

        if sub is None or jti is None:
            logger.warning("refresh_token_missing_fields")
            return False

        current_token = await self.refresh_token_repository.get_refresh_token_by_jti(jti)

        if current_token is None or current_token.revoke:
            logger.info("logout_all_already_logged_out", user_id=sub, jti=jti)
            return False

        user = await self.user_repository.get_user_by_id(UUID(sub))

        if user is None:
            logger.info("logout_all_user_not_found", user_id=sub, jti=jti)
            return False

        row_count = await self.refresh_token_repository.update_refresh_token_to_revoke(
            user_id=user.id
        )

        await revoke_access_to_all_tokens(UUID(sub))
        logger.info("all_sessions_logged_out", user_id=sub, session_count=row_count)

        return True
