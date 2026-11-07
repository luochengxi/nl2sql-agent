"""指标口径管理后台（F16，产品 + 运营维护）。

v1.1 决策：口径文档由产品/运营维护，所以必须有一个正式入口，
每条口径带 owner / version / updated / status，保存后自动同步 RAG 索引，
并留操作痕迹。否则 RAG 会慢慢变成"谁都不敢改的 stale 文档"。
"""
from __future__ import annotations

import os
import re
from datetime import date

import yaml

from .config import caliber_dir

_SAFE_NAME = re.compile(r"^[一-龥A-Za-z0-9_\-]+\.md$")


def _path(filename: str) -> str:
    if not _SAFE_NAME.match(filename):
        raise ValueError("口径文件名只允许中英文、数字、下划线、连字符 + .md")
    return os.path.join(caliber_dir(), filename)


def upsert(filename: str, metric: str, business_line: str, owner: str,
           version: str, body: str, status: str = "active") -> dict:
    meta = {
        "metric": metric,
        "business_line": business_line,
        "owner": owner,
        "version": str(version),
        "updated": date.today().isoformat(),
        "status": status,
    }
    content = "---\n" + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False) + "---\n\n" + body.strip() + "\n"
    with open(_path(filename), "w", encoding="utf-8") as f:
        f.write(content)
    return {"filename": filename, **meta}


def read(filename: str) -> dict:
    p = _path(filename)
    if not os.path.exists(p):
        raise KeyError("口径文档不存在")
    with open(p, encoding="utf-8") as f:
        text = f.read()
    meta, body = {}, text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            meta = yaml.safe_load(parts[1]) or {}
            body = parts[2].strip()
    return {"filename": filename, **meta, "body": body}
