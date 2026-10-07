"""HTTP after the SSRF check, pinned to that check's address snapshot.

``check_outbound_url`` resolves the host and then returns. A later ``httpx``
client resolves it again, so a name that answered with a public address for
the check can answer with a link-local address for the connect. Image APIs
hand back a result URL that this process then downloads; that second lookup
is the rebinding window.

Redirects are followed by hand. ``follow_redirects=True`` would connect to
the ``Location`` target before it is checked.
"""

from __future__ import annotations

import time
from typing import List, Optional
from urllib.parse import urljoin

import httpcore
import httpx

from src import url_safety

_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}

_HTTPCORE_TO_HTTPX_EXC = {
    httpcore.ConnectError: httpx.ConnectError,
    httpcore.ConnectTimeout: httpx.ConnectTimeout,
    httpcore.NetworkError: httpx.NetworkError,
    httpcore.PoolTimeout: httpx.PoolTimeout,
    httpcore.ProtocolError: httpx.ProtocolError,
    httpcore.ReadError: httpx.ReadError,
    httpcore.ReadTimeout: httpx.ReadTimeout,
    httpcore.RemoteProtocolError: httpx.RemoteProtocolError,
    httpcore.TimeoutException: httpx.TimeoutException,
    httpcore.WriteError: httpx.WriteError,
    httpcore.WriteTimeout: httpx.WriteTimeout,
}


class PinnedFetchError(ValueError):
    """The URL, or a redirect hop, failed the outbound safety check."""


def _validated_ips(raw_ips: List[str]) -> list:
    """De-duplicated IP objects from one resolver snapshot, in order."""
    import ipaddress

    ips = []
    seen = set()
    for raw in raw_ips:
        if not isinstance(raw, str):
            continue
        try:
            ip = ipaddress.ip_address(raw.split("%", 1)[0])
        except ValueError:
            continue
        if ip in seen:
            continue
        seen.add(ip)
        ips.append(ip)
    return ips


def resolve_pinned_ips(url: str, *, block_private: bool = False) -> list:
    """Resolve ``url`` once and return the addresses the safety check allowed."""
    resolved: List[str] = []

    def _recording(host: str) -> List[str]:
        answers = list(url_safety._default_resolver(host))
        resolved[:] = answers
        return answers

    ok, reason = url_safety.check_outbound_url(
        url, block_private=block_private, resolver=_recording
    )
    if not ok:
        raise PinnedFetchError(reason)
    ips = _validated_ips(resolved)
    if not ips:
        raise PinnedFetchError("host did not resolve to a usable address")
    return ips


class _PinnedAsyncBackend(httpcore.AsyncNetworkBackend):
    """Connect only to addresses from one validated DNS snapshot."""

    def __init__(self, ips: list):
        self._ips = [str(ip) for ip in ips]
        self._real = httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None,
                          socket_options=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        last_exc: Optional[Exception] = None
        for ip in self._ips:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            try:
                return await self._real.connect_tcp(
                    ip, port, remaining, local_address, socket_options
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_exc = exc
                if deadline is not None and time.monotonic() >= deadline:
                    break
        if last_exc is not None:
            raise last_exc
        raise httpcore.ConnectError("no validated address available")

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        return await self._real.connect_unix_socket(path, timeout, socket_options)

    async def sleep(self, seconds: float) -> None:
        return await self._real.sleep(seconds)


def _connect_check_url(host: str) -> str:
    """URL whose host is classified before a shared client connects."""
    name = (host or "").strip()
    if name.startswith("[") and "]" in name:
        return f"https://{name}/"
    if ":" in name:
        return f"https://[{name}]/"
    return f"https://{name}/"


class _CheckingAsyncBackend(httpcore.AsyncNetworkBackend):
    """Check the host at connect time, then connect only to that snapshot.

    A long-lived client talks to many hosts, so it cannot carry one IP list
    from construction. Each new TCP connection resolves once. An idle pooled
    connection keeps the address it already checked.
    """

    def __init__(self, *, block_private: bool = False):
        self._block_private = block_private
        self._real = httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None,
                          socket_options=None):
        if isinstance(host, (bytes, bytearray)):
            host = host.decode("ascii", "replace")
        hostname = str(host or "").strip()
        if not hostname:
            raise httpcore.ConnectError("missing host")
        try:
            ips = resolve_pinned_ips(
                _connect_check_url(hostname),
                block_private=self._block_private,
            )
        except PinnedFetchError as exc:
            raise httpcore.ConnectError(str(exc)) from exc
        deadline = None if timeout is None else time.monotonic() + timeout
        last_exc: Optional[Exception] = None
        for ip in ips:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            try:
                return await self._real.connect_tcp(
                    str(ip), port, remaining, local_address, socket_options
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_exc = exc
                if deadline is not None and time.monotonic() >= deadline:
                    break
        if last_exc is not None:
            raise last_exc
        raise httpcore.ConnectError("no validated address available")

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        return await self._real.connect_unix_socket(path, timeout, socket_options)

    async def sleep(self, seconds: float) -> None:
        return await self._real.sleep(seconds)


def pin_async_client_connects(client: httpx.AsyncClient, *, block_private: bool = False) -> None:
    """Pin new connections on a client that is reused across hosts."""
    transport = getattr(client, "_transport", None)
    pool = getattr(transport, "_pool", None)
    if pool is None or not hasattr(pool, "_network_backend"):
        raise PinnedFetchError("client transport cannot pin connections")
    pool._network_backend = _CheckingAsyncBackend(block_private=block_private)


class _PinnedAsyncTransport(httpx.AsyncBaseTransport):
    """Pin the TCP destination. Host, SNI, and the request URL stay unchanged."""

    def __init__(self, ips: list):
        self._pinned_ips = list(ips)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(),
            http1=True,
            http2=False,
            network_backend=_PinnedAsyncBackend(ips),
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        core_req = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        try:
            core_resp = await self._pool.handle_async_request(core_req)
            content = b"".join([chunk async for chunk in core_resp.aiter_stream()])
            await core_resp.aclose()
        except Exception as exc:
            mapped = _HTTPCORE_TO_HTTPX_EXC.get(type(exc))
            if mapped is not None:
                raise mapped(str(exc)) from exc
            raise
        return httpx.Response(
            status_code=core_resp.status,
            headers=core_resp.headers,
            content=content,
            extensions=core_resp.extensions,
            request=request,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()


async def aget_pinned(
    url: str,
    *,
    block_private: bool = False,
    timeout: float = 60.0,
    headers: Optional[dict] = None,
) -> httpx.Response:
    """GET ``url``, re-checking and re-pinning every redirect hop.

    Caller headers are sent only on the first request. A redirect must not
    carry them to the next host.
    """
    current = url
    hop_headers = headers
    for _ in range(_MAX_REDIRECTS + 1):
        ips = resolve_pinned_ips(current, block_private=block_private)
        async with httpx.AsyncClient(
            transport=_PinnedAsyncTransport(ips),
            follow_redirects=False,
            timeout=timeout,
        ) as client:
            get_kwargs = {}
            if hop_headers:
                get_kwargs["headers"] = hop_headers
            response = await client.get(current, **get_kwargs)
        hop_headers = None
        if response.status_code in _REDIRECT_STATUSES:
            location = response.headers.get("location")
            if not location:
                return response
            current = urljoin(str(response.url), location)
            continue
        return response
    raise PinnedFetchError("too many redirects")


async def arequest_pinned(
    method: str,
    url: str,
    *,
    block_private: bool = False,
    timeout: float | httpx.Timeout = 10.0,
    headers: Optional[dict] = None,
    json: Optional[object] = None,
    content: Optional[bytes | str] = None,
    data: Optional[dict] = None,
    files: Optional[dict] = None,
) -> httpx.Response:
    """One request, no redirects, connected only to the checked addresses.

    Reminder webhooks, ntfy, and image generation attach a bearer token.
    Gallery edits attach that token to a multipart body. Following a
    redirect or re-resolving DNS would send that token to a different host.
    ``data`` and ``files`` are omitted unless the caller set them, so
    existing JSON callers stay on the same request signature.
    """
    ips = resolve_pinned_ips(url, block_private=block_private)
    request_kwargs = {"headers": headers, "json": json, "content": content}
    if data is not None:
        request_kwargs["data"] = data
    if files is not None:
        request_kwargs["files"] = files
    async with httpx.AsyncClient(
        transport=_PinnedAsyncTransport(ips),
        follow_redirects=False,
        timeout=timeout,
    ) as client:
        return await client.request(method, url, **request_kwargs)


class _PinnedBackend(httpcore.NetworkBackend):
    """Sync connect limited to one validated address snapshot."""

    def __init__(self, ips: list):
        self._ips = [str(ip) for ip in ips]
        self._real = httpcore.SyncBackend()

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        last_exc: Optional[Exception] = None
        for ip in self._ips:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            try:
                return self._real.connect_tcp(
                    ip, port, remaining, local_address, socket_options
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_exc = exc
                if deadline is not None and time.monotonic() >= deadline:
                    break
        if last_exc is not None:
            raise last_exc
        raise httpcore.ConnectError("no validated address available")

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        return self._real.connect_unix_socket(path, timeout, socket_options)

    def sleep(self, seconds: float) -> None:
        return self._real.sleep(seconds)


class _PinnedTransport(httpx.BaseTransport):
    """Sync transport that pins the socket and keeps the request URL."""

    def __init__(self, ips: list, *, verify: bool | object = True):
        self._pinned_ips = list(ips)
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(verify=verify),
            http1=True,
            http2=False,
            network_backend=_PinnedBackend(ips),
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        core_response = None
        try:
            core_response = self._pool.handle_request(core_request)
            content = b"".join(core_response.stream)
        except Exception as exc:
            mapped = _HTTPCORE_TO_HTTPX_EXC.get(type(exc))
            if mapped is not None:
                raise mapped(str(exc)) from exc
            raise
        finally:
            if core_response is not None:
                core_response.close()
        return httpx.Response(
            status_code=core_response.status,
            headers=core_response.headers,
            content=content,
            extensions=core_response.extensions,
            request=request,
        )

    def close(self) -> None:
        self._pool.close()


_VERIFY_UNSET = object()


def request_pinned(
    method: str,
    url: str,
    *,
    block_private: bool = False,
    timeout: float | httpx.Timeout = 10.0,
    headers: Optional[dict] = None,
    json: Optional[object] = None,
    content: Optional[bytes] = None,
    data: Optional[dict] = None,
    params: Optional[dict] = None,
    auth=None,
    verify: object = _VERIFY_UNSET,
) -> httpx.Response:
    """One request, no redirects, connected only to the checked addresses.

    Embedding calls attach a bearer token and CardDAV calls attach basic
    auth. Following a redirect or re-resolving DNS would send those
    credentials to a different host. ``data`` and ``params`` are omitted
    unless the caller set them. ``verify`` is omitted unless set, so the
    default TLS context stays the one this transport already used.
    """
    ips = resolve_pinned_ips(url, block_private=block_private)
    transport = (
        _PinnedTransport(ips)
        if verify is _VERIFY_UNSET
        else _PinnedTransport(ips, verify=verify)
    )
    with httpx.Client(
        transport=transport,
        follow_redirects=False,
        timeout=timeout,
    ) as client:
        request_kwargs = {"headers": headers, "json": json, "content": content}
        if data is not None:
            request_kwargs["data"] = data
        if params is not None:
            request_kwargs["params"] = params
        if auth is not None:
            request_kwargs["auth"] = auth
        return client.request(method, url, **request_kwargs)


def sync_get(url: str, *, block_private: bool = False, **kwargs) -> httpx.Response:
    """``httpx.get`` shape, pinned. Does not follow redirects."""
    return _sync_request("GET", url, block_private=block_private, **kwargs)


def sync_post(url: str, *, block_private: bool = False, **kwargs) -> httpx.Response:
    """``httpx.post`` shape, pinned. Does not follow redirects."""
    return _sync_request("POST", url, block_private=block_private, **kwargs)


def _sync_request(method: str, url: str, *, block_private: bool = False, **kwargs) -> httpx.Response:
    timeout = kwargs.pop("timeout", 10.0)
    if timeout is None:
        timeout = 10.0
    headers = kwargs.pop("headers", None)
    json_body = kwargs.pop("json", None)
    content = kwargs.pop("content", None)
    data = kwargs.pop("data", None)
    params = kwargs.pop("params", None)
    auth = kwargs.pop("auth", None)
    verify = kwargs.pop("verify", _VERIFY_UNSET)
    kwargs.pop("follow_redirects", None)
    if kwargs:
        raise PinnedFetchError(
            "unsupported pinned fetch options: " + ", ".join(sorted(kwargs))
        )
    request_kwargs = {
        "block_private": block_private,
        "timeout": timeout,
        "headers": headers,
        "json": json_body,
        "content": content,
    }
    if data is not None:
        request_kwargs["data"] = data
    if params is not None:
        request_kwargs["params"] = params
    if auth is not None:
        request_kwargs["auth"] = auth
    if verify is not _VERIFY_UNSET:
        request_kwargs["verify"] = verify
    return request_pinned(method, url, **request_kwargs)
