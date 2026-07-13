"""Testes para mcd_donalds/checks.py.

O check de site e informativo (a Imperva responde 403 a um GET simples, e o
login do Selenium e quem prova acesso de verdade). Falha nele nao derruba a
pipeline. Ja o check de diretorios continua fatal.
"""

from __future__ import annotations

import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from mcd_donalds import checks
from mcd_donalds.checks import verificar
from mcd_donalds.config import Settings
from mcd_donalds.context import RunContext, StageStatus
from mcd_donalds.errors import DirectoryError, SiteAccessError


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


def test_site_inacessivel_nao_derruba_pipeline(
    settings: Settings, ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """403 da Imperva vira warning, nao falha — evita falso negativo."""
    def _bloqueado(_settings: Settings, _ctx: RunContext) -> None:
        raise SiteAccessError("Site retornou HTTP 403 (esperado 200)")

    monkeypatch.setattr(checks, "_verificar_site", _bloqueado)

    assert verificar(settings, ctx) is True
    assert ctx.stage_status["checks"] == StageStatus.PASSED


def test_site_ok_passa(
    settings: Settings, ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    chamado = {"n": 0}

    def _ok(_settings: Settings, _ctx: RunContext) -> None:
        chamado["n"] += 1

    monkeypatch.setattr(checks, "_verificar_site", _ok)

    assert verificar(settings, ctx) is True
    assert chamado["n"] == 1


def test_dry_run_nao_consulta_site(
    settings: Settings, ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _nao_deve_ser_chamado(_settings: Settings, _ctx: RunContext) -> None:
        raise AssertionError("dry-run nao pode bater no portal")

    monkeypatch.setattr(checks, "_verificar_site", _nao_deve_ser_chamado)
    settings.dry_run = True

    assert verificar(settings, ctx) is True


def test_diretorio_inviavel_continua_fatal(
    settings: Settings, ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O check de diretorios NAO foi afrouxado: sem disco, a pipeline para."""
    def _sem_disco(_settings: Settings, _ctx: RunContext) -> None:
        raise DirectoryError("disco cheio")

    monkeypatch.setattr(checks, "_verificar_diretorios", _sem_disco)
    settings.verificar_site = False

    with pytest.raises(DirectoryError):
        verificar(settings, ctx)
    assert ctx.stage_status["checks"] == StageStatus.FAILED
