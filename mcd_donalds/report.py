"""Relatorios: consulta dados carregados e gera sumarios/exportacoes.

Uso como biblioteca:
    from mcd_donalds.report import consultar
    df = consultar(settings, restaurante="PAN", ano=2026)

Uso como CLI:
    mcd-donalds-report --restaurante PAN --ano 2026 --formato csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from mcd_donalds.config import Settings
from mcd_donalds.db import Database
from mcd_donalds.errors import DBConnectionError, QueryError


def consultar(
    settings: Settings,
    n_oc_ad: int | None = None,
    tipo_doc: str | None = None,
    restaurante: str | None = None,
    dt_inicio: str | None = None,
    dt_fim: str | None = None,
) -> pd.DataFrame:
    """Consulta dados da tabela mcd_reclamacao com filtros opcionais.

    Args:
        settings: Configuracao (fornece db, tabela).
        n_oc_ad: Filtrar por numero da OC/AD.
        tipo_doc: Filtrar por tipo de documento ("QC", "Q1", etc).
        restaurante: Filtrar por restaurante (ex: "PAN").
        dt_inicio: Data inicio (YYYY-MM-DD) para dt_criacao.
        dt_fim: Data fim (YYYY-MM-DD) para dt_criacao.

    Returns:
        DataFrame com os registros encontrados.
    """
    tabela = settings.db.tabela_completa
    sql = f"SELECT * FROM {tabela} WHERE 1=1"
    params: list[object] = []

    if n_oc_ad is not None:
        sql += " AND n_oc_ad = %s"
        params.append(n_oc_ad)
    if tipo_doc:
        sql += " AND tipo_doc = %s"
        params.append(tipo_doc)
    if restaurante:
        sql += " AND restaurante = %s"
        params.append(restaurante)
    if dt_inicio:
        sql += " AND dt_criacao >= %s"
        params.append(dt_inicio)
    if dt_fim:
        sql += " AND dt_criacao <= %s"
        params.append(dt_fim)

    sql += " ORDER BY dt_criacao DESC, n_oc_ad DESC"

    db = Database(settings.db)
    try:
        db.connect()
        with db.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame.from_records(rows)
        return df
    except Exception as exc:
        raise QueryError(f"Falha ao consultar dados: {exc}") from exc
    finally:
        db.close()


def resumo_por_status(df: pd.DataFrame) -> pd.DataFrame:
    """Gera contagem de reclamacoes agrupadas por status."""
    if df.empty or "status" not in df.columns:
        return pd.DataFrame()
    return (
        df.groupby("status", as_index=False)
        .size()
        .rename(columns={"size": "quantidade"})
        .sort_values("quantidade", ascending=False)
    )


def resumo_por_motivo(df: pd.DataFrame) -> pd.DataFrame:
    """Gera contagem de reclamacoes agrupadas por motivo."""
    if df.empty or "motivo" not in df.columns:
        return pd.DataFrame()
    return (
        df.groupby("motivo", as_index=False)
        .size()
        .rename(columns={"size": "quantidade"})
        .sort_values("quantidade", ascending=False)
    )


def exportar_csv(df: pd.DataFrame, path: str | Path) -> Path:
    """Salva DataFrame como CSV.

    Args:
        df: DataFrame a exportar.
        path: Caminho de destino.

    Returns:
        Path absoluto do arquivo gerado.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8")
    return path.resolve()


def exportar_excel(df: pd.DataFrame, path: str | Path) -> Path:
    """Salva DataFrame como Excel (.xlsx).

    Args:
        df: DataFrame a exportar.
        path: Caminho de destino.

    Returns:
        Path absoluto do arquivo gerado.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Dados")
    return path.resolve()


# ── helpers ──


def _coluna_distinta(
    settings: Settings, db: Database | None, coluna: str
) -> list[Any]:
    """Retorna valores distintos de uma coluna da tabela."""
    fechar_db = db is None
    if db is None:
        db = Database(settings.db)

    tabela = settings.db.tabela_completa
    sql = f"SELECT DISTINCT {coluna} FROM {tabela} ORDER BY {coluna}"

    try:
        db.connect()
        with db.cursor() as cur:
            cur.execute(sql)
            return [row[coluna] for row in cur.fetchall()]
    except Exception as exc:
        raise QueryError(
            f"Falha ao listar {coluna}: {exc}"
        ) from exc
    finally:
        if fechar_db:
            db.close()


def main() -> None:
    """Entry point CLI para geracao de relatorios."""
    parser = argparse.ArgumentParser(
        prog="mcd-donalds-report",
        description="Relatorios de reclamacoes McDonald's (Martin Brower)",
    )
    parser.add_argument("--n-oc-ad", type=int, default=None, help="Filtrar por Nº OC/AD")
    parser.add_argument("--tipo-doc", type=str, default=None, help="Filtrar por tipo de documento")
    parser.add_argument("--restaurante", type=str, default=None, help="Filtrar por restaurante")
    parser.add_argument("--dt-inicio", type=str, default=None, help="Data inicio (YYYY-MM-DD)")
    parser.add_argument("--dt-fim", type=str, default=None, help="Data fim (YYYY-MM-DD)")
    parser.add_argument(
        "--formato", type=str, default="console",
        choices=["console", "csv", "excel"],
        help="Formato de saida (default: console)",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Caminho do arquivo de saida (obrigatorio se formato=csv ou excel)",
    )
    parser.add_argument("--env", type=str, default=".env", help="Caminho do .env")

    args = parser.parse_args()
    settings = Settings.load(args.env)

    if args.formato in ("csv", "excel") and not args.output:
        parser.error("--output e obrigatorio quando formato=csv ou excel")

    try:
        df = consultar(
            settings,
            n_oc_ad=args.n_oc_ad,
            tipo_doc=args.tipo_doc,
            restaurante=args.restaurante,
            dt_inicio=args.dt_inicio,
            dt_fim=args.dt_fim,
        )
    except (QueryError, DBConnectionError) as exc:
        print(f"Erro ao consultar dados: {exc}", file=sys.stderr)
        sys.exit(1)

    if df.empty:
        print("Nenhum registro encontrado para os filtros informados.")
        sys.exit(0)

    if args.formato == "console":
        colunas_exibir = [c for c in df.columns if c in (
            "n_oc_ad", "tipo_doc", "status", "dt_criacao",
            "restaurante", "cidade_restaurante", "motivo",
            "desc_produto", "qtde_reclamada",
        )]
        exibir = df[colunas_exibir] if colunas_exibir else df
        print(exibir.to_string(index=False))
    elif args.formato == "csv":
        caminho = exportar_csv(df, args.output)
        print(f"Arquivo salvo: {caminho}")
    elif args.formato == "excel":
        caminho = exportar_excel(df, args.output)
        print(f"Arquivo salvo: {caminho}")

    sys.exit(0)


if __name__ == "__main__":
    main()
