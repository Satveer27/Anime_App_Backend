from fastapi import APIRouter, Depends, Cookie, Response, Header
from app.security.schemas import UserRequestLogin, TokenResponse
from app.schemas import SuccessMessage
from app.security.deps import create_jwt_service
from app.security.service import JWTService
from app.security.exceptions import InvalidTokenError
from app.config import settings
from app.core.deps import optional_security
from fastapi.security import HTTPAuthorizationCredentials

auth_router = APIRouter(prefix="/auth", tags=["auth"])

@auth_router.post("/refresh", response_model=TokenResponse, status_code=200)
async def refresh_access_token( response: Response,
                                refresh_token: str | None = Cookie(default=None), 
                                service: JWTService = Depends(create_jwt_service),):
    if refresh_token is None:
        raise InvalidTokenError()
    else:
        result = await service.refresh_access_token_service(refresh_token)
        response.set_cookie(
                key="refresh_token",
                value=result.refresh_token,
                httponly=True,
                secure=settings.http_secure,       
                samesite="lax",
                max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
                path="/auth"
            )
        return TokenResponse(access_token=result.access_token, token_type="bearer")

@auth_router.post("/login", response_model=TokenResponse, status_code=200)
async def login(request: UserRequestLogin, 
                response: Response, 
                refresh_token: str | None = Cookie(default=None),
                service: JWTService = Depends(create_jwt_service)):

    result = await service.login_service(request.email, request.password, refresh_token)
    response.set_cookie(
        key="refresh_token",
        value=result.refresh_token,
        httponly=True,
        secure=settings.http_secure,       
        samesite="lax",
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
        path="/auth"
    )
    return TokenResponse(access_token=result.access_token, token_type="bearer")

@auth_router.post("/logout", response_model=SuccessMessage, status_code=200)
async def logout(response: Response, 
                 refresh_token: str | None = Cookie(default=None), 
                 credentials: HTTPAuthorizationCredentials | None = Depends(optional_security),
                 service: JWTService = Depends(create_jwt_service)):
    
    access_token = None
    if credentials:
        access_token = credentials.credentials

    if refresh_token is None:
        raise InvalidTokenError()

    await service.logout_service(refresh_token, access_token)

    response.delete_cookie("refresh_token", path="/auth")

    return SuccessMessage(success_message="Logged out successfully")


@auth_router.post("/logout-all", response_model=SuccessMessage, status_code=200)
async def logout_all(response: Response,
                     refresh_token: str | None = Cookie(default=None), 
                     service: JWTService = Depends(create_jwt_service)):
    
    if refresh_token is None:
        raise InvalidTokenError()
    
    await service.logout_all_accounts(refresh_token)
    
    response.delete_cookie("refresh_token", path="/auth")
    
    return SuccessMessage(success_message="Logged out all accounts successfully")