"""
Airbnb 検索結果をスクレイプし、日別の平均・明細を CSV で出力するスクリプト。

【目的】
  今日から DAYS_AHEAD 日先まで「1泊」の検索結果を日別に取得し、data/ に平均CSV・明細CSVを出力する。
  report.py がこのCSVを読み、HTMLレポートを生成する前提。

【前提条件】
  - Edge ドライバ（msedgedriver.exe）がカレントまたは PATH に存在すること。
  - Selenium が Edge を起動できること（ブラウザがインストール済みであること）。
  - Airbnb の検索ページが開き、DOM が本スクリプトの XPath と一致すること（UI 変更で動かなくなる可能性あり）。

【入力データの意味】
  - 入力は「日付範囲」のみ（start=今日、end=今日+DAYS_AHEAD）。検索条件（人数・目的地・価格等）は定数で指定。
  - 各日ごとに build_search_url で検索URLを組み立て、driver.get で遷移してカードから価格・物件情報を抽出する。

【出力の意味】
  - data/konohana_daily_avg.csv: 日別の checkin, avg_price_yen, count, min_price_yen, max_price_yen, url。
  - data/konohana_daily_details.csv: 物件ごとの checkin, price_yen, listing_url, raw_label, title, guests, bedrooms, beds, reviews_count, rating, subtitle。
  - 既存のCSVは実行前に data/<stem>_YYYYMMDD_HHMMSS.csv にリネームして退避する（上書き防止）。

【例外・エラー時の考え方】
  - ドライバ起動失敗: 未捕捉ならトレースバックで終了。msedgedriver のパス・Edge のバージョンを確認する。
  - 検索ページでカードが0件・タイムアウト: その日は details=[] のまま平均は None、件数0でCSVに書き、次の日に進む（処理は止めない）。
  - 価格・レビュー等の抽出失敗: その物件はスキップするか、該当フィールドを None/空で出力。全体は続行。
  - 次ページボタンが見つからない: その日は取得できたページまでで打ち切り、ループを抜けて次の日に進む。

【なぜこの実装になっているか】
  - 価格は span[@aria-label] の「（1泊）」付きテキストから正規表現で抽出している理由: Airbnb のDOMで価格が aria-label に含まれるため。HTML の class は変更されやすいが aria-label は比較的安定。
  - 日ごとに driver.get で検索URLを開いている理由: 日付ごとの結果を確実に取得するため。1回の検索で複数日を取るAPIは使わず、画面をシミュレートする。
  - 高額物件を閾値で除外している理由: 8人以上向けなど「条件外」の物件を省き、4人用の相場に近い値を出すため。閾値は日付・繁忙期で変える（get_price_threshold）。
  - レビュー4.84以下・10未満を除外している理由: 品質の低い物件や評価が少ない物件をレポートから外し、参考になりやすい物件だけを集計するため。
"""

import csv
import re
import time
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote
from selenium import webdriver
from selenium.webdriver.edge.service import Service
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# =============================================================================
# 定数定義（取得条件・パス・URL・フォーマットはここで変更する）
# =============================================================================
# 前提: report.py の DEFAULT_AVG_PATH / DEFAULT_DETAILS_PATH とファイル名を合わせる。

# --- 取得条件（Airbnb の条件付きURLに遷移する方が安定。URLに checkin/checkout/adults 等が含まれる）---
DESTINATION = "西九条駅"   # 検索エリア。URL の /s/<destination>/homes に使う
ADULTS = 4
CHILDREN = 0
INFANTS = 0
PETS = 0

DAYS_AHEAD = 120  # 今日から何日先まで取得するか（約4ヶ月）。長いほど処理時間は増える
SLEEP_BETWEEN_DAYS_SEC = 1.5  # 日付を変えるたびに待つ秒数。短すぎるとブロックされる可能性あり
SCROLL_TIMES = 5  # 1ページ内で追加スクロールする回数（遅延読み込みのカードを表示するため）。0でしない
SCROLL_WAIT_SEC = 1.0  # スクロール後の待機秒数

# 価格フィルタ（検索URLに付与。None/0 で無効）
PRICE_MIN = 0  # この価格未満の物件は検索結果に含めない（0で無効）
PRICE_MAX = None  # この価格超過は除外（Noneで無効）

# レビューフィルタ（抽出後に適用。閾値以下は明細・平均から除外）
RATING_MIN = 4.84  # 評価がこの値以下は除外（4.85以上のみ残す。境界は「より良い物件」を残すため）
REVIEWS_COUNT_MIN = 10  # レビュー数がこの値未満は除外（信頼性の低い物件を省く）

# 価格ベースのフィルタ（高額物件＝大人数向けとみなし除外）。get_price_threshold で日付に応じて選択される
PRICE_THRESHOLD_NORMAL = 50000   # 通常期（60日以上先かつ連休でない日）
PRICE_THRESHOLD_HOLIDAY = 50000  # 繁忙期（お盆・正月・年末・GW）
PRICE_THRESHOLD_LONG_WEEKEND = 45000  # 3連休以上（60日以上先で連休の日）
PRICE_THRESHOLD_2MONTHS = 45000  # 直近2ヶ月（30日超～60日以内）
PRICE_THRESHOLD_1MONTH = 45000   # 直近1ヶ月（30日以内）

# ページネーション: 1日あたりこの件数以上取れたら打ち切り。最大で MAX_PAGES までページ送りする
MIN_LISTINGS_PER_DAY = 20
MAX_PAGES = 5

# --- 出力先（data/ に格納。既存CSVは実行前に日時付きでリネーム退避）---
DATA_DIR = Path("data")
OUTPUT_CSV = DATA_DIR / "konohana_daily_avg.csv"        # report.py の DEFAULT_AVG_PATH と一致させる
OUTPUT_DETAIL_CSV = DATA_DIR / "konohana_daily_details.csv"  # report.py の DEFAULT_DETAILS_PATH と一致させる
FMT_DATED_SUFFIX = "%Y%m%d_%H%M%S"   # 退避ファイル名の日時部分

# --- ブラウザ・URL ---
EDGE_DRIVER_NAME = "msedgedriver.exe"   # 同梱または PATH に必要
AIRBNB_TOP_URL = "https://www.airbnb.jp/"   # 初回に開くページ（Cookie同意等のため）
SLEEP_AFTER_OPEN_SEC = 3   # トップページ表示後の待機（DOM安定のため）

# =============================================================================
# 前処理（Edge 起動・Airbnb 開く・data 作成・既存CSV退避）
# =============================================================================
# 前提: msedgedriver が利用可能であること。失敗時はここで例外となりスクリプト終了。

options = Options()
options.add_argument("--start-maximized")
options.add_argument("--disable-webrtc")
options.add_argument("--log-level=3")
service = Service(EDGE_DRIVER_NAME)
driver = webdriver.Edge(service=service, options=options)
driver.get(AIRBNB_TOP_URL)
time.sleep(SLEEP_AFTER_OPEN_SEC)

def maybe_accept_cookies() -> None:
    # 表示されることがあるCookieバナーを閉じる（失敗しても無視）
    try:
        btns = driver.find_elements(
            By.XPATH,
            '//button[contains(., "すべて承諾") or contains(., "すべてを承諾") or contains(., "同意") or contains(., "許可") or contains(., "Accept")]',
        )
        if btns:
            btns[0].click()
            time.sleep(1)
    except Exception:
        pass


def close_popups() -> None:
    """ポップアップやモーダルダイアログを閉じる（失敗しても無視）"""
    try:
        # OKボタンを探す
        ok_buttons = driver.find_elements(
            By.XPATH,
            '//button[contains(., "OK") or contains(., "ok") or contains(., "了解") or contains(., "閉じる") or contains(., "×") or contains(., "✕")] | '
            '//button[@aria-label="閉じる"] | '
            '//button[@aria-label="Close"] | '
            '//button[contains(@class, "close")] | '
            '//button[contains(@class, "modal-close")] | '
            '//div[contains(@class, "modal")]//button[contains(., "OK")] | '
            '//div[contains(@class, "dialog")]//button[contains(., "OK")]'
        )
        for btn in ok_buttons:
            try:
                if btn.is_displayed() and btn.is_enabled():
                    btn.click()
                    time.sleep(0.5)
                    print("  -> ポップアップを閉じました")
                    break
            except Exception:
                continue
    except Exception:
        pass
    
    try:
        # ESCキーで閉じる（モーダルが開いている場合）
        from selenium.webdriver.common.keys import Keys
        body = driver.find_element(By.TAG_NAME, "body")
        body.send_keys(Keys.ESCAPE)
        time.sleep(0.5)
    except Exception:
        pass
    
    try:
        # オーバーレイをクリックして閉じる
        overlays = driver.find_elements(
            By.XPATH,
            '//div[contains(@class, "overlay")] | '
            '//div[contains(@class, "backdrop")] | '
            '//div[contains(@role, "dialog")]//div[contains(@class, "close")]'
        )
        for overlay in overlays:
            try:
                if overlay.is_displayed():
                    overlay.click()
                    time.sleep(0.5)
                    break
            except Exception:
                continue
    except Exception:
        pass


def extract_room_id_from_url(url: str) -> Optional[str]:
    """URLからroom IDを抽出する（重複チェック用）"""
    if not url:
        return None
    # URL形式: https://www.airbnb.jp/rooms/1423919132995183693?adults=4&...
    # または: /rooms/1423919132995183693?adults=4&...
    match = re.search(r'/rooms/(\d+)', url)
    if match:
        return match.group(1)
    return None


# 検索URLを組み立てる。日付・人数等は定数を使用。report.py の build_search_url は環境変数も参照するが、ここでは定数のみ。
def build_search_url(checkin: date, checkout: date) -> str:
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


def is_obon_period(d: date) -> bool:
    """お盆期間かどうか（8月13日〜16日）"""
    return d.month == 8 and 13 <= d.day <= 16


def is_new_year_period(d: date) -> bool:
    """正月期間かどうか（12月29日〜1月3日）"""
    return (d.month == 12 and d.day >= 29) or (d.month == 1 and d.day <= 3)


def is_year_end_period(d: date) -> bool:
    """年末期間かどうか（12月28日〜31日）"""
    return d.month == 12 and 28 <= d.day <= 31


def is_golden_week(d: date) -> bool:
    """ゴールデンウィーク期間かどうか（4月29日〜5月5日）"""
    return d.month == 4 and d.day >= 29 or (d.month == 5 and d.day <= 5)


def is_long_weekend(d: date) -> bool:
    """3連休以上かどうか（土日を含む連続した休日をチェック）"""
    weekday = d.weekday()  # 0=月, 4=金, 5=土, 6=日
    
    # 前後2日間をチェックして、連続した休日があるか確認
    # 金-土-日、土-日-月、日-月-火などのパターンをチェック
    prev_day = d - timedelta(days=1)
    next_day = d + timedelta(days=1)
    prev_weekday = prev_day.weekday()
    next_weekday = next_day.weekday()
    
    # 金-土-日パターン
    if weekday == 5 and prev_weekday == 4:  # 土曜日で前日が金曜日
        return True
    # 土-日-月パターン
    if weekday == 6 and prev_weekday == 5 and next_weekday == 0:  # 日曜日で前日が土曜日、翌日が月曜日
        return True
    # 日-月-火パターン（月曜日が祝日の場合）
    if weekday == 0 and prev_weekday == 6:  # 月曜日で前日が日曜日
        return True
    
    return False


# 指定日の「この価格以上の物件は除外する」閾値を返す。直近ほど・連休は閾値を下げ、繁忙期は上げる。理由: 直近は相場が低め、繁忙期は高めになるため。
def get_price_threshold(d: date) -> int:
    """日付に応じた価格閾値を返す"""
    today = date.today()
    days_until = (d - today).days
    
    # 優先順位1: お盆・正月・年末・GW（最優先）
    if is_obon_period(d) or is_new_year_period(d) or is_year_end_period(d) or is_golden_week(d):
        return PRICE_THRESHOLD_HOLIDAY
    # 優先順位2: 直近1ヶ月以内（30日以内）
    elif 0 <= days_until <= 30:
        return PRICE_THRESHOLD_1MONTH
    # 優先順位3: 直近2ヶ月以内（60日以内）
    elif 0 <= days_until <= 60:
        return PRICE_THRESHOLD_2MONTHS
    # 優先順位4: 3連休以上（上記以外、つまり60日以上先で、かつ連休の場合）
    elif days_until > 60 and is_long_weekend(d):
        return PRICE_THRESHOLD_LONG_WEEKEND
    # 優先順位5: それ以外（60日以上先で、かつ連休でない場合）
    elif days_until > 60:
        return PRICE_THRESHOLD_NORMAL
    # フォールバック（過去の日付など）
    else:
        return PRICE_THRESHOLD_NORMAL


# 物件カードのリンク（/rooms/ を含む a 要素）を拾う XPath。DOM 変更に備え複数パターンを OR で列挙している。
def listing_anchors_xpath() -> str:
    # 例: <a target="listing_..." href="/rooms/...?...check_in=...&check_out=...">。rooms/ を拾いつつ、target="listing_" があれば優先
    return (
        '//a[contains(@href, "/rooms/") and contains(@target, "listing_")]'
        ' | //a[contains(@href, "/rooms/") and @aria-labelledby]'
        ' | //a[contains(@href, "/rooms/")]'
    )


def card_candidates_xpath() -> str:
    # Airbnb側のDOM変更に強くするため、複数パターンをまとめて候補にする
    return (
        listing_anchors_xpath()
        + ' | //div[@itemprop="itemListElement"]'
        + ' | //div[@data-testid="card-container"]'
        + ' | //div[contains(@data-testid, "property-card")]'
    )


def price_span_in_card_xpath() -> str:
    # 例: aria-label="¥ 10,000 （1泊）, 割引前は¥ 20,283です"
    return (
        './/span[@aria-label and ('
        'contains(@aria-label, "（1泊）") or contains(@aria-label, "1泊") or contains(@aria-label, "/泊")'
        ')]'
    )


def price_span_anywhere_xpath() -> str:
    # 価格そのものが span[aria-label] に入っているケースがあるので、ページ全体から拾えるようにする
    return (
        '//span[@aria-label and ('
        'contains(@aria-label, "（1泊）") or contains(@aria-label, "1泊") or contains(@aria-label, "/泊")'
        ')]'
    )


# テキスト（aria-label や .text）から「1泊あたりの価格」を円で抽出。合計金額は避け、「¥xx,xxx/泊」等のパターンを優先する。
def pick_price_from_text(text: str) -> Optional[int]:
    t = text.replace("\n", " ").strip()

    # “1泊”に紐づく価格を優先（検索は1泊固定のため）
    yen = r"[¥￥]"
    sp = r"[\s\u00a0]*"  # NBSP対策
    patterns = [
        rf"{yen}{sp}([\d,]+){sp}(?:/|／){sp}泊",
        rf"{yen}{sp}([\d,]+){sp}（{sp}1泊{sp}）",
        rf"1泊あたり{sp}{yen}{sp}([\d,]+)",
        rf"{yen}{sp}([\d,]+){sp}泊",
    ]
    for pat in patterns:
        m = re.search(pat, t)
        if m:
            return int(m.group(1).replace(",", ""))

    # fallback: 最初の「¥/￥ xx,xxx」ただし直前に「合計」があるものは避ける
    yen_matches = list(re.finditer(rf"{yen}{sp}([\d,]+)", t))
    if not yen_matches:
        return None

    for m in yen_matches:
        start = m.start()
        ctx = t[max(0, start - 10) : start]
        if "合計" in ctx:
            continue
        return int(m.group(1).replace(",", ""))

    # 全部合計っぽいなら最初を返す
    return int(yen_matches[0].group(1).replace(",", ""))


def pick_guests_from_text(text: str) -> Optional[int]:
    """テキストから定員（人数）を抽出。例: "6名" -> 6, "8 guests" -> 8, "8人まで" -> 8"""
    t = text.replace("\n", " ").strip()
    # パターン: "○名", "○ guests", "○人", "○人まで", "定員○名"
    patterns = [
        r"(\d+)\s*名",
        r"(\d+)\s*guests?",
        r"(\d+)\s*人(?:まで)?",
        r"定員[：:]\s*(\d+)",
        r"最大[：:]\s*(\d+)",
        r"(\d+)\s*人まで",
    ]
    for pat in patterns:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


def pick_bedrooms_from_text(text: str) -> Optional[int]:
    """テキストから寝室数を抽出。例: "2 bedrooms" -> 2, "2寝室" -> 2"""
    t = text.replace("\n", " ").strip()
    patterns = [
        r"(\d+)\s*bedrooms?",
        r"(\d+)\s*寝室",
        r"(\d+)\s*BR",
    ]
    for pat in patterns:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


def pick_beds_from_text(text: str) -> Optional[int]:
    """テキストからベッド数を抽出。例: "3 beds" -> 3, "3ベッド" -> 3"""
    t = text.replace("\n", " ").strip()
    patterns = [
        r"(\d+)\s*beds?",
        r"(\d+)\s*ベッド",
    ]
    for pat in patterns:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


def pick_reviews_count_from_text(text: str) -> Optional[int]:
    """テキストからレビュー数を抽出。例: "123 reviews" -> 123, "123件のレビュー" -> 123, "レビュー21件" -> 21, "(21)" -> 21"""
    t = text.replace("\n", " ").strip()
    patterns = [
        r"\((\d+)\)",  # "(21)" の形式（最優先、評価の後に来ることが多い）
        r"(\d+\.?\d*)\s*\((\d+)\)",  # "4.62 (21)" の形式（2番目の数値をレビュー数として取得）
        r"レビュー\s*(\d+)\s*件",  # "レビュー21件" の形式
        r"(\d+)\s*reviews?",
        r"(\d+)\s*件のレビュー",
        r"(\d+)\s*レビュー",
        r"レビュー[：:]\s*(\d+)",
    ]
    for pat in patterns:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            try:
                # パターンが2つのグループを持つ場合（例: "4.62 (21)"）、2番目を取得
                if len(m.groups()) >= 2:
                    return int(m.group(2))
                else:
                    return int(m.group(1))
            except (ValueError, IndexError):
                pass
    return None


def pick_rating_from_text(text: str) -> Optional[float]:
    """テキストから評価（星の数）を抽出。例: "4.5" -> 4.5, "4.5 stars" -> 4.5, "4.62 (21)" -> 4.62, "★4.62" -> 4.62, "5つ星中4.29つ星" -> 4.29, "4.8" -> 4.8, "5.0" -> 5.0, "5" -> 5.0"""
    t = text.replace("\n", " ").strip()
    patterns = [
        # 最優先: "Xつ星中Yつ星" の形式（Xは可変、Yが実際の評価値）
        r"\d+つ星中\s*(\d+\.?\d*)\s*つ星",
        # 次優先: "Y (Z)" の形式（Yが評価値、Zがレビュー数）
        r"(\d+\.?\d*)\s*\((\d+)\)",
        # 次優先: "★Y" や "⭐Y" の形式
        r"[★⭐]\s*(\d+\.?\d*)",
        # "Y (" の形式（括弧の前の数値）
        r"^(\d+\.?\d*)\s*\(",
        # "Yつ星" の形式（ただし「Xつ星中」の前にある場合は除外）
        r"(?<!\d+つ星中\s*)(\d+\.?\d*)\s*つ星",
        r"(\d+\.?\d*)\s*stars?",
        r"(\d+\.?\d*)\s*点",
        r"評価[：:]\s*(\d+\.?\d*)",
        # 単独の数値（小数点を含む場合は評価値の可能性が高い、5.0も含む）
        r"^(\d+\.\d+)$",  # 小数点を含む数値のみ（5.0も含む）
        # 整数の1-5も評価値として認識（5.0が整数の5として表示される場合に対応）
        r"^([1-5])$",  # 単独の整数1-5
    ]
    for pat in patterns:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            try:
                # グループ1が評価値（パターンによってはグループ2がレビュー数）
                rating_str = m.group(1)
                rating_value = float(rating_str)
                # 評価値が0より大きく5以下の範囲内であることを確認（5.0も含む）
                if 0 < rating_value <= 5.0:
                    return rating_value
            except (ValueError, IndexError):
                pass
    return None


def extract_listing_details_from_container(container) -> Dict[str, Optional[object]]:
    """コンテナから物件の詳細情報（ゲスト数、寝室数、ベッド数、レビュー数、評価、補足情報）を抽出"""
    details = {
        "guests": None,
        "bedrooms": None,
        "beds": None,
        "reviews_count": None,
        "rating": None,
        "subtitle": None,  # 補足情報（ベッド2・布団1組・寝室1など）
    }
    
    try:
        container_text = container.text or ""
        container_html = container.get_attribute("innerHTML") or ""
        
        # デバッグ: コンテナのテキストの最初の200文字を確認（必要に応じてコメントアウト）
        # print(f"DEBUG container_text (first 200 chars): {container_text[:200]}")
        
        # デバッグ: data-testid="listing-card-title"の存在を確認（必要に応じてコメントアウト）
        # try:
        #     title_elems = container.find_elements(By.XPATH, ".//*[@data-testid='listing-card-title']")
        #     if title_elems:
        #         for te in title_elems:
        #             title_text_debug = te.text or te.get_attribute("innerText") or te.get_attribute("textContent") or ""
        #             print(f"DEBUG found listing-card-title: '{title_text_debug[:50]}'")
        #     else:
        #         print(f"DEBUG listing-card-title not found in container")
        # except Exception as e:
        #     print(f"DEBUG listing-card-title search error: {e}")
        
        # ゲスト数 - より具体的な要素を探す（より広範囲に）
        # まず検索パラメータから取得を試みる（href属性から）
        try:
            href_elem = container.find_element(By.XPATH, ".//a[contains(@href, '/rooms/')] | .//meta[@itemprop='url']")
            href = href_elem.get_attribute("href") or href_elem.get_attribute("content") or ""
            if href and "adults=" in href:
                import re
                adults_match = re.search(r'adults=(\d+)', href)
                if adults_match:
                    details["guests"] = int(adults_match.group(1))
        except Exception:
            pass
        
        if not details["guests"]:
            try:
                # "人"や"guests"を含む要素を探す
                guest_elements = container.find_elements(By.XPATH,
                    ".//span[contains(text(), '人') or contains(text(), 'guests') or contains(text(), '名')] | "
                    ".//div[contains(text(), '人') or contains(text(), 'guests') or contains(text(), '名')] | "
                    ".//span[contains(@aria-label, '人') or contains(@aria-label, 'guests') or contains(@aria-label, '名')] | "
                    ".//div[contains(@aria-label, '人') or contains(@aria-label, 'guests')] | "
                    ".//span[contains(@data-testid, 'guest')] | "
                    ".//div[contains(@data-testid, 'guest')]"
                )
                for elem in guest_elements:
                    elem_text = elem.text or ""
                    aria_label = elem.get_attribute("aria-label") or ""
                    if not details["guests"]:
                        details["guests"] = pick_guests_from_text(elem_text) or pick_guests_from_text(aria_label)
            except Exception:
                pass
            
            if not details["guests"]:
                details["guests"] = pick_guests_from_text(container_text) or pick_guests_from_text(container_html)
        
        # 寝室数 - より具体的な要素を探す（より広範囲に）
        try:
            # data-testid="listing-card-subtitle" 内を優先的に探す
            subtitle_elem = container.find_element(By.XPATH, ".//div[@data-testid='listing-card-subtitle'] | .//span[@data-testid='listing-card-subtitle']")
            subtitle_text = subtitle_elem.text or ""
            if subtitle_text:
                details["bedrooms"] = pick_bedrooms_from_text(subtitle_text)
        except Exception:
            pass
        
        if not details["bedrooms"]:
            try:
                bedroom_elements = container.find_elements(By.XPATH,
                    ".//span[contains(text(), '寝室') or contains(text(), 'bedroom') or contains(text(), 'BR')] | "
                    ".//div[contains(text(), '寝室') or contains(text(), 'bedroom') or contains(text(), 'BR')] | "
                    ".//span[contains(@aria-label, '寝室') or contains(@aria-label, 'bedroom')] | "
                    ".//div[contains(@aria-label, '寝室') or contains(@aria-label, 'bedroom')] | "
                    ".//span[contains(@data-testid, 'bedroom')] | "
                    ".//div[contains(@data-testid, 'bedroom')]"
                )
                for elem in bedroom_elements:
                    elem_text = elem.text or ""
                    aria_label = elem.get_attribute("aria-label") or ""
                    if not details["bedrooms"]:
                        details["bedrooms"] = pick_bedrooms_from_text(elem_text) or pick_bedrooms_from_text(aria_label)
            except Exception:
                pass
            
            if not details["bedrooms"]:
                details["bedrooms"] = pick_bedrooms_from_text(container_text) or pick_bedrooms_from_text(container_html)
        
        # ベッド数 - より具体的な要素を探す（より広範囲に）
        try:
            # data-testid="listing-card-subtitle" 内を優先的に探す
            subtitle_elem = container.find_element(By.XPATH, ".//div[@data-testid='listing-card-subtitle'] | .//span[@data-testid='listing-card-subtitle']")
            subtitle_text = subtitle_elem.text or ""
            if subtitle_text:
                details["beds"] = pick_beds_from_text(subtitle_text)
        except Exception:
            pass
        
        if not details["beds"]:
            try:
                bed_elements = container.find_elements(By.XPATH,
                    ".//span[contains(text(), 'ベッド')] | "
                    ".//span[contains(text(), 'bed') and not(contains(text(), 'bedroom'))] | "
                    ".//div[contains(text(), 'ベッド')] | "
                    ".//div[contains(text(), 'bed') and not(contains(text(), 'bedroom'))] | "
                    ".//span[contains(@aria-label, 'ベッド')] | "
                    ".//span[contains(@aria-label, 'bed') and not(contains(@aria-label, 'bedroom'))] | "
                    ".//div[contains(@aria-label, 'ベッド')] | "
                    ".//div[contains(@aria-label, 'bed') and not(contains(@aria-label, 'bedroom'))] | "
                    ".//span[contains(@data-testid, 'bed') and not(contains(@data-testid, 'bedroom'))] | "
                    ".//div[contains(@data-testid, 'bed') and not(contains(@data-testid, 'bedroom'))]"
                )
                for elem in bed_elements:
                    elem_text = elem.text or ""
                    aria_label = elem.get_attribute("aria-label") or ""
                    if not details["beds"]:
                        details["beds"] = pick_beds_from_text(elem_text) or pick_beds_from_text(aria_label)
            except Exception:
                pass
            
            if not details["beds"]:
                details["beds"] = pick_beds_from_text(container_text) or pick_beds_from_text(container_html)
        
        # レビュー数と評価を探す（より広範囲に）
        try:
            # data-testid="price-availability-row" 内を優先的に探す
            price_row = container.find_element(By.XPATH, ".//div[@data-testid='price-availability-row']")
            price_row_text = price_row.text or price_row.get_attribute("innerText") or price_row.get_attribute("textContent") or ""
            if price_row_text:
                # 評価とレビュー数を同時に抽出（例: "★4.62 (21)" のような形式に対応）
                details["reviews_count"] = pick_reviews_count_from_text(price_row_text)
                if not details["rating"]:
                    details["rating"] = pick_rating_from_text(price_row_text)
        except Exception:
            pass
        
        if not details["reviews_count"]:
            try:
                # レビュー関連の要素を探す
                review_elements = container.find_elements(By.XPATH, 
                    ".//span[contains(text(), 'レビュー') or contains(text(), 'reviews') or contains(text(), '件')] | "
                    ".//div[contains(text(), 'レビュー') or contains(text(), 'reviews') or contains(text(), '件')] | "
                    ".//span[contains(@aria-label, 'レビュー') or contains(@aria-label, 'reviews')] | "
                    ".//div[contains(@aria-label, 'レビュー') or contains(@aria-label, 'reviews')] | "
                    ".//span[contains(@data-testid, 'review')] | "
                    ".//div[contains(@data-testid, 'review')]"
                )
                for elem in review_elements:
                    elem_text = elem.text or ""
                    aria_label = elem.get_attribute("aria-label") or ""
                    if not details["reviews_count"]:
                        details["reviews_count"] = pick_reviews_count_from_text(elem_text) or pick_reviews_count_from_text(aria_label)
            except Exception:
                pass
            
            if not details["reviews_count"]:
                details["reviews_count"] = pick_reviews_count_from_text(container_text) or pick_reviews_count_from_text(container_html)
        
        # 評価を探す（星マークや数値）（より広範囲に）
        if not details["rating"]:
            try:
                # data-testid="price-availability-row" 内の要素を探す（例: "4.62 (21)"）
                price_row = container.find_element(By.XPATH, ".//div[@data-testid='price-availability-row']")
                # まず aria-hidden="true" の span を探す
                rating_spans = price_row.find_elements(By.XPATH, ".//span[@aria-hidden='true'] | .//span[contains(text(), '★') or contains(text(), '⭐')]")
                for span in rating_spans:
                    span_text = span.text or span.get_attribute("innerText") or span.get_attribute("textContent") or ""
                    if not details["rating"]:
                        details["rating"] = pick_rating_from_text(span_text)
                    # レビュー数も同時に取得できる場合がある
                    if not details["reviews_count"]:
                        details["reviews_count"] = pick_reviews_count_from_text(span_text)
            except Exception:
                pass
        
        if not details["rating"]:
            try:
                # 評価関連の要素を探す
                rating_elements = container.find_elements(By.XPATH,
                    ".//span[contains(@aria-label, '星') or contains(@aria-label, 'star') or contains(@aria-label, 'rating')] | "
                    ".//div[contains(@data-testid, 'rating') or contains(@aria-label, 'rating') or contains(@aria-label, 'star')] | "
                    ".//span[contains(@data-testid, 'rating') or contains(@data-testid, 'star')] | "
                    ".//span[contains(text(), '★') or contains(text(), '⭐')] | "
                    ".//div[contains(text(), '★') or contains(text(), '⭐')] | "
                    ".//span[contains(@class, 'rating') or contains(@class, 'star')] | "
                    ".//div[contains(@class, 'rating') or contains(@class, 'star')]"
                )
                for elem in rating_elements:
                    aria_label = elem.get_attribute("aria-label") or ""
                    elem_text = elem.text or ""
                    if not details["rating"]:
                        details["rating"] = pick_rating_from_text(aria_label) or pick_rating_from_text(elem_text)
            except Exception:
                pass
            
            if not details["rating"]:
                details["rating"] = pick_rating_from_text(container_text) or pick_rating_from_text(container_html)
        
        # 最後の手段: コンテナ全体のテキストからレビュー値を抽出（より確実に取得）
        # 既に上で試しているが、念のため再度試す（他の処理の後に実行）
        if not details["rating"]:
            # コンテナ全体のテキストから直接抽出を試みる（複数のパターンを試す）
            # まず、数値パターンを直接探す
            rating_candidates = []
            # "4.85" や "4.9" のようなパターンを探す
            rating_matches = re.findall(r'\b([1-5](?:\.\d+)?)\b', container_text)
            for match in rating_matches:
                try:
                    val = float(match)
                    if 0 < val <= 5.0:
                        rating_candidates.append(val)
                except ValueError:
                    pass
            # 最も高い値を優先（レビュー値の可能性が高い）
            if rating_candidates:
                details["rating"] = max(rating_candidates)
            else:
                # パターンマッチングで抽出を試みる
                details["rating"] = pick_rating_from_text(container_text) or pick_rating_from_text(container_html)
        
        # 補足情報（data-testid="listing-card-subtitle" のテキスト全体）
        # 注意: コンテナ内の最初のsubtitle要素のみを使用（複数ある場合は最初のもの）
        try:
            # コンテナ内の最初のsubtitle要素のみを取得（他のカードの情報を取得しないように）
            subtitle_elem = container.find_element(By.XPATH, ".//div[@data-testid='listing-card-subtitle'] | .//span[@data-testid='listing-card-subtitle']")
            subtitle_text = subtitle_elem.text or ""
            if subtitle_text:
                # 「・」や「、」で区切られたテキストをそのまま取得（例: "ベッド2・布団1組・寝室1"）
                details["subtitle"] = subtitle_text.strip()
        except Exception:
            pass
        
        # 補足情報が取得できなかった場合、コンテナテキストから直接探す
        if not details["subtitle"]:
            # "ベッド"と"寝室"を含む行を探す
            lines = container_text.split("\n")
            for line in lines:
                line = line.strip()
                if ("ベッド" in line or "bed" in line.lower()) and ("寝室" in line or "bedroom" in line.lower()):
                    details["subtitle"] = line
                    break
        
    except Exception as e:
        # デバッグ用（必要に応じてコメントアウト）
        # print(f"DEBUG extract_listing_details_from_container error: {e}")
        pass
    
    return details


def extract_title_from_element(elem) -> str:
    """要素からタイトル（物件名）を抽出"""
    # 最優先: data-testid="listing-card-name" を探す（物件名）- 各カード固有の情報
    try:
        name_elems = elem.find_elements(By.XPATH, ".//span[@data-testid='listing-card-name'] | .//div[@data-testid='listing-card-name']")
        for name_elem in name_elems:
            name_text = name_elem.text or ""
            if name_text and len(name_text) > 5:
                return name_text.strip()
    except Exception:
        pass
    
    # 次優先: data-testid="listing-card-title" を探す（画像で表示されているタイトル）
    try:
        title_elem = None
        xpaths = [
            ".//div[@data-testid='listing-card-title']",
            ".//span[@data-testid='listing-card-title']",
            ".//a[@data-testid='listing-card-title']",
            ".//*[@data-testid='listing-card-title']",
        ]
        for xpath in xpaths:
            try:
                title_elem = elem.find_element(By.XPATH, xpath)
                if title_elem:
                    break
            except Exception:
                continue
        
        if title_elem:
            title_text = title_elem.text or title_elem.get_attribute("innerText") or title_elem.get_attribute("textContent") or ""
            if title_text and len(title_text.strip()) > 0:
                title_text = title_text.strip()
                if len(title_text) >= 3:
                    return title_text
    except Exception:
        pass
    
    # フォールバック: aria-label を試す
    try:
        aria_label = elem.get_attribute("aria-label") or ""
        if aria_label and len(aria_label) > 5:
            if "（1泊）" not in aria_label and "¥" not in aria_label and "1泊" not in aria_label:
                return aria_label.strip()
    except Exception:
        pass
    
    # フォールバック: title 属性を試す
    try:
        title_attr = elem.get_attribute("title") or ""
        if title_attr and len(title_attr) > 5 and "¥" not in title_attr and "1泊" not in title_attr:
            return title_attr.strip()
    except Exception:
        pass
    
    try:
        # meta itemprop="name" を探す
        meta_elem = elem.find_element(By.XPATH, ".//meta[@itemprop='name']")
        meta_content = meta_elem.get_attribute("content") or ""
        if meta_content and len(meta_content) > 5:
            return meta_content.strip()
    except Exception:
        pass
    
    try:
        # より広範囲にタイトル要素を探す
        title_elems = elem.find_elements(By.XPATH, 
            ".//div[contains(@data-testid, 'title')] | "
            ".//span[contains(@data-testid, 'title')] | "
            ".//a[contains(@data-testid, 'title')] | "
            ".//*[contains(@data-testid, 'title')] | "
            ".//h1 | .//h2 | .//h3 | "
            ".//div[contains(@class, 'title')] | "
            ".//span[contains(@class, 'title')] | "
            ".//a[contains(@href, '/rooms/')] | "
            ".//div[@role='link']"
        )
        for title_elem in title_elems:
            # text属性とinnerTextの両方を試す
            title_text = title_elem.text or title_elem.get_attribute("innerText") or title_elem.get_attribute("textContent") or ""
            if title_text:
                title_text = title_text.strip()
                # 価格情報を含まない、かつ適切な長さのテキストを返す
                if len(title_text) >= 3 and "¥" not in title_text and "1泊" not in title_text and "（1泊）" not in title_text:
                    return title_text
    except Exception:
        pass
    
    # 親要素のテキストから最初の長い行を取得（タイトルっぽいもの）
    try:
        parent = elem.find_element(By.XPATH, "./ancestor::*[self::div or self::article or self::section][1]")
        lines = [line.strip() for line in parent.text.split("\n") if line.strip()]
        for line in lines:
            # 価格や「名」を含む行は除外
            if len(line) > 10 and "¥" not in line and "名" not in line and "guests" not in line.lower() and "1泊" not in line:
                return line
    except Exception:
        pass
    
    # print(f"DEBUG extract_title: No title found for element")
    return ""


def extract_prices_from_cards(checkin_date: date) -> List[int]:
    details = extract_price_details_from_cards(checkin_date)
    return [d["price_yen"] for d in details if isinstance(d.get("price_yen"), int)]


# 現在表示されている検索結果ページから、価格・URL・タイトル・レビュー等を1件ずつ抽出。閾値以上の価格・RATING_MIN以下・REVIEWS_COUNT_MIN未満は除外する。
def extract_price_details_from_cards(checkin_date: date) -> List[Dict[str, object]]:
    """カードから価格詳細を抽出（価格ベースのフィルタリング適用）"""
    price_threshold = get_price_threshold(checkin_date)
    
    # 最優先: 「（1泊）」付きの aria-label を持つspanを直接集める（ユーザーが貼ってくれたDOMに一致）
    spans = driver.find_elements(By.XPATH, price_span_anywhere_xpath())
    if spans:
        by_href: Dict[str, Dict[str, object]] = {}
        loose: List[Dict[str, object]] = []
        for idx, s in enumerate(spans):
            label = s.get_attribute("aria-label") or ""
            p = pick_price_from_text(label) or pick_price_from_text(s.text)
            if p is None:
                continue

            # 価格ベースのフィルタリング（下限: 8000円以下は除外）
            if PRICE_MIN is not None and p < PRICE_MIN:
                continue
            # 価格ベースのフィルタリング（上限: 高額物件は8人以上とみなして除外）
            if p >= price_threshold:
                continue

            # 同じlistingを二重カウントしないよう、近傍の /rooms/ link でキー化
            # 価格spanを含む最小のカードコンテナを先に特定し、その中から/rooms/リンクを探す
            href = None
            title = ""
            listing_details = {}
            try:
                # まず、価格spanを含む最小のカードコンテナを特定
                try:
                    # card-containerを優先的に探す
                    container = s.find_element(By.XPATH, "./ancestor::*[@data-testid='card-container'][1]")
                except Exception:
                    try:
                        # card-containerが見つからない場合は、価格spanを含む最小のコンテナを探す
                        container = s.find_element(By.XPATH, "./ancestor::*[self::div or self::article][.//span[@aria-label and (contains(@aria-label,'（1泊）') or contains(@aria-label,'1泊') or contains(@aria-label,'/泊'))]][1]")
                    except Exception:
                        container = s.find_element(By.XPATH, "./ancestor::*[self::div or self::article][1]")
                
                # コンテナ内から/rooms/リンクを探す（同じコンテナ内のリンクを確実に取得）
                try:
                    a = container.find_element(By.XPATH, ".//a[contains(@href,'/rooms/')][1]")
                    href = a.get_attribute("href") or ""
                except Exception:
                    # コンテナ内に見つからない場合は、祖先要素から探す（フォールバック）
                    try:
                        a = s.find_element(By.XPATH, "./ancestor::*[.//a[contains(@href,'/rooms/')]][1]//a[contains(@href,'/rooms/')][1]")
                        href = a.get_attribute("href") or ""
                    except Exception:
                        href = None
                
                # タイトルを取得（コンテナから優先的に、次にa要素から）
                title = extract_title_from_element(container)
                if not title and href:
                    try:
                        a = container.find_element(By.XPATH, ".//a[contains(@href,'/rooms/')][1]")
                        title = extract_title_from_element(a)
                    except Exception:
                        pass
                
                # 物件詳細情報を取得
                listing_details = extract_listing_details_from_container(container)
                # レビューフィルタリング（4.84以下は除外、4.85以上は含む）
                rating = listing_details.get("rating")
                if rating is not None and rating <= RATING_MIN:
                    continue
                # レビュー数フィルタリング（10未満は除外）
                reviews_count = listing_details.get("reviews_count")
                if reviews_count is not None and reviews_count < REVIEWS_COUNT_MIN:
                    continue
            except Exception:
                href = None
                try:
                    # より広範囲のコンテナを探す
                    try:
                        # まず card-container を探す
                        container = s.find_element(By.XPATH, "./ancestor::*[@data-testid='card-container'][1]")
                    except Exception:
                        try:
                            # card-container が見つからない場合は、より広範囲のコンテナを探す
                            container = s.find_element(By.XPATH, "./ancestor::*[self::div or self::article][.//span[@aria-label and (contains(@aria-label,'（1泊）') or contains(@aria-label,'1泊') or contains(@aria-label,'/泊'))]][1]")
                        except Exception:
                            container = s.find_element(By.XPATH, "./ancestor::*[self::div or self::article][1]")
                    title = extract_title_from_element(container)
                    listing_details = extract_listing_details_from_container(container)
                    # レビューフィルタリング（4.84以下は除外、4.85以上は含む）
                    rating = listing_details.get("rating")
                    if rating is not None and rating <= RATING_MIN:
                        continue
                    # レビュー数フィルタリング（10未満は除外）
                    reviews_count = listing_details.get("reviews_count")
                    if reviews_count is not None and reviews_count < REVIEWS_COUNT_MIN:
                        continue
                except Exception:
                    title = ""
                    listing_details = {}

            if href:
                by_href[href] = {
                    "listing_url": href, 
                    "price_yen": p, 
                    "raw_label": label, 
                    "title": title,
                    **listing_details
                }
            else:
                loose.append({
                    "listing_url": "", 
                    "price_yen": p, 
                    "raw_label": label, 
                    "title": title, 
                    "idx": idx,
                    **listing_details
                })

        if by_href or loose:
            return list(by_href.values()) + loose

    # まずは listing の <a> をカードとして拾う（貼ってくれたDOMに一致）
    anchors = driver.find_elements(By.XPATH, listing_anchors_xpath())
    by_href2: Dict[str, Dict[str, object]] = {}
    loose2: List[Dict[str, object]] = []

    # anchorが取れた場合: その近傍（親要素）テキストから価格を抽出
    if anchors:
        for idx, a in enumerate(anchors):
            try:
                # 「（1泊）」を持つprice spanを含む最小のカード要素を探す
                # より広範囲のコンテナを探す（data-testid="card-container"を含む）
                try:
                    container = a.find_element(
                        By.XPATH,
                        "./ancestor::*[@data-testid='card-container' or (self::div or self::article)][.//span[@aria-label and (contains(@aria-label,'（1泊）') or contains(@aria-label,'1泊') or contains(@aria-label,'/泊'))]][1]",
                    )
                except Exception:
                    container = a.find_element(
                        By.XPATH,
                        "./ancestor::*[self::div or self::article][.//span[@aria-label and (contains(@aria-label,'（1泊）') or contains(@aria-label,'1泊') or contains(@aria-label,'/泊'))]][1]",
                    )

                # aria-label から拾う（.textだけだと情報が落ちる）
                spans = container.find_elements(By.XPATH, price_span_in_card_xpath())
                p = None
                raw_label = ""
                for s in spans:
                    label = s.get_attribute("aria-label") or ""
                    raw_label = label or raw_label
                    p = pick_price_from_text(label) or pick_price_from_text(s.text)
                    if p is not None:
                        break

                if p is None:
                    p = pick_price_from_text(container.text)
                
                # 価格ベースのフィルタリング（下限: 8000円以下は除外）
                if p is not None and PRICE_MIN is not None and p < PRICE_MIN:
                    continue
                # 価格ベースのフィルタリング（上限: 高額物件は8人以上とみなして除外）
                if p is not None and p >= price_threshold:
                    continue
                
                # タイトルを取得（コンテナから優先的に、次にa要素から）
                title = extract_title_from_element(container)
                if not title:
                    title = extract_title_from_element(a)
                
                # 物件詳細情報を取得
                listing_details = extract_listing_details_from_container(container)
                # レビューフィルタリング（4.84以下は除外、4.85以上は含む）
                rating = listing_details.get("rating")
                if rating is not None and rating <= RATING_MIN:
                    continue
                # レビュー数フィルタリング（10未満は除外）
                reviews_count = listing_details.get("reviews_count")
                if reviews_count is not None and reviews_count < REVIEWS_COUNT_MIN:
                    continue
            except Exception:
                p = pick_price_from_text(a.text)
                raw_label = ""
                title = extract_title_from_element(a)
                listing_details = {}
                # 価格ベースのフィルタリング
                if p is not None and p >= price_threshold:
                    continue
            
            if p is not None:
                href = a.get_attribute("href") or ""
                if href:
                    by_href2[href] = {
                        "listing_url": href, 
                        "price_yen": p, 
                        "raw_label": raw_label, 
                        "title": title,
                        **listing_details
                    }
                else:
                    loose2.append({
                        "listing_url": "", 
                        "price_yen": p, 
                        "raw_label": raw_label, 
                        "title": title, 
                        "idx": idx,
                        **listing_details
                    })
        return list(by_href2.values()) + loose2

    # fallback: 旧方式（divカード候補）
    cards = driver.find_elements(By.XPATH, card_candidates_xpath())
    details: List[Dict[str, object]] = []
    for c in cards:
        p = pick_price_from_text(c.text)
        if p is not None:
            # 価格ベースのフィルタリング（下限: 8000円以下は除外）
            if PRICE_MIN is not None and p < PRICE_MIN:
                continue
            # 価格ベースのフィルタリング（上限）
            if p >= price_threshold:
                continue
            title = extract_title_from_element(c)
            listing_details = extract_listing_details_from_container(c)
            # レビューフィルタリング（4.84以下は除外）
            rating = listing_details.get("rating")
            if rating is not None and rating <= RATING_MIN:
                continue
            # レビュー数フィルタリング（10未満は除外）
            reviews_count = listing_details.get("reviews_count")
            if reviews_count is not None and reviews_count < REVIEWS_COUNT_MIN:
                continue
            details.append({
                "listing_url": "", 
                "price_yen": p, 
                "raw_label": "", 
                "title": title,
                **listing_details
            })
    return details


wait = WebDriverWait(driver, 30)
maybe_accept_cookies()

start = date.today()
end = start + timedelta(days=DAYS_AHEAD)

# data 作成・既存CSVがあれば日時付きで退避（上書きされないようにする）。日時は各ファイルの更新日時（mtime）を使用。
DATA_DIR.mkdir(exist_ok=True)
for p in (OUTPUT_CSV, OUTPUT_DETAIL_CSV):
    if p.exists():
        stem, ext = p.stem, p.suffix
        mtime = p.stat().st_mtime
        ts = datetime.fromtimestamp(mtime).strftime(FMT_DATED_SUFFIX)
        dest = DATA_DIR / f"{stem}_{ts}{ext}"
        p.rename(dest)
        print(f"  前回分を履歴に退避: {p.name} → {dest.name}")

# =============================================================================
# メイン処理（日別ループ・CSV書き出し）
# =============================================================================
# 各日で build_search_url → driver.get → カード抽出 → 平均・明細をCSVに追記。例外でその日が0件になってもループは続行する。

with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
    # CSV（カンマ区切り）で出力。長時間処理でも結果が残るよう1日ごとに書き込みます。
    writer = csv.writer(f)
    writer.writerow(["checkin", "avg_price_yen", "count", "min_price_yen", "max_price_yen", "url"])
    with open(OUTPUT_DETAIL_CSV, "w", newline="", encoding="utf-8-sig") as f_detail:
        detail_writer = csv.writer(f_detail)
        detail_writer.writerow(["checkin", "price_yen", "listing_url", "raw_label", "title", "guests", "bedrooms", "beds", "reviews_count", "rating", "subtitle"])

        for d in range((end - start).days + 1):
            checkin = start + timedelta(days=d)
            checkout = checkin + timedelta(days=1)  # 1泊固定

            url = build_search_url(checkin, checkout)
            print(f"[{checkin.isoformat()}] open: {url}")
            driver.get(url)
            maybe_accept_cookies()
            close_popups()  # ポップアップを閉じる

            # カードが出るまで待つ（0件の日はタイムアウトするので except で details=[] として扱い、次の日に進む）
            all_details: List[Dict[str, object]] = []
            seen_room_ids: set = set()  # 同一物件の重複を防ぐため room ID で管理
            try:
                page_num = 1
                while True:
                    wait.until(
                        EC.presence_of_all_elements_located(
                            (
                                By.XPATH,
                                # 価格spanを起点に待つ方が安定
                                price_span_anywhere_xpath(),
                            )
                        )
                    )
                    time.sleep(1)  # ページ読み込み待ちを短縮
                    close_popups()  # ページ読み込み後にポップアップを閉じる

                    # 追加読み込み（必要なら）
                    for _ in range(SCROLL_TIMES):
                        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                        time.sleep(SCROLL_WAIT_SEC)
                    
                    # 「もっと見る」ボタンや「Show more」ボタンを探してクリック
                    try:
                        show_more_xpaths = [
                            "//button[contains(text(), 'もっと見る')]",
                            "//button[contains(text(), 'Show more')]",
                            "//a[contains(text(), 'もっと見る')]",
                            "//a[contains(text(), 'Show more')]",
                            "//button[contains(@aria-label, 'もっと見る')]",
                            "//button[contains(@aria-label, 'Show more')]",
                        ]
                        for xpath in show_more_xpaths:
                            try:
                                show_more_btn = driver.find_element(By.XPATH, xpath)
                                if show_more_btn.is_enabled() and show_more_btn.is_displayed():
                                    driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", show_more_btn)
                                    time.sleep(0.5)
                                    show_more_btn.click()
                                    print(f"  -> 「もっと見る」ボタンをクリック")
                                    time.sleep(1)  # 待機時間を短縮
                                    break
                            except Exception:
                                continue
                    except Exception:
                        pass

                    # 現在のページから詳細を取得
                    page_details = extract_price_details_from_cards(checkin)
                    
                    # 重複を除外して追加（room IDベース）
                    before_count = len(all_details)
                    for detail in page_details:
                        url = detail.get("listing_url", "")
                        room_id = extract_room_id_from_url(url)
                        # room IDが取得できた場合、重複チェック
                        if room_id:
                            if room_id not in seen_room_ids:
                                seen_room_ids.add(room_id)
                                all_details.append(detail)
                        elif url:
                            # URLはあるがroom IDが抽出できない場合（通常は発生しないが念のため）
                            # URL全体をキーとして使用（room IDと混在しないよう、プレフィックスを付ける）
                            url_key = f"url:{url}"
                            if url_key not in seen_room_ids:
                                seen_room_ids.add(url_key)
                                all_details.append(detail)
                        else:
                            # URLが空の場合は常に追加（重複の可能性あり）
                            all_details.append(detail)
                    
                    current_count = len(all_details)
                    print(f"  -> ページ{page_num}: {current_count - before_count}件取得（累計: {current_count}件）")
                    
                    # 件数がMIN_LISTINGS_PER_DAY以上になったら終了
                    if current_count >= MIN_LISTINGS_PER_DAY:
                        print(f"  -> {MIN_LISTINGS_PER_DAY}件以上取得できたため終了")
                        break
                    
                    # 最大ページ数に達したら終了
                    if page_num >= MAX_PAGES:
                        print(f"  -> 最大{MAX_PAGES}ページに達したため終了")
                        break
                    
                    # ポップアップを閉じる（次ページボタン検索前に）
                    close_popups()
                    
                    # 次ページボタンを探す
                    try:
                        # まず、現在のページのURLを保存
                        current_url_before = driver.current_url
                        
                        # ページネーション要素が見える位置までスクロール（フッターの少し上）
                        # ページの最下部付近までスクロール
                        driver.execute_script("window.scrollTo(0, document.body.scrollHeight - 200);")
                        time.sleep(0.5)  # スクロール待機時間を短縮
                        
                        # Airbnbの次ページボタンを探す（提供されたHTML構造に基づく）
                        next_button = None
                        xpaths = [
                            # 最優先: nav要素内のaria-label="次へ"のaタグ（提供されたHTML構造に一致）
                            "//nav[@aria-label='検索結果のページ割り']//a[@aria-label='次へ']",
                            "//nav[contains(@aria-label, 'ページ')]//a[@aria-label='次へ']",
                            # 次優先: aria-label="次へ"の完全一致
                            "//a[@aria-label='次へ']",
                            # その他のパターン
                            "//nav//a[contains(@aria-label, '次') and not(@disabled)]",
                            "//a[contains(@aria-label, '次へ') and not(@disabled)]",
                            "//a[contains(@aria-label, 'Next') and not(@disabled)]",
                            "//nav//a[contains(@href, 'pagination_search=true')]",
                        ]
                        for xpath in xpaths:
                            try:
                                elements = driver.find_elements(By.XPATH, xpath)
                                for elem in elements:
                                    # 無効化されているかチェック
                                    disabled = elem.get_attribute("disabled") or elem.get_attribute("aria-disabled") == "true"
                                    href = elem.get_attribute("href") or ""
                                    # hrefがあり、無効化されていない、かつ表示されている要素を探す
                                    if href and not disabled:
                                        try:
                                            if elem.is_displayed():
                                                next_button = elem
                                                break
                                        except Exception:
                                            # is_displayed()が失敗する場合は、要素が存在することを確認
                                            next_button = elem
                                            break
                                if next_button:
                                    break
                            except Exception:
                                continue
                        
                        if next_button:
                            try:
                                # スクロールしてボタンが見える位置に移動（フッターの少し上）
                                driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", next_button)
                                time.sleep(0.5)  # スクロール待機時間を短縮
                                
                                # href属性からURLを取得して直接遷移（より確実）
                                href = next_button.get_attribute("href")
                                if href:
                                    print(f"  -> ページ{page_num + 1}に移動（URL遷移）")
                                    driver.get(href)
                                    page_num += 1
                                    time.sleep(1.5)  # ページ読み込み待ちを短縮
                                else:
                                    # hrefがない場合はクリック
                                    next_button.click()
                                    page_num += 1
                                    print(f"  -> ページ{page_num}に移動（クリック）")
                                    time.sleep(1.5)  # ページ読み込み待ちを短縮
                                
                                # URLが変わったか確認
                                time.sleep(0.5)  # 待機時間を短縮
                                current_url_after = driver.current_url
                                if current_url_before == current_url_after and not href:
                                    print(f"  -> 警告: URLが変わっていません。次ページへの移動が失敗した可能性があります")
                                # ページ移動後にポップアップを閉じる
                                close_popups()
                            except Exception as click_error:
                                print(f"  -> 次ページボタンクリックエラー: {click_error}")
                                break
                        else:
                            print(f"  -> 次ページボタンが見つからないため終了（現在: {current_count}件）")
                            # デバッグ用: nav要素の存在を確認
                            try:
                                nav_elements = driver.find_elements(By.XPATH, "//nav[contains(@aria-label, 'ページ')]")
                                if nav_elements:
                                    print(f"  -> デバッグ: ページネーションnav要素は見つかりましたが、次へボタンが見つかりませんでした")
                                else:
                                    print(f"  -> デバッグ: ページネーションnav要素自体が見つかりませんでした")
                            except Exception:
                                pass
                            break
                    except Exception as e:
                        # 次ページボタンが見つからない場合は終了
                        print(f"  -> 次ページボタン検索エラー: {e}（現在: {current_count}件）")
                        break
                
                details = all_details
                prices = [d["price_yen"] for d in details if isinstance(d.get("price_yen"), int)]
            except Exception:
                details = []
                prices = []

            count = len(prices)
            avg_price = round(statistics.mean(prices)) if count > 0 else None
            min_price = min(prices) if count > 0 else None
            max_price = max(prices) if count > 0 else None

            writer.writerow([
                checkin.isoformat(),
                avg_price,
                count,
                min_price,
                max_price,
                url,
            ])
            f.flush()

            # 明細出力（平均の根拠）
            for row in details:
                detail_writer.writerow([
                    checkin.isoformat(),
                    row.get("price_yen"),
                    row.get("listing_url", ""),
                    row.get("raw_label", ""),
                    row.get("title", ""),
                    row.get("guests"),  # ゲスト数（今後取得予定）
                    row.get("bedrooms"),  # 寝室数（今後取得予定）
                    row.get("beds"),  # ベッド数（今後取得予定）
                    row.get("reviews_count"),  # レビュー数（今後取得予定）
                    row.get("rating"),  # 評価（今後取得予定）
                    row.get("subtitle", ""),  # 補足情報
                ])
            f_detail.flush()

            print(f"  -> count={count}, avg={avg_price}")
            time.sleep(SLEEP_BETWEEN_DAYS_SEC)

# =============================================================================
# 後処理（ブラウザ終了・完了メッセージ）
# =============================================================================
# 正常・異常どちらでも driver を閉じる（未処理の例外で抜けた場合は quit が呼ばれない可能性あり。必要なら try/finally でラップする）。

driver.quit()
print(f"✅ {OUTPUT_CSV} を出力しました")
print(f"✅ {OUTPUT_DETAIL_CSV} を出力しました")
