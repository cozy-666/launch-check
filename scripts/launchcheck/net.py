"""URL の取得。http / https だけを、公開アドレスにだけ取りに行く（SSRF 対策）。

- 接続する直前に名前解決した IP を確かめる（私的・ループバック・リンクローカル・メタデータなど公開でないアドレスは拒否）。
  名前解決の結果が途中で変わっても（DNS の付け替え）、実際に接続する IP で判定する。
- リダイレクトは1回ごとに同じ検査をする。CSS・JS はページと同じオリジンへのリダイレクトだけたどる。
- 環境変数のプロキシ、file:// ftp:// data: は使わない。
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from . import __version__

MAX_BYTES = 5 * 1024 * 1024
UA = f"launch-check/{__version__} (pre-launch site check)"
DEFAULT_PORTS = {"http": 80, "https": 443}
PRIVATE_HINT = "手元のサーバーを検査するときは --allow-private を付ける"


class BlockedURL(OSError):
    """取りに行かない URL（http/https 以外・公開でないアドレス・形式の誤り）。"""


class CrossOrigin(BlockedURL):
    """CSS・JS のリダイレクト先がページと別のオリジン。"""


def origin(url: str) -> tuple[str, str, int | None]:
    """(scheme, host, port)。形式が壊れていれば ValueError。"""
    p = urlsplit(url)
    scheme = p.scheme.lower()
    return scheme, (p.hostname or "").lower(), p.port or DEFAULT_PORTS.get(scheme)


def ip_is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%", 1)[0])
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


def check_url(url: str) -> None:
    try:
        p = urlsplit(url)
        host = p.hostname
        p.port  # noqa: B018 - ポート番号の誤りをここで出す
    except ValueError as e:
        raise BlockedURL(f"URL の形式が正しくない（{e}）") from None
    if p.scheme.lower() not in ("http", "https"):
        raise BlockedURL(f"http / https 以外は取得しない（{p.scheme or 'スキームなし'}）")
    if not host:
        raise BlockedURL("URL にホスト名がない")


def _connector(allow_private: bool):
    def create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, *a, **k):
        host, port = address
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not allow_private:
            bad = [info[4][0] for info in infos if not ip_is_public(info[4][0])]
            if bad:
                raise BlockedURL(f"{host} は公開されていないアドレス（{bad[0]}）なので取得しない。{PRIVATE_HINT}")
        err: OSError | None = None
        for family, kind, proto, _, sockaddr in infos:
            sock = socket.socket(family, kind, proto)
            try:
                if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                    sock.settimeout(timeout)
                if source_address:
                    sock.bind(source_address)
                sock.connect(sockaddr)
                return sock
            except OSError as e:
                err = e
                sock.close()
        raise err or OSError(f"{host} に接続できない")
    return create_connection


class _HTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, allow_private: bool):
        super().__init__()
        self.allow_private = allow_private

    def _conn(self, host, **kw):
        c = http.client.HTTPConnection(host, **kw)
        c._create_connection = _connector(self.allow_private)
        return c

    def http_open(self, req):
        return self.do_open(self._conn, req)


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, allow_private: bool):
        super().__init__()
        self.allow_private = allow_private

    def _conn(self, host, **kw):
        c = http.client.HTTPSConnection(host, **kw)
        c._create_connection = _connector(self.allow_private)
        return c

    def https_open(self, req):
        return self.do_open(self._conn, req, context=self._context)


class _RedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, same_origin: tuple | None):
        super().__init__()
        self.same_origin = same_origin

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        if self.same_origin and origin(newurl) != self.same_origin:
            raise CrossOrigin(f"リダイレクト先が別のオリジン（{newurl[:80]}）なので取得しない")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def make_opener(allow_private: bool, same_origin: tuple | None = None) -> urllib.request.OpenerDirector:
    opener = urllib.request.OpenerDirector()
    for h in (_HTTPHandler(allow_private), _HTTPSHandler(allow_private), _RedirectHandler(same_origin),
              urllib.request.HTTPDefaultErrorHandler(), urllib.request.HTTPErrorProcessor(), urllib.request.UnknownHandler()):
        opener.add_handler(h)
    return opener


def fetch(url: str, allow_private: bool = False, same_origin: tuple | None = None, timeout: float = 20.0) -> tuple[str, str]:
    """本文（文字列）と、リダイレクト後の最終 URL を返す。取りに行かない URL なら BlockedURL。"""
    check_url(url)
    if same_origin and origin(url) != same_origin:
        raise CrossOrigin(f"別のオリジン（{url[:80]}）なので取得しない")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with make_opener(allow_private, same_origin).open(req, timeout=timeout) as res:
            body = res.read(MAX_BYTES)
            charset = res.headers.get_content_charset() or "utf-8"
            final = res.geturl()
    except urllib.error.URLError as e:
        if isinstance(e.reason, BlockedURL):
            raise e.reason from None
        raise
    return body.decode(charset, errors="replace"), final
