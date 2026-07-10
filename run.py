#!/usr/bin/env python3
"""Conveniencia: executa o pipeline Martin Brower.

Uso:
    python run.py --dry-run -v

Equivalente a:
    mcd-donalds --dry-run -v
"""

from mcd_donalds.cli import main

if __name__ == "__main__":
    main()
