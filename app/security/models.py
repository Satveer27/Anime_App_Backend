from app.core.database.database import Base
from sqlalchemy import Boolean, Column, String, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID


class RefreshToken(Base) :
    __tablename__ = "refresh_token"

    jti = Column(PG_UUID(as_uuid=True), primary_key=True, index=True)
    user_id = Column(PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    revoke = Column(Boolean, default=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())