"""Testes para o modelo de dominio em mcd_donalds/model.py.

Cobre: Reclamacao DTO, COLUNAS_DB, MAPA_ORIGEM, normalizar(), normalizar_data().
"""

from __future__ import annotations

import pandas as pd
import pytest
from pydantic import ValidationError

from mcd_donalds.model import (
    COLUNAS_DB,
    COLUNAS_DATA,
    COLUNAS_INT,
    MAPA_ORIGEM,
    Reclamacao,
    normalizar,
    normalizar_data,
    normalizar_inteiro,
)


# ── Reclamacao DTO ──


def test_reclamacao_campos_ordem() -> None:
    campos = list(Reclamacao.model_fields.keys())
    assert campos == list(COLUNAS_DB)


def test_reclamacao_tipos() -> None:
    fields = Reclamacao.model_fields
    assert fields["n_oc_ad"].annotation is int
    assert fields["tipo_doc"].annotation is str
    assert fields["dt_criacao"].annotation is str
    assert fields["qtde_faturada"].annotation is int
    assert fields["observacao"].annotation is str


def test_reclamacao_all_required() -> None:
    with pytest.raises(ValidationError):
        Reclamacao()  # type: ignore[call-arg]


def test_reclamacao_valid() -> None:
    r = Reclamacao(
        n_oc_ad=1602696,
        tipo_doc="QC",
        nome_doc="Ocorrencias",
        status="Finalizada",
        dt_criacao="2026-01-01",
        dt_ultima_atualizacao="2026-01-02",
        restaurante="PAN",
        cidade_restaurante="Sao Paulo",
        estado_restaurante="SP",
        nome_contato="Irienis",
        cd="FT",
        filial="FT",
        nome_ocorrencia="Recl.Produto - Qualidade",
        motivo="Coloracao alterada",
        cod_produto="00018-001",
        desc_produto="TIRAS DE ALFACE AMERICANA-12KG",
        qtde_faturada=11,
        qtde_reclamada=2,
        qtde_autorizada=1,
        unidade="CX",
        fabricante="JFC E NATURAL SALADS",
        lote="15516",
        dt_fabricacao="2025-12-27",
        dt_vencimento="2026-01-05",
        conclucao="AUTORIZACAO SO NF",
        destino_mercadoria="DEBITAR FORNECEDOR",
        observacao="produto com coloracao fora do padrao",
        laudo="Identificamos que a oxidacao...",
    )
    assert r.n_oc_ad == 1602696
    assert r.tipo_doc == "QC"
    assert r.status == "Finalizada"
    assert r.dt_criacao == "2026-01-01"


# ── COLUNAS_DB ──


def test_colunas_db_ordem() -> None:
    assert len(COLUNAS_DB) == 28
    assert COLUNAS_DB[0] == "n_oc_ad"
    assert COLUNAS_DB[-1] == "laudo"


def test_colunas_db_conteudo() -> None:
    assert "n_oc_ad" in COLUNAS_DB
    assert "status" in COLUNAS_DB
    assert "restaurante" in COLUNAS_DB
    assert "cod_produto" in COLUNAS_DB
    assert "observacao" in COLUNAS_DB


# ── COLUNAS_DATA ──


def test_colunas_data() -> None:
    assert "dt_criacao" in COLUNAS_DATA
    assert "dt_vencimento" in COLUNAS_DATA
    assert len(COLUNAS_DATA) == 4


# ── COLUNAS_INT ──


def test_colunas_int() -> None:
    assert "n_oc_ad" in COLUNAS_INT
    assert "qtde_faturada" in COLUNAS_INT
    assert len(COLUNAS_INT) == 4


# ── MAPA_ORIGEM ──


def test_mapa_origem_chaves() -> None:
    chaves_esperadas = {
        "Nº OC/AD", "Tipo Doctº", "Nome Doctº", "Status",
        "Data Criação", "Data Última Atualização", "Restaurante",
        "Cidade", "Estado", "Nome Contato", "CD", "Filial",
        "Nome Ocorrência", "Motivo", "Cód. Produto", "Descr. Produto",
        "Qtde Faturada", "Qtde Reclamada", "Qtde Autorizada",
        "Unidade", "Fabricante", "Lote", "Dt. Fabricação",
        "Dt. Vencimento", "Conclusão Fornecedor",
        "Destino da Mercadoria", "Observações ao Cliente", "Laudo",
    }
    assert set(MAPA_ORIGEM.keys()) == chaves_esperadas


def test_mapa_origem_valores() -> None:
    for valor in MAPA_ORIGEM.values():
        assert valor in COLUNAS_DB, f"{valor} nao esta em COLUNAS_DB"


# ── normalizar_data ──


def test_normalizar_data_br_para_iso() -> None:
    serie = pd.Series(["01/01/2026", "15/12/2025", ""])
    resultado = normalizar_data(serie)
    assert resultado.tolist() == ["2026-01-01", "2025-12-15", ""]


def test_normalizar_data_iso_mantido() -> None:
    serie = pd.Series(["2026-01-01", "2025-12-15"])
    resultado = normalizar_data(serie)
    assert resultado.tolist() == ["2026-01-01", "2025-12-15"]


def test_normalizar_data_nan() -> None:
    serie = pd.Series([None, "01/01/2026"])
    resultado = normalizar_data(serie)
    assert resultado.tolist() == ["", "2026-01-01"]


# ── normalizar_inteiro ──


def test_normalizar_inteiro_valido() -> None:
    serie = pd.Series(["10", "5", "0"])
    resultado = normalizar_inteiro(serie)
    assert resultado.tolist() == [10, 5, 0]


def test_normalizar_inteiro_invalido() -> None:
    serie = pd.Series(["10", "abc", "", None])
    resultado = normalizar_inteiro(serie)
    assert resultado.tolist() == [10, 0, 0, 0]


# ── normalizar ──


def test_normalizar_colunas_validas() -> None:
    df = pd.DataFrame([{
        "Nº OC/AD": "1602696",
        "Tipo Doctº": "QC",
        "Nome Doctº": "Ocorrencias",
        "Status": "Finalizada",
        "Data Criação": "01/01/2026",
        "Data Última Atualização": "02/01/2026",
        "Restaurante": "PAN",
        "Cidade": "Sao Paulo",
        "Estado": "SP",
        "Nome Contato": "Irienis",
        "CD": "FT",
        "Filial": "FT",
        "Nome Ocorrência": "Recl.Produto",
        "Motivo": "Coloracao",
        "Cód. Produto": "00018-001",
        "Descr. Produto": "ALFACE",
        "Qtde Faturada": "11",
        "Qtde Reclamada": "2",
        "Qtde Autorizada": "1",
        "Unidade": "CX",
        "Fabricante": "JFC",
        "Lote": "15516",
        "Dt. Fabricação": "27/12/2025",
        "Dt. Vencimento": "05/01/2026",
        "Conclusão Fornecedor": "AUTORIZACAO SO NF",
        "Destino da Mercadoria": "DEBITAR FORNECEDOR",
        "Observações ao Cliente": "obs",
        "Laudo": "laudo",
    }])
    resultado = normalizar(df)
    assert list(resultado.columns) == list(COLUNAS_DB)
    assert len(resultado) == 1
    assert resultado.iloc[0]["n_oc_ad"] == "1602696"
    assert resultado.iloc[0]["status"] == "Finalizada"
    assert resultado.iloc[0]["restaurante"] == "PAN"


def test_normalizar_coluna_ausente() -> None:
    df = pd.DataFrame([{"Status": "Finalizada"}])
    with pytest.raises(KeyError):
        normalizar(df)


def test_normalizar_df_vazio() -> None:
    dados: dict[str, list[object]] = {k: [] for k in MAPA_ORIGEM}
    df = pd.DataFrame(dados)
    resultado = normalizar(df)
    assert list(resultado.columns) == list(COLUNAS_DB)
    assert len(resultado) == 0
