"""HTML を読み、検査に必要な情報（見える文字・見出し・リンク・タグ）を行番号つきで集める。標準ライブラリのみ。"""
from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field
from html.parser import HTMLParser

HIDDEN_TAGS = {"script", "style", "template", "noscript", "svg"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
# 英語だけのラベルを探す対象（本文全体だと固有名詞で誤検知が多いので、見出し・ボタン類に絞る）
LABEL_TAGS = HEADING_TAGS | {"button", "label", "th", "summary", "legend"}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
# 文字の流れを切らない要素。これらで分かれた文字（Na<span>N</span>）は1つの文字列としてつなげて検査する
INLINE_TAGS = {"a", "abbr", "b", "bdi", "bdo", "big", "cite", "code", "data", "del", "dfn", "em", "font", "i", "ins", "kbd",
               "mark", "nobr", "q", "s", "samp", "small", "span", "strong", "sub", "sup", "time", "tt", "u", "var"}
BLOCK_STYLE = re.compile(r"(?:^|;)\s*display\s*:\s*(?:block|flex|grid|table|list-item|flow-root)", re.I)
# 画面に出ない要素（hidden 属性、style の display:none / visibility:hidden）。aria-hidden は見た目では出るので対象外
HIDE_STYLE = re.compile(r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*(?:hidden|collapse))\s*(?:!important\s*)?(?:;|$)", re.I)
# 終了タグが省略されたときに暗黙に閉じる要素
P_CLOSERS = {"address", "article", "aside", "blockquote", "details", "div", "dl", "fieldset", "figcaption", "figure", "footer",
             "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "main", "nav", "ol", "p", "pre", "section", "table", "ul"}
IMPLIED_END = {"li": {"li"}, "dt": {"dt", "dd"}, "dd": {"dt", "dd"}, "option": {"option"},
               "tr": {"tr", "td", "th"}, "td": {"td", "th"}, "th": {"td", "th"}}


@dataclass
class Text:
    """インライン要素をまたいでつなげた、見える文字のひと続き（段落・見出し・セルなどの単位）。"""
    text: str  # 空白を詰める前の生の文字列
    line: int
    order: int  # ページ内の出現順（リンクとの前後比較に使う）
    segments: list[tuple[int, int, int]] = field(default_factory=list)  # (text 内の開始位置, 行, 出現順)


@dataclass
class Element:
    tag: str
    text: str
    line: int


@dataclass
class Link:
    href: str
    attrs: dict
    line: int
    order: int
    text: str = ""


@dataclass
class Page:
    name: str  # 表示用のパスまたは URL
    raw: str
    lang: str | None = None
    title: str | None = None
    description: str | None = None
    noindex: bool = False
    texts: list[Text] = field(default_factory=list)
    labels: list[Element] = field(default_factory=list)  # LABEL_TAGS の要素
    links: list[Link] = field(default_factory=list)
    stylesheets: list[str] = field(default_factory=list)
    script_srcs: list[str] = field(default_factory=list)
    inline_scripts: list[tuple[str, int]] = field(default_factory=list)
    styles: list[tuple[str, int]] = field(default_factory=list)  # <style> と style 属性
    class_attrs: list[tuple[str, int]] = field(default_factory=list)
    form_actions: list[tuple[str, int]] = field(default_factory=list)
    url: str | None = None  # ページの URL（URL 指定時はリダイレクト後、ディレクトリ指定時は --base-url から組み立てる）
    input_count: int = 0
    h1_count: int = 0

    @property
    def visible_text(self) -> str:
        return " ".join(" ".join(t.text.split()) for t in self.texts if t.text.strip())

    @property
    def is_japanese(self) -> bool:
        return self.lang is None or self.lang.lower().startswith("ja")


class _Parser(HTMLParser):
    def __init__(self, page: Page):
        super().__init__(convert_charrefs=True)
        self.p = page
        self.hidden = 0  # script・style など中身が文字として出ない要素の深さ
        self.css_hidden = 0  # hidden 属性・display:none の祖先の数
        self.stack: list[tuple[str, bool]] = []  # 開いている要素と、それが隠す要素か
        self.run: list[tuple[str, int, int]] = []  # つなげ中の文字 (data, 行, 出現順)
        self.after_inline_end = False  # 直前がインライン要素の終了タグ（間に文字なし）
        self.order = 0
        self.in_title = False
        self.title_parts: list[str] = []
        self.captures: list[list] = []  # [tag, line, parts]
        self.open_links: list[Link] = []

    def _flush(self):
        run, self.run = self.run, []
        if not run or not "".join(d for d, _, _ in run).strip():
            return
        text, segments, order = "", [], None
        for data, line, o in run:
            segments.append((len(text), line, o))
            text += data
            if order is None and data.strip():
                order = o
        self.p.texts.append(Text(text, run[0][1], order, segments))

    def _pop(self):
        _, hides, _block = self.stack.pop()
        if hides:
            self.css_hidden -= 1

    def _open(self, tag, a):
        if tag in IMPLIED_END:
            while self.stack and self.stack[-1][0] in IMPLIED_END[tag]:
                self._pop()
        if tag in P_CLOSERS:
            # A block start closes an open <p> even through inline descendants (<p hidden><span>x<div>…).
            for i in range(len(self.stack) - 1, -1, -1):
                t = self.stack[i][0]
                if t == "p":
                    while len(self.stack) > i:
                        self._pop()
                    break
                if t not in INLINE_TAGS:
                    break
        if tag in VOID_TAGS:
            return
        hides = "hidden" in a or bool(HIDE_STYLE.search(a.get("style", "")))
        block = tag not in INLINE_TAGS or bool(BLOCK_STYLE.search(a.get("style", "")))
        self.stack.append((tag, hides, block))
        if hides:
            self.css_hidden += 1

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        line = self.getpos()[0]
        # </span><span> のように要素が文字をはさまず並ぶときは、CSS で縦に並べた別の項目のことが多いので切る
        # （例: 価格表の「¥500</span><span class="sub">1.5 m」を「¥5001.5」と読まない）
        if tag not in INLINE_TAGS or self.after_inline_end or BLOCK_STYLE.search(a.get("style", "")):
            self._flush()
        self.after_inline_end = False
        self._open(tag, a)
        if tag in HIDDEN_TAGS:
            self.hidden += 1
            if tag == "script":
                if a.get("src"):
                    self.p.script_srcs.append(a["src"])
        if tag == "html" and a.get("lang"):
            self.p.lang = a["lang"].strip()
        elif tag == "title" and not self.hidden:
            self.in_title = True
        elif tag == "meta":
            name = a.get("name", "").lower()
            if name == "description":
                self.p.description = a.get("content", "")
            elif name == "robots" and "noindex" in a.get("content", "").lower():
                self.p.noindex = True
        elif tag == "link" and "stylesheet" in a.get("rel", "").lower() and a.get("href"):
            self.p.stylesheets.append(a["href"])
        elif tag == "a" and a.get("href"):
            self.order += 1
            link = Link(a["href"], a, line, self.order)
            self.p.links.append(link)
            self.open_links.append(link)
        elif tag == "form" and a.get("action"):
            self.p.form_actions.append((a["action"], line))
        if tag in ("input", "select", "textarea"):
            if a.get("type", "").lower() not in ("hidden", "submit", "button", "reset", "search", "image"):
                self.p.input_count += 1
        if tag == "h1":
            self.p.h1_count += 1
        if a.get("style"):
            self.p.styles.append((a["style"], line))
        if a.get("class"):
            self.p.class_attrs.append((a["class"], line))
        if tag in LABEL_TAGS | {"a", "button"} and not (self.hidden or self.css_hidden):
            self.captures.append([tag, line, []])

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        # An inline tag styled as a block (style="display:block") also ends a text run when it closes.
        open_block = next((e[2] for e in reversed(self.stack) if e[0] == tag), False)
        if tag not in INLINE_TAGS or open_block:
            self._flush()
        self.after_inline_end = tag in INLINE_TAGS
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                while len(self.stack) > i:
                    self._pop()
                break
        if tag in HIDDEN_TAGS:
            self.hidden = max(0, self.hidden - 1)
        if tag == "title" and self.in_title:
            self.in_title = False
            self.p.title = " ".join("".join(self.title_parts).split())
        if tag == "a" and self.open_links:
            self.open_links.pop()
        for i in range(len(self.captures) - 1, -1, -1):
            if self.captures[i][0] == tag:
                t, line, parts = self.captures.pop(i)
                text = " ".join("".join(parts).split())
                if text:
                    self.p.labels.append(Element(t, text, line))
                break

    def handle_data(self, data):
        line = self.getpos()[0]
        if self.in_title:
            self.title_parts.append(data)
            return
        if self.hidden:
            last = self.lasttag
            if last == "style":
                self.p.styles.append((data, line))
            elif last == "script" and data.strip():
                self.p.inline_scripts.append((data, line))
            return
        if not data:
            return
        self.after_inline_end = False
        if self.css_hidden:
            return
        if not data.strip():
            self.run.append((data, line, self.order))
            return
        self.order += 1
        self.run.append((data, line, self.order))
        for cap in self.captures:
            cap[2].append(data)
        for link in self.open_links:
            link.text += data


def parse(name: str, raw: str) -> Page:
    page = Page(name=name, raw=raw)
    parser = _Parser(page)
    try:
        parser.feed(raw)
        parser.close()
    except Exception:  # 壊れた HTML でも、読めたところまでで検査する
        pass
    parser._flush()
    return page


def _segment(text: Text, index: int) -> tuple[int, int, int]:
    if not text.segments:
        return 0, text.line, text.order
    i = bisect_right([s[0] for s in text.segments], index) - 1
    return text.segments[max(i, 0)]


def line_of(text: Text, index: int) -> int:
    """Text 内の文字位置 index が何行目かを返す。"""
    start, line, _ = _segment(text, index)
    return line + text.text.count("\n", start, index)


def order_of(text: Text, index: int) -> int:
    """Text 内の文字位置 index のページ内の出現順（リンクとの前後比較に使う）。"""
    return _segment(text, index)[2]


def snippet(s: str, start: int, end: int, width: int = 18) -> str:
    left = " ".join(s[max(0, start - width):start].split())
    mid = s[start:end]
    right = " ".join(s[end:end + width].split())
    return f"…{left}【{mid}】{right}…"
