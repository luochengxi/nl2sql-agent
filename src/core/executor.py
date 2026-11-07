"""只读执行器。

除了 validator 的应用层校验，这里再用 sqlite 的 mode=ro 从连接层
保证"物理上写不了"——即使上层校验全被绕过，也改不动库。
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field

from .config import db_path, settings
from .nl2sql import CompiledQuery
from .validator import validate


@dataclass
class QueryResult:
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    elapsed_ms: float = 0.0
    truncated: bool = False


class QueryExecutionError(Exception):
    pass


def _connect() -> sqlite3.Connection:
    uri = f"file:{db_path()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = None
    return conn


def run(compiled: CompiledQuery) -> QueryResult:
    validate(compiled.sql, compiled.params)
    limit = int(settings().get("export", {}).get("max_rows", 50000))
    t0 = time.perf_counter()
    try:
        conn = _connect()
        try:
            cur = conn.execute(compiled.sql, compiled.params)
            rows = [list(r) for r in cur.fetchall()]
        finally:
            conn.close()
    except sqlite3.Error as e:
        raise QueryExecutionError(f"查询执行失败: {e}") from e
    elapsed = (time.perf_counter() - t0) * 1000

    truncated = len(rows) > limit
    if truncated:
        rows = rows[:limit]
    return QueryResult(
        columns=compiled.columns,
        rows=rows,
        elapsed_ms=round(elapsed, 1),
        truncated=truncated,
    )


def run_raw(sql: str, params: list | None = None) -> QueryResult:
    """给下钻/导出等受控场景使用，同样走 validator。"""
    validate(sql, params or [])
    t0 = time.perf_counter()
    try:
        conn = _connect()
        try:
            cur = conn.execute(sql, params or [])
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = [list(r) for r in cur.fetchall()]
        finally:
            conn.close()
    except sqlite3.Error as e:
        raise QueryExecutionError(f"查询执行失败: {e}") from e
    return QueryResult(columns=cols, rows=rows,
                       elapsed_ms=round((time.perf_counter() - t0) * 1000, 1))
