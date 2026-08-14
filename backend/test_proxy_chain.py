"""Regression coverage for the local HTTP CONNECT -> SOCKS5 relay."""
from __future__ import annotations

import select
import socket
import socketserver
import struct
import threading
import unittest

import requests

from backend.config_manager import (
    config_manager,
    get_active_proxy,
    get_proxy_mode,
    get_requests_proxies,
    invalidate_proxy_chain,
    proxy_mode_settings,
)
from backend.proxy_chain import ChainedSocks5Relay, validate_http_connect_proxy


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("peer closed the connection")
        data.extend(chunk)
    return bytes(data)


def _recv_headers(sock: socket.socket) -> bytes:
    data = bytearray()
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("peer closed the connection")
        data.extend(chunk)
        if len(data) > 64 * 1024:
            raise ValueError("headers are too large")
    return bytes(data)


def _bridge(left: socket.socket, right: socket.socket) -> None:
    while True:
        readable, _, failed = select.select((left, right), (), (left, right), 5)
        if failed or not readable:
            return
        for source in readable:
            data = source.recv(64 * 1024)
            if not data:
                return
            (right if source is left else left).sendall(data)


class _ThreadingServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _TargetHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.server.requests.append(_recv_headers(self.request))  # type: ignore[attr-defined]
        body = b"chain-ok"
        self.request.sendall(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/plain\r\n"
            b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body
        )


class _Socks5Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        sock = self.request
        version, count = _recv_exact(sock, 2)
        methods = _recv_exact(sock, count)
        self.server.events.append("socks-greeting")  # type: ignore[attr-defined]
        if version != 5 or 2 not in methods:
            raise AssertionError("SOCKS5 username/password authentication was not requested")
        sock.sendall(b"\x05\x02")

        auth_version = _recv_exact(sock, 1)[0]
        username = _recv_exact(sock, _recv_exact(sock, 1)[0]).decode("utf-8")
        password = _recv_exact(sock, _recv_exact(sock, 1)[0]).decode("utf-8")
        expected = self.server.credentials  # type: ignore[attr-defined]
        if auth_version != 1 or (username, password) != expected:
            sock.sendall(b"\x01\x01")
            return
        self.server.events.append("socks-auth")  # type: ignore[attr-defined]
        sock.sendall(b"\x01\x00")

        version, command, _, address_type = _recv_exact(sock, 4)
        if version != 5 or command != 1:
            raise AssertionError("SOCKS5 CONNECT was not requested")
        if address_type == 1:
            host = socket.inet_ntoa(_recv_exact(sock, 4))
        elif address_type == 3:
            host = _recv_exact(sock, _recv_exact(sock, 1)[0]).decode("idna")
        elif address_type == 4:
            host = socket.inet_ntop(socket.AF_INET6, _recv_exact(sock, 16))
        else:
            raise AssertionError("unknown SOCKS5 address type")
        port = struct.unpack("!H", _recv_exact(sock, 2))[0]
        self.server.events.append(("socks-connect", host, port))  # type: ignore[attr-defined]

        target = socket.create_connection((host, port), timeout=5)
        try:
            sock.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            _bridge(sock, target)
        finally:
            target.close()


class _HttpConnectHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        request = _recv_headers(self.request)
        self.server.headers.append(request)  # type: ignore[attr-defined]
        first_line = request.split(b"\r\n", 1)[0].decode("ascii")
        self.server.events.append(first_line)  # type: ignore[attr-defined]
        _, authority, _ = first_line.split(" ", 2)
        expected = self.server.remote_authority  # type: ignore[attr-defined]
        if authority != expected:
            self.request.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            return

        host, port_text = authority.rsplit(":", 1)
        upstream = socket.create_connection((host, int(port_text)), timeout=5)
        try:
            self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            _bridge(self.request, upstream)
        finally:
            upstream.close()


def _start_server(handler):
    server = _ThreadingServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class ProxyChainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.relay = ChainedSocks5Relay()
        self.target, self.target_thread = _start_server(_TargetHandler)
        self.target.requests = []

        self.socks, self.socks_thread = _start_server(_Socks5Handler)
        self.socks.credentials = ("user@name", "p:ss")
        self.socks.events = []

        self.http_proxy, self.http_proxy_thread = _start_server(_HttpConnectHandler)
        self.http_proxy.events = []
        self.http_proxy.headers = []
        self.http_proxy.remote_authority = f"127.0.0.1:{self.socks.server_address[1]}"

    def tearDown(self) -> None:
        self.relay.invalidate()
        for server, thread in (
            (self.http_proxy, self.http_proxy_thread),
            (self.socks, self.socks_thread),
            (self.target, self.target_thread),
        ):
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_requests_flow_through_http_then_authenticated_socks5(self) -> None:
        local_http = f"http://local%40user:local%3Apass@127.0.0.1:{self.http_proxy.server_address[1]}"
        remote_socks = f"socks5h://user%40name:p%3Ass@127.0.0.1:{self.socks.server_address[1]}"
        relay_url = self.relay.ensure(local_http, remote_socks)

        response = requests.get(
            f"http://127.0.0.1:{self.target.server_address[1]}/through-chain",
            proxies={"http": relay_url, "https": relay_url},
            timeout=5,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "chain-ok")
        self.assertEqual(
            self.http_proxy.events,
            [f"CONNECT 127.0.0.1:{self.socks.server_address[1]} HTTP/1.1"],
        )
        self.assertIn(
            b"Proxy-Authorization: Basic bG9jYWxAdXNlcjpsb2NhbDpwYXNz",
            self.http_proxy.headers[0],
        )
        self.assertEqual(self.socks.events[0:2], ["socks-greeting", "socks-auth"])
        self.assertEqual(
            self.socks.events[2],
            ("socks-connect", "127.0.0.1", self.target.server_address[1]),
        )
        self.assertEqual(len(self.target.requests), 1)
        traffic = self.relay.traffic_snapshot()
        self.assertGreater(traffic["upload_bytes"], 0)
        self.assertGreater(traffic["download_bytes"], 0)
        self.assertEqual(traffic["total_bytes"], traffic["upload_bytes"] + traffic["download_bytes"])
        self.assertGreaterEqual(traffic["connections"], 1)
        self.assertTrue(traffic["running"])

    def test_chain_first_hop_requires_http(self) -> None:
        with self.assertRaises(ValueError):
            validate_http_connect_proxy("socks5://127.0.0.1:7890")

    def test_proxy_mode_maps_to_legacy_switches(self) -> None:
        self.assertEqual(
            proxy_mode_settings("chain"),
            {
                "use_proxy": True,
                "use_socks5_proxy": False,
                "use_socks5_proxy_chain": True,
            },
        )
        self.assertEqual(proxy_mode_settings("direct"), {
            "use_proxy": False,
            "use_socks5_proxy": True,
            "use_socks5_proxy_chain": False,
        })
        self.assertEqual(get_proxy_mode({"use_proxy": True}), "local")
        self.assertEqual(
            get_proxy_mode({"use_proxy": True, "use_socks5_proxy_chain": True}),
            "chain",
        )

    def test_chain_does_not_require_standalone_socks5_to_be_enabled(self) -> None:
        saved = dict(config_manager._data)
        try:
            config_manager._data.update({
                "use_proxy": True,
                "proxy": "http://127.0.0.1:7890",
                "use_socks5_proxy": False,
                "socks5_proxy": "socks5h://user:pass@proxy.example:1080",
                "use_socks5_proxy_chain": True,
            })
            kind, relay_url = get_active_proxy()

            self.assertEqual(kind, "chain")
            self.assertTrue(relay_url.startswith("socks5h://user:pass@127.0.0.1:"))
            self.assertIsNone(get_requests_proxies("socks5"))
            self.assertEqual(get_requests_proxies("chain") or {}, {
                "http": relay_url,
                "https": relay_url,
            })
        finally:
            invalidate_proxy_chain()
            config_manager._data = saved


if __name__ == "__main__":
    unittest.main()
