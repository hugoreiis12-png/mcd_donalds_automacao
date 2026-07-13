"""Orquestrador: coordena o pipeline completo do inicio ao fim.

Fluxo automatico:
    checks → extract → transform → quality → load → notify

Cada etapa alimenta a seguinte com seus artefatos (XLS → CSV → DB).
Uma unica chamada a executar() roda tudo.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import pandas as pd

from mcd_donalds import audit, checks, notify
from mcd_donalds.config import Settings
from mcd_donalds.context import RunContext, StageStatus
from mcd_donalds.errors import (
    CheckError,
    DatabaseError,
    ExtractError,
    LoadError,
    PipelineError,
    QualityGateError,
    QueryError,
    TransformError,
)
from mcd_donalds.quality import validar
from mcd_donalds.stages.extract import extrair
from mcd_donalds.stages.load import carregar
from mcd_donalds.stages.transform import transformar


def executar(settings: Settings, ctx: RunContext) -> int:
    """Executa a pipeline completa (checks → extract → transform → quality → load → notify).

    Returns:
        0 se todas as etapas passaram, 1 caso contrario (exit code).
    """
    total_registros = 0

    # ── 1. Checks ──
    ctx.logger.info("=" * 50)
    ctx.logger.info("ETAPA 1/5 — Verificacoes pre-voo")
    ctx.logger.info("=" * 50)
    if not _executar_etapa("checks", lambda: checks.verificar(settings, ctx), settings, ctx):
        _finalizar(ctx, settings, total_registros)
        return 1

    # ── 2. Extract ──
    ctx.logger.info("=" * 50)
    ctx.logger.info("ETAPA 2/5 — Extracao (Selenium)")
    ctx.logger.info("=" * 50)
    xls_paths: list[Path] = []
    def _etapa_extract() -> list[Path]:
        nonlocal xls_paths
        xls_paths = extrair(settings, ctx)
        return xls_paths

    resultado = _executar_etapa("extract", _etapa_extract, settings, ctx,
                                registros=lambda: len(xls_paths))
    if not resultado:
        _finalizar(ctx, settings, total_registros)
        return 1
    if not xls_paths:
        if settings.dry_run:
            ctx.logger.info(
                "[dry-run] Simulacao concluida — nenhum download/carga executado"
            )
            _finalizar(ctx, settings, total_registros)
            return 0
        ctx.logger.warning("Nenhum XLS baixado — pipeline encerrada")
        _finalizar(ctx, settings, total_registros)
        return 1

    # ── 3. Transform ──
    ctx.logger.info("=" * 50)
    ctx.logger.info("ETAPA 3/5 — Transformacao (XLS → CSV)")
    ctx.logger.info("=" * 50)
    csv_paths: list[Path] = []
    def _etapa_transform() -> list[Path]:
        nonlocal csv_paths
        csv_paths = transformar(settings, ctx, xls_paths)
        return csv_paths

    resultado = _executar_etapa("transform", _etapa_transform, settings, ctx,
                                registros=lambda: len(csv_paths))
    if not resultado:
        _finalizar(ctx, settings, total_registros)
        return 1
    if not csv_paths:
        ctx.logger.warning("Nenhum CSV gerado — pipeline encerrada")
        _finalizar(ctx, settings, total_registros)
        return 1

    # ── 4. Quality ──
    ctx.logger.info("=" * 50)
    ctx.logger.info("ETAPA 4/5 — Qualidade (gates)")
    ctx.logger.info("=" * 50)
    for csv_path in csv_paths:
        ctx.logger.info("Validando: %s", csv_path.name)
        try:
            df = pd.read_csv(csv_path, encoding="utf-8-sig")
            relatorio = validar(df, ctx, modo="rigido")
            if not relatorio.valido:
                raise QualityGateError(
                    f"Qualidade reprovada em {csv_path.name}: "
                    f"{len(relatorio.erros)} erro(s)"
                )
        except (QualityGateError, pd.errors.ParserError) as exc:
            ctx.logger.error("Qualidade falhou em %s: %s", csv_path.name, exc)
            ctx.finalizar_etapa("quality", StageStatus.FAILED)
            audit.registrar(ctx, settings, "quality", "failed",
                            produto=csv_path.stem.split("_")[0] if "_" in csv_path.stem else None,
                            erro=exc)
            _finalizar(ctx, settings, total_registros)
            return 1

    ctx.finalizar_etapa("quality", StageStatus.PASSED)
    audit.registrar(ctx, settings, "quality", "passed")
    ctx.logger.info("Qualidade OK — todos os CSVs validados")

    # ── 5. Load ──
    ctx.logger.info("=" * 50)
    ctx.logger.info("ETAPA 5/5 — Carga no banco")
    ctx.logger.info("=" * 50)
    def _etapa_load() -> int:
        nonlocal total_registros
        total_registros = carregar(settings, ctx, csv_paths)
        return total_registros

    resultado = _executar_etapa("load", _etapa_load, settings, ctx,
                                registros=lambda: total_registros)
    if not resultado:
        _finalizar(ctx, settings, total_registros)
        return 1

    _limpar_antigos(settings, ctx)

    ctx.logger.info("Pipeline concluida com sucesso — %d registros", total_registros)
    _finalizar(ctx, settings, total_registros)
    return 0


# ── helpers ──


def _limpar_antigos(settings: Settings, ctx: RunContext) -> None:
    """Apaga artefatos com mais de settings.retencao_dias dias.

    So roda depois de uma carga bem-sucedida, e nunca alcanca os arquivos desta
    execucao (acabaram de ser escritos, mtime = agora). Falha aqui e nao-fatal:
    o dado ja esta no banco, disco cheio nao pode invalidar a carga.
    """
    if settings.retencao_dias <= 0:
        return

    limite = time.time() - settings.retencao_dias * 86_400
    removidos = 0
    for diretorio in (settings.xlsx_dir, settings.csv_dir, settings.logs_dir):
        for arquivo in diretorio.glob("*"):
            try:
                if arquivo.is_file() and arquivo.stat().st_mtime < limite:
                    arquivo.unlink()
                    removidos += 1
            except OSError as exc:
                ctx.logger.warning("Nao foi possivel remover %s: %s", arquivo, exc)

    ctx.logger.info(
        "Retencao (%d dias): %d arquivo(s) antigo(s) removido(s)",
        settings.retencao_dias, removidos,
    )


def _executar_etapa(
    nome: str,
    fn: Callable[[], object],
    settings: Settings,
    ctx: RunContext,
    registros: Callable[[], int] | None = None,
) -> bool:
    """Executa uma etapa e registra auditoria.

    Returns:
        True se passou, False se falhou.
    """
    try:
        fn()
        status = ctx.stage_status.get(nome, StageStatus.PASSED)
        qtd = registros() if registros else 0
        audit.registrar(ctx, settings, nome, status.value, registros=qtd)
        return status in (StageStatus.PASSED, StageStatus.SKIPPED)
    except (CheckError, ExtractError, TransformError,
            QualityGateError, LoadError, QueryError,
            DatabaseError, PipelineError, OSError) as exc:
        ctx.logger.error("Etapa '%s' falhou: %s", nome, exc)
        audit.registrar(ctx, settings, nome, "failed", erro=exc)
        return False
    except Exception as exc:
        ctx.logger.error("Etapa '%s' falhou com erro inesperado: %s", nome, exc)
        # Passa a excecao original: reconstruir com type(exc)(str(exc)) estoura
        # em excecoes cujo __init__ exige mais de um argumento.
        audit.registrar(ctx, settings, nome, "failed", erro=exc)
        return False


def _finalizar(
    ctx: RunContext,
    settings: Settings,
    total_registros: int,
) -> None:
    """Notifica resultado e loga resumo final."""
    notify.notificar(ctx, settings, total_registros=total_registros)
    etapas_ok = [e for e, s in ctx.stage_status.items() if s.value == "passed"]
    etapas_falha = [e for e, s in ctx.stage_status.items() if s.value == "failed"]
    ctx.logger.info(
        "Pipeline finalizada — etapas OK: %d, falhas: %d, duracao: %.1fs",
        len(etapas_ok), len(etapas_falha), ctx.duracao_segundos,
    )
