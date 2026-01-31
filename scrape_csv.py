"""CSV出力系。既存ファイルの退避・平均CSV・明細CSVへの書き込み。"""

import csv
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

# 出力先
DATA_DIR = Path("data")
OUTPUT_CSV = DATA_DIR / "konohana_daily_avg.csv"
OUTPUT_DETAIL_CSV = DATA_DIR / "konohana_daily_details.csv"
FMT_DATED_SUFFIX = "%Y%m%d_%H%M%S"


# 既存 CSV を日時付きで退避（上書き防止）
#
# 引数:
#   （なし）
#
# 戻り値:
#   None
def backup_existing_csvs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    for p in (OUTPUT_CSV, OUTPUT_DETAIL_CSV):
        if p.exists():
            # mtime で日付付きファイル名を生成し data/ 内にリネーム退避
            stem, ext = p.stem, p.suffix
            mtime = p.stat().st_mtime
            ts = datetime.fromtimestamp(mtime).strftime(FMT_DATED_SUFFIX)
            dest = DATA_DIR / f"{stem}_{ts}{ext}"
            p.rename(dest)
            print(f"  前回分を履歴に退避: {p.name} → {dest.name}")


# 平均 CSV のヘッダー行を書き込む
#
# 引数:
#   writer (csv.writer): CSV ライター
#
# 戻り値:
#   None
def write_avg_header(writer: csv.writer) -> None:
    writer.writerow(["checkin", "avg_price_yen", "count", "min_price_yen", "max_price_yen", "url"])


# 明細 CSV のヘッダー行を書き込む
#
# 引数:
#   writer (csv.writer): CSV ライター
#
# 戻り値:
#   None
def write_detail_header(writer: csv.writer) -> None:
    writer.writerow(
        [
            "checkin",
            "price_yen",
            "listing_url",
            "raw_label",
            "title",
            "guests",
            "bedrooms",
            "beds",
            "reviews_count",
            "rating",
            "subtitle",
        ]
    )


# 1日分の平均行を書き込む
#
# 引数:
#   writer (csv.writer): CSV ライター
#   checkin (str): チェックイン日（ISO形式）
#   avg_price (Any): 平均価格
#   count (int): 件数
#   min_price (Any): 最安価格
#   max_price (Any): 最高価格
#   url (str): 検索 URL
#
# 戻り値:
#   None
def write_avg_row(
    writer: csv.writer,
    checkin: str,
    avg_price: Any,
    count: int,
    min_price: Any,
    max_price: Any,
    url: str,
) -> None:
    writer.writerow([checkin, avg_price, count, min_price, max_price, url])


# 1日分の明細行を書き込む
#
# 引数:
#   writer (csv.writer): CSV ライター
#   checkin (str): チェックイン日（ISO形式）
#   details (List[Dict]): 明細行のリスト
#
# 戻り値:
#   None
def write_detail_rows(writer: csv.writer, checkin: str, details: List[Dict[str, Any]]) -> None:
    # 各明細を1行ずつ書き込む
    for row in details:
        writer.writerow(
            [
                checkin,
                row.get("price_yen"),
                row.get("listing_url", ""),
                row.get("raw_label", ""),
                row.get("title", ""),
                row.get("guests"),
                row.get("bedrooms"),
                row.get("beds"),
                row.get("reviews_count"),
                row.get("rating"),
                row.get("subtitle", ""),
            ]
        )


