import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, foreign_key


class AuditLog(IdMixin, Base):
    __tablename__= "audit_logs"

    timestamp: Mapped[datetime]= mapped_column(DateTime(timezone=True), server_default=func.now())
    user_id: Mapped[uuid.UUID | None]= foreign_key("users.id", nullable=True, ondelete="SET NULL")
    repository_id: Mapped[uuid.UUID | None]= foreign_key("repositories.id", nullable=True, ondelete="SET NULL")
    action: Mapped[str]= mapped_column(String)
    details: Mapped[dict | None]= mapped_column(JSONB)
