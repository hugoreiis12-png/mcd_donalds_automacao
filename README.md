# mcd-donalds

Pipeline ETL para reclamacoes/ocorrencias McDonald's do portal Martin Brower.

Extrai dados do [portal Martin Brower](https://portal.martinbrower.com.br), transforma XLS
em CSV normalizado, aplica gates de qualidade e carrega no PostgreSQL.

## Requisitos

- Python >= 3.12
- Google Chrome (para extracao com Selenium)
- PostgreSQL (para armazenamento dos dados)

## Setup

```bash
# 1. Clonar e entrar no diretorio
git clone <repo>
cd mcd-donalds

# 2. Criar virtualenv e ativar
python -m venv .venv
.venv\Scripts\activate  # Windows
source .venv/bin/activate  # Linux/Mac

# 3. Instalar o pacote
make install
# ou: pip install -e .

# 4. Criar as tabelas no PostgreSQL
psql -h localhost -U postgres -d FABRICA -f sql/001_mcd_reclamacao.sql
psql -h localhost -U postgres -d FABRICA -f sql/002_pipeline_auditoria.sql
```

## Configuracao (.env)

Crie um arquivo `.env` na raiz (veja `.env.example`):

```env
PG_HOST=localhost
PG_PORT=5432
PG_DBNAME=FABRICA
PG_USER=postgres
PG_PASSWORD=postgres
PG_SCHEMA=public
BASE_DIR=./dados
HEADLESS=true
CANAL=console
```

## Uso

### Pipeline completa

```bash
# Executar pipeline
mcd-donalds

# Simular sem baixar nem inserir
mcd-donalds --dry-run -v

# Modo visivel (com GUI do Chrome)
mcd-donalds --no-headless

# Usando o atalho run.py
python run.py --dry-run -v
```

### Relatorios

```bash
# Consultar dados carregados
mcd-donalds-report --restaurante PAN --dt-inicio 2026-01-01

# Exportar para CSV
mcd-donalds-report --restaurante PAN --formato csv --output dados.csv

# Exportar para Excel
mcd-donalds-report --dt-inicio 2026-01-01 --formato excel --output relatorio.xlsx
```

### Makefile

```bash
make install    # pip install -e .
make test       # pytest tests/ -v
make mypy       # mypy mcd_donalds/ tests/
make run        # executar pipeline
make run-dry    # dry-run com verbose
make clean      # limpar caches
```

## Arquitetura

```
run.py / CLI
    |
orchestrator.py
    |
    +---> checks       (site acessivel, diretorios)
    +---> extract      (Selenium + Chrome -> XLS)
    +---> transform    (XLS -> CSV normalizado)
    +---> quality      (gates: nulo, duplicata, range)
    +---> load         (CSV -> PostgreSQL via COPY)
    |
    +---> audit        (registro na tabela pipeline_auditoria)
    +---> notify       (console / file / none)
```

### Fluxo de dados

```
Site Martin Brower  ──extract──>  dados/xlsx/*.xlsx
                                    |
                               transform
                                    |
                            dados/csv/*.csv
                                    |
                               quality (gates)
                                    |
                               load (COPY)
                                    |
                     PostgreSQL (public.mcd_reclamacao)
```

## Estrutura do projeto

```
mcd-donalds/
├── mcd_donalds/         # Pacote principal
│   ├── cli.py              # CLI (argparse)
│   ├── orchestrator.py     # Orquestrador do pipeline
│   ├── config.py           # Settings via pydantic-settings
│   ├── context.py          # RunContext, StageStatus
│   ├── errors.py           # Hierarquia de erros
│   ├── db.py               # Conexao PostgreSQL
│   ├── model.py            # DTO Reclamacao, COLUNAS_DB, MAPA_ORIGEM
│   ├── checks.py           # Verificacoes pre-voo
│   ├── audit.py            # Auditoria no banco
│   ├── notify.py           # Notificacoes
│   ├── quality.py          # Gates de qualidade
│   ├── report.py           # Relatorios pos-carga
│   └── stages/
│       ├── extract.py      # Selenium + Chrome
│       ├── transform.py    # XLS -> CSV
│       └── load.py         # CSV -> PostgreSQL
├── tests/                  # Testes unitarios (53 testes)
├── sql/                    # DDLs do banco
│   ├── 001_mcd_reclamacao.sql
│   └── 002_pipeline_auditoria.sql
├── dados/                  # Dados gerados (xlsx/, csv/, logs/)
├── run.py                  # Atalho: python run.py
├── Makefile                # Automacao
└── .env                    # Configuracao local
```

## Testes

```bash
# Todos os testes
pytest tests/ -v

# Testes especificos
pytest tests/test_errors.py -v
pytest tests/test_model.py -v
pytest tests/test_report.py -v
pytest tests/test_transform.py -v
```

53 testes unitarios cobrindo:
- Hierarquia de erros (17 classes)
- Modelo de dados (Reclamacao DTO, 28 colunas, normalizacao)
- Transformacao (XLS -> CSV, normalizacao de datas/inteiros)
- Relatorios (sumarios por status/motivo, exportacao CSV)

## Tecnologias

- **Python 3.12** — tipo estrito (mypy strict)
- **Selenium** + webdriver-manager — automacao do navegador
- **pandas** + openpyxl — transformacao de dados
- **psycopg2** — PostgreSQL (COPY bulk insert)
- **pydantic** + pydantic-settings — configuracao tipada
- **pytest** — testes unitarios

## Deploy com Docker / Portainer

A imagem roda o pipeline **todo dia as 08:00** (fuso `America/Sao_Paulo`),
via `cron` interno. O container fica de pe (`restart: unless-stopped`) e o
`cron` dispara o job no horario.

```bash
# Build local
docker build -t mcd-donalds:latest .

# Subir via compose (ajuste as variaveis no docker-compose.yml antes)
docker compose up -d

# Validar o deploy sem esperar o horario: executa o pipeline uma vez no start
#   defina RUN_ON_START=true no docker-compose.yml, suba, confira os logs,
#   depois volte para false.
docker compose logs -f
```

### Segredos (fora do git)

A senha do PostgreSQL e as credenciais do portal Martin Brower **nao** ficam
no repositorio. Sao fornecidas como variaveis de ambiente da stack, e o
`docker-compose.yml` as repassa ao container.

**No Portainer** (Stack a partir do git), em *Environment variables* adicione:

```
PG_PASSWORD=sua_senha_do_banco
LOGIN_USER=seu_usuario_do_portal
LOGIN_PASSWORD=sua_senha_do_portal
```

Sem `LOGIN_USER` / `LOGIN_PASSWORD` o container nao consegue logar no portal e a
extracao falha. Esses valores ficam so na configuracao da stack no Portainer,
nunca no git.

**Em dev local**, o `docker compose` le essas variaveis do arquivo `.env`
(gitignored) automaticamente.

> Nota: o `config.py` tambem aceita a convencao Docker `_FILE`
> (`PG_PASSWORD_FILE` / `PG_USER_FILE` apontando para um arquivo montado), util
> caso um dia migre para Docker Swarm e queira usar secrets nativos.

### Schema no banco de destino

O pipeline **nao cria** as tabelas. No destino `192.168.0.250/FABRICA` ambas
ficam no schema `public`: `public.mcd_reclamacao` (dados) e
`public.pipeline_auditoria` (auditoria, criada via `sql/002`). Por isso a
producao usa `PG_SCHEMA=public` — um unico schema cobre dados e auditoria.
Num banco novo (do zero), aplique os dois DDLs:

```bash
psql -h <host> -U postgres -d FABRICA -f sql/001_mcd_reclamacao.sql
psql -h <host> -U postgres -d FABRICA -f sql/002_pipeline_auditoria.sql
```

A auditoria e nao-fatal: se a tabela faltar, o pipeline ainda carrega os dados
(apenas emite warning e fica sem trilha historica).

### Como o Chrome roda no container

O `Dockerfile` instala `chromium` + `chromium-driver` do apt (versoes sempre
compativeis). As variaveis `CHROME_BINARY` e `CHROMEDRIVER_PATH` (ja setadas
na imagem) fazem o `extract.py` usar esses binarios diretamente, sem baixar
driver em runtime. Em dev local essas variaveis ficam vazias e o
`webdriver-manager` resolve o driver normalmente.

## Proximos passos

- [ ] Validar extracao com Selenium no portal de reclamacoes
- [ ] Canais de notificacao: email, Slack, WhatsApp
- [ ] Testes de integracao com banco real
- [ ] CI/CD (GitHub Actions)
