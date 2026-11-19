# 业务需求文档（PRD）

> 文档版本：v1.0　状态：已实现（v1.1 功能基线）　更新日期：2026-11-19
> 关联文档：[需求说明书](需求说明书.md)（FR/NFR 全景）、[Agent 能力契约](Agent能力契约.md)（必须/禁止边界）
> 适用范围：商户（卖家）/ 运营 / 数据分析三类角色的自然语言取数场景

---

## 0. 本文档定位

需求说明书回答"要做哪些功能（FR/NFR）"，本 PRD 回答"**业务是什么、给谁用、输入什么、输出什么、受什么合规约束**"，是测试用例中"业务正确性"断言的源头。二者分工：

| 文档 | 回答的问题 | 测试用途 |
| --- | --- | --- |
| 需求说明书 | 功能清单与优先级（FR/NFR） | 功能覆盖度矩阵 |
| **本 PRD** | 场景 / 输入边界 / 输出契约 / 合规 | 业务正确性与合规断言 |
| Agent 能力契约 | Agent 必须做/禁止做/需审批 | 安全与行为边界断言 |

---

## 1. 业务背景与目标

### 1.1 背景

零售业务已有绩效线 / 物流线 / 零售线三条数据线与对应数仓宽表视图。商户、运营、分析师取数依赖写 SQL 或提需求给数据团队，链路长、门槛高。本项目提供自然语言取数 Agent，让用户用简体中文提问即可拿到可信、可追溯的数据结果。

### 1.2 业务目标（可度量）

| 目标 | 度量口径 | 基线要求 |
| --- | --- | --- |
| 降低取数门槛 | 非技术角色可独立完成取数占比 | 主流程问句无需写 SQL |
| 结果可信可追溯 | 每次结果附带 SQL + 口径引用 + 执行 trace | result 类型 100% 带 `sql` 与 `trace_id` |
| 数据隔离合规 | 越权数据泄露事件数 | 0（权限在 SQL 层下推） |
| 口径一致 | 同名指标澄清率 | 歧义指标触发 clarify 而非猜测 |

---

## 2. 业务场景与用户

### 2.1 三类目标用户

| 角色 | 典型诉求 | 系统内权限画像（`rbac.yaml`） |
| --- | --- | --- |
| 商户 / 卖家 | 看自己店铺的订单、销售额、退款 | `store_admin`：scope=store，restricted 列策略 |
| 运营 | 按区域/城市看趋势、对比、下钻明细 | `region_manager` / `city_manager`：scope=region/city，standard 列策略 |
| 数据分析 | 跨业务线取数、导出做进一步分析 | `super_admin`：scope=all，full 列策略 |

> 组织树为卖家侧单链路，**无买家侧**。四级角色：超级管理员 → 区域经理 → 城市经理 → 店铺管理员（见需求说明书 §2.2）。

### 2.2 核心业务场景（用户故事）

| 场景 | 用户输入示例 | 期望输出 | 涉及能力 |
| --- | --- | --- | --- |
| S1 聚合取数 | "近7日各城市有效订单量" | 表格：城市 × 有效订单量 | query_metrics |
| S2 业务线切换 | "物流线近7日及时妥投率" | 物流宽表结果 | query_metrics（business_line=logistics） |
| S3 周期对比 | "本月销售额环比" | 本期 + 对比期 | compare_period |
| S4 口径答疑 | "有效订单量怎么算的" | 官方口径文档 | search_caliber |
| S5 数据字典 | "都能查什么指标" | 指标/维度清单 | get_data_dictionary |
| S6 下钻明细 | "展开上海明细" | 明细行表格 | drilldown_detail |
| S7 结果导出 | "导出成 Excel" | xlsx 文件 | export_result |
| S8 历史复用 | "我之前查过什么" | 历史问句列表 | query_history |
| S9 多轮追问 | "那华东呢？"（承接上文） | 继承上轮主体重新取数 | 会话记忆 |
| S10 歧义澄清 | "销售额"（零售/绩效同名不同口径） | 反问确认业务线 | clarify |

---

## 3. 用户输入范围

### 3.1 In Scope（支持）

- **语言**：仅简体中文（`settings.yaml` `app.language=zh-CN`），不做多语言。
- **问句类型**：聚合取数、维度分组、时间窗、业务线指定、周期对比、口径查询、数据字典、下钻、导出、历史、多轮追问。
- **时间表述**：绝对日期（`2026-10-27 到 2026-11-02`）、相对窗口（"近7日""昨日""本月"，按 `config.time.default_window_days=7`）。
- **时间锚点**：`time.today` 可注入以复现；为 `null` 时取系统当日。**重要业务语义**："近 N 日"=含今日在内最近 N 天；查空是合法结果，**不从数仓末日反推今天**。
- **显式指定业务线**：`ChatRequest.business_line` 可覆盖自动判定。

### 3.2 Out of Scope（不支持）

| 项 | 说明 |
| --- | --- |
| 买家侧数据 | 无买家账号与数据链路 |
| 写操作 | 不支持 INSERT/UPDATE/DELETE/DDL，数据库只读 |
| 多语言 | 不做 i18n |
| 系统嵌入 / 对外 OpenAPI | 仅独立对话页，接口层预留 |
| 图表下钻联动 / 自定义图表 | 仅基础图表（折线/柱/饼） |
| 预测 / 模型训练 / 数仓建模 | 不在范围 |
| 多模态输入 | 语音/截图上传为可选增强，本期不做 |

### 3.3 输入合法性的业务约束

- 指标 id 必须来自语义层白名单（`semantic_layer.yaml` 的 `metrics`），模型不得编造。
- 时间格式必须为 `YYYY-MM-DD`（工具 Schema 正则校验）。
- 业务线必须为 `retail / logistics / performance` 枚举之一。
- 超长/注入文本按 Guardrail 规则过滤（见 [Guardrail 安全规则文档](Guardrail安全规则文档.md)）。

---

## 4. 输出约束

### 4.1 输出契约（`ChatResponse`）

所有响应必须满足统一结构，测试据此断言。字段定义以 `src/core/models.py` 为准：

| 字段 | 类型 | 业务含义 | 约束 |
| --- | --- | --- | --- |
| `answer_type` | enum | 响态 | `result` / `clarify` / `fallback` / `error` 四态之一 |
| `text` | string | 面向用户的自然语言 | result 态含行数与 SQL 说明 |
| `sql` | string\|null | 生成的 SQL | **result 态必有**；clarify/fallback/error 态应为空 |
| `columns` / `rows` / `row_count` | array/array/int | 结果集 | 仅 result 态有实际数据 |
| `confidence` | float | 置信度 | 低于 `intent.min_confidence=0.60` 走 fallback |
| `plan` | dict\|null | 查询计划快照 | 用于可解释 |
| `caliber_refs` | array | 口径文档引用 | 口径类问题返回 |
| `chart` | dict\|null | 基础图表规格 | 数值型结果可成图 |
| `export_token` / `drilldown_token` | string\|null | 交付凭证 | 导出/下钻后置工具产生 |
| `suggestions` | array | 兜底/澄清建议 | clarify/fallback 态给出 |
| `trace_id` | string | 执行链路索引 | function calling 链路必带，可捞 trace |

### 4.2 四态响应语义（业务规则）

| 状态 | 触发条件 | 允许的动作 | 禁止的动作 |
| --- | --- | --- | --- |
| `result` | 成功取到数据 | 返回 sql/rows/图表/导出凭证 | 不得夹带越权列 |
| `clarify` | 主体缺失 / 指标歧义 / 时间不清 / 跨线无法判定 | 反问具体问题 + suggestions | 不得猜测性生成 SQL |
| `fallback` | 置信度低 / 无法确定业务线与指标 | 建议补充或转数据团队 | 不得编造答案 |
| `error` | 权限拒绝 / provider 不支持 / 系统异常 | 返回错误原因 | 不得泄露内部堆栈/密钥 |

### 4.3 呈现形式

- 表格为默认呈现；数值型维度结果可附基础图表（折线/柱/饼）。
- SQL 透明：展示生成 SQL 与"问题→SQL"映射说明，建立信任。
- 导出复用已裁剪结果集，附口径脚注与 SQL（见 `exporter.py`）。

---

## 5. 数据与口径范围

### 5.1 业务线与数据集

| 业务线 id | 名称 | 宽表视图 | 日期列 | 默认指标 |
| --- | --- | --- | --- | --- |
| `retail` | 零售线 | `v_retail_order` | `order_date` | `valid_order_cnt` |
| `logistics` | 物流线 | `v_logistics_ship` | `ship_date` | `ontime_rate` |
| `performance` | 绩效线 | `v_performance` | `stat_date` | `achieve_rate` |

### 5.2 指标（语义层 `metrics`）

| 业务线 | 指标 id | 名称 | 单位 |
| --- | --- | --- | --- |
| retail | `valid_order_cnt` | 有效订单量 | 单 |
| retail | `order_cnt` | 订单量 | 单 |
| retail | `pay_amount` | 销售额 | 元 |
| retail | `aov` | 客单价 | 元 |
| retail | `refund_rate` | 退款率 | 百分比 |
| retail | `sales_qty` | 销量 | 件 |
| logistics | `shipment_cnt` | 发货单量 | 单 |
| logistics | `avg_delivery_hours` | 平均履约时效 | 小时 |
| logistics | `ontime_rate` | 及时妥投率 | 百分比 |
| performance | `perf_gmv` | 绩效销售额 | 元 |
| performance | `achieve_rate` | 目标达成率 | 百分比 |
| performance | `commission` | 佣金 | 元 |
| performance | `target_gmv` | 目标销售额 | 元 |

> **口径歧义**：`pay_amount`（retail 销售额）与 `perf_gmv`（performance 绩效销售额）同名"销售额/GMV"，必须走意图澄清，不得静默选择。这也是 `business_lines.yaml` 不把"销售额"放进关键词的原因。

### 5.3 维度（语义层 `dimensions`）

`region`（区域）、`city`（城市）、`store`（店铺）、`seller`（卖家）、`category`（品类，仅 retail）、`product`（商品，仅 retail）、`date`（日期，编译时替换为业务线日期列）。

---

## 6. 合规要求

| 合规项 | 业务要求 | 实现落点 |
| --- | --- | --- |
| 只读 | 任何输入不得产生写操作 | 数据库 `readonly=true` + validator 关键字拦截 |
| 行级隔离 | 用户仅见授权组织范围，SQL 层下推 | `permission.row_filter_clause` |
| 列级脱敏 | PII（手机号）、成本/毛利按角色隐藏或脱敏 | `column_policies` + `mask_expression` |
| 数据最小化 | 不返回授权范围外的字段与行 | `visible_columns` + 下钻列白名单 |
| 审计留痕 | 谁/何时/查了什么/导出了什么 | `audit.jsonl`，留存 `retention_days=180` |
| 口径可追溯 | 结果附口径引用，口径变更留版本 | `caliber_refs` + 口径管理后台 |
| 执行可追溯 | function calling 链路逐步留痕 | `trace.jsonl`（decision + tool_call） |

---

## 7. 验收标准（可量化）

> 以下为业务需求验收口径，同时是测试用例"业务正确性与合规"断言的数据源。

| 编号 | 验收项 | 判定标准 |
| --- | --- | --- |
| A1 | 主链路可用 | 给定有数据的时间窗，result 态返回 `row_count>0` 且 `sql` 含正确视图 |
| A2 | 业务线正确 | 物流问句命中 `v_logistics_ship`，绩效命中 `v_performance` |
| A3 | 日期换算 | "近7日"换算为含今日的 7 天窗口（date_start=今日-6） |
| A4 | 指标口径 | 有效订单量 SQL 含 `is_valid = 1`；客单价分母为有效订单数 |
| A5 | 聚合正确 | SUM/AVG/MAX/MIN 按需求支持，结果等于独立 oracle 真值 |
| A6 | 行级隔离 | 区域经理/城市经理/店长查询不越权，越界结果为空或拒绝 |
| A7 | 列级脱敏 | 非授权角色不含禁用列，手机号按模板脱敏 |
| A8 | 歧义澄清 | "销售额"触发 clarify，请求用户选择业务线 |
| A9 | 缺失兜底 | 不存在指标/无前置上下文时不受理并给 suggestions |
| A10 | 只读安全 | 注入/写操作输入不产生写 SQL，被 validator 拦截 |
| A11 | 审计完整 | 查询、导出、下钻、口径变更均有审计记录 |
| A12 | 结果可追溯 | 每个 result 带 `trace_id`，可捞到 decision + tool_call 记录 |
