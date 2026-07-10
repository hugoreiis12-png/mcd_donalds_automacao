.PHONY: install test mypy run run-dry run-report clean

# ── instalacao ──

install:
	pip install -e .

# ── qualidade ──

test:
	pytest tests/ -v

mypy:
	mypy mcd_donalds/ tests/

# ── execucao ──

run:
	mcd-donalds

run-dry:
	mcd-donalds --dry-run -v

run-report:
	mcd-donalds-report --help

# ── limpeza ──

clean:
	rm -rf .mypy_cache .pytest_cache *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
