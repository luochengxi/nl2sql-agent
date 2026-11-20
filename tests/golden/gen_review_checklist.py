#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 golden_cases_fc100.jsonl 逐条拆成可勾选的人工检查测试点。

产出：
  - 交互清单 HTML（工作区，跟质量工作台放一起）
  - 可打印 Markdown（被测项目 tests/golden/，跟黄金集放一起）

测试点全部落到 FC 链路的真实断言抓手（trace.decision / tool_call / tool_return / SQL）。
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "golden_cases_fc100.jsonl")
MD_OUT = os.path.join(HERE, "fc100_testpoints.md")
HTML_OUT = "/Users/andrew/WorkBuddy/2026-11-19-16-36-26/fc100-review-checklist.html"

FIELD_CN = {
    "business_line": "业务线", "metrics": "指标", "dimensions": "维度",
    "compare_type": "对比类型", "fmt": "导出格式", "filters": "筛选条件",
    "scope": "权限范围",
}
DATE_FIELDS = {"date_start", "date_end"}
INTENT = {
    "result": "正确识别为取数请求，正常出数",
    "clarify": "识别为信息不足 → 返回澄清（给 suggestions），不裸出数",
    "fallback": "识别为无法处理/超范围 → 走兜底，不编造结果",
    "error": "识别为非法请求 → 明确报错，不幻觉数据",
}


def fmtval(v):
    if isinstance(v, list):
        return "[" + ", ".join(str(x) for x in v) + "]"
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    if v is None:
        return "null"
    return str(v)


def tps_for(case):
    """返回 [{cat, text, via}]"""
    tps = []
    def add(cat, text, via="-"):
        tps.append({"cat": cat, "text": text, "via": via})

    v = case["verdict"]; ctx = case["context"]
    tools = case["expected_tools"]; tags = set(case["tags"])
    at = v["answer_type"]
    behavioral = (at == "result" and not tools and v["rows_exact"] is None)

    # ① 意图与路由
    if behavioral:
        add("①意图路由", "行为型用例：按「设计意图」核对路由/隔离/权限行为，不以是否出数为准",
            "decision 行为 / session 隔离")
    else:
        add("①意图路由", INTENT.get(at, at), "decision.selected / 最终回答形态")

    # ② 工具选择
    names = [t["name"] for t in tools]
    if names:
        add("②工具选择", "选中：" + "、".join(names), "trace.decision[].selected")
    elif behavioral:
        add("②工具选择", "不调用工具 / 按设计意图决定是否续接上轮工具", "trace.tool_call[]")
    else:
        add("②工具选择", "本轮不调用任何工具（无工具路径）", "trace.tool_call[] 为空")
    for x in v["tools_must"]:
        if x not in names:  # 去重：已在「选中」里列出的不再重复
            add("②工具选择", "必须调用：" + x, "trace.tool_call[].name")
    for x in v["tools_must_not"]:
        add("②工具选择", "不得调用：" + x, "trace.tool_call[].name")

    # ③ 参数（逐工具逐字段；日期窗合并为一条）
    for t in tools:
        a = t["args_contract"]; name = t["name"]
        if not a:
            add("③工具参数", name + "：按回执/无需额外参数", "tool_call.tool_args")
            continue
        done = set()
        if "date_start" in a or "date_end" in a:
            add("③工具参数", "%s.日期窗 = %s ~ %s" % (name, a.get("date_start"), a.get("date_end")),
                "tool_call.tool_args.date_start/date_end")
            done |= DATE_FIELDS
        for k, val in a.items():
            if k in done:
                continue
            cn = FIELD_CN.get(k, k)
            add("③工具参数", "%s.%s = %s" % (name, cn, fmtval(val)), "tool_call.tool_args." + k)

    # ④ 顺序与依赖
    if len(tools) > 1:
        seq = " → ".join("%d.%s" % (t["order"], t["name"]) for t in sorted(tools, key=lambda x: x["order"]))
        add("④顺序依赖", "调用顺序：" + seq, "trace.tool_call[] 时序 / decision 轮次")
        for t in tools:
            if t.get("depends_on"):
                add("④顺序依赖", "%s 依赖 %s（前置先执行并带回执）" % (t["name"], t["depends_on"]),
                    "tool_call.args / tool_return")

    # ⑤ 轮次
    add("⑤轮次上限", "决策轮数 ≤ 3，未死循环重复调用", "trace.decision[] 长度 ≤ 3")

    # ⑥ 状态与 SQL 证据
    if v["no_tool_status"]:
        add("⑥状态证据", "无工具路径下状态不得为：" + "、".join(v["no_tool_status"]), "tool_call.status")
    if v["sql_must"]:
        add("⑥状态证据", "SQL 必须包含：" + "、".join(v["sql_must"]), "query_metrics 生成的 SQL（审计）")
    if v["sql_must_not"]:
        add("⑥状态证据", "SQL 不得包含：" + "、".join(v["sql_must_not"]), "query_metrics 生成的 SQL")

    # ⑦ 真值
    re_ = v["rows_exact"]; rc = v["row_count"]
    if re_ is None:
        add("⑦数值真值", "本条不核对数值；只核对行为/格式/意图", "-")
    elif re_ == {} or rc == 0:
        add("⑦数值真值", "合法空结果：0 行；不得报错、不得编造数据", "tool_return / 最终回答")
    else:
        n = rc if rc is not None else len(re_)  # row_count 缺失时以真值键数兜底
        add("⑦数值真值", "分组数=%s；逐组数值与下方冻结真值一致" % n, "tool_return ↔ oracle 比对")

    # ⑧ 禁止文本
    if v["forbidden_text"]:
        add("⑧输出禁语", "输出不得出现：" + "、".join(v["forbidden_text"]), "最终回答文本")

    # ⑨ 关注点专项
    if ctx.get("user_id") != "U001" or "security" in tags:
        add("⑨专项·安全【红线】",
            "角色 %s 必须行级下推/拒绝越权；话术不得放大权限（如“全区”）" % ctx.get("user_id"),
            "SQL 下推谓词 / tool_return 行范围")
    if "multi_turn" in tags or ctx.get("history"):
        add("⑨专项·多轮", "继承上轮时间窗/维度，按本轮问句切换；session_id 隔离", "trace.history / session 维度")
    if "context_isolation" in tags:
        add("⑨专项·多轮", "跨会话不继承上一会话筛选条件，不泄漏历史", "session_id 隔离 / 无跨会话状态")
    if "compare" in tags:
        add("⑨专项·同环比", "识别 mom/yoy/dod 意图且 compare_type 正确；T3 为回执实现，不编造对比数值",
            "compare_period 回执 / 编排补齐窗口")
    if "delivery" in tags:
        add("⑨专项·交付", "结构化字段完整、可审计（trace_id + 凭证），凭证绑 user_id", "export_result/drilldown 回执")
    if case["category"] == "boundary" or "absolute_date" in tags:
        add("⑨专项·边界", "空/未来时间窗返回合法空结果，BETWEEN 含端点，不越界不编造", "日期窗解析 / tool_return")
    if "focus-9" in tags:
        add("⑨专项·性能", "记录 latency_ms 与轮数作为基线，不作为 pass/fail", "trace.latency_ms")
    if "focus-10" in tags or case["category"] == "regression":
        add("⑨专项·回归", "历史 bad case 不复现", "与历史失败轨迹比对")
    if "focus-5" in tags:
        add("⑨专项·接地", "所有数值来自工具返回，不凭空生成", "tool_return ↔ 最终回答")
    return tps


def load():
    cases = []
    with open(SRC, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def build_data(cases):
    out = []
    for c in cases:
        ctx = c["context"]
        line = "—"
        for t in c["expected_tools"]:
            if "business_line" in t["args_contract"]:
                line = t["args_contract"]["business_line"]; break
        foci = sorted({x for x in c["tags"] if x.startswith("focus-")})
        out.append({
            "id": c["id"], "category": c["category"], "query": c["query"],
            "line": line, "focus": foci, "difficulty": c.get("difficulty", ""),
            "user": ctx.get("user_id"), "turn": ctx.get("turn_index", 1),
            "action": c["expected_action"], "note": c["verdict"].get("note", ""),
            "history": ctx.get("history") or [],
            "truth": c["verdict"]["rows_exact"], "row_count": c["verdict"]["row_count"],
            "tps": tps_for(c),
        })
    return out


# ---------------- Markdown ----------------
def gen_markdown(data):
    L = []
    L.append("# FC 黄金集 100 条 · 人工检查测试点清单\n")
    L.append("> 逐条对照 agent 实际输出与 trace 勾选测试点，判定通过/失败并记备注。\n")
    L.append("> 交互可勾选版：`fc100-review-checklist.html`（工作区）。\n")
    L.append("## 检查方法")
    L.append("1. 启动服务后用 `POST /api/chat-fc` 发起问句，取响应的 `trace_id`；")
    L.append("2. `GET /api/trace/{trace_id}` 拿 `decision[] + tool_call[]` 双层轨迹；")
    L.append("3. 对照下面每条测试点逐项核对，**全部勾到才算本 case 通过**；")
    L.append("4. 桌面交互版可记判定/备注并导出结果。\n")
    L.append("## 测试点类别 → 断言抓手速查\n")
    L.append("| 类别 | 看哪里 |")
    L.append("|---|---|")
    L.append("| ①意图路由 | `decision.selected` + 最终回答形态 |")
    L.append("| ②工具选择 | `trace.decision[].selected` / `tool_call[].name` |")
    L.append("| ③工具参数 | `tool_call.tool_args.*` |")
    L.append("| ④顺序依赖 | `tool_call[]` 时序 / `tool_return` |")
    L.append("| ⑤轮次上限 | `trace.decision[]` 长度 ≤ 3 |")
    L.append("| ⑥状态证据 | `tool_call.status` / 生成 SQL |")
    L.append("| ⑦数值真值 | `tool_return` ↔ 冻结真值 |")
    L.append("| ⑧输出禁语 | 最终回答文本 |")
    L.append("| ⑨专项 | 按关注点（安全/多轮/同环比/交付/边界/性能/回归/接地） |")
    L.append("")
    for d in data:
        L.append("---\n")
        L.append("## %s · %s · %s" % (d["id"], d["category"], "/".join(d["focus"]) or "-"))
        L.append("**问句**：%s" % d["query"])
        meta = "业务线=%s ｜ 角色=%s ｜ 第%d轮 ｜ 期望=%s ｜ 难度=%s" % (
            d["line"], d["user"], d["turn"], d["action"], d["difficulty"])
        L.append("**上下文**：%s" % meta)
        if d["history"]:
            L.append("**历史轮**：%s" % json.dumps(d["history"], ensure_ascii=False))
        if d["note"]:
            L.append("**设计意图**：%s" % d["note"])
        L.append("**测试点**：")
        for tp in d["tps"]:
            L.append("- [ ] **%s** %s `(%s)`" % (tp["cat"], tp["text"], tp["via"]))
        if isinstance(d["truth"], dict) and d["truth"]:
            pairs = " ｜ ".join("%s=%s" % (k, fmtval(vv)) for k, vv in d["truth"].items())
            L.append("**冻结真值**（分组数 %s）：%s" % (d["row_count"], pairs))
        elif d["truth"] == {}:
            L.append("**冻结真值**：合法空结果（0 行）")
        L.append("**人工判定**：☐ 通过　☐ 失败　☐ 待查　　备注：____________")
        L.append("")
    with open(MD_OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    return len(L)


# ---------------- HTML ----------------
HTML_TMPL = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FC 黄金集 100 条 · 人工检查清单</title>
<style>
:root{--bg:#f5f6f8;--card:#fff;--ink:#1f2430;--sub:#667085;--line:#e4e7ec;--brand:#3b5bdb;--brand-bg:#edf2ff;
--ok:#12805c;--ok-bg:#e7f6f0;--bad:#c0392b;--bad-bg:#fdecea;--wait:#b7791f;--wait-bg:#fff6e5;}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;font-size:14px;line-height:1.6}
.wrap{max-width:1180px;margin:0 auto;padding:22px 18px 60px}
h1{font-size:23px;margin:0 0 4px}h2{font-size:16px;margin:26px 0 10px}
.muted{color:var(--sub)}.brand{color:var(--brand)}
.hero{background:linear-gradient(120deg,#eef2ff,#f8fafc);border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin-bottom:16px}
.hero p{margin:6px 0 0;color:var(--sub)}
.bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;background:var(--card);border:1px solid var(--line);
border-radius:12px;padding:12px 14px;margin-bottom:14px;position:sticky;top:0;z-index:20;box-shadow:0 2px 10px rgba(16,24,40,.04)}
.bar select,.bar input{border:1px solid var(--line);border-radius:8px;padding:7px 9px;background:#fff;color:var(--ink);font-size:13px}
.bar input[type=search]{min-width:180px}
.btn{border:1px solid var(--line);background:#fff;color:var(--ink);border-radius:8px;padding:7px 11px;cursor:pointer;font-size:13px}
.btn:hover{border-color:var(--brand);color:var(--brand)}
.btn.pri{background:var(--brand);border-color:var(--brand);color:#fff}
.prog{flex:1;min-width:200px;font-size:12px;color:var(--sub)}
.ptrack{height:7px;background:var(--line);border-radius:6px;overflow:hidden;margin-top:4px}
.pfill{height:100%;background:var(--brand);width:0;transition:width .2s}
.case{background:var(--card);border:1px solid var(--line);border-radius:12px;margin-bottom:10px;overflow:hidden}
.case.skip{border-left:4px solid var(--wait)}.case.pass{border-left:4px solid var(--ok)}.case.fail{border-left:4px solid var(--bad)}
.chead{display:flex;gap:10px;align-items:flex-start;padding:13px 15px;cursor:pointer}
.chead:hover{background:#fafbfc}
.cid{font-weight:700;color:var(--brand);font-variant-numeric:tabular-nums;min-width:64px}
.q{flex:1}.q b{font-weight:600}
.chips{display:flex;flex-wrap:wrap;gap:5px;align-items:center}
.chip{font-size:11px;padding:2px 7px;border-radius:20px;background:#f0f2f5;color:var(--sub);white-space:nowrap}
.chip.hp{background:var(--ok-bg);color:var(--ok)}.chip.an{background:var(--bad-bg);color:var(--bad)}
.chip.bd{background:#eef2ff;color:var(--brand)}.chip.fc{background:#eef8ff;color:#0369a1}
.badge{font-size:11px;padding:3px 9px;border-radius:20px;font-weight:700;white-space:nowrap}
.badge.pass{background:var(--ok-bg);color:var(--ok)}.badge.fail{background:var(--bad-bg);color:var(--bad)}
.badge.wait{background:var(--wait-bg);color:var(--wait)}.badge.none{background:#f0f2f5;color:var(--sub)}
.cbody{display:none;border-top:1px solid var(--line);padding:14px 16px 18px}
.case.open .cbody{display:block}
.meta{background:#f8f9fb;border:1px solid var(--line);border-radius:8px;padding:9px 11px;color:var(--sub);font-size:12.5px;margin-bottom:12px}
.meta .n{color:var(--ink)}
.tpgroup{margin-bottom:12px}
.tpcat{font-size:12px;font-weight:700;color:var(--brand);margin:0 0 5px}
.tp{display:flex;gap:8px;align-items:flex-start;padding:5px 8px;border-radius:7px;cursor:pointer}
.tp:hover{background:#f7f8fa}.tp.done{opacity:.62}
.tp input{margin-top:3px;flex:none}
.tptxt{flex:1}.tpvia{color:var(--sub);font-size:11px;font-family:ui-monospace,Menlo,Consolas,monospace}
.truth{margin-top:10px;background:#fffbea;border:1px solid #f3e7b3;border-radius:8px;padding:9px 11px;font-size:12.5px}
.g{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:5px;margin-top:6px}
.g span{background:#fff;border:1px solid var(--line);border-radius:6px;padding:3px 8px;font-variant-numeric:tabular-nums}
.verdict{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:14px;padding-top:12px;border-top:1px dashed var(--line)}
.verdict .btn.sel{color:#fff}.verdict .btn.pass.sel{background:var(--ok);border-color:var(--ok)}
.verdict .btn.fail.sel{background:var(--bad);border-color:var(--bad)}.verdict .btn.wait.sel{background:var(--wait);border-color:var(--wait)}
.verdict textarea{flex:1;min-width:220px;border:1px solid var(--line);border-radius:8px;padding:7px 9px;font-size:12.5px;resize:vertical;min-height:34px}
.guide{background:var(--brand-bg);border:1px solid #d7e0ff;border-radius:10px;padding:12px 15px;margin-bottom:14px;font-size:13px}
.guide ol{margin:6px 0 0;padding-left:20px}code{background:#eef1f6;border-radius:4px;padding:1px 5px;font-size:12px}
@media(max-width:720px){.wrap{padding:14px 10px 40px}h1{font-size:19px}.bar{position:static}.cid{min-width:auto}.q{flex-basis:100%}}
</style></head><body><div class="wrap">
<div class="hero"><h1>FC 黄金集 100 条 · 人工检查清单</h1>
<p class="muted">逐条对照 agent 实际输出与 trace，勾选每个测试点；全部勾到才算通过。判定与备注自动存本机，可导出。</p></div>
<div class="guide"><b>检查方法</b>
<ol><li>启动服务：<code>./run.sh</code>，用 <code>POST /api/chat-fc</code> 发问句，取响应的 <code>trace_id</code></li>
<li><code>GET /api/trace/{trace_id}</code> 拿 <code>decision[] + tool_call[]</code> 双层轨迹</li>
<li>展开下方 case，逐项勾选测试点；类别括号里是断言落点</li>
<li>底部判定通过/失败/待查并写备注 → 顶部「导出结果」归档</li></ol></div>
<div class="bar">
<select id="fCat"><option value="">全部类别</option></select>
<select id="fFocus"><option value="">全部关注点</option></select>
<select id="fLine"><option value="">全部业务线</option></select>
<select id="fJudge"><option value="">全部判定</option><option value="none">未检查</option><option value="pass">通过</option><option value="fail">失败</option><option value="wait">待查</option></select>
<input type="search" id="fSearch" placeholder="搜 ID / 问句">
<button class="btn" id="btnNext">跳到下一条未检查</button>
<button class="btn" id="btnExpand">展开全部</button>
<button class="btn pri" id="btnExport">导出结果</button>
<div class="prog"><span id="ptext">0/100 已检查</span><div class="ptrack"><div class="pfill" id="pfill"></div></div></div>
</div>
<div id="list"></div>
</div>
<script id="DATA" type="application/json">__DATA__</script>
<script>
(function(){
  var DATA=JSON.parse(document.getElementById('DATA').textContent);
  var KEY='fc100_review_v1';
  var state={};
  try{state=JSON.parse(localStorage.getItem(KEY)||'{}')}catch(e){state={}}
  function save(){localStorage.setItem(KEY,JSON.stringify(state))}
  function st(id){if(!state[id])state[id]={tp:{},judge:'',note:''};return state[id]}
  function el(tag,cls,txt){var e=document.createElement(tag);if(cls)e.className=cls;if(txt!=null)e.textContent=txt;return e}
  var list=document.getElementById('list');
  var caseEls={};

  function chip(txt,cls){return el('span','chip '+(cls||''),txt)}
  function judgeBadge(j){var m={pass:'通过',fail:'失败',wait:'待查'};return el('span','badge '+(j||'none'),m[j]||'未检查')}

  function build(d){
    var card=el('div','case');card.dataset.id=d.id;
    var s=st(d.id);
    var head=el('div','chead');
    head.appendChild(el('span','cid',d.id));
    var q=el('div','q');q.appendChild(el('b','',d.query));
    var chips=el('div','chips');
    var cc=d.category==='happy_path'?'hp':(d.category==='adversarial'||d.category==='anomaly'?'an':'bd');
    chips.appendChild(chip(d.category,cc));
    chips.appendChild(chip('业务线:'+d.line));
    chips.appendChild(chip('角色:'+d.user));
    if(d.turn>1)chips.appendChild(chip('第'+d.turn+'轮'));
    d.focus.forEach(function(f){chips.appendChild(chip(f,'fc'))});
    q.appendChild(chips);head.appendChild(q);
    var bj=judgeBadge(s.judge);bj.className+=' jbadge';head.appendChild(bj);
    head.onclick=function(){card.classList.toggle('open')};
    card.appendChild(head);

    var body=el('div','cbody');
    var meta=el('div','meta');
    var n1=el('div','','期望动作 <span class="n">'+d.action+'</span> ｜ 难度 <span class="n">'+d.difficulty+'</span> ｜ 测试点 <span class="n">'+d.tps.length+'</span> 条');
    meta.appendChild(n1);
    if(d.history.length){var n2=el('div','','上轮：'+d.history.map(function(h){return h.query+'('+(h.metrics||[]).join(',')+')'}).join(' → '));meta.appendChild(n2)}
    if(d.note){var n3=el('div','','设计意图：'+d.note);meta.appendChild(n3)}
    body.appendChild(meta);

    var groups={};d.tps.forEach(function(tp,i){(groups[tp.cat]=groups[tp.cat]||[]).push({tp:tp,i:i})});
    Object.keys(groups).forEach(function(cat){
      var g=el('div','tpgroup');g.appendChild(el('div','tpcat',cat));
      groups[cat].forEach(function(o){
        var row=el('label','tp'+(s.tp[o.i]?' done':''));
        var cb=el('input');cb.type='checkbox';cb.checked=!!s.tp[o.i];
        cb.onchange=function(){var ss=st(d.id);ss.tp[o.i]=cb.checked;save();row.classList.toggle('done',cb.checked);refresh()};
        row.appendChild(cb);
        var txt=el('div','tptxt');txt.appendChild(document.createTextNode(o.tp.text+'  '));
        txt.appendChild(el('span','tpvia',o.tp.via));row.appendChild(txt);
        g.appendChild(row);
      });
      body.appendChild(g);
    });

    if(d.truth&&typeof d.truth==='object'&&Object.keys(d.truth).length){
      var tr=el('div','truth');tr.appendChild(el('div','','冻结真值（分组数 '+d.row_count+'）——逐组核对，不得编造'));
      var gg=el('div','g');
      Object.keys(d.truth).forEach(function(k){gg.appendChild(el('span','',k+' = '+JSON.stringify(d.truth[k])))});
      tr.appendChild(gg);body.appendChild(tr);
    }else if(Array.isArray(d.truth)&&d.truth.length===0||d.truth===null&&d.row_count===0){
      body.appendChild(el('div','truth','冻结真值：合法空结果（0 行）'));
    }

    var vd=el('div','verdict');
    [['pass','通过'],['fail','失败'],['wait','待查']].forEach(function(p){
      var b=el('button','btn '+p[0]+(s.judge===p[0]?' sel':''),p[1]);
      b.onclick=function(){var ss=st(d.id);ss.judge=(ss.judge===p[0]?'':p[0]);save();
        vd.querySelectorAll('.verdict .btn').forEach(function(x){x.classList.remove('sel')});
        if(ss.judge)b.classList.add('sel');
        card.classList.remove('pass','fail','skip');if(ss.judge==='pass')card.classList.add('pass');
        else if(ss.judge==='fail')card.classList.add('fail');else if(ss.judge==='wait')card.classList.add('skip');
        head.querySelector('.jbadge').outerHTML='';head.appendChild(judgeBadge(ss.judge));
        refresh()};
      vd.appendChild(b);
    });
    var ta=el('textarea');ta.placeholder='备注（问题现象 / 复现命令 / 缺陷号）';ta.value=s.note||'';
    ta.oninput=function(){st(d.id).note=ta.value;save()};
    vd.appendChild(ta);body.appendChild(vd);
    card.appendChild(body);
    caseEls[d.id]={card:card,head:head};
    list.appendChild(card);
    applyJudgeClass(d.id);
  }
  function applyJudgeClass(id){var c=caseEls[id].card,j=st(id).judge;
    c.classList.remove('pass','fail','skip');if(j==='pass')c.classList.add('pass');else if(j==='fail')c.classList.add('fail');else if(j==='wait')c.classList.add('skip')}

  // filter options
  function fill(sel,vals){var s=document.getElementById(sel);vals.forEach(function(v){var o=document.createElement('option');o.value=v;o.textContent=v;s.appendChild(o)})}
  fill('fCat',Array.from(new Set(DATA.map(function(d){return d.category}))).sort());
  fill('fFocus',Array.from(new Set(DATA.reduce(function(a,d){return a.concat(d.focus)},[]))).sort());
  fill('fLine',Array.from(new Set(DATA.map(function(d){return d.line}))).sort());

  function refresh(){
    var checked=0,total=0,pass=0,fail=0,wait=0;
    DATA.forEach(function(d){var s=st(d.id);if(s.judge==='pass')pass++;else if(s.judge==='fail')fail++;else if(s.judge==='wait')wait++;
      d.tps.forEach(function(_,i){total++;if(s.tp[i])checked++})});
    var done=pass+fail+wait;
    document.getElementById('ptext').textContent=done+'/'+DATA.length+' 已判定 ｜ 通过 '+pass+' 失败 '+fail+' 待查 '+wait+' ｜ 测试点 '+checked+'/'+total+' 已勾';
    document.getElementById('pfill').style.width=(100*done/DATA.length)+'%';
  }
  function applyFilter(){
    var cat=document.getElementById('fCat').value,foc=document.getElementById('fFocus').value,
        line=document.getElementById('fLine').value,jud=document.getElementById('fJudge').value,
        q=document.getElementById('fSearch').value.trim().toLowerCase();
    DATA.forEach(function(d){var s=st(d.id),show=true;
      if(cat&&d.category!==cat)show=false;
      if(foc&&d.focus.indexOf(foc)<0)show=false;
      if(line&&d.line!==line)show=false;
      if(jud==='none'&&s.judge)show=false;if(jud&&jud!=='none'&&s.judge!==jud)show=false;
      if(q&&(d.id+' '+d.query).toLowerCase().indexOf(q)<0)show=false;
      caseEls[d.id].card.style.display=show?'':'none'});
  }
  ['fCat','fFocus','fLine','fJudge'].forEach(function(id){document.getElementById(id).onchange=applyFilter});
  document.getElementById('fSearch').oninput=applyFilter;
  document.getElementById('btnExpand').onclick=function(){var cs=document.querySelectorAll('.case');var anyClosed=Array.prototype.some.call(cs,function(c){return !c.classList.contains('open')});
    cs.forEach(function(c){c.classList.toggle('open',anyClosed)})};
  document.getElementById('btnNext').onclick=function(){for(var i=0;i<DATA.length;i++){var d=DATA[i];if(!st(d.id).judge){var c=caseEls[d.id].card;
      c.classList.add('open');c.scrollIntoView({behavior:'smooth',block:'center'});return}}alert('全部 case 均已判定')};
  document.getElementById('btnExport').onclick=function(){
    var out=DATA.map(function(d){var s=st(d.id);return{id:d.id,query:d.query,category:d.category,focus:d.focus,
      judge:s.judge||'',note:s.note||'',testpoints:d.tps.map(function(tp,i){return{cat:tp.cat,text:tp.text,via:tp.via,checked:!!s.tp[i]}})}});
    var blob=new Blob([JSON.stringify(out,null,2)],{type:'application/json'});var a=document.createElement('a');
    a.href=URL.createObjectURL(blob);a.download='fc100_人工检查记录.json';a.click();URL.revokeObjectURL(a.href)};

  DATA.forEach(build);refresh();
})();
</script></body></html>"""


def gen_html(data):
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    html = HTML_TMPL.replace("__DATA__", payload)
    with open(HTML_OUT, "w", encoding="utf-8") as f:
        f.write(html)
    return len(html)


def main():
    cases = load()
    if len(cases) != 100:
        print("WARN: 期望 100 条，实际 %d 条" % len(cases), file=sys.stderr)
    data = build_data(cases)
    ntp = sum(len(d["tps"]) for d in data)
    nline = gen_markdown(data)
    nbyte = gen_html(data)
    print("case=%d, 测试点=%d, 平均=%.1f 条/case" % (len(data), ntp, ntp / len(data)))
    print("MD: %s (%d 行)" % (MD_OUT, nline))
    print("HTML: %s (%d 字节)" % (HTML_OUT, nbyte))


if __name__ == "__main__":
    main()
