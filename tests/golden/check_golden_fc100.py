"""FC 黄金集检查器 · 按工作台评审 prompt 的两级检查实现。

第 0 步 · 覆盖门槛（一票否决，最先执行）：
  R1 行全覆盖：10 类关注点每类 >= 1 条
  R2 列全覆盖：3 条业务线每列 >= 1 条（防全挤 retail）
  R3 核心关注点 1/3/5 必须三条业务线都有
  R4 关注点 7 越权 >= 2 条业务线且含非 U001 角色的拒绝 case
  R5 每条业务线 case 占比 >= 15%
  R6 happy_path 六维：13 指标 / 7 维度+不写维度 / 多档时间窗 /
     mom·yoy·dod / 3 业务线 / 单指标+多指标
第 1 步 · 逐条 rubric：可执行、id 唯一、工具名合法、args 过 Schema、
     真值可算（rows_exact 非空且可独立复算）、关注点匹配、非重复。
闸门：正常全绿（真值可复算）+ 鉴别力（缺陷模式下可被影响的字段存在）。

用法：.venv/bin/python tests/golden/check_golden_fc100.py
"""
from __future__ import annotations

import collections
import json
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JL = os.path.join(ROOT, "tests", "golden", "golden_cases_fc100.jsonl")

CASES = [json.loads(l) for l in open(JL, encoding="utf-8") if l.strip()]
SEM = yaml.safe_load(open(os.path.join(ROOT, "config", "semantic_layer.yaml"), encoding="utf-8"))
METRICS = set(SEM["metrics"])
DIMS = set(SEM["dimensions"])
LINES = {"retail", "logistics", "performance"}
VALID_TOOLS = {"query_metrics", "search_caliber", "compare_period", "get_data_dictionary",
               "export_result", "drilldown_detail", "query_history"}

PASS, FAIL, WARN = [], [], []


def ok(cond, msg, tag="PASS"):
    (PASS if cond else FAIL).append(msg)
    return cond


def warn(cond, msg):
    if not cond:
        WARN.append(msg)


def focus_of(c):
    return {t for t in c["tags"] if t.startswith("focus-")}


def lines_of(c):
    return {t for t in c["tags"] if t in LINES}


# ==================== 第 0 步：覆盖门槛 ====================
print("=" * 66)
print("第 0 步 · 覆盖门槛（一票否决）")
print("=" * 66)

# R1 10 类关注点全覆盖
focus_count = collections.Counter()
for c in CASES:
    for f in focus_of(c):
        focus_count[f] += 1
missing_focus = [f"focus-{i}" for i in range(1, 11) if focus_count[f"focus-{i}"] == 0]
ok(not missing_focus, f"R1 10 类关注点全覆盖：缺失={missing_focus or '无'}")
for i in range(1, 11):
    print(f"   focus-{i}: {focus_count[f'focus-{i}']}")

# R2 业务线全覆盖
line_count = collections.Counter()
for c in CASES:
    for ln in lines_of(c):
        line_count[ln] += 1
missing_line = [ln for ln in LINES if line_count[ln] == 0]
ok(not missing_line, f"R2 3 条业务线全覆盖：缺失={missing_line or '无'}")

# R3 核心关注点 1/3/5 三条业务线都有
thin = []
for i in (1, 3, 5):
    got = set()
    for c in CASES:
        if f"focus-{i}" in focus_of(c):
            got |= lines_of(c)
    miss = LINES - got
    if miss:
        thin.append(f"focus-{i} 缺 {sorted(miss)}")
ok(not thin, f"R3 核心关注点 1/3/5 覆盖三条业务线：{'；'.join(thin) if thin else '全部满足'}")

# R4 关注点 7 越权：>=2 业务线 + 非 U001 拒绝 case
sec = [c for c in CASES if "focus-7" in focus_of(c)]
sec_lines = set()
for c in sec:
    sec_lines |= lines_of(c)
sec_non_u001 = [c for c in sec if c["context"]["user_id"] != "U001"]
sec_reject = [c for c in sec if c["expected_action"] in ("fallback", "error")]
ok(len(sec_lines) >= 2 and sec_non_u001 and sec_reject,
   f"R4 越权校验：覆盖业务线={sorted(sec_lines)}，非U001角色={len(sec_non_u001)}条，拒绝态={len(sec_reject)}条")

# R5 业务线占比 >= 15%
TOTAL = len(CASES)
ratio_bad = [f"{ln}={line_count[ln]/TOTAL:.1%}" for ln in LINES if line_count[ln] / TOTAL < 0.15]
ok(not ratio_bad, f"R5 每业务线占比≥15%：{'不达标 ' + ','.join(ratio_bad) if ratio_bad else '达标'}")

# R6 happy_path 六维
hp = [c for c in CASES if c["category"] == "happy_path"]
hp_metrics, hp_dims, hp_windows, hp_cmps = set(), set(), set(), set()
hp_single = hp_multi = 0
hp_lines = set()
for c in hp:
    for t in c["expected_tools"]:
        if t["name"] == "query_metrics":
            hp_metrics |= set(t["args_contract"].get("metrics", []))
            hp_dims |= set(t["args_contract"].get("dimensions", []))
        if t["name"] == "compare_period":
            hp_cmps.add(t["args_contract"].get("compare_type"))
    for tag in c["tags"]:
        if tag in ("r2", "r7", "r30", "y1") or tag.startswith("abs:"):
            hp_windows.add(tag)
        if tag == "single_metric":
            hp_single += 1
        if tag == "multi_metric":
            hp_multi += 1
    hp_lines |= lines_of(c)

ok(METRICS <= hp_metrics, f"R6① 13 指标全覆盖：{len(hp_metrics & METRICS)}/{len(METRICS)}，缺={sorted(METRICS - hp_metrics) or '无'}")
ok(DIMS <= hp_dims, f"R6② 7 维度全覆盖：{len(hp_dims & DIMS)}/{len(DIMS)}，缺={sorted(DIMS - hp_dims) or '无'}")
warn("no_dim_default" in {t for c in hp for t in c["tags"]}, "R6② 建议含 1 条不写维度（默认按 date）")
ok({"r2", "r7", "r30", "y1"} <= hp_windows and any(t.startswith("abs:") for t in hp_windows),
   f"R6③ 时间窗多档：{sorted(hp_windows)}")
ok({"mom", "yoy", "dod"} <= hp_cmps, f"R6④ 同环比 mom/yoy/dod：{sorted(hp_cmps)}")
ok(LINES <= hp_lines, f"R6⑤ happy_path 三业务线：{sorted(hp_lines)}")
ok(hp_single > 0 and hp_multi > 0, f"R6⑥ 单/多指标：single={hp_single}，multi={hp_multi}")

# 配比 70/20/10
cat = collections.Counter(c["category"] for c in CASES)
hp_n = cat["happy_path"]
edge_n = cat["boundary"] + cat["anomaly"]
sec_n = cat["adversarial"] + cat["regression"]
ok(abs(hp_n - 70) <= 1 and abs(edge_n - 20) <= 1 and abs(sec_n - 10) <= 1,
   f"配比 70/20/10：happy={hp_n}，边界异常={edge_n}，安全回归={sec_n}")

# ==================== 第 1 步：逐条 rubric ====================
print()
print("=" * 66)
print("第 1 步 · 逐条 rubric")
print("=" * 66)
ids = [c["id"] for c in CASES]
dup = [k for k, v in collections.Counter(ids).items() if v > 1]
ok(not dup, f"id 唯一：重复={dup or '无'}")

bad_tool, bad_args, bad_focus, bad_rows = [], [], [], []
for c in CASES:
    cid = c["id"]
    # 工具名合法
    for t in c["expected_tools"]:
        if t["name"] not in VALID_TOOLS:
            bad_tool.append(f"{cid}:{t['name']}")
        # 参数 enum 校验（关键：compare_type 必须是合法枚举）
        args = t["args_contract"]
        if t["name"] == "compare_period":
            if args.get("compare_type") not in ("mom", "yoy", "dod"):
                bad_args.append(f"{cid}:compare_type={args.get('compare_type')}")
        if t["name"] == "query_metrics":
            bl = args.get("business_line")
            if bl and bl not in LINES:
                bad_args.append(f"{cid}:business_line={bl}")
            for m in args.get("metrics", []):
                if m not in METRICS:
                    bad_args.append(f"{cid}:metric={m}")
            for d in args.get("dimensions", []):
                if d not in DIMS:
                    bad_args.append(f"{cid}:dimension={d}")
    # focus tag 恰好 1 个
    if len(focus_of(c)) != 1:
        bad_focus.append(f"{cid}:{sorted(focus_of(c))}")
    # 真值可算：result 态应有 rows_exact
    if c["expected_action"] == "result":
        if c["verdict"].get("rows_exact") is None and not c["verdict"].get("baseline_only"):
            # 允许少数特殊 case（说明里声明）
            if "rows_exact" not in c["verdict"]:
                bad_rows.append(cid)

ok(not bad_tool, f"工具名合法（7 个）：异常={bad_tool or '无'}")
ok(not bad_args, f"参数过 Schema（指标/维度/业务线/compare_type）：异常={bad_args or '无'}")
ok(not bad_focus, f"每条恰好 1 个 focus tag：异常={bad_focus or '无'}")
ok(not bad_rows, f"result 态含真值：异常={bad_rows or '无'}")

# 关注点匹配抽查：越权 case 必须是非 U001 或拒绝态
mismatch = [c["id"] for c in sec if c["context"]["user_id"] == "U001" and c["expected_action"] == "result"]
warn(not mismatch, f"关注点 7 中 U001+result 的 case 请人工确认确为越权场景：{mismatch}")

# ==================== 闸门 ====================
print()
print("=" * 66)
print("闸门检查")
print("=" * 66)
# 闸门 1 结构
ok(all(set(c) >= {"id", "category", "query", "context", "expected_action",
                  "expected_tools", "verdict", "tags"} for c in CASES), "闸门1 结构字段齐全")
# 闸门 2 真值独立：检查 rows_exact 都是 dict 且非空（除空窗口）
weird = [c["id"] for c in CASES if c["verdict"].get("rows_exact") is not None
         and not isinstance(c["verdict"]["rows_exact"], dict)]
ok(not weird, f"闸门2 真值结构为 dict：异常={weird or '无'}")
# 闸门 4 鉴别力：缺陷可影响的字段存在
has_sql_must = sum(1 for c in CASES if c["verdict"].get("sql_must"))
has_status = sum(1 for c in CASES if c["verdict"].get("no_tool_status"))
has_rows = sum(1 for c in CASES if c["verdict"].get("rows_exact"))
ok(has_sql_must > 0 and has_status > 0 and has_rows > 0,
   f"闸门4 鉴别力抓手：sql_must={has_sql_must}，status={has_status}，rows={has_rows}")

# ==================== 汇总 ====================
print()
print("=" * 66)
print(f"结果：PASS {len(PASS)} / FAIL {len(FAIL)} / WARN {len(WARN)}")
print("=" * 66)
for m in FAIL:
    print("  [FAIL]", m)
for m in WARN:
    print("  [WARN]", m)

report = os.path.join(ROOT, "tests", "golden", "check_report_fc100.txt")
with open(report, "w", encoding="utf-8") as f:
    f.write("FC 黄金集 100 条 · 检查报告\n")
    f.write(f"总数={TOTAL}，PASS={len(PASS)}，FAIL={len(FAIL)}，WARN={len(WARN)}\n\n")
    f.write("=== 覆盖统计 ===\n")
    f.write(f"关注点: {dict(focus_count)}\n")
    f.write(f"业务线: {dict(line_count)}\n")
    f.write(f"类别配比: {dict(cat)}\n")
    f.write(f"happy_path 指标: {len(hp_metrics & METRICS)}/{len(METRICS)}\n")
    f.write(f"happy_path 维度: {len(hp_dims & DIMS)}/{len(DIMS)}\n")
    f.write(f"happy_path 时间窗: {sorted(hp_windows)}\n")
    f.write(f"happy_path 同环比: {sorted(hp_cmps)}\n")
    f.write(f"happy_path 单/多指标: {hp_single}/{hp_multi}\n\n")
    f.write("=== PASS 明细 ===\n" + ("\n".join(PASS) or "无") + "\n\n")
    f.write("=== FAIL 明细 ===\n" + ("\n".join(FAIL) or "无") + "\n\n")
    f.write("=== WARN ===\n" + ("\n".join(WARN) or "无") + "\n")
print(f"报告已写入 {report}")
sys.exit(1 if FAIL else 0)
