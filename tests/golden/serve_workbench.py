"""FC 链路测试执行工作台 · 本地一键运行服务。

为什么需要它：执行台发布在 workbuddy.cn 是静态页，浏览器不允许它跨域请求
本机 agent（混合内容 + CORS + 服务未启动）。本服务把「页面」和「执行接口」
放在同一个源（127.0.0.1:8765），页面点「运行」即可真实调用 agent 并拿回结果。

复用原则：判定逻辑一律调用 run_fc100.py（不重写、不改口径），本服务只负责
把它的函数包装成 HTTP 接口 + 托管页面。

启动：
  .venv/bin/python tests/golden/serve_workbench.py
  浏览器打开 http://127.0.0.1:8765

接口：
  GET  /                  托管执行台页面
  GET  /api/health        服务状态（锚点、runner、端口）
  GET  /api/goldens       可用黄金集列表（文件名 + case 数）
  POST /api/run           执行：{golden, index?, id?, category?, limit?, skip_soft?, soft_samples?}
                          返回与 run_fc100 --out 同结构 JSON（meta/summary/cases）

注意：本服务用 TestClient 直接调 app，不需要另起 agent 服务；同一时刻只允许
一个执行请求（TestClient 非线程安全，用锁串行化）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GOLDEN_DIR = os.path.join(ROOT, "tests", "golden")
RUNS_DIR = os.path.join(ROOT, "reports", "runs")   # 每次运行的快照，文件名带时间戳，永不覆盖
_RUN_NAME = re.compile(r"^\d{8}_\d{6}_[0-9a-f]{6}\.json$")
for p in (ROOT, GOLDEN_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
import uvicorn

# import runner：它顶部会冻结时间锚点并创建 TestClient —— 正是我们要的
import run_fc100 as R  # noqa: E402

app = FastAPI(title="FC 链路测试执行工作台·本地服务", version="1.0")

HTML_PATH = os.path.join(GOLDEN_DIR, "execution_workbench.html")
_run_lock = threading.Lock()  # TestClient 非线程安全，串行化执行


@app.get("/")
def index():
    if not os.path.exists(HTML_PATH):
        return JSONResponse({"error": "页面文件缺失", "path": HTML_PATH}, status_code=500)
    return FileResponse(HTML_PATH, media_type="text/html; charset=utf-8")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "service": "fc-exec-workbench",
        "anchor_t": R.ANCHOR_T,
        "runner": "run_fc100.py（判定逻辑原样复用）",
        "endpoint": "POST /api/chat-fc（TestClient 直调，无需另起 agent 服务）",
        "now": datetime.now().isoformat(timespec="seconds"),
    }


@app.get("/api/goldens")
def goldens():
    items = []
    for name in sorted(os.listdir(GOLDEN_DIR)):
        if not name.endswith(".jsonl"):
            continue
        path = os.path.join(GOLDEN_DIR, name)
        try:
            with open(path, encoding="utf-8") as f:
                count = sum(1 for line in f if line.strip())
        except Exception:
            count = -1
        items.append({
            "name": name,
            "path": os.path.relpath(path, ROOT).replace(os.sep, "/"),
            "count": count,
            "recommended": name == "golden_cases_fc100.jsonl",
        })
    items.sort(key=lambda x: (not x["recommended"], x["name"]))
    return {"goldens": items, "anchor_t": R.ANCHOR_T}


class RunReq(BaseModel):
    golden: str = "tests/golden/golden_cases_fc100.jsonl"
    index: int = 0
    id: str = ""
    category: str = ""
    limit: int = 0
    skip_soft: bool = True          # 页面点运行默认跳过软采样（快）；软断言仍跑硬判定
    soft_samples: int = 5


def _filter_cases(cases, req: RunReq):
    if req.category:
        cases = [c for c in cases if c.get("category") == req.category]
    if req.id:
        cases = [c for c in cases if c.get("id") == req.id or c["id"].startswith(req.id)]
    if req.index:
        if not 1 <= req.index <= len(cases):
            raise ValueError(f"index {req.index} 越界，当前候选 {len(cases)} 条")
        cases = [cases[req.index - 1]]
    if req.limit:
        cases = cases[:req.limit]
    return cases


@app.get("/api/trace/{tid}")
def proxy_trace(tid: str):
    """同源代理查 trace：页面复制的 curl 直接指向本服务，无需另起 agent 服务。"""
    try:
        r = R.client.get(f"/api/trace/{tid}")
        return JSONResponse(r.json(), status_code=r.status_code)
    except Exception as ex:
        raise HTTPException(500, f"查 trace 失败：{type(ex).__name__}: {ex}")


@app.get("/api/runs")
def list_runs():
    """列出所有运行快照（新→旧），供执行台「运行历史」回看。"""
    items = []
    if os.path.isdir(RUNS_DIR):
        for name in sorted(os.listdir(RUNS_DIR), reverse=True):
            if not _RUN_NAME.match(name):
                continue
            path = os.path.join(RUNS_DIR, name)
            try:
                with open(path, encoding="utf-8") as f:
                    d = json.load(f)
                meta, summary = d.get("meta") or {}, d.get("summary") or {}
                filt = meta.get("filter") or {}
                scope = filt.get("id") or filt.get("category") or (f"前{filt['limit']}条" if filt.get("limit") else "全部")
                items.append({
                    "name": name,
                    "run_at": meta.get("run_at", ""),
                    "golden": meta.get("golden_name", ""),
                    "scope": scope,
                    "total": summary.get("total", 0),
                    "pass": summary.get("pass", 0),
                    "fail": summary.get("fail", 0),
                    "bytes": os.path.getsize(path),
                })
            except Exception:
                continue                      # 坏快照跳过，不影响列表
    return {"runs": items, "dir": os.path.relpath(RUNS_DIR, ROOT).replace(os.sep, "/")}


@app.get("/api/runs/{name}")
def get_run(name: str):
    """读取单次运行快照的完整 JSON（含每个 case 的 trace 摘要）。"""
    if not _RUN_NAME.match(name):
        raise HTTPException(400, "非法快照名")
    path = os.path.join(RUNS_DIR, name)
    if not os.path.exists(path):
        raise HTTPException(404, "快照不存在")
    try:
        with open(path, encoding="utf-8") as f:
            return JSONResponse(json.load(f))
    except Exception as ex:
        raise HTTPException(500, f"读取快照失败：{type(ex).__name__}: {ex}")


@app.post("/api/run")
def run(req: RunReq, request: Request):
    base_url = str(request.base_url).rstrip("/")  # 页面复制的 curl 指向本服务，同源可查
    jl = req.golden if os.path.isabs(req.golden) else os.path.join(ROOT, req.golden)
    if not os.path.exists(jl):
        raise HTTPException(404, f"黄金集不存在：{req.golden}")
    try:
        with open(jl, encoding="utf-8") as f:
            cases = [json.loads(line) for line in f if line.strip()]
        cases = _filter_cases(cases, req)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    except Exception as ex:
        raise HTTPException(400, f"读取黄金集失败：{type(ex).__name__}: {ex}")
    if not cases:
        raise HTTPException(400, "筛选后没有匹配的 case")

    records = []
    n_pass = n_fail = 0
    t0 = time.time()
    with _run_lock:  # 串行：避免并发请求污染 TestClient / LLM 会话状态
        for case in cases:
            cid = case["id"]
            tid, tr, resp = "", [], {}
            runner_error = None
            case_t0 = time.time()
            try:
                resp, tr, tid = R.run_once(case)
                fails = R.check_hard(resp, tr, case)
            except Exception as ex:
                runner_error = f"RUNNER-ERROR {type(ex).__name__}: {ex}"
                fails = [runner_error]

            soft = None
            soft_samples = 0 if req.skip_soft else max(1, req.soft_samples)
            verdict = case.get("verdict") or {}
            if soft_samples and verdict.get("tools_must"):
                try:
                    soft = R.check_soft(case, soft_samples)
                    if soft and not soft[2]:
                        fails.append(f"tools_must 软断言 {soft[0]}/{soft[1]} < 0.8")
                except Exception as ex:
                    fails.append(f"SOFT-ERROR {type(ex).__name__}: {ex}")

            mark = "PASS" if not fails else "FAIL"
            n_pass += 1 if mark == "PASS" else 0
            n_fail += 1 if mark == "FAIL" else 0

            ctx = case.get("context") or {}
            focus = [x for x in case.get("tags", []) if x.startswith("focus-")]
            line = "-"
            for t in case.get("expected_tools", []):
                bl = (t.get("args_contract") or {}).get("business_line")
                if bl:
                    line = bl
                    break
            records.append({
                "id": cid,
                "category": case.get("category"),
                "expected_action": case.get("expected_action"),
                "query": case.get("query"),
                "focus": focus,
                "business_line": line,
                "user_id": ctx.get("user_id"),
                "note": verdict.get("note", ""),
                "verdict": mark,
                "fail_reasons": fails,
                "trace_id": tid,
                "trace_url": f"GET /api/trace/{tid}" if tid else "",
                "curl": f"curl -s {base_url}/api/trace/{tid} | python -m json.tool" if tid else "",
                "soft": ({"hits": soft[0], "samples": soft[1], "pass": soft[2]} if soft else None),
                "resp": R.resp_digest(resp) if resp else {},
                "trace": R.trace_digest(tr),
                "expected": {
                    "verdict": {k: vv for k, vv in verdict.items() if vv not in ([], None, "")},
                    "expected_tools": case.get("expected_tools", []),
                },
                "runner_error": runner_error,
                "elapsed_ms": round((time.time() - case_t0) * 1000, 1),
            })

    elapsed = time.time() - t0
    now = datetime.now()
    payload = {
        "meta": {
            "runner": "run_fc100.py（本地服务复用）",
            "golden": os.path.relpath(jl, ROOT).replace(os.sep, "/"),
            "golden_name": os.path.basename(jl),
            "run_at": now.isoformat(timespec="seconds"),
            "anchor_t": R.ANCHOR_T,
            "provider": "openai_like",
            "endpoint": "POST /api/chat-fc",
            "trace_endpoint": "GET /api/trace/{trace_id}",
            "soft_samples": soft_samples,
            "filter": req.model_dump(),
        },
        "summary": {
            "total": len(cases),
            "pass": n_pass,
            "fail": n_fail,
            "pass_rate": round(100.0 * n_pass / len(cases), 1),
            "elapsed_s": round(elapsed, 1),
        },
        "cases": records,
    }
    # 每次运行落盘一份快照，文件名带时间戳+随机串，永不覆盖 → 运行历史长期保留
    snap = f"{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.json"
    try:
        os.makedirs(RUNS_DIR, exist_ok=True)
        with open(os.path.join(RUNS_DIR, snap), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        payload["meta"]["snapshot"] = snap
    except Exception as ex:
        payload["meta"]["snapshot_error"] = f"{type(ex).__name__}: {ex}"
    return payload


def main():
    global HTML_PATH
    ap = argparse.ArgumentParser(description="FC 链路测试执行工作台·本地一键运行服务")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--html", default=HTML_PATH, help="执行台页面路径")
    args = ap.parse_args()

    HTML_PATH = args.html
    print("=" * 64)
    print("FC 链路测试执行工作台 · 本地服务")
    print("=" * 64)
    print(f"  页面地址   http://{args.host}:{args.port}")
    print(f"  执行接口   POST /api/run   （判定逻辑复用 run_fc100.py）")
    print(f"  时间锚点   {R.ANCHOR_T}")
    print(f"  页面文件   {HTML_PATH}")
    print("  说明       用浏览器打开上面的地址，即可点「运行」真实执行黄金集")
    print("=" * 64)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
