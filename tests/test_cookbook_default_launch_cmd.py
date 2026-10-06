"""Regression guard for #6474: llama.cpp launch command used `-hfr` for the file.

On current llama.cpp `-hfr` is an alias of `-hf` (the repo flag); the GGUF
filename flag is `-hff` / `--hf-file`. Passing a filename to `-hfr` makes
llama-server die with "invalid HF repo format".
"""
from src.tools.cookbook import _cookbook_default_launch_cmd


def test_llamacpp_passes_gguf_filename_with_hf_file():
    cmd = _cookbook_default_launch_cmd(
        "unsloth/Qwen3.5-4B-GGUF",
        "llama.cpp",
        port=8000,
        info={"siblings": ["README.md", "Qwen3.5-4B-Q4_K_M.gguf"]},
    )
    assert "-hf unsloth/Qwen3.5-4B-GGUF" in cmd
    assert "--hf-file Qwen3.5-4B-Q4_K_M.gguf" in cmd
    assert "-hfr" not in cmd
    assert cmd.endswith("--reasoning off")


def test_llamacpp_without_gguf_siblings_omits_file_flag():
    cmd = _cookbook_default_launch_cmd("org/model", "llama.cpp", port=8000, info={"siblings": ["README.md"]})
    assert cmd.startswith("llama-server -hf org/model ")
    assert "--hf-file" not in cmd and "-hfr" not in cmd
    assert "--reasoning" not in cmd


def test_llamacpp_reasoning_flag_follows_gguf_filename_when_repo_is_generic():
    cmd = _cookbook_default_launch_cmd(
        "org/model",
        "llama.cpp",
        info={"siblings": ["QwQ-32B-Q4_K_M.gguf"]},
    )
    assert "--hf-file QwQ-32B-Q4_K_M.gguf" in cmd
    assert cmd.endswith("--reasoning off")


def test_llamacpp_reasoning_flag_covers_deepseek_r1_and_magistral():
    for repo in ("unsloth/DeepSeek-R1-GGUF", "unsloth/Magistral-Small-2506-GGUF"):
        cmd = _cookbook_default_launch_cmd(repo, "llama.cpp")
        assert cmd.endswith("--reasoning off")
        assert "--hf-file" not in cmd


def test_llamacpp_does_not_disable_reasoning_for_unrelated_models():
    for repo in ("unsloth/gemma-3-4b-it-GGUF", "unsloth/Mistral-Small-24B-GGUF", "org/Llama-3.1-8B-GGUF"):
        cmd = _cookbook_default_launch_cmd(repo, "llama.cpp", info={"siblings": ["model.gguf"]})
        assert "--reasoning" not in cmd
