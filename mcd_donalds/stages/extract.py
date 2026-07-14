"""Etapa de extracao: baixa o XLS de reclamacoes do portal Martin Brower.

Abordagem hibrida: o Selenium faz apenas o que exige um browser real —
passar o desafio anti-bot da Imperva no login e aplicar o filtro de periodo
via "Processar Relatorio". O download em si NAO usa o browser: e um POST
direto na API `exportarDadosOpen`, reaproveitando a sessao (cookies Imperva +
x-auth-token do localStorage). A resposta e o proprio XLS
(application/octet-stream).

Isso elimina a instabilidade do download headless (que salvava `downloads.htm`
em vez do arquivo, entrava em loop no PerformanceObserver e estourava o
timeout) e torna a extracao deterministica e rapida.

Fluxo:
    1. Login (Selenium) -> passa Imperva, cria a sessao
    2. Abrir modal "Exportar Dados", preencher periodo, "Processar Relatorio"
    3. Aguardar o relatorio ficar pronto (botao "Baixar" habilitado)
    4. Capturar x-auth-token (localStorage) + cookies (Selenium)
    5. POST exportarDadosOpen -> bytes do XLS; poll ate o tamanho estabilizar
       (o servidor gera o relatorio de forma assincrona apos "Processar")
    6. Salvar em dados/xlsx/exportarDados(NNN).xls
    A sessao inteira e retentada ate settings.max_tentativas.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Literal

import requests
from selenium import webdriver
from selenium.common import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from mcd_donalds.config import Settings
from mcd_donalds.context import RunContext, StageStatus
from mcd_donalds.errors import (
    DownloadError,
    DownloadTimeoutError,
    ExtractError,
    FormFillError,
)

# Sessao autenticada reaproveitada pela API: (x-auth-token, cookies, user-agent).
Sessao = tuple[str, dict[str, str], str]

# Assinaturas de arquivo Excel valido: BIFF/OLE (.xls) e ZIP (.xlsx).
_XLS_MAGIC: tuple[bytes, ...] = (b"\xd0\xcf\x11\xe0", b"PK\x03\x04")

# Campos do formulario de login. Ancorados no 'type' do input, nao no rotulo:
# o aria-label e TRADUZIDO ("Usuário *" em pt-BR, "Username *" em en-US), e o
# 'id' e um UUID regerado a cada render (f_a2a3e0ce-...), inutil como ancora.
# O type e a unica coisa estavel — e sobrevive a uma mudanca de idioma do portal
# mesmo que --lang falhe. Os aria-label vem antes so como desempate, caso a tela
# ganhe outros inputs de texto no futuro.
_CSS_CAMPO_USER = (
    "input[aria-label^='Usuário'], input[aria-label^='Username'], "
    "input[type='text']"
)
_CSS_CAMPO_SENHA = "input[type='password']"


def extrair(settings: Settings, ctx: RunContext) -> list[Path]:
    """Executa a extracao: login -> filtro -> download via API.

    Cada tentativa usa uma sessao nova do Chrome. Retorna lista com o caminho
    do XLS baixado (uma unica posicao).

    Em dry-run nada e baixado: a etapa fica SKIPPED e a lista volta vazia.
    Esgotadas as tentativas, a etapa fica FAILED e o ultimo erro e propagado —
    uma falha de extracao nao pode ser confundida com "nao havia o que baixar"
    na trilha de auditoria.
    """
    ctx.iniciar_etapa("extract")
    settings.xlsx_dir.mkdir(parents=True, exist_ok=True)

    if settings.dry_run:
        ctx.logger.info("[dry-run] Extracao simulada — nenhum download sera realizado")
        ctx.finalizar_etapa("extract", StageStatus.SKIPPED)
        return []

    # Credencial vazia falha o login de um jeito indistinguivel de um bloqueio
    # do portal (timeout esperando o icone de usuario). Barrar aqui, antes de
    # abrir o Chrome, troca 3 tentativas silenciosas por uma causa nomeada.
    faltando = [
        nome
        for nome, valor in (
            ("LOGIN_USER", settings.login_user),
            ("LOGIN_PASSWORD", settings.login_password),
        )
        if not valor.strip()
    ]
    if faltando:
        ctx.finalizar_etapa("extract", StageStatus.FAILED)
        raise ExtractError(
            f"Credenciais do portal ausentes: {', '.join(faltando)} — "
            "defina no .env (dev) ou nas variaveis da stack (Portainer)."
        )

    ultimo_erro: Exception | None = None

    for tentativa in range(settings.max_tentativas):
        if tentativa > 0:
            pausa = settings.backoff_s * tentativa
            ctx.logger.info(
                "Tentativa %d/%d (nova sessao) apos %.1fs...",
                tentativa + 1, settings.max_tentativas, pausa,
            )
            time.sleep(pausa)

        driver: WebDriver | None = None
        try:
            driver = _init_driver(settings)
            _fazer_login(driver, settings, ctx)
            _abrir_e_processar(driver, settings, ctx)
            sessao = _capturar_sessao(driver)
            conteudo = _baixar_via_api(settings, ctx, sessao)
            arquivo = _salvar_xls(conteudo, settings)
            ctx.logger.info("Download concluido: %s (%d bytes)", arquivo, len(conteudo))
            ctx.finalizar_etapa("extract", StageStatus.PASSED)
            return [arquivo]
        except Exception as exc:
            ultimo_erro = exc
            ctx.logger.warning(
                "Tentativa %d/%d falhou: %s",
                tentativa + 1, settings.max_tentativas, exc,
            )
            if driver is not None:
                _dump_diagnostico(driver, settings, ctx, tentativa + 1)
            continue
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

    ctx.finalizar_etapa("extract", StageStatus.FAILED)
    msg = (
        f"Download nao concluido apos {settings.max_tentativas} tentativas. "
        f"Ultimo erro: {ultimo_erro}"
    )
    ctx.logger.error(msg)
    # Preserva o tipo especifico (DownloadTimeoutError, FormFillError, ...)
    # quando houver: e ele que vai para erro_tipo na auditoria.
    if isinstance(ultimo_erro, ExtractError):
        raise ultimo_erro
    raise ExtractError(msg) from ultimo_erro


# ── driver / login ──


def _init_driver(settings: Settings) -> WebDriver:
    """Configura o Chrome com as opcoes da pipeline.

    Dois modos de resolver o driver:
    - settings.chromedriver_path definido (Docker) -> usa o chromedriver do
      sistema (apt), deterministico e sem acesso a rede em runtime.
    - vazio (dev local) -> webdriver-manager baixa o driver compativel. O
      import de webdriver_manager e feito aqui (lazy) de proposito: o modulo
      de config chama load_dotenv() no import, o que injetaria o .env em
      os.environ e faria variaveis PG_* vencerem o --env. Importando so
      quando a extracao realmente roda, o Settings.load(--env) ja foi
      construido e nao e afetado.
    """
    chrome_options = Options()
    if settings.headless:
        chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    # Locale explicito. O portal e internacionalizado: sem locale, o Chromium do
    # container (Debian slim, sem locale definido) anuncia Accept-Language en-US
    # e a tela vem em INGLES ("Username *" em vez de "Usuário *"). Em dev local o
    # Chrome do Windows manda pt-BR e a tela vem em portugues — a mesma extracao
    # passava aqui e falhava la. Fixar o idioma alinha os dois ambientes.
    chrome_options.add_argument(f"--lang={settings.chrome_lang}")
    chrome_options.add_experimental_option(
        "prefs", {"intl.accept_languages": settings.chrome_lang}
    )
    if settings.chrome_binary:
        chrome_options.binary_location = settings.chrome_binary

    if settings.chromedriver_path:
        service = Service(settings.chromedriver_path)
    else:
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=chrome_options)


def _esperar(
    driver: WebDriver,
    wait: WebDriverWait[WebDriver],
    condicao: Callable[[WebDriver], WebElement | Literal[False]],
    descricao: str,
    timeout: int,
) -> WebElement:
    """Espera uma condicao e, no timeout, diz O QUE nao apareceu e ONDE estava.

    O str() de uma TimeoutException do Selenium e so o stack trace nativo do
    chromedriver (dezenas de '#0 0x... <unknown>'), sem mensagem: um login que
    morre no primeiro campo fica indistinguivel de um que morre confirmando a
    sessao — causas opostas (portal bloqueou vs. credencial recusada). A
    descricao + URL + titulo da pagina resolvem isso em uma linha de log.
    """
    try:
        return wait.until(condicao)
    except TimeoutException as exc:
        raise FormFillError(
            f"Timeout de {timeout}s aguardando {descricao} "
            f"[URL: {driver.current_url} | titulo: {driver.title!r}]"
        ) from exc


def _fazer_login(driver: WebDriver, settings: Settings, ctx: RunContext) -> None:
    """Autentica no portal Martin Brower com as credenciais configuradas.

    Navega ate a URL, preenche usuario/senha e clica em 'Entrar'.
    Aguarda o indicador de login bem-sucedido (icone de usuario).
    Lanca FormFillError se qualquer etapa falhar (timeout, elemento ausente).
    """
    ctx.logger.info("Efetuando login...")
    driver.get(settings.login_url)
    t = settings.postback_timeout
    wait: WebDriverWait[WebDriver] = WebDriverWait(driver, t)

    campo_user = _esperar(
        driver, wait,
        EC.presence_of_element_located((By.CSS_SELECTOR, _CSS_CAMPO_USER)),
        "o campo de usuario (a pagina de login nao renderizou o formulario)",
        t,
    )
    campo_user.clear()
    campo_user.send_keys(settings.login_user)

    campo_pass = _esperar(
        driver, wait,
        EC.presence_of_element_located((By.CSS_SELECTOR, _CSS_CAMPO_SENHA)),
        "o campo de senha",
        t,
    )
    campo_pass.clear()
    campo_pass.send_keys(settings.login_password)

    botao = _esperar(
        driver, wait,
        EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']")),
        "o botao 'Entrar' ficar clicavel",
        t,
    )
    botao.click()

    _esperar(
        driver, wait,
        EC.presence_of_element_located(
            (By.CSS_SELECTOR, "button.btn-home i-feather[name='user']")
        ),
        "a confirmacao do login (icone de usuario) — o formulario foi submetido "
        "mas a sessao nao abriu: credenciais recusadas?",
        t,
    )

    ctx.logger.info("Login OK")


def _dump_diagnostico(
    driver: WebDriver, settings: Settings, ctx: RunContext, tentativa: int
) -> None:
    """Salva screenshot + HTML da pagina no momento da falha, em dados/logs/.

    Sem isso, uma falha de extracao no container e uma caixa-preta: o log so
    tem o stack trace do chromedriver. Com a pagina em maos da para ver de
    imediato se a tela e o login, um desafio da Imperva ou um erro do portal.
    Nunca pode derrubar a extracao — falha aqui vira warning e segue.
    """
    try:
        settings.logs_dir.mkdir(parents=True, exist_ok=True)
        prefixo = f"falha_{ctx.run_id}_t{tentativa}"
        ctx.logger.warning(
            "  Pagina no momento da falha — URL: %s | titulo: %r",
            driver.current_url, driver.title,
        )

        png = settings.logs_dir / f"{prefixo}.png"
        if driver.save_screenshot(str(png)):
            ctx.logger.warning("  Screenshot: %s", png)

        html = settings.logs_dir / f"{prefixo}.html"
        html.write_text(driver.page_source, encoding="utf-8")
        ctx.logger.warning("  HTML: %s", html)
    except Exception as exc:
        ctx.logger.warning("  Nao foi possivel salvar o diagnostico: %s", exc)


# ── filtro de periodo ──


def _abrir_e_processar(driver: WebDriver, settings: Settings, ctx: RunContext) -> None:
    """Abre o modal de exportacao, aplica o periodo e dispara "Processar".

    Isso seta o filtro na sessao do servidor — o POST de download
    (exportarDadosOpen, body {}) exporta justamente esse estado.
    """
    ctx.logger.info("Abrindo formulario de extracao...")
    driver.get(settings.site_url)
    wait: WebDriverWait[WebDriver] = WebDriverWait(driver, settings.postback_timeout)

    try:
        btn_exportar = wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "button.btn-primary.secundary-custom")
            )
        )
        driver.execute_script("arguments[0].click();", btn_exportar)
        ctx.logger.info("Modal 'Exportar Dados' aberto")

        wait.until(
            EC.visibility_of_element_located(
                (By.CSS_SELECTOR, "#exampleModalCenter.modal.fade.show")
            )
        )

        dt_inicio = settings.dt_inicio_br
        hoje = date.today().strftime("%d/%m/%Y")
        v_inicio = _preencher_input(driver, "input#dtStart", dt_inicio)
        v_fim = _preencher_input(driver, "input#dtEnd", hoje)
        ctx.logger.info("Periodo configurado: %s a %s", v_inicio, v_fim)

        btn_processar = wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "button.btn-primary.primary-custom")
            )
        )
        driver.execute_script("arguments[0].click();", btn_processar)
        ctx.logger.info("Relatorio processado")

        time.sleep(2)
        _verificar_erro_pagina(driver, ctx)

        # Aguarda o botao "Baixar" habilitar — sinal de que o relatorio ficou
        # pronto no servidor antes de disparar o POST de download.
        _aguardar_relatorio_pronto(driver, wait, settings, ctx)
    except TimeoutException as exc:
        raise FormFillError(f"Timeout no fluxo de exportacao: {exc}") from exc


def _preencher_input(driver: WebDriver, css: str, valor: str) -> str:
    """Preenche um input e dispara os eventos que o Angular escuta.

    send_keys nem sempre atualiza o model do Angular; setar via o setter
    nativo + disparar input/change/blur garante o binding. Retorna o valor
    efetivamente presente no input (para conferencia no log).
    """
    el = driver.find_element(By.CSS_SELECTOR, css)
    driver.execute_script(
        """
        const el = arguments[0], v = arguments[1];
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        setter.call(el, v);
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
        el.dispatchEvent(new Event('blur', {bubbles: true}));
        """,
        el, valor,
    )
    return str(el.get_attribute("value") or "")


def _aguardar_relatorio_pronto(
    driver: WebDriver,
    wait: WebDriverWait[WebDriver],
    settings: Settings,
    ctx: RunContext,
) -> None:
    """Aguarda o botao 'Baixar' ficar presente e habilitado (relatorio pronto)."""
    timeout_baixar = min(settings.postback_timeout, 60)
    fim = time.time() + timeout_baixar
    seletor = (By.CSS_SELECTOR, "button.btn-primary.info")

    while time.time() < fim:
        try:
            btn = driver.find_element(*seletor)
            if btn.is_enabled():
                return
        except Exception:
            pass
        time.sleep(0.5)

    # Ultima tentativa com wait oficial (levanta TimeoutException se falhar).
    wait.until(EC.element_to_be_clickable(seletor))


_SELETORES_ERRO: tuple[tuple[str, str], ...] = (
    ("css selector", "div.toast-body"),
    ("css selector", "div.alert.alert-danger"),
    ("css selector", "div.alert.alert-warning"),
    ("css selector", ".modal-body .text-danger"),
    ("css selector", ".swal2-popup"),
    ("xpath", "//*[contains(text(), 'erro') or contains(text(), 'Erro')]"),
    ("xpath", "//*[contains(text(), 'limite') or contains(text(), 'Limite')]"),
    ("xpath", "//*[contains(text(), 'tente novamente')]"),
)


def _verificar_erro_pagina(driver: WebDriver, ctx: RunContext) -> None:
    """Levanta ExtractError se houver mensagens de erro visiveis na pagina."""
    for by, selector in _SELETORES_ERRO:
        try:
            elementos = driver.find_elements(by, selector)
            for el in elementos:
                if el.is_displayed():
                    texto = el.text.strip()
                    if texto:
                        ctx.logger.warning("Erro detectado na pagina: %s", texto[:200])
                        raise ExtractError(
                            f"Servidor retornou erro apos processar: {texto[:200]}"
                        )
        except ExtractError:
            raise
        except Exception:
            continue


# ── download via API ──


def _capturar_sessao(driver: WebDriver) -> Sessao:
    """Extrai da sessao do Selenium o necessario para a chamada da API.

    - x-auth-token: localStorage['token'] (formato user:timestamp:hash)
    - cookies: incluem os da Imperva (visid_incap / incap_ses)
    - user-agent: o mesmo do browser, para consistencia com o desafio anti-bot
    """
    token = driver.execute_script("return localStorage.getItem('token');")
    if not token:
        raise ExtractError(
            "x-auth-token nao encontrado em localStorage['token'] apos o login"
        )
    cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
    user_agent = str(driver.execute_script("return navigator.userAgent;") or "")
    return str(token), cookies, user_agent


def _baixar_via_api(settings: Settings, ctx: RunContext, sessao: Sessao) -> bytes:
    """POST em exportarDadosOpen ate o XLS ficar pronto e o tamanho estabilizar.

    O servidor gera o relatorio de forma assincrona apos "Processar"; logo apos,
    a API pode devolver um resultado ainda parcial/vazio. Fazemos polling ate
    obter dois retornos validos consecutivos de mesmo tamanho.
    """
    ctx.logger.info("Baixando via API: %s", settings.export_url)
    fim = time.time() + settings.download_timeout
    tam_anterior = -1
    ultimo_valido: bytes = b""

    while time.time() < fim:
        conteudo = _post_export(settings, sessao)
        if conteudo[:4] in _XLS_MAGIC:
            if len(conteudo) == tam_anterior:
                ctx.logger.info("Download estavel: %d bytes", len(conteudo))
                return conteudo
            ctx.logger.debug("XLS parcial/pronto: %d bytes — reconfirmando...", len(conteudo))
            tam_anterior = len(conteudo)
            ultimo_valido = conteudo
        else:
            ctx.logger.debug("Relatorio ainda nao pronto (resposta nao-XLS)")
            tam_anterior = -1
        time.sleep(3)

    if ultimo_valido:
        ctx.logger.warning("Timeout de estabilizacao — usando ultimo XLS valido")
        return ultimo_valido

    raise DownloadTimeoutError(
        f"Timeout de {settings.download_timeout}s aguardando o XLS da API "
        f"({settings.export_url})"
    )


def _post_export(settings: Settings, sessao: Sessao) -> bytes:
    """Faz um POST em exportarDadosOpen e retorna os bytes da resposta."""
    token, cookies, user_agent = sessao
    try:
        resp = requests.post(
            settings.export_url,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
                "Origin": "https://portal.martinbrower.com.br",
                "Referer": "https://portal.martinbrower.com.br/mbbr/crmfornec/",
                "User-Agent": user_agent,
                "x-auth-token": token,
            },
            cookies=cookies,
            data="{}",
            timeout=settings.postback_timeout,
        )
    except requests.RequestException as exc:
        raise ExtractError(f"Falha na chamada a exportarDadosOpen: {exc}") from exc

    if resp.status_code != 200:
        raise ExtractError(
            f"exportarDadosOpen retornou HTTP {resp.status_code}: {resp.text[:200]}"
        )
    return resp.content


# ── persistencia ──


def _salvar_xls(conteudo: bytes, settings: Settings) -> Path:
    """Salva os bytes do XLS em dados/xlsx/exportarDados(NNN).<ext>."""
    if conteudo[:4] not in _XLS_MAGIC:
        raise DownloadError(
            f"Conteudo baixado nao e um XLS valido (magic bytes: {conteudo[:4]!r})"
        )
    if len(conteudo) < 1024:
        raise DownloadError(
            f"Arquivo baixado muito pequeno / corrompido ({len(conteudo)} bytes)"
        )

    ext = ".xlsx" if conteudo[:4] == b"PK\x03\x04" else ".xls"
    num = _proximo_numero(settings.xlsx_dir)
    destino = settings.xlsx_dir / f"exportarDados({num:03d}){ext}"
    destino.write_bytes(conteudo)
    return destino.resolve()


def _proximo_numero(diretorio: Path) -> int:
    """Retorna o proximo numero sequencial para exportarDados(NNN)."""
    maior = 0
    for f in diretorio.glob("exportarDados(*).xls*"):
        m = re.search(r"exportarDados\((\d+)\)", f.stem)
        if m:
            num = int(m.group(1))
            if num > maior:
                maior = num
    return maior + 1
