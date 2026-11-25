"""给 LLM 的工具定义（OpenAI function calling 格式）+ 参数校验。"""
from __future__ import annotations
import re
from .config import business_lines, semantic

_CACHE = None

def tools_schema() -> list[dict]:
    global _CACHE
    if _CACHE is None:
        bl = list(business_lines()["business_lines"])
        mt = list(semantic()["metrics"])
        dim = list(semantic()["dimensions"])
        _CACHE = [
            {"type": "function", "function": {
                "name": "query_metrics",
                 "description": "按业务线和指标取聚合数据，是所有取数请求的主入口。业务线路由：零售销售用 retail，物流时效用 logistics，绩效用 performance，查错业务线会取到错误的表。日期换算：用户说'近N日'指含昨日在内最近N天，以系统提示给出的今天为 T，date_start=T-N、date_end=T-1，必须换算成 YYYY-MM-DD 再调用。指标 id 只能用 parameters 里枚举的值，不要编造。dimensions 默认留空取累计；仅当用户明确说按天/每日/各天时填 ['date']，地域/门店/品类/产品/卖家维度按用户原话填对应值，不要默认加 date。",
                "parameters": {"type": "object", "properties": {
                    "business_line": {"type": "string", "enum": bl},
                    "metrics": {"type": "array", "items": {"type": "string", "enum": mt}},
                    "dimensions": {"type": "array", "items": {"type": "string", "enum": dim}},
                    "date_start": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
                    "date_end": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
                }, "required": ["business_line", "metrics", "date_start", "date_end"]},
            }},
            {"type": "function", "function": {
                "name": "search_caliber",
                "description": "检索指标口径文档。遇到指标口径不确定、用户问口径定义时调用。",
                "parameters": {"type": "object", "properties": {
                    "query": {"type": "string"},
                    "business_line": {"type": "string", "enum": bl + ["all"]},
                }, "required": ["query"]},
            }},
            {"type": "function", "function": {
                "name": "compare_period",
                "description": "查同期对比（环比/同比）。只有用户明确说环比、同比、对比时才调用。",
                "parameters": {"type": "object", "properties": {
                    "business_line": {"type": "string", "enum": bl},
                    "compare_type": {"type": "string", "enum": ["mom", "yoy", "dod"]},
                }, "required": ["business_line", "compare_type"]},
            }},
            {"type": "function", "function": {
                "name": "get_data_dictionary",
                "description": "查数据字典：有哪些指标、维度、业务线。用户问'能查什么'时调用。",
                "parameters": {"type": "object", "properties": {
                    "business_line": {"type": "string", "enum": bl + ["all"]},
                }, "required": []},
            }},
            {"type": "function", "function": {
                "name": "export_result",
                "description": "导出当前取数结果为 CSV 或 Excel。用户说'导出''下载''存成Excel/CSV'时调用。必须在 query_metrics 之后调用，否则没有可导出的结果。",
                "parameters": {"type": "object", "properties": {
                    "fmt": {"type": "string", "enum": ["csv", "xlsx"]},
                }, "required": ["fmt"]},
            }},
            {"type": "function", "function": {
                "name": "drilldown_detail",
                "description": "展开当前取数结果的明细行。用户说'看明细''展开''下钻'时调用。必须在 query_metrics 之后调用。",
                "parameters": {"type": "object", "properties": {
                    "reason": {"type": "string"},
                }, "required": []},
            }},
            {"type": "function", "function": {
                "name": "query_history",
                "description": "查询用户之前的取数历史。用户说'我之前查过什么''历史记录'时调用。",
                "parameters": {"type": "object", "properties": {
                    "limit": {"type": "integer"},
                }, "required": []},
            }},
        ]
    return _CACHE

_SCHEMAS = {t["function"]["name"]: t["function"]["parameters"]
            for t in tools_schema()}

def validate_call(name: str, args: dict) -> str | None:
    """返回 None 表示通过，否则返回错误原因（记为无效调用）。"""
    schema = _SCHEMAS.get(name)
    if schema is None:
        return f"unknown_tool:{name}"
    props, required = schema["properties"], schema.get("required", [])
    for k in required:
        if k not in args:
            return f"missing:{k}"
    for k, v in args.items():
        spec = props.get(k)
        if spec is None:
            return f"extra_arg:{k}"              # 模型编造了不存在的参数
        if "enum" in spec and v not in spec["enum"]:
            return f"enum:{k}={v}"
        if "pattern" in spec and not re.fullmatch(spec["pattern"], str(v)):
            return f"pattern:{k}={v}"
        if spec["type"] == "array" and not isinstance(v, list):
            return f"type:{k}"
        if spec["type"] == "integer" and not isinstance(v, int):
            return f"type:{k}"
        if spec["type"] == "string" and not isinstance(v, str):
            return f"type:{k}"
    return None
