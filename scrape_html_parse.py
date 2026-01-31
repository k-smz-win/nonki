"""テキスト解析・URL組み立て・日付判定・価格閾値。"""

import re
from datetime import date, timedelta
from typing import Optional
from urllib.parse import quote

DESTINATION = "西九条駅"
ADULTS = 4
CHILDREN = 0
INFANTS = 0
PETS = 0
PRICE_MIN = 0
PRICE_MAX = None
PRICE_THRESHOLD_NORMAL = 50000
PRICE_THRESHOLD_HOLIDAY = 50000
PRICE_THRESHOLD_LONG_WEEKEND = 45000
PRICE_THRESHOLD_2MONTHS = 45000
PRICE_THRESHOLD_1MONTH = 45000


#Airbnb物件URLからroom IDを抽出する（重複判定用）
#
# 引数:
#   url (str): 物件URL
#
# 戻り値:
#   str  : 抽出したroom ID
#   None : URLが空、またはIDを取得できない場合
def extract_room_id_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    # /rooms/12345 の形式から数値部分を抽出
    match = re.search(r'/rooms/(\d+)', url)
    if match:
        return match.group(1)
    return None


# Airbnb検索用URLを組み立てる
#
# 引数:
#   checkin (date): チェックイン日
#   checkout (date): チェックアウト日
#
# 戻り値:
#   str: 組み立てた検索URL
def build_search_url(checkin: date, checkout: date) -> str:
    # チェックイン・チェックアウト・人数・価格範囲をクエリに組み立て
    params = {
        "checkin": checkin.isoformat(),
        "checkout": checkout.isoformat(),
        "adults": str(ADULTS),
        "children": str(CHILDREN),
        "infants": str(INFANTS),
        "pets": str(PETS),
    }
    if PRICE_MIN is not None:
        params["price_min"] = str(PRICE_MIN)
    if PRICE_MAX is not None:
        params["price_max"] = str(PRICE_MAX)
    query = "&".join([f"{k}={quote(v)}" for k, v in params.items()])
    return f"https://www.airbnb.jp/s/{quote(DESTINATION)}/homes?{query}"


# お盆期間か判定する（8/13-8/16）
#
# 引数:
#   d (date): 判定対象日
#
# 戻り値:
#   bool: Trueならお盆期間
def is_obon_period(d: date) -> bool:
    return d.month == 8 and 13 <= d.day <= 16


# 年末年始期間か判定する（12/29-1/3）
#
# 引数:
#   d (date): 判定対象日
#
# 戻り値:
#   bool: Trueなら年末年始期間
def is_new_year_period(d: date) -> bool:
    return (d.month == 12 and d.day >= 29) or (d.month == 1 and d.day <= 3)


# 年末の特定日か判定する（12/28-12/31）
#
# 引数:
#   d (date): 判定対象日
#
# 戻り値:
#   bool: Trueなら該当日
def is_year_end_period(d: date) -> bool:
    return d.month == 12 and 28 <= d.day <= 31


# ゴールデンウィーク期間か判定する（4/29-5/5）
#
# 引数:
#   d (date): 判定対象日
#
# 戻り値:
#   bool: Trueならゴールデンウィーク
def is_golden_week(d: date) -> bool:
    return d.month == 4 and d.day >= 29 or (d.month == 5 and d.day <= 5)


# 連続した週末などを長期連休として判定する
#
# 引数:
#   d (date): 判定対象日
#
# 戻り値:
#   bool: Trueなら長期連休に該当
def is_long_weekend(d: date) -> bool:
    weekday = d.weekday()
    prev_day = d - timedelta(days=1)
    next_day = d + timedelta(days=1)
    prev_weekday = prev_day.weekday()
    next_weekday = next_day.weekday()
    # 金土・土日月・日月のいずれかで連休とみなす
    if weekday == 5 and prev_weekday == 4:
        return True
    if weekday == 6 and prev_weekday == 5 and next_weekday == 0:
        return True
    if weekday == 0 and prev_weekday == 6:
        return True
    return False


# 日付に応じた価格閾値を返す（祝日・1ヶ月/2ヶ月/長期連休などを考慮）
#
# 引数:
#   d (date): 対象日
#
# 戻り値:
#   int: 価格閾値（円）
def get_price_threshold(d: date) -> int:
    today = date.today()
    days_until = (d - today).days
    # 繁忙期 → 祝日閾値 / 直近30日・60日 → 特例閾値 / 60日超 → 通常 or 3連休閾値
    if is_obon_period(d) or is_new_year_period(d) or is_year_end_period(d) or is_golden_week(d):
        return PRICE_THRESHOLD_HOLIDAY
    elif 0 <= days_until <= 30:
        return PRICE_THRESHOLD_1MONTH
    elif 0 <= days_until <= 60:
        return PRICE_THRESHOLD_2MONTHS
    elif days_until > 60 and is_long_weekend(d):
        return PRICE_THRESHOLD_LONG_WEEKEND
    elif days_until > 60:
        return PRICE_THRESHOLD_NORMAL
    else:
        return PRICE_THRESHOLD_NORMAL


# テキストから金額（円）を抽出する
#
# 引数:
#   text (str): 対象テキスト
#
# 戻り値:
#   int or None: 抽出した金額（見つからなければ None）
def pick_price_from_text(text: str) -> Optional[int]:
    t = text.replace("\n", " ").strip()
    yen = r"[¥￥]"
    sp = r"[\s\u00a0]*"
    # ¥12,345/泊 や 1泊あたり¥12,345 などをマッチ
    patterns = [
        rf"{yen}{sp}([\d,]+){sp}(?:/|／){sp}泊",
        rf"{yen}{sp}([\d,]+){sp}（{sp}1泊{sp}）",
        rf"1泊あたり{sp}{yen}{sp}([\d,]+)",
        rf"{yen}{sp}([\d,]+){sp}泊",
    ]
    # パターンを順に試して金額を抽出
    for pat in patterns:
        m = re.search(pat, t)
        if m:
            return int(m.group(1).replace(",", ""))
    yen_matches = list(re.finditer(rf"{yen}{sp}([\d,]+)", t))
    #
    if not yen_matches:
        return None
    for m in yen_matches:
        start = m.start()
        ctx = t[max(0, start - 10) : start]
        if "合計" in ctx:
            continue
        return int(m.group(1).replace(",", ""))
    return int(yen_matches[0].group(1).replace(",", ""))


# テキストからゲスト数を抽出する
#
# 引数:
#   text (str): 対象テキスト
#
# 戻り値:
#   int or None: 抽出したゲスト数（見つからなければ None）
def pick_guests_from_text(text: str) -> Optional[int]:
    t = text.replace("\n", " ").strip()
    patterns = [
        r"(\d+)\s*名",
        r"(\d+)\s*guests?",
        r"(\d+)\s*人(?:まで)?",
        r"定員[：:]\s*(\d+)",
        r"最大[：:]\s*(\d+)",
        r"(\d+)\s*人まで",
    ]
    # パターンを順に試してゲスト数を抽出
    for pat in patterns:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


# テキストから寝室数を抽出する
#
# 引数:
#   text (str): 対象テキスト
#
# 戻り値:
#   int or None: 抽出した寝室数（見つからなければ None）
def pick_bedrooms_from_text(text: str) -> Optional[int]:
    t = text.replace("\n", " ").strip()
    patterns = [
        r"(\d+)\s*bedrooms?",
        r"(\d+)\s*寝室",
        r"(\d+)\s*BR",
    ]
    # パターンを順に試して寝室数を抽出
    for pat in patterns:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


# テキストからベッド数を抽出する
#
# 引数:
#   text (str): 対象テキスト
#
# 戻り値:
#   int or None: 抽出したベッド数（見つからなければ None）
def pick_beds_from_text(text: str) -> Optional[int]:
    t = text.replace("\n", " ").strip()
    patterns = [
        r"(\d+)\s*beds?",
        r"(\d+)\s*ベッド",
    ]
    # パターンを順に試してベッド数を抽出
    for pat in patterns:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


# テキストからレビュー件数を抽出する
#
# 引数:
#   text (str): 対象テキスト
#
# 戻り値:
#   int or None: 抽出したレビュー件数（見つからなければ None）
def pick_reviews_count_from_text(text: str) -> Optional[int]:
    t = text.replace("\n", " ").strip()
    patterns = [
        r"\((\d+)\)",
        r"(\d+\.?\d*)\s*\((\d+)\)",
        r"レビュー\s*(\d+)\s*件",
        r"(\d+)\s*reviews?",
        r"(\d+)\s*件のレビュー",
        r"(\d+)\s*レビュー",
        r"レビュー[：:]\s*(\d+)",
    ]
    # 複数の表記パターンを順に試してレビュー件数を抽出
    for pat in patterns:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            try:
                if len(m.groups()) >= 2:
                    return int(m.group(2))
                else:
                    return int(m.group(1))
            except (ValueError, IndexError):
                pass
    return None


# テキストから評価（rating）を抽出する
#
# 引数:
#   text (str): 対象テキスト
#
# 戻り値:
#   float or None: 抽出した評価（見つからなければ None）
def pick_rating_from_text(text: str) -> Optional[float]:
    t = text.replace("\n", " ").strip()
    patterns = [
        r"\d+つ星中\s*(\d+\.?\d*)\s*つ星",
        r"(\d+\.?\d*)\s*\((\d+)\)",
        r"[★⭐]\s*(\d+\.?\d*)",
        r"^(\d+\.?\d*)\s*\(",
        r"(?<!\d+つ星中\s*)(\d+\.?\d*)\s*つ星",
        r"(\d+\.?\d*)\s*stars?",
        r"(\d+\.?\d*)\s*点",
        r"評価[：:]\s*(\d+\.?\d*)",
        r"^(\d+\.\d+)$",
        r"^([1-5])$",
    ]
    # 表記パターンを順に試して評価値を抽出
    for pat in patterns:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            try:
                rating_value = float(m.group(1))
                if 0 < rating_value <= 5.0:
                    return rating_value
            except (ValueError, IndexError):
                pass
    return None
