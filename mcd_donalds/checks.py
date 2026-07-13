"""Verificacoes pre-voo: site acessivel, diretorios viaveis.

Antes de iniciar o pipeline, garante que:
    1. O site Martin Brower esta respondendo (se habilitado)
    2. Os diretorios de saida existem ou podem ser criados
"""

from __future__ import annotations

from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from mcd_donalds.config import Settings
from mcd_donalds.context import RunContext, StageStatus
from mcd_donalds.errors import DirectoryError, SiteAccessError


def verificar(settings: Settings, ctx: RunContext) -> bool:
    """Executa todas as verificacoes pre-voo.

    O check de site e informativo, NAO fatal: o portal fica atras da Imperva,
    que costuma responder 403 a um GET simples (sem o desafio anti-bot que so
    um browser real resolve). Derrubar a pipeline por isso seria um falso
    negativo — quem de fato prova que o portal esta acessivel e o login do
    Selenium na etapa de extracao. Falha aqui vira warning.

    Returns:
        True se as verificacoes fatais passaram.

    Raises:
        DirectoryError: diretorio nao pode ser criado/acessado (fatal).
    """
    ctx.iniciar_etapa("checks")
    ctx.logger.info("Iniciando verificacoes pre-voo")

    try:
        if settings.verificar_site and not settings.dry_run:
            try:
                _verificar_site(settings, ctx)
            except SiteAccessError as exc:
                ctx.logger.warning(
                    "Check de site nao conclusivo (%s) — seguindo; o login do "
                    "Selenium e a verificacao real de acesso.", exc,
                )

        _verificar_diretorios(settings, ctx)

        ctx.finalizar_etapa("checks", StageStatus.PASSED)
        ctx.logger.info("Todas as verificacoes pre-voo passaram")
        return True
    except Exception:
        ctx.finalizar_etapa("checks", StageStatus.FAILED)
        raise


def _verificar_site(settings: Settings, ctx: RunContext) -> None:
    """Verifica se o site Martin Brower responde HTTP 200."""
    ctx.logger.info("Verificando site: %s", settings.site_url)

    req = Request(
        settings.site_url,
        headers={"User-Agent": "Mozilla/5.0 (compativel; mcd-donalds/0.1)"},
    )

    try:
        with urlopen(req, timeout=10) as resp:
            status = resp.status
            if status != 200:
                raise SiteAccessError(
                    f"Site retornou HTTP {status} (esperado 200): "
                    f"{settings.site_url}"
                )
            ctx.logger.info("  Site OK (HTTP %d)", status)
    except URLError as exc:
        raise SiteAccessError(
            f"Nao foi possivel acessar {settings.site_url}: {exc.reason}"
        ) from exc
    except OSError as exc:
        raise SiteAccessError(
            f"Erro de rede ao acessar {settings.site_url}: {exc}"
        ) from exc


def _verificar_diretorios(settings: Settings, ctx: RunContext) -> None:
    """Verifica/cria os diretorios de saida da pipeline."""
    diretorios: list[tuple[str, Path]] = [
        ("xlsx", settings.xlsx_dir),
        ("csv", settings.csv_dir),
        ("logs", settings.logs_dir),
    ]

    for nome, path in diretorios:
        try:
            path.mkdir(parents=True, exist_ok=True)
            # Testa escrita
            teste = path / ".hf_check"
            teste.write_text("")
            teste.unlink()
            ctx.logger.info("  Diretorio %s: %s (OK)", nome, path)
        except OSError as exc:
            raise DirectoryError(
                f"Diretorio '{nome}' em {path} nao pode ser criado/acessado: {exc}"
            ) from exc
