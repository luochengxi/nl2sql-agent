"""HP-011 失败定位脚本：逐层探针，暴露被 complete_with_tools 吞掉的真实异常。

背景：HP-011 现在返回 fallback，且 trace 里 decision=0 / tool_call=0（全空）。
按 chat_fc 的逻辑，只要模型正常返回就必然 trace.decision() 打点；trace 全空
说明 complete_with_tools() 返回了 None。而 llm.py:130 的 `except Exception:
return None` 把真实异常静默吞掉了，所以要从外部把异常逼出来。

用法：
  .venv/bin/python tests/golden/diagnose_hp011.py

只诊断、不改 agent 源码、不调测试断言。每一层打印探针结果，看差异第一次
出现在哪层，根因就在那层。
"""
from __future__ import annotations

import json
import os
import sys
import traceback
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

from src.core.config import settings, today
settings()["time"]["today"] = "2026-11-03"   # 与 golden 冻结锚点一致

from src.core.llm import get_provider
from src.core import tool_schemas


def sep(title: str):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def main():
    # ---- L0 配置层：确认 provider / key / 端点 ----
    sep("L0 配置层")
    p = get_provider()
    print(f"provider class : {type(p).__name__}")
    print(f"supports_tools : {p.supports_tools}")
    print(f"available      : {p.available}")
    print(f"api_key set    : {bool(getattr(p, 'api_key', ''))} (len={len(getattr(p, 'api_key', ''))})")
    print(f"base_url       : {getattr(p, 'base_url', '')}")
    print(f"model          : {getattr(p, 'model', '')}")
    print(f"today()        : {today()}")
    if not p.supports_tools or not p.available:
        print(">>> L0 已失败：provider 不可用，后面不用查了。去配 .env 的 OPENAI_API_KEY / settings.yaml 的 llm.provider。")
        return

    # ---- L1 封装层：看 complete_with_tools 是否返回 None ----
    sep("L1 封装层：complete_with_tools()")
    system = ("你是零售取数 Agent。必须调用工具回答，不要反问用户，不要自己编答案。"
              f"今天是 {today().isoformat()}。"
              "用户说'近7日'指从昨日起最近7天：date_start=T-7，date_end=T-1。")
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": "昨日 有效订单量"}]
    wrapped = p.complete_with_tools(messages, tool_schemas.tools_schema())
    print(f"返回值: {type(wrapped).__name__} = {json.dumps(wrapped, ensure_ascii=False)[:400] if wrapped else wrapped}")
    if wrapped is None:
        print(">>> L1 复现了 None。真实异常被 llm.py:130 吞掉，进入 L2 裸调把它逼出来。")
    else:
        print(">>> L1 正常返回，问题不在模型调用层（可能模型没选工具，去看 trace.decision 的 selected）。")

    # ---- L2 传输层：裸发同款 HTTP，不 try/except，异常直接抛 ----
    sep("L2 传输层：裸调 POST {base_url}/chat/completions")
    payload = {
        "model": p.model,
        "temperature": getattr(p, "temperature", 0),
        "tools": tool_schemas.tools_schema(),
        "tool_choice": "auto",
        "messages": messages,
    }
    req = urllib.request.Request(
        f"{p.base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {p.api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
        print(f"HTTP 200, 响应前 800 字：\n{raw[:800]}")
        ch = data["choices"][0]
        print(f"\nfinish_reason : {ch.get('finish_reason')}")
        print(f"tool_calls    : {[t.get('function', {}).get('name') for t in ch.get('message', {}).get('tool_calls') or []]}")
        print(f"content       : {(ch.get('message', {}).get('content') or '')[:300]}")
        print(">>> L2 传输正常。若 L1 却返回 None，说明异常在 llm.py 响应解析段（116-129 行）。")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        print(f">>> HTTP {e.code} {e.reason}")
        print(f"响应体: {body[:800]}")
        print("    401=key 无效/过期；403=无权限；404=端点或模型名错；429=限流；5xx=服务端")
    except Exception:
        print(">>> 裸调抛异常（这就是被吞掉的真凶）：")
        traceback.print_exc()
        print("    常见：URLError=网络/端点不通；TimeoutError=超时；socket.gaierror=DNS")

    print("\n诊断结束。把上面第一个 >>> 异常/失败层的信息对照修复即可。")


if __name__ == "__main__":
    main()
