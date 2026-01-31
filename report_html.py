"""祝日・フォーマット・HTML/SVG生成。"""

import os
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import quote
from xml.sax.saxutils import escape as _xml_escape

from report_data import (
    AvgRow,
    DetailRow,
    group_by_month,
    mean_int,
    median_int,
    quantile,
)

DIR_HTML_HISTORY = Path("html")
FMT_GENERATED_AT = "%Y-%m-%d %H:%M"
WEEKDAYS_JA = ["月", "火", "水", "木", "金", "土", "日"]


# HTML用にエスケープする
#
# 引数:
#   s (object): エスケープ対象
#
# 戻り値:
#   str: エスケープ済み文字列
def escape(s: object) -> str:
    return _xml_escape(str(s), {'"': "&quot;", "'": "&#x27;"})


# 日付を「YYYY年MM月DD日」形式で返す
#
# 引数:
#   d (date): 対象日付
#
# 戻り値:
#   str: フォーマット済み文字列
def fmt_date_jp(d: date) -> str:
    return d.strftime("%Y年%m月%d日")


# 日付を M/D 形式で返す
#
# 引数:
#   d (date): 対象日付
#
# 戻り値:
#   str: フォーマット済み文字列
def fmt_md(d: date) -> str:
    return f"{d.month}/{d.day}"


# 年月を省いた日付＋曜日（MM月DD日（曜））を返す
#
# 引数:
#   d (date): 対象日付
#
# 戻り値:
#   str: フォーマット済み文字列
def fmt_date_jp_with_weekday_no_year(d: date) -> str:
    w = WEEKDAYS_JA[d.weekday()]
    return f"{d.month}月{d.day}日（{w}）"


# 日付＋曜日を HTML エスケープ済みで返す（色なし）
#
# 引数:
#   d (date): 対象日付
#
# 戻り値:
#   str: フォーマット済み文字列
def fmt_date_jp_with_weekday_html_no_color(d: date) -> str:
    w = WEEKDAYS_JA[d.weekday()]
    return f"{escape(fmt_date_jp(d))}（{escape(w)}）"


# 日付＋曜日を HTML 形式で返す（祝日/土日で span にクラス付与）
#
# 引数:
#   d (date): 対象日付
#   holidays (Set[date]): 祝日セット
#
# 戻り値:
#   str: HTML フォーマット済み文字列
def fmt_date_jp_with_weekday_html(d: date, holidays: Set[date]) -> str:
    w = WEEKDAYS_JA[d.weekday()]
    w_cls = weekday_class(d, holidays)
    return f"{escape(fmt_date_jp(d))}<span class=\"wday {w_cls}\">（{escape(w)}）</span>"


# 価格を「¥x,xxx」形式で返す。None は「-」
#
# 引数:
#   v (Optional[int]): 価格（円）
#
# 戻り値:
#   str: フォーマット済み文字列
def fmt_yen(v: Optional[int]) -> str:
    if v is None:
        return "-"
    return f"¥{v:,}"


# 祝日/土/日/平日の CSS クラス名を返す
#
# 引数:
#   d (date): 対象日付
#   holidays (Set[date]): 祝日セット
#
# 戻り値:
#   str: "holiday", "sat", "sun", "weekday" のいずれか
def weekday_class(d: date, holidays: Set[date]) -> str:
    if d in holidays:
        return "holiday"
    if d.weekday() == 5:
        return "sat"
    if d.weekday() == 6:
        return "sun"
    return "weekday"


# 曜日に応じた色コードを返す
#
# 引数:
#   d (date): 対象日付
#   holidays (Set[date]): 祝日セット
#
# 戻り値:
#   str: 色コード（#16a34a 等）
def weekday_color(d: date, holidays: Set[date]) -> str:
    cls = weekday_class(d, holidays)
    if cls == "holiday":
        return "#16a34a"
    if cls == "sat":
        return "#2563eb"
    if cls == "sun":
        return "#dc2626"
    return "#666"


# 月の第 n 曜日を返す（weekday: 0=月..6=日）
#
# 引数:
#   year (int): 年
#   month (int): 月
#   weekday (int): 曜日（0=月..6=日）
#   n (int): 第 n 曜日
#
# 戻り値:
#   date: 該当日
def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    d = d.replace(day=1 + offset)
    return d.replace(day=d.day + 7 * (n - 1))


# 春分の日を返す
#
# 引数:
#   year (int): 年
#
# 戻り値:
#   date: 春分の日
def _vernal_equinox_day(year: int) -> date:
    day = int(20.8431 + 0.242194 * (year - 1980) - int((year - 1980) / 4))
    return date(year, 3, day)


# 秋分の日を返す
#
# 引数:
#   year (int): 年
#
# 戻り値:
#   date: 秋分の日
def _autumn_equinox_day(year: int) -> date:
    day = int(23.2488 + 0.242194 * (year - 1980) - int((year - 1980) / 4))
    return date(year, 9, day)


# 指定年の祝日セットを返す（振替休日・国民の休日を含む）
#
# 引数:
#   year (int): 年
#
# 戻り値:
#   Set[date]: 祝日セット
def _japan_holidays_for_year(year: int) -> Set[date]:
    # 固定祝日＋第n月曜（成人の日等）＋春分・秋分
    hols: Set[date] = {
        date(year, 1, 1), date(year, 2, 11), date(year, 2, 23),
        date(year, 4, 29), date(year, 5, 3), date(year, 5, 4), date(year, 5, 5),
        date(year, 8, 11), date(year, 11, 3), date(year, 11, 23),
        _nth_weekday_of_month(year, 1, 0, 2), _nth_weekday_of_month(year, 7, 0, 3),
        _nth_weekday_of_month(year, 9, 0, 3), _nth_weekday_of_month(year, 10, 0, 2),
        _vernal_equinox_day(year), _autumn_equinox_day(year),
    }
    # 日曜と重なる祝日 → 翌平日を振替休日として追加
    added_substitute: Set[date] = set()
    for h in sorted(hols):
        if h.weekday() == 6:
            d = h
            while True:
                d = d.fromordinal(d.toordinal() + 1)
                if d not in hols and d not in added_substitute:
                    added_substitute.add(d)
                    break
    hols |= added_substitute
    # 国民の休日（祝日にはさまれた平日）を追加
    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)
    d = year_start
    added_citizen: Set[date] = set()
    while d <= year_end:
        if d not in hols and d.weekday() != 6:
            prev_day = d.fromordinal(d.toordinal() - 1)
            next_day = d.fromordinal(d.toordinal() + 1)
            if prev_day in hols and next_day in hols:
                added_citizen.add(d)
        d = d.fromordinal(d.toordinal() + 1)
    hols |= added_citizen
    return hols


# 期間内の祝日セットを返す
#
# 引数:
#   start_d (date): 開始日
#   end_d (date): 終了日
#
# 戻り値:
#   Set[date]: 期間内の祝日セット
def load_jp_holidays_for_range(start_d: date, end_d: date) -> Set[date]:
    out: Set[date] = set()
    for y in range(start_d.year, end_d.year + 1):
        out |= _japan_holidays_for_year(y)
    # 指定期間内の祝日のみ返す
    return {d for d in out if start_d <= d <= end_d}


# 環境変数から検索条件を読み、Airbnb 検索 URL を生成
#
# 引数:
#   checkin (date): チェックイン日
#   checkout (date): チェックアウト日
#
# 戻り値:
#   str: 検索 URL
def build_search_url(checkin: date, checkout: date) -> str:
    from datetime import timedelta
    # 環境変数から検索条件を取得（未設定時はデフォルト）
    destination = os.getenv("AIRBNB_DESTINATION", "大阪市 此花区")
    adults = int(os.getenv("AIRBNB_ADULTS", "4"))
    children = int(os.getenv("AIRBNB_CHILDREN", "0"))
    infants = int(os.getenv("AIRBNB_INFANTS", "0"))
    pets = int(os.getenv("AIRBNB_PETS", "0"))
    price_min = os.getenv("AIRBNB_PRICE_MIN")
    price_max = os.getenv("AIRBNB_PRICE_MAX")
    params = {
        "checkin": checkin.isoformat(),
        "checkout": checkout.isoformat(),
        "adults": str(adults),
        "children": str(children),
        "infants": str(infants),
        "pets": str(pets),
    }
    if price_min:
        params["price_min"] = price_min
    if price_max:
        params["price_max"] = price_max
    query = "&".join([f"{k}={quote(v)}" for k, v in params.items()])
    return f"https://www.airbnb.jp/s/{quote(destination)}/homes?{query}"


# AvgRow の URL があればそのまま、なければ動的生成
#
# 引数:
#   r (AvgRow): 平均行
#
# 戻り値:
#   str: 検索 URL
def get_or_build_search_url(r: AvgRow) -> str:
    from datetime import timedelta
    # CSV の URL が有効ならそのまま使用
    if r.url and "checkin=" in r.url and "adults=" in r.url:
        return r.url
    checkout = r.checkin + timedelta(days=1)
    return build_search_url(r.checkin, checkout)


# 既存 HTML を mtime で日付付き名にリネームし html/ へ退避
#
# 引数:
#   out_path (Path): 出力先 HTML パス
#
# 戻り値:
#   None
def backup_existing_html(out_path: Path) -> None:
    if not out_path.exists():
        return
    try:
        ts = out_path.stat().st_mtime
    except Exception:
        return
    dt = datetime.fromtimestamp(ts)
    suffix = dt.strftime("%Y%m%d_%H%M%S")
    stem = "index" if out_path.name.lower() == "index.html" else out_path.stem
    DIR_HTML_HISTORY.mkdir(parents=True, exist_ok=True)
    candidate = DIR_HTML_HISTORY / f"{stem}{suffix}{out_path.suffix}"
    i = 2
    # 同名ファイルがあれば _2, _3 と連番を付与
    while candidate.exists():
        candidate = DIR_HTML_HISTORY / f"{stem}{suffix}_{i}{out_path.suffix}"
        i += 1
    out_path.rename(candidate)


# 1ヶ月単位の概要表 HTML を生成
#
# 引数:
#   avg_rows (List[AvgRow]): 平均行リスト
#   detail_stats_by_day (Dict): 日別の詳細統計
#
# 戻り値:
#   str: HTML 文字列
def html_monthly_summary(
    avg_rows: List[AvgRow],
    detail_stats_by_day: Dict[date, Dict[str, Optional[int]]],
) -> str:
    by_month = group_by_month(avg_rows)
    out = []
    # テーブルヘッダー
    out.append("<table class='small' style='font-size:11px; width:100%; table-layout:fixed;'>")
    out.append("<thead><tr><th style='font-size:11px; white-space:nowrap; width:40px; font-weight:bold; text-align:center;'>月</th><th style='font-size:11px; white-space:nowrap; width:35px; text-align:center;'>日数</th><th style='font-size:11px; white-space:nowrap; width:55px; text-align:center;'>平均</th><th style='font-size:11px; white-space:nowrap; width:55px; text-align:center;'>中央値</th><th style='font-size:11px; white-space:nowrap; width:70px; text-align:center;'>下位25%点</th><th style='font-size:11px; white-space:nowrap; width:70px; text-align:center;'>上位25%点</th><th style='font-size:11px; white-space:nowrap; width:50px; text-align:center;'>最小</th><th style='font-size:11px; white-space:nowrap; width:50px; text-align:center;'>最大</th></tr></thead>")
    out.append("<tbody>")
    for (year, month), month_rows in by_month.items():
        # 月内の日別統計を集約
        month_medians = []
        month_p25s = []
        month_p75s = []
        month_mins = []
        month_maxs = []
        month_avgs = [r.avg_price_yen for r in month_rows if r.avg_price_yen is not None]
        month_avgs_int = [v for v in month_avgs if isinstance(v, int)]
        for r in month_rows:
            s = detail_stats_by_day.get(r.checkin, {})
            if s.get("median") is not None:
                month_medians.append(s["median"])
            if s.get("p25") is not None:
                month_p25s.append(s["p25"])
            if s.get("p75") is not None:
                month_p75s.append(s["p75"])
            if s.get("min") is not None:
                month_mins.append(s["min"])
            if s.get("max") is not None:
                month_maxs.append(s["max"])
        month_mean = mean_int(month_avgs_int)
        month_median_val = mean_int(month_medians)
        month_p25_val = mean_int(month_p25s)
        month_p75_val = mean_int(month_p75s)
        month_min_val = min(month_mins) if month_mins else None
        month_max_val = max(month_maxs) if month_maxs else None
        month_label = f"{month}月"
        out.append("<tr>")
        out.append(f"<td style='font-size:11px; white-space:nowrap;'>{month_label}</td>")
        out.append(f"<td style='font-size:11px; white-space:nowrap; text-align:right;'>{len(month_rows)}日</td>")
        out.append(f"<td style='font-size:11px; white-space:nowrap; text-align:right;'>{escape(fmt_yen(month_mean))}</td>")
        out.append(f"<td style='font-size:11px; white-space:nowrap; text-align:right;'>{escape(fmt_yen(month_median_val))}</td>")
        out.append(f"<td style='font-size:11px; white-space:nowrap; text-align:right;'>{escape(fmt_yen(month_p25_val))}</td>")
        out.append(f"<td style='font-size:11px; white-space:nowrap; text-align:right;'>{escape(fmt_yen(month_p75_val))}</td>")
        out.append(f"<td style='font-size:11px; white-space:nowrap; text-align:right;'>{escape(fmt_yen(month_min_val))}</td>")
        out.append(f"<td style='font-size:11px; white-space:nowrap; text-align:right;'>{escape(fmt_yen(month_max_val))}</td>")
        out.append("</tr>")
    out.append("</tbody></table>")
    return "\n".join(out)


# 日別価格の折れ線グラフ SVG を生成
#
# 引数:
#   avg_rows (List[AvgRow]): 平均行リスト
#   detail_stats_by_day (Dict): 日別詳細統計
#   holidays (Set[date]): 祝日セット
#   base_width (int): グラフ幅
#   height (int): グラフ高さ
#
# 戻り値:
#   str: SVG HTML
def svg_line_chart(
    avg_rows: List[AvgRow],
    detail_stats_by_day: Dict[date, Dict[str, Optional[int]]],
    holidays: Set[date],
    base_width: int = 1100,
    height: int = 480,
) -> str:
    # Y軸の範囲計算用に全価格を収集
    xs: List[int] = []
    for r in avg_rows:
        if r.avg_price_yen is not None:
            xs.append(r.avg_price_yen)
        s = detail_stats_by_day.get(r.checkin)
        if s:
            for k in ("median", "p25", "p75"):
                v = s.get(k)
                if isinstance(v, int):
                    xs.append(v)
    if not xs:
        return "<p>グラフ: データがありません</p>"
    # 日数に応じてグラフ幅を拡張
    days_count = len(avg_rows)
    if days_count > 30:
        chart_width = int(base_width * (days_count / 30))
    else:
        chart_width = base_width
    # Y軸 min/max を p25/p75 の範囲から決定
    p25_values = []
    p75_values = []
    for r in avg_rows:
        s = detail_stats_by_day.get(r.checkin)
        if s:
            if s.get("p25") is not None:
                p25_values.append(s["p25"])
            if s.get("p75") is not None:
                p75_values.append(s["p75"])
    if p25_values and p75_values:
        y_min = min(p25_values)
        y_max = max(p75_values)
    elif xs:
        xs_sorted = sorted(xs)
        y_min = quantile(xs_sorted, 0.25) or min(xs_sorted)
        y_max = quantile(xs_sorted, 0.75) or max(xs_sorted)
    else:
        y_min = 0
        y_max = 20000
    y0, y1 = y_min, y_max
    margin_l, margin_r, margin_t, margin_b = 45, 30, 20, 50
    chart_margin_l, chart_margin_r = 5, 20
    chart_w = chart_width - chart_margin_l - chart_margin_r
    h = height - margin_t - margin_b

    def x_at(i: int) -> float:
        if len(avg_rows) <= 1:
            return chart_margin_l + chart_w / 2
        return chart_margin_l + (chart_w * i) / (len(avg_rows) - 1)

    def y_at(v: int) -> float:
        if y1 == y0:
            return margin_t + h / 2
        return margin_t + (h * (y1 - v)) / (y1 - y0)

    avg_points: List[Tuple[float, float, AvgRow]] = []
    for i, r in enumerate(avg_rows):
        if not isinstance(r.avg_price_yen, int):
            continue
        avg_points.append((x_at(i), y_at(r.avg_price_yen), r))

    def series_value(r: AvgRow, key: str) -> Optional[int]:
        if key == "avg":
            return r.avg_price_yen
        s = detail_stats_by_day.get(r.checkin)
        if not s:
            return None
        v = s.get(key)
        return v if isinstance(v, int) else None

    def polyline_segments(key: str) -> List[str]:
        segs, cur = [], []
        for i, r in enumerate(avg_rows):
            v = series_value(r, key)
            if v is None:
                if len(cur) >= 2:
                    segs.append(" ".join(cur))
                cur = []
                continue
            cur.append(f"{x_at(i):.2f},{y_at(v):.2f}")
        if len(cur) >= 2:
            segs.append(" ".join(cur))
        return segs

    ticks = 8
    y_ticks = []
    for t in range(ticks + 1):
        v = round(y0 + (y1 - y0) * (t / ticks))
        y_ticks.append((v, y_at(v)))
    x_labels = [(i, fmt_md(avg_rows[i].checkin)) for i in range(len(avg_rows))]

    y_axis_parts = [f'<svg viewBox="0 0 {margin_l} {height}" width="{margin_l}" height="{height}" style="flex-shrink: 0;">', '<rect x="0" y="0" width="100%" height="100%" fill="white"/>']
    for v, y in y_ticks:
        y_axis_parts.append(f'<text x="{margin_l - 2}" y="{y + 4:.2f}" text-anchor="end" font-size="11" fill="#666">{escape(fmt_yen(v))}</text>')
    y_axis_parts.append(f'<line x1="{margin_l - 1}" y1="{margin_t}" x2="{margin_l - 1}" y2="{height - margin_b}" stroke="#999"/>')
    y_axis_parts.append("</svg>")

    chart_parts = [f'<svg viewBox="0 0 {chart_width} {height}" width="{chart_width}" height="{height}" role="img" aria-label="日別価格グラフ">', '<rect x="0" y="0" width="100%" height="100%" fill="white" pointer-events="none"/>']
    for v, y in y_ticks:
        chart_parts.append(f'<line x1="{chart_margin_l}" y1="{y:.2f}" x2="{chart_width - chart_margin_r}" y2="{y:.2f}" stroke="#eee" pointer-events="none"/>')
    chart_parts.append(f'<line x1="{chart_margin_l}" y1="{height - margin_b}" x2="{chart_width - chart_margin_r}" y2="{height - margin_b}" stroke="#999" pointer-events="none"/>')
    for i, label in x_labels:
        x = x_at(i)
        y = height - 12
        chart_parts.append(f'<line x1="{x:.2f}" y1="{margin_t}" x2="{x:.2f}" y2="{height - margin_b}" stroke="#f3f4f6" pointer-events="none"/>')
        chart_parts.append(f'<text x="{x:.2f}" y="{y}" text-anchor="middle" font-size="9" fill="{weekday_color(avg_rows[i].checkin, holidays)}" pointer-events="none">{escape(label)}</text>')

    series = [("avg", "#2563eb", 3.5, None), ("median", "#8b5cf6", 2.8, None), ("p25", "#10b981", 2.2, "4,2"), ("p75", "#f59e0b", 2.2, "4,2")]
    for key, color, sw, dash_pattern in series:
        for seg in polyline_segments(key):
            dash_attr = f' stroke-dasharray="{dash_pattern}"' if dash_pattern else ""
            chart_parts.append(f'<polyline fill="none" stroke="{color}" stroke-width="{sw}"{dash_attr} points="{seg}" pointer-events="none"/>')

    for x, y, r in avg_points:
        s = detail_stats_by_day.get(r.checkin, {})
        date_str = fmt_date_jp_with_weekday_no_year(r.checkin)
        chart_parts.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3.6" fill="#2563eb" stroke="white" stroke-width="1" '
            f'style="cursor:pointer; pointer-events:auto;" '
            f'data-tooltip-date="{escape(date_str)}" '
            f'data-tooltip-avg="{escape(fmt_yen(r.avg_price_yen))}" '
            f'data-tooltip-median="{escape(fmt_yen(s.get("median")))}" '
            f'data-tooltip-p25="{escape(fmt_yen(s.get("p25")))}" '
            f'data-tooltip-p75="{escape(fmt_yen(s.get("p75")))}" '
            f'data-tooltip-min="{escape(fmt_yen(s.get("min")))}" '
            f'data-tooltip-max="{escape(fmt_yen(s.get("max")))}" '
            f'data-tooltip-count="{escape(str(r.count))}" '
            f'data-tooltip-x="{x:.2f}" '
            f'data-tooltip-y="{y:.2f}"></circle>'
        )
    chart_parts.append("</svg>")
    return f"""<div style="display: flex; align-items: stretch;">
        {'\n'.join(y_axis_parts)}
        <div style="overflow-x: auto; -webkit-overflow-scrolling: touch; flex: 1;">
            {'\n'.join(chart_parts)}
        </div>
    </div>"""


# モーダル用：日付（ISO）ごとに明細 JSON を生成
#
# 引数:
#   details_rows (List[DetailRow]): 明細行リスト
#   holidays (Set[date]): 祝日セット
#
# 戻り値:
#   Dict: ISO日付 -> {dateLabelHtml, wcls, rows}
def build_detail_payload_by_day(details_rows: List[DetailRow], holidays: Set[date]) -> Dict[str, Dict[str, object]]:
    # 日付ごとに明細をグループ化
    by_day: Dict[date, List[DetailRow]] = defaultdict(list)
    for r in details_rows:
        by_day[r.checkin].append(r)
    payload = {}
    for d in sorted(by_day.keys()):
        rows = sorted(by_day[d], key=lambda x: x.price_yen)
        payload[d.isoformat()] = {
            "dateLabelHtml": fmt_date_jp_with_weekday_html(d, holidays),
            "wcls": weekday_class(d, holidays),
            "rows": [
                {
                    "price": r.price_yen,
                    "url": r.listing_url,
                    "label": r.raw_label,
                    "title": r.title,
                    "guests": r.guests,
                    "bedrooms": r.bedrooms,
                    "beds": r.beds,
                    "reviews_count": r.reviews_count,
                    "rating": r.rating,
                    "subtitle": r.subtitle,
                }
                for r in rows
            ],
        }
    return payload


# 日別平均表の HTML を生成
#
# 引数:
#   rows (List[AvgRow]): 平均行リスト
#   detail_stats_by_day (Dict): 日別詳細統計
#   holidays (Set[date]): 祝日セット
#
# 戻り値:
#   str: HTML 文字列
def html_table_avg(
    rows: List[AvgRow],
    detail_stats_by_day: Dict[date, Dict[str, Optional[int]]],
    holidays: Set[date],
) -> str:
    out = []
    out.append('<table class="avg-table">')
    out.append("<thead><tr>")
    out.append("<th style='text-align:center;'></th><th style='text-align:center;'>チェックイン日</th><th style='text-align:center;'>平均</th><th style='text-align:center;'>中央値</th><th style='text-align:center;'>下位25%点</th><th style='text-align:center;'>上位25%点</th><th style='text-align:center;'>件数</th><th style='text-align:center;'>最小</th><th style='text-align:center;'>最大</th><th style='text-align:center;'>検索URL</th>")
    out.append("</tr></thead><tbody>")
    for r in rows:
        s = detail_stats_by_day.get(r.checkin, {})
        median_v = s.get("median")
        p25_v = s.get("p25")
        p75_v = s.get("p75")
        iso = r.checkin.isoformat()
        has_details = r.checkin in detail_stats_by_day
        out.append("<tr>")
        if has_details:
            out.append(f'<td><button type="button" class="jump-btn" onclick="openDayModal(\'{iso}\')" title="明細を開く">▶</button></td>')
        else:
            out.append('<td><button type="button" class="jump-btn" disabled title="明細なし">▶</button></td>')
        out.append(f"<td>{fmt_date_jp_with_weekday_html(r.checkin, holidays)}</td>")
        out.append(f"<td style='text-align:right;'>{escape(fmt_yen(r.avg_price_yen))}</td>")
        out.append(f"<td style='text-align:right;'>{escape(fmt_yen(median_v))}</td>")
        out.append(f"<td style='text-align:right;'>{escape(fmt_yen(p25_v))}</td>")
        out.append(f"<td style='text-align:right;'>{escape(fmt_yen(p75_v))}</td>")
        out.append(f"<td style='text-align:right;'>{r.count}</td>")
        out.append(f"<td style='text-align:right;'>{escape(fmt_yen(r.min_price_yen))}</td>")
        out.append(f"<td style='text-align:right;'>{escape(fmt_yen(r.max_price_yen))}</td>")
        search_url = get_or_build_search_url(r)
        out.append(f'<td style="text-align:center;"><a class="link-btn" href="{escape(search_url)}" target="_blank" rel="noreferrer">詳細</a></td>')
        out.append("</tr>")
    out.append("</tbody></table>")
    return "\n".join(out)
