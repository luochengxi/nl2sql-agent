# FC 黄金集 100 条 · 检查补充结论

> 配套 `check_report_fc100.txt`（由 check_golden_fc100.py 每次重跑生成）。本文件手工维护，不会被执行脚本覆盖。

## 真值独立复算（实证）

用独立 SQL（从 `semantic_layer.expr` 直取、直连 `warehouse.db`、**不 import 被测代码**）对全部含 `rows_exact` 的 case 复算：

- 无筛选 case：全部一致
- 带 filters / scope 的 4 条（HP-026 / 027 / 028、AD-004）：带上筛选条件后全部一致
  - 初版复算脚本漏带 filters 导致误报，非真值错误

**结论**：真值可独立复算，oracle 与被测对象解耦成立。这是黄金集能抓 D09/D10 公式篡改的根本。

## 重要发现：FC prompt 时间口径冲突（非本黄金集错误）

`orchestrator.py:317` 的 FC system prompt 写：

> 用户说'近7日'指含今天在内最近7天：date_start=今天减6天，date_end=今天

即 `[T-6, T]`；而 `time_parser.py` / `oracle.py` / `build_golden.py` / 本生成器全部是 `[T-7, T-1]`。

数仓末日 = 2026-11-02，两口径实算差异：

| 口径 | 区间 | 结果 |
|---|---|---|
| `[T-7, T-1]`（业务正确） | 10-27 ~ 11-02 | 7 天 / 924 行 |
| `[T-6, T]`（FC prompt） | 10-28 ~ 11-03 | 6 天 / 816 行，漏 10-27、多算无数据的 11-03 |

本黄金集冻结真值采用业务正确的 `[T-7, T-1]`。若 FC 链路按 prompt 的 `[T-6, T]` 出数，近 7 日相关 case 会失败——**这正是应被黄金集捕获的 prompt 缺陷（对应 D01 靶点）**，不是黄金集本身的问题。修 prompt 后应重跑自检确认转绿。

## 已知缺陷：存量 TC-003 参数不合法

存量 `golden_cases.jsonl` 的 `TC-003` 使用 `compare_type="wow"`，但 `tool_schemas.py:39` 的 enum 只有 `mom / yoy / dod`。本 100 条黄金集的同环比 case 全部使用合法枚举（HP-019=mom / HP-020=yoy / HP-021=dod），并在 note 中标注了这一点。存量那条建议修正。

## 覆盖薄弱点（后续补充建议）

- `focus-8`（输出交付质量）仅 1 条，建议补 2~3 条：结构化字段稳定性、话术口径一致性、可审计（trace_id 与凭证溯源）。
- `focus-2`（规划与子任务分解）4 条，集中在同环比与工具链，可再补"中途目标变更""低成本路径选择"两类。
