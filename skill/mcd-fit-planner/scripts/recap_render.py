"""Self-contained recap artwork and HTML; no remote assets, tracking or CDN."""
from __future__ import annotations

from html import escape
import base64
import os
from pathlib import Path
import shutil
import re
import subprocess
import tempfile

WIDTH, HEIGHT = 1080, 1780
INK, MUTED, YELLOW, RED, GREEN = "#342317", "#786857", "#ffce32", "#cc3c2f", "#317757"
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


def render_svg(recap, demo=False):
    out = []
    def add(value): out.append(value)
    def rect(x, y, w, h, fill, r=24, extra=""):
        add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" {extra}/>')
    def txt(x, y, value, size=22, fill=INK, weight=400, extra=""):
        add(f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" font-weight="{weight}" {extra}>{escape(str(value))}</text>')
    bill, food = recap["billing"], recap["mcd_intake"]
    assessment = recap["meal_assessment"]
    counts = assessment["counts"]
    add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title desc">')
    add('<title id="title">我的吃麦手账</title><desc id="desc">消费、实际摄入与已记录目标的月度或年度总结。</desc>')
    add(f'<g font-family="{FONT}">')
    rect(0, 0, WIDTH, HEIGHT, "#fcf5e6", 0)
    add('<circle cx="1030" cy="80" r="300" fill="#ffde6a" opacity=".38"/>')
    add('<circle cx="-30" cy="1640" r="220" fill="#f3dfbb" opacity=".5"/>')
    rect(56, 48, 48, 48, RED, 15)
    txt(80, 81, "麦", 25, "#fff8e9", 700, 'text-anchor="middle"')
    txt(120, 80, "我的吃麦手账", 24, weight=700)
    txt(1024, 79, "MCD FIT PLANNER", 17, weight=700, extra='text-anchor="end" letter-spacing="2"')
    period = recap["period"].replace("-", " 年 ") + (" 月" if recap["kind"] == "month" else " 年")
    txt(56, 142, period + "  /  " + ("月度回味" if recap["kind"] == "month" else "年度回味"), 23, MUTED)
    txt(50, 226, "这一个月，" if recap["kind"] == "month" else "这一年，", 64, weight=800)
    txt(50, 306, "每一口都有数。", 64, weight=800)
    # Original vector burger illustration, separate from any official logo.
    add('<g transform="translate(762 165) rotate(-9 105 85)">')
    add('<ellipse cx="110" cy="157" rx="117" ry="19" fill="#decda9"/>')
    add('<path d="M0 70 Q12 -15 110 -15 Q208 -15 220 70Z" fill="#eaa844" stroke="#342317" stroke-width="5"/>')
    for x,y in [(48,30),(92,14),(143,27),(181,45),(114,46)]:
        add(f'<path d="M{x} {y}l8 -4" stroke="#fff2cc" stroke-width="5" stroke-linecap="round"/>')
    add('<path d="M1 78 Q30 65 58 82 T111 80 T166 78 T220 79 L215 98 L4 98Z" fill="#7b9d42" stroke="#342317" stroke-width="4"/>')
    rect(3, 96, 216, 25, "#713c27", 10)
    add('<path d="M3 94L78 119L113 99L192 115L218 94Z" fill="#ffd13c" stroke="#342317" stroke-width="3"/>')
    add('<path d="M3 124H218Q215 158 190 158H31Q8 158 3 124Z" fill="#eaa844" stroke="#342317" stroke-width="5"/>')
    add('</g>')
    if demo:
        rect(784, 318, 240, 34, "#fff8e9", 17)
        txt(904, 341, "合成示例 · 非个人记录", 16, RED, 700, 'text-anchor="middle"')
    rect(56, 374, 968, 250, INK, 30)
    txt(88, 423, "这一期，吃麦净支出", 23, "#efe0c6")
    amount = "¥" + money(bill["net_cents"])
    txt(86, 532, amount, min(84, 600 / (len(amount) * .61)), "#fff6df", 800)
    txt(90, 583, f"已付 ¥{money(bill['gross_cents'])}   /   已退 ¥{money(bill['refund_cents'])}", 20, "#e2c6a6")
    add('<path d="M724 420V580" stroke="#715943"/>')
    txt(766, 436, "付款订单", 20, "#e2c6a6")
    txt(766, 495, bill["paid_order_count"], 52, "#ffce32", 800)
    txt(766, 541, f"单均净支出 ¥{money(bill['average_net_cents'])}", 18, "#fff6df")
    txt(766, 576, f"待付款 {bill['pending_count']} 单 · 不计支出", 18, "#e2c6a6")
    stats = [("实际吃麦热量 · kcal", nutrient_text(food, "kcal")),
             ("实际吃麦蛋白质 · g", nutrient_text(food, "protein_g")),
             ("记录了吃麦的日子", str(recap["mcd_recorded_days"]) + " 天")]
    for index, (label, value) in enumerate(stats):
        x = 56 + index * 328
        rect(x, 652, 312, 146, "#fffdf7", 24)
        txt(x + 23, 694, label, 19, MUTED)
        txt(x + 23, 753, value, 32 if len(value) < 14 else 24, weight=800)
    txt(56, 833, "按实际食用份数统计；未知仅列已知下限，估算保留区间。", 17, MUTED)
    rect(56, 872, 436, 338, "#fffdf7", 26)
    txt(83, 918, "你的常点榜", 28, weight=800)
    txt(83, 951, "购买份数，不等于已经吃掉", 17, MUTED)
    if not recap["favorites"]:
        txt(83, 1025, "等一笔有餐品的付款记录", 21, MUTED)
    for index, item in enumerate(recap["favorites"]):
        y = 1008 + index * 65
        rect(83, y - 25, 34, 34, YELLOW if index == 0 else "#f3ead8", 11)
        txt(100, y, index + 1, 18, weight=700, extra='text-anchor="middle"')
        txt(131, y, short(item["name"], 12), 21, weight=600)
        txt(461, y + 25, f"× {item['quantity']:g}", 17, MUTED, extra='text-anchor="end"')
    rect(512, 872, 512, 338, "#eaf0df", 26)
    txt(540, 918, "吃麦餐食 × 我的目标", 28, weight=800)
    txt(540, 951, "对照录入时的训练 / 高碳 / 休息目标", 17, MUTED)
    percent = assessment["verified_within_percent"]
    add('<circle cx="614" cy="1047" r="58" fill="none" stroke="#d2dec4" stroke-width="13"/>')
    if percent is not None:
        add(f'<circle cx="614" cy="1047" r="58" fill="none" stroke="{GREEN}" stroke-width="13" stroke-dasharray="{percent / 100 * 364.425} 364.425" transform="rotate(-90 614 1047)" stroke-linecap="round"/>')
    txt(614, 1057, "—" if percent is None else f"{percent:g}%", 32, GREEN, 800, 'text-anchor="middle"')
    txt(614, 1140, "可核实达标率", 17, MUTED, extra='text-anchor="middle"')
    over = counts["above"] + counts["mixed"]
    txt(710, 1005, f"达标 {counts['within']} 餐  ·  估算达标 {counts['estimated_within']}", 19, GREEN, 700)
    txt(710, 1047, f"超出 {over} 餐  ·  不足 {counts['below'] + counts['mixed']} 餐", 19, RED, 700)
    unassessed = sum(counts[s] for s in ("uncertain", "no_goal", "no_history", "conflict"))
    txt(710, 1089, f"待核实 / 未设目标 {unassessed} 餐", 19, MUTED)
    txt(540, 1180, f"达标率分母 {assessment['verified_count']} 餐；缺失与估算不计入。", 16, MUTED)
    daily = recap["daily_assessment"]
    rect(56, 1234, 968, 92, "#f3e7cf", 22)
    dc = daily['counts']
    txt(82, 1271, f"全天核对：录全 {daily['total']} 天 · 达标 {dc['within']} 天 · 估算达标 {dc['estimated_within']} 天", 23, weight=700)
    txt(82, 1302, f"超出 {dc['above'] + dc['mixed']} 天 · 不足 {dc['below'] + dc['mixed']} 天；其他结果见明细。另有 {recap['unclosed_recorded_days']} 个记录日未确认录全。", 18, MUTED)
    rect(56, 1350, 968, 240, "#fffdf7", 26)
    txt(83, 1394, "这一期的吃麦节奏", 26, weight=800)
    txt(994, 1394, "每日净支出" if recap["kind"] == "month" else "每月净支出", 18, MUTED, extra='text-anchor="end"')
    buckets = recap["spending_buckets"]
    maximum = max([b["net_cents"] for b in buckets] + [1])
    step = 900 / len(buckets)
    for index, bucket in enumerate(buckets):
        x = 87 + index * step
        height = bucket["net_cents"] / maximum * 104
        rect(round(x, 2), 1525 - height, max(5, step - 8), max(3, height), YELLOW if bucket["net_cents"] else "#ece4d5", 4)
        if len(buckets) <= 12 or index % 5 == 0 or index == len(buckets) - 1:
            txt(x + (step - 8) / 2, 1558, bucket["label"], 16, MUTED, extra='text-anchor="middle"')
    txt(56, 1631, "小账本，只写真实发生的事。", 23, weight=700)
    txt(56, 1669, "本地已记录数据，不代表完整历史。账单不推断摄入，退款不删除饮食。", 18, MUTED)
    txt(56, 1700, f"未标明食物来源的记录 {recap['unclassified_entry_count']} 条，未计入吃麦摄入；一餐达标不等于全天或训练效果达标。", 17, MUTED)
    txt(56, 1745, "MCD FIT PLANNER  /  PERSONAL FOOD JOURNAL", 14, MUTED, extra='letter-spacing="2"')
    txt(1024, 1745, "非麦当劳官方出品", 15, MUTED, extra='text-anchor="end"')
    add('</g></svg>')
    return "\n".join(out)


def render_html(recap, svg, demo=False):
    from period_recap import STATUS_NAMES
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
    for d in recap["days"]:
        label = STATUS_NAMES[d["assessment"]["status"]] if d["assessment"] else "不作全天判断"
        if d["assessment"] and d["assessment"].get("estimated") and d["assessment"]["status"] != "estimated_within":
            label += "（估算）"
        cells = [d["day"], "已确认" if d["full_day_confirmed"] else "未确认", label, details(d["assessment"])]
        daily_rows.append('<tr>' + ''.join(f'<td>{escape(v)}</td>' for v in cells) + '</tr>')
    notes = ''.join(f'<li>{escape(note)}</li>' for note in recap["notes"])
    nutrients = ''.join(f'<div><span>{label}</span><strong>{escape(nutrient_text(recap["mcd_intake"], n))} {unit}</strong></div>' for n, label, unit in [('kcal','热量','kcal'),('protein_g','蛋白质','g'),('carb_g','碳水','g'),('fat_g','脂肪','g')])
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'">
<title>{escape(recap['period'])} · 我的吃麦手账</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f1eadc;color:{INK};font:15px/1.7 {FONT}}}
nav{{max-width:1080px;margin:24px auto 18px;display:flex;align-items:center;justify-content:space-between;padding:0 18px;gap:12px;flex-wrap:wrap}}
nav strong{{font-size:19px}}button,select{{border:1px solid #d8cbb9;background:#fffbf2;color:{INK};border-radius:12px;padding:10px 16px;font:inherit;cursor:pointer}}button.primary{{background:{INK};color:#fff5dd}}button:focus-visible,select:focus-visible{{outline:3px solid {RED}}}
.poster{{max-width:850px;margin:auto;border-radius:24px;overflow:hidden;box-shadow:0 22px 70px #5b40251c}}.poster svg{{display:block;width:100%;height:auto}}main{{max-width:1080px;margin:auto;padding:18px}}section{{background:#fffaf0;margin:26px 0;padding:28px;border-radius:22px}}h2{{margin:0 0 8px;font-size:23px}}p{{color:{MUTED}}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}}.grid span{{display:block;color:{MUTED}}}.grid strong{{font-size:22px}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;min-width:780px;text-align:left}}th,td{{padding:14px 12px;border-bottom:1px solid #e8decb;vertical-align:top}}th{{color:{MUTED};font-weight:500;white-space:nowrap}}td:last-child{{min-width:360px;font-size:13px}}.tools{{display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap}}li{{margin:9px 0;color:{MUTED}}}footer{{text-align:center;color:{MUTED};padding:20px 0 50px}}#message{{min-height:24px}}@media(max-width:620px){{section{{padding:20px}}.grid{{grid-template-columns:repeat(2,1fr)}}.poster{{border-radius:0}}nav{{margin:16px auto}}}}@media print{{nav,.tools select,footer{{display:none}}body{{background:white}}.poster{{max-width:100%;box-shadow:none}}main{{page-break-before:always}}}}
</style></head><body><nav><strong>吃麦有回味，记录有分寸。</strong><div><button id="svg-download">保存 SVG</button> <button id="png-download" class="primary">下载分享长图 PNG</button></div><span id="message" role="status"></span></nav>
<div class="poster" id="poster">{svg}</div><main>
<section><h2>实际吃麦 · 四项营养</h2><p>只统计明确标记的实际食用，包含半份、估算与缺失。{'这是合成示例，餐品、价格和目标均为虚构。' if demo else '这份文件包含你的消费与饮食统计，分享前请确认内容。'}</p><div class="grid">{nutrients}</div></section>
<section><div class="tools"><h2>吃麦餐食 · 目标明细</h2><label>筛选 <select id="filter"><option value="all">全部结果</option><option value="within">达标</option><option value="estimated_within">估算达标</option><option value="outside">超出或不足</option><option value="unknown">待核实或未设目标</option></select></label></div><p>每一行合并同一天、同餐段的已记录食物；包括该餐其他食物。超出和不足可能同时存在。</p><div class="scroll"><table><thead><tr><th>日期</th><th>餐段</th><th>当时模板</th><th>结果</th><th>实际 / 目标</th></tr></thead><tbody id="meal-rows">{''.join(rows) or '<tr><td colspan="5">暂无已确认的吃麦摄入；订单不会自动变成饮食记录。</td></tr>'}</tbody></table></div></section>
<section><h2>全天记录 · 另行核对</h2><p>只有用户明确确认全天饮食录全的日期才判断全天目标；新增或修正饮食会撤销该日确认。</p><div class="scroll"><table><thead><tr><th>日期</th><th>全天录全</th><th>结果</th><th>实际 / 目标</th></tr></thead><tbody>{''.join(daily_rows) or '<tr><td colspan="4">暂无实际饮食记录。</td></tr>'}</tbody></table></div></section>
<section><h2>怎么读这份手账</h2><ul>{notes}</ul></section></main><footer>MCD FIT PLANNER · 本机生成 · 无外部资源或网络请求 · 非麦当劳官方出品</footer>
<script>
const message=document.getElementById('message');
function save(blob,name){{const u=URL.createObjectURL(blob),a=document.createElement('a');a.href=u;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(u),1000)}}
function artwork(){{return new XMLSerializer().serializeToString(document.querySelector('#poster svg'))}}
document.getElementById('svg-download').onclick=()=>save(new Blob([artwork()],{{type:'image/svg+xml;charset=utf-8'}}),'mcd-recap-{recap['period']}.svg');
document.getElementById('png-download').onclick=async()=>{{message.textContent='正在生成长图…';await document.fonts.ready;const url=URL.createObjectURL(new Blob([artwork()],{{type:'image/svg+xml;charset=utf-8'}}));const img=new Image();img.onload=()=>{{try{{const canvas=document.createElement('canvas');canvas.width={WIDTH};canvas.height={HEIGHT};canvas.getContext('2d').drawImage(img,0,0);canvas.toBlob(blob=>{{if(blob){{save(blob,'mcd-recap-{recap['period']}.png');message.textContent='长图已生成';}}else message.textContent='当前浏览器无法导出，请保存 SVG。';}},'image/png')}}catch(e){{message.textContent='当前浏览器无法导出，请保存 SVG。'}}finally{{URL.revokeObjectURL(url)}}}};img.onerror=()=>{{URL.revokeObjectURL(url);message.textContent='图片导出失败，请保存 SVG。'}};img.src=url}};
document.getElementById('filter').onchange=e=>{{for(const row of document.querySelectorAll('#meal-rows tr[data-status]')){{const s=row.dataset.status,v=e.target.value;row.hidden=!(v==='all'||v===s||(v==='outside'&&['above','below','mixed'].includes(s))||(v==='unknown'&&['uncertain','no_goal','no_history','conflict'].includes(s)))}}}};
</script></body></html>'''


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
