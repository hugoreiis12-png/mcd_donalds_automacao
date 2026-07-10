"""Gates de qualidade: validam dados apos transformacao.

Antes da carga no banco, cada lote de dados passa por:
    1. NullFieldGate  → campos obrigatorios vazios
    2. DuplicateGate  → linhas duplicadas
    3. RangeGate      → valores numericos fora dos limites
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from mcd_donalds.context import RunContext
from mcd_donalds.errors import DuplicateError, NullFieldError, RangeError


# Campos obrigatorios: apenas os ESTRUTURAIS/identidade, que todo registro
# valido tem e que sustentam a carga idempotente (dt_criacao e a chave do
# DELETE por periodo). Os demais campos sao DESCRITIVOS e o portal Martin
# Brower legitimamente os deixa em branco (ex: status, motivo) — exigi-los
# descartaria dado real. Vazios nesses campos viram NULL no banco (o DDL nao
# tem NOT NULL). O gate de nulos aqui serve so para detectar quebra estrutural
# (uma dessas colunas-chave vindo toda vazia = layout do relatorio mudou).
_CAMPOS_OBRIGATORIOS: list[str] = [
    "n_oc_ad", "tipo_doc", "dt_criacao",
]

# Intervalos aceitaveis para campos numericos.
_RANGES: dict[str, tuple[int, int]] = {
    "n_oc_ad": (1, 9_999_999),
    "qtde_faturada": (0, 99_999),
    "qtde_reclamada": (0, 99_999),
    "qtde_autorizada": (0, 99_999),
}


@dataclass
class RelatorioQualidade:
    """Relatorio com todos os problemas encontrados."""
    total_linhas: int = 0
    erros: list[dict[str, Any]] = field(default_factory=list)
    nulos: int = 0
    duplicatas: int = 0
    fora_range: int = 0

    @property
    def valido(self) -> bool:
        return len(self.erros) == 0


def validar(
    df: "pd.DataFrame",
    ctx: RunContext,
    modo: str = "rigido",
) -> RelatorioQualidade:
    """Executa todos os gates de qualidade no DataFrame.

    Args:
        df: DataFrame ja normalizado (colunas = COLUNAS_DB).
        ctx: Contexto de execucao.
        modo: "rigido" → levanta excecao no primeiro erro.
              "relatorio" → acumula todos os erros e retorna relatorio.

    Returns:
        RelatorioQualidade com sumario dos problemas.

    Raises (apenas no modo "rigido"):
        NullFieldError: campo obrigatorio nulo.
        DuplicateError: linhas duplicadas.
        RangeError: valor fora do intervalo aceitavel.
    """
    relatorio = RelatorioQualidade(total_linhas=len(df))

    relatorio = _gate_nulos(df, ctx, relatorio)
    if not relatorio.valido and modo == "rigido":
        _levantar_primeiro(relatorio)

    relatorio = _gate_duplicatas(df, ctx, relatorio)
    if not relatorio.valido and modo == "rigido":
        _levantar_primeiro(relatorio)

    relatorio = _gate_range(df, ctx, relatorio)
    if not relatorio.valido and modo == "rigido":
        _levantar_primeiro(relatorio)

    if relatorio.valido:
        ctx.logger.info(
            "Qualidade OK — %d linhas, 0 erros", relatorio.total_linhas
        )
    else:
        ctx.logger.warning(
            "Qualidade: %d erros (%d nulos, %d duplicatas, %d fora range)",
            len(relatorio.erros),
            relatorio.nulos,
            relatorio.duplicatas,
            relatorio.fora_range,
        )

    return relatorio


# ── gates individuais ──


def _gate_nulos(
    df: "pd.DataFrame", ctx: RunContext, relatorio: RelatorioQualidade
) -> RelatorioQualidade:
    """Verifica campos obrigatorios nulos ou vazios."""
    for col in _CAMPOS_OBRIGATORIOS:
        if col not in df.columns:
            continue
        nulos = df[col].isna() | (df[col].astype(str).str.strip() == "")
        indices = df.index[nulos].tolist()
        for idx in indices:
            relatorio.erros.append({
                "gate": "null",
                "coluna": col,
                "linha": int(idx),
                "mensagem": f"Campo '{col}' vazio na linha {idx}",
            })
            relatorio.nulos += 1
    return relatorio


def _gate_duplicatas(
    df: "pd.DataFrame", ctx: RunContext, relatorio: RelatorioQualidade
) -> RelatorioQualidade:
    """Verifica linhas duplicadas (considerando todas as colunas)."""
    dup = df.duplicated(keep="first")
    indices = df.index[dup].tolist()
    for idx in indices:
        relatorio.erros.append({
            "gate": "duplicate",
            "coluna": "todas",
            "linha": int(idx),
            "mensagem": f"Linha duplicada da original no indice {idx}",
        })
        relatorio.duplicatas += 1
    return relatorio


def _gate_range(
    df: "pd.DataFrame",
    ctx: RunContext,
    relatorio: RelatorioQualidade,
) -> RelatorioQualidade:
    """Verifica campos numericos dentro dos intervalos aceitaveis."""
    for col, (min_v, max_v) in _RANGES.items():
        if col not in df.columns:
            continue
        try:
            serie = pd.to_numeric(df[col], errors="coerce")
            fora = serie.isna() | (serie < min_v) | (serie > max_v)
            indices = df.index[fora].tolist()
            for idx in indices:
                valor = df.loc[idx, col]
                relatorio.erros.append({
                    "gate": "range",
                    "coluna": col,
                    "linha": int(idx),
                    "mensagem": (
                        f"Campo '{col}' = {valor} fora do intervalo "
                        f"[{min_v}, {max_v}] na linha {idx}"
                    ),
                })
                relatorio.fora_range += 1
        except Exception:
            continue
    return relatorio


def _levantar_primeiro(relatorio: RelatorioQualidade) -> None:
    """Levanta excecao para o primeiro erro encontrado (modo rigido)."""
    if not relatorio.erros:
        return
    err = relatorio.erros[0]
    gate = err["gate"]

    if gate == "null":
        raise NullFieldError(err["mensagem"])
    if gate == "duplicate":
        raise DuplicateError(err["mensagem"])
    if gate == "range":
        raise RangeError(err["mensagem"])
