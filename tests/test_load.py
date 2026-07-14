"""Testes para mcd_donalds/stages/load.py.

Foco no guard de sanidade da carga (_validar_volume) — sem banco real:
    - guard desligado (default) nunca bloqueia, mesmo com carga encolhida
    - guard ligado bloqueia quando inseridos < removidos * limiar
    - guard ligado deixa passar quando a razao esta ok
    - removidos == 0 (tabela vazia / primeira carga) nunca bloqueia
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcd_donalds.config import Settings
from mcd_donalds.context import RunContext
from mcd_donalds.errors import LoadError
from mcd_donalds.stages.load import _validar_volume

_CSV = Path("exportarDados(001)_db.csv")


@pytest.fixture
def ctx() -> RunContext:
    return RunContext()


def test_guard_desligado_por_default_nao_bloqueia(ctx: RunContext) -> None:
    """Default 0.0 preserva o comportamento atual: carga encolhida passa."""
    settings = Settings()
    assert settings.carga_min_ratio == 0.0

    # Nao levanta mesmo com perda brutal (1000 -> 1).
    _validar_volume(1000, 1, settings, ctx, _CSV)


def test_guard_ligado_bloqueia_carga_encolhida(ctx: RunContext) -> None:
    """Export truncado: inserir menos que o limiar aborta antes do commit."""
    settings = Settings(carga_min_ratio=0.9)

    with pytest.raises(LoadError) as exc_info:
        _validar_volume(1000, 500, settings, ctx, _CSV)

    # A mensagem precisa dar ao operador os numeros para diagnosticar.
    msg = str(exc_info.value)
    assert "removidos=1000" in msg
    assert "inseridos=500" in msg
    assert "0.900" in msg


def test_guard_ligado_deixa_passar_razao_ok(ctx: RunContext) -> None:
    settings = Settings(carga_min_ratio=0.9)

    # Exatamente no limiar (900/1000 = 0.9) tambem passa: o corte e estrito.
    _validar_volume(1000, 900, settings, ctx, _CSV)
    _validar_volume(1000, 1000, settings, ctx, _CSV)
    _validar_volume(1000, 1500, settings, ctx, _CSV)


def test_removidos_zero_nunca_bloqueia(ctx: RunContext) -> None:
    """Tabela vazia / primeira carga: nao ha base de comparacao, nao checa."""
    settings = Settings(carga_min_ratio=0.9)

    _validar_volume(0, 0, settings, ctx, _CSV)
    _validar_volume(0, 5000, settings, ctx, _CSV)