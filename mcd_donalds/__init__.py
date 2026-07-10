"""mcd-donalds: Pipeline ETL para reclamacoes McDonald's (Martin Brower)."""

from mcd_donalds.config import Settings
from mcd_donalds.context import RunContext, StageStatus
from mcd_donalds.orchestrator import executar

__version__ = "0.1.0"

__all__ = [
    "executar",
    "Settings",
    "RunContext",
    "StageStatus",
    "__version__",
]
