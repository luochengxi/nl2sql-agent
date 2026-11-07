"""数据导出（C6）：短期 CSV / Excel。

关键设计：导出复用【已经过权限裁剪的结果集】，而不是重新查一次库。
这样权限只有一条路径，不会出现"界面上看不到、导出里却有"的越权。
token 绑定 user_id，导出时二次校验。
"""
from __future__ import annotations

import csv
import io
import uuid

from openpyxl import Workbook

from ..defects import registry as _defects

_CACHE: dict[str, dict] = {}


def create_export_token(user_id: str, columns: list[str], rows: list[list],
                        sql: str | None, caliber_refs: list[str]) -> str:
    token = uuid.uuid4().hex[:16]
    _CACHE[token] = {
        "user_id": user_id,
        "columns": columns,
        "rows": rows,
        "sql": sql,
        "caliber_refs": caliber_refs,
    }
    return token


def export(token: str, user_id: str, fmt: str = "csv") -> tuple[str, bytes]:
    rec = _CACHE.get(token)
    if not rec:
        raise KeyError("导出凭证不存在或已过期")
    # [DEFECT-ID: D16] 导出凭证未绑定账号：任何人拿到 token 都能导出他人数据
    if not _defects.on("D16") and rec["user_id"] != user_id:
        raise PermissionError("无权导出该结果集")

    columns = rec["columns"]
    rows = rec["rows"]
    if fmt == "xlsx":
        wb = Workbook()
        ws = wb.active
        ws.title = "取数结果"
        ws.append(columns)
        for r in rows:
            ws.append(r)
        ws.append([])
        ws.append(["口径说明", "；".join(rec["caliber_refs"]) or "无"])
        ws.append(["SQL", rec["sql"] or ""])
        buf = io.BytesIO()
        wb.save(buf)
        return "取数结果.xlsx", buf.getvalue()

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(columns)
    w.writerows(rows)
    w.writerow([])
    w.writerow(["口径说明", "；".join(rec["caliber_refs"]) or "无"])
    w.writerow(["SQL", rec["sql"] or ""])
    # UTF-8 BOM，避免 Excel 打开中文乱码
    return "取数结果.csv", ("\ufeff" + out.getvalue()).encode("utf-8")
