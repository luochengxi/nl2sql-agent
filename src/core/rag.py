"""指标口径 RAG。

为什么是"关键词检索"而不是向量库（见技术方案拆解 §7）:
- 口径文档量小、术语规范，关键词 + 业务线过滤已足够精准。
- 零外部依赖，clone 下来就能跑；不需要 embedding 服务或网络。
- 命中可解释：能明确告诉用户"这次回答引用了哪条口径、哪个版本"。
- 文档是独立文件，产品/运营改文档不需要改代码、不需要发版。

索引是内存单例；F16 的口径管理后台通过 refresh() 让改动生效。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime

import yaml

from .config import caliber_dir
from ..defects import registry as _defects


@dataclass
class CaliberDoc:
    filename: str
    metric: str = ""
    business_line: str = "all"
    owner: str = ""
    version: str = "1.0"
    updated: str = ""
    status: str = "active"
    body: str = ""
    keywords: list[str] = field(default_factory=list)

    def ref(self) -> str:
        return f"{self.metric}（v{self.version}，{self.owner}，{self.filename}）"

    def summary(self, limit: int = 220) -> str:
        flat = re.sub(r"\s+", " ", self.body).strip()
        return flat[:limit] + ("…" if len(flat) > limit else "")


def _parse(path: str) -> CaliberDoc:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    meta: dict = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            meta = yaml.safe_load(parts[1]) or {}
            body = parts[2]
    doc = CaliberDoc(
        filename=os.path.basename(path),
        metric=str(meta.get("metric", os.path.splitext(os.path.basename(path))[0])),
        business_line=str(meta.get("business_line", "all")),
        owner=str(meta.get("owner", "")),
        version=str(meta.get("version", "1.0")),
        updated=str(meta.get("updated", "")),
        status=str(meta.get("status", "active")),
        body=body.strip(),
    )
    doc.keywords = [doc.metric, doc.filename.replace(".md", "")] + re.findall(r"[一-龥A-Za-z0-9]{2,}", doc.metric)
    return doc


class CaliberIndex:
    def __init__(self) -> None:
        self.docs: list[CaliberDoc] = []
        self.loaded_at: str = ""
        self.refresh()

    def refresh(self) -> int:
        """重新扫描口径目录。F16 改动文档后必须调用它才会生效。"""
        # [DEFECT-ID: D15] 口径文档变更后索引未同步：改动看不出效果
        if _defects.on("D15") and self.docs:
            return len(self.docs)
        found: list[CaliberDoc] = []
        d = caliber_dir()
        if os.path.isdir(d):
            for name in sorted(os.listdir(d)):
                if name.endswith(".md"):
                    found.append(_parse(os.path.join(d, name)))
        self.docs = found
        self.loaded_at = datetime.now().isoformat(timespec="seconds")
        return len(self.docs)

    def search(self, query: str, business_line: str | None = None,
               metric_names: list[str] | None = None, top_k: int = 3) -> list[CaliberDoc]:
        terms: list[str] = []
        if metric_names:
            terms.extend(metric_names)
        terms.extend(re.findall(r"[一-龥]{2,}|[A-Za-z]{3,}", query))
        terms = [t for t in dict.fromkeys(terms) if t]

        scored: list[tuple[int, CaliberDoc]] = []
        for doc in self.docs:
            if doc.status != "active":
                continue
            # 先算关键词命中，零命中的文档直接跳过：
            # 仅凭"业务线匹配"不应把不相关口径文档召回（否则答非所问）。
            keyword_score = 0
            for t in terms:
                if t == doc.metric:
                    keyword_score += 12
                elif t in doc.metric or t in doc.filename:
                    keyword_score += 5
                if t in doc.body:
                    keyword_score += 2
            if keyword_score <= 0:
                continue
            score = keyword_score
            if business_line and doc.business_line in (business_line, "all"):
                score += 3
            if score >= 6:
                scored.append((score, doc))
        scored.sort(key=lambda x: (-x[0], x[1].filename))
        return [d for _, d in scored[:top_k]]

    def get(self, filename: str) -> CaliberDoc | None:
        return next((d for d in self.docs if d.filename == filename), None)

    def list_all(self) -> list[CaliberDoc]:
        return list(self.docs)


_index: CaliberIndex | None = None


def get_index() -> CaliberIndex:
    global _index
    if _index is None:
        _index = CaliberIndex()
    return _index
