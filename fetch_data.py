# -*- coding: utf-8 -*-
"""
宏观模型数据更新脚本（增量模式）
红线: 沪深300全收益指数(H00300) —— 中证指数官网
蓝线: 10年期国债持有回报累积指数 —— 中债登10年期国债收益率(久期近似法)反推
输出: data.json (供页面每日读取)

运行方式:
  python fetch_data.py          # 增量更新（已有 data.json 时只拉新数据；无则全量构建）
  python fetch_data.py --full   # 强制全量重建
建议每日收盘后执行一次。
"""
import json
import os
import sys
import time
import datetime
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(BASE_DIR, "data.json")
LOG = os.path.join(BASE_DIR, "fetch.log")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}

BOND_DURATION = 8.5   # 10年期国债修正久期近似值
BASE_YEAR = 2005
MIN_ROWS = 4000       # 全量历史交易日最低阈值


def log(msg):
    line = f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def get(url, params=None, retries=3, timeout=40):
    for i in range(retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            if r.status_code == 200:
                return r
            log(f"HTTP {r.status_code} for {url} (try {i+1})")
        except Exception as e:
            log(f"GET {url} error: {e} (try {i+1})")
        time.sleep(3 * (i + 1))
    return None


# ============ 沪深300全收益指数 H00300 ============
def fetch_h00300_range(sd, ed, retries=6):
    """拉取 [sd, ed] 区间交易日，返回 [{date, close}]；异常/为空时延长间隔重试"""
    for i in range(retries):
        try:
            r = requests.get("https://www.csindex.com.cn/csindex-home/perf/index-perf",
                             params={"indexCode": "H00300", "startDate": sd, "endDate": ed},
                             headers=HEADERS, timeout=60)
        except Exception as e:
            log(f"WARN: {sd}~{ed} 请求失败: {e} (try {i+1})")
            time.sleep(12)
            continue
        if r.status_code != 200:
            log(f"WARN: {sd}~{ed} HTTP {r.status_code} (try {i+1})")
            time.sleep(12)
            continue
        try:
            d = r.json()
        except Exception as e:
            log(f"WARN: {sd}~{ed} JSON解析失败: {e} (try {i+1})")
            time.sleep(12)
            continue
        if d.get("code") == "200" and isinstance(d.get("data"), list):
            rows = []
            for item in d["data"]:
                # 早期年份 open 为 null 但 close 有值，只要求 close
                if item.get("close") is None:
                    continue
                rows.append({"date": item["tradeDate"], "close": float(item["close"])})
            if rows:
                return rows
        log(f"DEBUG: {sd}~{ed} resp code={d.get('code')} msg={d.get('msg')} data_type={type(d.get('data')).__name__} n={len(d.get('data') or [])} body_start={r.text[:200]!r}")
        time.sleep(12)
    return []


def fetch_h00300_full():
    """按年分段全量拉取 2005 至今（每段请求小、稳定；单年失败可重试）"""
    today = datetime.date.today()
    rows = []
    year = BASE_YEAR
    while year <= today.year:
        sd = f"{year}0101"
        ed = f"{year}1231" if year < today.year else today.strftime("%Y%m%d")
        got = fetch_h00300_range(sd, ed)
        if not got:
            raise RuntimeError(f"H00300 {year} 年数据拉取失败")
        rows.extend(got)
        log(f"H00300 {year}: {len(got)} 条")
        year += 1
        time.sleep(3)
    rows = merge_rows([], rows)
    if len(rows) < MIN_ROWS:
        raise RuntimeError(f"H00300 全量数据量异常: {len(rows)}")
    log(f"H00300 全量 OK: {len(rows)} 条, {rows[0]['date']} ~ {rows[-1]['date']}")
    return rows


def merge_rows(old, new):
    """按日期合并去重排序"""
    d = {}
    for r in old:
        d[r["date"]] = r
    for r in new:
        d[r["date"]] = r
    return [d[k] for k in sorted(d)]


# ============ 中债登 10年期国债收益率 ============
def fetch_cn10y_range(sd, ed):
    """拉取 [sd, ed] 区间 10年期国债收益率（区间<=1年），返回 {date: yield(小数)}"""
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        r0 = s.get("https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/showHistory",
                   params={"locale": "zh_CN"}, timeout=40)
        if r0.status_code != 200:
            raise RuntimeError("中债登会话建立失败")
    except Exception as e:
        raise RuntimeError(f"中债登会话建立失败: {e}")
    base = "https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/historyQuery"
    params = {"startDate": sd, "endDate": ed, "gjqx": "10", "qxId": "ycqx", "locale": "zh_CN"}
    for i in range(4):
        try:
            r = s.get(base, params=params, timeout=40)
            if r.status_code == 200:
                got = parse_yield_table(r.text)
                if got:
                    return got
                log(f"WARN: {sd}~{ed} 收益率返回空 (try {i+1})")
        except Exception as e:
            log(f"WARN: {sd}~{ed} 收益率请求失败: {e} (try {i+1})")
        time.sleep(5)
    return {}


def fetch_cn10y_incremental(last_date=None):
    """拉取收益率。last_date 为 None 时按年拉全量（2005至今）"""
    today = datetime.date.today()
    result = {}
    if last_date is None:
        year = BASE_YEAR
        while year <= today.year:
            sd = f"{year}-01-01"
            ed = f"{year}-12-31" if year < today.year else today.strftime("%Y-%m-%d")
            got = fetch_cn10y_range(sd, ed)
            result.update(got)
            log(f"CN10Y {year}: {len(got)} 条")
            year += 1
            time.sleep(1.5)
    else:
        sd = (datetime.date.fromisoformat(last_date) - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        ed = today.strftime("%Y-%m-%d")
        got = fetch_cn10y_range(sd, ed)
        result.update(got)
        log(f"CN10Y 增量 {sd}~{ed}: {len(got)} 条")
    if not result:
        raise RuntimeError("CN10Y 拉取结果为空")
    return result


def parse_yield_table(html):
    import re
    out = {}
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
    for row in rows:
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
        if len(cells) < 9 or cells[0] != "中债国债收益率曲线":
            continue
        if not re.match(r"\d{4}-\d{2}-\d{2}$", cells[1]):
            continue
        val = cells[8].strip()
        if val and val != "--":
            try:
                out[cells[1]] = float(val) / 100.0  # 转小数
            except ValueError:
                continue
    return out


# ============ 序列构建 ============
def build_full(dates, red, y10_map, first_close):
    """由完整日期/红线/收益率映射构建 blue 累积曲线"""
    n = len(dates)
    sorted_y = sorted(y10_map.items())
    yi = 0
    prev_y = sorted_y[0][1] if sorted_y else None
    ys = []
    for d in dates:
        while yi < len(sorted_y) and sorted_y[yi][0] <= d:
            prev_y = sorted_y[yi][1]
            yi += 1
        ys.append(prev_y if prev_y is not None else sorted_y[0][1])

    blue = [0.0] * n
    blue[0] = 1000.0
    for i in range(1, n):
        carry = ys[i - 1] / 365.0
        price_ret = -BOND_DURATION * (ys[i] - ys[i - 1])
        blue[i] = round(blue[i - 1] * (1.0 + carry + price_ret), 2)
    return blue, [round(y * 100, 4) for y in ys]


def detect_events(dates, red, blue):
    """标注各时期风险溢价（回报/成本比）阶段性低点，日期格式与数据一致(YYYYMMDD)"""
    events = []
    windows = [
        ("20050101", "20051231", "风险溢价全年最低"),
        ("20080901", "20090228", "风险溢价跌至谷底"),
        ("20140101", "20141231", "风险溢价区间低位"),
        ("20180901", "20190331", "风险溢价历史低位"),
        ("20240801", "20241231", "风险溢价历史低位"),
    ]
    for ws, we, text in windows:
        best_i, best_ratio = None, None
        for i, d in enumerate(dates):
            if ws <= d <= we:
                ratio = red[i] / blue[i] if blue[i] else 1e9
                if best_ratio is None or ratio < best_ratio:
                    best_ratio = ratio
                    best_i = i
        if best_i is not None:
            events.append({"date": dates[best_i], "text": text, "ratio": round(best_ratio, 3)})
    return events


# ============ 主流程 ============
def load_existing():
    if not os.path.exists(OUTPUT):
        return None
    try:
        with open(OUTPUT, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"WARN: 现有 data.json 读取失败: {e}，将全量重建")
        return None


def main():
    full = "--full" in sys.argv
    log("========== 开始更新" + ("（全量重建）" if full else "（增量）") + " ==========")
    try:
        old = None if full else load_existing()
        today = datetime.date.today().strftime("%Y%m%d")

        if old and old.get("series") and old["series"].get("dates"):
            # ---------- 增量路径 ----------
            last_date = old["series"]["dates"][-1]
            first_close = old.get("first_close")
            if not first_close:
                raise RuntimeError("data.json 缺少 first_close，请用 --full 重建")

            # 拉新增交易日（含 last_date 以便去重）
            new_rows = fetch_h00300_range(last_date, today)
            if not new_rows:
                raise RuntimeError("增量区间未拉到新数据（接口异常）")
            # 重建 rows（含历史 red 值，通过 first_close 换算）
            old_dates = old["series"]["dates"]
            old_red = old["series"]["red"]
            hist = [{"date": d, "close": r / 1000.0 * first_close} for d, r in zip(old_dates, old_red)]
            rows = merge_rows(hist, new_rows)

            # 收益率：old 完整映射 + 增量
            y10_map = {}
            if "y10" in old["series"]:
                y10_map = {d: v / 100.0 for d, v in zip(old_dates, old["series"]["y10"])}
            inc = fetch_cn10y_incremental(last_date)
            y10_map = {**y10_map, **inc}
        else:
            # ---------- 全量路径 ----------
            rows = fetch_h00300_full()
            first_close = rows[0]["close"]
            y10_map = fetch_cn10y_incremental(None)

        # 统一构建
        dates = [r["date"] for r in rows]
        red = [round(r["close"] / first_close * 1000.0, 2) for r in rows]
        blue, y10_pct = build_full(dates, red, y10_map, first_close)
        events = detect_events(dates, red, blue)

        data = {
            "updated": dates[-1],
            "base": 1000,
            "first_close": first_close,
            "labels": {
                "red": "沪深300全收益指数",
                "blue": "10年期国债持有回报",
            },
            "series": {
                "dates": dates,
                "red": red,
                "blue": blue,
                "y10": y10_pct,
            },
            "events": events,
        }
        tmp = OUTPUT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, OUTPUT)
        log(f"输出 {OUTPUT}: {len(dates)} 条, {len(events)} 个标注点, 更新至 {dates[-1]}")
        log("========== 更新完成 ==========")
    except Exception as e:
        log(f"!! 更新失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
