.PHONY: format lint typecheck quality

format:
	black backend
	ruff check backend --fix

lint:
	ruff check backend
	black --check backend

typecheck:
	mypy backend

quality: lint typecheck
