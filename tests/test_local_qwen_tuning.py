"""Local Qwen tuning: model-card sampling for local OpenAI-compatible servers,
and prompt-size guards that keep agent turns small enough to prefill quickly.
"""
import pytest

from src import llm_core

LOCAL_URL = "http://127.0.0.1:8080/v1/chat/completions"
QWEN = "/models/qwen38-mlx/4-bit/Qwen3.5-27B"


@pytest.fixture
def local_endpoints(monkeypatch):
    import src.model_context as model_context

    monkeypatch.setattr(
        model_context, "is_local_endpoint", lambda url: "127.0.0.1" in url or "localhost" in url
    )


def test_local_qwen_pins_enable_thinking_to_think_flag(local_endpoints):
    """A stale enable_thinking=true must not survive think=false.

    mlx-vlm honors enable_thinking, not Ollama's think flag. setdefault left
    the stale value in place and tool calls came back wrapped in <think>.
    """
    off = {"model": QWEN, "temperature": 0.7, "think": False, "enable_thinking": True}
    llm_core._apply_local_generation_stability(off, LOCAL_URL, QWEN)
    assert off["enable_thinking"] is False

    on = {"model": QWEN, "think": True, "enable_thinking": False}
    llm_core._apply_local_generation_stability(on, LOCAL_URL, QWEN)
    assert on["enable_thinking"] is True


def test_local_qwen_non_thinking_gets_model_card_sampling(local_endpoints):
    payload = {"model": QWEN, "temperature": 1.0, "think": False}
    llm_core._apply_local_generation_stability(payload, LOCAL_URL, QWEN)

    assert payload["temperature"] == 0.7
    assert payload["top_p"] == 0.8
    assert payload["top_k"] == 20
    assert payload["min_p"] == 0.0
    assert payload["enable_thinking"] is False


def test_local_qwen_thinking_keeps_temperature_and_widens_top_p(local_endpoints):
    payload = {"model": QWEN, "temperature": 1.0, "think": True}
    llm_core._apply_local_generation_stability(payload, LOCAL_URL, QWEN)

    assert payload["temperature"] == 1.0
    assert payload["top_p"] == 0.95
    assert payload["enable_thinking"] is True


def test_local_qwen_respects_explicit_values(local_endpoints):
    payload = {"model": QWEN, "temperature": 0.2, "top_p": 0.5, "top_k": 5}
    llm_core._apply_local_generation_stability(payload, LOCAL_URL, QWEN)

    assert payload["temperature"] == 0.2
    assert payload["top_p"] == 0.5
    assert payload["top_k"] == 5


def test_local_qwen_bad_temperature_falls_back_to_cap(local_endpoints):
    payload = {"model": QWEN, "temperature": "hot"}
    llm_core._apply_local_generation_stability(payload, LOCAL_URL, QWEN)

    assert payload["temperature"] == 0.7


def test_remote_qwen_is_untouched(local_endpoints):
    payload = {"model": "qwen/qwen3-235b", "temperature": 1.0}
    llm_core._apply_local_generation_stability(
        payload, "https://openrouter.ai/api/v1/chat/completions", "qwen/qwen3-235b"
    )

    assert payload == {"model": "qwen/qwen3-235b", "temperature": 1.0}


def test_local_non_qwen_is_untouched(local_endpoints):
    payload = {"model": "llama3.1:8b", "temperature": 1.0}
    llm_core._apply_local_generation_stability(payload, LOCAL_URL, "llama3.1:8b")

    assert payload == {"model": "llama3.1:8b", "temperature": 1.0}


def _msgs(text):
    return [{"role": "user", "content": text}]


@pytest.mark.parametrize(
    "text",
    [
        "Read the file /tmp/project/note.txt and count the words",
        "note.txt has a typo",
        "fix my docker build",
        "ask chatgpt what it thinks",
        "open ~/settings-backup/main.py",
    ],
)
def test_admin_intent_ignores_paths_and_partial_words(text):
    from src.agent_loop import _detect_admin_intent

    assert _detect_admin_intent(_msgs(text)) is False


@pytest.mark.parametrize(
    "text",
    ["take a note: buy milk", "show my notes.", "what jobs are scheduled?", "delete this chat"],
)
def test_admin_intent_still_matches_words_and_inflections(text):
    from src.agent_loop import _detect_admin_intent

    assert _detect_admin_intent(_msgs(text)) is True


def test_retry_after_file_work_inherits_file_context():
    from src.agent_loop import _classify_agent_request

    msgs = [
        {"role": "user", "content": "Read the file /tmp/project/note.txt and count the words."},
        {"role": "assistant", "content": "It has 3 words."},
        {"role": "user", "content": "Now read it again and tell me the last word."},
    ]
    intent = _classify_agent_request(msgs, msgs[-1]["content"])

    assert intent["continuation"] is True
    assert "files" in intent["domains"]
    assert "/tmp/project/note.txt" in intent["retrieval_query"]


def test_retry_after_plain_chat_does_not_inherit():
    from src.agent_loop import _classify_agent_request

    msgs = [
        {"role": "user", "content": "Tell me a joke about cats"},
        {"role": "assistant", "content": "..."},
        {"role": "user", "content": "tell it again"},
    ]
    intent = _classify_agent_request(msgs, msgs[-1]["content"])

    assert intent["continuation"] is False
    assert intent["retrieval_query"] == "tell it again"


def _system_text(monkeypatch, relevant_tools):
    import src.agent_loop as al

    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "blocked_tools_for_owner", lambda owner: set(), raising=False)
    al._cached_base_prompt = None
    al._cached_base_prompt_key = None
    messages, _ = al._build_system_prompt(
        messages=[{"role": "user", "content": "what is 17 times 23?"}],
        model="qwen-test",
        active_document=None,
        mcp_mgr=None,
        relevant_tools=relevant_tools,
    )
    return "\n\n".join(m.get("content", "") for m in messages if m.get("role") == "system")


def test_loop_primitives_alone_do_not_add_local_machine_rules(monkeypatch):
    text = _system_text(monkeypatch, {"ask_user", "update_plan"})
    assert "local-machine mode" not in text


def test_file_tools_add_local_machine_rules(monkeypatch):
    import src.agent_loop as al

    text = _system_text(monkeypatch, set(al._WORKSPACE_TERMINUS_TOOLS))
    assert "local-machine mode" in text
