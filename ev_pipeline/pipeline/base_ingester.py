"""
pipeline/base_ingester.py
Classe base para todos os ingestores de dados brutos.
Define o contrato fetch → parse → upsert e métricas básicas.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from config.database import get_session
from utils.logger import get_logger


@dataclass
class IngestionResult:
    source: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    records_fetched: int = 0
    records_inserted: int = 0
    records_updated: int = 0
    records_skipped: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0

    @property
    def duration_seconds(self) -> float | None:
        if self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None

    def finish(self) -> "IngestionResult":
        self.finished_at = datetime.now(timezone.utc)
        return self

    def __str__(self) -> str:
        return (
            f"[{self.source}] "
            f"fetched={self.records_fetched} "
            f"inserted={self.records_inserted} "
            f"updated={self.records_updated} "
            f"skipped={self.records_skipped} "
            f"errors={len(self.errors)} "
            f"duration={self.duration_seconds:.1f}s"
        )


class BaseIngester(ABC):
    """
    Contrato base para ingestores de dados brutos (staging).

    Subclasses implementam:
      - fetch_raw()  → busca dados da fonte externa
      - parse()      → transforma em lista de dicts prontos para upsert
      - upsert()     → persiste no banco via SQLAlchemy
    """

    source_name: str = "unknown"

    def __init__(self) -> None:
        self.logger = get_logger(f"pipeline.{self.source_name}")

    @abstractmethod
    def fetch_raw(self, **kwargs) -> list[dict[str, Any]]:
        """Busca dados da API/fonte externa. Retorna lista de dicts brutos."""
        ...

    @abstractmethod
    def parse(self, raw_records: list[dict[str, Any]]) -> list[Any]:
        """
        Transforma dicts brutos em instâncias dos models SQLAlchemy.
        Não deve fazer queries ao banco.
        """
        ...

    @abstractmethod
    def upsert(self, session: Session, records: list[Any]) -> tuple[int, int]:
        """
        Persiste registros no banco.
        Retorna (inserted, updated).
        """
        ...

    def run(self, **kwargs) -> IngestionResult:
        """
        Executa o ciclo completo: fetch → parse → upsert.
        Gerencia sessão, logging e tratamento de erros.
        """
        result = IngestionResult(source=self.source_name)
        self.logger.info("Iniciando ingestão: %s", self.source_name)

        try:
            raw = self.fetch_raw(**kwargs)
            result.records_fetched = len(raw)
            self.logger.info("Registros obtidos da fonte: %d", result.records_fetched)

            records = self.parse(raw)

            with get_session() as session:
                inserted, updated = self.upsert(session, records)
                result.records_inserted = inserted
                result.records_updated = updated

        except Exception as exc:
            result.errors.append(str(exc))
            self.logger.exception("Erro durante ingestão de %s", self.source_name)

        result.finish()
        self.logger.info("%s", result)
        return result
