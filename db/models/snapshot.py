from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.models.base import Base


class StatusSnapshot(Base):
    __tablename__ = "status_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    station_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("stations.id"), nullable=False, index=True
    )
    connector_code: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    session_started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    energy_kwh: Mapped[Optional[float]] = mapped_column(Numeric(10, 3), nullable=True)
    session_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    station: Mapped["Station"] = relationship("Station", back_populates="snapshots")

    @classmethod
    def from_tupi(cls, station_id: int, raw_connector: dict) -> "StatusSnapshot":
        """Build a snapshot from a raw Tupi API connector payload."""
        status = raw_connector.get("status") or raw_connector.get("state") or "UNKNOWN"
        session = raw_connector.get("currentSession") or {}
        energy = session.get("energyDelivered") or session.get("energy_kwh")
        duration = session.get("duration") or session.get("session_minutes")
        started_raw = session.get("startTime") or session.get("started_at")

        started_at: Optional[datetime] = None
        if started_raw:
            try:
                started_at = datetime.fromisoformat(str(started_raw).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        return cls(
            station_id=station_id,
            connector_code=str(raw_connector.get("connectorId") or raw_connector.get("id", "")),
            status=str(status).upper(),
            session_started_at=started_at,
            energy_kwh=float(energy) if energy is not None else None,
            session_minutes=int(duration) if duration is not None else None,
        )
