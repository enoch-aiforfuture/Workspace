"""One FastEmbed ONNX session per model per process.

RAG, memory, and the tool index each build an embedding lane. Each lane used
to construct its own TextEmbedding, and a failed HTTP probe constructed yet
another. onnxruntime keeps a private copy of the weights, so one boot loaded
the same model three or four times.
"""
import sys
import threading
import time
import types

import src.embedding_lanes as lanes
import src.embeddings as embeddings


class _TextEmbedding:
    constructed = []

    def __init__(self, model_name=None, cache_dir=None):
        _TextEmbedding.constructed.append(model_name)

    def embed(self, texts):
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


def _install_stub(monkeypatch):
    fastembed = types.ModuleType("fastembed")
    fastembed.TextEmbedding = _TextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", fastembed)
    _TextEmbedding.constructed = []
    embeddings.reset_fastembed_clients()
    embeddings.reset_http_embed_state()
    monkeypatch.setattr(embeddings, "_load_persisted_endpoint", lambda: {})


def _http_down(self):
    raise RuntimeError("endpoint down")


def test_lanes_and_factory_share_one_fastembed_session(monkeypatch):
    _install_stub(monkeypatch)
    monkeypatch.setattr(embeddings.EmbeddingClient, "get_sentence_embedding_dimension", _http_down)
    try:
        for _ in range(3):
            lanes._build_fastembed_client()
        for _ in range(2):
            client = embeddings.get_embedding_client()
            assert client is lanes._build_fastembed_client()
        assert _TextEmbedding.constructed == ["sentence-transformers/all-MiniLM-L6-v2"]
    finally:
        embeddings.reset_fastembed_clients()
        embeddings.reset_http_embed_state()


def test_distinct_models_are_cached_separately(monkeypatch):
    _install_stub(monkeypatch)
    try:
        first = embeddings.get_fastembed_client("org/model-a")
        second = embeddings.get_fastembed_client("org/model-b")
        again = embeddings.get_fastembed_client("org/model-a")
        assert first is again
        assert first is not second
        assert _TextEmbedding.constructed == ["org/model-a", "org/model-b"]
    finally:
        embeddings.reset_fastembed_clients()


def test_concurrent_first_load_constructs_once(monkeypatch):
    _install_stub(monkeypatch)
    entered = []
    release = threading.Event()
    real_init = _TextEmbedding.__init__

    def slow_init(self, model_name=None, cache_dir=None):
        entered.append(threading.get_ident())
        assert release.wait(timeout=2)
        real_init(self, model_name=model_name, cache_dir=cache_dir)

    monkeypatch.setattr(_TextEmbedding, "__init__", slow_init)
    errors = []

    def load():
        try:
            embeddings.get_fastembed_client("org/shared")
        except Exception as exc:
            errors.append(exc)

    try:
        threads = [threading.Thread(target=load) for _ in range(8)]
        for thread in threads:
            thread.start()
        deadline = time.time() + 2
        while not entered and time.time() < deadline:
            time.sleep(0.01)
        time.sleep(0.1)
        assert len(entered) == 1
        release.set()
        for thread in threads:
            thread.join(timeout=5)
        assert errors == []
        assert _TextEmbedding.constructed == ["org/shared"]
    finally:
        release.set()
        embeddings.reset_fastembed_clients()
