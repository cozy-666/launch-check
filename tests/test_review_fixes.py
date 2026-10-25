"""外部レビュー（P1-1〜P2-8）の指摘ごとの再現テスト。それぞれ「直っていれば通る」形で書いている。"""
import contextlib
import functools
import io
import json
import sys
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from launchcheck import net, rules  # noqa: E402
from launchcheck.cli import main  # noqa: E402
from launchcheck.page import parse  # noqa: E402

JA_HEAD = '<!doctype html><html lang="ja"><head><title>t</title><meta name="description" content="d"></head><body>'
FAKE_ANT = "sk-" + "ant-" + "a" * 30  # 本物に見える文字列をリポジトリに置かない


def page(body: str, url: str | None = None):
    p = parse("p.html", JA_HEAD + body + "</body></html>")
    p.url = url
    return p


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def site(d: str, files: dict[str, str]) -> Path:
    root = Path(d) / "site"
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return root


def ids(findings):
    return sorted({f.rule + ":" + f.severity for f in findings})


# ---------------------------------------------------------------- P1-1 伏せ字

class P1_1_RedactionTest(unittest.TestCase):
    def test_secret_in_heading_is_masked_in_text_and_json(self):
        with tempfile.TemporaryDirectory() as d:
            root = site(d, {"index.html": JA_HEAD + f"<h1>✅{FAKE_ANT}</h1></body></html>"})
            code, out, _ = run(str(root))
            self.assertEqual(code, 1)
            self.assertIn("[AI01]", out)
            self.assertIn("[SC01]", out)
            self.assertNotIn(FAKE_ANT, out)
            self.assertNotIn("a" * 8, out)
            _, js, _ = run(str(root), "--json")
            self.assertNotIn(FAKE_ANT, js)
            self.assertNotIn("a" * 8, js)
            self.assertIn("AI01", {f["rule"] for f in json.loads(js)["findings"]})

    def test_truncated_secret_fragment_is_masked(self):
        # 見出しは 40 文字で切られるので、キーの途中までしか出ない。その断片も伏せる
        with tempfile.TemporaryDirectory() as d:
            root = site(d, {"index.html": JA_HEAD + f"<h1>✅ お知らせ一覧 {FAKE_ANT}</h1></body></html>"})
            _, out, _ = run(str(root), "--only", "ai,secret")
            self.assertIn("[AI01]", out)
            self.assertNotIn("a" * 8, out)

    def test_blocklist_word_in_other_rule_is_masked(self):
        with tempfile.TemporaryDirectory() as d:
            root = site(d, {"index.html": JA_HEAD + "<p>TODO 秘密会社</p></body></html>"})
            block = Path(d) / "block.txt"
            block.write_text("秘密会社\n", encoding="utf-8")
            code, out, _ = run(str(root), "--blocklist", str(block))
            self.assertEqual(code, 1)
            self.assertIn("[QA02]", out)
            self.assertIn("[SC03]", out)
            self.assertNotIn("秘密会社", out)
            _, js, _ = run(str(root), "--blocklist", str(block), "--json")
            self.assertNotIn("秘密会社", js)

    def test_blocklist_fragment_at_snippet_edge_and_location(self):
        # 抜粋の左端で語が切れる（「密会社」だけ残る）場合と、ファイル名に語が入る場合
        with tempfile.TemporaryDirectory() as d:
            body = "<p>秘密会社" + "あ" * 15 + "TODO</p>"
            root = site(d, {"秘密会社/index.html": JA_HEAD + body + "</body></html>"})
            block = Path(d) / "block.txt"
            block.write_text("秘密会社\n", encoding="utf-8")
            _, out, _ = run(str(root), "--blocklist", str(block), "--only", "qa,secret")
            self.assertIn("[QA02]", out)
            self.assertNotIn("密会社", out)

    def test_no_secret_no_masking(self):
        r = rules.Redactor(set(), [])
        self.assertEqual(r.apply("見出しに絵文字: ✅ できること"), "見出しに絵文字: ✅ できること")


# ---------------------------------------------------------------- P1-2 SSRF

class _Handler(SimpleHTTPRequestHandler):
    redirects: dict = {}
    hits: list = []

    def do_GET(self):  # noqa: N802
        type(self).hits.append(self.path)
        if self.path in self.redirects:
            self.send_response(302)
            self.send_header("Location", self.redirects[self.path])
            self.end_headers()
            return
        super().do_GET()

    def log_message(self, *a, **k):
        pass


def _serve(directory: str, handler_cls):
    h = functools.partial(handler_cls, directory=directory)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


class P1_2_SsrfTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.tmp2 = tempfile.TemporaryDirectory()

        class A(_Handler):
            redirects, hits = {}, []

        class B(_Handler):
            redirects, hits = {}, []

        cls.A, cls.B = A, B
        cls.srv_b, cls.base_b = _serve(str(Path(cls.tmp2.name) / "site"), B)
        site(cls.tmp2.name, {"evil.css": ".h{background:linear-gradient(90deg,#6366f1,#a855f7)}"})
        cls.srv_a, cls.base_a = _serve(str(Path(cls.tmp.name) / "site"), A)
        good = (ROOT / "tests" / "fixtures" / "good" / "index.html").read_text(encoding="utf-8")
        site(cls.tmp.name, {
            "ok/index.html": good,
            "ok/style.css": "body{color:#222}",
            "asset-redirect/index.html": good.replace('href="style.css"', 'href="/r/css"'),
            "bad-asset/index.html": good.replace('href="style.css"', 'href="http://[bad/"'),
            "form/index.html": good.replace("</main>", '<form action="%s/send"></form></main>' % cls.base_a),
        })
        A.redirects.update({
            "/r/meta": "http://169.254.169.254/latest/meta-data/",
            "/r/file": "file:///etc/passwd",
            "/r/css": f"{cls.base_b}/evil.css",
            "/r/ok": "/ok/",
        })

    @classmethod
    def tearDownClass(cls):
        for s in (cls.srv_a, cls.srv_b):
            s.shutdown()
            s.server_close()
        cls.tmp.cleanup()
        cls.tmp2.cleanup()

    def test_loopback_blocked_without_flag(self):
        self.A.hits.clear()
        code, out, _ = run(f"{self.base_a}/ok/")
        self.assertEqual(code, 1)
        self.assertIn("BS00", out)
        self.assertIn("--allow-private", out)
        self.assertEqual(self.A.hits, [])  # 接続していない

    def test_loopback_allowed_with_flag(self):
        code, out, _ = run(f"{self.base_a}/ok/", "--allow-private")
        self.assertEqual(code, 0, out)

    def test_non_http_scheme_in_url_list(self):
        with tempfile.TemporaryDirectory() as d:
            lst = Path(d) / "urls.txt"
            lst.write_text("file:///etc/passwd\n", encoding="utf-8")
            code, out, _ = run("--urls", str(lst), "--allow-private")
            self.assertEqual(code, 1)
            self.assertIn("BS00", out)
            self.assertNotIn("root:", out)

    def test_redirect_to_metadata_blocked(self):
        real = net.ip_is_public
        with mock.patch.object(net, "ip_is_public", lambda ip: ip == "127.0.0.1" or real(ip)):
            code, out, _ = run(f"{self.base_a}/r/meta")
        self.assertEqual(code, 1)
        self.assertIn("BS00", out)
        self.assertIn("169.254.169.254", out)

    def test_redirect_to_file_blocked_even_with_flag(self):
        code, out, _ = run(f"{self.base_a}/r/file", "--allow-private")
        self.assertEqual(code, 1)
        self.assertIn("BS00", out)

    def test_same_origin_redirect_followed(self):
        code, out, _ = run(f"{self.base_a}/r/ok", "--allow-private")
        self.assertEqual(code, 0, out)

    def test_asset_redirect_to_other_origin_not_fetched(self):
        self.B.hits.clear()
        _, out, _ = run(f"{self.base_a}/asset-redirect/", "--allow-private")
        self.assertNotIn("AI05", out)
        self.assertEqual(self.B.hits, [])

    def test_connection_level_check(self):
        # 名前解決のあとの接続先も確かめる（DNS の付け替え対策）
        with self.assertRaises(net.BlockedURL):
            net.fetch(f"{self.base_a}/ok/", allow_private=False)
        body, final = net.fetch(f"{self.base_a}/r/ok", allow_private=True)
        self.assertTrue(final.endswith("/ok/"))
        self.assertIn("<html", body)

    def test_ip_policy(self):
        for ip in ("127.0.0.1", "10.0.0.1", "192.168.1.1", "172.16.0.1", "169.254.169.254", "100.64.0.1",
                   "0.0.0.0", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "224.0.0.1"):
            self.assertFalse(net.ip_is_public(ip), ip)
        for ip in ("8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"):
            self.assertTrue(net.ip_is_public(ip), ip)

    # P2-3 壊れたアセット URL
    def test_malformed_asset_url_reported_and_continues(self):
        code, out, _ = run(f"{self.base_a}/bad-asset/", f"{self.base_a}/ok/", "--allow-private")
        self.assertIn("WARN [BS00]", out)
        self.assertIn("2 ページ", out)
        self.assertNotIn("Traceback", out)

    # P2-8 URL モードの同じオリジンのフォーム
    def test_same_origin_form_in_url_mode(self):
        _, out, _ = run(f"{self.base_a}/form/", "--allow-private", "--only", "legal")
        self.assertNotIn("LG08", out)


# ---------------------------------------------------------------- P2-3（URL の部品）

class P2_3_OriginTest(unittest.TestCase):
    def test_origin_raises_value_error_for_bad_url(self):
        with self.assertRaises(ValueError):
            net.origin("http://[bad/")
        self.assertEqual(net.origin("https://Example.com/a"), net.origin("https://example.com:443/b"))
        self.assertNotEqual(net.origin("http://example.com/"), net.origin("https://example.com/"))


# ---------------------------------------------------------------- P2-4 LG01 は見える文字だけで判定

class P2_4_AdLabelTest(unittest.TestCase):
    AD = '<a href="https://px.a8.net/svt/ejp?a8mat=X" rel="sponsored">比べる</a>'

    def test_ad_class_does_not_exempt(self):
        self.assertIn("LG01:FAIL", ids(rules.check_legal(page(f'<div class="ad"><p>{self.AD}</p></div>'))))

    def test_label_in_comment_or_script_does_not_count(self):
        self.assertIn("LG01:FAIL", ids(rules.check_legal(page(f"<!-- 広告 --><p>{self.AD}</p>"))))
        self.assertIn("LG01:FAIL", ids(rules.check_legal(page(f'<script>var s="PR"</script><p>{self.AD}</p>'))))

    def test_visible_label_still_passes(self):
        self.assertFalse(rules.check_legal(page(f'<p class="ad">広告</p><p>{self.AD}</p>')))


# ---------------------------------------------------------------- P2-5 隠れた要素

class P2_5_HiddenTest(unittest.TestCase):
    def test_hidden_elements_are_not_visible_text(self):
        for body in ('<p hidden>TODO</p>', '<div style="display:none"><p>NaN</p></div>',
                     '<div style="color:red; visibility: hidden"><p>TODO</p></div>',
                     '<section hidden><div><h2>FEATURES</h2><p>TODO</p></div></section>'):
            p = page(body)
            self.assertFalse(rules.check_qa(p) + rules.check_ai(p), body)

    def test_text_after_hidden_element_counts(self):
        self.assertEqual(ids(rules.check_qa(page("<div hidden><p>x</p></div><p>TODO</p>"))), ["QA02:FAIL"])
        self.assertEqual(ids(rules.check_qa(page('<div style="display:block"><p>TODO</p></div>'))), ["QA02:FAIL"])

    def test_aria_hidden_is_still_visible(self):
        self.assertEqual(ids(rules.check_qa(page('<p aria-hidden="true">TODO</p>'))), ["QA02:FAIL"])

    def test_hidden_heading_emoji_not_flagged(self):
        self.assertFalse(rules.check_ai(page('<h2>できること<span hidden>✅</span></h2>')))
        self.assertTrue(rules.check_ai(page('<h2>できること<span>✅</span></h2>')))

    def test_unclosed_p_does_not_hide_following_block(self):
        self.assertEqual(ids(rules.check_qa(page("<p hidden>x<p>TODO</p>"))), ["QA02:FAIL"])


# ---------------------------------------------------------------- P2-6 インライン要素で分かれた文字

class P2_6_InlineTextTest(unittest.TestCase):
    def test_split_by_span_is_detected_with_line(self):
        p = parse("p.html", '<html lang="ja"><body>\n<p>手取りは\nNa<span>N</span> 円</p></body></html>')
        f = [x for x in rules.check_qa(p) if x.rule == "QA01"]
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].line, 3)
        self.assertIn("【NaN】", f[0].evidence)

    def test_react_comment_separator_and_cliche(self):
        self.assertEqual(ids(rules.check_qa(page("<p>合計 NaN<!-- -->円</p>"))), ["QA01:FAIL"])
        self.assertEqual(ids(rules.check_ai(page("<p>シーム<b>レス</b>につながる</p>"))), ["AI02:FAIL"])

    def test_block_boundaries_are_kept(self):
        self.assertFalse(rules.check_qa(page("<p>Na</p><p>N</p>")))
        self.assertFalse(rules.check_qa(page("<div>Na<div>N</div></div>")))
        self.assertFalse(rules.check_qa(page("<p>Na<br>N</p>")))
        self.assertFalse(rules.check_qa(page('<p>Na<span style="display:block">N</span></p>')))

    def test_adjacent_inline_items_are_not_joined(self):
        # CSS で縦に並べた価格表（実サイトで「¥5001.5」と誤検知した形）
        p = page('<li><span class="sub">1 m 未満<!-- -->: <!-- -->¥500</span><span class="sub">1.5 m 以上: ¥750</span></li>')
        self.assertFalse(rules.check_qa(p))
        self.assertEqual(ids(rules.check_qa(page("<p><b>Na</b>N</p>"))), ["QA01:FAIL"])

    def test_line_of_later_segment(self):
        p = parse("p.html", '<html lang="ja"><body><p>a\nb\n<b>c</b>\nTO<i>DO</i></p></body></html>')
        f = rules.check_qa(p)
        self.assertEqual([x.line for x in f], [4])

    def test_label_after_link_in_same_paragraph_still_warns(self):
        ad = '<a href="https://px.a8.net/x" rel="sponsored">比べる</a>'
        self.assertEqual(ids(rules.check_legal(page(f"<p>表を見る {ad} 広告</p>"))), ["LG01:WARN"])
        self.assertFalse(rules.check_legal(page(f"<p>広告 {ad}</p>")))


# ---------------------------------------------------------------- P2-7 広告ドメインはホスト名で判定

class P2_7_AffiliateHostTest(unittest.TestCase):
    def test_host_in_path_or_query_is_not_affiliate(self):
        for href in ("https://example.org/articles/a8.net", "https://example.org/?u=px.a8.net",
                     "https://evil-a8.net/x", "https://a8.net.example.org/x",
                     "https://example.org/amazon.co.jp?tag=x", "https://www.amazon.co.jp/dp/B0?tags=x",
                     "https://www.amazon.co.jp/dp/B0?tag=", "/a8.net/x", "http://[bad/"):
            self.assertFalse(rules.is_affiliate(href, ""), href)

    def test_real_affiliate_hosts(self):
        for href in ("https://px.a8.net/svt/ejp?a8mat=X", "https://a8.net/", "//af.moshimo.com/af/c/click?a_id=1",
                     "https://www.amazon.co.jp/dp/B0?tag=abc-22", "https://amazon.com/dp/B0?x=1&tag=abc-20",
                     "https://AMZN.TO/abc"):
            self.assertTrue(rules.is_affiliate(href, ""), href)
        self.assertTrue(rules.is_affiliate("https://shop.partner.example.jp/c", "", ("partner.example.jp",)))
        self.assertFalse(rules.is_affiliate("https://example.org/partner.example.jp", "", ("partner.example.jp",)))
        self.assertTrue(rules.is_affiliate("https://example.org/x", "sponsored"))


# ---------------------------------------------------------------- P2-8 同じオリジンのフォーム

class P2_8_FormOriginTest(unittest.TestCase):
    def test_same_origin_absolute_action_is_not_external(self):
        p = page('<form action="https://ex.example/send"></form><p>目安</p>', url="https://ex.example/tool/")
        self.assertFalse(rules.check_legal(p))
        p = page('<form action="//ex.example/send"></form><p>目安</p>', url="https://ex.example/tool/")
        self.assertFalse(rules.check_legal(p))

    def test_other_origin_is_external(self):
        p = page('<form action="https://forms.other.example/a"></form><p>目安</p>', url="https://ex.example/")
        self.assertEqual(ids(rules.check_legal(p)), ["LG08:WARN"])
        p = page('<form action="https://ex.example:8443/a"></form><p>目安</p>', url="https://ex.example/")
        self.assertEqual(ids(rules.check_legal(p)), ["LG08:WARN"])

    def test_scheme_relative_without_base_is_external(self):
        self.assertEqual(ids(rules.check_legal(page('<form action="//x.example/a"></form><p>目安</p>'))), ["LG08:WARN"])
        self.assertFalse(rules.check_legal(page('<form action="/send"></form><p>目安</p>')))

    def test_http_still_fails(self):
        p = page('<form action="http://ex.example/a"></form><p>目安</p>', url="https://ex.example/")
        self.assertEqual(ids(rules.check_legal(p)), ["LG08:FAIL"])
        p = page('<form action="//ex.example/a"></form><p>目安</p>', url="http://ex.example/")
        self.assertFalse(rules.check_legal(p))  # ページ自体が http のときの相対指定はフォームの問題ではない
        p = page('<form action="http://ex.example/a"></form><p>目安</p>', url="http://ex.example/")
        self.assertFalse(rules.check_legal(p))
        p = page('<form action="http://other.example/a"></form><p>目安</p>', url="http://ex.example/")
        self.assertEqual(ids(rules.check_legal(p)), ["LG08:FAIL"])
        self.assertEqual(ids(rules.check_legal(page('<form action="http://ex.example/a"></form><p>目安</p>'))), ["LG08:FAIL"])

    def test_base_url_option_in_dir_mode(self):
        good = (ROOT / "tests" / "fixtures" / "good" / "index.html").read_text(encoding="utf-8")
        html = good.replace("</main>", '<form action="https://ex.example/send"></form></main>')
        with tempfile.TemporaryDirectory() as d:
            root = site(d, {"index.html": html})
            _, out, _ = run(str(root), "--only", "legal")
            self.assertIn("LG08", out)
            _, out, _ = run(str(root), "--only", "legal", "--base-url", "https://ex.example/")
            self.assertNotIn("LG08", out)
            self.assertEqual(run(str(root), "--base-url", "ftp://ex.example/")[0], 2)


if __name__ == "__main__":
    unittest.main()
