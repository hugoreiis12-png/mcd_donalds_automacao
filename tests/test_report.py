"""Testes para o modulo report.py.

Testa resumo_por_status, resumo_por_motivo, exportar_csv, exportar_excel
com DataFrames mockados — sem dependencia de banco de dados.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator

import pandas as pd
import pytest

from mcd_donalds.report import (
    exportar_csv,
    exportar_excel,
    resumo_por_motivo,
    resumo_por_status,
)


# ── fixtures ──


@pytest.fixture
def df_base() -> pd.DataFrame:
    return pd.DataFrame({
        "n_oc_ad": [1602696, 1602699, 1602700, 1602703],
        "tipo_doc": ["QC", "QC", "QC", "QC"],
        "status": ["Finalizada", "Nao procedente NF", "Finalizada", "Finalizada"],
        "motivo": [
            "Coloracao alterada",
            "Consistencia fora do padrao",
            "Produto fora de temperatura",
            "Produto fora de temperatura",
        ],
        "restaurante": ["PAN", "BGU", "SPV", "SPV"],
    })


@pytest.fixture
def tmp_path() -> Generator[str, None, None]:
    with tempfile.TemporaryDirectory() as d:
        yield d


# ── resumo_por_status ──


def test_resumo_por_status_com_dados(df_base: pd.DataFrame) -> None:
    resumo = resumo_por_status(df_base)
    assert not resumo.empty
    assert list(resumo.columns) == ["status", "quantidade"]
    assert len(resumo) == 2
    finalizada = resumo[resumo["status"] == "Finalizada"].iloc[0]
    assert finalizada["quantidade"] == 3


def test_resumo_por_status_vazio() -> None:
    resumo = resumo_por_status(pd.DataFrame())
    assert resumo.empty


def test_resumo_por_status_sem_coluna() -> None:
    df = pd.DataFrame({"outra": ["x"]})
    resumo = resumo_por_status(df)
    assert resumo.empty


# ── resumo_por_motivo ──


def test_resumo_por_motivo_com_dados(df_base: pd.DataFrame) -> None:
    resumo = resumo_por_motivo(df_base)
    assert not resumo.empty
    assert "motivo" in resumo.columns
    assert "quantidade" in resumo.columns
    temp = resumo[resumo["motivo"] == "Produto fora de temperatura"].iloc[0]
    assert temp["quantidade"] == 2


def test_resumo_por_motivo_vazio() -> None:
    resumo = resumo_por_motivo(pd.DataFrame())
    assert resumo.empty


# ── exportar_csv ──


def test_exportar_csv(df_base: pd.DataFrame, tmp_path: str) -> None:
    caminho = os.path.join(tmp_path, "teste.csv")
    resultado = exportar_csv(df_base, caminho)
    assert os.path.isfile(resultado)
    lido = pd.read_csv(resultado)
    assert len(lido) == 4


def test_exportar_csv_dataframe_vazio(tmp_path: str) -> None:
    caminho = os.path.join(tmp_path, "vazio.csv")
    resultado = exportar_csv(pd.DataFrame(), caminho)
    assert os.path.isfile(resultado)


# ── exportar_excel ──


@pytest.mark.skipif(
    not os.environ.get("OPENPYXL_TEST"),
    reason="OPENPYXL_TEST nao definido; exportar_excel requer openpyxl instalado",
)
def test_exportar_excel(df_base: pd.DataFrame, tmp_path: str) -> None:
    caminho = os.path.join(tmp_path, "teste.xlsx")
    resultado = exportar_excel(df_base, caminho)
    assert os.path.isfile(resultado)
    lido = pd.read_excel(resultado, engine="openpyxl")
    assert len(lido) == 4
