"""Database tables (SPEC §3.2). One class = one table, one attribute = one column."""

import datetime as dt

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Checkin(Base):
    __tablename__ = "checkin"

    id: Mapped[int] = mapped_column(primary_key=True)  # 1, 2, 3… assigned by the DB
    date: Mapped[dt.date] = mapped_column(index=True)
    created_at: Mapped[dt.datetime]

    # The answers, as received
    energy: Mapped[int]
    sleep: Mapped[int]
    motivation: Mapped[int]
    soreness: Mapped[int]
    minutes: Mapped[int]

    # The result of compute_readiness()
    score: Mapped[int]
    level: Mapped[str] = mapped_column(String(10))
    format: Mapped[str] = mapped_column(String(10))
