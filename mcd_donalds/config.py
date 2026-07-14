"""Configuracao central, tipada com pydantic-settings.

Le as variaveis do ambiente / .env com validacao de tipos. As decisoes
ainda em aberto (onde roda, canal, parametros do filtro, tipo do 'ano')
continuam como campos com defaults sensatos -- nunca espalhadas pelo codigo.
"""
from __future__ import annotations
import os
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Convencao Docker "_FILE": pares (env que aponta para o arquivo, campo do DTO).
# Usado para injetar credenciais sensiveis via secret montado (/run/secrets/...).
_DB_SECRETS_FILE: tuple[tuple[str, str], ...] = (
    ("PG_PASSWORD_FILE", "password"),
    ("PG_USER_FILE", "user"),
)


class DBConfig(BaseSettings):
    """Conexao e destino no PostgreSQL (le variaveis PG_* do ambiente/.env)."""
    model_config = SettingsConfigDict(
        env_prefix="PG_", env_file=".env", extra="ignore")

    host: str = "localhost"
    port: int = 5432
    dbname: str = "FABRICA"
    user: str = ""
    password: str = ""
    # 'schema' conflita com atributo do pydantic -> atributo db_schema, env PG_SCHEMA
    db_schema: str = Field(default="mcd_donalds", validation_alias="PG_SCHEMA")
    tabela: str = "mcd_reclamacao"
    audit_tabela: str = "pipeline_auditoria"

    @model_validator(mode="before")
    @classmethod
    def _resolver_secrets_file(cls, data: Any) -> Any:
        """Suporte a Docker/Swarm secrets (convencao _FILE das imagens oficiais).

        Se PG_PASSWORD_FILE / PG_USER_FILE apontar para um arquivo (ex:
        /run/secrets/pg_password), le o conteudo (sem espacos nas bordas) e usa
        como valor do campo. Assim a senha nunca precisa ir em variavel de
        ambiente nem ser commitada no git — fica so no secret montado pelo
        orquestrador. Precede o valor vindo de PG_PASSWORD/PG_USER em env.
        """
        if not isinstance(data, dict):
            return data
        for env_file, campo in _DB_SECRETS_FILE:
            caminho = os.environ.get(env_file)
            if caminho and Path(caminho).is_file():
                data[campo] = Path(caminho).read_text(encoding="utf-8").strip()
        return data

    @property
    def tabela_completa(self) -> str:
        return f"{self.db_schema}.{self.tabela}"

    @property
    def auditoria_completa(self) -> str:
        return f"{self.db_schema}.{self.audit_tabela}"


class Settings(BaseSettings):
    """Parametros gerais da pipeline (le variaveis do ambiente/.env)."""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    base_dir: Path = Path("./dados")

    # URL da pagina de login do portal.
    login_url: str = "https://portal.martinbrower.com.br/mbbr/security-app/login"
    # URL da pagina do formulario de extracao (filtro de periodo + exportar).
    site_url: str = "https://portal.martinbrower.com.br/mbbr/crmfornec/#/home"
    # Endpoint da API que devolve o XLS (application/octet-stream). O browser
    # so faz login + aplicar o filtro; o download e um POST direto nesta URL,
    # reaproveitando a sessao (cookies Imperva + x-auth-token do localStorage).
    export_url: str = (
        "https://portal.martinbrower.com.br/mbbr/crmfornec/"
        "ocorrencFornec/exportarDadosOpen"
    )
    headless: bool = True
    postback_timeout: int = 30
    download_timeout: int = 300

    # Data inicial da janela de extracao (ISO YYYY-MM-DD). Vazio = 1o de
    # janeiro do ano corrente, calculado em runtime (evita data fixa que
    # envelhece na virada do ano). A data final e sempre "hoje".
    dt_inicio_extracao: str = ""

    # Credenciais do portal Martin Brower (login obrigatorio antes da extracao).
    login_user: str = ""
    login_password: str = ""
    
    # Selenium/Chrome: caminhos explicitos para binario e driver. Vazios em
    # dev local (usa webdriver-manager); no container Docker apontam para o
    # chromium/chromedriver instalados via apt (CHROME_BINARY / CHROMEDRIVER_PATH),
    # tornando a extracao deterministica sem download de driver em runtime.
    chrome_binary: str | None = None
    chromedriver_path: str | None = None

    # Idioma do Chrome (Accept-Language). O portal traduz a interface, e os
    # rotulos mudam junto: em en-US o campo de login vira "Username *". O
    # Chromium do container sobe sem locale (Debian slim) e cai em en-US; o
    # Chrome do Windows manda pt-BR. Fixar aqui alinha os dois ambientes.
    chrome_lang: str = "pt-BR"

    # comportamento
    verificar_site: bool = True
    dry_run: bool = False

    # Retencao dos artefatos em dados/ (xlsx, csv, logs). Rodando todo dia, os
    # diretorios crescem sem teto dentro do volume. Com N > 0, arquivos com mais
    # de N dias sao apagados APOS uma carga bem-sucedida. 0 = desligado (guarda
    # tudo) — default conservador para nao mudar o comportamento atual.
    retencao_dias: int = 0

    # resiliencia
    max_tentativas: int = 3
    backoff_s: float = 5.0

    # TODO: definir canal (console | file | email | slack | teams | whatsapp)
    canal: str = "console"
    notificar_sucesso: bool = False

    db: DBConfig = Field(default_factory=DBConfig)

    @property
    def dt_inicio_br(self) -> str:
        """Data inicial da extracao no formato BR (DD/MM/YYYY).

        Usa dt_inicio_extracao (se definida) ou 1o de janeiro do ano corrente.
        """
        inicio = (
            date.fromisoformat(self.dt_inicio_extracao)
            if self.dt_inicio_extracao
            else date(date.today().year, 1, 1)
        )
        return inicio.strftime("%d/%m/%Y")

    @property
    def xlsx_dir(self) -> Path:
        return self.base_dir / "xlsx"

    @property
    def csv_dir(self) -> Path:
        return self.base_dir / "csv"

    @property
    def logs_dir(self) -> Path:
        return self.base_dir / "logs"

    @classmethod
    def load(cls, env_file: str | None = ".env") -> "Settings":
        """Carrega Settings e DBConfig a partir do MESMO arquivo .env.

        O DBConfig precisa ser construido explicitamente com o env_file: o
        default_factory criaria DBConfig() sem argumentos, lendo sempre o
        '.env' fixo do seu model_config e ignorando o env_file escolhido aqui
        (CLI --env). Variaveis de ambiente PG_* continuam tendo prioridade.
        """
        db = DBConfig(_env_file=env_file)  # type: ignore[call-arg]
        return cls(_env_file=env_file, db=db)  # type: ignore[call-arg]