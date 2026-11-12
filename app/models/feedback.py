import uuid

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, IdMixin, foreign_key


class FalsePositiveFeedback(IdMixin, CreatedAtMixin, Base):
    __tablename__= "false_positive_feedbacks"

    threat_finding_id: Mapped[uuid.UUID]= foreign_key("threat_findings.id")
    user_id: Mapped[uuid.UUID]= foreign_key("users.id")
    reason: Mapped[str | None]= mapped_column(Text)
