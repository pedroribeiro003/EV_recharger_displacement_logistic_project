from typing import Generic, Optional, Sequence, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.models.base import Base

T = TypeVar("T", bound=Base)


class BaseRepository(Generic[T]):
    model: type[T]

    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Basic CRUD
    # ------------------------------------------------------------------

    def get(self, id: int) -> Optional[T]:
        return self.session.get(self.model, id)

    def get_all(self, limit: int = 1000, offset: int = 0) -> Sequence[T]:
        stmt = select(self.model).limit(limit).offset(offset)
        return self.session.scalars(stmt).all()

    def create(self, obj: T) -> T:
        self.session.add(obj)
        self.session.flush()
        self.session.refresh(obj)
        return obj

    def bulk_create(self, objs: list[T], batch_size: int = 500) -> int:
        total = 0
        for i in range(0, len(objs), batch_size):
            batch = objs[i : i + batch_size]
            self.session.add_all(batch)
            self.session.flush()
            total += len(batch)
        return total

    def update(self, obj: T) -> T:
        self.session.add(obj)
        self.session.flush()
        self.session.refresh(obj)
        return obj

    def delete(self, id: int) -> bool:
        obj = self.get(id)
        if obj is None:
            return False
        self.session.delete(obj)
        self.session.flush()
        return True

    def count(self) -> int:
        stmt = select(func.count()).select_from(self.model)
        return self.session.scalar(stmt) or 0
