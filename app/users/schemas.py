from pydantic import BaseModel, EmailStr, Field, field_validator
from app.users.enum import F1Teams, UserRank
from uuid import UUID
from datetime import datetime

class UserCreateSchema(BaseModel):
    email: EmailStr = Field(..., max_length=255, description="The email of the user")
    password: str = Field(..., min_length=8, max_length=60, description="The password of the user")
    username: str = Field(..., min_length=3, max_length=50, description="The username of the user")
    f1_team: F1Teams = Field(..., description="The F1 team of the user")

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.lower()


class UserResponseSchema(BaseModel):
    id: UUID = Field(..., description="The ID of the user")
    email: EmailStr = Field(..., max_length=255, description="The email of the user")
    username: str = Field(..., min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_-]+$", description="The username of the user")
    f1_team: F1Teams = Field(..., description="The F1 team of the user")
    rank: UserRank = Field(..., description="The rank of the user")
    rank_elo: int = Field(..., description="The rank elo of the user")
    is_admin: bool = Field(..., description="Whether the user is an admin")
    created_at: datetime  = Field(..., description="The creation date of the user")

    model_config = {"from_attributes": True}

class ResendVerificationSchema(BaseModel):
    email: EmailStr