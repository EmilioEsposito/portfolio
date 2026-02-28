from datetime import datetime
from sqlalchemy import String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from api.src.database.database import Base


class WebPushSubscription(Base):
    __tablename__ = "web_push_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    clerk_user_id: Mapped[str] = mapped_column(String, index=True)
    endpoint: Mapped[str] = mapped_column(String, unique=True)
    p256dh: Mapped[str] = mapped_column(String)
    auth: Mapped[str] = mapped_column(String)
    user_agent: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<WebPushSubscription(id={self.id}, user={self.clerk_user_id}, endpoint={self.endpoint[:30]}...)>"
