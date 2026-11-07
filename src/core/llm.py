"""LLM 可插拔层。

为什么要 Mock + 真实双通道（见技术方案拆解 §11）:
1. 作品集可演示：别人 clone 下来没有 API Key 也能跑通全流程。
2. 测试可复现：Mock 输出确定，断言才稳定；真实模型有随机性。
3. 成本：开发调试不烧 token。

重要边界：LLM 只负责"从问句里抽取指标/维度/业务线"，
时间、权限范围、SQL 编译一律由确定性代码负责（见 intent.py / nl2sql.py）。
即使 LLM 返回乱码或超时，系统仍能降级到规则解析并给出正确结果。
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from .config import settings


class LLMProvider:
    name = "base"

    def complete_json(self, system: str, user: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @property
    def available(self) -> bool:
        return True


class MockProvider(LLMProvider):
    """确定性规则通道：不调用任何外部服务，返回 None 表示"交给规则解析"。"""
    name = "mock"

    def complete_json(self, system: str, user: str) -> dict[str, Any] | None:
        return None

    @property
    def available(self) -> bool:
        return False


class OpenAILikeProvider(LLMProvider):
    name = "openai_like"

    def __init__(self) -> None:
        cfg = settings().get("llm", {})
        self.base_url = str(cfg.get("openai_base_url", "https://api.openai.com/v1")).rstrip("/")
        self.model = str(cfg.get("openai_model", "gpt-4o-mini"))
        self.api_key = os.environ.get(str(cfg.get("openai_api_key_env", "OPENAI_API_KEY")), "")
        self.temperature = float(cfg.get("temperature", 0))

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def complete_json(self, system: str, user: str) -> dict[str, Any] | None:
        if not self.available:
            return None
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            return _extract_json(content)
        except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError, OSError):
            return None


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


_provider: LLMProvider | None = None


def get_provider() -> LLMProvider:
    global _provider
    if _provider is None:
        name = settings().get("llm", {}).get("provider", "mock")
        _provider = OpenAILikeProvider() if name == "openai_like" else MockProvider()
    return _provider
