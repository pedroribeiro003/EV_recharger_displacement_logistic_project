from datetime import datetime, timezone, timedelta
from typing import Any, Sequence

from sqlalchemy import func, select, text

from db.models.snapshot import StatusSnapshot
from db.repositories.base import BaseRepository


class SnapshotRepository(BaseRepository[StatusSnapshot]):
    model = StatusSnapshot

    def bulk_insert(self, snapshots: list[StatusSnapshot]) -> int:
        """Fast batch insert — no conflict check."""
        if not snapshots:
            return 0
        self.session.add_all(snapshots)
        self.session.flush()
        return len(snapshots)

    def get_recent(self, station_id: int, limit: int = 100) -> Sequence[StatusSnapshot]:
        stmt = (
            select(StatusSnapshot)
            .where(StatusSnapshot.station_id == station_id)
            .order_by(StatusSnapshot.captured_at.desc())
            .limit(limit)
        )
        return self.session.scalars(stmt).all()

    def get_occupancy_by_hour(
        self, station_id: int, days_back: int = 30
    ) -> dict[int, float]:
        """Return {hour: occupancy_rate} where occupancy_rate is fraction of time occupied."""
        since = datetime.now(timezone.utc) - timedelta(days=days_back)
        stmt = text(
            """
            SELECT
                EXTRACT(HOUR FROM captured_at AT TIME ZONE 'America/Sao_Paulo')::int AS hour,
                COUNT(*) FILTER (WHERE status = 'OCCUPIED') * 1.0 / NULLIF(COUNT(*), 0) AS rate
            FROM status_snapshots
            WHERE station_id = :station_id
              AND captured_at >= :since
            GROUP BY 1
            ORDER BY 1
            """
        )
        rows = self.session.execute(stmt, {"station_id": station_id, "since": since}).fetchall()
        return {int(row[0]): float(row[1] or 0) for row in rows}

    def get_station_stats(
        self, station_id: int, days_back: int = 30
    ) -> dict[str, Any]:
        """Return aggregate stats dict for a station over the past N days."""
        since = datetime.now(timezone.utc) - timedelta(days=days_back)
        stmt = text(
            """
            SELECT
                AVG(CASE WHEN status = 'OCCUPIED' THEN 1.0 ELSE 0.0 END) AS avg_occupancy,
                AVG(session_minutes) AS avg_session_minutes,
                AVG(CASE WHEN status = 'FAULT' THEN 1.0 ELSE 0.0 END) AS fault_rate,
                MODE() WITHIN GROUP (
                    ORDER BY EXTRACT(HOUR FROM captured_at AT TIME ZONE 'America/Sao_Paulo')::int
                ) AS peak_hour
            FROM status_snapshots
            WHERE station_id = :station_id
              AND captured_at >= :since
            """
        )
        row = self.session.execute(stmt, {"station_id": station_id, "since": since}).fetchone()
        if row is None:
            return {}
        return {
            "avg_occupancy": float(row[0] or 0),
            "avg_session_minutes": float(row[1] or 0),
            "fault_rate": float(row[2] or 0),
            "peak_hour": int(row[3]) if row[3] is not None else None,
        }
