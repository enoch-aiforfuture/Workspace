"""CalDAV SSRF-via-redirect hardening.

``validate_caldav_url`` resolves and vets the initial host, but the CalDAV
client's HTTP session follows 3xx redirects by default — so a validated public
URL can be redirected, at request time, into loopback/private space (an SSRF
that bypasses the host check). ``_build_dav_client`` pins the session to zero
redirects. These tests exercise the real DAVClient request path (the sync /
write-back surface), not just the settings/test-connection endpoint.
"""

import http.server
import socket
import socketserver
import threading

import pytest

from src import caldav_sync, caldav_writeback


def test_build_dav_client_disables_redirects():
    """The hardened client must carry a redirect-disabled session."""
    pytest.importorskip("caldav")
    client = caldav_sync._build_dav_client("https://calendar.example.com/dav", "u", "p")
    assert client.session.max_redirects == 0


def test_dav_client_does_not_follow_redirect_to_internal_host():
    """End-to-end through the real DAVClient: a 302 toward an internal host
    must NOT be followed. Without the fix the sink is contacted (SSRF); with it
    the redirect is refused and the sink is never reached."""
    pytest.importorskip("caldav")

    sink_hits: list[str] = []
    public_methods: list[str] = []

    class _Internal(http.server.BaseHTTPRequestHandler):
        # Stand-in for an internal service the attacker redirects toward.
        def do_GET(self):  # noqa: N802
            sink_hits.append(self.path)
            self.send_response(207)
            self.end_headers()

        do_PROPFIND = do_GET

        def log_message(self, *a):  # silence test server
            pass

    class _Public(http.server.BaseHTTPRequestHandler):
        # The "validated" public CalDAV server that redirects everything inward.
        def do_GET(self):  # noqa: N802
            public_methods.append(self.command)
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{internal_port}/leak")
            self.end_headers()

        do_PROPFIND = do_GET

        def log_message(self, *a):
            pass

    internal = socketserver.TCPServer(("127.0.0.1", 0), _Internal)
    internal_port = internal.server_address[1]
    public = socketserver.TCPServer(("127.0.0.1", 0), _Public)
    public_port = public.server_address[1]
    threading.Thread(target=internal.serve_forever, daemon=True).start()
    threading.Thread(target=public.serve_forever, daemon=True).start()
    try:
        public_url = f"http://127.0.0.1:{public_port}/dav"
        client = caldav_sync._build_dav_client(public_url, "u", "p")
        client.timeout = 5
        try:
            client.request(public_url, "PROPFIND", "")
        except Exception:
            # Refusing the redirect surfaces as an exception (TooManyRedirects);
            # that is the intended fail-closed behavior. The security assertion
            # is that the internal sink was never contacted.
            pass
        # The request must actually have left the building — otherwise an early
        # error would make "sink not hit" pass vacuously.
        assert public_methods == ["PROPFIND"], "the PROPFIND must reach the public server first"
        assert sink_hits == [], "redirect toward an internal host must not be followed"
    finally:
        internal.shutdown()
        public.shutdown()


def _gai(ip, port):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))]


def _lookup(ip, host, port):
    return caldav_sync._caldav_pinned_getaddrinfo(
        lambda *a, **k: _gai(ip, port),
        host,
        port,
        (socket.AF_UNSPEC, socket.SOCK_STREAM),
        {},
    )


def test_pinned_resolver_rejects_a_link_local_hostname():
    with pytest.raises(OSError, match="not allowed"):
        _lookup("169.254.169.254", "calendar.example.com", 443)


def test_pinned_resolver_keeps_a_public_snapshot():
    results = _lookup("93.184.216.34", "calendar.example.com", 443)
    assert results[0][4][0] == "93.184.216.34"


def test_pinned_resolver_leaves_ip_literals_unchanged():
    """The redirect test connects to 127.0.0.1. A literal cannot be rebound."""
    results = _lookup("127.0.0.1", "127.0.0.1", 80)
    assert results[0][4][0] == "127.0.0.1"


def test_build_dav_client_pins_the_session_resolver(monkeypatch):
    pytest.importorskip("caldav")
    client = caldav_sync._build_dav_client("https://calendar.example.com/dav", "u", "p")
    adapter = client.session.get_adapter("https://calendar.example.com/")
    resolver = adapter.poolmanager._resolver
    assert getattr(resolver, "_caldav_pinned", False) is True
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: _gai("169.254.169.254", args[1] if len(args) > 1 else 443))
    with pytest.raises(OSError, match="not allowed"):
        resolver.getaddrinfo("calendar.example.com", 443, socket.AF_UNSPEC, socket.SOCK_STREAM)


def test_sync_and_writeback_construct_clients_through_the_helper():
    """Guard against a raw DAVClient (redirects enabled) creeping back in.
    Every DAVClient on the sync/write-back paths must go through
    ``_build_dav_client`` so the redirect protection can't be bypassed."""
    sync_src = (caldav_sync.__file__)
    wb_src = (caldav_writeback.__file__)
    with open(sync_src, encoding="utf-8") as f:
        sync_text = f.read()
    with open(wb_src, encoding="utf-8") as f:
        wb_text = f.read()

    # In caldav_sync the only raw construction lives inside the helper itself.
    assert sync_text.count("caldav.DAVClient(") == 1
    assert "max_redirects = 0" in sync_text
    assert "_pin_caldav_session(" in sync_text
    assert "_build_dav_client(" in sync_text

    # Write-back must not construct its own raw client; it reuses the helper.
    assert "caldav.DAVClient(" not in wb_text
    assert "_build_dav_client(" in wb_text
