import secrets
from uuid import UUID
import structlog
from app.exceptions import ResourceDoesNotExistError, TooManyRequestsError
from app.users.repository import UserRepository
from app.users.schemas import UserCreateSchema
from app.users.exceptions import UserAlreadyExistsError
from app.users.schemas import UserResponseSchema
from app.schemas import SuccessMessage
from app.users.models import User
from app.core.redis.redis_client import redis_server
from app.security.utils.password import hash_password
from app.security.repository import RefreshTokenRepository
from app.users.tasks.tasks import send_email_verification_task, send_email_password_reset_task
from app.security.utils.redis_util import revoke_access_to_all_tokens

logger = structlog.get_logger()

class UserService:
    def __init__(self, user_repository: UserRepository, token_repository: RefreshTokenRepository):
        self.user_repository = user_repository
        self.token_repository = token_repository

    async def create_user_service(self, request: UserCreateSchema) -> UserResponseSchema:
        exists = await self.user_repository.get_user_by_email(request.email)
        if exists:
            raise UserAlreadyExistsError(f"User with email {request.email} already exists.")

        hashed_password = hash_password(request.password)

        token = secrets.token_urlsafe(32)

        user = User(
            email = request.email,
            username = request.username,
            password = hashed_password,
            f1_team = request.f1_team,
        )

        result = await self.user_repository.create_user(user)

        await redis_server.setex(f"email_verification:{token}", 900, str(result.id))
        logger.info("user_signed_up", user_id=str(result.id))

        send_email_verification_task.delay(result.email, token)

        return UserResponseSchema.model_validate(result)

    async def verify_user_email_service(self, token: str) -> SuccessMessage:
        result = await redis_server.get(f"email_verification:{token}")
        if not result:
            logger.warning("email_verification_token_not_found", token=token)
            raise ResourceDoesNotExistError("Token does not exist or has expired.")
        user_id = UUID(result)
        user = await self.user_repository.get_user_by_id(user_id)
        if not user:
            logger.warning("email_verification_user_not_found", user_id=str(user_id))
            raise ResourceDoesNotExistError("User does not exist.")
        user.is_verified = True
        await self.user_repository.update_user(user)
        await redis_server.delete(f"email_verification:{token}")

        logger.info("email_verified", user_id=str(user_id))
        return SuccessMessage(success_message="Email verified successfully.")

    async def resend_email_verification_service(self, email: str) -> SuccessMessage:
        user = await self.user_repository.get_user_by_email(email)
        if not user:
            logger.warning("resend_email_verification_user_not_found", email=email)
            return SuccessMessage(success_message="If the email exists, a verification email has been sent. Please check your inbox.")
        if user.is_verified:
            logger.warning("resend_email_verification_already_verified", email=email)
            return SuccessMessage(success_message="If the email exists, a verification email has been sent. Please check your inbox.")


        cooldown_key = f"resend_cooldown:{user.id}"
        on_cooldown = await redis_server.get(cooldown_key)
        if on_cooldown:
            logger.warning("resend_verification_rate_limited", user_id=str(user.id))
            raise TooManyRequestsError("Please wait before requesting another verification email.")

        token = secrets.token_urlsafe(32)
        await redis_server.setex(f"email_verification:{token}", 900, str(user.id))
        await redis_server.setex(cooldown_key, 300, "1")  # Set cooldown for 5 minutes
        
        send_email_verification_task.delay(user.email, token)

        logger.info("resend_email_verification_sent", user_id=str(user.id))
        return SuccessMessage(success_message="If the email exists, a verification email has been sent. Please check your inbox.")


    async def forgot_password_service(self, email:str) -> SuccessMessage:
        user = await self.user_repository.get_user_by_email(email)
        if user is None:
            logger.warning("forgot_password_user_not_found", email=email)
            return SuccessMessage(success_message="If the email exists, a password reset email has been sent. Please check your inbox.")

        on_cooldown = await redis_server.get(f"forgot_password_cooldown:{user.id}")
        if on_cooldown:
            logger.warning("forgot_password_rate_limited", user_id=str(user.id))
            raise TooManyRequestsError("Please wait before requesting another password reset email.")

        await redis_server.setex(f"forgot_password_cooldown:{user.id}", 300, "1")

        token = secrets.token_urlsafe(32)
        await redis_server.setex(f"password_reset:{token}", 900, str(user.id))
        send_email_password_reset_task.delay(user.email, token)

        logger.info("forgot_password_requested", user_id=str(user.id))
        return SuccessMessage(success_message="If the email exists, a password reset email has been sent. Please check your inbox.")

    async def reset_password_service_via_email(self, token:str, new_password:str) -> SuccessMessage:
        result = await redis_server.get(f"password_reset:{token}")
        if not result:
            logger.warning("password_reset_token_not_found", token=token)
            raise ResourceDoesNotExistError("Token does not exist or has expired.")
        
        user_id = UUID(result)
        user = await self.user_repository.get_user_by_id(user_id)
        if not user:
            logger.warning("password_reset_user_not_found", user_id=str(user_id))
            raise ResourceDoesNotExistError("User does not exist.")

        hashed_password = hash_password(new_password)
        user.password = hashed_password
        await self.user_repository.update_user(user)
        await redis_server.delete(f"password_reset:{token}")

        #Revoke all sessions
        await self.token_repository.delete_all_refresh_tokens_for_user(user_id)

        # Revoke all access tokens redis
        await revoke_access_to_all_tokens(user_id)


        logger.info("password_reset_successful", user_id=str(user_id))
        return SuccessMessage(success_message="Password reset successfully.")
        

    
        
        