"""Editorial replay artwork. Product photograph is embedded for offline sharing."""
from __future__ import annotations

import base64
from html import escape
from pathlib import Path

WIDTH, HEIGHT = 1080, 1780
INK, MUTED, RED, PAPER, SAGE = "#211e19", "#736e63", "#d73a27", "#f5f1e7", "#dde5d7"
FONT = "'Microsoft YaHei','PingFang SC','Segoe UI',sans-serif"


def short(value, limit=18):
    value = str(value)
    return value if len(value) <= limit else value[:limit - 1] + "…"


def money(cents):
    return f"{cents / 100:,.2f}"


def nutrient_text(total, nutrient):
    if not total["item_count"]:
        return "未记录"
    b = total["nutrition_bounds"][nutrient]
    if b["max"] is None:
        return f"≥{b['min']:,.0f} · 待补全"
    if b["min"] != b["max"]:
        return f"{b['min']:,.0f}–{b['max']:,.0f}"
    return ("约 " if total["estimated"] else "") + f"{b['min']:,.0f}"


def product_image():
    data = (Path(__file__).resolve().parents[1] / "assets" / "big-mac-official.png").read_bytes()
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def replay_facts(recap):
    monthly = recap["kind"] == "month"
    period = recap["period"]
    counts = recap["meal_assessment"]["counts"]
    favorite = recap["favorites"][0] if recap["favorites"] else None
    return {"period_label": period.replace("-", " 年 ") + (" 月" if monthly else " 年"),
        "edition": "MONTHLY REPLAY" if monthly else "ANNUAL REPLAY",
        "this_period": "这个月" if monthly else "这一年", "favorite": favorite,
        "favorite_heading": "喜欢的那一口，\n又点了一次。" if favorite and favorite["quantity"] > 1 else "这一口，\n留在记录里。" if favorite else "下一份喜欢，\n还没写进来。",
        "above": counts["above"] + counts["mixed"], "below": counts["below"] + counts["mixed"],
        "unassessed": sum(counts[s] for s in ("uncertain", "no_goal", "no_history", "conflict")),
        "percent": recap["meal_assessment"]["verified_within_percent"]}


def render_svg(recap, demo=False):
    f = replay_facts(recap)
    bill, food, assessment = recap["billing"], recap["mcd_intake"], recap["meal_assessment"]
    counts = assessment["counts"]
    out = []
    def add(value): out.append(value)
    def box(x, y, w, h, color):
        add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{color}"/>')
    def text(x, y, value, size=24, color=INK, weight=400, extra=""):
        add(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{weight}" {extra}>{escape(str(value))}</text>')
    def rule(y, color="#d7d0c4", x=56, end=1024):
        add(f'<path d="M{x} {y}H{end}" stroke="{color}"/>')
    add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title desc">')
    add('<title id="title">吃麦的那些日子 · 我的吃麦回顾</title><desc id="desc">消费、常点餐品与实际饮食目标回顾。商品图为视觉配图。</desc>')
    add(f'<g font-family="{FONT}">')
    box(0, 0, WIDTH, HEIGHT, PAPER)
    text(56, 65, "MCD / REPLAY", 21, weight=800, extra='letter-spacing="2"')
    text(1024, 65, f["period_label"], 21, extra='text-anchor="end"')
    rule(88)
    text(51, 229, "吃麦的", 112, weight=900, extra='letter-spacing="-4"')
    text(51, 365, "那些日子。", 112, weight=900, extra='letter-spacing="-4"')
    text(620, 213, recap["period"][:4], 156, "#e4dfd2", 900, 'letter-spacing="-12"')
    add(f'<image href="{product_image()}" x="530" y="125" width="550" height="550" transform="rotate(-9 805 400)"/>')
    text(58, 483, recap["mcd_recorded_days"], 105, RED, 900)
    label_x = 56 + max(64, len(str(recap["mcd_recorded_days"])) * 62) + 44
    text(label_x, 446, "个有记录的吃麦日", 27, weight=600)
    text(label_x, 485, "从吃了什么，到为什么选它。", 21, MUTED)
    text(56, 548, "01 — THE LITTLE THINGS WE KEEP", 15, MUTED, extra='letter-spacing="2"')
    # Spending is a bold full-width chapter, rather than a dashboard widget.
    box(0, 580, WIDTH, 304, RED)
    text(56, 630, "记在这份账本里的净支出", 24, PAPER, 500)
    amount = "¥" + money(bill["net_cents"])
    text(48, 765, amount, min(142, 960 / (len(amount) * .65)), PAPER, 900, 'letter-spacing="-6"')
    text(58, 833, f"付款 {bill['paid_order_count']} 单", 25, PAPER, 700)
    text(370, 833, f"单均净支出 ¥{money(bill['average_net_cents'])}", 23, PAPER)
    text(1024, 833, f"已退 ¥{money(bill['refund_cents'])}", 23, PAPER, extra='text-anchor="end"')
    text(56, 938, "02 / 常点的那一份", 19, MUTED)
    favorite = f["favorite"]
    if favorite:
        text(52, 1021, short(favorite["name"], 14), 54, weight=900)
        text(56, 1072, f"在已付款且未全退的订单里，你点了 {favorite['quantity']:g} 份。", 24)
        remaining = "   /   ".join(f"{i + 2:02d}  {short(item['name'], 14)} ×{item['quantity']:g}" for i, item in enumerate(recap["favorites"][1:]))
        text(56, 1126, remaining or "这一期的常点榜，只有这一份。", 21, MUTED)
    else:
        text(52, 1021, "下一份喜欢，还没写进来。", 48, weight=800)
        text(56, 1080, "有餐品明细的付款记录，会在这里留下名字。", 23, MUTED)
    box(0, 1172, WIDTH, 333, SAGE)
    text(56, 1225, "03 / 和当时的饮食目标对照一下", 20)
    value = "—" if f["percent"] is None else f"{f['percent']:g}%"
    text(48, 1382, value, 142, weight=900, extra='letter-spacing="-7"')
    text(58, 1437, f"可核实达标率 · 分母 {assessment['verified_count']} 餐", 22)
    text(610, 1295, f"达标 {counts['within']} 餐", 32, weight=800)
    text(610, 1344, f"超出 {f['above']} 餐 / 不足 {f['below']} 餐", 24)
    text(610, 1390, f"估算达标 {counts['estimated_within']} 餐", 23)
    text(610, 1435, f"待核实或缺目标 {f['unassessed']} 餐", 22, MUTED)
    daily = recap["daily_assessment"]
    text(56, 1554, f"全天录全 {daily['total']} 天，其中达标 {daily['counts']['within']} 天；其余见网页。", 22, weight=600)
    text(56, 1602, "实际吃麦摄入", 19, MUTED)
    for index, (n, label, unit) in enumerate([("kcal", "热量", "kcal"), ("protein_g", "蛋白质", "g"), ("carb_g", "碳水", "g"), ("fat_g", "脂肪", "g")]):
        x = 56 + index * 247
        text(x, 1642, f"{label} / {unit}", 18, MUTED)
        display = nutrient_text(food, n)
        text(x, 1685, display, min(29, 220 / (len(display) * .65)), weight=800)
    rule(1721)
    text(56, 1755, "合成示例 · 非个人记录" if demo else "仅含本地记录 · 缺失与估算不计入达标率", 16, MUTED)
    text(1024, 1755, "商品图来自麦当劳中国官网 · 非官方作品", 15, MUTED, extra='text-anchor="end"')
    add('</g></svg>')
    return "\n".join(out)
