"""Etapa de carga: le CSV normalizado e insere no PostgreSQL.

Para cada CSV em dados/csv/:
    1. Le o CSV com pandas para extrair periodo (min/max dt_criacao)
    2. DELETE FROM mcd_reclamacao WHERE dt_criacao BETWEEN periodo
    3. COPY dados FROM STDIN (bulk insert)
    4. Commit ao final; rollback em qualquer erro
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2 import sql

from mcd_donalds.config import Settings
from mcd_donalds.context import RunContext, StageStatus
from mcd_donalds.db import Database
from mcd_donalds.errors import LoadError, QueryError
from mcd_donalds.model import COLUNAS_DB


def carregar(
    settings: Settings,
    ctx: RunContext,
    arquivos_csv: list[Path] | None = None,
) -> int:
    """Carrega CSVs normalizados na tabela mcd_reclamacao.

    Args:
        settings: Configuracao da pipeline.
        ctx: Contexto de execucao.
        arquivos_csv: Lista de paths CSV. Se None, varre settings.csv_dir.

    Returns:
        Total de registros inseridos.
    """
    ctx.iniciar_etapa("load")

    csvs = arquivos_csv or _listar_csv(settings)
    if not csvs:
        ctx.logger.warning("Nenhum CSV encontrado em %s", settings.csv_dir)
        ctx.finalizar_etapa("load", StageStatus.SKIPPED)
        return 0

    db = Database(settings.db)
    total_inseridos = 0

    try:
        db.connect()
        for csv_path in csvs:
            inseridos = _carregar_csv(db, csv_path, settings, ctx)
            total_inseridos += inseridos
        db.commit()
    except (psycopg2.Error, LoadError, QueryError) as exc:
        ctx.logger.error("Erro na carga — executando rollback: %s", exc)
        db.rollback()
        ctx.finalizar_etapa("load", StageStatus.FAILED)
        raise
    else:
        ctx.logger.info("Carga concluida: %d registros inseridos", total_inseridos)
        ctx.finalizar_etapa("load", StageStatus.PASSED)
        return total_inseridos
    finally:
        db.close()


# ── helpers ──


def _listar_csv(settings: Settings) -> list[Path]:
    """Retorna todos os CSVs canonicos do diretorio de transformacao."""
    return sorted(settings.csv_dir.glob("*_db.csv"))


def _extrair_metadados(path: Path) -> str | None:
    """Extrai tipo_doc do nome do arquivo.

    Formatos esperados:
      - TIPO_DOC_reclamacao_db.csv     (ex: QC_reclamacao_db.csv)  → tipo
      - exportarDados(NNN)_db.csv      (ex: exportarDados(001)_db.csv) → GERAL
    """
    stem = path.stem.upper().replace("_DB", "")
    stem = re.sub(r"\(\d+\)$", "", stem).strip()
    match = re.match(r"^([A-Z0-9]+)_RECLAMACAO$", stem)
    if match:
        return match.group(1)
    if stem == "EXPORTARDADOS":
        return "GERAL"
    return None


def _carregar_csv(
    db: Database,
    csv_path: Path,
    settings: Settings,
    ctx: RunContext,
) -> int:
    """Executa DELETE (por periodo) + COPY para um unico CSV."""
    tipo_doc = _extrair_metadados(csv_path)
    if tipo_doc is None:
        ctx.logger.warning(
            "Nome de arquivo invalido (esperado *_db.csv): %s — pulando",
            csv_path.name,
        )
        return 0

    ctx.logger.info(
        "Carregando: %s (tipo_doc=%s)",
        csv_path.name, tipo_doc,
    )

    # 1. Ler periodo do CSV (dt_criacao min/max)
    try:
        df = pd.read_csv(csv_path, encoding="utf-8-sig")
    except Exception as exc:
        raise LoadError(f"Falha ao ler {csv_path.name}: {exc}") from exc

    if df.empty:
        ctx.logger.warning("CSV vazio: %s — pulando", csv_path.name)
        return 0

    with db.cursor() as cur:
        # 2. DELETE registros do periodo
        _deletar_por_periodo(cur, settings, df, ctx)

        # 3. COPY bulk insert
        inseridos = _copiar_csv(cur, csv_path, settings, ctx)

    return inseridos


def _deletar_por_periodo(
    cur: psycopg2.extensions.cursor,
    settings: Settings,
    df: pd.DataFrame,
    ctx: RunContext,
) -> None:
    """Remove registros do mesmo periodo (dt_criacao) — carga idempotente."""
    dt_min = df["dt_criacao"].dropna().min()
    dt_max = df["dt_criacao"].dropna().max()

    if pd.isna(dt_min) or pd.isna(dt_max):
        ctx.logger.warning("  Sem dt_criacao valida no CSV — pulando DELETE")
        return

    tabela = sql.Identifier(settings.db.db_schema, settings.db.tabela)
    query = sql.SQL(
        "DELETE FROM {} WHERE dt_criacao BETWEEN %s AND %s"
    ).format(tabela)

    try:
        cur.execute(query, (dt_min, dt_max))
        removidos = cur.rowcount
        ctx.logger.info("  Registros removidos (periodo %s a %s): %d", dt_min, dt_max, removidos)
    except psycopg2.Error as exc:
        raise QueryError(
            f"Falha ao deletar registros do periodo {dt_min} a {dt_max}: {exc}"
        ) from exc


def _copiar_csv(
    cur: psycopg2.extensions.cursor,
    csv_path: Path,
    settings: Settings,
    ctx: RunContext,
) -> int:
    """Faz COPY do CSV para a tabela via psycopg2 copy_expert."""
    colunas = ", ".join(COLUNAS_DB)
    tabela = f"{settings.db.db_schema}.{settings.db.tabela}"
    sql_copy = f"COPY {tabela} ({colunas}) FROM STDIN WITH CSV HEADER"

    try:
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            cur.copy_expert(sql_copy, f)
        inseridos: int = cur.rowcount or 0
        ctx.logger.info("  Registros inseridos: %d", inseridos)
        return inseridos
    except (psycopg2.Error, OSError) as exc:
        raise LoadError(
            f"Falha ao copiar {csv_path.name} para {tabela}: {exc}"
        ) from exc
