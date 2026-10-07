"""Speech and search credential fetches must stay on the checked address.

TTS and STT post an endpoint API key. Brave, Tavily, Google PSE, and Serper
post a provider key to a fixed public host. A second DNS answer must not
move those secrets onto a link-local address.
"""
from types import SimpleNamespace

from src.pinned_fetch import _PinnedTransport, sync_post


class _Audio:
    content = b"audio-bytes"

    def raise_for_status(self):
        return None


class _Transcript:
    def raise_for_status(self):
        return None

    def json(self):
        return {"text": "hello"}


class _Search:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"web": {"results": [{"title": "T", "url": "https://example.com", "description": "s"}]}}


class _DB:
    def __init__(self, base_url, api_key):
        self._ep = SimpleNamespace(base_url=base_url, api_key=api_key)

    def query(self, _model):
        return self

    def filter(self, *_args):
        return self

    def first(self):
        return self._ep

    def close(self):
        return None


def test_sync_post_forwards_multipart_files(monkeypatch):
    seen = {}

    class _Client:
        def __init__(self, *args, **kwargs):
            seen["follow_redirects"] = kwargs.get("follow_redirects")
            seen["transport"] = kwargs.get("transport")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def request(self, method, url, **kwargs):
            seen["call"] = (method, url, kwargs)
            return _Transcript()

    monkeypatch.setattr("src.url_safety._default_resolver", lambda host: ["93.184.216.34"])
    monkeypatch.setattr("src.pinned_fetch.httpx.Client", _Client)

    files = {"file": ("audio.webm", b"abc", "audio/webm")}
    sync_post(
        "https://speech.example/v1/audio/transcriptions",
        headers={"Authorization": "Bearer stt-key"},
        files=files,
        data={"model": "whisper-1"},
        block_private=False,
        timeout=60,
    )

    assert seen["follow_redirects"] is False
    assert isinstance(seen["transport"], _PinnedTransport)
    method, url, kwargs = seen["call"]
    assert method == "POST"
    assert url == "https://speech.example/v1/audio/transcriptions"
    assert kwargs["files"]["file"][0] == "audio.webm"
    assert kwargs["data"]["model"] == "whisper-1"
    assert kwargs["headers"]["Authorization"] == "Bearer stt-key"


def test_tts_posts_the_api_key_on_a_pinned_request(monkeypatch, tmp_path):
    from services.tts.tts_service import TTSService

    seen = {}

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return _Audio()

    monkeypatch.setattr("services.tts.tts_service._sync_post", fake_post)
    monkeypatch.setattr("src.database.SessionLocal", lambda: _DB("https://speech.example/v1/", "tts-key"))

    audio = TTSService(cache_dir=str(tmp_path))._synthesize_api("hello", "ep", "tts-1", "alloy")

    assert audio == b"audio-bytes"
    assert seen["url"] == "https://speech.example/v1/audio/speech"
    assert seen["kwargs"]["block_private"] is False
    assert seen["kwargs"]["headers"]["Authorization"] == "Bearer tts-key"
    assert seen["kwargs"]["json"]["input"] == "hello"


def test_stt_posts_the_api_key_and_audio_on_a_pinned_request(monkeypatch):
    from services.stt.stt_service import STTService

    seen = {}

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return _Transcript()

    monkeypatch.setattr("services.stt.stt_service._sync_post", fake_post)
    monkeypatch.setattr("src.database.SessionLocal", lambda: _DB("https://speech.example/v1/", "stt-key"))

    text = STTService()._transcribe_api(b"abc", "ep", "whisper-1", language="en")

    assert text == "hello"
    assert seen["url"] == "https://speech.example/v1/audio/transcriptions"
    assert seen["kwargs"]["block_private"] is False
    assert seen["kwargs"]["headers"]["Authorization"] == "Bearer stt-key"
    assert seen["kwargs"]["data"]["model"] == "whisper-1"
    assert seen["kwargs"]["data"]["language"] == "en"
    assert seen["kwargs"]["files"]["file"][0] == "audio.webm"


def test_brave_sends_the_subscription_token_on_a_pinned_get(monkeypatch):
    from services.search import providers

    seen = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return _Search()

    monkeypatch.setattr(providers, "_sync_get", fake_get)
    monkeypatch.setattr(providers, "_get_provider_key", lambda _name: "brave-key")
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {})

    results = providers.brave_search("query", count=1)

    assert results[0]["url"] == "https://example.com"
    assert seen["url"] == "https://api.search.brave.com/res/v1/web/search"
    assert seen["kwargs"]["block_private"] is True
    assert seen["kwargs"]["headers"]["X-Subscription-Token"] == "brave-key"


def test_tavily_sends_the_bearer_token_on_a_pinned_post(monkeypatch):
    from services.search import providers

    seen = {}

    class _Tavily:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"results": [{"title": "T", "url": "https://example.com", "content": "s"}]}

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return _Tavily()

    monkeypatch.setattr(providers, "_sync_post", fake_post)
    monkeypatch.setattr(providers, "_get_provider_key", lambda _name: "tavily-key")
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {})

    results = providers.tavily_search("query", count=1)

    assert results[0]["url"] == "https://example.com"
    assert seen["url"] == "https://api.tavily.com/search"
    assert seen["kwargs"]["block_private"] is True
    assert seen["kwargs"]["headers"]["Authorization"] == "Bearer tavily-key"


def test_brave_rejects_a_link_local_answer(monkeypatch):
    from services.search import providers

    monkeypatch.setattr("src.url_safety._default_resolver", lambda host: ["169.254.169.254"])
    monkeypatch.setenv("DATA_BRAVE_API_KEY", "brave-key")
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {})

    assert providers.brave_search("query", count=1) == []
