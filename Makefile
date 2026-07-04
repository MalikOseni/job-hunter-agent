PYTHON ?= /usr/bin/python3
PROJECT_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
PYTHONPATH := $(PROJECT_ROOT)/src

.PHONY: run run-daily test lint init-db

run:
	PYTHONPATH="$(PYTHONPATH)" $(PYTHON) -m job_hunter_agent.main

run-daily:
	$(PROJECT_ROOT)/scripts/run_daily.sh

test:
	PYTHONPATH="$(PYTHONPATH)" $(PYTHON) -m unittest discover -s "$(PROJECT_ROOT)/tests" -p "test_*.py"

lint:
	PYTHONPATH="$(PYTHONPATH)" $(PYTHON) -m py_compile \
		$(PROJECT_ROOT)/src/job_hunter_agent/*.py \
		$(PROJECT_ROOT)/src/job_hunter_agent/writing/*.py \
		$(PROJECT_ROOT)/scripts/init_db.py \
		$(PROJECT_ROOT)/tests/test_*.py

init-db:
	$(PYTHON) $(PROJECT_ROOT)/scripts/init_db.py
