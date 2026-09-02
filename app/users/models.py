from app.core.database.database import Base
from sqlalchemy import Boolean, Column, Integer, String, DateTime, func, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from app.users.enum import UserRank, F1Teams
import uuid

class User(Base):
    __tablename__ = "users"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, index=True, default=uuid.uuid4)
    email = Column(String, unique=True, index=True, nullable=False)
    username = Column(String, nullable=False)
    password = Column(String, nullable=False)
    f1_team = Column(SQLEnum(F1Teams), nullable=False)
    rank = Column(SQLEnum(UserRank), default=UserRank.ROOKIE)
    rank_elo = Column(Integer, default=0)
    is_admin = Column(Boolean, default=False)
    email_verification_code = Column(String, nullable=True, unique=True, index=True)
    is_verified = Column(Boolean, default=False)
    email_verification_expiry = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


