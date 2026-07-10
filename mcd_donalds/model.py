"""Modelo de dominio da reclamacao McDonald's: a unica fonte da verdade
para as colunas que atravessam a pipeline.

O DTO Reclamacao espelha 1:1 as colunas da tabela public.mcd_reclamacao.
Dele derivam COLUNAS_DB (ordem do COPY) e o mapeamento dos rotulos do
relatorio exportado (portugues com acento) para os nomes canonicos do banco.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    import pandas as pd


class Reclamacao(BaseModel):
    """DTO tipado que espelha as colunas de public.mcd_reclamacao.

    A ordem dos campos = ordem das colunas no banco (usada no COPY).
    Datas trafegam como str ISO 'YYYY-MM-DD' ("" = nulo); quantidades como
    int; identificadores/textos como str. O campo 'conclucao' preserva o
    typo existente na coluna real do banco (sem o 's' de 'conclusao').
    """
    n_oc_ad: int
    tipo_doc: str
    nome_doc: str
    status: str
    dt_criacao: str
    dt_ultima_atualizacao: str
    restaurante: str
    cidade_restaurante: str
    estado_restaurante: str
    nome_contato: str
    cd: str
    filial: str
    nome_ocorrencia: str
    motivo: str
    cod_produto: str
    desc_produto: str
    qtde_faturada: int
    qtde_reclamada: int
    qtde_autorizada: int
    unidade: str
    fabricante: str
    lote: str
    dt_fabricacao: str
    dt_vencimento: str
    conclucao: str
    destino_mercadoria: str
    observacao: str
    laudo: str


# Colunas canonicas na ordem do banco, DERIVADAS do DTO (sem duplicar).
COLUNAS_DB: tuple[str, ...] = tuple(Reclamacao.model_fields)

# Chave da janela de idempotencia: o replace do periodo e feito por dt_criacao
# (DELETE ... WHERE dt_criacao BETWEEN inicio AND fim -> COPY do mesmo periodo).
# n_oc_ad NAO e unico (o mesmo numero aparece com tipos de documento diferentes).
COL_CHAVE_PERIODO = "dt_criacao"

# Campos de data (formato BR DD/MM/YYYY na origem -> ISO YYYY-MM-DD no banco).
COLUNAS_DATA: tuple[str, ...] = (
    "dt_criacao", "dt_ultima_atualizacao", "dt_fabricacao", "dt_vencimento",
)

# Campos numericos inteiros.
COLUNAS_INT: tuple[str, ...] = (
    "n_oc_ad", "qtde_faturada", "qtde_reclamada", "qtde_autorizada",
)

# Mapa dos rotulos do relatorio exportado (cabecalho do Excel/CSV, com acento)
# para os nomes canonicos do banco. Fonte unica para o transform renomear.
MAPA_ORIGEM: dict[str, str] = {
    "Nº OC/AD": "n_oc_ad",
    "Tipo Doctº": "tipo_doc",
    "Nome Doctº": "nome_doc",
    "Status": "status",
    "Data Criação": "dt_criacao",
    "Data Última Atualização": "dt_ultima_atualizacao",
    "Restaurante": "restaurante",
    "Cidade": "cidade_restaurante",
    "Estado": "estado_restaurante",
    "Nome Contato": "nome_contato",
    "CD": "cd",
    "Filial": "filial",
    "Nome Ocorrência": "nome_ocorrencia",
    "Motivo": "motivo",
    "Cód. Produto": "cod_produto",
    "Descr. Produto": "desc_produto",
    "Qtde Faturada": "qtde_faturada",
    "Qtde Reclamada": "qtde_reclamada",
    "Qtde Autorizada": "qtde_autorizada",
    "Unidade": "unidade",
    "Fabricante": "fabricante",
    "Lote": "lote",
    "Dt. Fabricação": "dt_fabricacao",
    "Dt. Vencimento": "dt_vencimento",
    "Conclusão Fornecedor": "conclucao",
    "Destino da Mercadoria": "destino_mercadoria",
    "Observações ao Cliente": "observacao",
    "Laudo": "laudo",
}


def colunas_db() -> tuple[str, ...]:
    """Lista ordenada de colunas para o COPY (deriva do DTO)."""
    return COLUNAS_DB


def normalizar_data(serie: "pd.Series") -> "pd.Series":
    """Converte datas do formato BR (DD/MM/YYYY) para ISO (YYYY-MM-DD).

    - "01/01/2026" -> "2026-01-01"
    - "2020-01-17" -> "2020-01-17"  (ja ISO: preservado)
    - "" / NaN / invalida -> ""      (vira NULL na carga)
    """
    import pandas as pd

    s = serie.fillna("").astype(str).str.strip()
    # Formato do relatorio exportado: DD/MM/YYYY (explicito e deterministico).
    dt = pd.to_datetime(s, format="%d/%m/%Y", errors="coerce")
    # Fallback para valores que ja cheguem em ISO (YYYY-MM-DD).
    faltando = dt.isna() & s.ne("")
    if faltando.any():
        dt.loc[faltando] = pd.to_datetime(
            s[faltando], format="%Y-%m-%d", errors="coerce"
        )
    return dt.dt.strftime("%Y-%m-%d").fillna("")


def normalizar_inteiro(serie: "pd.Series") -> "pd.Series":
    """Converte uma coluna textual em inteiro (NaN/invalido -> 0)."""
    import pandas as pd

    return pd.to_numeric(serie, errors="coerce").fillna(0).astype(int)


def normalizar(df: "pd.DataFrame") -> "pd.DataFrame":
    """Renomeia as colunas de origem para o contrato e reordena.

    Qualquer coluna canonica ausente apos o rename vira erro (sinal de que o
    layout do relatorio mudou). O transform garante colunas opcionais antes.
    """
    df = df.rename(columns=MAPA_ORIGEM)
    faltando = [c for c in COLUNAS_DB if c not in df.columns]
    if faltando:
        raise KeyError(f"colunas ausentes apos normalizacao: {faltando}")
    return df[list(COLUNAS_DB)]
