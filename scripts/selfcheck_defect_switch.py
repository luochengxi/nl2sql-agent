#!/usr/bin/env python3
"""缺陷开关自检（不是测试套件）。

用途：确认"配置切换"这条链路真的生效——同一问句在
正常模式 / 指定缺陷模式下应产生可观测差异。
真正的测试用例设计由项目所有者自行完成。

用法:
    python scripts/selfcheck_defect_switch.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROBE = """
import json
from src.core.orchestrator import chat
from src.core.models import ChatRequest
r = chat(ChatRequest(user_id={user}, query={query}))
if {second}:
    r = chat(ChatRequest(user_id={user}, query={second}, session_id=r.session_id))
print(json.dumps(r.model_dump(), ensure_ascii=False, default=str))
"""

# (缺陷ID, 说明, user, query, 第二轮, 提取路径)
CASES = [
    ("D01", "近N日窗口是否含当天", "U001", "近7日有效订单量", None, "plan.time_range.end"),
    ("D02", "环比对比周期对齐", "U001", "近7日有效订单量环比", None, "plan.time_range.compare_start"),
    ("D03", "区域经理行级下推", "U002", "近7日各城市有效订单量", None, "row_count"),
    ("D06", "禁用字段剔除", "U006", "近7日佣金", None, "answer_type"),
    ("D09", "客单价分母口径", "U001", "近7日客单价", None, "sql"),
    ("D10", "有效订单量状态过滤", "U001", "近7日有效订单量", None, "sql"),
    ("D11", "同名不同口径歧义", "U001", "近7日销售额", None, "answer_type"),
    ("D12", "缺时间的默认窗口", "U001", "各城市有效订单量", None, "plan.time_range.preset"),
    ("D13", "追问槽位继承", "U002", "近7日各城市有效订单量", "再看看", "plan.dimensions"),
    ("D17", "低置信兜底", "U001", "随便看看", None, "answer_type"),
]


def getpath(obj, path):
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def run(mode: str | None, user: str, query: str, second: str | None) -> dict:
    env = dict(os.environ)
    if mode:
        env["NL2SQL_DEFECT_MODE"] = mode
    else:
        env.pop("NL2SQL_DEFECT_MODE", None)
    code = PROBE.format(user=repr(user), query=repr(query), second=repr(second))
    out = subprocess.check_output([sys.executable, "-c", code], cwd=ROOT,
                                  env=env, text=True)
    return json.loads(out.strip().splitlines()[-1])


def main() -> int:
    print(f"{'ID':<5}{'说明':<22}{'正常模式':<34}{'缺陷模式':<34}可观测")
    print("-" * 108)
    ok = 0
    for did, desc, user, query, second, path in CASES:
        normal = run(None, user, query, second)
        defect = run(did, user, query, second)
        a, b = getpath(normal, path), getpath(defect, path)
        sa = str(a).replace("\n", " ")
        sb = str(b).replace("\n", " ")
        diff = sa != sb
        ok += int(diff)
        print(f"{did:<5}{desc:<22}{sa[:32]:<34}{sb[:32]:<34}{'✔' if diff else '✘'}")
    print("-" * 108)
    print(f"可观测差异 {ok}/{len(CASES)} 项。"
          "未列出的 D04/D05/D07/D08/D14/D15/D16/D18 需通过下钻、导出、审计等接口手工验证，"
          "见 docs/缺陷答案key.local.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
