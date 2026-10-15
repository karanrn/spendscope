PYTHON ?= python3
APP = $(PYTHON) -m expense_tracker

.PHONY: help sync dry-run statement serve report report-open run test

help:
	@echo "Expense tracker targets:"
	@echo "  make sync        Import latest expenses into SQLite"
	@echo "  make dry-run     Preview import without saving expenses"
	@echo "  make statement FILES=...  Import bank statements (.xls/.txt) into SQLite"
	@echo "  make serve       Start the editable local dashboard"
	@echo "  make run         Sync, then start the editable dashboard"
	@echo "  make report      Generate a standalone, view-only dashboard.html"
	@echo "  make report-open Generate and open the standalone dashboard"
	@echo "  make test        Run the unit tests"

sync:
	$(APP) sync

dry-run:
	$(APP) sync --dry-run

statement:
	@test -n "$(FILES)" || { echo "usage: make statement FILES='Acct_Statement_*.xls'"; exit 2; }
	$(APP) sync --statement $(FILES)

serve:
	$(APP) serve

report:
	$(APP) report

report-open:
	$(APP) report --open

run: sync
	$(APP) serve

test:
	$(PYTHON) -m unittest discover -s tests -t .
