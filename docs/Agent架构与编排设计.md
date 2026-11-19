# Agent 架构与编排设计文档

> 文档版本：v1.0　更新日期：2026-11-19　状态：与 v1.1 代码基线一致
> 关联：[技术方案拆解.md](../技术方案拆解.md)（编译/权限/测试原理）、[Tools 函数文档](Tools函数文档.md)、[Prompt 快照](SystemPrompt版本快照.md)
> 定位：描述系统分层、双链路编排、状态机、多轮流转、记忆、循环终止与防死循环策略。

---

## 1. 总体架构（分层）

系统采用"**理解与执行解耦、确定性主导、可观测贯穿**"的分层架构：

```
┌─────────────────────────────────────────────────────────┐
│ 接入层  src/api/main.py  FastAPI                        │
│   /api/chat  /api/chat-fc  /api/export  /api/drilldown   │
│   /api/trace  /api/history  /api/calibers ...           │
└───────────────┬─────────────────────────────────────────┘
                │
┌───────────────▼─────────────────────────────────────────┐
│ 编排层  src/core/orchestrator.py                        │
│   chat()      —— 链路A：规则编排（确定性流水线）         │
│   chat_fc()   —— 链路B：模型驱动（function calling 循环）│
└───────┬──────────────────────────────┬──────────────────┘
        │                              │
┌───────▼────────────┐   ┌─────────────▼──────────────────┐
│ 理解层             │   │ 工具层（链路B）                 │
│  intent.py         │   │  tool_schemas.py  工具定义/校验 │
│  time_parser.py    │   │  dispatcher.py   校验/执行/打点 │
│  llm.py（可插拔）   │   │  _TURN_CTX       turn 级上下文  │
└───────┬────────────┘   └─────────────┬──────────────────┘
        │                              │
┌───────▼──────────────────────────────▼──────────────────┐
│ 执行层                                                   │
│  QueryPlan(models.py) → nl2sql.compile → validator      │
│  → executor.run（只读 SQLite）→ permission 下推/脱敏      │
└───────┬──────────────────────────────────────────────────┘
        │
┌───────▼──────────────────────────────────────────────────┐
│ 支撑层                                                   │
│  rag.py 口径检索  memory/ 会话与长期记忆  export/ 导出    │
│  observability/ audit + metrics + trace   defects/ 缺陷  │
└──────────────────────────────────────────────────────────┘
```

**核心契约：QueryPlan 是枢纽**（`models.py`）。理解侧只产出 Plan，执行侧只消费 Plan，两侧独立演进、独立测试。LLM 不直接写 SQL。

---

## 2. 双链路编排

系统保留两条并存的主链路，便于 A/B 对比与降级：

| 维度 | 链路 A：`chat()` 规则编排 | 链路 B：`chat_fc()` 模型驱动 |
| --- | --- | --- |
| 决策者 | Python 代码（写死流水线） | LLM（function calling 选工具） |
| LLM 角色 | 只建议 metric/dimension/business_line 槽位 | 选工具 + 填参数 |
| 流程 | `build_plan → RAG → compile → run → 呈现` | `模型决策 → 工具执行 → 结果回填 → 再决策` 循环 |
| 确定性 | 高（同输入同输出） | 有随机性（temperature=0 降低） |
| 端点 | `/api/chat` | `/api/chat-fc` |
| 打点 | audit + metrics | audit + metrics + **trace（decision/tool_call）** |
| 适用 | 稳定主流程、测试基线 | Agent 工具选择评测、能力上限探索 |

### 2.1 链路 A：规则编排流水线

```
build_plan(意图解析)
   → RAG.search(口径补全)
   → compile(QueryPlan→SQL, 注入权限)
   → validator.validate(只读校验)
   → executor.run(执行)
   → (可选) compare_period(对比)
   → 出口判定(result/clarify/fallback)
   → 呈现(表格/图表/SQL)
```

LLM 在 `build_plan` 末尾通过 `_llm_refine()` 建议槽位，但被白名单过滤（见 Prompt 快照 SP-INTENT-001）。

### 2.2 链路 B：模型驱动循环（详见 §4）

模型每轮返回 `tool_calls`，编排层执行并回填 `tool` 消息，直到模型停止调用或达最大轮次。这是"Agent 专属"指标（工具调用正确性、轮次开销）的数据源。

---

## 3. 状态机

### 3.1 请求级状态机（单次 chat-fc）

```
        ┌─────────┐
        │ INIT    │  生成 turn_id / 载入用户 / 检查 provider
        └────┬────┘
             ▼
        ┌─────────┐
   ┌───▶│ DECIDE  │  LLM complete_with_tools → tool_calls
   │    └────┬────┘
   │         │ 无 calls / finish=stop
   │         ├──────────────▶ ┌──────────┐
   │         │                │ FINALIZE │ → result / fallback
   │         ▼                └──────────┘
   │    ┌─────────┐
   │    │ EXECUTE │  dispatcher.execute（校验+执行+打点）
   │    └────┬────┘
   │         ▼
   │    ┌─────────┐
   │    │ FEEDBACK│  回填 assistant.tool_calls + tool 消息
   │    └────┬────┘
   │         │ round < MAX_ROUNDS
   └─────────┘
             │ round == MAX_ROUNDS → 记 tool_round_limit → FINALIZE
```

### 3.2 响应态状态机

```
result   ← 取到数据
clarify  ← 歧义/缺槽位（等待用户补充，可回到 INIT）
fallback ← 低置信/无法确定（等待用户补充）
error    ← 权限/能力/系统异常（终止）
```

`clarify` / `fallback` 是可恢复态：用户补充后携带 `session_id` 重新进入 INIT。

---

## 4. 多轮流转与循环控制

### 4.1 模型驱动循环（chat_fc 核心）

```python
for rnd in range(1, dispatcher.MAX_ROUNDS + 1):   # MAX_ROUNDS = 3
    resp = provider.complete_with_tools(messages, tools)   # 模型决策
    if resp is None: break                          # 终止条件①：调用失败
    calls = resp.get("tool_calls") or []
    trace.decision(turn_id, rnd, calls, ...)        # 决策打点
    if not calls: break                             # 终止条件②：模型主动停止
    messages.append(assistant 消息(tool_calls))      # 协议要求原样回填
    for c in calls:
        out = dispatcher.execute(turn_id, rnd, c, user)   # 校验+执行+打点
        messages.append(tool 消息(out))              # 结果回填给模型
        if c.name == "query_metrics" and out.status == "ok":
            last_result = out.result
    if rnd == MAX_ROUNDS: metrics.incr("tool_round_limit")  # 超限打点
```

### 4.2 循环终止条件

| 条件 | 触发 | 后续 |
| --- | --- | --- |
| ① 调用失败 | `complete_with_tools` 返回 None | 跳出，若 last_result 空 → fallback |
| ② 模型停止 | `tool_calls` 为空 / finish_reason=stop | 正常跳出 → result/fallback |
| ③ 轮次超限 | 达到 `MAX_ROUNDS=3` | 记 `tool_round_limit` 指标 → FINALIZE |
| ④ 权限拒绝 | load_user 抛 PermissionDenied | 直接 error |

### 4.3 防死循环策略

| 策略 | 实现 | 作用 |
| --- | --- | --- |
| 硬轮次上限 | `MAX_ROUNDS=3` | 杜绝无限决策 |
| 超限指标 | `metrics.incr("tool_round_limit")` | 监控"反复查表"的坏规划 |
| 结果摘要化 | tool 结果只回摘要（row_count/sql） | 防止上下文膨胀拖垮循环 |
| 依赖检查 | 后置工具无前置直接 error | 防止无意义重试 |
| temperature=0 | 配置锁定 | 降低输出抖动与震荡 |
| 协议原样回填 | assistant.tool_calls 带 id 回传 | 避免模型因协议错位反复调用 |

---

## 5. 记忆与多轮

### 5.1 三层记忆

| 层级 | 载体 | 生命周期 | 用途 |
| --- | --- | --- | --- |
| 短期（会话槽） | `session_store`（内存） | ttl=60min，max 10 轮 | 指代消解（"那华东呢？"继承上轮主体/时间/维度） |
| 长期（偏好/历史） | `memory/long_term.py`（JSON 落盘） | 跨会话 | 常用业务线偏好、查询历史、反馈纠错 |
| turn 级上下文 | `dispatcher._TURN_CTX`（按 turn_id） | 单次请求 | 后置工具链（取数结果→导出/下钻） |

### 5.2 多轮流转

```
第1轮：用户问"近7日各城市有效订单量"
   → build_plan → 结果 + session_id
第2轮：用户问"那华东呢？"（带 session_id）
   → session_store 读取上轮 slots → 继承 time/metric/dim，替换 region=华东
   → build_plan(inherited=True) → 重新取数
```

**会话隔离**：`inherited` 槽位按 session_id 取，跨会话不共享（缺陷开关 D14 用于验证隔离有效性）。

### 5.3 turn 级上下文隔离

`_TURN_CTX` 是 `dict[turn_id, dict]`，`query_metrics` 成功后写入 `last_result / last_plan / last_meta`，仅供**同一 turn** 的 `export_result` / `drilldown_detail` 读取。跨 turn 读不到 → 报 error，防止串台。

---

## 6. 工具分工（无独立子 Agent）

当前实现采用"**能力工具**"而非"多子 Agent"：

- 不按业务线拆工具（retail/logistics/performance 是 `query_metrics` 的参数，不是独立工具）。
- 按**能力种类**拆 7 个工具（见 [Tools 函数文档](Tools函数文档.md)）。
- 工具无独立 LLM 循环，由 dispatcher 同步执行。

> 未来若引入子 Agent（如"口径专家 Agent""取数 Agent"），编排层扩展为层级调度，但 trace 打点仍落在统一 `dispatcher.execute()`，测试断言不变。

---

## 7. 可观测设计（双层 Trace + Audit）

### 7.1 三类观测数据

| 类型 | 文件 | 记录内容 | 用途 |
| --- | --- | --- | --- |
| Audit | `logs/audit.jsonl` | 业务事实（谁/何时/查了什么/导出） | 合规追溯 |
| Metrics | 内存快照 `/api/metrics` | 计数（query / tool_round_limit…） | 轮次开销指标 |
| Trace | `logs/trace.jsonl` | decision + tool_call 双层 | Agent 决策与执行评测 |

### 7.2 Trace 双层结构

| kind | 记录方 | 关键字段 |
| --- | --- | --- |
| `decision` | `chat_fc` 每轮 | `round` / `selected`（工具名列表）/ `raw`（原始 calls）/ `finish_reason` |
| `tool_call` | `dispatcher.execute` 每次 | `tool_name` / `tool_args` / `tool_return`（摘要）/ `status` / `latency_ms` / `error` |

**分层意义**：`decision` 回答"模型选对没"，`tool_call` 回答"执行对没"。混在一起就分不清是模型的锅还是工具的锅。

### 7.3 从 Trace 计算的 Agent 指标

| 指标 | 计算 |
| --- | --- |
| 工具调用正确率 | decision.selected vs 期望工具集合 |
| 参数正确率 | tool_call.tool_args 过 Schema 校验 |
| 无效调用占比 | `status in {empty,error}` 的比例 |
| 冗余调用 | 相同 (tool_name, args) 出现 >1 |
| 缺失调用 | 期望集合 − 实际集合非空 |
| 平均轮次 | 每次请求 decision 条数均值 |
| 超限占比 | 达 MAX_ROUNDS 的请求比例 |

---

## 8. 关键设计决策（Why）

| 决策 | 理由 |
| --- | --- |
| QueryPlan 做枢纽契约 | 理解/执行解耦，可独立测试，换模型不影响 SQL 质量 |
| 规则主导 + LLM 增强 | 权限/SQL 用确定性代码，LLM 只填槽位/选工具，安全可控 |
| 宽表视图 | join 固化在 DB，编译器面向单表，权限注入点唯一 |
| 权限 SQL 层下推 | 聚合指标事后过滤不成立，且越权数据不进内存 |
| 双链路并存 | 稳定主流程 + Agent 能力探索，A/B 对比素材 |
| dispatcher 统一打点 | 工具选择与执行分层留痕，机制测试与模型评测分离 |
| 双层 trace 摘要化 | 决策与执行分开、结果只存摘要，防 trace 爆炸 |
| turn 级 ctx 隔离 | 支持有状态工具链测试，同时防跨请求串台 |
