#!/usr/bin/env python3
"""
初始化模拟零售数仓。

用法:
    python scripts/init_warehouse.py                    # 以系统当天为锚点
    python scripts/init_warehouse.py --today 2026-11-03  # 钉死日期（测试可复现）
    python scripts/init_warehouse.py --days 120

设计要点（为什么要这样写）:
1. 固定随机种子 + 可注入 today  -> 任何人 clone 下来生成的数据完全一致，
   测试结果可复现；不依赖真实日历，避免"昨天的测试今天挂了"。
2. 数据只生成到 T-1            -> 与口径文档「近7日不含当天」一致，
   否则会出现"今天数据虚低"，这正是口径文档要防的坑。
3. 维度表 + 事实表 + 宽表视图   -> 视图把 JOIN 固化在库里，编译器只面向单表，
   行级权限下推有唯一注入点（见技术方案拆解 §5）。
4. is_valid / is_signed 等标记列 -> 让"口径"能用 SQL 精确表达，而不是靠 LLM 猜。
"""
from __future__ import annotations

import argparse
import os
import random
import sqlite3
import sys
from datetime import date, timedelta

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "warehouse.db")

SEED = 20261103

# 组织树从 config/org_tree.yaml 读取，与意图识别层共用同一份配置
with open(os.path.join(ROOT, "config", "org_tree.yaml"), encoding="utf-8") as _f:
    ORG = yaml.safe_load(_f)
REGIONS: dict[str, list[str]] = ORG["regions"]
CITY_CODE: dict[str, str] = ORG["city_code"]
STORES_PER_CITY: int = int(ORG.get("stores_per_city", 2))
PRODUCTS = [
    ("P001", "纯棉基础T恤", "服饰"),
    ("P002", "轻薄羽绒服", "服饰"),
    ("P003", "真皮休闲鞋", "服饰"),
    ("P004", "鲜萃挂耳咖啡", "食品"),
    ("P005", "坚果每日礼盒", "食品"),
    ("P006", "低温鲜牛奶", "食品"),
    ("P007", "无线降噪耳机", "数码"),
    ("P008", "便携充电宝", "数码"),
    ("P009", "智能手环", "数码"),
    ("P010", "氨基酸洗面奶", "日化"),
    ("P011", "植萃护发素", "日化"),
    ("P012", "厨房去污套装", "日化"),
]
ORDER_STATUS = ["已完成"] * 78 + ["已取消"] * 9 + ["已退款"] * 8 + ["待发货"] * 5
# 与口径文档一致：同城 24h、跨城 48h
SAME_CITY_LIMIT = 24
CROSS_CITY_LIMIT = 48


def build_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP VIEW IF EXISTS v_retail_order;
        DROP VIEW IF EXISTS v_logistics_ship;
        DROP VIEW IF EXISTS v_performance;
        DROP TABLE IF EXISTS fact_order;
        DROP TABLE IF EXISTS fact_logistics;
        DROP TABLE IF EXISTS fact_performance;
        DROP TABLE IF EXISTS dim_store;
        DROP TABLE IF EXISTS dim_city;
        DROP TABLE IF EXISTS dim_region;
        DROP TABLE IF EXISTS dim_seller;
        DROP TABLE IF EXISTS dim_product;

        CREATE TABLE dim_region (
            region_id   TEXT PRIMARY KEY,
            region_name TEXT NOT NULL
        );
        CREATE TABLE dim_city (
            city_id   TEXT PRIMARY KEY,
            city_name TEXT NOT NULL,
            region_id TEXT NOT NULL
        );
        CREATE TABLE dim_seller (
            seller_id   TEXT PRIMARY KEY,
            seller_name TEXT NOT NULL,
            phone       TEXT
        );
        CREATE TABLE dim_store (
            store_id   TEXT PRIMARY KEY,
            store_name TEXT NOT NULL,
            city_id    TEXT NOT NULL,
            seller_id  TEXT NOT NULL
        );
        CREATE TABLE dim_product (
            product_id   TEXT PRIMARY KEY,
            product_name TEXT NOT NULL,
            category     TEXT NOT NULL
        );

        CREATE TABLE fact_order (
            order_id       TEXT PRIMARY KEY,
            order_date     TEXT NOT NULL,
            store_id       TEXT NOT NULL,
            seller_id      TEXT NOT NULL,
            product_id     TEXT NOT NULL,
            qty            INTEGER NOT NULL,
            order_amount   REAL NOT NULL,
            pay_amount     REAL NOT NULL,
            cost_amount    REAL NOT NULL,
            order_status   TEXT NOT NULL,
            is_valid       INTEGER NOT NULL,
            customer_phone TEXT
        );
        CREATE TABLE fact_logistics (
            shipment_id    TEXT PRIMARY KEY,
            ship_date      TEXT NOT NULL,
            store_id       TEXT NOT NULL,
            order_id       TEXT NOT NULL,
            is_same_city   INTEGER NOT NULL,
            is_signed      INTEGER NOT NULL,
            delivery_hours REAL,
            is_ontime      INTEGER,
            supplier_cost  REAL
        );
        CREATE TABLE fact_performance (
            perf_id     TEXT PRIMARY KEY,
            stat_date   TEXT NOT NULL,
            store_id    TEXT NOT NULL,
            seller_id   TEXT NOT NULL,
            gmv         REAL NOT NULL,
            target_gmv  REAL NOT NULL,
            commission  REAL NOT NULL
        );

        CREATE INDEX idx_order_date   ON fact_order(order_date);
        CREATE INDEX idx_order_store  ON fact_order(store_id);
        CREATE INDEX idx_ship_date    ON fact_logistics(ship_date);
        CREATE INDEX idx_perf_date    ON fact_performance(stat_date);
        """
    )


def build_dims(conn: sqlite3.Connection) -> list[dict]:
    rng = random.Random(SEED)
    regions, cities, sellers, stores = [], [], [], []
    store_no = 0

    for rid, (region_name, city_names) in enumerate(REGIONS.items(), start=1):
        region_id = f"R{rid:02d}"
        regions.append((region_id, region_name))
        for cid, city_name in enumerate(city_names, start=1):
            city_id = f"C{rid:02d}{cid:02d}"
            cities.append((city_id, city_name, region_id))
            for shop in range(1, STORES_PER_CITY + 1):
                store_no += 1
                store_id = f"S{store_no:03d}"
                seller_id = f"SL{store_no:03d}"
                sellers.append((seller_id, f"{city_name}{shop}号店卖家", f"138{rng.randint(10000000, 99999999)}"))
                stores.append({
                    "store_id": store_id,
                    "store_name": f"{CITY_CODE[city_name]}{shop}号店",
                    "city_id": city_id,
                    "city_name": city_name,
                    "region_name": region_name,
                    "seller_id": seller_id,
                })

    conn.executemany("INSERT INTO dim_region VALUES (?,?)", regions)
    conn.executemany("INSERT INTO dim_city VALUES (?,?,?)", cities)
    conn.executemany("INSERT INTO dim_seller VALUES (?,?,?)", sellers)
    conn.executemany(
        "INSERT INTO dim_store(store_id,store_name,city_id,seller_id) VALUES (?,?,?,?)",
        [(s["store_id"], s["store_name"], s["city_id"], s["seller_id"]) for s in stores],
    )
    conn.executemany("INSERT INTO dim_product VALUES (?,?,?)", PRODUCTS)
    return stores


def build_facts(conn: sqlite3.Connection, stores: list[dict], today: date, days: int) -> tuple[int, int, int]:
    rng = random.Random(SEED + 1)
    dates = [(today - timedelta(days=d)).isoformat() for d in range(days, 0, -1)]

    orders, shipments, perfs = [], [], []
    order_seq = 0
    ship_seq = 0
    perf_seq = 0

    for d in dates:
        # 轻微周末效应，让趋势图有形状
        weekday = date.fromisoformat(d).weekday()
        weekend_boost = 1.4 if weekday >= 5 else 1.0

        for store in stores:
            n_orders = max(1, int(rng.randint(4, 9) * weekend_boost))
            for _ in range(n_orders):
                order_seq += 1
                oid = f"O{order_seq:07d}"
                product_id, _, _ = rng.choice(PRODUCTS)
                qty = rng.randint(1, 4)
                unit_price = rng.uniform(29, 399)
                order_amount = round(unit_price * qty, 2)
                status = rng.choice(ORDER_STATUS)
                pay_amount = 0.0 if status == "已取消" else order_amount
                is_valid = 1 if status == "已完成" else 0
                customer_phone = f"1{rng.choice('35678')}{rng.randint(10**8, 10**9 - 1)}"
                cost_amount = round(pay_amount * rng.uniform(0.55, 0.75), 2)

                orders.append((
                    oid, d, store["store_id"], store["seller_id"], product_id,
                    qty, order_amount, pay_amount, cost_amount,
                    status, is_valid, customer_phone,
                ))

                # 已完成 / 已退款 / 待发货的单据进入物流（已取消不发货）
                if status != "已取消" and rng.random() < 0.92:
                    ship_seq += 1
                    is_same_city = 1 if rng.random() < 0.6 else 0
                    is_signed = 1 if rng.random() < 0.93 else 0
                    if is_signed:
                        limit = SAME_CITY_LIMIT if is_same_city else CROSS_CITY_LIMIT
                        hours = round(rng.uniform(6, limit * 1.6), 1)
                        is_ontime = 1 if hours <= limit else 0
                    else:
                        hours = None
                        is_ontime = None
                    shipments.append((
                        f"W{ship_seq:07d}", d, store["store_id"], oid,
                        is_same_city, is_signed, hours, is_ontime,
                        round(rng.uniform(4, 18), 2),
                    ))

            # 绩效：每店每天一条
            perf_seq += 1
            gmv = round(rng.uniform(6000, 26000) * weekend_boost, 2)
            target = round(gmv * rng.uniform(0.85, 1.25), 2)
            perfs.append((
                f"F{perf_seq:07d}", d, store["store_id"], store["seller_id"],
                gmv, target, round(gmv * 0.03, 2),
            ))

    conn.executemany(
        "INSERT INTO fact_order VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", orders
    )
    conn.executemany(
        "INSERT INTO fact_logistics VALUES (?,?,?,?,?,?,?,?,?)", shipments
    )
    conn.executemany(
        "INSERT INTO fact_performance VALUES (?,?,?,?,?,?,?)", perfs
    )
    return len(orders), len(shipments), len(perfs)


def build_views(conn: sqlite3.Connection) -> None:
    # 三个视图统一携带同一套「权限相关列」，让行级过滤与列级脱敏逻辑完全一致。
    conn.executescript(
        """
        CREATE VIEW v_retail_order AS
        SELECT
            o.order_id, o.order_date, o.qty, o.order_amount, o.pay_amount,
            o.cost_amount, o.order_status, o.is_valid, o.customer_phone,
            s.store_id, s.store_name, s.seller_id,
            sl.seller_name, sl.phone AS seller_phone,
            c.city_name, r.region_name,
            p.product_name, p.category,
            NULL AS supplier_cost, NULL AS commission,
            NULL AS target_gmv, NULL AS gmv,
            NULL AS delivery_hours, NULL AS is_ontime,
            NULL AS is_signed, NULL AS stat_date, NULL AS ship_date
        FROM fact_order o
        JOIN dim_store   s  ON o.store_id = s.store_id
        JOIN dim_city    c  ON s.city_id  = c.city_id
        JOIN dim_region  r  ON c.region_id = r.region_id
        JOIN dim_seller  sl ON s.seller_id = sl.seller_id
        JOIN dim_product p  ON o.product_id = p.product_id;

        CREATE VIEW v_logistics_ship AS
        SELECT
            w.shipment_id, w.ship_date, w.delivery_hours, w.is_ontime,
            w.is_signed, w.supplier_cost, w.is_same_city,
            s.store_id, s.store_name, s.seller_id,
            sl.seller_name, sl.phone AS seller_phone,
            c.city_name, r.region_name,
            w.order_id,
            NULL AS customer_phone, NULL AS cost_amount,
            NULL AS commission, NULL AS target_gmv, NULL AS gmv,
            NULL AS order_date, NULL AS stat_date,
            NULL AS product_name, NULL AS category, NULL AS qty,
            NULL AS order_amount, NULL AS pay_amount, NULL AS order_status,
            NULL AS is_valid
        FROM fact_logistics w
        JOIN dim_store  s  ON w.store_id = s.store_id
        JOIN dim_city   c  ON s.city_id  = c.city_id
        JOIN dim_region r  ON c.region_id = r.region_id
        JOIN dim_seller sl ON s.seller_id = sl.seller_id;

        CREATE VIEW v_performance AS
        SELECT
            f.perf_id, f.stat_date, f.gmv, f.target_gmv, f.commission,
            s.store_id, s.store_name, s.seller_id,
            sl.seller_name, sl.phone AS seller_phone,
            c.city_name, r.region_name,
            NULL AS customer_phone, NULL AS cost_amount,
            NULL AS supplier_cost,
            NULL AS delivery_hours, NULL AS is_ontime, NULL AS is_signed,
            NULL AS order_date, NULL AS ship_date,
            NULL AS product_name, NULL AS category, NULL AS qty,
            NULL AS order_amount, NULL AS pay_amount, NULL AS order_status,
            NULL AS is_valid
        FROM fact_performance f
        JOIN dim_store  s  ON f.store_id = s.store_id
        JOIN dim_city   c  ON s.city_id  = c.city_id
        JOIN dim_region r  ON c.region_id = r.region_id
        JOIN dim_seller sl ON s.seller_id = sl.seller_id;
        """
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--today", default=date.today().isoformat(), help="锚定日期 YYYY-MM-DD")
    ap.add_argument("--days", type=int, default=120, help="生成多少天历史（到 T-1）")
    args = ap.parse_args()

    today = date.fromisoformat(args.today)
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    try:
        build_schema(conn)
        stores = build_dims(conn)
        n_order, n_ship, n_perf = build_facts(conn, stores, today, args.days)
        build_views(conn)
        conn.commit()
    finally:
        conn.close()

    print(f"[OK] {DB_PATH}")
    print(f"     锚定日 T={today.isoformat()}，数据区间 {args.days} 天，止于 T-1={(today - timedelta(days=1)).isoformat()}")
    print(f"     组织树: {len(REGIONS)} 区域 / {sum(len(v) for v in REGIONS.values())} 城市 / {len(stores)} 店铺")
    print(f"     事实表: 订单 {n_order} 行 / 物流 {n_ship} 行 / 绩效 {n_perf} 行")
    print(f"     视图:   v_retail_order, v_logistics_ship, v_performance")
    return 0


if __name__ == "__main__":
    sys.exit(main())
