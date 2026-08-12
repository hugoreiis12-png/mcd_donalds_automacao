"""Testes para mcd_donalds/context.py.

Foco no handler de console: o pipeline loga setas ("XLS → CSV") e acentos, e o
terminal do Windows e cp1252. Antes, cada uma dessas linhas cuspia um
"--- Logging error --- UnicodeEncodeError" no meio da saida.
"""

from __future__ import annotations

import logging
from typing import TextIO, cast

from mcd_donalds.context import _SafeStreamHandler


class _StreamFalso:
    """Stream de terminal com codificacao controlada (StringIO nao deixa
    definir 'encoding', que e somente-leitura)."""

    def __init__(self, encoding: str) -> None:
        self.encoding = encoding
        self._escrito: list[str] = []

    def write(self, texto: str) -> int:
        self._escrito.append(texto)
        return len(texto)

    def flush(self) -> None:
        pass

    def getvalue(self) -> str:
        return "".join(self._escrito)


def _logar(encoding: str, mensagem: str) -> str:
    stream = _StreamFalso(encoding)
    handler = _SafeStreamHandler(cast(TextIO, stream))
    handler.setFormatter(logging.Formatter("%(message)s"))

    logger = logging.getLogger(f"teste-safe-handler-{encoding}")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.info(mensagem)

    return stream.getvalue()


def test_caractere_fora_do_terminal_vira_substituto_sem_estourar() -> None:
    """cp1252 nao desenha '→': a linha sai com substituto, nao com traceback."""
    saida = _logar("cp1252", "ETAPA 3/5 — Transformacao (XLS → CSV)")

    assert "→" not in saida
    assert "ETAPA 3/5" in saida
    assert "CSV" in saida


def test_terminal_utf8_preserva_o_caractere() -> None:
    """A troca so acontece quando a codificacao do terminal nao suporta."""
    saida = _logar("utf-8", "XLS → CSV")

    assert "XLS → CSV" in saida