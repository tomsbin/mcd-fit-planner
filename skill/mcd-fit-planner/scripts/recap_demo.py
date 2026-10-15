"""Explicitly fictional recap fixtures; never query an account or place orders."""
from __future__ import annotations

from datetime import date

import fit_core as core


def populate(store, period):
    from period_recap import period_dates
    start, end = period_dates(period)
    meals = {"lunch": {"budget_cents": 3500,
        "target": {"kcal_min": 500, "kcal_max": 750, "protein_g_min": 30, "fat_g_max": 30}}}
    daily = {"kcal_min": 1800, "kcal_max": 2300, "protein_g_min": 90, "fat_g_max": 80}
    store.configure({"start_date": start.isoformat(), "cycle": ["训练日", "高碳日", "休息日"],
        "rest_template": "休息日", "templates": {
            "训练日": {"daily": daily, "meals": meals},
            "高碳日": {"daily": {**daily, "kcal_max": 2500}, "meals": {"lunch": {
                "budget_cents": 3500, "target": {"kcal_min": 600, "kcal_max": 900, "carb_g_min": 80}}}},
            "休息日": {"daily": {**daily, "kcal_max": 2100}, "meals": {"lunch": {
                "budget_cents": 3500, "target": {"kcal_min": 450, "kcal_max": 650, "protein_g_min": 25}}}}}})
    dates = ([date(start.year, start.month, d) for d in (1, 3, 6, 8, 10, 13, 16, 18, 21, 23, 25, min(end.day, 28))]
        if len(period) == 7 else [date(start.year, month, d) for month in range(1, 13) for d in (3, 12, 23)])
    names = ["经典汉堡三件套", "鸡肉汉堡轻食搭配", "双层汉堡套餐", "汉堡配玉米杯"]
    for index, day in enumerate(dates):
        day = day.isoformat()
        price = [2290, 2690, 3150, 2490, 1990][index % 5]
        status = "refunded" if index % 12 == 10 else "partial_refund" if index % 12 == 7 else "pending" if index % 12 == 11 else "paid"
        store.bill({"id": f"synthetic-bill-{index}", "day": day, "status": status,
            "paid_cents": 0 if status == "pending" else price,
            "refund_cents": price if status == "refunded" else 500 if status == "partial_refund" else 0,
            "source": "synthetic_demo", "items": [{"name": names[index % 4], "quantity": 1}]})
        if status in ("pending", "refunded"):
            continue
        kcal = [620, 810, 570, 690, 480, 730][index % 6]
        item = {"name": names[index % 4], "source": "official", "quantity": 1,
            "nutrition": {"kcal": kcal, "protein_g": [36, 29, 33, 42][index % 4],
                "carb_g": [75, 92, 65, 88][index % 4], "fat_g": [23, 35, 21, 28][index % 4]}}
        if index % 7 == 4:
            item["nutrition"]["kcal"] = None
        elif index % 7 == 5:
            item["source"] = "estimated"
            item["nutrition"]["kcal"] = None
            item["nutrition_bounds"] = {"kcal": {"min": kcal - 40, "max": kcal + 40}}
        store.record_intake({"id": f"synthetic-intake-{index}", "day": day, "meal": "lunch",
            "venue": "mcdonalds", "items": [item]})
        if index % 3 == 0:
            store.record_intake({"id": f"synthetic-other-{index}", "day": day, "meal": "dinner", "venue": "other",
                "items": [{"name": "其他全天餐食（合成）", "quantity": 1, "source": "official",
                    "nutrition": {"kcal": 1320, "protein_g": 70, "carb_g": 170, "fat_g": 36}}]})
            if day <= core.today():
                store.close_day(day, True)
