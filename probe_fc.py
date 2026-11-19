
import json
from src.core.llm import get_provider
from src.core.tool_schemas import tools_schema

p = get_provider()
r = p.complete_with_tools(
    [{"role": "system", "content": "你是取数助手，用工具回答。"},
     {"role": "user", "content": "近7日各城市有效订单量"}],
    tools_schema(),
)
print("provider:", p.name, "available:", p.available)
print(json.dumps(r, ensure_ascii=False, indent=2))
