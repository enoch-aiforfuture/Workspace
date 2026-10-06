"""Every llama-server launch this app writes disables reasoning for reasoning GGUFs.

The default Cookbook command already did this. Hand-typed commands, the serve
form, saved presets, and retries go through the same normalizer. An explicit
--reasoning value is kept. A python llama_cpp.server command is not rewritten.
"""

from routes.cookbook_helpers import _normalize_llama_server_reasoning


def test_hand_written_llama_server_command_gains_reasoning_off():
    cmd = "llama-server -hf unsloth/Qwen3-8B-GGUF --host 0.0.0.0 --port 8080"
    assert _normalize_llama_server_reasoning(cmd).endswith("--reasoning off")


def test_repo_id_marks_a_generic_model_path():
    cmd = 'llama-server --model "/models/model.gguf" --port 8080'
    normalized = _normalize_llama_server_reasoning(cmd, "org/QwQ-32B-GGUF")
    assert normalized.endswith("--reasoning off")
    assert normalized.startswith(cmd)


def test_explicit_reasoning_value_is_preserved():
    cmd = "llama-server -hf org/Qwen3-8B-GGUF --reasoning on --port 8080"
    assert _normalize_llama_server_reasoning(cmd) == cmd


def test_reasoning_off_is_not_duplicated():
    cmd = "llama-server -hf org/Magistral-Small-GGUF --reasoning off"
    assert _normalize_llama_server_reasoning(cmd) == cmd


def test_unrelated_llama_server_model_is_unchanged():
    cmd = "llama-server -hf unsloth/gemma-3-4b-it-GGUF --port 8080"
    assert _normalize_llama_server_reasoning(cmd) == cmd


def test_llama_cpp_python_server_is_not_given_the_flag():
    cmd = "python3 -m llama_cpp.server --model /models/Qwen3-8B.gguf --port 8080"
    assert _normalize_llama_server_reasoning(cmd) == cmd


def test_vllm_command_is_not_given_the_llama_server_flag():
    cmd = "vllm serve Qwen/Qwen3-8B --host 0.0.0.0 --port 8000"
    assert _normalize_llama_server_reasoning(cmd) == cmd
