"""托管版 LLM 客户端构造：思考开关等 extra_body 必须作为显式参数，不能夹在 model_kwargs 里"""
import pytest


@pytest.fixture
def saas_llm_config(monkeypatch):
    from config import settings
    from llm_service import llm_config

    monkeypatch.setattr(settings, "deploy_mode", "saas")
    for attr in ("openai_api_key", "moonshot_api_key", "deepseek_api_key", "qwen_api_key"):
        monkeypatch.setattr(settings, attr, "test-key")
    llm_config.clear_llm_cache()
    yield llm_config
    llm_config.clear_llm_cache()


def test_every_model_config_passes_extra_body_explicitly(saas_llm_config):
    configs = saas_llm_config._build_model_configs()
    with_extra_body = {key: cfg for key, cfg in configs.items() if (cfg.get("model_kwargs") or {}).get("extra_body")}
    assert with_extra_body, "配置里应至少有一个带思考开关的模型"

    for key, cfg in with_extra_body.items():
        kwargs = {}
        saas_llm_config._apply_model_kwargs(kwargs, cfg)
        assert kwargs["extra_body"] == cfg["model_kwargs"]["extra_body"], key
        assert "extra_body" not in kwargs.get("model_kwargs", {}), key
        # 不能改动共享的配置字典
        assert "extra_body" in cfg["model_kwargs"], key


def test_built_clients_carry_thinking_switch_as_explicit_parameter(saas_llm_config):
    configs = saas_llm_config._build_model_configs()
    for key, cfg in configs.items():
        expected = (cfg.get("model_kwargs") or {}).get("extra_body")
        if not expected:
            continue
        llm = saas_llm_config.get_qa_llm(key, use_cache=False)
        assert llm.extra_body == expected, key
        assert "extra_body" not in (llm.model_kwargs or {}), key


def test_vendor_parameters_travel_in_extra_body(saas_llm_config):
    # model_kwargs 的其他键会成为 create() 的关键字参数：openai 1.54 不认识 reasoning_effort，
    # 长报告因此 TypeError；ThinkingChatOpenAI 的流式请求还会把它丢掉
    for key, cfg in saas_llm_config._build_model_configs().items():
        assert set(cfg.get("model_kwargs") or {}) <= {"extra_body"}, key


@pytest.mark.asyncio
async def test_reasoning_effort_reaches_both_request_paths(saas_llm_config, monkeypatch):
    from types import SimpleNamespace
    from langchain_core.messages import HumanMessage

    expected = {"thinking": {"type": "enabled"}, "reasoning_effort": "high"}
    llm = saas_llm_config.get_qa_llm("deepseek-v4-flash", use_cache=False)

    payload = llm._get_request_payload([HumanMessage(content="hi")])
    assert payload["extra_body"] == expected
    assert "reasoning_effort" not in payload

    sent = {}

    async def create(**kwargs):
        sent.update(kwargs)

        async def no_chunks():
            return
            yield

        return no_chunks()

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(type(llm), "_get_openai_client", lambda self: fake_client)
    [chunk async for chunk in llm._astream([HumanMessage(content="hi")])]
    assert sent["extra_body"] == expected
    assert "reasoning_effort" not in sent


THINKING_OFF = {"extra_body": {"thinking": {"type": "disabled"}}}
THINKING_ON = {"extra_body": {"thinking": {"type": "enabled"}}}


@pytest.mark.parametrize("config, expected", [
    # Kimi K2.5 / K2.6：思考（默认开启）只接受 1.0，关闭思考只接受 0.6，其他取值 400
    ({"model": "kimi-k2.6"}, 1.0),
    ({"model": "kimi-k2.6", "model_kwargs": THINKING_ON}, 1.0),
    ({"model": "kimi-k2.6", "model_kwargs": THINKING_OFF}, 0.6),
    ({"model": "kimi-k2.5", "model_kwargs": THINKING_OFF}, 0.6),
    # 规则取决于模型而非服务地址：经模型网关调用同样适用（网关上的 ID 还可能是大写）
    ({"model": "Kimi-K2.6", "base_url": "https://gateway.example.com/v1"}, 1.0),
    ({"model": "kimi-k2-thinking-turbo"}, 1.0),
    # 其他模型保持调用方给的温度
    ({"model": "qwen3.8-flash", "temperature": 0.5}, 0.6),
])
def test_kimi_temperature_follows_thinking_mode(config, expected):
    from llm_service.llm_config import effective_temperature

    assert effective_temperature(config, 0.6) == expected


def test_saas_kimi_clients_use_a_temperature_their_mode_allows(saas_llm_config):
    for key, cfg in saas_llm_config._build_model_configs().items():
        if not str(cfg.get("model", "")).lower().startswith("kimi-k2"):
            continue
        thinking = (cfg.get("model_kwargs") or {}).get("extra_body", {}).get("thinking", {}).get("type")
        llm = saas_llm_config.get_qa_llm(key, use_cache=False)
        assert llm.temperature == (0.6 if thinking == "disabled" else 1.0), key


def test_local_mode_with_kimi_provider_uses_thinking_temperature(credentials):
    from llm_service import llm_config

    # 本地版不发送 thinking 参数 → Kimi 按默认的思考模式处理，温度必须为 1.0
    credentials.set_credentials("moonshot", api_key="test-key", model="kimi-k2.6", use_for_chat=True)
    llm_config.clear_llm_cache()
    llm = llm_config.get_qa_llm("moonshot", use_cache=False)
    assert llm.model_name == "kimi-k2.6"
    assert llm.temperature == 1.0
    polish_config = llm_config.get_model_config(llm_config.FEATURE_MODEL_MAP["polish"])
    assert llm_config.effective_temperature(polish_config, 0.6) == 1.0
