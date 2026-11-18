"""规格：一个 QuerySpec 同时渲染出「问句」和「测试意图」。
改一条 spec，问句和真值一起变，不用两处硬编码。"""
from dataclasses import dataclass
from datetime import date, timedelta

# T = 业务上的"今天"。这是【测试数据准备】的选择，不是 oracle 逻辑。
# 数仓末日 2026-11-02；T=11-03 时"昨日"=11-02 有数据。想测空窗口就把 T 改大。
ANCHOR_T = "2026-11-03"


@dataclass(frozen=True)
class TimeWindow:
    kind: str        # relative | absolute
    start: str
    end: str
    days: int
    phrase: str      # 渲染进问句的时间短语


def relative(n: int, t: str = ANCHOR_T) -> TimeWindow:
    """近 n 日 = [T-n, T-1]；用标准库算，不 import 被测代码。"""
    t0 = date.fromisoformat(t)
    return TimeWindow("relative", (t0 - timedelta(days=n)).isoformat(),
                      (t0 - timedelta(days=1)).isoformat(), n, f"近{n}日")


def yesterday(t: str = ANCHOR_T) -> TimeWindow:
    d = (date.fromisoformat(t) - timedelta(days=1)).isoformat()
    return TimeWindow("relative", d, d, 1, "昨日")


def absolute(start: str, end: str) -> TimeWindow:
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    return TimeWindow("absolute", start, end, days, f"{start}到{end}")


# 渲染用中文词：每个指标挑一个「不跨业务线歧义」的别名，避免触发澄清
METRIC_WORD = {
    "valid_order_cnt": "有效订单量", "order_cnt": "订单量",
    "pay_amount": "成交额",        "aov": "客单价",
    "refund_rate": "退款率",       "sales_qty": "销量",
    "shipment_cnt": "发货单量",    "avg_delivery_hours": "平均履约时效",
    "ontime_rate": "及时妥投率",   "perf_gmv": "绩效销售额",
    "achieve_rate": "目标达成率",  "commission": "佣金",
    "target_gmv": "目标销售额",
}
# 维度短语：None=不写维度词（agent 会默认按 date）；其余命中对应维度别名
DIM_WORD = {None: "", "date": "按天", "region": "按区域", "city": "按城市",
            "store": "按门店", "seller": "按卖家", "category": "按品类",
            "product": "按商品"}


@dataclass(frozen=True)
class QuerySpec:
    metric: str                       # 语义层 metric id
    time: TimeWindow
    dim: str | None = None            # 语义层 dimension id
    scope: tuple[str, str] | None = None  # (维度id, 值)，如 ("region","华东")；None=全量
    user_id: str = "U001"             # U001=超管，无行级下推

    @property
    def query(self) -> str:
        parts = [self.scope[1] if self.scope else "", self.time.phrase,
                 DIM_WORD.get(self.dim, ""), METRIC_WORD[self.metric]]
        return " ".join(p for p in parts if p)

    @property
    def window(self) -> tuple[str, str, int]:
        return self.time.start, self.time.end, self.time.days




