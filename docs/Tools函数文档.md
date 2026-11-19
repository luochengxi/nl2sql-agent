# Tools（Function Calling）文档

> 文档版本：v1.0　更新日期：2026-11-19　状态：与 v1.1 代码基线一致
> 事实来源：`src/core/tool_schemas.py`（定义/校验）、`src/core/dispatcher.py`（执行/上下文）、`src/export/exporter.py`、`src/core/drilldown.py`
> 定位：工具 Schema、权限、调用约束、幂等、错误码、超时重试的完整契约，是"工具调用正确性"测试断言的源头。

---

## 1. 工具总览

共 **7 个能力工具**，按能力种类拆分（**不是**按业务线拆；`business_line` 是 `query_metrics` 的参数）。

| # | 工具名 | 类别 | 状态性 | 依赖 | 用途 |
| --- | --- | --- | --- | --- | --- |
| T1 | `query_metrics` | 主取数 | 无状态（写 ctx） | 无 | 按业务线/指标/维度取聚合数据 |
| T2 | `search_caliber` | 知识 | 无状态 | 无 | 检索指标口径文档 |
| T3 | `compare_period` | 分析 | 无状态 | 无 | 周期对比（回执参数） |
| T4 | `get_data_dictionary` | 知识 | 无状态 | 无 | 查指标/维度/业务线清单 |
| T5 | `export_result` | 交付 | **有状态** | 需 T1 成功 | 导出当前结果为 CSV/Excel |
| T6 | `drilldown_detail` | 分析 | **有状态** | 需 T1 成功 | 展开当前结果明细 |
| T7 | `query_history` | 效率 | 无状态（读外部状态） | 无 | 查用户历史问句 |

> **状态性判据**：同样参数调两次，结果是否依赖参数之外的隐藏状态。T5/T6 读 `_TURN_CTX`（参数外），是真·有状态工具；T7 读落盘历史，可当无状态读操作测。

---

## 2. 工具 Schema（入参 / 出参 / 枚举 / 必填 / 类型）

> 定义格式遵循 OpenAI function calling spec，由 `tools_schema()` 从配置**动态生成枚举**（不硬编码）：`business_line` 取自 `business_lines.yaml`，`metrics`/`dimensions` 取自 `semantic_layer.yaml`。

### T1 `query_metrics`

**描述**：按业务线和指标取聚合数据。零售销售查 retail，物流时效查 logistics，绩效查 performance；查错业务线会取到错误的表。

| 参数 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- |
| `business_line` | string | ✅ | enum：`retail`/`logistics`/`performance` | 业务线 |
| `metrics` | array[string] | ✅ | items enum：语义层 13 个 metric id | 指标列表 |
| `dimensions` | array[string] | ❌ | items enum：7 个 dimension id | 维度列表 |
| `date_start` | string | ✅ | pattern `^\d{4}-\d{2}-\d{2}$` | 起始日期 |
| `date_end` | string | ✅ | pattern `^\d{4}-\d{2}-\d{2}$` | 结束日期 |

**出参**（`dispatcher` 摘要）：
```json
{ "row_count": 9, "columns": ["city_name","valid_order_cnt"],
  "rows": [[...]],            // 最多 5 行摘要
  "sql": "SELECT ...", "view": "v_retail_order" }
```
**副作用**：`result.rows` 非空时写入 `_TURN_CTX[turn_id]` 的 `last_result`（全量）/`last_plan`/`last_meta`，供 T5/T6 使用。

### T2 `search_caliber`

| 参数 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `query` | string | ✅ | — |
| `business_line` | string | ❌ | enum：`retail`/`logistics`/`performance`/`all` |

**出参**：`{ "doc_count": int, "docs": [文档引用...] }`（top_k=3）

### T3 `compare_period`

| 参数 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `business_line` | string | ✅ | enum：三条业务线 |
| `compare_type` | string | ✅ | enum：`mom`/`yoy`/`dod` |

**出参**：`{ "compare_type", "business_line", "note" }`
> ⚠️ **当前为回执实现**：只回参数，不取真实对比期数据。对比值由编排层用锚定窗口补齐（待增强）。

### T4 `get_data_dictionary`

| 参数 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `business_line` | string | ❌ | enum：三条业务线 + `all` |

**出参**：`{ "business_lines": [...], "metrics": [...], "dimensions": [...] }`

### T5 `export_result`（有状态）

| 参数 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `fmt` | string | ✅ | enum：`csv`/`xlsx` |

**出参**：`{ "token": str, "fmt": str, "row_count": int }`
**前置**：`_TURN_CTX[turn_id].last_result` 必须存在，否则 `ValueError`。
**副作用**：调用 `create_export_token` 把全量结果缓存为凭证（token 绑定 user_id）。

### T6 `drilldown_detail`（有状态）

| 参数 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `reason` | string | ❌ | — |

**出参**：`{ "token": str, "note": str }`
**前置**：`_TURN_CTX[turn_id].last_plan` 必须存在，否则 `ValueError`。
**副作用**：调用 `create_drilldown_token` 缓存下钻计划凭证。

### T7 `query_history`

| 参数 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `limit` | integer | ❌ | 默认 5 |

**出参**：`{ "history_count": int, "recent": [问句...] }`（读 `long_term.history`）

---

## 3. 通用执行契约：`dispatcher.execute()`

所有工具调用必经统一入口，流程固定：

```
校验(validate_call) → 执行(handler) → 打点(trace.tool_call) → 返回
```

**返回结构固定**（三字段，供编排层与测试消费）：
```json
{ "status": "ok|empty|error", "error": null|"原因", "result": {摘要} }
```

| status | 含义 | 触发 |
| --- | --- | --- |
| `ok` | 成功 | 校验通过且执行有结果 |
| `empty` | 空结果 | `row_count==0` 或 `doc_count==0` |
| `error` | 失败 | 校验失败 / handler 抛异常 / 未知工具 |

**关键设计**：`execute()` **不抛异常**，把失败转成 `status=error` 写入 trace——这样"无效调用"成为可断言的数据，而非被异常吞掉。

---

## 4. 校验规则与错误码（`validate_call`）

`validate_call(name, args) -> str | None`，返回 `None` 表示通过，否则返回错误码字符串（记为无效调用）：

| 错误码 | 含义 | 触发示例 |
| --- | --- | --- |
| `unknown_tool:{name}` | 工具未注册 | 模型编造工具名 |
| `missing:{k}` | 缺必填参数 | query_metrics 未传 date_start |
| `extra_arg:{k}` | 多余参数 | search_caliber 传了未定义的 bad 参数 |
| `enum:{k}={v}` | 枚举值非法 | business_line="warehouse" |
| `pattern:{k}={v}` | 正则不匹配 | date_start="11/01" |
| `type:{k}` | 类型错误 | limit 传字符串、metrics 传非数组 |

**校验顺序**：先查必填 `missing` → 再逐参数查 `extra_arg`/`enum`/`pattern`/`type`（array/integer/string 三种类型检查）。

---

## 5. 工具权限与调用约束

### 5.1 权限继承

工具不单独鉴权，统一继承调用方 `UserContext`（`load_user` 一次解析），在 `query_metrics` 的 compile 阶段注入行级/列级权限：

- 所有工具执行前 `chat_fc` 已 `load_user`，未知账号直接 error。
- 导出凭证绑定 user_id（D16 关闭时二次校验），下钻凭证同样绑定。
- 后置工具读的是 T1 **已裁剪**的结果，不会出现"界面看不到、导出却有"的越权。

### 5.2 调用约束与依赖链

```
                 ┌──────────────┐
                 │ query_metrics│  主取数（可单独调用）
                 └──────┬───────┘
             写 ctx     │     写 ctx
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
 ┌─────────────┐ ┌─────────────┐ ┌──────────┐
 │export_result│ │drilldown_   │ │ (后续可扩)│
 │  (T5)       │ │detail (T6)  │ │          │
 └─────────────┘ └─────────────┘ └──────────┘

 无依赖工具：T2 search_caliber / T3 compare_period / T4 get_data_dictionary / T7 query_history
```

| 约束 | 说明 |
| --- | --- |
| T5/T6 必须在 T1 之后 | 单句"导出"若无前置取数 → error（模型选对工具但场景缺前置） |
| ctx 按 turn_id 隔离 | 同 turn 内串联；跨 turn 不继承（已知边界：跨两次请求导出测不了） |
| T1 空结果不写 ctx | 防止后置工具拿到脏数据 |
| 一次可多工具 | 模型可在同一轮返回多个 tool_calls，顺序执行 |

### 5.3 测试影响

- **无状态工具**：直接喂参数，断言 `status` 与结果值，确定性 100%。
- **有状态工具（T5/T6）**：测三态——无 ctx 报错 / 有 ctx 验值 / 跨 turn 隔离。
- **模型选工具层**：软断言（成功率），真实 LLM 有随机性。

---

## 6. 幂等规则

| 工具 | 幂等性 | 说明 |
| --- | --- | --- |
| T1/T2/T3/T4/T7 | 幂等 | 只读，同样参数结果一致（同一时刻） |
| T5 `export_result` | **非幂等** | 每次生成新 token，但导出内容一致 |
| T6 `drilldown_detail` | **非幂等** | 每次生成新 token，明细内容一致 |
| `/api/export`（token 消费） | 幂等读 | token 未设一次性消费，可重复导出 |
| `/api/feedback` | 覆盖写 | 同 query_id 重复反馈覆盖旧记录 |

> 幂等规则影响"冗余调用"指标：T1 重复调用是可疑冗余（同样取数两次）；T5/T6 重复调用产生不同 token，需结合 args+语义判断。

---

## 7. 超时与重试策略

### 7.1 当前实现的超时

| 环节 | 超时 | 位置 |
| --- | --- | --- |
| `complete_json` | 20s | `llm.py` urlopen |
| `complete_with_tools` | 30s | `llm.py` urlopen |
| 导出上限 | 50000 行 | `settings.yaml` `export.max_rows` |
| 会话 TTL | 60 min / 10 轮 | `settings.yaml` `session` |

### 7.2 重试策略（现状 vs 建议）

| 项 | 当前实现 | 说明 |
| --- | --- | --- |
| 工具执行重试 | **无自动重试** | 失败直接记 `status=error` 回填模型，由模型决定是否重试 |
| LLM 调用重试 | **无自动重试** | 异常返回 None → 跳出循环 |
| 模型驱动重试 | 间接存在 | 模型看到 error 的 tool 消息后可能换参数再调（受 MAX_ROUNDS 限制） |

> 设计取向：把重试决策交给模型（通过 tool 结果回填），而非代码层硬重试——这样 trace 能完整记录"模型如何纠错重试"，本身就是评测素材。若后续要加代码层重试，需注意指数退避 + 最大次数，避免放大无效调用指标。

### 7.3 错误回填协议

工具失败时不中断循环，而是把 `{"status":"error","error":原因}` 作为 tool 消息回填给模型（`chat_fc` 的 `messages.append({"role":"tool",...})`），让模型"看见"错误并决策下一步。这是模型驱动循环能自纠的关键。

---

## 8. 与 API 端点的对应

| 工具 | 对应 HTTP 端点 | 说明 |
| --- | --- | --- |
| T1–T7 统一入口 | `POST /api/chat-fc` | 模型驱动循环内调用 |
| 取数（链路A） | `POST /api/chat` | 规则编排，不走工具层 |
| T5 凭证消费 | `GET /api/export?token=&user_id=&fmt=` | 真正下载文件 |
| T6 凭证消费 | `POST /api/drilldown` | 真正执行明细 SQL |
| T7 | `GET /api/history?user_id=&limit=` | 直接读历史 |
| trace 查询 | `GET /api/trace/{turn_id}` | 捞决策与执行记录 |

> 注意：T5/T6 工具本身只**生成凭证**，真正的文件下载/明细执行在独立端点完成。这样凭证可绑定 user_id 二次校验，且凭证与结果解耦。

---

## 9. 可测试性对照

| 测试目标 | 断言点 |
| --- | --- |
| 工具选对 | `trace.decision.selected` vs 期望工具集合 |
| 参数传对 | `validate_call` 单测 + `trace.tool_call.tool_args` |
| 调用成功 | `trace.tool_call.status == ok` + 结果值 |
| 无效调用 | `status in {empty,error}` |
| 冗余/缺失调用 | 相同签名去重 / 期望集合差 |
| 依赖链 | 有状态工具三态测试 |
| 超时/超限 | `tool_round_limit` 指标 + `MAX_ROUNDS` |
