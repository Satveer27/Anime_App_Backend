from app.schemas import SuccessMessage
from app.users.schemas import ForgotPasswordSchema, ResendVerificationSchema, ResetPasswordSchema, UserResponseSchema, UserCreateSchema
from app.users.deps import create_user_service
from fastapi import Depends
from app.users.service import UserService
from fastapi import APIRouter

user_router = APIRouter(prefix="/users", tags=["users"])

@user_router.post("/signup", response_model=UserResponseSchema, status_code=201)
async def signup(payload: UserCreateSchema, service : UserService = Depends(create_user_service)):
    return await service.create_user_service(payload)

@user_router.get("/verify-email", response_model=SuccessMessage, status_code=200)
async def verify_email(token:str, service: UserService = Depends(create_user_service)):
    return await service.verify_user_email_service(token)

@user_router.post("/resend-verification", response_model=SuccessMessage, status_code=200)
async def resend_verification(payload: ResendVerificationSchema, service: UserService = Depends(create_user_service)):
    return await service.resend_email_verification_service(payload.email)

@user_router.post("/forgot-password", response_model=SuccessMessage, status_code=200)
async def forgot_password(payload: ForgotPasswordSchema, service: UserService = Depends(create_user_service)):
    return await service.forgot_password_service(payload.email)

@user_router.post("/reset-password", response_model=SuccessMessage, status_code=200)
async def reset_password(token: str, payload: ResetPasswordSchema, service: UserService = Depends(create_user_service)):
    return await service.reset_password_service_via_email(token, payload.password)