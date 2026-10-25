"""Built-in LLM vendor presets (AI-CAT-001, S5IF-GOV07).

Why this is a list of *suggestions* rather than a list of *capabilities*: the runtime has exactly
one wire protocol — :class:`factory_intelligence.llm.openai_compat.OpenAICompatProvider`, which
posts to ``<endpoint>/chat/completions`` with a ``Bearer`` header and reads ``<endpoint>/models``
for health.  Nothing in S3 dispatches on the ``adapter`` column, so a preset could promise an
Anthropic- or Gemini-shaped endpoint, register happily, and fail on the first stage that needed it.
A catalogue that can be wrong is worse than a text box, so every entry here is an
OpenAI-compatible root URL, stated the way the client will use it.

Model names are the vendors' public defaults at the time of writing and are **not** verified
against the deployment: a private gateway, a fine-tune or a renamed model all still work, which is
why the console keeps a free-text path next to this list (UI-SET-006).  ``GET /setup`` and the
health probe are what actually establish that an endpoint answers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: The only protocol this build speaks.  Reported per entry so a console can show it and so the
#: answer does not depend on the ``adapter`` column a provider row happens to carry.
WIRE_PROTOCOL = "openai_compat"


@dataclass(frozen=True)
class VendorPreset:
    key: str
    display_name: str
    endpoint: str
    models: tuple[str, ...]
    docs_url: str
    #: A reference name to suggest, for vendors that authenticate with a key the operator probably
    #: already holds in their env file. Empty for the local servers: they usually need no key, and
    #: suggesting `env://OLLAMA_API_KEY` would hand them a reference that cannot resolve — the one
    #: failure mode this field could have avoided.
    key_ref_suggestion: str = ""
    #: A URL on the operator's own machine, so the console can say "start the server first" and
    #: "the model list is whatever you loaded" rather than implying this deployment can reach it.
    local: bool = False
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "displayName": self.display_name,
            "endpoint": self.endpoint,
            "wireProtocol": WIRE_PROTOCOL,
            "models": list(self.models),
            "keyRefSuggestion": self.key_ref_suggestion,
            "docsUrl": self.docs_url,
            "local": self.local,
            "note": self.note,
        }


_PRESETS: tuple[VendorPreset, ...] = (
    VendorPreset(
        key="openai",
        display_name="OpenAI",
        endpoint="https://api.openai.com/v1",
        models=("gpt-4o", "gpt-4o-mini", "gpt-4.1-mini"),
        key_ref_suggestion="env://OPENAI_API_KEY",
        docs_url="https://platform.openai.com/docs/api-reference",
    ),
    VendorPreset(
        key="deepseek",
        display_name="DeepSeek",
        endpoint="https://api.deepseek.com/v1",
        models=("deepseek-chat", "deepseek-reasoner"),
        key_ref_suggestion="env://DEEPSEEK_API_KEY",
        docs_url="https://api-docs.deepseek.com/",
    ),
    VendorPreset(
        key="dashscope",
        display_name="阿里云百炼 / 通义千问",
        endpoint="https://dashscope.aliyuncs.com/compatible-mode/v1",
        models=("qwen-plus", "qwen-max", "qwen-turbo"),
        key_ref_suggestion="env://DASHSCOPE_API_KEY",
        docs_url="https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope",
        note="必须使用 compatible-mode 路径，否则不是 OpenAI 协议。",
    ),
    VendorPreset(
        key="moonshot",
        display_name="月之暗面 Kimi",
        endpoint="https://api.moonshot.cn/v1",
        models=("kimi-latest", "moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"),
        key_ref_suggestion="env://MOONSHOT_API_KEY",
        docs_url="https://platform.moonshot.cn/docs/api/chat",
    ),
    VendorPreset(
        key="zhipu",
        display_name="智谱 GLM",
        endpoint="https://open.bigmodel.cn/api/paas/v4",
        models=("glm-4-plus", "glm-4-flash", "glm-4-long"),
        key_ref_suggestion="env://ZHIPU_API_KEY",
        docs_url="https://docs.bigmodel.cn/cn/guide/develop/openai",
        note="兼容路径以 /v4 结尾（不是 /v1），按原样填写即可。",
    ),
    VendorPreset(
        key="siliconflow",
        display_name="硅基流动 SiliconFlow",
        endpoint="https://api.siliconflow.cn/v1",
        models=("deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-72B-Instruct"),
        key_ref_suggestion="env://SILICONFLOW_API_KEY",
        docs_url="https://docs.siliconflow.cn/cn/api-reference/chat-completions/chat-completions",
    ),
    VendorPreset(
        key="groq",
        display_name="Groq",
        endpoint="https://api.groq.com/openai/v1",
        models=("llama-3.3-70b-versatile", "llama-3.1-8b-instant"),
        key_ref_suggestion="env://GROQ_API_KEY",
        docs_url="https://console.groq.com/docs/openai",
    ),
    VendorPreset(
        key="together",
        display_name="Together AI",
        endpoint="https://api.together.xyz/v1",
        models=("meta-llama/Llama-3.3-70B-Instruct-Turbo", "Qwen/Qwen2.5-72B-Instruct-Turbo"),
        key_ref_suggestion="env://TOGETHER_API_KEY",
        docs_url="https://docs.together.ai/reference/chat-completions-1",
    ),
    VendorPreset(
        key="openrouter",
        display_name="OpenRouter",
        endpoint="https://openrouter.ai/api/v1",
        models=("deepseek/deepseek-chat", "anthropic/claude-3.5-sonnet"),
        key_ref_suggestion="env://OPENROUTER_API_KEY",
        docs_url="https://openrouter.ai/docs/api-reference/overview",
        note="列出的名字是 OpenRouter 自己的路由 ID；非 OpenAI 协议的上游由它转换成这一种协议。",
    ),
    VendorPreset(
        key="ollama",
        display_name="Ollama（本机）",
        endpoint="http://localhost:11434/v1",
        models=("qwen2.5", "llama3.1"),
        docs_url="https://github.com/ollama/ollama/blob/main/docs/openai.md",
        local=True,
        note="本机服务通常不校验 key，密钥引用留空即可；模型名取决于本机实际拉起了什么。",
    ),
    VendorPreset(
        key="vllm",
        display_name="vLLM（自建）",
        endpoint="http://localhost:8000/v1",
        models=(),
        docs_url="https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html",
        local=True,
        note="填你部署的那个模型名（通常等于 --served-model-name）；没开 --api-key 时密钥引用留空。",
    ),
    VendorPreset(
        key="lmstudio",
        display_name="LM Studio（本机）",
        endpoint="http://localhost:1234/v1",
        models=(),
        docs_url="https://lmstudio.ai/docs/app/api/endpoints/openai/openai",
        local=True,
        note="需要先在应用里开启本地服务器；默认不校验 key，密钥引用可留空。",
    ),
)


def presets() -> list[dict[str, Any]]:
    """The catalogue in wire shape, ordered as a console should show it."""
    return [preset.as_dict() for preset in _PRESETS]


__all__ = ["WIRE_PROTOCOL", "VendorPreset", "presets"]
