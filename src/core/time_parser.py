"""时间解析：把中文时间表述翻译成确定性的日期区间。

为什么独立成模块（见技术方案拆解 §10）:
- "近 7 日" 是业务日历问题，不是 SQL 问题；LLM 算日期极不可靠。
- 口径必须全公司统一，否则 A 报表和 B 报表对不上。
- 基准日 T 可注入，测试才能钉死日期、结果可复现。
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from .config import settings, today
from ..defects import registry as _defects

_COMPARE_POP = re.compile(r"环比|较上期|上期对比|对比上期|增长率|增长了多少|涨幅")
_COMPARE_YOY = re.compile(r"同比|去年同期|去年同比|年同比")


def _minus_year(d: date) -> date:
    """自然年对齐（不是 -365 天），处理闰年 2/29。"""
    try:
        return d.replace(year=d.year - 1)
    except ValueError:
        return d.replace(year=d.year - 1, day=28)


def default_time_range(base: date | None = None) -> "TimeRange":
    from .models import TimeRange

    t = base or today()
    n = int(settings().get("time", {}).get("default_window_days", 7))
    return TimeRange(start=(t - timedelta(days=n)).isoformat(),
                     end=(t - timedelta(days=1)).isoformat(),
                     preset=f"近{n}日(默认)")


def _attach_compare(tr: "TimeRange", text: str) -> "TimeRange":
    """给区间挂上对比周期（F9 环比 / 同比）。"""
    length = (date.fromisoformat(tr.end) - date.fromisoformat(tr.start)).days + 1
    if _COMPARE_YOY.search(text):
        tr.compare_type = "yoy"
        tr.compare_start = _minus_year(date.fromisoformat(tr.start)).isoformat()
        tr.compare_end = _minus_year(date.fromisoformat(tr.end)).isoformat()
    elif _COMPARE_POP.search(text):
        tr.compare_type = "pop"
        s = date.fromisoformat(tr.start) - timedelta(days=length)
        e = date.fromisoformat(tr.start) - timedelta(days=1)
        # [DEFECT-ID: D02] 对比周期长度不对：用单日平移代替整周期平移
        if _defects.on("D02"):
            s = date.fromisoformat(tr.start) - timedelta(days=1)
            e = date.fromisoformat(tr.start) - timedelta(days=1)
        tr.compare_start, tr.compare_end = s.isoformat(), e.isoformat()
    return tr


def parse_time(text: str, base: date | None = None) -> "TimeRange | None":
    from .models import TimeRange

    t = base or today()
    s = text.strip()

    m = re.search(r"近\s*(\d+)\s*(?:日|天)", s)
    if m:
        n = int(m.group(1))
        # [DEFECT-ID: D01] 滚动窗口含了当天，导致"今日数据虚低"
        end_offset = 0 if _defects.on("D01") else 1
        tr = TimeRange(
            start=(t - timedelta(days=n)).isoformat(),
            end=(t - timedelta(days=end_offset)).isoformat(),
            preset=f"近{n}日",
        )
        return _attach_compare(tr, s)

    m = re.search(r"近\s*(\d+)\s*个?月", s)
    if m:
        n = int(m.group(1)) * 30
        tr = TimeRange(start=(t - timedelta(days=n)).isoformat(),
                       end=(t - timedelta(days=1)).isoformat(), preset=f"近{m.group(1)}个月")
        return _attach_compare(tr, s)

    if re.search(r"昨天|昨日", s):
        d = (t - timedelta(days=1)).isoformat()
        return _attach_compare(TimeRange(start=d, end=d, preset="昨日"), s)

    if re.search(r"今天|今日|当天", s):
        d = t.isoformat()
        return _attach_compare(TimeRange(start=d, end=d, preset="今日"), s)

    if re.search(r"上周", s):
        this_monday = t - timedelta(days=t.weekday())
        last_monday = this_monday - timedelta(days=7)
        last_sunday = this_monday - timedelta(days=1)
        return _attach_compare(TimeRange(start=last_monday.isoformat(),
                                         end=last_sunday.isoformat(), preset="上周"), s)

    if re.search(r"本周|这周", s):
        monday = t - timedelta(days=t.weekday())
        end = min(t - timedelta(days=1), t)
        return _attach_compare(TimeRange(start=monday.isoformat(),
                                         end=end.isoformat(), preset="本周"), s)

    if re.search(r"上月|上个月", s):
        first = t.replace(day=1)
        last_month_end = first - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        return _attach_compare(TimeRange(start=last_month_start.isoformat(),
                                         end=last_month_end.isoformat(), preset="上月"), s)

    if re.search(r"本月|这个月", s):
        return _attach_compare(TimeRange(start=t.replace(day=1).isoformat(),
                                         end=(t - timedelta(days=1)).isoformat(),
                                         preset="本月"), s)

    if re.search(r"去年", s):
        return _attach_compare(TimeRange(start=date(t.year - 1, 1, 1).isoformat(),
                                         end=date(t.year - 1, 12, 31).isoformat(),
                                         preset="去年"), s)

    if re.search(r"今年|本年", s):
        return _attach_compare(TimeRange(start=date(t.year, 1, 1).isoformat(),
                                         end=(t - timedelta(days=1)).isoformat(),
                                         preset="今年"), s)

    m = re.search(r"第?\s*([1-4])\s*季度|Q([1-4])", s, re.I)
    if m:
        q = int(m.group(1) or m.group(2))
        start_m, end_m = (q - 1) * 3 + 1, q * 3
        return _attach_compare(TimeRange(
            start=date(t.year, start_m, 1).isoformat(),
            end=date(t.year, end_m, calendar.monthrange(t.year, end_m)[1]).isoformat(),
            preset=f"第{q}季度"), s)

    # 显式日期区间 2026-10-01~2026-10-07
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s*[~至到\-—]\s*(\d{4}-\d{2}-\d{2})", s)
    if m:
        return _attach_compare(TimeRange(start=m.group(1), end=m.group(2), preset="指定区间"), s)

    m = re.search(r"(\d{4}-\d{2}-\d{2})", s)
    if m:
        return _attach_compare(TimeRange(start=m.group(1), end=m.group(1), preset="指定日期"), s)

    return None


def describe(tr: "TimeRange | None") -> str:
    if tr is None:
        return "未指定时间"
    label = tr.preset or "自定义"
    base = f"{tr.start} ~ {tr.end}（{label}）"
    if tr.compare_type:
        cname = "同比" if tr.compare_type == "yoy" else "环比"
        base += f"，{cname}对比期 {tr.compare_start} ~ {tr.compare_end}"
    return base
