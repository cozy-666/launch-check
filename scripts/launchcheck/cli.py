"""コマンドライン。ビルド済みの静的ディレクトリか URL の一覧を検査し、問題があれば終了コード 1 を返す。"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin, urlparse

from . import __version__
from .page import Page, parse
from .rules import (FAIL, WARN, Finding, check_ai, check_basic, check_css_file, check_forbidden_path, check_legal,
                    check_qa, check_secrets, same_host, site_legal)

CATEGORIES = ("ai", "qa", "legal", "secret", "basic")
TEXT_EXT = {".html", ".htm", ".js", ".mjs", ".cjs", ".json", ".txt", ".xml", ".map", ".css", ".md", ".csv", ".yml", ".yaml"}
MAX_BYTES = 5 * 1024 * 1024
UA = f"launch-check/{__version__} (pre-launch site check)"


def load_blocklist(path: str | None) -> list[str]:
    if not path:
        return []
    words = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        w = line.strip()
        if w and not w.startswith("#"):
            words.append(w)
    return words


def fetch(url: str, timeout: float = 20.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        body = res.read(MAX_BYTES)
        charset = res.headers.get_content_charset() or "utf-8"
    return body.decode(charset, errors="replace")


def run_checks(pages: list[Page], css_files: list[tuple[str, str]], text_files: list[tuple[str, str]],
               paths: list[str], args) -> list[Finding]:
    cats = set(args.only.split(",")) if args.only else set(CATEGORIES)
    blocklist = load_blocklist(args.blocklist)
    extra_hosts = tuple(args.affiliate_host or ())
    found: list[Finding] = []
    for page in pages:
        if "ai" in cats:
            found += check_ai(page)
        if "qa" in cats:
            found += check_qa(page)
        if "legal" in cats:
            found += check_legal(page, extra_hosts)
        if "basic" in cats:
            found += check_basic(page, args.min_text)
    if "ai" in cats:
        for name, css in css_files:
            found += check_css_file(name, css)
    if "legal" in cats:
        found += site_legal(pages)
    if "secret" in cats:
        for name, raw in text_files:
            found += check_secrets(name, raw, blocklist)
        for rel in paths:
            found += check_forbidden_path(rel)
    ignore = set(args.ignore or ())
    return [f for f in found if f.rule not in ignore]


def collect_dir(root: Path):
    pages, css_files, text_files, paths = [], [], [], []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        parts = rel.split("/")
        if ".git" in parts[:-1] or "node_modules" in parts[:-1]:
            continue  # .git ディレクトリは中身ではなくディレクトリ自体を1回だけ報告する
        if p.is_dir():
            if parts[-1] == ".git":
                paths.append(rel)
            continue
        paths.append(rel)
        if p.suffix.lower() not in TEXT_EXT or p.stat().st_size > MAX_BYTES:
            continue
        raw = p.read_text(encoding="utf-8", errors="replace")
        text_files.append((rel, raw))
        if p.suffix.lower() in (".html", ".htm"):
            pages.append(parse(rel, raw))
        elif p.suffix.lower() == ".css":
            css_files.append((rel, raw))
    return pages, css_files, text_files, sorted(set(paths))


def collect_urls(urls: list[str]):
    pages, css_files, text_files, errors = [], [], [], []
    seen_assets: set[str] = set()
    for url in urls:
        try:
            raw = fetch(url)
        except Exception as e:  # noqa: BLE001 - 失敗は一覧に出して続ける
            errors.append(Finding("BS00", FAIL, url, None, f"取得できない: {e}", "URL・公開設定・認証を確認する"))
            continue
        page = parse(url, raw)
        pages.append(page)
        text_files.append((url, raw))
        assets = [(h, "css") for h in page.stylesheets[:5]] + [(s, "js") for s in page.script_srcs[:10]]
        for href, kind in assets:
            full = urljoin(url, href)
            if full in seen_assets or not same_host(url, full) or urlparse(full).scheme not in ("http", "https"):
                continue
            seen_assets.add(full)
            try:
                body = fetch(full)
            except Exception:  # noqa: BLE001
                continue
            text_files.append((full, body))
            if kind == "css":
                css_files.append((full, body))
    return pages, css_files, text_files, errors


def print_report(found: list[Finding], n_pages: int, max_per_rule: int, strict: bool) -> None:
    by_rule: dict[tuple[str, str], int] = defaultdict(int)
    hidden: dict[str, int] = defaultdict(int)
    order = {FAIL: 0, WARN: 1}
    for f in sorted(found, key=lambda x: (order[x.severity], x.rule, x.where, x.line or 0)):
        key = (f.rule, f.severity)
        by_rule[key] += 1
        if max_per_rule and by_rule[key] > max_per_rule:
            hidden[f"{f.severity} {f.rule}"] += 1
            continue
        loc = f"{f.where}:{f.line}" if f.line else f.where
        print(f"{f.severity} [{f.rule}] {loc}  {f.message}")
        if f.evidence:
            print(f"      該当: {f.evidence}")
        print(f"      直し方: {f.fix}")
    for k, n in hidden.items():
        print(f"（{k} はほかに {n} 件。--max-per-rule 0 ですべて表示）")
    fails = sum(f.severity == FAIL for f in found)
    warns = sum(f.severity == WARN for f in found)
    blocked = fails or (strict and warns)
    print()
    print(f"launch-check: {'要修正' if blocked else '通過'}（{n_pages} ページ / FAIL {fails} 件 / WARN {warns} 件"
          f"{' / --strict のため WARN も不合格' if strict and warns and not fails else ''}）")
    if found:
        counts = defaultdict(int)
        for f in found:
            counts[f"{f.rule} {f.severity}"] += 1
        print("内訳: " + "、".join(f"{k} {v}" for k, v in sorted(counts.items())))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="launch_check.py",
        description="日本語サイトの公開前チェック。ビルド済みディレクトリか URL を検査する。")
    ap.add_argument("targets", nargs="*", help="ビルド済みディレクトリ1つ、または http(s) の URL（複数可）")
    ap.add_argument("--urls", help="URL を1行に1つ書いたファイル")
    ap.add_argument("--only", help=f"検査する分類をカンマ区切りで（{','.join(CATEGORIES)}）")
    ap.add_argument("--ignore", action="append", metavar="RULE", help="無視するルール ID（例: --ignore AI03）。くり返し指定可")
    ap.add_argument("--blocklist", help="公開物に出てはいけない語を1行1語で書いたファイル（社名・本名など）。リポジトリに入れない場所に置く")
    ap.add_argument("--affiliate-host", action="append", metavar="HOST", help="広告リンクとして扱うドメインを追加")
    ap.add_argument("--min-text", type=int, default=400, help="本文が少ないページの基準文字数（既定 400）")
    ap.add_argument("--strict", action="store_true", help="WARN も不合格にする")
    ap.add_argument("--json", action="store_true", help="結果を JSON で出す")
    ap.add_argument("--max-per-rule", type=int, default=20, help="同じルール・重さの表示件数の上限（0 で無制限、既定 20）")
    ap.add_argument("--version", action="version", version=f"launch-check {__version__}")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    urls = [t for t in args.targets if t.startswith(("http://", "https://"))]
    dirs = [t for t in args.targets if t not in urls]
    if args.urls:
        urls += [l.strip() for l in Path(args.urls).read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.strip().startswith("#")]
    if args.only:
        bad = set(args.only.split(",")) - set(CATEGORIES)
        if bad:
            print(f"--only に使えない分類: {','.join(sorted(bad))}（使えるもの: {','.join(CATEGORIES)}）", file=sys.stderr)
            return 2
    if (dirs and urls) or len(dirs) > 1 or not (dirs or urls):
        print("ディレクトリ1つ、または URL を指定してください。例: launch_check.py ./out  /  launch_check.py --urls urls.txt",
              file=sys.stderr)
        return 2
    if args.blocklist and not Path(args.blocklist).is_file():
        print(f"ブロックリストが見つからない: {args.blocklist}", file=sys.stderr)
        return 2

    if dirs:
        root = Path(dirs[0])
        if not root.is_dir():
            print(f"ディレクトリが見つからない: {root}", file=sys.stderr)
            return 2
        pages, css_files, text_files, paths = collect_dir(root)
        found = run_checks(pages, css_files, text_files, paths, args)
    else:
        pages, css_files, text_files, errors = collect_urls(urls)
        found = errors + run_checks(pages, css_files, text_files, [], args)

    if not pages and not found:
        print("HTML が1つも見つからない。ビルド後の出力ディレクトリ（out/ dist/ build/ など）を指定してください。", file=sys.stderr)
        return 2
    fails = any(f.severity == FAIL for f in found)
    warns = any(f.severity == WARN for f in found)
    if args.json:
        print(json.dumps({"pages": len(pages), "findings": [f.as_dict() for f in found],
                          "passed": not (fails or (args.strict and warns))}, ensure_ascii=False, indent=2))
    else:
        if not args.blocklist and (not args.only or "secret" in args.only):
            print("（ブロックリスト未指定: 社名・本名などの混入は検査していない。--blocklist で指定できる）")
        print_report(found, len(pages), args.max_per_rule, args.strict)
    return 1 if fails or (args.strict and warns) else 0
