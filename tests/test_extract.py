"""Testes para mcd_donalds/stages/extract.py.

Foco no contrato de status da etapa — nenhum Chrome e aberto aqui:
    - dry-run          -> SKIPPED, lista vazia, nada baixado
    - tentativas esgotadas -> FAILED + excecao propagada (nao mais SKIPPED,
      que mascarava falha real como "nao havia o que baixar" na auditoria)
"""

from __future__ import annotations

import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from mcd_donalds.config import Settings
from mcd_donalds.context import RunContext, StageStatus
from mcd_donalds.errors import ExtractError, FormFillError
from mcd_donalds.stages import extract
from mcd_donalds.stages.extract import extrair


@pytest.fixture
def tmp_base() -> Generator[str, None, None]:
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def settings(tmp_base: str) -> Settings:
    # backoff zerado: o teste de retentativa nao pode dormir.
    # Credenciais explicitas: sem elas o Settings cairia no .env da maquina —
    # o teste passaria aqui e quebraria em qualquer ambiente sem .env.
    return Settings(
        base_dir=Path(tmp_base),
        max_tentativas=2,
        backoff_s=0.0,
        login_user="usuario",
        login_password="senha",
    )


@pytest.fixture
def ctx() -> RunContext:
    return RunContext()


def test_dry_run_pula_extracao(settings: Settings, ctx: RunContext) -> None:
    settings.dry_run = True

    assert extrair(settings, ctx) == []
    assert ctx.stage_status["extract"] == StageStatus.SKIPPED


def test_tentativas_esgotadas_marca_failed_e_propaga(
    settings: Settings, ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Falha real de extracao vira FAILED — nao pode ser auditada como SKIPPED."""
    tentativas = {"n": 0}

    def _driver_quebrado(_settings: Settings) -> object:
        tentativas["n"] += 1
        raise FormFillError("timeout ao efetuar login")

    monkeypatch.setattr(extract, "_init_driver", _driver_quebrado)

    with pytest.raises(ExtractError) as exc_info:
        extrair(settings, ctx)

    # O tipo especifico e preservado (vai para erro_tipo na auditoria).
    assert isinstance(exc_info.value, FormFillError)
    assert ctx.stage_status["extract"] == StageStatus.FAILED
    assert tentativas["n"] == settings.max_tentativas


def test_credencial_ausente_falha_antes_de_abrir_o_chrome(
    settings: Settings, ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sem credencial, a causa e nomeada — nao 3 timeouts de login opacos."""
    settings.login_password = ""

    def _nao_deve_abrir(_settings: Settings) -> object:
        raise AssertionError("Chrome nao pode ser aberto sem credencial")

    monkeypatch.setattr(extract, "_init_driver", _nao_deve_abrir)

    with pytest.raises(ExtractError, match="LOGIN_PASSWORD"):
        extrair(settings, ctx)

    assert ctx.stage_status["extract"] == StageStatus.FAILED