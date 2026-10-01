PYTHON ?= python3

.PHONY: help sync dry-run serve report report-open run

help:
	@echo "Expense tracker targets:"
	@echo "  make sync        Import latest expenses into SQLite"
	@echo "  make dry-run     Preview import without saving expenses"
	@echo "  make serve       Start the editable local dashboard"
	@echo "  make run         Sync, then start the editable dashboard"
	@echo "  make report      Generate a standalone, view-only dashboard.html"
	@echo "  make report-open Generate and open the standalone dashboard"

sync:
	$(PYTHON) sync.py

dry-run:
	$(PYTHON) sync.py --dry-run

serve:
	$(PYTHON) serve.py

report:
	$(PYTHON) report.py

report-open:
	$(PYTHON) report.py --open

run: sync
	$(PYTHON) serve.py
