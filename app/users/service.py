import secrets
from uuid import UUID
import structlog
from app.exceptions import AuthenticationError, ForbiddenError, ResourceDoesNotExistError, TooManyRequestsError
from app.users.repository import UserRepository
from app.users.schemas import BulkDeleteResult, UserCreateSchema, UserUpdateSchema, GetAllUserSchema, PaginatedUsersResponse
from app.users.exceptions import UserAlreadyExistsError
from app.users.schemas import UserResponseSchema
from app.schemas import SuccessMessage
from app.users.models import User
from app.core.redis.redis_client import redis_server
from app.security.utils.password import check_password, hash_password
from app.security.repository import RefreshTokenRepository
from app.users.tasks.tasks import send_email_verification_task, send_email_password_reset_task
from app.security.utils.redis_util import revoke_access_to_all_tokens

logger = structlog.get_logger()

class UserService:
    def __init__(self, user_repository: UserRepository, token_repository: RefreshTokenRepository):
        self.user_repository = user_repository
        self.token_repository = token_repository

    async def create_user_service(self, request: UserCreateSchema) -> UserResponseSchema:
        logger.info("signup_attempt", email=request.email)
        exists = await self.user_repository.get_user_by_email(request.email)
        if exists:
            logger.warning("signup_email_already_exists", email=request.email)
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
        logger.info("email_verification_attempt")
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
        logger.info("resend_email_verification_attempt", email=email)
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
        logger.info("forgot_password_attempt", email=email)
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
        logger.info("password_reset_attempt")
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

    async def update_email_service(self, user_id: UUID, new_email: str) -> SuccessMessage:
        logger.info("update_email_attempt", user_id=str(user_id))

        user = await self.user_repository.get_user_by_id(user_id)
        if not user:
            logger.warning("update_email_user_not_found", user_id=str(user_id))
            raise ResourceDoesNotExistError("User does not exist.")

        if user.email == new_email:
            logger.info("update_email_no_change", user_id=str(user_id))
            return SuccessMessage(success_message="This is already your current email address.")

        existing = await self.user_repository.get_user_by_email(new_email)
        if existing:
            logger.warning("email_already_taken", user_id=str(user_id), attempted_email=new_email)
            raise UserAlreadyExistsError("User Already exist")

        user.email = new_email
        user.is_verified = False
        await self.user_repository.update_user(user)

        await revoke_access_to_all_tokens(user.id)
        await self.token_repository.delete_all_refresh_tokens_for_user(user.id)

        token = secrets.token_urlsafe(32)
        await redis_server.setex(f"email_verification:{token}", 900, str(user.id))
        
        send_email_verification_task.delay(user.email, token)

        logger.info("email_change_succesful", user_id=str(user.id))
        return SuccessMessage(success_message="Your email has been updated. Please check your inbox to verify your new email.")

    async def update_user_fields_service(self, request: UserUpdateSchema, user_id: UUID) -> UserResponseSchema:
        logger.info("update_user_fields_attempt", user_id=str(user_id))

        user = await self.user_repository.get_user_by_id(user_id)
        if not user:
            logger.warning("update_user_not_found", user_id=str(user_id))
            raise ResourceDoesNotExistError("User does not exist.")

        if request.username is not None:
            user.username = request.username

        updated_user = await self.user_repository.update_user(user)

        logger.info("user_updated_successfully", user_id=str(user_id))
        return UserResponseSchema.model_validate(updated_user)

    async def delete_current_user_service(self, user_id: UUID) -> SuccessMessage:
        logger.info("delete_own_account_attempt", user_id=str(user_id))

        user = await self.user_repository.get_user_by_id(user_id)
        if not user:
            logger.warning("delete_user_user_not_found", user_id=str(user_id))
            raise ResourceDoesNotExistError("User does not exist.")

        await revoke_access_to_all_tokens(user.id)
        await self.user_repository.delete_user(user)

        logger.info("user_deleted_successfully", user_id=str(user_id))
        return SuccessMessage(success_message="User deleted successfully.")

    async def update_user_password_service(self, user_id: UUID, new_password: str, old_password: str) -> SuccessMessage:
        logger.info("update_password_attempt", user_id=str(user_id))

        user = await self.user_repository.get_user_by_id(user_id)
        if not user:
            logger.warning("update_password_user_not_found", user_id=str(user_id))
            raise ResourceDoesNotExistError("User does not exist.")

        if not check_password(old_password, user.password):
            logger.warning("update_password_incorrect_old_password", user_id=str(user_id))
            raise AuthenticationError("The old password is incorrect.")

        hashed_password = hash_password(new_password)
        user.password = hashed_password
        await self.user_repository.update_user(user)

        #Revoke all sessions
        await self.token_repository.delete_all_refresh_tokens_for_user(user_id)

        # Revoke all access tokens redis
        await revoke_access_to_all_tokens(user_id)

        logger.info("password_updated_successfully", user_id=str(user_id))
        return SuccessMessage(success_message="Password updated successfully.")
       
    async def get_user_by_id_service(self, user_id: UUID) -> UserResponseSchema:
        user = await self.user_repository.get_user_by_id(user_id)
        if not user:
            logger.warning("get_user_by_id_not_found", user_id=str(user_id))
            raise ResourceDoesNotExistError("User does not exist.")
        return UserResponseSchema.model_validate(user)



    # Admin only services
    async def get_all_users_service(self, getAllUserRequestSchema: GetAllUserSchema, page: int, page_size: int, admin_id: UUID) -> PaginatedUsersResponse:
        logger.info("admin_list_users_attempt", admin_id=str(admin_id), page=page, page_size=page_size)

        total = await self.user_repository.count_users(username= getAllUserRequestSchema.username,
                                                        email=getAllUserRequestSchema.email,
                                                        is_admin=getAllUserRequestSchema.is_admin,
                                                        created_after=getAllUserRequestSchema.created_after,
                                                        created_before=getAllUserRequestSchema.created_before)

        offset = (page - 1) * page_size
        users = await self.user_repository.get_users(limit=page_size, 
                                                     offset=offset, 
                                                     username= getAllUserRequestSchema.username,
                                                     email=getAllUserRequestSchema.email,
                                                     is_admin=getAllUserRequestSchema.is_admin,
                                                     created_after=getAllUserRequestSchema.created_after,
                                                     created_before=getAllUserRequestSchema.created_before,
                                                     username_sorted_bool=getAllUserRequestSchema.username_sorted_bool)
        
        return PaginatedUsersResponse(items=[UserResponseSchema.model_validate(user) for user in users],
                                      total=total,
                                      page_size=page_size, 
                                      page=page)

    async def update_user_email_by_id_service(self, user_id: UUID, new_email: str, admin_id: UUID) -> UserResponseSchema:
        logger.info("admin_update_email_attempt", admin_id=str(admin_id), target_user_id=str(user_id))

        user = await self.user_repository.get_user_by_id(user_id)
        if not user:
            logger.warning("update_user_not_found", admin_id=str(admin_id), user_id=str(user_id))
            raise ResourceDoesNotExistError("User does not exist.")

        if user.email == new_email:
            logger.info("update_email_no_change", admin_id=str(admin_id), user_id=str(user_id))
            return UserResponseSchema.model_validate(user)


        existing = await self.user_repository.get_user_by_email(new_email)
        if existing:
            logger.warning("email_already_taken", admin_id=str(admin_id), user_id=str(user_id), attempted_email=new_email)
            raise UserAlreadyExistsError(f"User with email {new_email} already exists.")

        user.email = new_email
        user.is_verified = False

        result  = await self.user_repository.update_user(user)

        await revoke_access_to_all_tokens(user_id)
        await self.token_repository.delete_all_refresh_tokens_for_user(user_id)

        
        token = secrets.token_urlsafe(32)
        await redis_server.setex(f"email_verification:{token}", 900, str(user.id))
        
        send_email_verification_task.delay(user.email, token)

        logger.info("admin_updated_user_email", admin_id=str(admin_id), user_id=str(result.id))
        return UserResponseSchema.model_validate(result)

    async def delete_user_by_id_service(self, user_id: UUID, admin_id: UUID) -> SuccessMessage:
        logger.info("admin_delete_user_attempt", admin_id=str(admin_id), target_user_id=str(user_id))

        if admin_id == user_id:
            logger.warning("admin_delete_self_blocked", admin_id=str(admin_id), user_id=str(user_id))
            raise ForbiddenError("You cannot delete your own account.")

        user = await self.user_repository.get_user_by_id(user_id)
        if not user:
            logger.warning("delete_user_not_found", admin_id=str(admin_id), user_id=str(user_id))
            raise ResourceDoesNotExistError("User does not exist.")

        if user.is_admin:
            logger.warning("admin_delete_admin_blocked", admin_id=str(admin_id), user_id=str(user_id))
            raise ForbiddenError("You cannot delete another admins account.")

        await revoke_access_to_all_tokens(user_id)
        await self.user_repository.delete_user(user)

        logger.info("admin_deleted_user", admin_id=str(admin_id), user_id=str(user_id))
        return SuccessMessage(success_message="User deleted successfully.")

    async def delete_multiple_users_by_ids_service(self, user_ids: list[UUID], admin_id: UUID) -> BulkDeleteResult:
        logger.info("admin_bulk_delete_attempt", admin_id=str(admin_id), target_count=len(user_ids))

        if admin_id in user_ids:
            logger.warning("admin_bulk_delete_self_blocked", admin_id=str(admin_id))
            raise ForbiddenError("You cannot delete your own account.")

        not_found_ids = []
        deleted_ids = []
        skipped_admin_ids = []

        for user_id in user_ids:
            user = await self.user_repository.get_user_by_id(user_id)
            if not user:
                logger.warning("delete_user_not_found", admin_id=str(admin_id), user_id=str(user_id))
                not_found_ids.append(str(user_id))

            elif user and user.is_admin:
                logger.warning("admin_bulk_delete_admin_blocked", admin_id=str(admin_id), user_id=str(user_id))
                skipped_admin_ids.append(str(user_id))

            else:
                await revoke_access_to_all_tokens(user_id)
                await self.user_repository.delete_user(user)
                deleted_ids.append(str(user_id))

        logger.info("bulk_users_deleted", admin_id=str(admin_id), deleted_count=len(deleted_ids), not_found_count=len(not_found_ids), skipped_admin_count=len(skipped_admin_ids))
        return BulkDeleteResult(deleted=deleted_ids, not_found=not_found_ids, skipped_admins=skipped_admin_ids)