"""検査ルール。どれも機械で見つけられる「兆候」を拾うだけで、文章を AI が書いたかどうかは判定しない。

ルール ID の頭文字:
  AI = 生成 AI のテンプレっぽい見た目・言い回し
  QA = 画面に出てはいけない値・未完成のまま
  LG = 広告表示・断定表現・免責など（日本の法令・ガイドラインで問題になりやすい点）
  SC = 秘密情報・公開してはいけないファイル
  BS = タイトル・言語など公開前の基本
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from urllib.parse import parse_qs, urljoin, urlsplit

from .net import origin
from .page import Page, line_of, order_of, snippet

FAIL, WARN = "FAIL", "WARN"


@dataclass
class Finding:
    rule: str
    severity: str
    where: str
    line: int | None
    message: str
    fix: str
    evidence: str = ""

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _text_hits(page: Page, pattern: re.Pattern):
    """見える文字の各ノードで pattern に一致した箇所を (match, Text) で返す。"""
    for t in page.texts:
        for m in pattern.finditer(t.text):
            yield m, t


# ---------------------------------------------------------------- AI: 見た目・言い回し

EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐✅]")

# 決まり文句 → 直し方。生成 AI の日本語に多く、人が書いた具体的な文章ではまず出ないものに絞っている。
CLICHES = {
    "シームレス": "何と何が、どの操作でつながるのかを具体的に書く",
    "革新的": "何が従来と違うのかを数字や手順で示す",
    "次のレベル": "何がどれだけ良くなるのかを数字で書く",
    "ワンストップ": "このページでできることを列挙する（例: 「見積もり・申込・支払いまでこの画面で完結」）",
    "今すぐ始めよう": "ボタンの文言を操作そのものにする（例: 「計算する」「無料で登録」）",
    "選ばれる理由": "見出しを読者の疑問にする（例: 「ほかの方法と何が違うか」）",
    "3つのステップ": "手順が本当に3つなら番号つきリストだけで足りる。見出しにしない",
    "３つのステップ": "手順が本当に3つなら番号つきリストだけで足りる。見出しにしない",
    "主な特徴": "見出しを具体的な内容にする（例: 「対応している自治体と料金」）",
    "もっと身近に": "誰が何をできるようになるのかを書く",
    "をサポートします": "具体的に何をするのか動詞で書く（例: 「〜の書類を自動で作ります」）",
    "最適なソリューション": "何を解決するのか、対象と結果を書く",
    "可能性を広げ": "できるようになることを具体的に書く",
    "新しい体験": "使うと何が変わるのかを具体的に書く",
    "を実現します": "結果を事実として書く（例: 「入力は3項目だけです」）",
    "の世界へようこそ": "挨拶を削り、最初の一文で何のページかを言う",
    "いかがでしたか": "まとめの問いかけを削り、結論か次の行動を書く",
    "いかがでしたでしょうか": "まとめの問いかけを削り、結論か次の行動を書く",
    "ぜひ参考にしてみてください": "締めの定型文を削る",
    "さあ、": "呼びかけを削り、内容から書き始める",
}
LATIN_LABEL = re.compile(r"^(?:[A-Z]{4,}|[A-Za-z][A-Za-z'&/-]*(?: [A-Za-z][A-Za-z'&/-]*){1,5})$")
ALLOWED_LATIN = {"PR", "FAQ", "Q", "A", "Q.", "A.", "OK", "kWh", "Wi-Fi", "Email", "E-mail"}
REASSURE = re.compile(r"(送信|保存)(され|も)(ません|ない|しません)|どこにも送信|サーバーに送られません")
AI_COLORS = r"(#6366f1|#8b5cf6|#7c3aed|#a855f7|#4f46e5|#9333ea|#6d28d9|indigo|purple|violet)"
AI_GRADIENT_CSS = re.compile(r"gradient\([^;{}]*" + AI_COLORS, re.I)
TW_GRADIENT = re.compile(r"\bbg-(?:gradient|linear)-to-[a-z]+\b")
TW_AI_COLOR = re.compile(r"\b(?:from|via|to)-(?:indigo|purple|violet|fuchsia)-\d{2,3}\b")


def check_ai(page: Page) -> list[Finding]:
    out: list[Finding] = []
    for el in page.labels:
        if el.tag.startswith("h") and EMOJI.search(el.text):
            out.append(Finding("AI01", FAIL, page.name, el.line, f"見出しに絵文字: {el.text[:40]}",
                               "見出しから絵文字を外す。区切りが必要なら文字の大きさ・余白・アイコン画像で示す"))
    for phrase, fix in CLICHES.items():
        pat = re.compile(re.escape(phrase))
        for m, t in _text_hits(page, pat):
            out.append(Finding("AI02", FAIL, page.name, line_of(t, m.start()), f"決まり文句「{phrase}」",
                               fix, snippet(t.text, m.start(), m.end())))
            break  # 同じ語は 1 ページ 1 件まで
    if page.is_japanese:
        for el in page.labels:
            if el.tag != "a" and el.text not in ALLOWED_LATIN and len(el.text) <= 40 and LATIN_LABEL.match(el.text):
                out.append(Finding("AI03", WARN, page.name, el.line, f"英語だけの見出し・ラベル: {el.text}",
                                   "日本語のページでは日本語にする（例: FEATURES → できること、How it works → 使い方）"))
    hits = [(m, t) for m, t in _text_hits(page, REASSURE)]
    if len(hits) > 1:
        m, t = hits[1]
        out.append(Finding("AI04", WARN, page.name, line_of(t, m.start()),
                           f"「送信しません」系の安心文が {len(hits)} 回",
                           "1ページ1回にまとめる（入力欄の近くに1回）。くり返すと定型文に見える",
                           snippet(t.text, m.start(), m.end())))
    for css, line in page.styles:
        if AI_GRADIENT_CSS.search(css):
            out.append(_gradient_finding(page.name, line))
            break
    for cls, line in page.class_attrs:
        if TW_GRADIENT.search(cls) and TW_AI_COLOR.search(cls):
            out.append(_gradient_finding(page.name, line, tailwind=True))
            break
    return out


def _gradient_finding(where: str, line: int | None, tailwind: bool = False) -> Finding:
    kind = "Tailwind のクラス" if tailwind else "CSS"
    return Finding("AI05", WARN, where, line, f"紫〜藍のグラデーション（{kind}）",
                   "サイトの主色1色＋無彩色にする。グラデーションを使うなら同系色の明暗だけにする")


def check_css_file(name: str, css: str) -> list[Finding]:
    m = AI_GRADIENT_CSS.search(css)
    if not m:
        return []
    return [_gradient_finding(name, css.count("\n", 0, m.start()) + 1)]


# ---------------------------------------------------------------- QA: 画面に出てはいけないもの

BROKEN_VALUE = re.compile(r"(?<![A-Za-z])(NaN|undefined|Infinity|\[object Object\])(?![A-Za-z])|\{\{\s*[\w.]+\s*\}\}")
PLACEHOLDER_FAIL = re.compile(r"lorem ipsum|(?<![A-Za-z])(TODO|FIXME|TBD)(?![A-Za-z])|ダミーテキスト|ここにテキスト|テキストが入ります", re.I)
PLACEHOLDER_WARN = re.compile(r"〇〇|○○|◯◯|XXX円|xx円")
YEN_DECIMAL = re.compile(r"(?:[¥￥]\s?\d[\d,]*\.\d+|\d[\d,]*\.\d+\s?円)(?!\s?[/／毎])")


def check_qa(page: Page) -> list[Finding]:
    out: list[Finding] = []
    for m, t in _text_hits(page, BROKEN_VALUE):
        out.append(Finding("QA01", FAIL, page.name, line_of(t, m.start()), f"画面に壊れた値「{m.group(0)}」",
                           "計算・テンプレートの入力が空のときの処理を直す（空なら「—」を出す、初期値を入れる）",
                           snippet(t.text, m.start(), m.end())))
    for m, t in _text_hits(page, PLACEHOLDER_FAIL):
        out.append(Finding("QA02", FAIL, page.name, line_of(t, m.start()), f"仮の文言が残っている「{m.group(0)}」",
                           "本番の文章に置き換えるか、その部分を消す", snippet(t.text, m.start(), m.end())))
    for m, t in _text_hits(page, PLACEHOLDER_WARN):
        out.append(Finding("QA02", WARN, page.name, line_of(t, m.start()), f"伏せ字・仮の値かもしれない「{m.group(0)}」",
                           "具体的な値に置き換える。例として意図的に使っているなら無視してよい",
                           snippet(t.text, m.start(), m.end())))
        break
    for m, t in _text_hits(page, YEN_DECIMAL):
        out.append(Finding("QA03", WARN, page.name, line_of(t, m.start()), f"円に小数「{m.group(0)}」",
                           "金額は円単位に丸める（端数の扱いを明記）。単価（円/kWh など）なら単位を書けばこの警告は出ない",
                           snippet(t.text, m.start(), m.end())))
        break
    return out


# ---------------------------------------------------------------- LG: 広告・表示・免責

AFFILIATE_HOSTS = (
    "a8.net", "moshimo.com", "valuecommerce.com", "valuecommerce.ne.jp", "accesstrade.net", "afi-b.com",
    "affiliate-b.com", "felmat.net", "linksynergy.com", "hb.afl.rakuten.co.jp", "amzn.to", "rentracks.jp",
    "j-a-net.jp",
)
AD_LABEL = re.compile(r"広告|プロモーション|アフィリエイト|(?<![A-Za-z])(PR|Ad|Ads|Sponsored|Advertisement|Affiliate)(?![A-Za-z])")
SUPERLATIVE = re.compile(r"業界最安|最安|業界一|業界No\.?\s?1(?!\d)|日本一|世界一|(?<![A-Za-z])N[oO]\s?[.．]\s?1(?![\d.])|ナンバーワン|世界初|日本初|業界初"
                         r"|(?:満足度|効果|安全性?)\S{0,4}100\s?[%％]")
EVIDENCE_NEARBY = re.compile(r"調査|出典|時点|※|比較|ランキング")
RISKY_CLAIM = re.compile(
    r"(?:必ず|絶対に?|確実に|誰でも)(?:痩せ|やせ|儲か|稼げ|治)|飲むだけで|塗るだけで|貼るだけで|完治|副作用(?:は|が)?(?:なし|ない|ありません)"
    r"|元本保証|元本割れ(?:は|の心配は)?(?:なし|ない|ありません)|ノーリスク|リスクなし|絶対に損(?:しない|はしない)")
DISCLAIMER_HINTS = re.compile(r"免責|目安|参考値|概算|保証するものではありません|責任を負いかねます|公式(?:サイト|発表|情報)|専門家|税理士|医師"
                              r"|estimate|for reference|disclaimer|official (?:information|source|site|website)|not (?:tax|legal|financial|medical) advice", re.I)
ANALYTICS = ("googletagmanager.com", "google-analytics.com", "clarity.ms", "cloudflareinsights.com", "connect.facebook.net",
             "pagead2.googlesyndication.com", "analytics.tiktok.com", "static.hotjar.com", "plausible.io")
ANALYTICS_INLINE = re.compile(r"\bgtag\(|\bfbq\(|dataLayer\.push|clarity\(")
PRIVACY_LINK = re.compile(r"privacy|プライバシー|個人情報|外部送信|cookie|クッキー", re.I)
SALES_HOSTS = ("buy.stripe.com", "checkout.stripe.com", "paypal.com/cgi-bin", "paypal.me", "square.link", "pay.jp")
SALES_BUTTON = re.compile(r"^(?:今すぐ)?(?:ご?購入(?:する|手続き|はこちら)?|カートに入れる|買い物かごに入れる|注文する)")
TOKUSHOHO = re.compile(r"特定商取引法|特商法")


def _host_is(host: str, domain: str) -> bool:
    domain = domain.lower().strip(".")
    return host == domain or host.endswith("." + domain)


def is_affiliate(href: str, rel: str, extra_hosts: tuple[str, ...] = ()) -> bool:
    """広告リンクか。ドメインはホスト名の完全一致かサブドメインで判定する（パスやクエリに含まれるだけなら違う）。"""
    if "sponsored" in rel.lower():
        return True
    try:
        u = urlsplit(href.strip())
        host = (u.hostname or "").rstrip(".")
    except ValueError:
        return False
    if not host:
        return False
    if any(_host_is(host, d) for d in AFFILIATE_HOSTS + tuple(extra_hosts)):
        return True
    if _host_is(host, "amazon.co.jp") or _host_is(host, "amazon.com"):
        return any(v.strip() for v in parse_qs(u.query).get("tag", []))
    return False


def check_legal(page: Page, extra_hosts: tuple[str, ...] = ()) -> list[Finding]:
    out: list[Finding] = []
    ads = [l for l in page.links if is_affiliate(l.href, l.attrs.get("rel", ""), extra_hosts)]
    if ads:
        first = ads[0]
        # 表記は画面に見える文字だけで判定する（class 名・コメント・スクリプトの中身は数えない）
        label_orders = [order_of(t, m.start()) for m, t in _text_hits(page, AD_LABEL)]
        if not label_orders:
            out.append(Finding("LG01", FAIL, page.name, first.line,
                               f"広告リンク {len(ads)} 件に「広告」「PR」の表記がない（ステマ規制）",
                               "ページ上部か広告リンクの直前に「広告」「PR」「プロモーションを含みます」と見える文字で書く。"
                               "2023年10月から景品表示法の指定告示で、広告であることが分かりにくい表示は不当表示になる",
                               first.href[:80]))
        elif min(label_orders) > first.order:
            out.append(Finding("LG01", WARN, page.name, first.line,
                               "「広告」「PR」の表記が最初の広告リンクより後ろにある",
                               "表記を最初の広告リンクより前（ページ上部や記事の冒頭）に移す。末尾だけだと見落とされやすい",
                               first.href[:80]))
        no_rel = [l for l in ads if not re.search(r"sponsored|nofollow", l.attrs.get("rel", ""), re.I)]
        if no_rel:
            out.append(Finding("LG02", WARN, page.name, no_rel[0].line,
                               f"広告リンク {len(no_rel)} 件に rel=\"sponsored\" がない",
                               '<a href="…" rel="sponsored noopener"> にする（Google は広告リンクに sponsored か nofollow を求めている）',
                               no_rel[0].href[:80]))
    for m, t in _text_hits(page, SUPERLATIVE):
        window = t.text[max(0, m.start() - 80):m.end() + 80]
        has_basis = EVIDENCE_NEARBY.search(window.replace(m.group(0), "")) is not None
        out.append(Finding("LG03", WARN if has_basis else FAIL, page.name, line_of(t, m.start()),
                           f"最上級・No.1 表現「{m.group(0)}」" + ("（近くに根拠らしき注記あり。中身を確認）" if has_basis else "（根拠の注記が近くにない）"),
                           "客観的な調査の主体・時期・対象を同じ画面に書けないなら消す。"
                           "根拠のない No.1・最安表示は景品表示法の優良誤認・有利誤認になりうる",
                           snippet(t.text, m.start(), m.end())))
    for m, t in _text_hits(page, RISKY_CLAIM):
        out.append(Finding("LG04", WARN, page.name, line_of(t, m.start()), f"断定・効果の保証に読める表現「{m.group(0)}」",
                           "効果や利益を約束する言い方をやめ、条件と根拠を書く。健康は薬機法・健康増進法、"
                           "投資は金融商品取引法（断定的判断の提供）で問題になりやすい",
                           snippet(t.text, m.start(), m.end())))
    if page.input_count and not _is_error_page(page.name) and not DISCLAIMER_HINTS.search(page.visible_text):
        out.append(Finding("LG05", WARN, page.name, None, "入力欄のあるページに免責・前提の記載がない",
                           "結果の近くに1回だけ書く（例: 「〇年度の公式の料率で計算した目安です。正確な金額は公式サイトで確認してください」）"))
    for action, line in page.form_actions:
        kind = _form_target(action.strip(), page.url)
        if kind == "http":
            out.append(Finding("LG08", FAIL, page.name, line, f"フォームが暗号化されていない http へ送信: {action[:60]}",
                               "送信先を https にする"))
        elif kind == "external":
            out.append(Finding("LG08", WARN, page.name, line, f"フォームが外部へ送信: {action[:60]}",
                               "送る項目と送信先をページに書き、プライバシーポリシーに外部サービス名を載せる"))
    return out


def _form_target(action: str, page_url: str | None) -> str | None:
    """フォームの送信先の種類。"http"（暗号化なし）/ "external"（別オリジン）/ None（同じオリジン・相対・判定不能）。"""
    low = action.lower()
    if not low.startswith(("http://", "https://", "//")):
        return None  # 相対パス（同じオリジン）や javascript: など
    try:
        same = bool(page_url) and origin(urljoin(page_url, action)) == origin(page_url)
    except ValueError:
        same = False
    if low.startswith("http://"):
        # ページ自体が http で同じオリジンなら、問題はフォームではなくページの配信方法なのでここでは出さない
        return None if same and page_url.lower().startswith("http://") else "http"
    # ページの URL が分からない（ディレクトリ指定で --base-url なし）ときは、ホスト名のある送信先を外部とみなす
    return None if same else "external"


def site_legal(pages: list[Page]) -> list[Finding]:
    """サイト全体で1回だけ判定するもの（外部送信の公表、特商法表記）。"""
    out: list[Finding] = []
    has_privacy = any(PRIVACY_LINK.search(l.href + " " + l.text) for p in pages for l in p.links)
    tracked = [p for p in pages if any(h in s for s in p.script_srcs for h in ANALYTICS)
               or any(ANALYTICS_INLINE.search(s) for s, _ in p.inline_scripts)]
    if tracked and not has_privacy:
        out.append(Finding("LG06", WARN, tracked[0].name, None,
                           f"解析・広告タグが {len(tracked)} ページにあるが、プライバシーポリシーへのリンクが見つからない",
                           "使っている解析・広告サービス名、送る情報、目的を書いたページを作り、全ページのフッターからリンクする"
                           "（2023年6月施行の電気通信事業法の外部送信規律の対象になる場合がある）"))
    has_toku = any(TOKUSHOHO.search(p.visible_text) for p in pages)
    selling = [p for p in pages if any(h in l.href for l in p.links for h in SALES_HOSTS)
               or any(el.tag in ("a", "button") and SALES_BUTTON.search(el.text) for el in p.labels)]
    if selling and not has_toku:
        out.append(Finding("LG07", WARN, selling[0].name, None,
                           f"販売・申込らしきページが {len(selling)} 件あるが「特定商取引法に基づく表記」が見つからない",
                           "自分で代金を受け取る通信販売なら、販売者・連絡先・価格・支払時期・返品条件などの表記ページを作りリンクする。"
                           "販売プラットフォーム側が表記する場合は不要"))
    return out


# ---------------------------------------------------------------- SC: 秘密情報

SECRET_PATTERNS = (
    ("Anthropic API キー", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"), FAIL),
    ("OpenAI API キー", re.compile(r"sk-(?:proj-)?[A-Za-z0-9]{20,}"), FAIL),
    ("AWS アクセスキー", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), FAIL),
    ("GitHub トークン", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"), FAIL),
    ("Slack トークン", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), FAIL),
    ("Stripe 秘密キー", re.compile(r"\b[sr]k_live_[A-Za-z0-9]{16,}"), FAIL),
    ("秘密鍵", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), FAIL),
    ("Google API キー", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), WARN),
)
SECRET_FIX = "そのキーを発行元で無効化して作り直し、ビルド成果物に入らないようにする（サーバー側だけで使う）"
GOOGLE_FIX = "Firebase や Maps の公開用キーなら、発行元でリファラ・API の制限がかかっているか確認する。それ以外は無効化して作り直す"
FORBIDDEN_FAIL = re.compile(r"(^|/)(\.env(\.[^/]+)?|id_rsa|id_ed25519|[^/]*\.pem|[^/]*\.key|\.git|\.npmrc|\.htpasswd)$")
FORBIDDEN_WARN = re.compile(r"(^|/)(\.DS_Store|[^/]*\.sqlite3?|[^/]*\.db|[^/]*\.bak|[^/]*~)$")


def check_secrets(name: str, raw: str, blocklist: list[str]) -> list[Finding]:
    out: list[Finding] = []
    for label, pat, sev in SECRET_PATTERNS:
        m = pat.search(raw)
        if m:
            masked = m.group(0)[:6] + "…"
            out.append(Finding("SC01", sev, name, raw.count("\n", 0, m.start()) + 1, f"{label}らしき文字列 {masked}",
                               GOOGLE_FIX if sev == WARN else SECRET_FIX))
    low = raw.lower()
    for word in blocklist:
        i = low.find(word.lower())
        if i >= 0:
            out.append(Finding("SC03", FAIL, name, raw.count("\n", 0, i) + 1,
                               f"ブロックリストの語「{word[:1]}…」（{len(word)}文字）が含まれる",
                               "その語を消すか、言い換える。ビルド前のソースやデータも確認する"))
    return out


def find_secret_values(raw: str) -> set[str]:
    return {m.group(0) for _, pat, _ in SECRET_PATTERNS for m in pat.finditer(raw)}


class Redactor:
    """結果に出す文字列から、見つかった秘密情報とブロックリストの語を伏せる。

    メッセージや抜粋は途中で切られる（見出しは 40 文字、抜粋は前後 18 文字）ので、
    秘密情報は 8 文字以上の断片、ブロックリストの語は切れ目（文字列の端・「…」の隣）に残った 2 文字以上の断片も伏せる。
    """
    MASK = "[伏せ字]"
    SECRET_GRAM = 8

    def __init__(self, secrets: set[str], blocklist: list[str]):
        self.secrets = [s for s in secrets if s]
        self.words = [w.lower() for w in blocklist if w]

    @staticmethod
    def _lower(s: str) -> str:
        return "".join(c.lower() if len(c.lower()) == 1 else c for c in s)  # 長さを変えずに小文字にする

    @staticmethod
    def _edges(s: str, term: str, min_len: int, spans: list) -> None:
        for k in range(len(term) - 1, min_len - 1, -1):
            head, tail = term[:k], term[-k:]
            if s.endswith(head):
                spans.append((len(s) - k, len(s)))
            if s.startswith(tail):
                spans.append((0, k))
            i = s.find(head + "…")
            while i >= 0:
                spans.append((i, i + k))
                i = s.find(head + "…", i + 1)
            i = s.find("…" + tail)
            while i >= 0:
                spans.append((i + 1, i + 1 + k))
                i = s.find("…" + tail, i + 1)

    def apply(self, s: str) -> str:
        if not s or not (self.secrets or self.words):
            return s
        spans: list[tuple[int, int]] = []
        n = self.SECRET_GRAM
        for v in self.secrets:
            grams = {v[i:i + n] for i in range(len(v) - n + 1)}
            spans += [(i, i + n) for i in range(len(s) - n + 1) if s[i:i + n] in grams]
            self._edges(s, v, 4, spans)
        low = self._lower(s)
        for w in self.words:
            i = low.find(w)
            while i >= 0:
                spans.append((i, i + len(w)))
                i = low.find(w, i + 1)
            if len(w) >= 3:
                self._edges(low, w, 2, spans)
        if not spans:
            return s
        out, pos = [], 0
        for a, b in sorted(spans):
            if a < pos:
                if b > pos:
                    pos = b
                continue
            out += [s[pos:a], self.MASK]
            pos = b
        merged = "".join(out) + s[pos:]
        while self.MASK + self.MASK in merged:
            merged = merged.replace(self.MASK + self.MASK, self.MASK)
        return merged

    def finding(self, f: Finding) -> Finding:
        # SC01・SC03 のメッセージは作るときに伏せてある（先頭6文字・1文字目だけ）
        message = f.message if f.rule in ("SC01", "SC03") else self.apply(f.message)
        return replace(f, where=self.apply(f.where), message=message, evidence=self.apply(f.evidence))


def check_forbidden_path(rel: str) -> list[Finding]:
    sev = FAIL if FORBIDDEN_FAIL.search(rel) else WARN if FORBIDDEN_WARN.search(rel) else None
    if sev:
        return [Finding("SC02", sev, rel, None, "公開ディレクトリに置くべきでないファイル",
                        "ビルド成果物から外す（.gitignore だけでは公開を防げない。デプロイ対象のディレクトリから消す）")]
    return []


# ---------------------------------------------------------------- BS: 基本

def _is_error_page(name: str) -> bool:
    return "404" in name or "_not-found" in name or "500" in name


def check_basic(page: Page, min_text: int = 400) -> list[Finding]:
    out: list[Finding] = []
    error_page = _is_error_page(page.name)
    if not page.title:
        out.append(Finding("BS01", WARN, page.name, None, "<title> がない", "検索結果に出る名前を 30 字前後で書く（主な検索語を先に、サイト名は後ろ）"))
    if not error_page and not page.noindex and not page.description:
        out.append(Finding("BS02", WARN, page.name, None, "meta description がない",
                           "ページで分かることを具体的な数字を入れて 80〜120 字で書く"))
    if not error_page and page.h1_count != 1:
        out.append(Finding("BS03", WARN, page.name, None, f"h1 が {page.h1_count} 個",
                           "ページの主題を表す h1 を1つだけにする"))
    if page.lang is None:
        out.append(Finding("BS04", WARN, page.name, None, '<html lang> がない',
                           '<html lang="ja"> を付ける（読み上げ・翻訳・検索が言語を判断する）'))
    n = len(page.visible_text)
    if not error_page and not page.noindex and n < min_text:
        out.append(Finding("BS05", WARN, page.name, None, f"検索に出すページなのに本文が {n} 文字",
                           f"中身を足す（{min_text} 文字が目安）か、まだ載せないなら <meta name=\"robots\" content=\"noindex\"> を付ける"))
    return out

