"""llama-server must not look ready while weights are still loading (#6474).

GET /v1/models returns 200 during load. /health stays 503 until the server
logs `model loaded`. Other engines still count any HTTP access log as ready.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from routes.cookbook_helpers import _parse_serve_phase

_REPO = Path(__file__).resolve().parent.parent
_HAS_NODE = shutil.which("node") is not None

_LLAMA = "llama-server -hf unsloth/Qwen3.5-4B-GGUF --hf-file q.gguf --port 8000\n"


def test_llama_models_200_while_loading_is_not_ready():
    snap = _LLAMA + '127.0.0.1 - - "GET /v1/models HTTP/1.1" 200\nLoading model\n'
    assert _parse_serve_phase(snap).get("status") != "ready"


def test_llama_model_loaded_is_ready_even_after_models_probe():
    snap = _LLAMA + '"GET /v1/models HTTP/1.1" 200\nsrv llama_server: model loaded\n'
    assert _parse_serve_phase(snap)["status"] == "ready"


def test_llama_health_200_is_ready_and_503_is_not():
    ready = _parse_serve_phase(_LLAMA + '"GET /health HTTP/1.1" 200\n')
    loading = _parse_serve_phase(
        _LLAMA + '"GET /health HTTP/1.1" 503\n"GET /v1/models HTTP/1.1" 200\n'
    )
    assert ready["status"] == "ready"
    assert loading.get("status") != "ready"


def test_llama_chat_200_is_ready_and_503_is_not():
    ready = _parse_serve_phase(_LLAMA + '"POST /v1/chat/completions HTTP/1.1" 200\n')
    loading = _parse_serve_phase(_LLAMA + '"POST /v1/chat/completions HTTP/1.1" 503\n')
    assert ready["status"] == "ready"
    assert loading.get("status") != "ready"


def test_non_llama_models_200_is_still_ready():
    snap = 'vllm serve org/model\nINFO: "GET /v1/models HTTP/1.1" 200\n'
    assert _parse_serve_phase(snap)["status"] == "ready"


def test_application_startup_complete_still_wins():
    snap = _LLAMA + "Application startup complete\n"
    assert _parse_serve_phase(snap) == {"phase": "ready", "status": "ready"}


def test_llama_load_progress_is_not_hidden_by_models_probe():
    snap = _LLAMA + '"GET /v1/models HTTP/1.1" 200\nLoading safetensors checkpoint 40%\n'
    info = _parse_serve_phase(snap)
    assert info["status"] == "running"
    assert info["pct"] == 40


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_js_phase_parser_matches_llama_loading_rule():
    script = r"""
        import { parseServePhase } from './static/js/cookbookServePhase.js';
        const llama = 'llama-server -hf unsloth/Qwen3.5-4B-GGUF --port 8000\n';
        const cases = {
          models: parseServePhase(llama + '"GET /v1/models HTTP/1.1" 200\n'),
          loaded: parseServePhase(llama + 'srv llama_server: model loaded\n'),
          health200: parseServePhase(llama + '"GET /health HTTP/1.1" 200\n'),
          health503: parseServePhase(llama + '"GET /health HTTP/1.1" 503\n'),
          vllm: parseServePhase('vllm serve m\n"GET /v1/models HTTP/1.1" 200\n'),
          startup: parseServePhase(llama + 'Application startup complete\n'),
        };
        console.log(JSON.stringify(cases));
    """
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=_REPO, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    cases = json.loads(proc.stdout.strip().splitlines()[-1])
    assert cases["models"].get("status") != "ready"
    assert cases["loaded"]["status"] == "ready"
    assert cases["health200"]["status"] == "ready"
    assert cases["health503"].get("status") != "ready"
    assert cases["vllm"]["status"] == "ready"
    assert cases["startup"]["status"] == "ready"
