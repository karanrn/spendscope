"""init, demo, doctor, template, backup, config discovery and the CLI's --version."""
import contextlib
import io
import os
import sqlite3
import tempfile
import tomllib
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from unittest import mock

from expense_tracker import ExpenseTrackerError, __version__, db
from expense_tracker.cli import main
from expense_tracker.config import ENV_VAR, database_path, find_config, load_config
from expense_tracker.demo import build_demo
from expense_tracker.doctor import run_doctor
from expense_tracker.scaffold import init_project, set_key
from expense_tracker.sync import sync, sync_statements
from expense_tracker.template import write_template
from tests.helpers import Project, quietly


@contextlib.contextmanager
def in_dir(path: Path):
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        # keep the real per-user config and $EXPENSE_TRACKER_CONFIG out of the tests
        env = {k: v for k, v in os.environ.items() if k != ENV_VAR}
        env["XDG_CONFIG_HOME"] = env["APPDATA"] = str(self.root / "user-config")
        self._env = mock.patch.dict(os.environ, env, clear=True)
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()


class ConfigDiscoveryTests(TempDirTest):
    def test_order_is_env_then_working_dir_then_user_config(self):
        user = self.root / "user-config" / "expense-tracker" / "config.toml"
        user.parent.mkdir(parents=True)
        user.write_text("", encoding="utf-8")
        work = self.root / "work"
        work.mkdir()
        with in_dir(work):
            self.assertEqual(find_config(), user)
            (work / "config.toml").write_text("", encoding="utf-8")
            self.assertEqual(find_config(), work / "config.toml")
            other = self.root / "other.toml"
            other.write_text("", encoding="utf-8")
            with mock.patch.dict(os.environ, {ENV_VAR: str(other)}):
                self.assertEqual(find_config(), other)

    def test_no_config_points_to_init_and_demo(self):
        with in_dir(self.root), self.assertRaises(ExpenseTrackerError) as cm:
            find_config()
        self.assertIn("init", str(cm.exception))
        self.assertIn("demo", str(cm.exception))

    def test_missing_env_config_is_an_error_not_a_fallback(self):
        (self.root / "config.toml").write_text("", encoding="utf-8")
        with in_dir(self.root), mock.patch.dict(os.environ, {ENV_VAR: str(self.root / "nope.toml")}):
            with self.assertRaises(ExpenseTrackerError):
                find_config()

    def test_defaults_and_legacy_oauth_file_name(self):
        config = self.root / "config.toml"
        config.write_text('[source]\ntype = "file"\n', encoding="utf-8")
        cfg = load_config(config)
        self.assertEqual(cfg["source"]["oauth_client_json"], str(self.root / "oauth_client.json"))
        self.assertEqual(cfg["columns"]["amount"], "Expense")
        (self.root / "service_account.json").write_text("{}", encoding="utf-8")
        self.assertEqual(load_config(config)["source"]["oauth_client_json"], str(self.root / "service_account.json"))

    def test_bad_decimal_separator_and_bad_toml(self):
        config = self.root / "config.toml"
        config.write_text('[parsing]\ndecimal_separator = ";"\n', encoding="utf-8")
        with self.assertRaises(ExpenseTrackerError):
            load_config(config)
        config.write_text("[parsing\n", encoding="utf-8")
        with self.assertRaises(ExpenseTrackerError):
            load_config(config)


class InitTests(TempDirTest):
    def test_file_source_writes_config_and_a_workbook_that_syncs(self):
        config = self.root / "config.toml"
        written, out = quietly(init_project, config, "file", "us", interactive=False)
        self.assertEqual(written[0], config)
        self.assertTrue(written[1].name.startswith("Expenses ") and written[1].exists())
        self.assertIn("doctor", out)
        cfg = load_config(config)
        self.assertEqual(cfg["source"]["type"], "file")
        self.assertEqual(cfg["parsing"]["currency_symbol"], "$")
        self.assertEqual(cfg["display"]["locale"], "en-US")
        self.assertEqual(cfg["parsing"]["date_formats"][0], "%m/%d/%Y")
        self.assertEqual(cfg["statements"]["date_formats"][0], "%m/%d/%Y")
        from openpyxl import load_workbook
        self.assertEqual(load_workbook(written[1])["Mar" + written[1].stem[-2:]]["B1"].value, "Expense(USD)")
        quietly(sync, cfg)  # the empty template imports cleanly
        with self.assertRaises(ExpenseTrackerError):
            quietly(init_project, config, "file", "us", interactive=False)
        quietly(init_project, config, "gsheet_api", "eu", force=True, interactive=False)
        cfg = load_config(config)
        self.assertEqual((cfg["source"]["type"], cfg["parsing"]["decimal_separator"]), ("gsheet_api", ","))

    def test_global_writes_the_user_config(self):
        written, _ = quietly(init_project, None, "gsheet_csv", "uk", global_config=True, interactive=False)
        self.assertEqual(written, [self.root / "user-config" / "expense-tracker" / "config.toml"])

    def test_unknown_choices_are_rejected(self):
        with self.assertRaises(ExpenseTrackerError):
            init_project(self.root / "c.toml", "dropbox", "in", interactive=False)
        with self.assertRaises(ExpenseTrackerError):
            init_project(self.root / "c.toml", "file", "mars", interactive=False)

    def test_set_key_is_section_aware_and_keeps_comments(self):
        text = '[a]\nx = 1 # one\n[b]\nx = 2  # two\n'
        self.assertEqual(set_key(text, "b", "x", ["p", "q"]), '[a]\nx = 1 # one\n[b]\nx = ["p", "q"]  # two\n')
        with self.assertRaises(ValueError):
            set_key(text, "c", "x", "v")

    def test_example_config_is_valid_toml(self):
        from expense_tracker.config import EXAMPLE_CONFIG
        tomllib.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))


class TemplateTests(TempDirTest):
    def test_other_year_renames_tabs_and_keeps_the_chart(self):
        from openpyxl import load_workbook
        out = write_template(self.root / "Expenses 2027.xlsx", 2027)
        with zipfile.ZipFile(out) as z:
            self.assertIn("xl/charts/chart1.xml", z.namelist())
            self.assertNotIn("Jan26", z.read("xl/charts/chart1.xml").decode())
        wb = load_workbook(out)
        self.assertEqual(wb.sheetnames, ["Summary"] + [f"{m}27" for m in
                         "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()])
        self.assertIn("'Jan27'!G4", wb["Summary"]["C31"].value)
        with self.assertRaises(ExpenseTrackerError):
            write_template(out, 2027)

    def test_cli_writes_into_a_folder(self):
        quietly(main, ["template", "--year", "2030", "--out", str(self.root)])
        self.assertTrue((self.root / "Expenses 2030.xlsx").exists())


class DemoTests(TempDirTest):
    def test_demo_builds_imports_and_refuses_unrelated_folders(self):
        from datetime import date
        built = build_demo(self.root / "demo", date(2027, 2, 10))
        self.assertEqual([p.name for p in built["workbooks"]], ["Expenses 2026.xlsx", "Expenses 2027.xlsx"])
        cfg = load_config(built["config"])
        quietly(sync, cfg)
        _, out = quietly(sync_statements, cfg, [built["statement"]])
        self.assertIn("new income: 1", out)  # the salary; the friend's split comes after 9 Feb
        self.assertIn("Rules set a Label", out)
        build_demo(self.root / "demo", date(2027, 2, 10))  # an earlier demo may be replaced
        keep = self.root / "mine"
        keep.mkdir()
        (keep / "notes.txt").write_text("keep me", encoding="utf-8")
        with self.assertRaises(ExpenseTrackerError):
            build_demo(keep)
        self.assertTrue((keep / "notes.txt").exists())


class DoctorAndBackupTests(unittest.TestCase):
    def setUp(self):
        self.project = Project()

    def tearDown(self):
        self.project.cleanup()

    def test_doctor_passes_a_working_project_and_fails_a_missing_source(self):
        ok, out = quietly(run_doctor, str(self.project.config_path))
        self.assertTrue(ok, out)
        self.assertIn("Read 3 expenses", out)
        self.assertIn("No database yet", out)
        (self.project.root / "expenses.csv").unlink()
        ok, out = quietly(run_doctor, str(self.project.config_path))
        self.assertFalse(ok)
        self.assertIn("[FAIL] Source file", out)
        with self.assertRaises(SystemExit) as cm:
            quietly(main, ["doctor", "--config", str(self.project.config_path)])
        self.assertEqual(cm.exception.code, 1)

    def test_doctor_does_not_start_a_google_sign_in(self):
        config = self.project.config_path
        config.write_text(config.read_text().replace('type = "file"', 'type = "gsheet_api"'), encoding="utf-8")
        (self.project.root / "oauth_client.json").write_text("{}", encoding="utf-8")
        with mock.patch("expense_tracker.sources.read_google_api") as read:
            ok, out = quietly(run_doctor, str(config))
        read.assert_not_called()
        self.assertTrue(ok)
        self.assertIn("Not signed in to Google yet", out)

    def test_backup_copies_every_row(self):
        cfg = self.project.config()
        quietly(sync, cfg)
        _, out = quietly(main, ["backup", "--config", str(self.project.config_path)])
        copies = list((database_path(cfg).parent / "backups").glob("expenses-*.db"))
        self.assertEqual(len(copies), 1)
        with closing(sqlite3.connect(copies[0])) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM expenses").fetchone()[0], 3)
        with self.assertRaises(ExpenseTrackerError):
            db.backup(database_path(cfg), copies[0])  # never overwrites

    def test_version(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            main(["--version"])
        self.assertIn(__version__, out.getvalue())


if __name__ == "__main__":
    unittest.main()
