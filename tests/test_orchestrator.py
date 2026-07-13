"""Testes para mcd_donalds/orchestrator.py.

Cobre os dois pontos tocados na manutencao:
    - _executar_etapa: auditoria recebe a excecao ORIGINAL (antes ela era
      reconstruida com type(exc)(str(exc)), o que estoura em excecoes cujo
      __init__ exige mais de um argumento).
    - _limpar_antigos: retencao opt-in (0 = desligado, comportamento atual).
"""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from mcd_donalds import audit
from mcd_donalds.config import Settings
from mcd_donalds.context import RunContext, StageStatus
from mcd_donalds.orchestrator import _executar_etapa, _limpar_antigos


# ── fixtures ──


@pytest.fixture
def tmp_base() -> Generator[str, None, None]:
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def settings(tmp_base: str) -> Settings:
    return Settings(base_dir=Path(tmp_base))


@pytest.fixture
def ctx() -> RunContext:
    return RunContext()


@pytest.fixture
def auditoria(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Substitui audit.registrar por um espiao (nao toca no banco)."""
    chamadas: list[dict[str, Any]] = []

    def _fake(
        _ctx: RunContext,
        _settings: Settings,
        etapa: str,
        status: str,
        **kwargs: Any,
    ) -> None:
        chamadas.append({"etapa": etapa, "status": status, **kwargs})

    # O orchestrator faz `from mcd_donalds import audit` e chama audit.registrar,
    # ou seja, guarda o MODULO — patchar o atributo aqui alcanca a chamada dele.
    monkeypatch.setattr(audit, "registrar", _fake)
    return chamadas


# Excecao cujo __init__ exige DOIS argumentos: e exatamente o caso que a
# reconstrucao type(exc)(str(exc)) quebrava.
class _ErroComDoisArgs(Exception):
    def __init__(self, codigo: int, detalhe: str) -> None:
        super().__init__(f"[{codigo}] {detalhe}")
        self.codigo = codigo


# ── _executar_etapa ──


def test_executar_etapa_audita_excecao_original(
    settings: Settings, ctx: RunContext, auditoria: list[dict[str, Any]]
) -> None:
    """Excecao inesperada com __init__ de 2 args nao pode estourar na auditoria."""
    def _falha() -> None:
        raise _ErroComDoisArgs(42, "portal fora do ar")

    ok = _executar_etapa("extract", _falha, settings, ctx)

    assert ok is False
    assert len(auditoria) == 1
    assert auditoria[0]["status"] == "failed"
    # A excecao chega intacta (tipo + mensagem), sem reconstrucao.
    erro = auditoria[0]["erro"]
    assert isinstance(erro, _ErroComDoisArgs)
    assert erro.codigo == 42


def test_executar_etapa_sucesso_audita_passed(
    settings: Settings, ctx: RunContext, auditoria: list[dict[str, Any]]
) -> None:
    def _ok() -> None:
        ctx.finalizar_etapa("transform", StageStatus.PASSED)

    assert _executar_etapa("transform", _ok, settings, ctx) is True
    assert auditoria[0]["status"] == "passed"


# ── _limpar_antigos ──


def _envelhecer(arquivo: Path, dias: int) -> None:
    """Recua o mtime do arquivo em N dias."""
    antigo = time.time() - dias * 86_400
    os.utime(arquivo, (antigo, antigo))


def test_retencao_desligada_por_default_nao_apaga_nada(
    settings: Settings, ctx: RunContext
) -> None:
    """retencao_dias=0 (default) preserva o comportamento atual: guarda tudo."""
    assert settings.retencao_dias == 0

    settings.xlsx_dir.mkdir(parents=True, exist_ok=True)
    velho = settings.xlsx_dir / "exportarDados(001).xls"
    velho.write_bytes(b"x")
    _envelhecer(velho, dias=999)

    _limpar_antigos(settings, ctx)

    assert velho.exists()


def test_retencao_apaga_antigos_e_preserva_recentes(
    settings: Settings, ctx: RunContext
) -> None:
    settings.retencao_dias = 7
    for diretorio in (settings.xlsx_dir, settings.csv_dir, settings.logs_dir):
        diretorio.mkdir(parents=True, exist_ok=True)

    antigo = settings.xlsx_dir / "exportarDados(001).xls"
    antigo.write_bytes(b"x")
    _envelhecer(antigo, dias=30)

    log_antigo = settings.logs_dir / "pipeline_antigo.log"
    log_antigo.write_text("velho")
    _envelhecer(log_antigo, dias=8)

    # Artefatos "desta execucao": mtime = agora, nunca podem ser tocados.
    recente = settings.csv_dir / "exportarDados(002)_db.csv"
    recente.write_text("n_oc_ad\n1\n")

    _limpar_antigos(settings, ctx)

    assert not antigo.exists()
    assert not log_antigo.exists()
    assert recente.exists()
