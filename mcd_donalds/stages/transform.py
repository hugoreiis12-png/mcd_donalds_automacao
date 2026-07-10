"""Etapa de transformacao: le XLS, normaliza colunas, exporta CSV.

Para cada arquivo XLS em dados/xlsx/:
    1. Le com pandas + xlrd (.xls) ou openpyxl (.xlsx)
    2. Pula linhas de cabecalho (titulo, periodo, timestamp)
    3. Identifica a linha de cabecalho real ("Nº OC/AD")
    4. Mapeia colunas (MAPA_ORIGEM + heuristica)
    5. Normaliza tipos (datas → ISO, inteiros → int)
    6. Salva CSV em dados/csv/
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from mcd_donalds.config import Settings
from mcd_donalds.context import RunContext, StageStatus
from mcd_donalds.errors import ParseError
from mcd_donalds.model import (
    COLUNAS_DATA,
    COLUNAS_DB,
    COLUNAS_INT,
    MAPA_ORIGEM,
    normalizar,
    normalizar_data,
    normalizar_inteiro,
)


# Heuristica complementar: normaliza nomes de colunas mesmo sem
# entrada explicita no MAPA_ORIGEM (ex: "Data Criação" -> "dt_criacao").
# Usada apenas como fallback quando MAPA_ORIGEM nao cobre o rotulo.
_HEURISTICA_COL: dict[str, str] = {
    "n_oc_ad": "n_oc_ad",
    "tipo_doc": "tipo_doc",
    "nome_doc": "nome_doc",
    "status": "status",
    "restaurante": "restaurante",
    "cidade": "cidade_restaurante",
    "estado": "estado_restaurante",
    "nome_contato": "nome_contato",
    "cd": "cd",
    "filial": "filial",
    "motivo": "motivo",
    "cod_produto": "cod_produto",
    "desc_produto": "desc_produto",
    "unidade": "unidade",
    "fabricante": "fabricante",
    "lote": "lote",
    "observacao": "observacao",
    "laudo": "laudo",
}


def transformar(
    settings: Settings,
    ctx: RunContext,
    arquivos_xls: list[Path] | None = None,
) -> list[Path]:
    """Transforma arquivos XLS em CSVs normalizados.

    Args:
        settings: Configuracao da pipeline.
        ctx: Contexto de execucao.
        arquivos_xls: Lista de paths XLS para processar.
                       Se None, varre settings.xlsx_dir.

    Returns:
        Lista de paths dos CSVs gerados.
    """
    ctx.iniciar_etapa("transform")
    settings.csv_dir.mkdir(parents=True, exist_ok=True)

    xls_paths = arquivos_xls or _listar_xls(settings)
    if not xls_paths:
        ctx.logger.warning("Nenhum arquivo XLS encontrado em %s", settings.xlsx_dir)
        ctx.finalizar_etapa("transform", StageStatus.SKIPPED)
        return []

    csvs_gerados: list[Path] = []

    for path in xls_paths:
        try:
            csv_path = _transformar_arquivo(path, settings, ctx)
            if csv_path:
                csvs_gerados.append(csv_path)
        except ParseError:
            ctx.logger.exception("Erro ao processar %s — pulando", path.name)
            continue
        except Exception:
            ctx.finalizar_etapa("transform", StageStatus.FAILED)
            raise

    if csvs_gerados:
        ctx.finalizar_etapa("transform", StageStatus.PASSED)
    else:
        ctx.finalizar_etapa("transform", StageStatus.FAILED)

    return csvs_gerados


# ── helpers ──


def _listar_xls(settings: Settings) -> list[Path]:
    """Retorna arquivos .xls/.xlsx, dando prioridade ao .xls original.

    Se existir .xls e .xlsx com mesmo nome (ex: exportarDados.xls +
    exportarDados.xlsx gerado pelo proprio transform), retorna apenas o .xls.
    """
    todos = sorted(settings.xlsx_dir.glob("*.xls*"))
    stems: set[str] = set()
    resultado: list[Path] = []
    for p in todos:
        if p.suffix.lower() == ".xlsx" and p.stem in stems:
            continue
        stems.add(p.stem)
        resultado.append(p)
    return resultado


def _transformar_arquivo(
    path: Path, settings: Settings, ctx: RunContext
) -> Path | None:
    """Processa um unico XLS: limpa rodapé, salva XLSX+CSV originais,
    depois processa para formato canonico e salva CSV para DB."""
    ctx.logger.info("Transformando: %s", path.name)

    tipo_doc = _extrair_produto(path)
    if not tipo_doc:
        ctx.logger.warning("Nome invalido: %s — pulando", path.name)
        return None

    ctx.logger.info("  Tipo de documento detectado: %s", tipo_doc)

    # 1. Leitura bruta (colunas originais preservadas)
    df = _ler_xls(path)
    if df.empty:
        ctx.logger.warning("  XLS vazio: %s — pulando", path.name)
        return None

    ctx.logger.info("  Linhas brutas: %d | Colunas: %s", len(df), list(df.columns))

    # 2. Remove linhas-residuo (rodapé "Fonte:...") ANTES de qq manipulacao
    df = _remover_residuos(df, ctx)
    if df.empty:
        ctx.logger.warning("  Todas as linhas removidas em %s", path.name)
        return None

    # 3. Salva XLSX limpo (sobrescreve o baixado, agora sem rodapé)
    _salvar_xlsx_limpo(df, path, settings, ctx)

    # 5. Salva CSV com colunas ORIGINAIS (= XLSX, separador ";", UTF-8 BOM)
    csv_nome = path.with_suffix(".csv").name
    csv_path_original = settings.csv_dir / csv_nome
    _salvar_csv_original(df, csv_path_original)
    ctx.logger.info("  CSV original: %s (%d linhas)", csv_nome, len(df))

    # 6. Processa para formato canonico (DB)
    df_canonico = _processar_canonico(df, tipo_doc, ctx)
    if df_canonico.empty:
        ctx.logger.warning("  Zero linhas apos processamento canonico em %s", path.name)
        return None

    # 7. Salva CSV canonico para DB
    csv_path_db = _salvar_csv_canonico(df_canonico, path, settings)
    ctx.logger.info("  CSV DB: %s (%d linhas)", csv_path_db.name, len(df_canonico))

    return csv_path_db


# Colunas numericas (sentinela 0) vs textuais (sentinela "") quando ausentes.
_COLUNAS_NUMERICAS: set[str] = set(COLUNAS_INT)


def _garantir_colunas_canonicas(
    df: pd.DataFrame, produto: str, ctx: RunContext
) -> pd.DataFrame:
    """Adiciona colunas de COLUNAS_DB ausentes com defaults sensatos.

    Numericas → 0 (sentinela), textuais → "". Evita que layouts que
    omitem colunas (ex: dados anuais sem 'dia') quebrem normalizar().
    """
    faltando = [c for c in COLUNAS_DB if c not in df.columns]
    if not faltando:
        return df
    ctx.logger.info("  Colunas ausentes preenchidas com default: %s", faltando)
    for col in faltando:
        df[col] = 0 if col in _COLUNAS_NUMERICAS else ""
    return df


# Marcadores de linha-residuo: linhas cujo conteudo em qq coluna textual
# COMECA com um destes sao descartadas (ex: rodape 'Fonte:' do relatorio exportado).
MARCADORES_RESIDUO: tuple[str, ...] = ("Fonte:",)


def _remover_residuos(df: pd.DataFrame, ctx: RunContext) -> pd.DataFrame:
    """Remove linhas-residuo identificadas por marcadores em qq coluna textual.

    Varre todas as colunas textuais em busca de marcadores (ex: "Fonte:"),
    que indicam linhas de rodape que nao sao registros de reclamacao.
    """
    mask = pd.Series(False, index=df.index)
    for col in df.columns:
        if pd.api.types.is_string_dtype(df[col]):
            txt = df[col].fillna("").astype(str).str.strip()
            for marcador in MARCADORES_RESIDUO:
                mask = mask | txt.str.startswith(marcador, na=False)
    if mask.any():
        ctx.logger.info("  Linha(s)-residuo removida: %d", mask.sum())
        df = df[~mask]
    return df


def _extrair_produto(path: Path) -> str | None:
    """Extrai o tipo de documento do nome do arquivo.

    Formato esperado:
      - TIPO_RECLAMACAO.xlsx         (ex: QC_reclamacao.xlsx) → tipo
      - exportarDados.xls            (ex: exportarDados.xls)  → GERAL
      - exportarDados(NNN).xls       (ex: exportarDados(001).xls) → GERAL
    """
    stem = re.sub(r"\(\d+\)$", "", path.stem).strip()
    match = re.match(r"^([A-Z0-9]+)_RECLAMACAO$", stem.upper())
    if match:
        return match.group(1)
    if stem.upper() == "EXPORTARDADOS":
        return "GERAL"
    partes = stem.split("_")
    if partes and partes[0].strip():
        return partes[0].strip().upper()
    return None


_HEADER_MARKER = "OC/AD"
"""Marcador para localizar a linha do cabecalho real no XLS."""


def _ler_xls(path: Path) -> pd.DataFrame:
    """Le XLS/XLSX, localiza cabecalho real (que pode estar em linha nao zero).

    O relatorio exportado tem linhas de preambulo antes do cabecalho:
      Row 0: "CRM - EXTRACAO DE DADOS" (titulo)
      Row 1: "Periodo: ..."
      Row 3: "09/07/2026 13:13" (timestamp)
      Row 5: cabecalho real (Nº OC/AD, Tipo Doctº, ...)

    Engine: xlrd para .xls, openpyxl para .xlsx.
    """
    engine = "xlrd" if path.suffix.lower() == ".xls" else "openpyxl"
    try:
        df = pd.read_excel(path, engine=engine, header=None, dtype=str)
    except Exception as exc:
        raise ParseError(f"Falha ao ler {path.name}: {exc}") from exc

    header_row = None
    for i in range(len(df)):
        vals = df.iloc[i].dropna().astype(str).str.strip().tolist()
        if any(_HEADER_MARKER in v for v in vals):
            header_row = i
            break

    if header_row is None:
        raise ParseError(
            f"Cabecalho (\"{_HEADER_MARKER}\") nao encontrado em {path.name}"
        )

    df.columns = df.iloc[header_row].tolist()
    df = df.iloc[header_row + 1:].reset_index(drop=True)
    df = df.loc[:, ~df.columns.str.contains("^Unnamed", na=False, case=False)]
    df = df.dropna(how="all").reset_index(drop=True)
    return df


def _normalizar_colunas(df: pd.DataFrame) -> pd.DataFrame:
    """Renomeia colunas usando MAPA_ORIGEM + heuristica.

    1. Tenta MAPA_ORIGEM (match exato)
    2. Fallback heuristico (lowercase + strip + match parcial por palavra-chave)
    """
    colunas_antigas = list(df.columns)
    novo_nome: dict[str, str] = {}

    for col in colunas_antigas:
        col_str = str(col).strip()

        # 1. Match exato no MAPA_ORIGEM
        if col_str in MAPA_ORIGEM:
            novo_nome[col] = MAPA_ORIGEM[col_str]
            continue

        # 2. Match heuristico (case-insensitive, normalizado)
        chave = col_str.lower().strip()
        if chave in _HEURISTICA_COL:
            novo_nome[col] = _HEURISTICA_COL[chave]
            continue

        # 3. Match parcial (coluna contem palavra-chave)
        for palavra, canonico in _HEURISTICA_COL.items():
            if palavra in chave:
                novo_nome[col] = canonico
                break

    if not novo_nome:
        return pd.DataFrame()

    df = df.rename(columns=novo_nome)

    # Mantem apenas colunas que conseguimos mapear
    uteis = [c for c in novo_nome.values() if c in df.columns]
    # Remove duplicatas mantendo a primeira
    uteis = list(dict.fromkeys(uteis))
    return df[uteis]


def _ajustar_tipos(df: pd.DataFrame) -> pd.DataFrame:
    """Converte tipos para o contrato do DTO / banco."""
    # Colunas de data: BR (DD/MM/YYYY) → ISO (YYYY-MM-DD)
    for col in COLUNAS_DATA:
        if col in df.columns:
            df[col] = normalizar_data(df[col])

    # Colunas inteiras: qtde_faturada, qtde_reclamada, qtde_autorizada, n_oc_ad
    for col in COLUNAS_INT:
        if col in df.columns:
            df[col] = normalizar_inteiro(df[col])

    return df


def _salvar_xlsx_limpo(
    df: pd.DataFrame, xls_path: Path, settings: Settings, ctx: RunContext
) -> Path:
    """Salva DataFrame limpo como XLSX (extensao .xlsx)."""
    xlsx_path = xls_path.with_suffix(".xlsx")
    df.to_excel(xlsx_path, index=False, engine="openpyxl")
    ctx.logger.info("  XLSX limpo salvo: %s", xlsx_path.name)
    return xlsx_path


def _salvar_csv_original(df: pd.DataFrame, csv_path: Path) -> Path:
    """Salva CSV com colunas originais (separador ";", UTF-8 BOM).

    Usa separador ; para compatibilidade com Excel pt-BR e
    encoding UTF-8 BOM para que o Excel reconheca o encoding.
    """
    df.to_csv(csv_path, index=False, sep=";", encoding="utf-8-sig")
    return csv_path


def _salvar_csv_canonico(df: pd.DataFrame, xls_path: Path, settings: Settings) -> Path:
    """Salva DataFrame normalizado como CSV canonico para carga no DB."""
    csv_nome = xls_path.stem + "_db.csv"
    csv_path = settings.csv_dir / csv_nome
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    return csv_path


def _processar_canonico(
    df: pd.DataFrame, tipo_doc: str, ctx: RunContext
) -> pd.DataFrame:
    """Processa DataFrame limpo para formato canonico (DB)."""
    df = _normalizar_colunas(df)
    if df.empty:
        return df
    df = df.dropna(how="all")
    if "tipo_doc" in df.columns:
        df["tipo_doc"] = df["tipo_doc"].fillna(tipo_doc)
    else:
        df["tipo_doc"] = tipo_doc
    df = _garantir_colunas_canonicas(df, tipo_doc, ctx)
    df = normalizar(df)
    df = _ajustar_tipos(df)
    return df
