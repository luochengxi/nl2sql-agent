# Agent 能力契约（Capability Spec）

> 文档版本：v1.0　更新日期：2026-11-19　状态：与 v1.1 代码基线一致
> 关联：[PRD](PRD业务需求文档.md)、[架构与编排](Agent架构与编排设计.md)、[Guardrail](Guardrail安全规则文档.md)
> 定位：定义 Agent "**必须做 / 禁止做 / 需人工审批或升级**"的行为边界，是安全与行为测试断言的源头。

---

## 1. 业务目标与契约总览

本 Agent 的唯一业务目标是：**在只读、权限隔离、口径一致的前提下，把自然语言问句转成可信可追溯的数据结果**。一切能力围绕该目标，任何偏离（写操作、越权、编造、静默猜测）都是契约违约。

契约由四部分构成：

| 部分 | 含义 | 违约后果 |
| --- | --- | --- |
| MUST（必须做） | 正常路径必须履行的义务 | 功能缺陷 |
| MUST NOT（禁止做） | 任何情况都不得触碰的红线 | 安全事故 |
| ESCALATE（需审批/升级） | 不确定时必须交还人类 | 错误决策 |
| 禁止动作清单 | 不可逆/写操作的显式枚举 | 数据损坏 |

---

## 2. MUST — 必须做（能力义务）

| # | 义务 | 判定标准 | 代码落点 |
| --- | --- | --- | --- |
| M1 | 只读取数 | 生成 SQL 必为单条 SELECT | `validator.validate` |
| M2 | 行级隔离 | 权限条件注入 SQL 的 WHERE，参数化 | `permission.row_filter_clause` |
| M3 | 列级脱敏 | 禁用列剔除、敏感列按模板脱敏 | `permission.denied_columns` / `mask_expression` |
| M4 | 口径一致 | 指标按语义层 expr 编译，附口径引用 | `semantic_layer.yaml` / `caliber_refs` |
| M5 | SQL 透明 | result 态返回 SQL 与映射说明 | `ChatResponse.sql` / `explanation` |
| M6 | 结果可追溯 | 每个请求生成 trace_id，记录决策与执行 | `trace.new_turn` / `decision` / `tool_call` |
| M7 | 歧义必澄清 | 同名不同口径/缺主体/时间不清时反问 | `intent.clarify` → `answer_type=clarify` |
| M8 | 低置信兜底 | 置信度 < 0.60 转 fallback，不硬编 SQL | `intent.min_confidence` |
| M9 | 后置工具依赖检查 | 导出/下钻前必须有 query_metrics 结果 | `dispatcher.export_result` / `drilldown_detail` |
| M10 | 审计留痕 | 查询/导出/下钻/口径变更均记录 | `audit.log` |
| M11 | 多轮记忆隔离 | 会话槽内继承槽位，跨会话不串数据 | `session_store`（ttl=60min，max 10 轮） |
| M12 | 工具参数校验 | 每次工具调用先过 Schema 校验再执行 | `tool_schemas.validate_call` |

---

## 3. MUST NOT — 禁止做（红线）

| # | 禁止行为 | 具体表现 | 防线 |
| --- | --- | --- | --- |
| N1 | 禁止写操作 | 不得生成 INSERT/UPDATE/DELETE/DDL | validator 关键字黑名单 + 只读库 |
| N2 | 禁止注入 | 不得多语句、注释绕过、参数夹带控制字符 | validator `_FORBIDDEN` / `_COMMENT` |
| N3 | 禁止非白名单表 | 不得引用业务线宽表视图以外的表/子查询 | validator 视图白名单 |
| N4 | 禁止越权 | 不得放大可见范围、不得泄露禁用列 | 权限下推 + 列策略 |
| N5 | 禁止编造指标 | 不得输出语义层白名单外的 metric id | intent 白名单过滤 + 工具 enum 校验 |
| N6 | 禁止猜测 SQL | 无法确定业务线/指标时不得硬编 | fallback 分支 |
| N7 | 禁止编造工具参数 | 不得传 Schema 未定义的参数 | `validate_call` → `extra_arg` |
| N8 | 禁止跨 turn 串上下文 | 后置工具不得读取他人 ctx | `_TURN_CTX` 按 turn_id 隔离 |
| N9 | 禁止泄露内部信息 | 错误响应不得暴露堆栈、密钥、内部路径 | error 态仅回原因文本 |
| N10 | 禁止反问替代工具调用（function calling 链路） | chat_fc 下模型必须调工具，不得只反问要日期 | system prompt 约束 + 日期锚点 |

---

## 4. ESCALATE — 需人工审批 / 升级的边界

Agent 在以下情形**必须停下并交还人类决策**，不得自主推进：

| 场景 | 触发条件 | 升级动作 | 响应态 |
| --- | --- | --- | --- |
| 指标歧义 | "销售额"同时命中 retail/pay_amount 与 performance/perf_gmv | 列出候选让用户选业务线 | `clarify` |
| 必要槽位缺失 | 无指标、主体缺失、时间完全不清 | 反问具体缺失项 + suggestions | `clarify` |
| 低置信 | confidence < 0.60 | 建议补充描述或转数据团队 | `fallback` |
| 无法生成 | 三轮工具决策仍未拿到数据 | 提示补充业务线与指标 | `fallback` |
| 权限拒绝 | 账号未知 / 指标依赖禁用字段 | 拒绝并说明无权限 | `error` |
| Provider 不支持 | 当前 provider 无 function calling 能力 | 提示能力不可用 | `error` |
| 口径变更 | 产品/运营修改口径文档 | 需重建 RAG 索引后生效 | 管理后台流程 |

> **设计原则**：Agent 只在"确定且安全"的区间自主行动；一旦进入"不确定"或"不可逆"区间，必须升级给人类。这既保护数据，也让测试有明确的边界断言点。

---

## 5. 禁止动作清单（不可逆操作 / 写操作权限）

### 5.1 数据库层禁止动作

以下 SQL 关键字被 `validator._FORBIDDEN` 显式拦截（缺陷开关 D08 关闭时生效）：

```
INSERT  UPDATE  DELETE  DROP  ALTER  CREATE  TRUNCATE  REPLACE
ATTACH  DETACH  PRAGMA  VACUUM  EXEC  EXECUTE  REINDEX  LOAD_EXTENSION
```

同时禁止：
- 多语句（`;`，D07 关闭时拦截）
- SQL 注释（`--`、`/*`、`#`，D07 关闭时拦截）
- 子查询（`FROM (`）
- 非白名单表引用

### 5.2 写权限声明

| 主体 | 写权限 | 说明 |
| --- | --- | --- |
| Agent（查询链路） | **无** | 数据库 `readonly=true`，只能 SELECT |
| 产品/运营（口径后台） | 仅口径文档 | 通过 `/api/calibers` 增删改口径 md，不涉及业务数据 |
| 用户反馈 | 仅反馈记录 | `/api/feedback` 写 `long_term.json`，不改查询结果 |

> 口径文档编辑与反馈记录是"**元数据写入**"，不是业务数据写入，属于可控的可逆操作；业务数仓对 Agent 全程只读。

### 5.3 不可逆动作界定

本项目**不存在不可逆的用户侧动作**：导出生成凭证、下钻展开明细均为只读派生。真正需要谨慎的"口径变更"通过版本管理与索引重建流程约束（可回滚），见需求说明书 §4.4。

---

## 6. 能力分级与契约强度

| 级别 | 能力 | 契约强度 |
| --- | --- | --- |
| P0 核心 | 取数、只读安全、权限隔离、口径 RAG、澄清兜底、导出 | 强契约：违约即阻断 |
| P1 重要 | 对比、下钻、业务线切换、审计、口径后台 | 中契约：违约记缺陷 |
| P2 增强 | 历史复用、引导模板、缓存限流 | 弱契约：违约优化项 |

---

## 7. 契约可测试性对照

> 本契约每条都可直接转成测试断言，这是"Agent 行为边界测试"的素材来源。

| 契约条款 | 测试断言 |
| --- | --- |
| M1 / N1 | 注入写操作输入，断言 SQL 非 DDL/DML、被 validator 拦截 |
| M2 / N4 | 切换 U002/U004/U006 查询，断言结果行在授权范围内 |
| M3 | 非授权角色导出/下钻，断言无禁用列、手机号脱敏 |
| M5 / M6 | 断言 result 态有 sql 与 trace_id |
| M7 / A8 | 问"销售额"，断言 answer_type=clarify |
| M8 | 构造低置信，断言 fallback |
| M9 | 无前置直接导出，断言 error 且含"先调用 query_metrics" |
| N5 / N7 | 传非法 metric/多余参数，断言 validate_call 返回错误 |
| N8 | t1 取数后 t2 直接导出，断言 error（不串台） |
| ESCALATE | 逐场景断言响应态为对应 clarify/fallback/error |
