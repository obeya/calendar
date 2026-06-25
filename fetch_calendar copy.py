"""
财经日历 → ICS 日历文件生成器
从 rl.fx678.com 提取：财经数据、国际假期预告、财经大事件
按国家生成 ICS，美国按重要性额外拆分，假期视为高重要性
"""
import requests
import re
import os
import sys
from datetime import datetime, timedelta, date
from bs4 import BeautifulSoup
import subprocess
import time
import urllib.parse
import hmac
import hashlib
import base64
import json

webhook = "https://oapi.dingtalk.com/robot/send?access_token=7a34e5f050de696dd5ad7d2bd2608cadca96ab57e60e31872a2a14048b62cb67"
secret = "SEC009baa8df944bd43608b4e2d50c02df8ccf00a962567e015d939ff9932da297d"

BASE_URL = "https://rl.fx678.com/date/{date}.html"

OUTPUT_DIR = '/mnt/internal/workspace/calendar'
# OUTPUT_DIR = r'C:\Users\obeya\OneDrive\Workspaces\calendar'

# ============================================================
# 配置：需要生成 ICS 的国家/区域（按需增删）
# ============================================================
ENABLED_COUNTRIES = [
    "中国",
    "美国",
    "日本",
    "德国",
    "欧元区",
]

US_IMPORTANCE_SPLIT = True

# ============================================================
# 钉钉通知配置：控制哪些事件需要提前推送
# ============================================================
NOTIFY_CONFIG = {
    "advance_minutes": 5,                     # 提前多少分钟推送
    "importance": ['高'],                      # 只推送高重要性事件
    "countries": ['美国', '中国'],    # 只推送这些国家/区域
    "types": ['财经数据', '财经大事件', '国际假期预告'],        # 只推送这些类型（"国际假期预告"一般不需要推送）
}

COUNTRY_MAP = {
    "c_usa": "美国", "c_uk": "英国", "c_euro": "欧元区",
    "c_germany": "德国", "c_france": "法国", "c_italy": "意大利",
    "c_spain": "西班牙", "c_greece": "希腊",
    "c_japan": "日本", "c_china": "中国", "c_korea_south": "韩国",
    "c_australia": "澳大利亚", "c_new_zealand": "新西兰",
    "c_canada": "加拿大", "c_switzerland": "瑞士",
    "c_russia": "俄罗斯", "c_india": "印度", "c_indea": "印度",
    "c_brazil": "巴西", "c_thailand": "泰国", "c_singapore": "新加坡",
    "c_south_africa": "南非", "c_mexico": "墨西哥", "c_turkey": "土耳其",
    "c_european_union": "欧元区", "c_ukraine": "乌克兰",
    "c_hong_kong": "中国香港", "c_taiwan": "中国台湾",
    "c_sweden": "瑞典", "c_norway": "挪威", "c_philippines": "菲律宾",
    "c_vietnam": "越南", "c_indonesia": "印度尼西亚",
    "c_null_flags": "其他",
}

# ISO 国家/地区缩写，用于生成 ASCII 安全文件名
COUNTRY_ABBR = {
    "中国": "CN",
    "美国": "US",
    "日本": "JP",
    "德国": "DE",
    "欧元区": "EU",
    "英国": "GB",
    "法国": "FR",
    "意大利": "IT",
    "西班牙": "ES",
    "希腊": "GR",
    "韩国": "KR",
    "澳大利亚": "AU",
    "新西兰": "NZ",
    "加拿大": "CA",
    "瑞士": "CH",
    "俄罗斯": "RU",
    "印度": "IN",
    "巴西": "BR",
    "泰国": "TH",
    "新加坡": "SG",
    "南非": "ZA",
    "墨西哥": "MX",
    "土耳其": "TR",
    "乌克兰": "UA",
    "中国香港": "HK",
    "中国台湾": "TW",
    "瑞典": "SE",
    "挪威": "NO",
    "菲律宾": "PH",
    "越南": "VN",
    "印度尼西亚": "ID",
}


def safe_filename(name: str) -> str:
    """返回 ASCII 安全的文件名，避免 Linux 非 UTF-8 locale 下中文乱码"""
    return COUNTRY_ABBR.get(name, name.upper())


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def ics_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def format_dt(d: date, hm: str) -> tuple[str, str]:
    if hm and re.match(r"^\d{1,2}:\d{2}$", hm):
        h, m = hm.split(":")
        dt_str = d.strftime("%Y%m%d") + f"T{int(h):02d}{int(m):02d}00"
        return dt_str, dt_str
    else:
        dt_str = d.strftime("%Y%m%d")
        return dt_str, dt_str


def parse_economic_data(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id="current_data")
    if not table:
        return []

    raw_rows = []
    current_time = ""
    current_country = ""

    for row in table.find_all("tr"):
        cls = row.get("class") or []
        if "title-fixed" in cls or "thss" in cls:
            continue

        time_cell = row.find("td", class_=re.compile(r"tab_time"))
        if time_cell:
            current_time = time_cell.get_text(strip=True)

        flag_div = row.find("div", class_=re.compile(r"flag_bb"))
        if flag_div:
            country_div = flag_div.find("div", class_=re.compile(r"circle_flag"))
            if country_div:
                for c in country_div.get("class", []):
                    if c.startswith("c_"):
                        current_country = COUNTRY_MAP.get(c, c)
                        break

        font_cell = row.find("td", class_="tab_font")
        if not font_cell:
            continue
        link = font_cell.find("a")
        if not link:
            continue
        indicator = link.get_text(strip=True)

        prev = row.find("td", class_="previous_price")
        previous = prev.get_text(strip=True) if prev else ""

        srv = row.find("td", class_="survey_price")
        survey = srv.get_text(strip=True) if srv else ""

        act = row.find("td", class_=re.compile(r"gb"))
        actual = act.get_text(strip=True) if act else ""

        importance = ""
        for td in row.find_all("td"):
            txt = td.get_text(strip=True)
            if txt in ("高", "中", "低"):
                importance = txt
                break

        # 判断是否为子项（td.tab_font 有 follow 类）
        is_child = any("follow" in c for c in font_cell.get("class", []))

        raw_rows.append({
            "time": current_time,
            "country": current_country,
            "indicator": indicator,
            "previous": previous,
            "survey": survey if survey else "-",
            "actual": actual if actual else "-",
            "importance": importance,
            "is_child": is_child,
        })

    # 合并子项到父项
    results = []
    for row in raw_rows:
        if row["is_child"] and results:
            # 合并到上一个同组项（共享 time+country）
            parent = results[-1]
            parent["children"].append(row)
            # 父项重要性取最高
            imp_order = {"高": 3, "中": 2, "低": 1, "": 0}
            if imp_order.get(row["importance"], 0) > imp_order.get(parent["importance"], 0):
                parent["importance"] = row["importance"]
        else:
            results.append({
                "type": "财经数据",
                "time": row["time"],
                "country": row["country"],
                "summary": row["indicator"],
                "description": f"前值: {row['previous']} | 预测: {row['survey']} | 公布: {row['actual']}",
                "importance": row["importance"],
                "location": row["country"],
                "children": [],
            })

    # 生成含子项的 description，子项标注重要性
    imp_star = {"高": " ★★★", "中": " ★★", "低": " ★", "": ""}
    for item in results:
        if item["children"]:
            extra_lines = []
            for child in item["children"]:
                star = imp_star.get(child["importance"], "")
                extra_lines.append(
                    f"-- {child['indicator']}{star}: "
                    f"前值: {child['previous']} | "
                    f"预测: {child['survey']} | "
                    f"公布: {child['actual']}"
                )
            item["description"] += "\n" + "\n".join(extra_lines)

    return results


def parse_holidays_and_events(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    results = []
    tables = soup.find_all("table", class_="cjsj_tab2")
    current_section = ""

    for table in tables:
        prev_header = table.find_previous("div", class_="sq_logo")
        if prev_header:
            img = prev_header.find("img")
            if img and "calendar_redio" in img.get("src", ""):
                current_section = "国际假期预告"
            elif img and "calendar_thigs" in img.get("src", ""):
                current_section = "财经大事件"

        if table.get("id") in ("next_event",) or table.get("style") == "display:none":
            continue
        if prev_header:
            img = prev_header.find("img")
            if img and "gz.png" in img.get("src", ""):
                break

        for row in table.find_all("tr"):
            if row.get("class") and "s_blue" in row["class"]:
                continue

            cells = row.find_all("td")
            if not cells:
                continue

            time_str = ""
            for cell in cells:
                if cell.get("class") and "tab_time" in cell.get("class", []):
                    time_str = cell.get_text(strip=True)
                    break
            if not time_str:
                time_str = cells[0].get_text(strip=True) if cells else ""

            country = ""
            flag_div = row.find("div", class_=re.compile(r"flag_bb"))
            if flag_div:
                span = flag_div.find("span")
                if span:
                    country = span.get_text(strip=True)
                else:
                    country_div = flag_div.find("div", class_=re.compile(r"circle_flag"))
                    if country_div:
                        for c in country_div.get("class", []):
                            if c.startswith("c_"):
                                country = COUNTRY_MAP.get(c, c)
                                break

            if current_section == "国际假期预告":
                if len(cells) >= 4:
                    location = cells[2].get_text(strip=True) if len(cells) > 2 else ""
                    if not location or location == "---":
                        location = country  # 无地点时用国家
                    event = cells[3].get_text(strip=True) if len(cells) > 3 else ""
                    results.append({
                        "type": "国际假期预告",
                        "time": time_str,
                        "country": country,
                        "summary": event,
                        "description": event,
                        "importance": "高",
                        "location": location,
                        "children": [],
                    })
            elif current_section == "财经大事件":
                if len(cells) >= 5:
                    location = cells[2].get_text(strip=True) if len(cells) > 2 else ""
                    event = cells[4].get_text(strip=True) if len(cells) > 4 else ""
                    imp = ""
                    imp_img = cells[3].find("img") if len(cells) > 3 else None
                    if imp_img:
                        src = imp_img.get("src", "")
                        if "star_3" in src:
                            imp = "高"
                        elif "star_2" in src:
                            imp = "中"
                        elif "star_1" in src:
                            imp = "低"
                    results.append({
                        "type": "财经大事件",
                        "time": time_str,
                        "country": country,
                        "summary": event,
                        "description": event,
                        "importance": imp,
                        "location": location,
                        "children": [],
                    })

    return results


KEY_KEYWORDS = ["CPI", "PPI", "非农", "FOMC", "GDP", "假期", "就业", "失业", "生产", "消费", "PCE"]
KEY_PATTERN = re.compile("|".join(KEY_KEYWORDS), re.IGNORECASE)


def filter_key_events(items: list[dict]) -> list[dict]:
    """只保留 CPI / PPI / 非农 / FOMC / GDP / 假期 相关事件"""
    result = []
    for item in items:
        summary = item.get("summary", "")
        desc = item.get("description", "")
        etype = item.get("type", "")
        # 假期类事件 type 为 "国际假期预告"，需额外检查 type 字段
        if KEY_PATTERN.search(summary) or KEY_PATTERN.search(desc) or KEY_PATTERN.search(etype):
            result.append(item)
    return result


def filter_by_importance(items: list[dict], level: str) -> list[dict]:
    """筛选指定重要性的条目，子项也同步过滤（仅保留同级别子项）"""
    imp_star = {"高": " ★★★", "中": " ★★", "低": " ★", "": ""}

    result = []
    for item in items:
        if item.get("importance", "") != level:
            continue
        filtered = dict(item)
        if item.get("children"):
            kept = [c for c in item["children"] if c.get("importance", "") == level]
            parent_desc = item["description"].split("\n-- ")[0]
            if kept:
                extra = []
                for c in kept:
                    star = imp_star.get(c.get("importance", ""), "")
                    extra.append(
                        f"-- {c['indicator']}{star}: "
                        f"前值: {c['previous']} | "
                        f"预测: {c['survey']} | "
                        f"公布: {c['actual']}"
                    )
                filtered["description"] = parent_desc + "\n" + "\n".join(extra)
            else:
                filtered["description"] = parent_desc
            filtered["children"] = kept
        result.append(filtered)
    return result


def fetch_date(date_str: str) -> list[dict]:
    url = BASE_URL.format(date=date_str)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = "utf-8"
        items = []
        items.extend(parse_economic_data(resp.text))
        items.extend(parse_holidays_and_events(resp.text))
        return items
    except Exception as e:
        print(f"  [错误] {date_str}: {e}", file=sys.stderr)
        return []


def build_ics(name: str, items: list[dict]) -> str:
    imp_map = {"高": "★★★", "中": "★★", "低": "★"}

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//FX678 Economic Calendar//https://gitee.com/obeya//",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{name}",
        f"X-WR-CALDESC:财经日历 ({name})",
        "X-WR-TIMEZONE:Asia/Shanghai",
        "BEGIN:VTIMEZONE",
        "TZID:Asia/Shanghai",
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        "TZOFFSETFROM:+0800",
        "TZOFFSETTO:+0800",
        "TZNAME:CST",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]

    for item in items:
        event_date = item.get("date_obj")
        if not event_date:
            continue

        time_str = item.get("time", "")
        dt_start, dt_end = format_dt(event_date, time_str)

        stars = imp_map.get(item.get("importance", ""), "")
        etype = item.get("type", "")
        country = item.get("country", "")

        # 类型 → emoji + 标签
        type_meta = {
            "财经数据":   ("📊", "财经数据"),
            "国际假期预告": ("🏖️", "国际假期"),
            "财经大事件":  ("📰", "财经事件"),
        }
        emoji, type_label = type_meta.get(etype, ("📌", etype))
        summary = f"{emoji} {item['summary']}"

        desc_parts = [f"{emoji} {type_label}", f"区域: {country}"]
        if stars:
            desc_parts.append(f"重要性: {stars}")
        desc_parts.append(item.get("description", ""))
        description = "\n".join(desc_parts)

        uid = f"fx678-{event_date.strftime('%Y%m%d')}-{hash(item['summary'] + country + etype) & 0x7FFFFFFF:08x}"
        is_all_day = "T" not in dt_start

        lines.append("BEGIN:VEVENT")
        if is_all_day:
            lines.append(f"DTSTART;VALUE=DATE:{dt_start}")
            lines.append(f"DTEND;VALUE=DATE:{dt_end}")
        else:
            lines.append(f"DTSTART;TZID=Asia/Shanghai:{dt_start}")
            lines.append(f"DTEND;TZID=Asia/Shanghai:{dt_end}")

        # LOCATION: 假期设置地点（网页提供 或 国家兜底），其他类型不设
        if item.get("location") and item["location"] != "---":
            lines.append(f"LOCATION:{ics_text(item['location'])}")
        elif etype == "国际假期预告":
            lines.append(f"LOCATION:{ics_text(country)}")

        lines.append(f"SUMMARY:{ics_text(summary)}")
        lines.append(f"DESCRIPTION:{ics_text(description)}")
        lines.append(f"UID:{uid}")
        lines.append(f"CATEGORIES:{etype}")

        # Outlook 兼容：X-MICROSOFT-CDO-BUSYSTATUS
        lines.append("X-MICROSOFT-CDO-BUSYSTATUS:BUSY")

        if not is_all_day:
            lines.append("BEGIN:VALARM")
            lines.append("TRIGGER;RELATED=START:-PT10M")
            lines.append("ACTION:DISPLAY")
            lines.append(f"DESCRIPTION:{ics_text(summary)}")
            lines.append("END:VALARM")
        else:
            lines.append("BEGIN:VALARM")
            lines.append("TRIGGER;RELATED=START:-PT12H")
            lines.append("ACTION:DISPLAY")
            lines.append(f"DESCRIPTION:{ics_text(summary)}")
            lines.append("END:VALARM")

        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


def write_ics_file(fname: str, name: str, items: list[dict]):
    if not items:
        return
    ics_content = build_ics(name, items)
    fpath = os.path.join(OUTPUT_DIR, fname)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(ics_content)


def fetch_all_data() -> list[dict]:
    """拉取昨天、今天、后7天的所有日历数据"""
    today = date.today()
    dates = []
    for offset in range(-1, 8):
        d = today + timedelta(days=offset)
        dates.append(d.strftime("%Y%m%d"))

    print(f"财经日历 ICS 生成器")
    print(f"运行日期: {today}")
    print(f"日期范围: {dates[0]} ~ {dates[-1]} (共{len(dates)}天)")
    print(f"数据源: rl.fx678.com\n")

    all_items = []
    for date_str in dates:
        y, m, d = date_str[:4], date_str[4:6], date_str[6:8]
        print(f"  获取 {y}-{m}-{d} ...", end=" ", flush=True)
        items = fetch_date(date_str)
        page_date = datetime.strptime(date_str, "%Y%m%d").date()
        for item in items:
            item["date_obj"] = page_date
        all_items.extend(items)
        print(f"{len(items)} 条")

    type_counts = {}
    for item in all_items:
        t = item.get("type", "未知")
        type_counts[t] = type_counts.get(t, 0) + 1
    print(f"\n总计: {len(all_items)} 条记录")
    for t, c in type_counts.items():
        print(f"  {t}: {c} 条")

    return all_items


def generate_ics_files(all_items: list[dict]):
    """按国家/重要性生成所有 ICS 文件"""
    # 按国家分组
    groups: dict[str, list[dict]] = {}
    for item in all_items:
        country = item["country"]
        if country not in groups:
            groups[country] = []
        groups[country].append(item)

    # 1. 按配置生成各国家 ICS
    for country in ENABLED_COUNTRIES:
        items = groups.get(country, [])
        if items:
            write_ics_file(f"{safe_filename(country)}.ics", country, items)
        else:
            print(f"\n  [跳过] {country}: 当前日期范围无数据")

    # 2. 美国按重要性拆分
    if US_IMPORTANCE_SPLIT and "美国" in ENABLED_COUNTRIES:
        us_items = groups.get("美国", [])
        if us_items:
            us_high = filter_by_importance(us_items, "高")
            us_mid = filter_by_importance(us_items, "中")

            for imp_label, items_sub in [
                ("High", us_high),
                ("Mid", us_mid),
            ]:
                if items_sub:
                    write_ics_file(f"US_{imp_label.upper()}.ics", f"美国-{imp_label}", items_sub)

            print(f"\n美国重要性拆分:")
            print(f"  高重要性 (含假期，子项过滤): {len(us_high)} 条")
            print(f"  中重要性: {len(us_mid)} 条")

            # 3. 美国关键+重要事件 → US_KEY.ics
            us_key = filter_key_events(us_items)
            us_key_high = filter_by_importance(us_key, "高")
            if us_key_high:
                write_ics_file("US_KEY.ics", "美国-Key", us_key_high)
                print(f"\n美国关键重要事件 (CPI/PPI/非农/FOMC/GDP/假期 + 高重要性): {len(us_key_high)} 条")

    # 4. 中国高重要性事件 → CN_HIGH.ics
    cn_items = groups.get("中国", [])
    if cn_items:
        cn_high = filter_by_importance(cn_items, "高")
        if cn_high:
            write_ics_file("CN_HIGH.ics", "中国-High", cn_high)
            print(f"\n中国高重要性事件: {len(cn_high)} 条")

    print(f"\nICS 文件已生成到根目录:")
    for fname in sorted(os.listdir(OUTPUT_DIR)):
        if fname.endswith(".ics"):
            fpath = os.path.join(OUTPUT_DIR, fname)
            size = os.path.getsize(fpath)
            print(f"  {fname} ({size:,} bytes)")


def build_notification_queue(all_items: list[dict]) -> list[tuple]:
    """从所有事件中提取有时间且符合通知配置的事件，构建按通知时间排序的队列"""
    now = datetime.now()
    queue = []

    cfg_importance = set(NOTIFY_CONFIG.get("importance", []))
    cfg_countries = set(NOTIFY_CONFIG.get("countries", []))
    cfg_types = set(NOTIFY_CONFIG.get("types", []))

    for item in all_items:
        time_str = item.get("time", "")
        date_obj = item.get("date_obj")
        if not date_obj or not time_str:
            continue
        if not re.match(r"^\d{1,2}:\d{2}$", time_str):
            continue

        # 按配置过滤
        if cfg_importance and item.get("importance", "") not in cfg_importance:
            continue
        if cfg_countries and item.get("country", "") not in cfg_countries:
            continue
        if cfg_types and item.get("type", "") not in cfg_types:
            continue

        h, m = time_str.split(":")
        event_dt = datetime(date_obj.year, date_obj.month, date_obj.day, int(h), int(m))
        notify_dt = event_dt - timedelta(minutes=NOTIFY_CONFIG.get("advance_minutes", 15))

        if notify_dt > now:
            queue.append((notify_dt, item, event_dt))

    queue.sort(key=lambda x: x[0])
    return queue


def build_markdown_message(item: dict, event_dt: datetime) -> list[str]:
    """构建钉钉 Markdown 通知内容"""
    etype = item.get("type", "")
    country = item.get("country", "")
    importance = item.get("importance", "")
    imp_star = {"高": "★★★", "中": "★★", "低": "★"}.get(importance, "")
    description = item.get("description", "")

    type_emoji = {"财经数据": "📊", "国际假期预告": "🏖️", "财经大事件": "📰"}.get(etype, "📌")

    return [
        f"## {type_emoji} {item['summary']}",
        f"---",
        f"- **类型**: {etype}",
        f"- **国家/区域**: {country}",
        f"- **时间**: {event_dt.strftime('%Y-%m-%d %H:%M')}",
        f"- **重要性**: {imp_star} {importance}",
        f"- **详情**: {description}",
    ]


def daemon():
    """
    守护进程主循环:
    - 启动时立即拉取数据 + 生成 ICS
    - 每天早上 6:00 刷新日历数据
    - 持续监控，为每个事件提前 15 分钟推送钉钉通知
    """
    print("=" * 60)
    print("财经日历守护进程启动")
    print(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("  • 每天早上 6:00 刷新日历 ICS")
    advance = NOTIFY_CONFIG.get("advance_minutes", 15)
    print(f"  • 事件前 {advance} 分钟推送钉钉通知")
    print("=" * 60)

    queue: list[tuple] = []

    while True:
        now = datetime.now()

        # 计算下一次 6:00 刷新时间
        next_6am = datetime(now.year, now.month, now.day, 6, 0)
        if now >= next_6am:
            next_6am += timedelta(days=1)

        # --- 每日刷新 ---
        print(f"\n{'─' * 50}")
        print(f"🔄 [{now.strftime('%H:%M:%S')}] 刷新日历数据...")
        try:
            all_items = fetch_all_data()
            generate_ics_files(all_items)
            git_push(OUTPUT_DIR, '.', datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            queue = build_notification_queue(all_items)
            print(f"📢 通知队列: {len(queue)} 条待发送")
        except Exception as e:
            print(f"⚠️ 刷新失败: {e}", file=sys.stderr)
        print(f"⏰ 下次刷新: {next_6am.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'─' * 50}")

        # --- 通知循环（持续运行直到下次 6:00 刷新） ---
        while True:
            now = datetime.now()

            # 到达 6:00 → 跳出内循环，刷新数据
            if now >= next_6am:
                break

            # 发送所有到期的通知（next_notify <= now 的都要发送）
            while queue and queue[0][0] <= now:
                _, next_item, next_event = queue.pop(0)
                try:
                    content = build_markdown_message(next_item, next_event)
                    print(f"🔔 [{datetime.now().strftime('%H:%M:%S')}] 推送: {next_item['summary']}")
                    ding_message(content)
                except Exception as e:
                    print(f"⚠️ 推送失败: {e}", file=sys.stderr)

            # 清理已过期的残留通知（安全网）
            queue = [(n, i, e) for n, i, e in queue if n > now]

            if not queue:
                # 无待发通知，每 30 秒检查一次
                time.sleep(30)
                continue

            # 休眠：取 (到通知时间, 到6:00, 30秒兜底) 的最小值
            wait_sec = (queue[0][0] - datetime.now()).total_seconds()
            until_6am = (next_6am - datetime.now()).total_seconds()
            sleep_sec = min(wait_sec, until_6am, 30)
            time.sleep(max(sleep_sec, 1))


def sign():
    timestamp = str(round(time.time() * 1000))
    secret_enc = secret.encode('utf-8')
    string_to_sign = '{}\n{}'.format(timestamp, secret)
    string_to_sign_enc = string_to_sign.encode('utf-8')
    hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    url = webhook+'&timestamp='+timestamp+'&sign='+sign
    return url

def ding_message(content):
    url = sign()
    header = {
        "Content-Type": "application/json",
        "Charset": "UTF-8"
    }

    # markdown样式
    message = {
        "msgtype": "markdown",
        "markdown": {
            "title":"📊 财经日历提醒",
            "text": '\n'.join(content)
        },
        "at": {
            "atMobiles": [],
            "atUserIds": [],
            "isAtAll": False
        }
    }

    message_json = json.dumps(message)
    send_message = requests.post(url=url, data=message_json, headers=header)
    print(f"钉钉通知: {send_message.text}")


def git_push(repo_path, file_path, commit_msg):
    try:
        # 切换到仓库目录
        os.chdir(repo_path)

        # Git操作序列
        subprocess.run(['git', 'pull'], check=True)
        subprocess.run(['git', 'add', file_path], check=True)
        subprocess.run(['git', 'commit', '-m', commit_msg], check=True)
        subprocess.run(['git', 'push'], check=True)

        print("推送成功！")
    except subprocess.CalledProcessError as e:
        print(f"Git命令执行失败: {e}")
    except Exception as e:
        print(f"发生错误: {e}")

if __name__ == "__main__":
    daemon()