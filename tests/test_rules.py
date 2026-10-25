import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from launchcheck.page import parse  # noqa: E402
from launchcheck import rules  # noqa: E402

JA_HEAD = '<!doctype html><html lang="ja"><head><title>t</title></head><body>'


def page(body: str, head: str = JA_HEAD):
    return parse("p.html", head + body + "</body></html>")


def rule_ids(findings):
    return sorted({f.rule + ":" + f.severity for f in findings})


class AiLookTest(unittest.TestCase):
    def test_emoji_heading_fails_with_line(self):
        p = parse("p.html", '<html lang="ja">\n<body>\n<h2><span>✅</span> できること</h2>\n</body></html>')
        f = [x for x in rules.check_ai(p) if x.rule == "AI01"]
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].line, 3)
        self.assertEqual(f[0].severity, rules.FAIL)

    def test_emoji_in_body_is_not_flagged(self):
        self.assertFalse([x for x in rules.check_ai(page("<p>完了しました ✅</p>")) if x.rule == "AI01"])

    def test_cliche_reported_once_per_page_with_line(self):
        p = parse("p.html", '<html lang="ja"><body>\n<p>a</p>\n<p>シームレスに。シームレスに。</p></body></html>')
        f = [x for x in rules.check_ai(p) if x.rule == "AI02"]
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].line, 3)
        self.assertIn("【シームレス】", f[0].evidence)

    def test_cliche_inside_script_is_ignored(self):
        self.assertFalse(rules.check_ai(page('<script>var s="シームレス"</script><p>入力は3項目です</p>')))

    def test_english_label_only_on_japanese_pages(self):
        body = "<h2>How it works</h2><button>FAQ</button>"
        self.assertEqual(rule_ids(rules.check_ai(page(body))), ["AI03:WARN"])
        en = page(body, '<html lang="en"><head><title>t</title></head><body>')
        self.assertFalse(rules.check_ai(en))

    def test_english_link_text_is_not_a_label(self):
        self.assertFalse(rules.check_ai(page('<a href="/x">Privacy Policy</a>')))

    def test_reassurance_repeated(self):
        once = page("<p>入力した値は送信されません。</p>")
        twice = page("<p>入力した値は送信されません。</p><p>どこにも送信しません。</p>")
        self.assertFalse([x for x in rules.check_ai(once) if x.rule == "AI04"])
        self.assertEqual(rule_ids(rules.check_ai(twice)), ["AI04:WARN"])

    def test_purple_gradient_css_and_tailwind(self):
        self.assertTrue(rules.check_css_file("a.css", "a{}\n.h{background:linear-gradient(90deg,#6366f1,#a855f7)}"))
        self.assertEqual(rules.check_css_file("a.css", "a{}\n.h{background:linear-gradient(90deg,#6366f1,#a855f7)}")[0].line, 2)
        self.assertFalse(rules.check_css_file("a.css", ".h{background:linear-gradient(#f0fdfa,#fff)}"))
        tw = page('<div class="bg-gradient-to-r from-indigo-500 to-purple-600"><p>x</p></div>')
        self.assertEqual(rule_ids(rules.check_ai(tw)), ["AI05:WARN"])
        self.assertFalse(rules.check_ai(page('<div class="from-indigo-500"><p>x</p></div>')))


class QaTest(unittest.TestCase):
    def test_broken_values(self):
        f = rules.check_qa(page("<p>手取りは NaN 円</p><p>{{ total }}</p><p>[object Object]</p>"))
        self.assertEqual(len([x for x in f if x.rule == "QA01"]), 3)

    def test_word_containing_nan_is_ok(self):
        self.assertFalse(rules.check_qa(page("<p>Nancy と Infinityward は固有名詞</p>")))

    def test_placeholders(self):
        self.assertEqual(rule_ids(rules.check_qa(page("<p>Lorem ipsum dolor</p>"))), ["QA02:FAIL"])
        self.assertEqual(rule_ids(rules.check_qa(page("<p>〇〇市の場合</p>"))), ["QA02:WARN"])

    def test_yen_decimals_but_unit_prices_ok(self):
        self.assertEqual(rule_ids(rules.check_qa(page("<p>保険料 12,345.6円</p>"))), ["QA03:WARN"])
        self.assertFalse(rules.check_qa(page("<p>単価 31.0円/kWh、1,234円</p>")))


class LegalTest(unittest.TestCase):
    AD = '<a href="https://px.a8.net/svt/ejp?a8mat=X" rel="sponsored">比べる</a>'

    def test_affiliate_without_label_fails(self):
        f = rules.check_legal(page(f"<p>{self.AD}</p>"))
        self.assertIn("LG01:FAIL", rule_ids(f))

    def test_affiliate_with_label_before_passes(self):
        self.assertFalse(rules.check_legal(page(f"<p>広告を含みます</p><p>{self.AD}</p>")))

    def test_label_only_after_link_warns(self):
        f = rules.check_legal(page(f"<p>{self.AD}</p><p>※PR</p>"))
        self.assertEqual(rule_ids(f), ["LG01:WARN"])

    def test_missing_rel_sponsored_warns(self):
        f = rules.check_legal(page('<p>PR</p><a href="https://af.moshimo.com/af/c/click?a_id=1">x</a>'))
        self.assertEqual(rule_ids(f), ["LG02:WARN"])

    def test_amazon_tag_and_extra_host(self):
        self.assertTrue(rules.is_affiliate("https://www.amazon.co.jp/dp/B0?tag=abc-22", ""))
        self.assertFalse(rules.is_affiliate("https://www.amazon.co.jp/dp/B0", ""))
        self.assertTrue(rules.is_affiliate("https://partner.example.jp/c?id=1", "", ("partner.example.jp",)))

    def test_superlative_with_and_without_basis(self):
        self.assertEqual(rule_ids(rules.check_legal(page("<p>業界No.1の満足度</p>"))), ["LG03:FAIL"])
        with_basis = page("<p>利用者数No.1（※2026年9月 自社調べではなく第三者機関の調査）</p>")
        self.assertEqual(rule_ids(rules.check_legal(with_basis)), ["LG03:WARN"])

    def test_item_numbers_are_not_superlatives(self):
        self.assertFalse(rules.check_legal(page("<p>No.12 の品目、No.1.5 版</p>")))

    def test_risky_claims_warn(self):
        f = rules.check_legal(page("<p>飲むだけで必ず痩せる。元本保証。</p>"))
        self.assertEqual({x.rule for x in f}, {"LG04"})
        self.assertEqual(len(f), 3)

    def test_disclaimer_needed_only_for_input_pages(self):
        tool = page('<input type="number"><p>結果 1,000円</p>')
        self.assertEqual(rule_ids(rules.check_legal(tool)), ["LG05:WARN"])
        self.assertFalse(rules.check_legal(page('<input type="number"><p>この結果は目安です</p>')))
        self.assertFalse(rules.check_legal(page('<input type="search"><p>絞り込み</p>')))

    def test_insecure_and_external_forms(self):
        self.assertEqual(rule_ids(rules.check_legal(page('<form action="http://x.example/a"></form><p>目安</p>'))), ["LG08:FAIL"])
        self.assertEqual(rule_ids(rules.check_legal(page('<form action="https://x.example/a"></form>'))), ["LG08:WARN"])

    def test_site_level_privacy_and_tokushoho(self):
        tracked = page('<script src="https://www.googletagmanager.com/gtag/js?id=G-1"></script><p>x</p>')
        self.assertEqual(rule_ids(rules.site_legal([tracked])), ["LG06:WARN"])
        privacy = page('<a href="/privacy/">プライバシーポリシー</a>')
        self.assertFalse(rules.site_legal([tracked, privacy]))
        shop = page('<button>購入する</button>')
        self.assertEqual(rule_ids(rules.site_legal([shop])), ["LG07:WARN"])
        self.assertFalse(rules.site_legal([shop, page("<p>特定商取引法に基づく表記</p>")]))
        self.assertFalse(rules.site_legal([page("<p>買い替えは購入する店に引取りを依頼</p>")]))


class SecretTest(unittest.TestCase):
    # 本物に見える文字列をリポジトリに置かないよう、テスト中に組み立てる
    FAKE_AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
    FAKE_ANT = "sk-" + "ant-" + "a" * 30

    def test_secret_patterns(self):
        f = rules.check_secrets("app.js", f"x\nconst k='{self.FAKE_ANT}';\nconst a='{self.FAKE_AWS}'", [])
        self.assertEqual({x.rule + x.severity for x in f}, {"SC01FAIL"})
        self.assertEqual(sorted(x.line for x in f), [2, 3])
        self.assertTrue(all(self.FAKE_ANT not in x.message for x in f))  # 値そのものは表示しない

    def test_google_key_is_warning(self):
        f = rules.check_secrets("a.js", "AIza" + "B" * 35, [])
        self.assertEqual([x.severity for x in f], [rules.WARN])

    def test_blocklist_masks_word(self):
        f = rules.check_secrets("a.html", "<p>勤務先は Example Corp です</p>", ["example corp"])
        self.assertEqual(len(f), 1)
        self.assertNotIn("example corp", f[0].message.lower())

    def test_forbidden_paths(self):
        self.assertEqual(rules.check_forbidden_path(".env")[0].severity, rules.FAIL)
        self.assertEqual(rules.check_forbidden_path("deep/.env.production")[0].severity, rules.FAIL)
        self.assertEqual(rules.check_forbidden_path(".DS_Store")[0].severity, rules.WARN)
        self.assertFalse(rules.check_forbidden_path("assets/env.js"))


class BasicTest(unittest.TestCase):
    def test_missing_basics(self):
        p = parse("about.html", "<html><body><p>短い</p></body></html>")
        self.assertEqual(rule_ids(rules.check_basic(p)), ["BS01:WARN", "BS02:WARN", "BS03:WARN", "BS04:WARN", "BS05:WARN"])

    def test_noindex_and_404_skip_content_checks(self):
        p = parse("x.html", '<html lang="ja"><head><title>t</title><meta name="robots" content="noindex"></head><body><h1>a</h1></body></html>')
        self.assertFalse(rules.check_basic(p))
        e = parse("404.html", '<html lang="ja"><head><title>見つかりません</title></head><body><p>ない</p></body></html>')
        self.assertFalse(rules.check_basic(e))


if __name__ == "__main__":
    unittest.main()
