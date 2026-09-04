from uuid import UUID
from app.exceptions import ForbiddenError
from app.users.models import User
from app.core.deps import require_admin, get_current_user
from app.schemas import SuccessMessage
from app.users.schemas import BulkDeleteResult, BulkDeleteUsersSchema, ForgotPasswordSchema, ResendVerificationSchema, ResetPasswordSchema, UserResponseSchema, UserCreateSchema, AdminUpdateUserEmailSchema, UpdatePasswordSchema, UpdateEmailSchema, UserUpdateSchema
from app.users.deps import create_user_service
from fastapi import Depends
from app.users.service import UserService
from fastapi import APIRouter

user_router = APIRouter(prefix="/users", tags=["users"])

# User POST requests
@user_router.post("/signup", response_model=UserResponseSchema, status_code=201)
async def signup(payload: UserCreateSchema, service : UserService = Depends(create_user_service)):
    return await service.create_user_service(payload)

@user_router.post("/resend-verification", response_model=SuccessMessage, status_code=200)
async def resend_verification(payload: ResendVerificationSchema, service: UserService = Depends(create_user_service)):
    return await service.resend_email_verification_service(payload.email)

@user_router.post("/forgot-password", response_model=SuccessMessage, status_code=200)
async def forgot_password(payload: ForgotPasswordSchema, service: UserService = Depends(create_user_service)):
    return await service.forgot_password_service(payload.email)

@user_router.post("/reset-password", response_model=SuccessMessage, status_code=200)
async def reset_password(token: str, payload: ResetPasswordSchema, service: UserService = Depends(create_user_service)):
    return await service.reset_password_service_via_email(token, payload.password)

# User GET requests
@user_router.get("/verify-email", response_model=SuccessMessage, status_code=200)
async def verify_email(token:str, service: UserService = Depends(create_user_service)):
    return await service.verify_user_email_service(token)

@user_router.get("/me", response_model=UserResponseSchema, status_code=200)
async def get_user(user: User = Depends(get_current_user), service: UserService = Depends(create_user_service)):
    return await service.get_user_by_id_service(user.id)

# User PUT requests
@user_router.put("/update-password", response_model=SuccessMessage, status_code=200)
async def update_user_password(payload: UpdatePasswordSchema, 
                               user: User = Depends(get_current_user), 
                               service: UserService = Depends(create_user_service)):
    return await service.update_user_password_service(user.id, payload.new_password, payload.old_password)

@user_router.put("/update-email", response_model=SuccessMessage, status_code=200)
async def update_user_email(payload: UpdateEmailSchema, 
                            user: User = Depends(get_current_user), 
                            service: UserService = Depends(create_user_service)):
    
    return await service.update_email_service(user.id, payload.email)

@user_router.put("/update-user", response_model=UserResponseSchema, status_code=200)
async def update_user_user(payload: UserUpdateSchema, 
                            user: User = Depends(get_current_user), 
                            service: UserService = Depends(create_user_service)):
    
    return await service.update_user_fields_service(payload, user.id)

# User DELETE requests
@user_router.delete("/delete-user", response_model=SuccessMessage, status_code=200)
async def delete_user(user: User = Depends(get_current_user), service: UserService = Depends(create_user_service)):
    return await service.delete_current_user_service(user.id)

# Admin requests
@user_router.get("/admin/user", response_model=UserResponseSchema, status_code=200)
async def get_user_by_id(user_id: str, service: UserService = Depends(create_user_service), _: User = Depends(require_admin)):
    return await service.get_user_by_id_service(UUID(user_id))

@user_router.get("/admin/users", response_model=list[UserResponseSchema], status_code=200)
async def get_all_users(service: UserService = Depends(create_user_service), _: User = Depends(require_admin)):
    return await service.get_all_users_service()

@user_router.put("/admin/update", response_model=UserResponseSchema, status_code=200)
async def update_user(user_id: str, 
                      payload: AdminUpdateUserEmailSchema, 
                      service: UserService = Depends(create_user_service), 
                      _: User = Depends(require_admin)):
    return await service.update_user_email_by_id_service(UUID(user_id), payload.email)

@user_router.delete("/admin/delete", response_model=SuccessMessage, status_code=200)
async def admin_delete_user(user_id: str, service: UserService = Depends(create_user_service), admin: User = Depends(require_admin)):
    if user_id == str(admin.id):
        raise ForbiddenError("You cannot delete your own account.")
    
    return await service.delete_user_by_id_service(UUID(user_id))

@user_router.post("/admin/bulk-delete", response_model=BulkDeleteResult, status_code=200)
async def bulk_delete_users(payload: BulkDeleteUsersSchema, 
                            service: UserService = Depends(create_user_service), 
                            admin: User = Depends(require_admin)):
    return await service.delete_multiple_users_by_ids_service(payload.user_ids, admin.id)
