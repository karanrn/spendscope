# Shortcuts for the common commands. Without make (e.g. on Windows), run the commands after
# each target directly: python -m expense_tracker <command>.
PYTHON ?= python3
APP = $(PYTHON) -m expense_tracker

.PHONY: help init demo doctor sync dry-run statement serve report report-open run backup test build media

help:
	@echo "Expense tracker targets:"
	@echo "  make demo        Try the dashboard on made-up data"
	@echo "  make init        Write a starter config.toml"
	@echo "  make doctor      Check the config, credentials and source"
	@echo "  make sync        Import latest expenses into SQLite"
	@echo "  make dry-run     Preview import without saving expenses"
	@echo "  make statement FILES=...  Import bank statements (.xls/.xlsx/.txt/.csv)"
	@echo "  make serve       Start the editable local dashboard"
	@echo "  make run         Sync, then start the editable dashboard"
	@echo "  make report      Generate a standalone, view-only dashboard.html"
	@echo "  make report-open Generate and open the standalone dashboard"
	@echo "  make backup      Copy the database to backups/"
	@echo "  make test        Run the unit tests"
	@echo "  make build       Build the wheel and sdist into dist/"
	@echo "  make media       Re-record the README video and screenshots (needs node, ffmpeg)"

init:
	$(APP) init

demo:
	$(APP) demo

doctor:
	$(APP) doctor

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

backup:
	$(APP) backup

test:
	$(PYTHON) -m unittest discover -s tests -t .

build:
	rm -rf dist build *.egg-info
	$(PYTHON) -m build

media:
	PYTHON=$(PYTHON) docs/media/build.sh
