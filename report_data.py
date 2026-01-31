"""データ読み込み・集計。CSV解析、統計算出。"""

import csv
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import DefaultDict, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class AvgRow:
    checkin: date
    avg_price_yen: Optional[int]
    count: int
    min_price_yen: Optional[int]
    max_price_yen: Optional[int]
    url: str


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


# 文字列を int に変換。空や "none" は None
#
# 引数:
#   value (str): 変換対象の文字列
#
# 戻り値:
#   int or None: 変換結果。失敗時は None
def _parse_int(value: str) -> Optional[int]:
    v = (value or "").strip()
    # 空または "none" は None
    if v == "" or v.lower() == "none":
        return None
    try:
        return int(v)
    except ValueError:
        return None


# 文字列を float に変換。空や "none" は None
#
# 引数:
#   value (str): 変換対象の文字列
#
# 戻り値:
#   float or None: 変換結果。失敗時は None
def _parse_float(value: str) -> Optional[float]:
    v = (value or "").strip()
    # 空または "none" は None
    if v == "" or v.lower() == "none":
        return None
    try:
        return float(v)
    except ValueError:
        return None


# ISO日付文字列を date に変換。空は None
#
# 引数:
#   value (str): ISO形式の日付文字列
#
# 戻り値:
#   date or None: 変換結果。失敗時は None
def _parse_date(value: str) -> Optional[date]:
    v = (value or "").strip()
    if not v:
        return None
    # ISO形式（YYYY-MM-DD）でパース
    try:
        return datetime.fromisoformat(v).date()
    except ValueError:
        return None


# 整数リストの平均を返す（四捨五入）。空は None
#
# 引数:
#   values (list): 整数のリスト
#
# 戻り値:
#   int or None: 平均値。空リスト時は None
def mean_int(values: list) -> Optional[int]:
    if not values:
        return None
    return round(statistics.mean(values))


# 整数リストの中央値を返す。空は None
#
# 引数:
#   values (list): 整数のリスト
#
# 戻り値:
#   int or None: 中央値。空リスト時は None
def median_int(values: list) -> Optional[int]:
    if not values:
        return None
    return round(statistics.median(values))


# 整数リストの分位点を返す。空は None
#
# 引数:
#   values (list): 整数のリスト
#   p (float): 分位点（0〜1）
#
# 戻り値:
#   int or None: 分位点の値。空リスト時は None
def quantile(values: list, p: float) -> Optional[int]:
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    # 線形補間で分位点を計算
    i = (len(xs) - 1) * p
    lo = int(i)
    hi = min(lo + 1, len(xs) - 1)
    w = i - lo
    return round(xs[lo] * (1 - w) + xs[hi] * w)


# 平均CSVを読み込み AvgRow リストとして返す（checkin 順）
#
# 引数:
#   path (Path): 平均CSVパス
#
# 戻り値:
#   List[AvgRow]: checkin 順にソート済み
def read_avg_csv(path: Path) -> List[AvgRow]:
    rows: List[AvgRow] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            # checkin 必須。パース失敗行はスキップ
            d = _parse_date(r.get("checkin", ""))
            if d is None:
                continue
            rows.append(
                AvgRow(
                    checkin=d,
                    avg_price_yen=_parse_int(r.get("avg_price_yen", "")),
                    count=int(r.get("count", "0") or 0),
                    min_price_yen=_parse_int(r.get("min_price_yen", "")),
                    max_price_yen=_parse_int(r.get("max_price_yen", "")),
                    url=r.get("url", "") or "",
                )
            )
    # checkin 昇順でソート
    return sorted(rows, key=lambda x: x.checkin)


# 明細CSVを読み込み DetailRow リストとして返す。checkin・価格必須、欠損行はスキップ
#
# 引数:
#   path (Path): 明細CSVパス
#
# 戻り値:
#   List[DetailRow]: checkin, price_yen, listing_url でソート済み
def read_details_csv(path: Path) -> List[DetailRow]:
    rows: List[DetailRow] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            # checkin と price_yen 必須。欠損行はスキップ
            d = _parse_date(r.get("checkin", ""))
            p = _parse_int(r.get("price_yen", ""))
            if d is None or p is None:
                continue
            rows.append(
                DetailRow(
                    checkin=d,
                    price_yen=p,
                    listing_url=r.get("listing_url", "") or "",
                    raw_label=r.get("raw_label", "") or "",
                    title=r.get("title", "") or "",
                    guests=_parse_int(r.get("guests", "")),
                    bedrooms=_parse_int(r.get("bedrooms", "")),
                    beds=_parse_int(r.get("beds", "")),
                    reviews_count=_parse_int(r.get("reviews_count", "")),
                    rating=_parse_float(r.get("rating", "")),
                    subtitle=r.get("subtitle", "") or None,
                )
            )
    # checkin, price_yen, listing_url の順でソート
    return sorted(rows, key=lambda x: (x.checkin, x.price_yen, x.listing_url))


# AvgRow を年月でグループ化
#
# 引数:
#   rows (List[AvgRow]): 平均行リスト
#
# 戻り値:
#   Dict[(year, month), List[AvgRow]]: 年月ごとの AvgRow リスト
def group_by_month(rows: List[AvgRow]) -> Dict[Tuple[int, int], List[AvgRow]]:
    by_month: Dict[Tuple[int, int], List[AvgRow]] = defaultdict(list)
    for r in rows:
        key = (r.checkin.year, r.checkin.month)
        by_month[key].append(r)
    # 年月順にソートして返す
    return dict(sorted(by_month.items()))


# 日付ごとの中央値・p25・p75・min・max を算出
#
# 引数:
#   details_rows (List[DetailRow]): 明細行リスト
#
# 戻り値:
#   Dict[date, Dict]: 日別統計（median, p25, p75, min, max）
def build_detail_stats_by_day(details_rows: List[DetailRow]) -> Dict[date, Dict[str, Optional[int]]]:
    # 日付ごとに価格リストを集約
    by_day: DefaultDict[date, List[int]] = defaultdict(list)
    for r in details_rows:
        by_day[r.checkin].append(r.price_yen)

    # 各日の中央値・分位点・min・max を算出
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
