from pydantic import BaseModel, EmailStr, Field, field_validator, ValidationInfo
from app.users.enum import F1Teams
from uuid import UUID
from datetime import datetime

#Tools
class NormalizedEmailMixin(BaseModel):
    @field_validator("email", check_fields=False)
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.lower()

# Schemas
class UserCreateSchema(NormalizedEmailMixin):
    email: EmailStr = Field(..., max_length=255, description="The email of the user")
    password: str = Field(..., min_length=8, max_length=60, description="The password of the user")
    username: str = Field(..., min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_-]+$", description="The username of the user")
    f1_team: F1Teams = Field(..., description="The F1 team of the user")

class UserUpdateSchema(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_-]+$", description="The username of the user")

class UserResponseSchema(BaseModel):
    id: UUID = Field(..., description="The ID of the user")
    email: EmailStr = Field(..., max_length=255, description="The email of the user")
    username: str = Field(..., min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_-]+$", description="The username of the user")
    is_admin: bool = Field(..., description="Whether the user is an admin")
    created_at: datetime  = Field(..., description="The creation date of the user")

    model_config = {"from_attributes": True}

class ResendVerificationSchema(NormalizedEmailMixin):
    email: EmailStr = Field(..., max_length=255, description="The email of the user")
    
class ForgotPasswordSchema(NormalizedEmailMixin):
    email: EmailStr = Field(..., max_length=255, description="The email of the user")

class AdminUpdateUserEmailSchema(NormalizedEmailMixin):
    email: EmailStr = Field(..., max_length=255, description="The email of the user")

class UpdateEmailSchema(NormalizedEmailMixin):
    email: EmailStr = Field(..., max_length=255, description="The email of the user")

class BulkDeleteUsersSchema(BaseModel):
    user_ids: list[UUID] = Field(..., min_length=1, max_length=50)

class BulkDeleteResult(BaseModel):
    deleted: list[UUID]
    not_found: list[UUID]

class ResetPasswordSchema(BaseModel):
    password: str = Field(..., min_length=8, max_length=60, description="The new password of the user")
    confirm_password: str = Field(..., min_length=8, max_length=60, description="The confirmation of the new password")

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v: str, info: ValidationInfo) -> str:
        if "password" in info.data and v != info.data["password"]:
            raise ValueError("Passwords do not match")
        return v

class UpdatePasswordSchema(BaseModel):
    old_password: str = Field(..., min_length=8, max_length=60, description="The old password of the user")
    new_password: str = Field(..., min_length=8, max_length=60, description="The new password of the user")
    confirm_new_password: str = Field(..., min_length=8, max_length=60, description="The confirmation of the new password")

    @field_validator("confirm_new_password")
    @classmethod
    def passwords_match(cls, v: str, info: ValidationInfo) -> str:
        if "new_password" in info.data and v != info.data["new_password"]:
            raise ValueError("Passwords do not match")
        return v

class GetAllUserSchema(BaseModel):
    email: EmailStr | None = Field(default= None, max_length=255, description="The email of the user")
    username: str | None = Field(default= None, min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_-]+$", description="The username of the user")
    is_admin: bool | None = Field(default= None, description="Whether the user is an admin")
    created_after: datetime | None = Field(default= None, description="Filter for users created after the date")
    created_before: datetime | None = Field(default= None, description="Filter for users created before the date")
    username_sorted_bool: bool = Field(default= False, description="Whether you would want to sort the users by username")
    
class PaginatedUsersResponse(BaseModel):
    items: list[UserResponseSchema]
    total: int
    page_size: int
    page: int
