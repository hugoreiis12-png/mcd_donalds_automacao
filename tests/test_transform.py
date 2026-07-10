"""Testes para a etapa de transformacao em mcd_donalds/stages/transform.py.

Cobre: _extrair_produto, _ler_xls, _normalizar_colunas, _ajustar_tipos,
_salvar_csv e transformar (fluxo completo com fixture XLS inline).
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pandas as pd
import pytest
from openpyxl import Workbook

from mcd_donalds.config import Settings
from mcd_donalds.context import RunContext
from mcd_donalds.stages.transform import (
    _ajustar_tipos,
    _extrair_produto,
    _ler_xls,
    _normalizar_colunas,
    _salvar_csv_canonico,
    _salvar_csv_original,
    _salvar_xlsx_limpo,
    transformar,
)


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
def xls_path(tmp_base: str) -> Generator[str, None, None]:
    path = os.path.join(tmp_base, "QC_reclamacao.xlsx")
    wb = Workbook()
    ws = wb.active
    ws.append([
        "Nº OC/AD", "Tipo Doctº", "Nome Doctº", "Status", "Data Criação",
        "Data Última Atualização", "Restaurante", "Cidade", "Estado",
        "Nome Contato", "CD", "Filial", "Nome Ocorrência", "Motivo",
        "Cód. Produto", "Descr. Produto", "Qtde Faturada", "Qtde Reclamada",
        "Qtde Autorizada", "Unidade", "Fabricante", "Lote", "Dt. Fabricação",
        "Dt. Vencimento", "Conclusão Fornecedor", "Destino da Mercadoria",
        "Observações ao Cliente", "Laudo",
    ])
    ws.append([
        "1602696", "QC", "Ocorrencias", "Finalizada", "01/01/2026",
        "02/01/2026", "PAN", "Sao Paulo", "SP",
        "Irienis", "FT", "FT", "Recl.Produto - Qualidade", "Coloracao alterada",
        "00018-001", "TIRAS DE ALFACE AMERICANA-12KG", "11", "2", "1",
        "CX", "JFC E NATURAL SALADS", "15516", "27/12/2025", "05/01/2026",
        "AUTORIZACAO SO NF", "DEBITAR FORNECEDOR",
        "produto com coloracao fora do padrao.",
        "Identificamos que a oxidacao...",
    ])
    wb.save(path)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


# ── _extrair_produto ──


def test_extrair_produto_padrao() -> None:
    assert _extrair_produto(Path("QC_reclamacao.xlsx")) == "QC"


def test_extrair_produto_case_insensitive() -> None:
    assert _extrair_produto(Path("q1_reclamacao.xlsx")) == "Q1"


def test_extrair_produto_fallback() -> None:
    assert _extrair_produto(Path("QR_xyz.xlsx")) == "QR"


def test_extrair_produto_invalido() -> None:
    assert _extrair_produto(Path("_.xlsx")) is None


# ── _ler_xls ──


def test_ler_xls_valido(xls_path: str) -> None:
    df = _ler_xls(Path(xls_path))
    assert not df.empty
    assert "Nº OC/AD" in df.columns
    assert "Status" in df.columns
    assert len(df) == 1


def test_ler_xls_inexistente() -> None:
    from mcd_donalds.errors import ParseError
    with pytest.raises(ParseError):
        _ler_xls(Path("nao_existe.xlsx"))


# ── _normalizar_colunas ──


def test_normalizar_mapa_exato() -> None:
    df = pd.DataFrame([{"Status": "Finalizada", "Restaurante": "PAN"}])
    resultado = _normalizar_colunas(df)
    assert "status" in resultado.columns
    assert "restaurante" in resultado.columns


def test_normalizar_heuristico() -> None:
    df = pd.DataFrame([{"status": "Finalizada", "cidade": "Sao Paulo"}])
    resultado = _normalizar_colunas(df)
    assert "status" in resultado.columns
    assert "cidade_restaurante" in resultado.columns


def test_normalizar_sem_match() -> None:
    df = pd.DataFrame([{"coluna_estranha": "x"}])
    resultado = _normalizar_colunas(df)
    assert resultado.empty


def test_normalizar_misto() -> None:
    df = pd.DataFrame([{"Status": "Finalizada", "motivo": "Coloracao", "cod_produto": "001"}])
    resultado = _normalizar_colunas(df)
    assert "status" in resultado.columns
    assert "motivo" in resultado.columns
    assert "cod_produto" in resultado.columns


# ── _ajustar_tipos ──


def test_ajustar_datas() -> None:
    df = pd.DataFrame({
        "dt_criacao": ["01/01/2026", "15/12/2025", ""],
        "dt_vencimento": ["05/01/2026", "", "2026-01-10"],
    })
    resultado = _ajustar_tipos(df)
    assert resultado["dt_criacao"].tolist() == ["2026-01-01", "2025-12-15", ""]
    assert resultado["dt_vencimento"].tolist() == ["2026-01-05", "", "2026-01-10"]


def test_ajustar_int_colunas() -> None:
    df = pd.DataFrame({
        "n_oc_ad": ["1602696", ""],
        "qtde_faturada": ["10", "abc"],
        "qtde_reclamada": ["2", ""],
        "qtde_autorizada": ["1", "0"],
    })
    resultado = _ajustar_tipos(df)
    assert resultado["n_oc_ad"].tolist() == [1602696, 0]
    assert resultado["qtde_faturada"].tolist() == [10, 0]
    assert resultado["qtde_reclamada"].tolist() == [2, 0]
    assert resultado["qtde_autorizada"].tolist() == [1, 0]
    assert resultado["n_oc_ad"].dtype == int


def test_ajustar_colunas_ausentes() -> None:
    df = pd.DataFrame({"outra": ["x"]})
    resultado = _ajustar_tipos(df)
    assert "outra" in resultado.columns


# ── _salvar_csv ──


def test_salvar_csv_canonico(settings: Settings) -> None:
    settings.csv_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({"n_oc_ad": [1602696], "status": ["Finalizada"]})
    xls_path = settings.base_dir / "QC_reclamacao.xlsx"
    csv_path = _salvar_csv_canonico(df, xls_path, settings)
    assert csv_path.exists()
    assert csv_path.name == "QC_reclamacao_db.csv"
    lido = pd.read_csv(csv_path)
    assert len(lido) == 1
    assert lido["n_oc_ad"].iloc[0] == 1602696


def test_salvar_csv_original(settings: Settings) -> None:
    settings.csv_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({"Nº OC/AD": [1602696], "Status": ["Finalizada"]})
    csv_path = settings.csv_dir / "QC_reclamacao.csv"
    _salvar_csv_original(df, csv_path)
    assert csv_path.exists()
    lido = pd.read_csv(csv_path, sep=";", encoding="utf-8-sig")
    assert len(lido) == 1
    assert "Nº OC/AD" in lido.columns


def test_salvar_xlsx_limpo(settings: Settings, ctx: RunContext) -> None:
    xlsx_dir = settings.base_dir / "xlsx"
    xlsx_dir.mkdir(parents=True, exist_ok=True)
    xls_path = xlsx_dir / "QC_reclamacao.xlsx"
    df = pd.DataFrame({"Status": ["Finalizada"]})
    _salvar_xlsx_limpo(df, xls_path, settings, ctx)
    assert xls_path.exists()
    lido = pd.read_excel(xls_path, engine="openpyxl")
    assert "Status" in lido.columns


# ── transformar (fluxo completo) ──


def test_transformar_com_xls(settings: Settings, ctx: RunContext, xls_path: str) -> None:
    csvs = transformar(settings, ctx, [Path(xls_path)])
    assert len(csvs) == 1
    csv_path = csvs[0]
    assert csv_path.exists()
    assert csv_path.name.endswith("_db.csv")
    lido = pd.read_csv(csv_path)
    assert not lido.empty
    assert "n_oc_ad" in lido.columns
    assert lido["n_oc_ad"].iloc[0] == 1602696


def test_transformar_sem_xls(settings: Settings, ctx: RunContext) -> None:
    csvs = transformar(settings, ctx, [])
    assert csvs == []
