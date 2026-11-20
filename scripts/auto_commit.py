#!/usr/bin/env python3
"""auto_commit.py — 把改动自动归类到版本锚点，生成规范 commit message，并可推送 GitHub。

你要的三段逻辑全在这里：
    改了什么  →  归类到版本锚点  →  写进 commit message
    (git diff)   (AREA_RULES)      (build_message)

用法：
    python3 scripts/auto_commit.py --dry-run     # 预览：只打印分类表和 message，绝不写仓库
    python3 scripts/auto_commit.py               # 提交：本地 git commit，不联网
    python3 scripts/auto_commit.py --push        # 提交并推送到 origin（间歇 502 自动重试）
    python3 scripts/auto_commit.py --push --message "fix(fc): 自定义 [SP-FC-002]"

设计原则：
    1. 安全闸优先：改动内容命中 token/key/密码 等敏感词，直接拒绝提交。
    2. --dry-run 永远只读，先看清楚再动手。
    3. 只有 --push 才联网；commit 是本地操作，失败可重来。
    4. 锚点号和 type/scope 都来自文件归属，不靠人肉记忆。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 文件归属规则：(类别中文名, commit type, scope, 锚点键)
# 判定顺序自上而下，首个命中即采用。
AREA_RULES = [
    ("口径文档", "fix", "caliber", "caliber_version", ("knowledge/caliber/",)),
    ("语义层",   "fix", "semantic", None,              ("semantic_layer", "config/semantic")),
    ("Prompt",   "fix", "prompt",   "prompt_snapshot", ("orchestrator.py", "intent.py")),
    ("工具契约", "fix", "tool",     "prompt_snapshot", ("tool_schemas.py", "dispatcher.py")),
    ("黄金集",   "test", "golden",  "golden_version",  ("golden",)),
    ("测试代码", "test", "test",    None,              ("tests/", "test_")),
    ("Web界面",  "feat", "web",     None,              ("web/", "index.html", "static/")),
    ("文档",     "docs", "docs",    None,              ("readme", "docs/")),
    ("测试工具", "feat", "tool",    None,              ("scripts/",)),
    ("运行产物", "chore", "report", None,              ("reports/",)),
]

# 敏感信息正则：命中即拒绝提交（公开仓库防御）
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"gho_[A-Za-z0-9]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{16,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}"),
    re.compile(r"(?i)(api[_-]?key|password|secret)\s*[:=]\s*['\"][^'\"]{8,}"),
]


def git(*args: str, check: bool = False) -> tuple[int, str]:
    p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if check and p.returncode != 0:
        print(f"[错误] git {' '.join(args)} 失败：\n{p.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return p.returncode, (p.stdout or p.stderr).strip()


def changes() -> list[str]:
    """解析 git status --porcelain -z，返回改动文件路径（含未跟踪）。

    用 -z（NUL 分隔）+ core.quotepath=false：中文路径不会被转成八进制转义，
    也不会被 strip() 吃掉前导状态位，避免 EADME.md 这类截断。
    """
    p = subprocess.run(
        ["git", "-c", "core.quotepath=false", "status", "--porcelain", "-z"],
        cwd=ROOT, capture_output=True,
    )
    out = p.stdout.decode("utf-8", "replace")
    paths = []
    for rec in out.split("\0"):
        if len(rec) < 4:
            continue
        if " -> " in rec:          # 重命名：取新路径
            path = rec.split(" -> ")[-1]
        else:
            path = rec[3:]         # 前 2 位是 XY 状态位，第 3 位是空格
        if path.strip():
            paths.append(path.strip())
    return paths


def classify(path: str):
    p = path.replace("\\", "/").lower()
    for name, typ, scope, anchor_key, needles in AREA_RULES:
        if any(n.lower() in p for n in needles):
            return dict(area=name, type=typ, scope=scope,
                        anchor_key=anchor_key, path=path)
    return dict(area="其他", type="chore", scope="misc",
                anchor_key=None, path=path)


def collect_anchors() -> dict[str, str]:
    """从项目文件里采集当前版本锚点，与 version_ctx_demo.py 同源。"""
    anchors: dict[str, str] = {}

    # Prompt 快照号：快照索引表里"当前生效"的 SP-FC-xxx
    snap = ROOT / "docs/SystemPrompt版本快照.md"
    if snap.exists():
        m = re.search(r"\|\s*\*\*(SP-FC-\d+)\*\*\s*\|[^\n]*当前生效",
                      snap.read_text(encoding="utf-8"))
        if m:
            anchors["prompt_snapshot"] = m.group(1)

    # 口径版本：销售额.md 的 frontmatter
    cal = ROOT / "knowledge/caliber/销售额.md"
    if cal.exists():
        m = re.search(r"^version:\s*(\S+)", cal.read_text(encoding="utf-8"), re.M)
        if m:
            anchors["caliber_version"] = f"销售额 v{m.group(1)}"

    # 黄金集版本：golden 文件名里的日期，或 reports 结果里的 golden_name
    gdir = ROOT / "tests/golden"
    if gdir.exists():
        names = sorted(x.name for x in gdir.glob("golden_cases_fc100*"))
        if names:
            anchors["golden_version"] = names[-1].replace("golden_cases_fc100", "fc100").split(".")[0]

    return anchors


def scan_secrets(paths: list[str]) -> list[str]:
    """对改动文件做敏感信息扫描，返回命中项。"""
    hits = []
    for rel in paths:
        f = ROOT / rel
        if not f.is_file() or f.stat().st_size > 2_000_000:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pat in SECRET_PATTERNS:
            m = pat.search(text)
            if m:
                hits.append(f"{rel}: 命中 {m.group(0)[:24]}…")
                break
    return hits


def build_message(groups: dict[str, list[str]], anchors: dict[str, str],
                  custom: str | None) -> str:
    if custom:
        return custom
    areas = list(groups.keys())
    used_anchors = []
    for name in areas:
        for area, typ, scope, key, _ in AREA_RULES:
            if area == name and key and anchors.get(key) and anchors[key] not in used_anchors:
                used_anchors.append(anchors[key])
    suffix = " [" + " ".join(used_anchors) + "]" if used_anchors else ""

    if len(areas) == 1:
        name = areas[0]
        for area, typ, scope, key, _ in AREA_RULES:
            if area == name:
                return f"{typ}({scope}): 更新{name}（{len(groups[name])} 个文件）{suffix}"
    summary = "、".join(areas)
    return f"chore(mixed): 更新{summary}{suffix}"


def push_with_retry(retries: int = 6) -> bool:
    """联网推送；代理间歇 502，失败按 2/4/6… 秒退避重试。"""
    for i in range(1, retries + 1):
        code, out = git("push", "origin", "HEAD")
        if code == 0:
            print("[推送] 成功")
            return True
        print(f"[推送] 第 {i}/{retries} 次失败：{out.splitlines()[-1] if out else '未知错误'}")
        if i < retries:
            time.sleep(2 * i)
    return False


def main() -> None:
    ap = argparse.ArgumentParser(description="改动归类到版本锚点 → 生成 commit message → 推送")
    ap.add_argument("--dry-run", action="store_true", help="只预览，不写仓库")
    ap.add_argument("--push", action="store_true", help="提交后推送到 origin")
    ap.add_argument("--message", help="自定义 commit message（覆盖自动生成）")
    args = ap.parse_args()

    paths = changes()
    anchors = collect_anchors()

    print("=" * 60)
    print("1) 改了什么 —— git status")
    for p in paths or ["（无未提交改动）"]:
        print("   -", p)

    grouped = [classify(p) for p in paths]
    groups: dict[str, list[str]] = {}
    for g in grouped:
        groups.setdefault(g["area"], []).append(g["path"])

    print("\n2) 归类到版本锚点")
    for g in grouped:
        anchor = f" ← {anchors[g['anchor_key']]}" if g["anchor_key"] and anchors.get(g["anchor_key"]) else ""
        print(f"   {g['path']:42s} → {g['area']} ({g['type']}({g['scope']})){anchor}")

    msg = build_message(groups, anchors, args.message)
    print("\n3) 写进 commit message")
    print("  ", msg)

    if args.dry_run:
        print("\n[dry-run] 仅预览，未提交、未推送。")
        return

    if not paths:
        _, ahead = git("rev-list", "--count", "origin/main..HEAD")
        if ahead != "0" and args.push:
            print(f"\n无新改动，但有 {ahead} 个本地 commit 待推送。")
            if not push_with_retry():
                sys.exit(1)
            return
        print("\n无可提交内容。")
        return

    hits = scan_secrets(paths)
    if hits:
        print("\n[安全闸拦截] 改动含疑似敏感信息，已拒绝提交：", file=sys.stderr)
        for h in hits:
            print("   -", h, file=sys.stderr)
        sys.exit(2)

    git("add", "-A", check=True)
    git("commit", "-m", msg, check=True)
    print("\n[提交] 本地 commit 完成。")

    if args.push:
        if not push_with_retry():
            print("\n[推送失败] 本地 commit 已保留，稍后可手动 git push。", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
