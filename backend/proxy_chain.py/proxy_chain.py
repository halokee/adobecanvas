"""Local relay for HTTP CONNECT -> SOCKS5 proxy chains.

The relay only binds to 127.0.0.1.  Clients speak SOCKS5 to the relay; each
connection is first tunneled through the configured local HTTP proxy to the
remote SOCKS5 endpoint, then the SOCKS5 negotiation is forwarded unchanged.
"""
from __future__ import annotations

import atexit
import base64
import logging
import select
import socket
import socketserver
import threading
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote, unquote, urlsplit


logger = logging.getLogger("local-canvas")

_CONNECT_TIMEOUT = 20
_MAX_HTTP_RESPONSE_BYTES = 64 * 1024


class ProxyChainError(RuntimeError):
    """Raised when the local HTTP-to-SOCKS5 chain cannot be initialized."""


class _TrafficStats:
    """Thread-safe counters for bytes forwarded by the local relay."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started_at = int(time.time())
        self._upload_bytes = 0
        self._download_bytes = 0
        self._connections = 0
        self._active_connections = 0

    def connection_opened(self) -> None:
        with self._lock:
            self._connections += 1
            self._active_connections += 1

    def connection_closed(self) -> None:
        with self._lock:
            self._active_connections = max(0, self._active_connections - 1)

    def record_upload(self, byte_count: int) -> None:
        with self._lock:
            self._upload_bytes += byte_count

    def record_download(self, byte_count: int) -> None:
        with self._lock:
            self._download_bytes += byte_count

    def snapshot(self, *, running: bool) -> dict[str, int | bool]:
        with self._lock:
            upload_bytes = self._upload_bytes
            download_bytes = self._download_bytes
            return {
                "upload_bytes": upload_bytes,
                "download_bytes": download_bytes,
                "total_bytes": upload_bytes + download_bytes,
                "connections": self._connections,
                "active_connections": self._active_connections,
                "started_at": self._started_at,
                "running": running,
            }


@dataclass(frozen=True)
class _HttpProxy:
    host: str
    port: int
    authorization: str = ""


@dataclass(frozen=True)
class _SocksProxy:
    scheme: str
    host: str
    port: int
    username: Optional[str]
    password: Optional[str]


def validate_http_connect_proxy(value: object) -> str:
    """Validate the HTTP first hop used for a SOCKS5 proxy chain."""
    proxy = str(value or "").strip()
    if not proxy:
        return ""

    parsed = urlsplit(proxy)
    if parsed.scheme.lower() != "http":
        raise ValueError("链式代理的本地代理必须以 http:// 开头")
    if not parsed.hostname:
        raise ValueError("链式代理的本地 HTTP 代理缺少主机地址")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("链式代理的本地 HTTP 代理端口无效") from exc
    if port is None or not 1 <= port <= 65535:
        raise ValueError("链式代理的本地 HTTP 代理必须包含 1-65535 之间的端口")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("链式代理的本地 HTTP 代理地址不能包含路径、查询参数或片段")
    return proxy


def _parse_http_proxy(value: str) -> _HttpProxy:
    proxy = validate_http_connect_proxy(value)
    if not proxy:
        raise ProxyChainError("链式代理缺少本地 HTTP 代理地址")
    parsed = urlsplit(proxy)
    host = parsed.hostname
    port = parsed.port
    if not host or port is None:
        raise ProxyChainError("链式代理的本地 HTTP 代理地址无效")

    authorization = ""
    if parsed.username is not None:
        username = unquote(parsed.username)
        password = unquote(parsed.password or "")
        raw = f"{username}:{password}".encode("utf-8")
        authorization = "Basic " + base64.b64encode(raw).decode("ascii")
    return _HttpProxy(host=host, port=port, authorization=authorization)


def _parse_socks_proxy(value: str) -> _SocksProxy:
    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    if scheme not in {"socks5", "socks5h"}:
        raise ProxyChainError("链式代理的上游必须是 SOCKS5 地址")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ProxyChainError("链式代理的上游 SOCKS5 端口无效") from exc
    if not parsed.hostname or port is None:
        raise ProxyChainError("链式代理的上游 SOCKS5 地址无效")
    return _SocksProxy(
        scheme=scheme,
        host=parsed.hostname,
        port=port,
        username=unquote(parsed.username) if parsed.username is not None else None,
        password=unquote(parsed.password) if parsed.password is not None else None,
    )


def _format_authority(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def _read_http_response(sock: socket.socket) -> tuple[int, bytes]:
    data = bytearray()
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            raise ProxyChainError("本地 HTTP 代理在 CONNECT 响应前关闭了连接")
        data.extend(chunk)
        if len(data) > _MAX_HTTP_RESPONSE_BYTES:
            raise ProxyChainError("本地 HTTP 代理的 CONNECT 响应过大")

    raw_headers, remaining = bytes(data).split(b"\r\n\r\n", 1)
    first_line = raw_headers.split(b"\r\n", 1)[0].decode("iso-8859-1", errors="replace")
    parts = first_line.split(" ", 2)
    if len(parts) < 2 or not parts[0].startswith("HTTP/"):
        raise ProxyChainError("本地 HTTP 代理返回了无效的 CONNECT 响应")
    try:
        status = int(parts[1])
    except ValueError as exc:
        raise ProxyChainError("本地 HTTP 代理返回了无效的 CONNECT 状态") from exc
    return status, remaining


def _relay_bidirectionally(
    left: socket.socket,
    right: socket.socket,
    traffic: Optional[_TrafficStats] = None,
) -> None:
    sockets = (left, right)
    while True:
        readable, _, failed = select.select(sockets, (), sockets, 60)
        if failed:
            return
        if not readable:
            continue
        for source in readable:
            data = source.recv(64 * 1024)
            if not data:
                return
            target = right if source is left else left
            target.sendall(data)
            if traffic is not None:
                if source is left:
                    traffic.record_upload(len(data))
                else:
                    traffic.record_download(len(data))


class _RelayServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _RelayHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server: _RelayServer = self.server  # type: ignore[assignment]
        local_proxy: _HttpProxy = server.local_proxy  # type: ignore[attr-defined]
        remote_proxy: _SocksProxy = server.remote_proxy  # type: ignore[attr-defined]
        traffic: _TrafficStats = server.traffic  # type: ignore[attr-defined]
        upstream: Optional[socket.socket] = None
        traffic.connection_opened()
        try:
            upstream = socket.create_connection((local_proxy.host, local_proxy.port), timeout=_CONNECT_TIMEOUT)
            authority = _format_authority(remote_proxy.host, remote_proxy.port)
            lines = [
                f"CONNECT {authority} HTTP/1.1",
                f"Host: {authority}",
                "Proxy-Connection: Keep-Alive",
            ]
            if local_proxy.authorization:
                lines.append(f"Proxy-Authorization: {local_proxy.authorization}")
            upstream.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("iso-8859-1"))
            status, remaining = _read_http_response(upstream)
            if not 200 <= status < 300:
                raise ProxyChainError(f"本地 HTTP 代理拒绝连接上游 SOCKS5（HTTP {status}）")
            upstream.settimeout(None)
            if remaining:
                self.request.sendall(remaining)
                traffic.record_download(len(remaining))
            _relay_bidirectionally(self.request, upstream, traffic)
        except (OSError, ProxyChainError) as exc:
            logger.debug("proxy chain connection failed: %s", exc)
        finally:
            traffic.connection_closed()
            if upstream is not None:
                try:
                    upstream.close()
                except OSError:
                    pass


class ChainedSocks5Relay:
    """Keeps a local SOCKS5 relay running for the active proxy-chain config."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._server: Optional[_RelayServer] = None
        self._thread: Optional[threading.Thread] = None
        self._fingerprint: Optional[tuple[str, str]] = None
        self._traffic = _TrafficStats()

    def ensure(self, local_http_proxy: str, remote_socks_proxy: str) -> str:
        fingerprint = (local_http_proxy.strip(), remote_socks_proxy.strip())
        with self._lock:
            if self._server is not None and self._fingerprint == fingerprint:
                return self._local_socks_url(remote_socks_proxy, self._server.server_address[1])

            self._stop_locked()
            local_proxy = _parse_http_proxy(local_http_proxy)
            remote_proxy = _parse_socks_proxy(remote_socks_proxy)
            server = _RelayServer(("127.0.0.1", 0), _RelayHandler)
            server.local_proxy = local_proxy  # type: ignore[attr-defined]
            server.remote_proxy = remote_proxy  # type: ignore[attr-defined]
            server.traffic = self._traffic  # type: ignore[attr-defined]
            thread = threading.Thread(
                target=server.serve_forever,
                name="local-canvas-proxy-chain",
                daemon=True,
            )
            thread.start()
            self._server = server
            self._thread = thread
            self._fingerprint = fingerprint
            return self._local_socks_url(remote_socks_proxy, server.server_address[1])

    def invalidate(self) -> None:
        with self._lock:
            self._stop_locked()

    def traffic_snapshot(self) -> dict[str, int | bool]:
        """Return traffic sent through this relay since the backend started."""
        with self._lock:
            running = self._server is not None
        return self._traffic.snapshot(running=running)

    def _stop_locked(self) -> None:
        server = self._server
        self._server = None
        self._thread = None
        self._fingerprint = None
        if server is None:
            return
        server.shutdown()
        server.server_close()

    @staticmethod
    def _local_socks_url(remote_socks_proxy: str, port: int) -> str:
        remote = _parse_socks_proxy(remote_socks_proxy)
        credentials = ""
        if remote.username is not None:
            username = quote(remote.username, safe="")
            password = quote(remote.password or "", safe="")
            credentials = f"{username}:{password}@"
        return f"{remote.scheme}://{credentials}127.0.0.1:{port}"


chained_socks5_relay = ChainedSocks5Relay()
atexit.register(chained_socks5_relay.invalidate)
