"""
Airbnb CSV から HTML レポートを生成するスクリプト。

【目的】
  scrape.py が出力した data/ 内の CSV を読み、1 つの HTML ファイル（index.html）にまとめる。
  gitpush.py がこの index.html を docs/ に配置して GitHub Pages で公開する前提。

【前提条件】
  - 平均CSV（デフォルト: data/konohana_daily_avg.csv）と明細CSV（デフォルト: data/konohana_daily_details.csv）が存在すること（scrape.py 実行後）。
  - CSV は UTF-8 または BOM 付き UTF-8。1 行目はヘッダー。checkin は ISO 日付（YYYY-MM-DD）。

【入力データの意味】
  - 平均CSV: 列 checkin, avg_price_yen, count, min_price_yen, max_price_yen, url。日別の平均価格・件数・最小最大・検索URL。
  - 明細CSV: 列 checkin, price_yen, listing_url, raw_label, title, guests, bedrooms, beds, reviews_count, rating, subtitle。物件ごとの明細。同日は複数行あり得る。

【例外・エラー時の考え方】
  - CSV が存在しない: そのCSV由来のデータは空リストのまま処理を続行。HTML 上は「データなし」や「明細を開けません」と表示。
  - 日付パース失敗: その行は読み飛ばす（parse_date が None を返す）。不正行があっても他行は処理する。
  - 数値パース失敗: parse_int/parse_float は None を返す。表示では「-」などで表現。
  - 祝日計算: 簡易実装のため振替休日・国民の休日は近似。必要なら外部ライブラリに差し替える。

【なぜこの実装になっているか】
  - 標準の html モジュールを使わず xml.sax.saxutils.escape を使っている理由: 本ファイル名が report.py であり、html という名前のモジュールと衝突しうるため。
  - 生成日時を HTML に埋め込んでいる理由: 同じ日でも実行ごとに内容が変わり、gitpush 側で「変更あり」と判定されやすくするため。
  - 明細は一覧ではなくモーダルで表示している理由: 件数が多く一覧だと長くなるため。日別の平均表の「▶」からその日だけ開く形にしている。
"""

import argparse
import csv
import json
import os
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import DefaultDict, Dict, List, Optional, Set, Tuple
from urllib.parse import quote
from xml.sax.saxutils import escape as _xml_escape

# =============================================================================
# 定数定義（パス・デフォルト値・フォーマットはここで変更する）
# =============================================================================
# 前提: scrape.py が data/ に出力するCSV名と一致させる。別名で保存している場合は --avg / --details で指定する。

DEFAULT_AVG_PATH = "data/konohana_daily_avg.csv"   # 日別平均CSV（checkin, avg_price_yen, count, min, max, url）
DEFAULT_DETAILS_PATH = "data/konohana_daily_details.csv"  # 日別明細CSV（物件ごとの価格・URL・タイトル等）
DEFAULT_OUT_PATH = "docs/index.html"   # 出力先（docs フォルダ直下）。既存があればそのファイルの更新日時で html/ に履歴として退避してから上書き
DIR_HTML_HISTORY = Path("html")   # 古い index.html の退避先フォルダ
DEFAULT_TITLE = "Airbnb価格レポート"
DEFAULT_DETAILS_LIMIT = 50   # モーダル内の各日表示件数。0で全件。多すぎるとHTMLが重くなるため上限を設けている
FMT_GENERATED_AT = "%Y-%m-%d %H:%M"   # HTMLに埋め込む生成日時。実行ごとに変わり、git で「変更あり」と判定されやすくする

WEEKDAYS_JA = ["月", "火", "水", "木", "金", "土", "日"]   # 曜日表示用。index 0=月, 6=日
JP_HOLIDAYS: Set[date] = set()  # 実行時にデータ期間に合わせて祝日を読み込む（祝日は緑色で表示）


# =============================================================================
# データ型・フォーマット（日付・曜日・エスケープ）
# =============================================================================
# 入力: CSV の checkin は ISO 日付（YYYY-MM-DD）。不正な行は parse_date が None を返すため読み飛ばす。

def fmt_date_jp(d: date) -> str:
    # 例: 2026年01月23日
    return d.strftime("%Y年%m月%d日")


def fmt_date_jp_with_weekday(d: date) -> str:
    # 例: 2026年01月23日（金）
    return f"{fmt_date_jp(d)}（{WEEKDAYS_JA[d.weekday()]}）"

def fmt_date_jp_with_weekday_html(d: date) -> str:
    # HTML用: 日付は通常色、（曜）だけ色を付ける（祝日=緑 / 土=青 / 日=赤）
    w = WEEKDAYS_JA[d.weekday()]
    w_cls = weekday_class(d)
    return f"{escape(fmt_date_jp(d))}<span class=\"wday {w_cls}\">（{escape(w)}）</span>"

def fmt_date_jp_with_weekday_html_no_color(d: date) -> str:
    # HTML用: 日付と曜日を表示（色は付けない、期間表示用）
    w = WEEKDAYS_JA[d.weekday()]
    return f"{escape(fmt_date_jp(d))}（{escape(w)}）"


def fmt_day_with_weekday_html(d: date) -> str:
    # HTML用: 日と曜日だけを表示（年月は除く）
    w = WEEKDAYS_JA[d.weekday()]
    w_cls = weekday_class(d)
    return f"{d.day}日<span class=\"wday {w_cls}\">（{escape(w)}）</span>"

def fmt_date_jp_with_weekday_no_year(d: date) -> str:
    # ツールチップ用: 年を除いた日付表示 例: 1月23日（金）
    w = WEEKDAYS_JA[d.weekday()]
    return f"{d.month}月{d.day}日（{w}）"

def escape(s: object) -> str:
    # `html.py` というファイル名が標準ライブラリ `html` と衝突するため、
    # ここでは xml.sax の escape を使って HTML エスケープします。
    return _xml_escape(str(s), {'"': "&quot;", "'": "&#x27;"})

def fmt_md(d: date) -> str:
    # グラフ用の短い日付表示: m/d
    return f"{d.month}/{d.day}"


# 平均CSVの1行を表す。scrape.py の日別集計結果（checkin 日ごとに1行）。
@dataclass(frozen=True)
class AvgRow:
    checkin: date              # チェックイン日（1泊のため checkout は checkin+1 日）
    avg_price_yen: Optional[int]  # その日の平均価格（円）。フィルタ後物件が0件の日は None になり得る
    count: int                 # 物件数（平均・最小・最大の根拠）
    min_price_yen: Optional[int]
    max_price_yen: Optional[int]
    url: str                   # 検索URL。空の場合は build_search_url で動的生成する


# 明細CSVの1行を表す。同日に複数行あり得る（物件ごと）。
@dataclass(frozen=True)
class DetailRow:
    checkin: date
    price_yen: int
    listing_url: str
    raw_label: str
    title: str
    guests: Optional[int] = None
    bedrooms: Optional[int] = None
    beds: Optional[int] = None
    reviews_count: Optional[int] = None
    rating: Optional[float] = None
    subtitle: Optional[str] = None


# CSVのセルを整数に変換。空・"none"・不正値は None。例外時はその行だけ無効にして他は続行する方針。
def parse_int(value: str) -> Optional[int]:
    v = (value or "").strip()
    if v == "" or v.lower() == "none":
        return None
    try:
        return int(v)
    except ValueError:
        return None


# CSVのセルを浮動小数に変換。評価（rating）などで使用。不正値は None。
def parse_float(value: str) -> Optional[float]:
    v = (value or "").strip()
    if v == "" or v.lower() == "none":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def build_search_url(checkin: date, checkout: date) -> str:
    """
    検索条件を含むAirbnb検索URLを生成
    環境変数から検索条件を読み込む（scrape.pyと同じ設定）
    """
    # 検索条件を環境変数から読み込む（デフォルト値あり）
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


def get_or_build_search_url(r: AvgRow) -> str:
    """
    AvgRowのURLが空または検索条件が含まれていない場合、動的に検索URLを生成
    """
    if r.url and "checkin=" in r.url and "adults=" in r.url:
        # 既に検索条件が含まれている場合はそのまま使用
        return r.url
    
    # 検索条件が含まれていない場合は動的に生成
    checkout = r.checkin + timedelta(days=1)  # 1泊固定
    return build_search_url(r.checkin, checkout)


def group_by_month(rows: List[AvgRow]) -> Dict[Tuple[int, int], List[AvgRow]]:
    """
    日付を年・月でグループ化
    """
    by_month: Dict[Tuple[int, int], List[AvgRow]] = defaultdict(list)
    for r in rows:
        key = (r.checkin.year, r.checkin.month)
        by_month[key].append(r)
    return dict(sorted(by_month.items()))


def html_monthly_summary(avg_rows: List[AvgRow], detail_stats_by_day: Dict[date, Dict[str, Optional[int]]]) -> str:
    """
    1ヶ月単位の概要表を生成（年は表示せず、文字を小さく）
    平均、中央値、下位25%点、上位25%点はその日数の平均
    最小はその日数の最小、最大はその日数の最大
    """
    by_month = group_by_month(avg_rows)
    out = []
    out.append("<table class='small' style='font-size:11px; width:100%; table-layout:fixed;'>")
    out.append("<thead><tr><th style='font-size:11px; white-space:nowrap; width:40px; font-weight:bold; text-align:center;'>月</th><th style='font-size:11px; white-space:nowrap; width:35px; text-align:center;'>日数</th><th style='font-size:11px; white-space:nowrap; width:55px; text-align:center;'>平均</th><th style='font-size:11px; white-space:nowrap; width:55px; text-align:center;'>中央値</th><th style='font-size:11px; white-space:nowrap; width:70px; text-align:center;'>下位25%点</th><th style='font-size:11px; white-space:nowrap; width:70px; text-align:center;'>上位25%点</th><th style='font-size:11px; white-space:nowrap; width:50px; text-align:center;'>最小</th><th style='font-size:11px; white-space:nowrap; width:50px; text-align:center;'>最大</th></tr></thead>")
    out.append("<tbody>")
    
    for (year, month), month_rows in by_month.items():
        # 日ごとの詳細データを収集
        month_medians = []
        month_p25s = []
        month_p75s = []
        month_mins = []
        month_maxs = []
        
        # 平均価格のリスト（平均列用）
        month_avgs = [r.avg_price_yen for r in month_rows if r.avg_price_yen is not None]
        month_avgs_int = [v for v in month_avgs if isinstance(v, int)]
        
        for r in month_rows:
            s = detail_stats_by_day.get(r.checkin, {})
            if s.get("median") is not None: month_medians.append(s["median"])
            if s.get("p25") is not None: month_p25s.append(s["p25"])
            if s.get("p75") is not None: month_p75s.append(s["p75"])
            if s.get("min") is not None: month_mins.append(s["min"])
            if s.get("max") is not None: month_maxs.append(s["max"])
        
        # 平均：各日の平均価格の平均
        month_mean = mean_int(month_avgs_int)
        
        # 中央値、p25、p75：各日の値の平均
        month_median_val = mean_int(month_medians)
        month_p25_val = mean_int(month_p25s)
        month_p75_val = mean_int(month_p75s)
        
        # 最小：各日の最小値の最小
        month_min_val = min(month_mins) if month_mins else None
        
        # 最大：各日の最大値の最大
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


# CSVの checkin 列（YYYY-MM-DD）を date に変換。不正行は None を返し呼び出し側でスキップする。
def parse_date(value: str) -> Optional[date]:
    v = (value or "").strip()
    if not v:
        return None
    try:
        return datetime.fromisoformat(v).date()
    except ValueError:
        return None


# 平均CSVを読み AvgRow のリストで返す。ファイルが存在しない場合は呼び出し側で [] を渡す想定（main で exists チェック）。
# エラー時: 存在しない path は呼び出し前に弾く。読み込み中の例外は未捕捉ならそのまま伝播する。
def read_avg_csv(path: Path) -> List[AvgRow]:
    rows: List[AvgRow] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            d = parse_date(r.get("checkin", ""))
            if d is None:
                continue
            rows.append(
                AvgRow(
                    checkin=d,
                    avg_price_yen=parse_int(r.get("avg_price_yen", "")),
                    count=int(r.get("count", "0") or 0),
                    min_price_yen=parse_int(r.get("min_price_yen", "")),
                    max_price_yen=parse_int(r.get("max_price_yen", "")),
                    url=r.get("url", "") or "",
                )
            )
    return sorted(rows, key=lambda x: x.checkin)


# 明細CSVを読み DetailRow のリストで返す。checkin と price_yen が必須で、どちらか欠けている行はスキップする。
def read_details_csv(path: Path) -> List[DetailRow]:
    rows: List[DetailRow] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            d = parse_date(r.get("checkin", ""))
            p = parse_int(r.get("price_yen", ""))
            if d is None or p is None:
                continue
            rows.append(
                DetailRow(
                    checkin=d,
                    price_yen=p,
                    listing_url=r.get("listing_url", "") or "",
                    raw_label=r.get("raw_label", "") or "",
                    title=r.get("title", "") or "",
                    guests=parse_int(r.get("guests", "")),
                    bedrooms=parse_int(r.get("bedrooms", "")),
                    beds=parse_int(r.get("beds", "")),
                    reviews_count=parse_int(r.get("reviews_count", "")),
                    rating=parse_float(r.get("rating", "")),
                    subtitle=r.get("subtitle", "") or None,
                )
            )
    return sorted(rows, key=lambda x: (x.checkin, x.price_yen, x.listing_url))


# 整数リストの平均（四捨五入）。空リストは None。月別サマリや曜日別集計で使用。
def mean_int(values: List[int]) -> Optional[int]:
    if not values:
        return None
    return round(statistics.mean(values))


# 中央値（四捨五入）。日別の価格分布を表すために使用。
def median_int(values: List[int]) -> Optional[int]:
    if not values:
        return None
    return round(statistics.median(values))


# 分位点（0<=p<=1）。p=0.25 が下位25%点、p=0.75 が上位25%点。グラフのY軸範囲や凡例で使用。
def quantile(values: List[int], p: float) -> Optional[int]:
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    # linear interpolation (0..1)
    i = (len(xs) - 1) * p
    lo = int(i)
    hi = min(lo + 1, len(xs) - 1)
    w = i - lo
    return round(xs[lo] * (1 - w) + xs[hi] * w)


# 価格表示用。None は "-" にし、数値はカンマ区切りで表示。HTML・ツールチップの両方で使う。
def fmt_yen(v: Optional[int]) -> str:
    if v is None:
        return "-"
    return f"¥{v:,}"


def is_jp_holiday(d: date) -> bool:
    return d in JP_HOLIDAYS


def weekday_class(d: date) -> str:
    # 優先順位: 祝日 > 土 > 日 > 平日
    if is_jp_holiday(d):
        return "holiday"
    if d.weekday() == 5:
        return "sat"
    if d.weekday() == 6:
        return "sun"
    return "weekday"


def weekday_color(d: date) -> str:
    cls = weekday_class(d)
    if cls == "holiday":
        return "#16a34a"  # green
    if cls == "sat":
        return "#2563eb"  # blue
    if cls == "sun":
        return "#dc2626"  # red
    return "#666"


def nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    # weekday: Monday=0 .. Sunday=6
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    d = d.replace(day=1 + offset)
    return d.replace(day=d.day + 7 * (n - 1))


def vernal_equinox_day(year: int) -> date:
    # 1980-2099の近似式
    day = int(20.8431 + 0.242194 * (year - 1980) - int((year - 1980) / 4))
    return date(year, 3, day)


def autumn_equinox_day(year: int) -> date:
    # 1980-2099の近似式
    day = int(23.2488 + 0.242194 * (year - 1980) - int((year - 1980) / 4))
    return date(year, 9, day)


def japan_holidays_for_year(year: int) -> Set[date]:
    """
    日本の祝日（簡易実装）
    - 祝日法に基づく基本セット + 振替休日 + 国民の休日
    - 2026年以降の通常ルールを想定（オリンピック特例などは未対応）
    """
    hols: Set[date] = set()

    # 固定日
    hols.add(date(year, 1, 1))   # 元日
    hols.add(date(year, 2, 11))  # 建国記念の日
    hols.add(date(year, 2, 23))  # 天皇誕生日（2020-）
    hols.add(date(year, 4, 29))  # 昭和の日
    hols.add(date(year, 5, 3))   # 憲法記念日
    hols.add(date(year, 5, 4))   # みどりの日
    hols.add(date(year, 5, 5))   # こどもの日
    hols.add(date(year, 8, 11))  # 山の日
    hols.add(date(year, 11, 3))  # 文化の日
    hols.add(date(year, 11, 23)) # 勤労感謝の日

    # 移動祝日（ハッピーマンデー）
    hols.add(nth_weekday_of_month(year, 1, 0, 2))   # 成人の日: 1月第2月曜
    hols.add(nth_weekday_of_month(year, 7, 0, 3))   # 海の日: 7月第3月曜
    hols.add(nth_weekday_of_month(year, 9, 0, 3))   # 敬老の日: 9月第3月曜
    hols.add(nth_weekday_of_month(year, 10, 0, 2))  # スポーツの日: 10月第2月曜

    # 春分・秋分
    hols.add(vernal_equinox_day(year))
    hols.add(autumn_equinox_day(year))

    # 振替休日（祝日が日曜の場合、次の平日へ。連鎖あり）
    added_substitute: Set[date] = set()
    for h in sorted(hols):
        if h.weekday() == 6:  # Sunday
            d = h
            while True:
                d = date(d.year, d.month, d.day)  # explicit (clarity)
                d = d.replace(day=d.day)  # no-op
                d = d.fromordinal(d.toordinal() + 1)
                if d not in hols and d not in added_substitute:
                    added_substitute.add(d)
                    break
    hols |= added_substitute

    # 国民の休日（前後が祝日で、かつ日曜以外）
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


def load_jp_holidays_for_range(start_d: date, end_d: date) -> Set[date]:
    out: Set[date] = set()
    for y in range(start_d.year, end_d.year + 1):
        out |= japan_holidays_for_year(y)
    return {d for d in out if start_d <= d <= end_d}


# 既存の index.html を上書きする前に、そのファイルの更新日時（mtime）で日付付き名にし、html/ に退避する。
def backup_existing_html(out_path: Path) -> None:
    """
    既存のHTMLがあれば html/ フォルダへ退避（日時付きファイル名）。
    - 最新: docs/index.html（上書きする）
    - 旧:   html/index_YYYYMMDD_HHMMSS.html（そのファイルの更新日時を24時間表記で使用）

    ファイル名の日時は、そのファイルの更新日時（mtime）をローカル時刻で24時間表記にする。
    12時間表記（%I+%p）だと19時が「07」と紛らわしくなるため、%H（00–23）を使用する。
    """
    if not out_path.exists():
        return

    try:
        ts = out_path.stat().st_mtime
    except Exception:
        return

    dt = datetime.fromtimestamp(ts)
    # 例: 20260127_193052（24時間表記。19時は「19」と表示される）
    suffix = dt.strftime("%Y%m%d_%H%M%S")

    stem = "index" if out_path.name.lower() == "index.html" else out_path.stem
    DIR_HTML_HISTORY.mkdir(parents=True, exist_ok=True)
    candidate = DIR_HTML_HISTORY / f"{stem}{suffix}{out_path.suffix}"

    # かぶったら連番で回避
    i = 2
    while candidate.exists():
        candidate = DIR_HTML_HISTORY / f"{stem}{suffix}_{i}{out_path.suffix}"
        i += 1

    out_path.rename(candidate)


def svg_line_chart(
    avg_rows: List[AvgRow],
    detail_stats_by_day: Dict[date, Dict[str, Optional[int]]],
    base_width: int = 1100,
    height: int = 480,
) -> str:
    # y-range should consider all series (avg/median/p25/p75)
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

    # 1ヶ月分（約30日）を基準にして、データが多い場合は幅を拡張
    days_count = len(avg_rows)
    days_per_month = 30
    if days_count > days_per_month:
        # 30日を超える場合は幅を比例的に拡張
        chart_width = int(base_width * (days_count / days_per_month))
    else:
        chart_width = base_width

    # Y軸範囲設定：
    # - 下限：各日の下位25%点の中で最小の値
    # - 上限：各日の上位25%点の中で最大の値
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
        y_min = min(p25_values)  # 日別下位25%点の最小
        y_max = max(p75_values)  # 日別上位25%点の最大
    elif xs:
        # フォールバック：P25/P75データがない場合は全データの25%点と75%点
        xs_sorted = sorted(xs)
        y_min = quantile(xs_sorted, 0.25) or min(xs_sorted)
        y_max = quantile(xs_sorted, 0.75) or max(xs_sorted)
    else:
        y_min = 0
        y_max = 20000
    # Y軸範囲を厳密にP25最小〜P75最大に設定（パディングなし）
    y0 = y_min
    y1 = y_max

    # X軸ラベルのマージンを調整（Y軸とグラフ間の隙間を最小化）
    margin_l, margin_r, margin_t, margin_b = 45, 30, 20, 50
    chart_margin_l, chart_margin_r = 5, 20  # グラフ本体用の左右マージン（左を最小に）
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

    # 中心点は「平均」を主役として表示する
    avg_points: List[Tuple[float, float, AvgRow]] = []
    for i, r in enumerate(avg_rows):
        if not isinstance(r.avg_price_yen, int):
            continue
        avg_points.append((x_at(i), y_at(r.avg_price_yen), r))

    # series values aligned by day
    def series_value(r: AvgRow, key: str) -> Optional[int]:
        if key == "avg":
            return r.avg_price_yen
        s = detail_stats_by_day.get(r.checkin)
        if not s:
            return None
        v = s.get(key)
        return v if isinstance(v, int) else None

    def polyline_segments(key: str) -> List[str]:
        segs: List[str] = []
        cur: List[str] = []
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

    # ticks (8段に増やす)
    ticks = 8
    y_ticks = []
    for t in range(ticks + 1):
        v = round(y0 + (y1 - y0) * (t / ticks))
        y = y_at(v)
        y_ticks.append((v, y))

    # x labels: 全ての日付を表示（日次表示）
    x_labels = [(i, fmt_md(avg_rows[i].checkin)) for i in range(len(avg_rows))]

    # Y軸SVG（固定表示）
    y_axis_parts: List[str] = []
    y_axis_parts.append(f'<svg viewBox="0 0 {margin_l} {height}" width="{margin_l}" height="{height}" style="flex-shrink: 0;">')
    y_axis_parts.append('<rect x="0" y="0" width="100%" height="100%" fill="white"/>')
    
    # y grid + labels
    for v, y in y_ticks:
        y_axis_parts.append(f'<text x="{margin_l - 2}" y="{y + 4:.2f}" text-anchor="end" font-size="11" fill="#666">{escape(fmt_yen(v))}</text>')
    
    # Y axis
    y_axis_parts.append(f'<line x1="{margin_l - 1}" y1="{margin_t}" x2="{margin_l - 1}" y2="{height - margin_b}" stroke="#999"/>')
    y_axis_parts.append("</svg>")

    # グラフ本体SVG（スクロール表示）
    chart_parts: List[str] = []
    chart_parts.append(f'<svg viewBox="0 0 {chart_width} {height}" width="{chart_width}" height="{height}" role="img" aria-label="日別価格グラフ">')
    chart_parts.append('<rect x="0" y="0" width="100%" height="100%" fill="white" pointer-events="none"/>')

    # y grid lines (Y軸ラベルは除く)
    for v, y in y_ticks:
        chart_parts.append(f'<line x1="{chart_margin_l}" y1="{y:.2f}" x2="{chart_width - chart_margin_r}" y2="{y:.2f}" stroke="#eee" pointer-events="none"/>')

    # X axis
    chart_parts.append(f'<line x1="{chart_margin_l}" y1="{height - margin_b}" x2="{chart_width - chart_margin_r}" y2="{height - margin_b}" stroke="#999" pointer-events="none"/>')

    # x tick dotted lines + labels
    for i, label in x_labels:
        x = x_at(i)
        y = height - 12
        # dotted vertical guide line
        chart_parts.append(
            f'<line x1="{x:.2f}" y1="{margin_t}" x2="{x:.2f}" y2="{height - margin_b}" stroke="#f3f4f6" pointer-events="none"/>'
        )
        chart_parts.append(
            f'<text x="{x:.2f}" y="{y}" text-anchor="middle" font-size="9" fill="{weekday_color(avg_rows[i].checkin)}" pointer-events="none">{escape(label)}</text>'
        )

    # series lines
    series = [
        ("avg", "#2563eb", 3.5, None),        # 平均: 濃い青、太線、実線
        ("median", "#8b5cf6", 2.8, None),      # 中央値: 濃い紫、中太線、実線
        ("p25", "#10b981", 2.2, "4,2"),       # P25: 濃い緑、中線、破線
        ("p75", "#f59e0b", 2.2, "4,2"),       # P75: 濃いオレンジ、中線、破線
    ]
    for key, color, sw, dash_pattern in series:
        for seg in polyline_segments(key):
            dash_attr = f' stroke-dasharray="{dash_pattern}"' if dash_pattern else ""
            chart_parts.append(f'<polyline fill="none" stroke="{color}" stroke-width="{sw}"{dash_attr} points="{seg}" pointer-events="none"/>')

    # center dots (avg) with tooltip
    for x, y, r in avg_points:
        s = detail_stats_by_day.get(r.checkin, {})
        date_str = fmt_date_jp_with_weekday_no_year(r.checkin)
        avg_str = fmt_yen(r.avg_price_yen)
        median_str = fmt_yen(s.get('median'))
        p25_str = fmt_yen(s.get('p25'))
        p75_str = fmt_yen(s.get('p75'))
        min_str = fmt_yen(s.get('min'))
        max_str = fmt_yen(s.get('max'))
        count_str = str(r.count)
        chart_parts.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3.6" fill="#2563eb" stroke="white" stroke-width="1" '
            f'style="cursor:pointer; pointer-events:auto;" '
            f'data-tooltip-date="{escape(date_str)}" '
            f'data-tooltip-avg="{escape(avg_str)}" '
            f'data-tooltip-median="{escape(median_str)}" '
            f'data-tooltip-p25="{escape(p25_str)}" '
            f'data-tooltip-p75="{escape(p75_str)}" '
            f'data-tooltip-min="{escape(min_str)}" '
            f'data-tooltip-max="{escape(max_str)}" '
            f'data-tooltip-count="{escape(count_str)}" '
            f'data-tooltip-x="{x:.2f}" '
            f'data-tooltip-y="{y:.2f}"></circle>'
        )

    chart_parts.append("</svg>")
    
    # Y軸とグラフを横並びで表示
    result = f"""<div style="display: flex; align-items: stretch;">
        {'\n'.join(y_axis_parts)}
        <div style="overflow-x: auto; -webkit-overflow-scrolling: touch; flex: 1;">
            {'\n'.join(chart_parts)}
        </div>
    </div>"""
    
    return result


# 日付ごとに価格の中央値・p25・p75・min・max を計算。グラフと日別表の「中央値」「下位25%点」等に使う。
def build_detail_stats_by_day(details_rows: List[DetailRow]) -> Dict[date, Dict[str, Optional[int]]]:
    by_day: DefaultDict[date, List[int]] = defaultdict(list)
    for r in details_rows:
        by_day[r.checkin].append(r.price_yen)

    out: Dict[date, Dict[str, Optional[int]]] = {}
    for d, prices in by_day.items():
        out[d] = {
            "median": median_int(prices),
            "p25": quantile(prices, 0.25),
            "p75": quantile(prices, 0.75),
            "min": min(prices) if prices else None,
            "max": max(prices) if prices else None,
        }
    return out


def build_detail_payload_by_day(details_rows: List[DetailRow]) -> Dict[str, Dict[str, object]]:
    """
    UI用: 日付(ISO)ごとに明細をまとめたJSONを生成する。
    - 表示は平均表の▶から「その日だけ」開く前提
    """
    by_day: DefaultDict[date, List[DetailRow]] = defaultdict(list)
    for r in details_rows:
        by_day[r.checkin].append(r)

    payload: Dict[str, Dict[str, object]] = {}
    for d in sorted(by_day.keys()):
        rows = sorted(by_day[d], key=lambda x: x.price_yen)
        payload[d.isoformat()] = {
            "dateLabelHtml": fmt_date_jp_with_weekday_html(d),
            "wcls": weekday_class(d),
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


def html_table_avg(rows: List[AvgRow], detail_stats_by_day: Dict[date, Dict[str, Optional[int]]]) -> str:
    out = []
    out.append('<table class="avg-table">')
    out.append("<thead><tr>")
    out.append(
        "<th style='text-align:center;'></th>"
        "<th style='text-align:center;'>チェックイン日</th>"
        "<th style='text-align:center;'>平均</th>"
        "<th style='text-align:center;'>中央値</th>"
        "<th style='text-align:center;'>下位25%点</th>"
        "<th style='text-align:center;'>上位25%点</th>"
        "<th style='text-align:center;'>件数</th>"
        "<th style='text-align:center;'>最小</th>"
        "<th style='text-align:center;'>最大</th>"
        "<th style='text-align:center;'>検索URL</th>"
    )
    out.append("</tr></thead>")
    out.append("<tbody>")
    for r in rows:
        s = detail_stats_by_day.get(r.checkin, {})
        median_v = s.get("median")
        p25_v = s.get("p25")
        p75_v = s.get("p75")
        iso = r.checkin.isoformat()
        has_details = r.checkin in detail_stats_by_day

        out.append("<tr>")
        if has_details:
            out.append(
                f'<td><button type="button" class="jump-btn" onclick="openDayModal(\'{iso}\')" title="明細を開く">▶</button></td>'
            )
        else:
            out.append('<td><button type="button" class="jump-btn" disabled title="明細なし">▶</button></td>')
        out.append(f"<td>{fmt_date_jp_with_weekday_html(r.checkin)}</td>")
        out.append(f"<td style='text-align:right;'>{escape(fmt_yen(r.avg_price_yen))}</td>")
        out.append(f"<td style='text-align:right;'>{escape(fmt_yen(median_v))}</td>")
        out.append(f"<td style='text-align:right;'>{escape(fmt_yen(p25_v))}</td>")
        out.append(f"<td style='text-align:right;'>{escape(fmt_yen(p75_v))}</td>")
        out.append(f"<td style='text-align:right;'>{r.count}</td>")
        out.append(f"<td style='text-align:right;'>{escape(fmt_yen(r.min_price_yen))}</td>")
        out.append(f"<td style='text-align:right;'>{escape(fmt_yen(r.max_price_yen))}</td>")
        # 検索URLを取得（空の場合は動的に生成）
        search_url = get_or_build_search_url(r)
        out.append(f'<td style="text-align:center;"><a class="link-btn" href="{escape(search_url)}" target="_blank" rel="noreferrer">詳細</a></td>')
        out.append("</tr>")
    out.append("</tbody></table>")
    return "\n".join(out)


def html_details_by_day(details: List[DetailRow], limit_each_day: Optional[int]) -> str:
    by_day: DefaultDict[date, List[DetailRow]] = defaultdict(list)
    for r in details:
        by_day[r.checkin].append(r)

    out: List[str] = []
    for d in sorted(by_day.keys()):
        rows = sorted(by_day[d], key=lambda x: x.price_yen)
        prices = [r.price_yen for r in rows]
        min_v = min(prices) if prices else None
        max_v = max(prices) if prices else None
        summary_html = (
            f"{fmt_date_jp_with_weekday_html(d)}  "
            f"件数={escape(len(rows))}  "
            f"平均={escape(fmt_yen(mean_int(prices)))}  "
            f"中央={escape(fmt_yen(median_int(prices)))}  "
            f"最小={escape(fmt_yen(min_v))}  "
            f"最大={escape(fmt_yen(max_v))}  "
            f"下位25%点={escape(fmt_yen(quantile(prices, 0.25)))}  "
            f"上位25%点={escape(fmt_yen(quantile(prices, 0.75)))}"
        )
        out.append(f'<details id="details-{d.isoformat()}"><summary>{summary_html}</summary>')
        out.append("<div class='details-table-wrap'>")
        out.append("<table>")
        out.append("<thead><tr><th>#</th><th>価格</th><th>物件URL</th><th>備考</th></tr></thead>")
        out.append("<tbody>")

        show = rows if limit_each_day is None else rows[:limit_each_day]
        for i, r in enumerate(show, start=1):
            out.append("<tr>")
            out.append(f"<td>{i}</td>")
            out.append(f"<td>{escape(fmt_yen(r.price_yen))}</td>")
            if r.listing_url:
                out.append(
                    f'<td><a href="{escape(r.listing_url)}" target="_blank" rel="noreferrer">'
                    f'<span class="truncate-url" title="{escape(r.listing_url)}">{escape(r.listing_url[:40]) + ("…" if len(r.listing_url) > 40 else "")}</span>'
                    f'</a></td>'
                )
            else:
                out.append("<td>-</td>")
            out.append(f"<td>{escape(r.raw_label)}</td>")
            out.append("</tr>")
        out.append("</tbody></table>")
        out.append("</div>")
        if limit_each_day is not None and len(rows) > limit_each_day:
            out.append(f"<p class='note'>表示は最安{limit_each_day}件のみ（全{len(rows)}件）</p>")
        out.append("</details>")
    return "\n".join(out)


# =============================================================================
# エントリポイント（引数解釈・データ読み込み・祝日準備・HTML組み立て・出力）
# =============================================================================

def main() -> int:
    """
    CSV を読み、祝日を準備し、HTML を組み立てて 1 ファイルに出力する。
    前提: --avg / --details で指定するパスは scrape.py の出力と一致させる（未指定時は定数 DEFAULT_* を使用）。
    入力: 平均CSV・明細CSV。存在しない場合は空リストで続行し、HTML 上は「データなし」等と表示する。
    例外: ファイル読み込みで例外が出た場合は未捕捉で終了。パース失敗行はスキップするだけなので処理は続行。
    """
    parser = argparse.ArgumentParser(description="Airbnb CSVを解析してHTMLレポートを生成します。")
    parser.add_argument("--avg", default=DEFAULT_AVG_PATH, help=f"平均CSV (default: {DEFAULT_AVG_PATH})")
    parser.add_argument("--details", default=DEFAULT_DETAILS_PATH, help=f"明細CSV (default: {DEFAULT_DETAILS_PATH})")
    parser.add_argument("--out", default=DEFAULT_OUT_PATH, help=f"出力HTML (default: {DEFAULT_OUT_PATH})")
    parser.add_argument("--title", default=DEFAULT_TITLE, help=f"HTMLタイトル (default: {DEFAULT_TITLE})")
    parser.add_argument("--details-limit", type=int, default=DEFAULT_DETAILS_LIMIT, help="明細の各日表示件数（0で全件）")
    args = parser.parse_args()

    avg_path = Path(args.avg)
    details_path = Path(args.details)
    out_path = Path(args.out)

    # 入力: CSV がなければ空リストのまま続行（「データなし」表示）。存在する場合のみ読み込み。
    avg_rows: List[AvgRow] = read_avg_csv(avg_path) if avg_path.exists() else []
    details_rows: List[DetailRow] = read_details_csv(details_path) if details_path.exists() else []

    # 祝日セットの準備（データ期間に合わせる）。祝日は曜日表示で緑色にするため。期間外の祝日は読み込まない。
    global JP_HOLIDAYS
    if avg_rows:
        JP_HOLIDAYS = load_jp_holidays_for_range(avg_rows[0].checkin, avg_rows[-1].checkin)
    elif details_rows:
        JP_HOLIDAYS = load_jp_holidays_for_range(details_rows[0].checkin, details_rows[-1].checkin)
    else:
        JP_HOLIDAYS = set()

    # summary stats (avg)
    avg_values = [r.avg_price_yen for r in avg_rows if r.avg_price_yen is not None]
    avg_values_int = [v for v in avg_values if isinstance(v, int)]
    overall_mean = mean_int(avg_values_int)
    overall_median = median_int(avg_values_int)

    best: Optional[AvgRow] = None
    worst: Optional[AvgRow] = None
    for r in avg_rows:
        if r.avg_price_yen is None:
            continue
        if best is None or r.avg_price_yen < (best.avg_price_yen or 10**18):
            best = r
        if worst is None or r.avg_price_yen > (worst.avg_price_yen or -1):
            worst = r

    weekday_map: DefaultDict[int, List[int]] = defaultdict(list)
    for r in avg_rows:
        if r.avg_price_yen is None:
            continue
        weekday_map[r.checkin.weekday()].append(r.avg_price_yen)

    weekday_lines: List[str] = []
    weekday_lines.append("<table class='small weekday-table'>")
    weekday_lines.append("<thead><tr><th>曜日</th><th>日数</th><th>日別平均の平均</th></tr></thead><tbody>")
    for wd in range(7):
        vals = weekday_map.get(wd, [])
        if wd == 5:
            cls = "sat"
        elif wd == 6:
            cls = "sun"
        else:
            cls = "weekday"
        weekday_lines.append(
            f'<tr class="{cls}"><td>{WEEKDAYS_JA[wd]}</td><td>{len(vals)}</td><td>{escape(fmt_yen(mean_int(vals)))}</td></tr>'
        )
    weekday_lines.append("</tbody></table>")

    # 明細の表示件数上限（0で全件）。日別表・グラフの中央値等は detail_stats_by_day、モーダル用JSONは detail_payload_by_day。
    details_limit = None if args.details_limit <= 0 else args.details_limit
    detail_stats_by_day = build_detail_stats_by_day(details_rows) if details_rows else {}
    detail_payload_by_day = build_detail_payload_by_day(details_rows) if details_rows else {}

    # HTML 組み立て（1ファイルにまとめる。スタイル・スクリプトはインラインで埋め込み、外部依存なし）
    title = args.title
    html_parts: List[str] = []
    html_parts.append("<!DOCTYPE html>")
    html_parts.append('<html lang="ja">')
    html_parts.append("<head>")
    html_parts.append('<meta charset="UTF-8">')
    html_parts.append(f"<title>{escape(title)}</title>")
    html_parts.append(
        """
<style>
body { font-family: "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Noto Sans JP", "Yu Gothic", "Meiryo", system-ui, -apple-system, Segoe UI, Arial, sans-serif; padding: 18px; color: #111; }
h1 { margin: 0 0 10px; font-size: 20px; }
.muted { color: #666; }
.grid { display: grid; grid-template-columns: 0.8fr 2.7fr; gap: 12px; align-items: stretch; max-width: 1127px; }
.card { border: 1px solid #e5e7eb; border-radius: 10px; padding: 12px; background: #fff; }
.card-head { display:flex; align-items: baseline; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.legend { display:flex; gap: 12px; flex-wrap: wrap; align-items: center; }
.legend-item { display:flex; gap: 6px; align-items: center; font-size: 12px; color: #111; }
.swatch { width: 22px; height: 0; border-top: 3px solid #999; }
.note { color: #666; font-size: 12px; margin: 8px 0 0; }
.graph-tooltip { position: fixed; background: #fff; border: 1px solid #d1d5db; border-radius: 8px; padding: 8px 10px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); font-size: 12px; line-height: 1.5; z-index: 1000; pointer-events: none; min-width: 160px; max-width: 200px; }
.graph-tooltip .tooltip-header { font-weight: 600; color: #111; margin-bottom: 6px; padding-bottom: 4px; border-bottom: 1px solid #e5e7eb; }
.graph-tooltip .tooltip-row { margin: 2px 0; display: flex; justify-content: space-between; align-items: baseline; }
.graph-tooltip .tooltip-label { color: #666; font-size: 11px; }
.graph-tooltip .tooltip-value { color: #111; font-weight: 500; text-align: right; }
.graph-tooltip .tooltip-section { margin-top: 6px; padding-top: 6px; border-top: 1px solid #f3f4f6; }
.graph-tooltip .tooltip-section:first-child { margin-top: 0; padding-top: 0; border-top: none; }
.scroll-box { max-height: 520px; overflow: auto; border: 1px solid #e5e7eb; border-radius: 10px; -webkit-overflow-scrolling: touch; }
.scroll-box.pad { padding: 6px; }
.scroll-box.avg { max-height: 420px; }
.jump-btn { border: 1px solid #e5e7eb; background: #fff; border-radius: 8px; padding: 4px 9px; cursor: pointer; font-size: 12px; min-width: 28px; min-height: 24px; }
.jump-btn:hover { background: #f3f4f6; }
.jump-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.modal-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.45); display: none; align-items: center; justify-content: center; padding: 18px; z-index: 9999; }
.modal-backdrop.open { display: flex; }
.modal { width: min(1280px, 98vw); max-height: min(95vh, 1000px); background: #fff; border-radius: 14px; border: 1px solid #e5e7eb; overflow: hidden; box-shadow: 0 12px 40px rgba(0,0,0,0.25); display: flex; flex-direction: column; }
.modal-header { display:flex; align-items:center; justify-content: space-between; gap: 10px; padding: 12px 14px; border-bottom: 1px solid #e5e7eb; flex-shrink: 0; }
.modal-title { font-weight: 600; font-size: 13px; }
.modal-close { 
  border: none; 
  background: none; 
  cursor: pointer; 
  font-size: 20px; 
  line-height: 1; 
  color: #666; 
  padding: 4px; 
  border-radius: 4px; 
  width: 32px; 
  height: 32px; 
  display: flex; 
  align-items: center; 
  justify-content: center;
  transition: background-color 0.2s, color 0.2s;
}
.modal-close:hover { 
  background: #f3f4f6; 
  color: #333; 
}
.modal-body { padding: 10px 12px; overflow-y: auto; overflow-x: hidden; flex: 1; min-height: 0; -webkit-overflow-scrolling: touch; }
.modal table { 
  table-layout: fixed; 
  border-collapse: collapse; 
  border-spacing: 0; 
}
.modal th, .modal td { 
  padding: 3px 5px; 
  font-size: 11px; 
  line-height: 1.25; 
  vertical-align: middle; 
  border: 1px solid #e5e7eb; 
  position: relative;
}
.modal th { 
  background: #f9fafb; 
  position: sticky; 
  top: 0; 
  z-index: 10;
  border-bottom: 2px solid #e5e7eb;
}
.modal td:nth-child(1) { text-align: center; }
/* モーダルテーブルの列幅設定 */
.modal th:nth-child(1), .modal td:nth-child(1) { width: 40px; text-align: center; } /* No. */
.modal th:nth-child(2), .modal td:nth-child(2) { width: 60px; } /* 価格 */
.modal th:nth-child(3), .modal td:nth-child(3) { width: 140px; } /* タイトル */
.modal th:nth-child(4), .modal td:nth-child(4) { width: 50px; } /* レビュー */
.modal th:nth-child(5), .modal td:nth-child(5) { width: 50px; } /* レビュー数 */
.modal th:nth-child(6), .modal td:nth-child(6) { width: 300px; } /* 補足情報 */
.modal th:nth-child(7), .modal td:nth-child(7) { width: 50px; } /* 詳細 */
.modal th:nth-child(8), .modal td:nth-child(8) { width: 200px; } /* 備考 */
/* 備考列のテキスト折り返しを改善 */
.modal td:nth-child(8) { word-break: break-word; white-space: normal; line-height: 1.4; }
/* タイトル列のテキスト折り返しを改善 */
.modal td:nth-child(3) { word-break: break-word; white-space: normal; line-height: 1.4; }
/* 補足情報列のテキスト折り返しを改善 */
.modal td:nth-child(6) { word-break: break-word; white-space: normal; line-height: 1.4; }
.modal .truncate-url { max-width: 100%; display: inline-block; }
.modal .link-btn, .link-btn { display: inline-flex; align-items: center; justify-content: center; padding: 2px 8px; background: #60a5fa; color: #fff; text-decoration: none; border: none; border-radius: 4px; font-size: 10px; white-space: nowrap; height: 20px; line-height: 1; box-shadow: none; outline: none; }
.modal .link-btn:hover, .link-btn:hover { background: #3b82f6; }
.modal .link-btn:focus, .link-btn:focus { outline: none; box-shadow: none; }
.modal .details-table-wrap { max-height: none; overflow: visible; border: none; margin-top: 0; }
.modal-body .details-table-wrap { 
  max-height: calc(95vh - 180px); 
  overflow-y: auto; 
  overflow-x: auto; 
  border: 1px solid #e5e7eb; 
  border-radius: 8px; 
  margin-top: 8px; 
  -webkit-overflow-scrolling: touch;
  background: #fff;
}
.modal-body #modalStats { flex-shrink: 0; margin-bottom: 10px; }
.pill { display: inline-block; padding: 4px 10px; border-radius: 6px; font-size: 11px; background: #f3f4f6; color: #111; margin-right: 8px; margin-bottom: 4px; font-weight: 500; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #e5e7eb; padding: 6px 8px; text-align: left; vertical-align: top; word-break: break-word; }
td a { word-break: break-all; }
th { background: #f9fafb; position: sticky; top: 0; }
tr:nth-child(even) { background: #fcfcfd; }
table.small th, table.small td { padding: 5px 7px; font-size: 13px; vertical-align: middle; }
.weekday-table th, .weekday-table td { vertical-align: middle; }
details { margin: 10px 0; }
summary { cursor: pointer; font-weight: 600; }
.details-table-wrap { max-height: 420px; overflow: auto; border: 1px solid #e5e7eb; border-radius: 10px; margin-top: 8px; -webkit-overflow-scrolling: touch; }
.weekday-table tbody td:first-child { color: #0f766e; font-weight: 700; } /* 曜日の文字色だけ変更 */
.weekday-table tr.sat td:first-child { color: #2563eb; }
.weekday-table tr.sun td:first-child { color: #dc2626; }
.wday { font-weight: 700; }
.wday.sat { color: #2563eb; }
.wday.sun { color: #dc2626; }
.wday.holiday { color: #16a34a; }
.wday.weekday { color: inherit; }
.avg-table th, .avg-table td { padding: 4px 6px; font-size: 12px; vertical-align: middle; }
.avg-table th:nth-child(1), .avg-table td:nth-child(1) { width: 34px; text-align: center; padding: 4px 4px; }
.avg-table th:nth-child(2), .avg-table td:nth-child(2) { width: 180px; text-align: center; } /* チェックイン日 */
.avg-table th:nth-child(3), .avg-table td:nth-child(3) { width: 80px; } /* 平均 */
.avg-table th:nth-child(4), .avg-table td:nth-child(4) { width: 80px; } /* 中央値 */
.avg-table th:nth-child(5), .avg-table td:nth-child(5) { width: 90px; } /* 下位25%点 */
.avg-table th:nth-child(6), .avg-table td:nth-child(6) { width: 90px; } /* 上位25%点 */
.avg-table th:nth-child(7), .avg-table td:nth-child(7) { width: 60px; } /* 件数 */
.avg-table th:nth-child(8), .avg-table td:nth-child(8) { width: 80px; } /* 最小 */
.avg-table th:nth-child(9), .avg-table td:nth-child(9) { width: 80px; } /* 最大 */
.avg-table th:nth-child(10), .avg-table td:nth-child(10) { width: 80px; } /* 検索URL */
.avg-table { min-width: 980px; }
.truncate-url { display: inline-block; max-width: 240px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; vertical-align: bottom; }
@media (max-width: 900px) {
  body { padding: 12px; }
  .grid { grid-template-columns: 1fr; }
  .card { padding: 10px; }
  .card-head { flex-direction: column; align-items: flex-start; }
  .legend { gap: 8px; }
  .scroll-box.avg { max-height: 55vh; }
  .truncate-url { max-width: 120px; }
  .avg-table th, .avg-table td { font-size: 11px; padding: 3px 5px; }
  .avg-table { min-width: 940px; }
}
@media (max-width: 600px) {
  h1 { font-size: 18px; }
  h2 { font-size: 16px; margin: 6px 0; }
  .legend-item { font-size: 11px; }
  .modal-backdrop { padding: 0; }
  .modal { width: 100vw; max-height: 100vh; height: 100vh; border-radius: 0; }
  .modal-body { padding: 8px 10px; }
  .modal-body .details-table-wrap { max-height: calc(100vh - 160px); }
  .modal th, .modal td { font-size: 10px; padding: 3px 4px; }
  .modal th:nth-child(1), .modal td:nth-child(1) { width: 30px; text-align: center; }
  .modal th:nth-child(2), .modal td:nth-child(2) { width: 50px; }
  .modal th:nth-child(3), .modal td:nth-child(3) { width: 90px; }
  .modal th:nth-child(4), .modal td:nth-child(4) { width: 40px; }
  .modal th:nth-child(5), .modal td:nth-child(5) { width: 40px; }
  .modal th:nth-child(6), .modal td:nth-child(6) { width: 120px; }
  .modal th:nth-child(7), .modal td:nth-child(7) { width: 40px; }
  .modal th:nth-child(8), .modal td:nth-child(8) { width: 100px; }
  .pill { font-size: 11px; }
}
/* 斜め日付をやめたので不要
svg text[transform] {
    transform: none !important;
}
*/
</style>
<script id="day-details-data" type="application/json">
"""
        )
    html_parts.append(json.dumps(detail_payload_by_day, ensure_ascii=False).replace("</", "<\\/"))
    html_parts.append(
        """
</script>
<script>
function escHtml(s){
  return String(s).replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;").replaceAll("'","&#x27;");
}
function fmtYen(n){
  if(n === null || n === undefined || n === "") return "-";
  const v = Number(n);
  if(!Number.isFinite(v)) return "-";
  return "¥" + v.toLocaleString("ja-JP");
}
function mean(arr){
  if(arr.length === 0) return null;
  let s = 0;
  for(const v of arr) s += v;
  return Math.round(s / arr.length);
}
function median(arr){
  if(arr.length === 0) return null;
  const xs = [...arr].sort((a,b)=>a-b);
  const m = Math.floor(xs.length/2);
  if(xs.length % 2 === 1) return xs[m];
  return Math.round((xs[m-1] + xs[m]) / 2);
}
function quantile(arr, p){
  if(arr.length === 0) return null;
  const xs = [...arr].sort((a,b)=>a-b);
  if(xs.length === 1) return xs[0];
  const i = (xs.length - 1) * p;
  const lo = Math.floor(i);
  const hi = Math.min(lo + 1, xs.length - 1);
  const w = i - lo;
  return Math.round(xs[lo] * (1 - w) + xs[hi] * w);
}

let DAY_DETAILS = {};
window.addEventListener("DOMContentLoaded", () => {
  try{
    DAY_DETAILS = JSON.parse(document.getElementById("day-details-data").textContent || "{}");
  }catch(e){
    DAY_DETAILS = {};
  }

  const backdrop = document.getElementById("modalBackdrop");
  const btnClose = document.getElementById("modalClose");
  backdrop.addEventListener("click", (e) => { if(e.target === backdrop) closeDayModal(); });
  btnClose.addEventListener("click", closeDayModal);
  document.addEventListener("keydown", (e) => { if(e.key === "Escape") closeDayModal(); });
  
  initGraphTooltip();
});

function openDayModal(isoDate){
  const item = DAY_DETAILS[isoDate];
  if(!item) return;

  const title = document.getElementById("modalTitle");
  const stats = document.getElementById("modalStats");
  const body = document.getElementById("modalTableBody");

  title.innerHTML = item.dateLabelHtml || escHtml(isoDate);

  const rows = (item.rows || []).slice();
  const prices = rows.map(r => Number(r.price)).filter(v => Number.isFinite(v));
  const minV = prices.length ? Math.min(...prices) : null;
  const maxV = prices.length ? Math.max(...prices) : null;
  stats.innerHTML = [
    `<span class="pill">件数 ${rows.length}</span>`,
    `<span class="pill">平均 ${escHtml(fmtYen(mean(prices)))}</span>`,
    `<span class="pill">中央値 ${escHtml(fmtYen(median(prices)))}</span>`,
    `<span class="pill">下位25%点 ${escHtml(fmtYen(quantile(prices, 0.25)))}</span>`,
    `<span class="pill">上位25%点 ${escHtml(fmtYen(quantile(prices, 0.75)))}</span>`,
    `<span class="pill">最小 ${escHtml(fmtYen(minV))}</span>`,
    `<span class="pill">最大 ${escHtml(fmtYen(maxV))}</span>`,
  ].join(" ");

  rows.sort((a,b)=>Number(a.price)-Number(b.price));
  const isMobile = window.innerWidth <= 600;
  const html = rows.map((r, idx) => {
    const url = r.url || "";
    const label = r.label || "";
    const title = r.title || "";
    const reviewsCount = r.reviews_count != null ? String(r.reviews_count) : "";
    const rating = r.rating != null ? parseFloat(r.rating).toFixed(2) : "";
    const subtitle = r.subtitle || "";
    const detailLink = url ? `<a class="link-btn" href="${escHtml(url)}" target="_blank" rel="noreferrer">詳細</a>` : "-";
    const labelShort = label.length > 180 ? (label.slice(0,180) + "…") : label;
    const titleShort = title.length > 90 ? (title.slice(0,90) + "…") : title;
    const subtitleShort = subtitle.length > 150 ? (subtitle.slice(0,150) + "…") : subtitle;
    return `<tr>
      <td>${idx + 1}</td>
      <td style='text-align:right;'>${escHtml(fmtYen(r.price))}</td>
      <td>${escHtml(titleShort || "-")}</td>
      <td style='text-align:right;'>${escHtml(rating || "-")}</td>
      <td style='text-align:right;'>${escHtml(reviewsCount || "-")}</td>
      <td>${escHtml(subtitleShort || "-")}</td>
      <td style="text-align:center;">${detailLink}</td>
      <td>${escHtml(labelShort)}</td>
    </tr>`;
  }).join("");
  body.innerHTML = html || `<tr><td colspan="8" class="muted">明細なし</td></tr>`;

  // ツールチップを無効化
  const linkBtns = body.querySelectorAll('.link-btn');
  linkBtns.forEach(btn => {
    btn.removeAttribute('title');
    // href属性によるブラウザのデフォルトツールチップも防ぐ
    btn.addEventListener('mouseenter', (e) => {
      e.preventDefault();
      e.stopPropagation();
    });
  });
  
  // 備考列（最後の列）のツールチップを無効化
  const allCells = body.querySelectorAll('td');
  allCells.forEach((cell, index) => {
    // 各trの最後のtd（備考列）を特定
    const row = cell.parentElement;
    if (row && cell === row.lastElementChild) {
      // title属性を完全に削除（空文字列ではなく）
      cell.removeAttribute('title');
      // ブラウザのデフォルトツールチップを防ぐ（title属性は設定しない）
      cell.style.cursor = 'default';
      // フォーカス時のツールチップも防ぐ
      cell.addEventListener('focus', (e) => {
        e.preventDefault();
        e.stopPropagation();
      });
      cell.addEventListener('mouseenter', (e) => {
        e.preventDefault();
        e.stopPropagation();
      });
      // マウスオーバー時にtitle属性が追加されないように監視
      const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
          if (mutation.type === 'attributes' && mutation.attributeName === 'title') {
            cell.removeAttribute('title');
          }
        });
      });
      observer.observe(cell, { attributes: true, attributeFilter: ['title'] });
    }
  });

  const backdrop = document.getElementById("modalBackdrop");
  backdrop.classList.add("open");
  // スクロール位置をトップへ
  document.getElementById("modalBody").scrollTop = 0;
}

function closeDayModal(){
  const backdrop = document.getElementById("modalBackdrop");
  if(backdrop) backdrop.classList.remove("open");
}

// Graph tooltip
let tooltipEl = null;

function updateTooltipPosition(circle) {
  if(!tooltipEl) return;
  
  // ツールチップを一度表示してサイズを取得
  tooltipEl.style.display = 'block';
  
  const circleRect = circle.getBoundingClientRect();
  
  // 点の中心座標（ビューポート座標、position: fixedなのでそのまま使用）
  const px = circleRect.left + circleRect.width / 2;
  const py = circleRect.top + circleRect.height / 2;
  
  const offset = 8; // 点とツールチップの間の余白
  
  // 点の上端の座標
  const circleTop = circleRect.top;
  
  // ツールチップのサイズを取得（まだ位置が設定されていない状態）
  const tooltipHeight = tooltipEl.offsetHeight;
  const tooltipWidth = tooltipEl.offsetWidth;
  
  // 点の真上に配置：点の上端の上にツールチップの下端が来るように
  // ツールチップの上端 = 点の上端 - ツールチップの高さ - オフセット
  let tooltipTop = circleTop - tooltipHeight - offset;
  
  // 水平位置：点の中心から少し右に配置
  let tooltipLeft = px + 12;
  
  // 位置を設定
  tooltipEl.style.left = tooltipLeft + 'px';
  tooltipEl.style.top = tooltipTop + 'px';
  
  // 画面外に出ないように調整
  const tooltipRect = tooltipEl.getBoundingClientRect();
  
  // 右側にはみ出す場合
  if(tooltipRect.right > window.innerWidth - 10){
    tooltipEl.style.left = (px - tooltipWidth - 12) + 'px';
  }
  
  // 左側にはみ出す場合
  if(tooltipRect.left < 10){
    tooltipEl.style.left = '10px';
  }
  
  // 上側にはみ出す場合（点の下に表示）
  if(tooltipRect.top < 10){
    // 点の下端の下に配置
    tooltipEl.style.top = (circleRect.bottom + offset) + 'px';
  }
  
  // 下側にはみ出す場合（再計算）
  const finalRect = tooltipEl.getBoundingClientRect();
  if(finalRect.bottom > window.innerHeight - 10){
    tooltipEl.style.top = (window.innerHeight - finalRect.height - 10) + 'px';
  }
}

function initGraphTooltip(){
  // ツールチップ要素を作成
  if(!tooltipEl){
    tooltipEl = document.createElement('div');
    tooltipEl.className = 'graph-tooltip';
    tooltipEl.style.display = 'none';
    document.body.appendChild(tooltipEl);
  }
  
  // スクロール可能な部分のSVG内の円を探す（より確実に選択）
  // 少し遅延を入れて、DOMが完全に読み込まれるのを待つ
  function tryInit() {
    const circles = document.querySelectorAll('svg circle[data-tooltip-date]');
    if(circles.length) {
      attachTooltipListeners(circles);
    } else {
      // 再試行（DOMがまだ完全に読み込まれていない可能性）
      setTimeout(tryInit, 100);
    }
  }
  
  // 複数回試行（DOM読み込みのタイミングに依存しないように）
  setTimeout(tryInit, 50);
  setTimeout(tryInit, 200);
  setTimeout(tryInit, 500);
}

function attachTooltipListeners(circles){
  circles.forEach(circle => {
    // 既にイベントリスナーが設定されている場合はスキップ
    if(circle.hasAttribute('data-tooltip-attached')) return;
    circle.setAttribute('data-tooltip-attached', 'true');
    
    // pointer-eventsを明示的に設定（念のため）
    circle.style.pointerEvents = 'auto';
    
    circle.addEventListener('mouseenter', (e) => {
      e.stopPropagation();
      const c = e.target;
      const date = c.getAttribute('data-tooltip-date');
      const avg = c.getAttribute('data-tooltip-avg');
      const median = c.getAttribute('data-tooltip-median');
      const p25 = c.getAttribute('data-tooltip-p25');
      const p75 = c.getAttribute('data-tooltip-p75');
      const min = c.getAttribute('data-tooltip-min');
      const max = c.getAttribute('data-tooltip-max');
      const count = c.getAttribute('data-tooltip-count');
      
      if(!date) return; // データがない場合はスキップ
      
      if(!tooltipEl) {
        tooltipEl = document.createElement('div');
        tooltipEl.className = 'graph-tooltip';
        tooltipEl.style.display = 'none';
        document.body.appendChild(tooltipEl);
      }
      
      tooltipEl.innerHTML = [
        `<div class="tooltip-header">${escHtml(date)}</div>`,
        `<div class="tooltip-section">`,
        `<div class="tooltip-row"><span class="tooltip-label">平均</span><span class="tooltip-value">${escHtml(avg)}</span></div>`,
        `<div class="tooltip-row"><span class="tooltip-label">中央値</span><span class="tooltip-value">${escHtml(median)}</span></div>`,
        `</div>`,
        `<div class="tooltip-section">`,
        `<div class="tooltip-row"><span class="tooltip-label">下位25%点</span><span class="tooltip-value">${escHtml(p25)}</span></div>`,
        `<div class="tooltip-row"><span class="tooltip-label">上位25%点</span><span class="tooltip-value">${escHtml(p75)}</span></div>`,
        `</div>`,
        `<div class="tooltip-section">`,
        `<div class="tooltip-row"><span class="tooltip-label">最小</span><span class="tooltip-value">${escHtml(min)}</span></div>`,
        `<div class="tooltip-row"><span class="tooltip-label">最大</span><span class="tooltip-value">${escHtml(max)}</span></div>`,
        `</div>`,
        `<div class="tooltip-row" style="margin-top:8px; padding-top:8px; border-top:1px solid #f3f4f6;"><span class="tooltip-label">件数</span><span class="tooltip-value">${escHtml(count)}</span></div>`
      ].join('');
      
      // 座標計算（スクロール位置を考慮）
      updateTooltipPosition(c);
    });
    
    circle.addEventListener('mouseleave', (e) => {
      e.stopPropagation();
      if(tooltipEl) tooltipEl.style.display = 'none';
    });
    
    circle.addEventListener('mousemove', (e) => {
      e.stopPropagation();
      if(!tooltipEl || tooltipEl.style.display === 'none') return;
      updateTooltipPosition(e.target);
    });
  });
}

</script>
"""
    )
    html_parts.append("</head>")
    html_parts.append("<body>")
    
    # タイトルと日数・対象期間を横並びに
    if avg_rows:
        html_parts.append("<div style='display:flex; align-items:center; gap:16px; margin-bottom:10px;'>")
        html_parts.append(f"<h1 style='margin:0; line-height:1;'>{escape(title)}</h1>")
        html_parts.append("<div style='font-size:12px; color:#666; line-height:1; display:flex; align-items:center;'>")
        min_date = fmt_date_jp_with_weekday_html_no_color(min(r.checkin for r in avg_rows))
        max_date = fmt_date_jp_with_weekday_html_no_color(max(r.checkin for r in avg_rows))
        # ～の後に&nbsp;で半角スペースを確実に表示
        html_parts.append(f"<b>{min_date}</b> ～&nbsp;<b>{max_date}</b>")
        html_parts.append("</div>")
        html_parts.append("</div>")
    else:
        html_parts.append(f"<h1>{escape(title)}</h1>")
    
    # 生成日時を追加（毎回内容が変わるようにするため）
    generated_at = datetime.now().strftime(FMT_GENERATED_AT)
    html_parts.append(f"<p style='margin:4px 0 10px 0; font-size:12px; color:#666;'>生成日時: {escape(generated_at)}</p>")

    # Summary
    if avg_rows:
        html_parts.append("<div style='max-width:50%; margin-right:auto;'>")
        html_parts.append("<div class='card' style='padding:17px;'>")
        html_parts.append("<h2 style='font-size:13px; margin-top:0; margin-bottom:12px; font-weight:600;'>概要</h2>")
        
        # 価格フィルタ条件を概要の下に追加
        html_parts.append("<p style='margin:4px 0 0 0; font-size:11px; color:#666; line-height:1.6;'>レビュー4.84以下は除外、レビュー数10未満は除外、上限は直近1ヶ月45,000円、直近2ヶ月45,000円、通常期50,000円、3連休45,000円、繁忙期（お盆・正月・年末・GW）50,000円としています。</p>")
        
        # 1ヶ月単位の概要表
        html_parts.append("<div style='margin-top:12px;'>")
        html_parts.append("<h3 style='margin:0 0 8px 0; font-size:13px; font-weight:600;'>1ヶ月単位の推移</h3>")
        html_parts.append(html_monthly_summary(avg_rows, detail_stats_by_day))
        html_parts.append("</div>")
        html_parts.append("</div>")
        html_parts.append("</div>")

        html_parts.append("<div class='card' style='margin-top:12px; max-width:1100px;'>")
        html_parts.append('<div class="card-head">')
        html_parts.append("<h2 style='font-size:13px; margin-top:0; margin-bottom:12px; font-weight:600;'>日別価格グラフ</h2>")
        html_parts.append(
            """
<div class="legend">
  <div class="legend-item"><span class="swatch" style="border-top-color:#2563eb; border-top-width:3.5px"></span>平均</div>
  <div class="legend-item"><span class="swatch" style="border-top-color:#8b5cf6; border-top-width:2.8px"></span>中央値</div>
  <div class="legend-item"><span class="swatch" style="border-top-color:#10b981; border-top-width:2.2px; border-top-style:dashed"></span>下位25%点</div>
  <div class="legend-item"><span class="swatch" style="border-top-color:#f59e0b; border-top-width:2.2px; border-top-style:dashed"></span>上位25%点</div>
</div>
"""
        )
        html_parts.append("</div>")
        html_parts.append(svg_line_chart(avg_rows, detail_stats_by_day))
        html_parts.append(
            "<p class='note'>"
            "点にマウスを置くと日付・平均・中央値・下位25%点・上位25%点・件数が見られます。"
            "</p>"
        )
        html_parts.append("</div>")

        html_parts.append("<div class='card' style='margin-top:12px; max-width:1100px;'>")
        html_parts.append("<h2 style='font-size:13px; margin-top:0; margin-bottom:12px; font-weight:600;'>日別明細</h2>")
        html_parts.append("<div class='scroll-box avg'>")
        html_parts.append(html_table_avg(avg_rows, detail_stats_by_day))
        html_parts.append("</div>")
        html_parts.append("</div>")
    else:
        html_parts.append("<div class='card'><p>平均CSVが見つからないか、読み込めませんでした。</p></div>")

    # 明細は下に一覧表示せず、▶クリック時のモーダルで表示する
    if not details_rows:
        html_parts.append("<div class='card' style='margin-top:12px'><p>明細CSVが見つからないため、▶から明細を開けません。</p></div>")

    # モーダル（常時DOMに置いておく）
    html_parts.append(
        """
<div class="modal-backdrop" id="modalBackdrop" aria-hidden="true">
  <div class="modal" role="dialog" aria-modal="true" aria-label="明細">
    <div class="modal-header">
      <div class="modal-title" id="modalTitle">明細</div>
      <button type="button" class="modal-close" id="modalClose" aria-label="閉じる">×</button>
    </div>
    <div class="modal-body" id="modalBody">
      <div id="modalStats" style="margin-bottom:10px"></div>
      <div class="details-table-wrap">
        <table>
          <thead><tr><th style='text-align:center;'>No.</th><th style='text-align:center;'>価格</th><th style='text-align:center;'>タイトル</th><th style='text-align:center;'>レビュー</th><th style='text-align:center;'>レビュー数</th><th style='text-align:center;'>補足情報</th><th style='text-align:center;'>詳細</th><th style='text-align:center;'>備考</th></tr></thead>
          <tbody id="modalTableBody"></tbody>
        </table>
      </div>
    </div>
  </div>
</div>
"""
    )

    html_parts.append("</body></html>")

    out_path.parent.mkdir(parents=True, exist_ok=True)   # docs/ が無い場合は作成
    backup_existing_html(out_path)   # 既存の docs/index.html があれば、その更新日時で html/ に退避
    out_path.write_text("\n".join(html_parts), encoding="utf-8")
    print(f"HTMLを出力しました: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
