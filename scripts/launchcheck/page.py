"""HTML を読み、検査に必要な情報（見える文字・見出し・リンク・タグ）を行番号つきで集める。標準ライブラリのみ。"""
from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser

HIDDEN_TAGS = {"script", "style", "template", "noscript", "svg"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
# 英語だけのラベルを探す対象（本文全体だと固有名詞で誤検知が多いので、見出し・ボタン類に絞る）
LABEL_TAGS = HEADING_TAGS | {"button", "label", "th", "summary", "legend"}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


@dataclass
class Text:
    text: str  # 空白を詰める前の生の文字列
    line: int
    order: int  # ページ内の出現順（リンクとの前後比較に使う）


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
        self.hidden = 0
        self.order = 0
        self.in_title = False
        self.title_parts: list[str] = []
        self.captures: list[list] = []  # [tag, line, parts]
        self.open_links: list[Link] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        line = self.getpos()[0]
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
        if tag in LABEL_TAGS and not self.hidden:
            self.captures.append([tag, line, []])
        elif tag in ("a", "button") and not self.hidden:
            self.captures.append([tag, line, []])

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
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
        if not data.strip():
            return
        self.order += 1
        self.p.texts.append(Text(data, line, self.order))
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
    return page


def line_of(text: Text, index: int) -> int:
    """Text 内の文字位置 index が何行目かを返す。"""
    return text.line + text.text.count("\n", 0, index)


def snippet(s: str, start: int, end: int, width: int = 18) -> str:
    left = " ".join(s[max(0, start - width):start].split())
    mid = s[start:end]
    right = " ".join(s[end:end + width].split())
    return f"…{left}【{mid}】{right}…"
