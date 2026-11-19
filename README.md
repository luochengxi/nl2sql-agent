# 零售取数 Agent（NL2SQL）

面向**商户（卖家）/ 运营 / 数据分析**三类角色的自然语言取数 Agent。
用中文提问即可查数，自动生成 SQL、按组织树做数据隔离、给出图表与口径说明，并支持导出。

> **核心设计：LLM 只负责"理解"，代码负责"编译与执行"。**
> 大模型不直接写 SQL，而是输出结构化 `QueryPlan`；由确定性的编译器把 Plan 编译成 SQL，
> 并在编译过程中固定注入行级权限、列级脱敏与只读约束。
> 这样权限有唯一注入点、SQL 可解释可审计、换模型不影响查询质量，也更好测试。
> 完整原理见 [技术方案拆解.md](技术方案拆解.md)。

> **双执行通道**：默认的 `/api/chat` 是确定性编排（规则主导 + LLM 填槽）；
> 另有 `/api/chat-fc` 走 **function calling**——由模型自主选择并调用工具，编排层只负责执行、
> 打点与兜底。后者是 Agent 工具调用与轨迹评测的对象，详见
> [架构与编排设计](docs/Agent架构与编排设计.md) 与 [Tools 函数文档](docs/Tools函数文档.md)。

---

## 快速开始

```bash
# 1. 依赖
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. 生成模拟数仓（固定随机种子，任何人跑出来数据一致）
.venv/bin/python scripts/init_warehouse.py --today 2026-11-03 --days 120

# 3. 启动
.venv/bin/python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000> 即是独立对话页。**无需任何 API Key 即可跑通全流程**（默认 Mock 通道）。

---

## 演示账号（切换体验数据隔离）

| 账号 | 角色 | 可见范围 |
| --- | --- | --- |
| U001 张超管 | 超级管理员 | 全部数据 |
| U002 李华东 | 区域经理 | 华东（上海 / 杭州 / 南京） |
| U003 王华西 | 区域经理 | 华西（成都 / 重庆） |
| U004 赵上海 | 城市经理 | 上海 |
| U005 钱成都 | 城市经理 | 成都 |
| U006 孙店长 | 店铺管理员 | S001（上海 1 号店） |
| U007 周店长 | 店铺管理员 | S007（成都 1 号店） |

同一个问题「近 7 日各城市有效订单量」，四个角色分别返回 **9 / 3 / 1 / 1 行**——
隔离在 SQL 的 `WHERE` 里下推，不是查出全量再过滤。

## 示例问法

```
近 7 日各城市有效订单量
华东本月销售额
近 7 日有效订单量环比          # 同比/环比自动对齐对比周期
物流及时妥投率
各城市目标达成率               # 绩效线
上海各门店客单价
近 7 日销售额                  # 触发同名不同口径的意图澄清
那按天看呢                     # 多轮追问，继承上轮槽位
```

---

## 已实现能力（对应 [需求说明书.md](docs/需求说明书.md) v1.1）

### P0 核心
| 能力 | 实现位置 |
| --- | --- |
| 卖家侧四级角色行级隔离 | `config/rbac.yaml` + `src/core/permission.py` |
| 多轮对话 + 跨轮记忆 | `src/memory/session_store.py` + `src/core/intent.py` |
| 意图澄清 | `src/core/intent.py`（歧义指标 / 缺槽位 / 追问合并） |
| 指标口径 RAG | `knowledge/caliber/*.md` + `src/core/rag.py` |
| CSV / Excel 导出 | `src/export/exporter.py` + `/api/export` |
| NL2SQL 引擎 + 语义层 | `config/semantic_layer.yaml` + `src/core/nl2sql.py` |
| 只读安全 + 注入防护 | `src/core/validator.py` + `src/core/executor.py`（`mode=ro`） |
| 表格 + 基础图表 | `web/index.html`（自绘 SVG：折线 / 柱状 / 饼） |
| SQL 透明 + 置信度 + 映射说明 | `ChatResponse.sql / explanation / confidence` |
| 列级权限 + 脱敏 | `src/core/permission.py` |

### P1 增强
| 能力 | 实现位置 |
| --- | --- |
| 查询历史 / 复用 | `src/memory/long_term.py` + `/api/history` |
| 反馈纠错闭环 | `/api/feedback` |
| 审计日志 | `src/observability/audit.py` + `/api/audit` |
| 周期对比 + 智能时间解析 | `src/core/time_parser.py` + `orchestrator._merge_compare` |
| 表格态下钻明细 | `src/core/drilldown.py` + `/api/drilldown` |
| 业务线切换（零售 / 物流 / 绩效） | `config/business_lines.yaml` + `/api/business-lines` |
| 低置信兜底 | `src/core/orchestrator.py` |
| 口径管理后台（产品 + 运营维护） | `src/core/caliber_admin.py` + `/api/calibers` |
| 结果缓存 | `src/core/orchestrator.py`（缺陷模式下自动禁用） |

### Function calling 通道（Agent 评测对象）
| 能力 | 实现位置 |
| --- | --- |
| 模型自主选工具（OpenAI tools 协议） | `src/core/llm.py:complete_with_tools` + `src/core/tool_schemas.py` |
| 工具注册 + 参数 Schema 校验 | `src/core/tool_schemas.py`（7 个能力工具） |
| 工具执行器 + 上下文隔离 | `src/core/dispatcher.py`（`_TURN_CTX` 按 turn 隔离） |
| 模型驱动循环（选→执行→回填→再决策） | `src/core/orchestrator.py:chat_fc` |
| 双层执行轨迹（decision / tool_call） | `src/observability/trace.py` + `/api/trace/{turn_id}` |

7 个工具：`query_metrics` / `search_caliber` / `compare_period` / `get_data_dictionary` /
`export_result` / `drilldown_detail` / `query_history`。其中导出与下钻是**有上下文依赖**的
后置工具——必须先取数写入 turn 上下文才能执行，用来评测"缺失调用 / 依赖链"类问题。

---

## 架构

```
┌─ 接入层   FastAPI + 独立对话页（web/index.html，纯静态、无 CDN 依赖）
├─ 编排层   Orchestrator：取数主流程状态机
├─ 能力层   intent / rag / nl2sql / validator / permission / executor
│           time_parser / drilldown / caliber_admin
├─ 数据层   semantic_layer.yaml（语义层） / SQLite 宽表视图 / 口径文档
└─ 治理层   audit / metrics / defects（缺陷开关）
```

```
用户问句
   ↓ 意图识别 + 业务线判定（LLM 可选增强，默认规则解析）
QueryPlan（指标 / 维度 / 时间 / 筛选 / 对比 / 置信度）
   ↓ 槽位检查 ──缺/歧义──> 意图澄清
   ↓ 口径 RAG 检索
   ↓ 编译器：QueryPlan + 语义层 → SQL
        ├─ 行级权限下推（WHERE region_name / city_name / store_id = ?）
        └─ 列级脱敏（SELECT substr(phone,...)）
   ↓ 只读校验（单 SELECT / 无 DDL DML / 无注释 / 白名单视图）
   ↓ mode=ro 只读连接执行
   ↓ 表格 + 基础图表 + SQL 透明 + 口径引用 + 置信度
   ↓ 导出 / 下钻 / 反馈 / 审计 / 写回记忆
```

### 为什么用宽表视图
把 JOIN 提前固化在数据库里，编译器只面向单表，不必让模型猜 JOIN；
同时让「行级权限下推」有唯一注入点。三个视图统一携带同一套权限相关列。

### 为什么 Mock 优先
`config/settings.yaml` 的 `llm.provider` 默认 `mock`：零依赖、零成本、输出确定，
clone 下来就能跑，测试断言也稳定。切到 `openai_like` 并设置环境变量即可接真实模型：

```bash
export OPENAI_API_KEY=sk-...
# settings.yaml: llm.provider: openai_like
```
即便真实模型超时或返回乱码，系统也会降级到规则解析，时间、权限、SQL 编译不受影响。

---

## 目录结构

```
nl2sql-agent/
├── config/          语义层 / RBAC / 业务线 / 组织树 / 全局设置
├── knowledge/       指标口径文档（RAG 语料，产品 + 运营维护）
├── data/            SQLite 模拟数仓（构建产物，不入库）
├── scripts/         init_warehouse.py / selfcheck_defect_switch.py
├── src/
│   ├── core/        意图 / 编译 / 校验 / 权限 / 执行 / RAG / 时间 / 下钻 / 编排
│   ├── memory/      短期会话记忆 + 长期记忆
│   ├── defects/     缺陷开关注册中心
│   ├── observability/ 审计 + 运行指标
│   ├── export/      CSV / Excel 导出
│   └── api/         FastAPI 接口
├── web/             独立对话页
├── docs/            PRD / 能力契约 / 架构编排 / Tools / Prompt 快照 / Guardrail
│                    需求说明书 / 测试设计引导 /（本地）缺陷答案 key
└── tests/           测试由项目所有者自行设计
```

---

## 配置要点

| 配置 | 说明 |
| --- | --- |
| `time.today` | 注入统计基准日 T；`null` 取系统当天。钉死日期后测试可复现 |
| `time.default_window_days` | 问句不带时间时的默认窗口（7 日，不含当天） |
| `intent.min_confidence` | 低于该值走兜底，不猜测性出数 |
| `llm.provider` | `mock`（默认）/ `openai_like` |
| `defect_mode` | **默认关闭**，见下节 |

---

## 缺陷开关（测试教学用）

本项目内置一个**可配置切换的缺陷模式**，用于练习设计测试用例：
代码中以 `# [DEFECT-ID: Dxx]` 标记了 18 类缺陷，全部由开关控制，**默认关闭**，
对外展示时是正常行为。

```bash
# 只激活一类
NL2SQL_DEFECT_MODE=D03 ./.venv/bin/python -m uvicorn src.api.main:app --port 8000

# 一次全开
NL2SQL_DEFECT_MODE=ALL ./.venv/bin/python -m uvicorn src.api.main:app --port 8000

# 自检：对比正常模式与缺陷模式的可观测差异
.venv/bin/python scripts/selfcheck_defect_switch.py
```

- `GET /api/defects` 仅在开启时返回分类清单（只有分类与提示，没有位置）。
- 缺陷位置与修复方式写在 `docs/缺陷答案key.local.md`，该文件被 `.gitignore` 排除，
  **不会进入公开仓库**。
- 测试设计方向见 [测试设计引导.md](docs/测试设计引导.md)。
- 项目**不含测试套件**——测试由项目所有者自行设计。

建议的验证思路：同一批用例在正常模式应全绿、在 `ALL` 模式应有若干条失败，
逐条 `Dxx` 激活可确认「哪条测试抓到了哪类缺陷」。

---

## 文档索引

| 文档 | 内容 |
| --- | --- |
| [需求说明书.md](docs/需求说明书.md) | 产品能力边界、FR/NFR、v1.1 已锁定决策 |
| [技术方案拆解.md](技术方案拆解.md) | 每个技术决策的「怎么做 / 为什么 / 好处」 |
| [测试设计引导.md](docs/测试设计引导.md) | 能力域与测试方向（不含缺陷位置） |
| **Agent 文档体系（面向评测 / 简历）** | |
| [PRD业务需求文档.md](docs/PRD业务需求文档.md) | 业务场景、用户输入范围、输出契约、合规、验收标准 |
| [Agent能力契约.md](docs/Agent能力契约.md) | 必须做 / 禁止做 / 需审批边界、禁止动作清单 |
| [Agent架构与编排设计.md](docs/Agent架构与编排设计.md) | 状态机、多轮流转、记忆、循环终止、防死循环、双层 trace |
| [Tools函数文档.md](docs/Tools函数文档.md) | 7 个工具的 Schema / 权限 / 依赖 / 幂等 / 错误码 / 超时重试 |
| [SystemPrompt版本快照.md](docs/SystemPrompt版本快照.md) | Prompt 原文归档、版本登记、测试绑定规范 |
| [Guardrail安全规则文档.md](docs/Guardrail安全规则文档.md) | 输入过滤 / 输出审核 / 脱敏 / 注入防护 / 拒绝话术 |

## 长期路线（本期不做，v1.1 已锁定）

推送 IM（企业微信/飞书）、系统嵌入 / OpenAPI、图表下钻联动与自定义图表、
BI 直连、多语言、多模态输入。
