# 系统提示词（System Prompt）版本快照

> 文档版本：v1.0　更新日期：2026-11-19
> 定位：**Prompt 是 Agent 逻辑的一部分**，与代码同等重要。每次变更必须归档、测试必须绑定固定版本。
> 原则：本文件是**只增不改**的快照档案，新版本追加、旧版本保留。

---

## 1. 为什么 Prompt 要版本快照

| 理由 | 说明 |
| --- | --- |
| Prompt 即逻辑 | 本项目的工具选择、日期换算、业务线路由全靠 system 约束，改一个字行为就变 |
| 结果不可复现 | 不绑定版本，同一测试今天过明天挂，无法定位是模型还是 prompt 变了 |
| 归因需要 | 线上选错工具时，必须能回答"当时用的是哪版 prompt" |
| 测试基线 | 工具调用成功率的软断言阈值，必须在固定 prompt 版本下才有意义 |

**规则**：任何 system prompt 变更 → 新增快照版本 → 更新测试绑定版本 → 重跑成功率基线。

---

## 2. 快照索引与测试绑定

| 版本 ID | 所属链路 | 适用模型 | 状态 | 代码位置 | 绑定测试 |
| --- | --- | --- | --- | --- | --- |
| **SP-FC-001** | chat_fc（模型驱动） | qwen3.7-plus | ✅ 当前生效 | `orchestrator.py:314-323` | `test_tool_calling.py`（软断言基线） |
| **SP-INTENT-001** | chat（规则编排） | qwen3.7-plus | ✅ 当前生效 | `intent.py:105-110` | `test_indicator_accuracy.py` 等 |
| SP-FC-000 | chat_fc | qwen3.7-plus | ⛔ 已弃用 | 历史 | —（探测阶段弱 prompt） |

> 测试绑定方式：工具选择成功率用例在 fixture 中固定"使用 SP-FC-001"，prompt 升级到 002 时必须显式改绑定并重新校准阈值。

---

## 3. SP-FC-001 — chat_fc 系统提示词（当前生效）

**来源**：`src/core/orchestrator.py` 第 314–323 行，`chat_fc()` 内构造。
**生效日期**：2026-11-19　**关联工具集**：7 个（见 [Tools 函数文档](Tools函数文档.md)）

### 3.1 完整原文（逐字归档）

```
你是零售取数 Agent。必须调用工具回答，不要反问用户，不要自己编答案。
今天是 {today.isoformat()}。用户可见范围：{scope_label(user)}。
用户说'近7日'指含今天在内最近7天：date_start=今天减6天，date_end=今天，
换算成 YYYY-MM-DD 再调用 query_metrics。
问物流时效查 logistics，问绩效查 performance，其余查 retail。
用户说导出/下载就调 export_result，说看明细/展开就调 drilldown_detail，
说查历史就调 query_history。导出和下钻必须先调 query_metrics 拿到结果。
不要编造指标 id。
```

> 其中 `{today.isoformat()}` 与 `{scope_label(user)}` 为运行时注入（不是字面量）：前者来自 `config.today()`，后者来自 `permission.scope_label(user)`。测试时通过 `time.today` 注入固定日期以复现。

### 3.2 每句话的设计意图（可测试映射）

| Prompt 片段 | 解决的问题 | 对应测试断言 |
| --- | --- | --- |
| "必须调用工具，不要反问" | 探测阶段模型反问"请提供日期范围" | decision.selected 非空 |
| "今天是 X；近7日=今天减6到今天，YYYY-MM-DD" | 模型无日期锚点不敢填必填参数 | query_metrics.tool_args 日期正确 |
| "物流查 logistics，绩效查 performance，其余 retail" | 降低选错业务线概率 | business_line 断言 |
| "导出调 export_result，下钻调 drilldown_detail，历史调 query_history" | 让模型知道后置/辅助工具何时用 | 各工具被选中率 |
| "导出和下钻必须先调 query_metrics" | 保证 ctx 已建立 | 依赖链 status=ok |
| "不要编造指标 id" | enum 白名单约束 | 无 enum 错误 |

### 3.3 变更历史

| 版本 | 变更 | 原因 |
| --- | --- | --- |
| SP-FC-000 | 初始弱 prompt："你是取数助手，用工具回答。" | 探测发现模型反问、不选工具 |
| SP-FC-001 | 加入日期锚点、换算规则、禁反问、业务线路由、后置工具指引 | 修复探测暴露的三类问题（缺锚点/爱反问/不知工具） |

---

## 4. SP-INTENT-001 — 意图解析系统提示词（当前生效）

**来源**：`src/core/intent.py` 第 105–110 行，`_llm_refine()` 内构造。
**生效日期**：2026-11-03　**用途**：规则编排链路中，LLM 只建议槽位（metric/dimension/business_line）。

### 4.1 完整原文（逐字归档）

```
你是取数意图解析器。只输出 JSON：
{"metrics":[id...],"dimensions":[id...],"business_line":id|null}。
id 必须来自下面这份清单，不要发明新 id，不要输出 SQL。
清单={catalog}
```

> `{catalog}` 为运行时注入的封闭清单，结构：
> ```json
> {
>   "metrics": {"<id>": {"name": ..., "business_line": ...}, ...},
>   "dimensions": {"<id>": {"name": ...}, ...},
>   "business_lines": ["retail","logistics","performance"]
> }
> ```

### 4.2 设计意图

| 约束 | 防御目标 |
| --- | --- |
| "只输出 JSON，三字段" | 限制 LLM 输出面，便于程序解析 |
| "id 必须来自清单，不要发明" | 防编造指标（N5） |
| "不要输出 SQL" | SQL 由确定性编译器生成，不由 LLM 写 |
| 白名单二次过滤 | 即使模型返回非法 id，也被 `if m in metrics` 拦掉 |

> **双保险**：prompt 约束 + 代码白名单。测试既验证 prompt 生效（模型返回合法 id），也验证白名单兜底（注入非法 id 被过滤）。

---

## 5. Prompt 变更管理流程

```
1. 提出变更（提升工具选择准确率 / 修复错误行为）
        ↓
2. 复制当前快照 → 新版本号（SP-FC-002 ...）
        ↓
3. 在代码修改 system，记录变更点与实际原文
        ↓
4. 更新本文件：新版本追加 + 旧版本标为"已弃用" + 索引表更新
        ↓
5. 更新测试绑定版本，重跑成功率基线（N≥5，阈值≥0.8）
        ↓
6. 对比新旧：准确率是否提升、有无新增错误模式
        ↓
7. 达标 → 标记新版本"当前生效"；不达标 → 回滚
```

### 5.1 归档要求（每次变更必填）

| 字段 | 说明 |
| --- | --- |
| 版本 ID | SP-{链路}-{序号}，序号递增 |
| 生效/弃用日期 | 时间线可追溯 |
| 完整原文 | 逐字，含运行时注入说明 |
| 关联工具集版本 | 对应的 [Tools 文档](Tools函数文档.md) 版本 |
| 关联模型 | 模型变更也可能导致 prompt 失效 |
| 变更原因 + 预期 | 为什么改、期望改善什么指标 |
| 测试基线 | 变更前后的成功率对比 |

---

## 6. 测试绑定规范

| 规则 | 说明 |
| --- | --- |
| 固定版本 | 成功率软断言用例必须声明"基于 SP-FC-001" |
| 显式升级 | prompt 版本变化不会自动生效到测试，需手动改绑定 |
| 阈值随版本 | 每个版本单独校准 `THRESHOLD`（默认 0.8），不可跨版本沿用 |
| 原文校验 | 可加一条测试：读取代码中的 system 文本，与本快照原文比对，防"改了代码没归档" |
| 注入值隔离 | 日期锚点通过 `time.today` 注入固定值，避免"今天"漂移导致断言不稳 |

### 6.1 防漂移断言示例（思路）

```python
# 思路：确保代码里的 system 与快照一致（防止暗改 prompt）
# 实际实现可将 system 抽成常量后比对，此处仅示意
def test_prompt_snapshot_matches():
    from src.core.orchestrator import FC_SYSTEM_TEMPLATE  # 需重构抽出
    assert FC_SYSTEM_TEMPLATE == SP_FC_001_TEMPLATE
```

> 当前 `chat_fc` 的 system 内联在函数里，若要做原文比对测试，需先把模板抽成模块常量——这是后续可改进项，文档先记录规范。

---

## 7. 已知风险

| 风险 | 说明 | 缓解 |
| --- | --- | --- |
| 内联未抽取 | system 目前写在函数里，无法自动化原文比对 | 抽成常量 + 加快照比对测试 |
| 模型版本耦合 | prompt 针对 qwen3.7-plus 调优，换模型可能失效 | 版本表记录关联模型 |
| 注入值未快照 | 日期/范围是运行时值，快照只有模板 | 测试注入固定 `time.today` |
| 无 A/B 数据留存 | 成功率基线未持久化 | 建议归档到报告目录 |
