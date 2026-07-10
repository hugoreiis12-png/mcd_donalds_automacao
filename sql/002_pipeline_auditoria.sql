-- ============================================================
-- DDL: tabela de auditoria pipeline_auditoria
-- Registra cada execucao da pipeline (sucesso/falha/metadados)
--
-- Fica no schema 'public', junto de public.mcd_reclamacao (sql/001), para
-- que um unico PG_SCHEMA=public cubra dados e auditoria. Em producao
-- (192.168.0.250/FABRICA) a tabela de dados ja existe em public; este DDL
-- cria a auditoria no mesmo schema.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.pipeline_auditoria (
    id              SERIAL PRIMARY KEY,
    run_id          UUID            NOT NULL,
    etapa           VARCHAR(20)     NOT NULL,
    status          VARCHAR(10)     NOT NULL,
    produto         VARCHAR(100),
    ano             VARCHAR(4),
    registros       INTEGER         DEFAULT 0,
    duracao_s       NUMERIC(10, 2),
    erro_tipo       VARCHAR(100),
    erro_msg        TEXT,
    executado_em    TIMESTAMP       NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_auditoria_run_id
    ON public.pipeline_auditoria (run_id);

CREATE INDEX IF NOT EXISTS idx_auditoria_status
    ON public.pipeline_auditoria (status);
