from pydantic import BaseModel, EmailStr, Field

from app.schemas import Password


class RefreshResponse(BaseModel):
    refresh_token: str = Field(..., description="The newly generated refresh token")
    access_token: str = Field(..., description="The newly generated access token")


class UserRequestLogin(BaseModel):
    email: EmailStr = Field(..., max_length=255, description="The email of the user")
    password: Password = Field(..., max_length=60, description="The password of the user")


class TokenResponse(BaseModel):
    access_token: str = Field(..., description="The access token")
    token_type: str = Field(max_length=255, default="Bearer", description="The type of token")
