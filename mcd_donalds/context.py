"""Contexto de execucao: RunContext, StageStatus e logging estruturado.

Cada execucao da pipeline ganha um run_id (UUID) que a acompanha ate
o fim, permitindo rastrear logs, auditoria e eventuais falhas.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TextIO
from uuid import uuid4, UUID


# Namespace base do logger da pipeline (neutro — nao acoplado a nenhum
# dominio de negocio). Cada run vira o filho "{NAMESPACE}.{run_id}".
LOGGER_NAMESPACE = "pipeline"


# ── status de cada etapa ──


class StageStatus(str, Enum):
    """Situacao de uma etapa (extract / transform / load / quality)."""
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


# ── logger seguro ──


class _SafeStreamHandler(logging.StreamHandler[TextIO]):
    """StreamHandler que substitui caracteres nao graficaveis no terminal."""

    def __init__(self, stream: TextIO) -> None:
        super().__init__(stream)
        self._safe_stream = stream

    def emit(self, record: logging.LogRecord) -> None:
        try:
            super().emit(record)
        except UnicodeEncodeError:
            msg = self.format(record)
            enc = self._safe_stream.encoding or "utf-8"
            try:
                self._safe_stream.write(
                    msg.encode(enc, errors="replace").decode(enc)
                    + self.terminator
                )
                self._safe_stream.flush()
            except Exception:
                self.handleError(record)


# ── contexto compartilhado ──


class RunContext:
    """Agrupa metadados da execucao corrente.

    Attributes:
        run_id: UUID unico desta execucao.
        stage_status: Mapa de etapa -> StageStatus.
        inicio: Timestamp de criacao do contexto.
        settings_ref: Referencia opcional ao Settings (evita import circular).
    """

    def __init__(self) -> None:
        self.run_id: UUID = uuid4()
        self.stage_status: dict[str, StageStatus] = {}
        self.inicio: datetime = datetime.now()
        self._logger: logging.Logger | None = None

    # ── gerenciamento de etapas ──

    def iniciar_etapa(self, etapa: str) -> None:
        self.stage_status[etapa] = StageStatus.RUNNING

    def finalizar_etapa(self, etapa: str, status: StageStatus) -> None:
        self.stage_status[etapa] = status

    def etapa_ok(self, etapa: str) -> bool:
        return self.stage_status.get(etapa) == StageStatus.PASSED

    # ── logger ──

    @property
    def logger(self) -> logging.Logger:
        if self._logger is None:
            self._logger = logging.getLogger(f"{LOGGER_NAMESPACE}.{self.run_id}")
        return self._logger

    def configurar_logger(self, level: int = logging.INFO,
                          log_file: Path | None = None) -> None:
        """Configura handler de console e, opcionalmente, arquivo.

        Idempotente: remove handlers previos antes de anexar novos, para que
        uma segunda chamada nao duplique cada linha de log. propagate=False
        evita que as mensagens subam para o root logger (dupla emissao).
        """
        logger = self.logger
        logger.setLevel(level)
        logger.propagate = False
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

        fmt = logging.Formatter(
            "[%(asctime)s] %(levelname)-8s %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # console (com fallback para caracteres nao suportados pela codificacao
        # do terminal, ex: Windows cp1252)
        ch = _SafeStreamHandler(sys.stdout)
        ch.setLevel(level)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

        # arquivo
        if log_file:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(str(log_file), encoding="utf-8")
            fh.setLevel(level)
            fh.setFormatter(fmt)
            logger.addHandler(fh)

    # ── duracao ──

    @property
    def duracao_segundos(self) -> float:
        return (datetime.now() - self.inicio).total_seconds()

    def __repr__(self) -> str:
        return (f"RunContext(run_id={self.run_id}, "
                f"inicio={self.inicio.isoformat()})")
