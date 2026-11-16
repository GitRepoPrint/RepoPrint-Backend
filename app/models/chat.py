import uuid

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, IdMixin, foreign_key


class ChatConversation(IdMixin, CreatedAtMixin, Base):
    __tablename__= "chat_conversations"

    analysis_run_id: Mapped[uuid.UUID]= foreign_key("analysis_runs.id")
    threat_finding_id: Mapped[uuid.UUID | None]= foreign_key("threat_findings.id", nullable=True, ondelete="SET NULL")
    user_id: Mapped[uuid.UUID]= foreign_key("users.id")


class ChatMessage(IdMixin, CreatedAtMixin, Base):
    __tablename__= "chat_messages"

    conversation_id: Mapped[uuid.UUID]= foreign_key("chat_conversations.id")
    sender_type: Mapped[str]= mapped_column(String)
    message: Mapped[str]= mapped_column(Text)
