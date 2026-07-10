-- ============================================================
-- DDL de REFERENCIA: tabela destino public.mcd_reclamacao
-- Reclamacoes/ocorrencias McDonald's (origem: portal Martin Brower).
--
-- ATENCAO: a tabela ja existe em producao (192.168.0.250 / FABRICA).
-- Este arquivo documenta a estrutura esperada e permite recriar o
-- ambiente do zero. Os TIPOS abaixo sao a melhor inferencia a partir do
-- dump real; VERIFICAR contra o information_schema do banco antes de
-- assumir como verdade (o COPY casta o CSV textual para estes tipos).
-- ============================================================

CREATE TABLE IF NOT EXISTS public.mcd_reclamacao (
    n_oc_ad                 INTEGER,
    tipo_doc                VARCHAR(10),
    nome_doc                VARCHAR(60),
    status                  VARCHAR(60),
    dt_criacao              DATE,
    dt_ultima_atualizacao   DATE,
    restaurante             VARCHAR(60),
    cidade_restaurante      VARCHAR(80),
    estado_restaurante      VARCHAR(4),
    nome_contato            VARCHAR(120),
    cd                      VARCHAR(10),
    filial                  VARCHAR(60),
    nome_ocorrencia         VARCHAR(120),
    motivo                  VARCHAR(80),
    cod_produto             VARCHAR(30),
    desc_produto            VARCHAR(80),
    qtde_faturada           INTEGER,
    qtde_reclamada          INTEGER,
    qtde_autorizada         INTEGER,
    unidade                 VARCHAR(10),
    fabricante              VARCHAR(60),
    lote                    VARCHAR(160),
    dt_fabricacao           DATE,
    dt_vencimento           DATE,
    conclucao               VARCHAR(60),
    destino_mercadoria      VARCHAR(60),
    observacao              TEXT,
    laudo                   TEXT
);

-- Indice na chave da janela de idempotencia (DELETE/INSERT por periodo).
CREATE INDEX IF NOT EXISTS idx_mcd_reclamacao_dt_criacao
    ON public.mcd_reclamacao (dt_criacao);