"""Self-contained chapter-based replay and share image; no remote runtime assets."""
from __future__ import annotations

from html import escape
import base64
import os
from pathlib import Path
import shutil
import re
import subprocess
from string import Template
import tempfile

from recap_art import WIDTH, HEIGHT, FONT, INK, MUTED, RED, short, money, nutrient_text, product_image, replay_facts, render_svg


def render_html(recap, svg, demo=False):
    from period_recap import STATUS_NAMES
    assets = Path(__file__).resolve().parents[1] / "assets"
    facts = replay_facts(recap)
    meals = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}
    names = {"kcal": "热量", "protein_g": "蛋白质", "carb_g": "碳水", "fat_g": "脂肪"}
    def details(assessment):
        if assessment is None:
            return "尚未确认全天录全"
        pieces = []
        for n, result in assessment["nutrients"].items():
            b = result["actual"]
            actual = f"≥{b['min']:g}" if b["max"] is None else f"{b['min']:g}" if b["min"] == b["max"] else f"{b['min']:g}–{b['max']:g}"
            unit = "kcal" if n == "kcal" else "g"
            target = f"{result['target_min'] if result['target_min'] is not None else '无下限'}–{result['target_max'] if result['target_max'] is not None else '无上限'}"
            pieces.append(f"{names[n]} {actual} {unit} / 目标 {target} {unit}（{STATUS_NAMES[result['status']]}）")
        return "；".join(pieces) or STATUS_NAMES[assessment["status"]]
    rows = []
    for m in recap["meals"]:
        a = m["assessment"]
        label = STATUS_NAMES[a["status"]] + ("（估算）" if a.get("estimated") and a["status"] != "estimated_within" else "")
        cells = [m["day"], meals.get(m["meal"], m["meal"]), " / ".join(m["templates"]) or "未保存", label, details(a)]
        rows.append(f'<tr data-status="{a["status"]}">' + "".join(f"<td>{escape(v)}</td>" for v in cells) + '</tr>')
    daily_rows = []
    for day in recap["days"]:
        a = day["assessment"]
        label = STATUS_NAMES[a["status"]] if a else "不作全天判断"
        if a and a.get("estimated") and a["status"] != "estimated_within":
            label += "（估算）"
        cells = [day["day"], "已确认" if day["full_day_confirmed"] else "未确认", label, details(a)]
        daily_rows.append('<tr>' + ''.join(f'<td>{escape(v)}</td>' for v in cells) + '</tr>')
    billing = recap["billing"]
    counts = recap["meal_assessment"]["counts"]
    maximum = max([b["net_cents"] for b in recap["spending_buckets"]] + [1])
    bars = ''.join(f'<span class="spend-bar" style="height:{max(2, b["net_cents"] / maximum * 100):.2f}%" title="{escape(b["label"])}{("日" if recap["kind"] == "month" else "月")}：¥{money(b["net_cents"])}"></span>' for b in recap["spending_buckets"])
    favorites = ''.join(f'<li><span class="rank">{i + 1:02d}</span><span class="name">{escape(item["name"])}</span><span class="quantity">× {item["quantity"]:g}</span></li>' for i, item in enumerate(recap["favorites"]))
    nutrient_cells = ''.join(f'<div><span>{label} / {unit}</span><strong>{escape(nutrient_text(recap["mcd_intake"], n))}</strong></div>' for n, label, unit in [('kcal','热量','kcal'),('protein_g','蛋白质','g'),('carb_g','碳水','g'),('fat_g','脂肪','g')])
    strip = ''.join(f'<span style="flex:{value};background:{color}"></span>' for value, color in [(counts["within"], "#514337"), (counts["estimated_within"], "#a68e75"), (counts["above"] + counts["below"] + counts["mixed"], "#c6b39d"), (facts["unassessed"], "#e0d5c6")] if value)
    favorite = facts["favorite"]
    values = {"period": recap["period"], "period_label": facts["period_label"], "edition": facts["edition"],
        "this_period": facts["this_period"], "year": recap["period"][:4],
        "seal": recap["period"][5:] if recap["kind"] == "month" else recap["period"][:4],
        "demo_label": "合成示例 / 餐品、价格、营养与目标均为虚构" if demo else "来自你的本地记录 / 只在本机生成",
        "recorded_days": recap["mcd_recorded_days"], "net": money(billing["net_cents"]),
        "gross": money(billing["gross_cents"]), "refund": money(billing["refund_cents"]),
        "paid_count": billing["paid_order_count"], "average": money(billing["average_net_cents"]), "pending_count": billing["pending_count"],
        "chart_label": "本期每日净支出" if recap["kind"] == "month" else "本期每月净支出",
        "chart_start": "01", "chart_end": recap["spending_buckets"][-1]["label"], "chart_unit": "每日净支出" if recap["kind"] == "month" else "每月净支出",
        "favorite_name": favorite["name"] if favorite else "常点席位，暂时空着。",
        "rank_number": "01" if favorite else "—", "favorite_memo": "还是老样子。" if favorite and favorite["quantity"] > 1 else "记下这一口。" if favorite else "等待下一口。",
        "percent": "—" if facts["percent"] is None else f"{facts['percent']:g}%",
        "within": counts["within"], "above": facts["above"], "below": facts["below"],
        "estimated": counts["estimated_within"], "unassessed": facts["unassessed"], "verified_count": recap["meal_assessment"]["verified_count"],
        "goal_sentence": f"{recap['meal_assessment']['total']} 餐已记录吃麦餐食，{counts['within']} 餐符合录入时的目标。" if recap["meal_assessment"]["total"] else "还没有可核对的实际吃麦餐食，不从订单推断摄入。",
        "daily_total": recap["daily_assessment"]["total"], "daily_within": recap["daily_assessment"]["counts"]["within"],
        "daily_estimated": recap["daily_assessment"]["counts"]["estimated_within"], "unclosed_days": recap["unclosed_recorded_days"],
        "unclassified": recap["unclassified_entry_count"],
        "privacy_note": "本页为合成示例，不含个人账本。" if demo else "网页包含个人消费与饮食统计，分享前请确认内容。",
        "ticket_note": "SYNTHETIC DEMO / 合成示例" if demo else "YOUR LOCAL RECORDS / 非完整历史"}
    values = {key: escape(str(value)) for key, value in values.items()}
    values.update({"stylesheet": (assets / "recap.css").read_text(encoding="utf-8"),
        "javascript": (assets / "recap.js").read_text(encoding="utf-8"), "food_image": product_image(), "poster": svg,
        "favorite_heading": '<br>'.join(escape(line) for line in facts["favorite_heading"].splitlines()),
        "favorite_sentence": f'你在已付款且未全退的订单里，点了 <strong>{favorite["quantity"]:g}</strong> 份。' if favorite else '有餐品明细的付款记录，会在这里留下名字。',
        "spending_bars": bars, "favorite_rows": favorites or '<li>暂无有餐品明细的已付款记录。</li>',
        "goal_strip": strip, "nutrient_cells": nutrient_cells,
        "meal_rows": ''.join(rows) or '<tr><td colspan="5">暂无已确认的吃麦摄入；订单不会自动变成饮食记录。</td></tr>',
        "daily_rows": ''.join(daily_rows) or '<tr><td colspan="4">暂无实际饮食记录。</td></tr>',
        "notes": ''.join(f'<li>{escape(note)}</li>' for note in recap["notes"])})
    return Template((assets / "recap.html").read_text(encoding="utf-8")).substitute(values)


def render_png(svg, destination):
    candidates = [os.environ.get("MCD_RECAP_BROWSER"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe"]
    candidates += [shutil.which(name) for name in ("chromium", "chromium-browser", "google-chrome", "microsoft-edge")]
    browser = next((p for p in candidates if p and Path(p).is_file()), None)
    if not browser:
        return "未找到 Edge/Chrome；打开 HTML 点击下载 PNG，或使用 SVG。"
    with tempfile.TemporaryDirectory(prefix="mcd-recap-render-") as folder:
        folder = Path(folder)
        page = folder / "poster.html"
        source = base64.b64encode(svg.encode("utf-8")).decode("ascii")
        page.write_text(f'''<!doctype html><meta charset="utf-8"><body><script>
const img=new Image();img.onload=async()=>{{await document.fonts.ready;const canvas=document.createElement('canvas');canvas.width={WIDTH};canvas.height={HEIGHT};canvas.getContext('2d').drawImage(img,0,0);document.body.textContent=canvas.toDataURL('image/png')}};img.src='data:image/svg+xml;base64,{source}';
</script></body>''', encoding="utf-8")
        try:
            result = subprocess.run([str(browser), "--headless=new", "--disable-gpu", "--hide-scrollbars",
                "--no-first-run", "--disable-extensions", "--disable-background-networking",
                "--allow-file-access-from-files", "--force-device-scale-factor=1",
                "--run-all-compositor-stages-before-draw", "--virtual-time-budget=1200",
                f"--user-data-dir={folder / 'profile'}", "--dump-dom", page.as_uri()], capture_output=True, timeout=45,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            match = re.search(rb'<body>data:image/png;base64,([A-Za-z0-9+/=]+)</body>', result.stdout)
            if result.returncode != 0 or not match:
                return "浏览器未生成 PNG；打开 HTML 下载 PNG，或使用 SVG。"
            Path(destination).write_bytes(base64.b64decode(match[1], validate=True))
        except (subprocess.TimeoutExpired, OSError):
            return "浏览器渲染未完成；打开 HTML 下载 PNG，或使用 SVG。"
    return None


def write_artifacts(recap, html_path, svg_path=None, png_path=None, demo=False):
    svg = render_svg(recap, demo)
    html_path = Path(html_path).resolve()
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(render_html(recap, svg, demo), encoding="utf-8")
    result = {"html": str(html_path), "synthetic_demo": demo}
    if svg_path:
        path = Path(svg_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(svg, encoding="utf-8")
        result["svg"] = str(path)
    if png_path:
        path = Path(png_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        warning = render_png(svg, path)
        if warning:
            result["png_note"] = warning
        else:
            result["png"] = str(path)
    return result
