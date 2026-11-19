# Guardrail 安全规则文档

> 文档版本：v1.0　更新日期：2026-11-19　状态：与 v1.1 代码基线一致
> 事实来源：`validator.py`、`permission.py`、`tool_schemas.py`、`orchestrator.py`、`rbac.yaml`、`settings.yaml`
> 定位：输入过滤 / 输出审核 / 敏感信息拦截 / 注入防护 / 越狱规则 / 拒绝话术的完整规范，是"安全与权限"测试断言的源头。

---

## 1. 纵深防御模型（L1–L5）

Guardrail 不是单点，而是**五层独立防线**——任何一层失效，后续层仍能兜住：

```
用户输入
   │
   ▼
L1 输入过滤      工具参数 Schema 校验（正则/枚举/类型）
   │
   ▼
L2 意图约束      System Prompt 约束 + 白名单槽位过滤
   │
   ▼
L3 SQL 编译      确定性编译，只认语义层 id，不采信用户表名
   │
   ▼
L4 执行前校验    validator 独立只读校验（纵深防御最后一道）
   │
   ▼
L5 权限与脱敏    行级下推 + 列级裁剪/脱敏（SQL 层生效）
   │
   ▼
L6 输出审核      响应态约束 + 审计留痕 + trace 追溯
   │
   ▼
返回用户
```

**核心原则**：越靠近数据的防线越独立。L4 不信任 L3 的编译结果，L5 不信任应用层过滤——因为聚合指标事后过滤不成立，越权数据一旦进内存就可能从日志/异常泄露。

---

## 2. 输入过滤（L1）

### 2.1 工具参数校验（`tool_schemas.validate_call`）

每次工具调用先过 Schema，失败记 `status=error`：

| 过滤项 | 规则 | 拦截示例 |
| --- | --- | --- |
| 必填检查 | required 参数缺失 → `missing:{k}` | query_metrics 缺 date_start |
| 多余参数 | 未定义参数 → `extra_arg:{k}` | 模型编造 bad 参数 |
| 枚举校验 | 值须在 enum 内 → `enum:{k}={v}` | business_line=warehouse |
| 正则校验 | 日期须 `YYYY-MM-DD` → `pattern:{k}={v}` | date_start="11/01" |
| 类型校验 | array/integer/string → `type:{k}` | limit="5" |

### 2.2 问句输入边界

| 规则 | 实现 |
| --- | --- |
| 语言 | 仅简体中文，非中文问句走澄清/兜底 |
| 指标白名单 | 模型建议的 metric id 经 `if m in metrics` 过滤（N5） |
| 业务线枚举 | 仅三条业务线，其他判为无法处理 |
| 时间格式 | 相对时间由 `time_parser` 解析，非法时间走默认窗口/澄清 |

---

## 3. 意图约束（L2）

| 规则 | Prompt 来源 | 防御目标 |
| --- | --- | --- |
| "不要编造指标 id" | SP-FC-001 / SP-INTENT-001 | 防 N5 编造指标 |
| "不要反问/不要自己编答案" | SP-FC-001 | 防只反问或幻觉 |
| "id 必须来自清单，不要输出 SQL" | SP-INTENT-001 | 约束 LLM 输出面 |
| 白名单二次过滤 | `intent._llm_refine` 代码 | 即使 prompt 被绕过也拦得住 |

> **越狱防护**：即使模型被诱导输出非法内容，代码层白名单（metric id、business line enum、Schema enum）仍会拦截。Prompt 是第一道柔性约束，代码白名单是刚性兜底。

---

## 4. SQL 编译安全（L3）

| 规则 | 实现 | 说明 |
| --- | --- | --- |
| 只认语义层 id | `nl2sql.compile` 只接受 metric/dimension id | 不采信用户输入的表名/字段名 |
| 宽表视图 | 编译器面向单表视图，不生成 JOIN | join 固化在 DB 层 |
| 参数化 | 权限过滤、筛选条件均用 `?` 占位 | 杜绝字符串拼接注入 |
| 只读 | 只生成 SELECT | 无写操作模板 |

---

## 5. 注入防护（L4 — validator 独立校验）

`validator.validate(sql, params)` 贴着数据库连接做最后一道检查，**不依赖前三层**。

### 5.1 SQL 层拦截规则

| 检查 | 规则 | 缺陷开关 |
| --- | --- | --- |
| 单条 SELECT | 必须以 `SELECT` 开头 | — |
| DDL/DML 关键字 | `_FORBIDDEN` 黑名单（18 个关键字） | D08（关闭时跳过拦截） |
| 多语句 | 禁止 `;` | D07（关闭时跳过） |
| SQL 注释 | 禁止 `--`、`/*`、`*/`、`#` | D07（关闭时跳过） |
| 子查询 | 禁止 `FROM (` | — |
| 表白名单 | FROM/JOIN 的表须在业务线视图白名单内 | — |
| 参数控制字符 | 参数值禁止 `'`、`"`、`;`、`--`、`/*` | — |

**被拦截的关键字全集**：
```
INSERT UPDATE DELETE DROP ALTER CREATE TRUNCATE REPLACE
ATTACH DETACH PRAGMA VACUUM EXEC EXECUTE REINDEX LOAD_EXTENSION
```

> **纵深防御价值**：编译层（L3）可能有 Bug、LLM 可能被提示词注入绕过，L4 是独立实现、不共享假设的最后防线。

---

## 6. 敏感信息拦截（L5）

### 6.1 行级隔离（`permission.row_filter_clause`）

| 角色 scope | 下推条件 | 可见范围 |
| --- | --- | --- |
| `all` | `1=1` | 全部 |
| `region` | `region_name = ?` | 所辖区域 |
| `city` | `city_name = ?` | 所辖城市 |
| `store` | `store_id = ?` | 本店 |

**缺陷开关 D03**：开启时区域经理下推被跳过（可见范围放大到全量）——用于验证测试能否抓到越权。

### 6.2 列级权限（`rbac.column_policies`）

| 策略 | 角色 | 禁用列（deny） | 脱敏列（mask） |
| --- | --- | --- | --- |
| `full` | super_admin | 无 | 无 |
| `standard` | region/city_manager | `cost_amount`、`supplier_cost` | `customer_phone`、`seller_phone` |
| `restricted` | store_admin | 上列 + `commission`、`target_gmv` | 同上 |

**脱敏表达式**（`mask_rules`）：
- 手机号：`substr(col,1,3) || '****' || substr(col,-4)` → `138****5678`
- 金额：`round(col/100.0,0)*100`（分转元取整）

**缺陷开关**：D05（脱敏表达式失效）、D06（禁用列清单不应用）。

### 6.3 权限一致性

| 场景 | 保障 |
| --- | --- |
| 导出 | 复用已裁剪结果集，token 绑定 user_id（D16 关闭时校验） |
| 下钻 | 明细列经同样的 visible_columns + mask + row_filter（D04 关闭时附加行收窄） |
| 指标 | 依赖禁用字段的指标直接 `PermissionDenied`（`check_metric_allowed`） |

---

## 7. 输出审核（L6）

### 7.1 响应态约束

| 规则 | 实现 |
| --- | --- |
| 四态枚举 | `answer_type ∈ {result, clarify, fallback, error}` |
| 结果才带 SQL/数据 | result 态有 sql/rows；其他态应为空 |
| 拒绝态给建议 | clarify/fallback 附 suggestions，不裸拒 |
| 错误不泄密 | error 态仅回原因文本，不含堆栈/密钥/内部路径 |

### 7.2 审计与追溯

| 记录 | 内容 | 留存 |
| --- | --- | --- |
| `audit.jsonl` | 查询/导出/下钻/反馈/口径变更 | 180 天（`audit.retention_days`） |
| `trace.jsonl` | decision + tool_call（决策与执行证据） | 滚动缓冲 2000 + 落盘 |
| `/api/metrics` | query / tool_round_limit 等计数 | 内存快照 |

**缺陷开关 D18**：审计留痕不完整——用于验证测试能否发现漏记。

### 7.3 输出审核的测试断言

- result 态必有 sql 与 trace_id；clarify/fallback 态无 sql。
- 非授权角色结果不含禁用列、手机号已脱敏。
- 每个查询/导出/下钻在 audit 中有对应记录。

---

## 8. 拒绝话术规范

| 场景 | 响应态 | 规范话术（示例） | 依据 |
| --- | --- | --- | --- |
| 指标歧义 | clarify | "「销售额」在不同业务线下口径不同，你想看哪一个？（零售线销售额/绩效线业绩）" | `intent._clarify_for_metrics` |
| 缺必要槽位 | clarify | 反问具体缺失项 + suggestions | `plan.clarification_question` |
| 低置信/无法确定 | fallback | "这次没能确定业务线和指标，请补充（如'零售线近7日各城市有效订单量'）" | `chat_fc` fallback 分支 |
| 未知账号 | error | "未知账号: {user_id}" | `load_user` |
| 指标越权 | error | "指标「X」依赖的字段 [...] 不在你的可见范围内" | `check_metric_allowed` |
| provider 不支持 | error | "当前 provider 不支持 function calling" | `chat_fc` |
| 无前置导出/下钻 | error（工具层） | "没有可导出的结果，请先调用 query_metrics" | `dispatcher` |

**话术原则**：①说明原因 ②给出下一步 ③不暴露内部细节 ④简体中文。

---

## 9. 越狱与提示词注入防护

| 攻击面 | 防护 |
| --- | --- |
| 诱导写库 | L3 只生成 SELECT + L4 DDL/DML 黑名单 + 数据库只读 |
| 诱导越权 | L5 权限在 SQL 层下推，prompt 无法绕过 |
| 编造指标/表 | L1/L2 白名单 + Schema enum + L4 表白名单 |
| 角色扮演绕过 | Agent 无"调试/管理员"后门指令，权限只看 user_id |
| 多语句/注释注入 | L4 拦截 `;` 与注释符 |
| Prompt 泄露 | 错误响应不含 system 原文；`/api/config` 只暴露非敏感配置 |

> **边界诚实声明**：当前未实现专门的"提示词注入检测器"（如分类模型/关键词过滤）。防护依赖**结构化约束**（工具 enum、白名单、只读校验），而非试图识别所有注入话术。这是更稳健的工程取向——不赌模型能识别注入，而是让注入即使成功也无从造成破坏。

---

## 10. 缺陷开关对照表（安全测试靶点）

18 个缺陷开关（`src/defects/registry.py`）默认关闭，作品集正常展示；开启后用于验证测试能否抓到对应的安全/质量问题：

| 开关 | 分类 | Guardrail 环节 | 验证目标 |
| --- | --- | --- | --- |
| D03 | 行级权限 | L5 行隔离 | 区域经理越权 |
| D04 | 行级权限 | L5 下钻 | 下钻未收窄到点击行 |
| D05 | 列级权限 | L5 脱敏 | 手机号未脱敏 |
| D06 | 列级权限 | L5 列裁剪 | 禁用列未剔除 |
| D07 | 只读安全 | L4 注入 | 多语句/注释绕过 |
| D08 | 只读安全 | L4 注入 | DDL/DML 未拦截 |
| D11 | 意图澄清 | L2 | 同名歧义未澄清 |
| D12 | 意图澄清 | L2 | 缺槽位未处理 |
| D16 | 数据导出 | L5 | 导出未绑定账号 |
| D18 | 审计日志 | L6 | 操作漏记 |

> 完整 18 项分类见 `src/defects/registry.py`；其余（时间口径、语义层、多轮记忆、RAG、兜底）属质量维度，见需求说明书与测试设计引导。

---

## 11. 安全测试矩阵

| 测试类 | 断言 | 对应防线 |
| --- | --- | --- |
| 参数校验 | 非法 metric/日期/业务线被 validate_call 拦截 | L1 |
| 只读 | 注入写操作，SQL 非 DDL/DML | L3/L4 |
| 注入 | 多语句/注释/参数控制字符被拦 | L4 |
| 行越权 | U002/U004/U006 查询不出授权外数据 | L5 |
| 列脱敏 | 禁用列消失、手机号脱敏 | L5 |
| 导出越权 | 他人 token 导出被拒（D16 关闭时） | L5 |
| 下钻越权 | 明细不含禁用列/他人范围 | L5 |
| 错误脱敏 | error 响应不含堆栈/密钥 | L6 |
| 审计完整 | 各操作有 audit 记录 | L6 |
| 话术规范 | clarify/fallback 态有 suggestions | L6 |
