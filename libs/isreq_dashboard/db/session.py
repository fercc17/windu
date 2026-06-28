"""Session factory."""

from __future__ import annotations

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)
