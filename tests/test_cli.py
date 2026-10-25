import contextlib
import functools
import io
import json
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from launchcheck.cli import main  # noqa: E402

FIX = ROOT / "tests" / "fixtures"


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class DirModeTest(unittest.TestCase):
    def test_good_fixture_passes(self):
        code, out, _ = run(str(FIX / "good"))
        self.assertEqual(code, 0, out)
        self.assertIn("通過", out)

    def test_good_fixture_passes_strict(self):
        code, out, _ = run(str(FIX / "good"), "--strict")
        self.assertEqual(code, 0, out)

    def test_bad_fixture_fails_with_file_line_and_fix(self):
        code, out, _ = run(str(FIX / "bad"))
        self.assertEqual(code, 1)
        self.assertIn("FAIL [AI01] index.html:11", out)
        self.assertIn("WARN [AI05] style.css:4", out)
        self.assertIn("直し方:", out)
        self.assertIn("要修正", out)

    def test_json_output(self):
        code, out, _ = run(str(FIX / "bad"), "--json")
        data = json.loads(out)
        self.assertEqual(code, 1)
        self.assertFalse(data["passed"])
        rules = {f["rule"] for f in data["findings"]}
        self.assertTrue({"AI01", "AI02", "QA01", "LG01", "LG03", "LG06", "LG07"} <= rules)

    def test_only_and_ignore(self):
        code, out, _ = run(str(FIX / "bad"), "--only", "basic")
        self.assertEqual(code, 0)
        self.assertNotIn("AI0", out)
        code, out, _ = run(str(FIX / "bad"), "--only", "ai", "--ignore", "AI01", "--ignore", "AI02")
        self.assertEqual(code, 0)
        self.assertIn("AI03", out)

    def test_max_per_rule_hides_extra(self):
        _, out, _ = run(str(FIX / "bad"), "--max-per-rule", "1")
        self.assertIn("AI02 はほかに 5 件", out)

    def test_usage_errors_exit_2(self):
        self.assertEqual(run()[0], 2)
        self.assertEqual(run(str(FIX / "nope"))[0], 2)
        self.assertEqual(run(str(FIX / "good"), "--only", "xyz")[0], 2)
        self.assertEqual(run(str(FIX / "good"), "--blocklist", str(FIX / "missing.txt"))[0], 2)

    def test_secrets_forbidden_files_and_blocklist(self):
        with tempfile.TemporaryDirectory() as d:
            site = Path(d) / "site"
            shutil.copytree(FIX / "good", site)
            (site / ".env").write_text("TOKEN=x\n", encoding="utf-8")
            (site / "app.js").write_text("const k='" + "sk-" + "ant-" + "b" * 30 + "';\n", encoding="utf-8")
            (site / "index.html").write_text((site / "index.html").read_text(encoding="utf-8").replace(
                "くらしの計算メモ", "くらしの計算メモ 運営: 山田商事"), encoding="utf-8")
            block = Path(d) / "block.txt"
            block.write_text("# 社名\n山田商事\n", encoding="utf-8")
            code, out, _ = run(str(site), "--blocklist", str(block))
            self.assertEqual(code, 1)
            self.assertIn("FAIL [SC02] .env", out)
            self.assertIn("FAIL [SC01] app.js:1", out)
            self.assertIn("FAIL [SC03] index.html", out)
            self.assertNotIn("山田商事", out)  # ブロックリストの語は伏せて表示する


class UrlModeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handler = functools.partial(SimpleHTTPRequestHandler, directory=str(FIX))
        handler.log_message = lambda *a, **k: None
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_urls_good_and_bad(self):
        code, out, _ = run(f"{self.base}/good/", f"{self.base}/good/privacy/")
        self.assertEqual(code, 0, out)
        code, out, _ = run(f"{self.base}/bad/")
        self.assertEqual(code, 1)
        self.assertIn("/bad/style.css:4", out)  # 同じホストの CSS も取りに行く

    def test_url_list_file_and_unreachable(self):
        with tempfile.TemporaryDirectory() as d:
            lst = Path(d) / "urls.txt"
            lst.write_text(f"# コメント\n{self.base}/good/\n{self.base}/missing/\n", encoding="utf-8")
            code, out, _ = run("--urls", str(lst))
            self.assertEqual(code, 1)
            self.assertIn("BS00", out)


if __name__ == "__main__":
    unittest.main()
