"""
Airbnb 検索結果をスクレイプし、日別の平均・明細を CSV で出力する。

scrape_html_parse: テキスト解析・URL・日付判定・価格閾値
scrape_html_extract: XPath・DOMからの物件情報抽出
scrape_csv: CSV 出力系
"""

import csv
import logging
import os
import statistics
import time
from datetime import date, timedelta
from typing import Dict, List, Tuple

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options
from selenium.webdriver.edge.service import Service
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import scrape_csv as csv_module
from scrape_html_extract import extract_price_details_from_cards, price_span_anywhere_xpath
from scrape_html_parse import build_search_url, extract_room_id_from_url

# ドライバ・URL
EDGE_DRIVER_NAME = "msedgedriver.exe"
AIRBNB_TOP_URL = "https://www.airbnb.jp/"
SLEEP_AFTER_OPEN_SEC = 3
# 取得日数
DAYS_AHEAD = 120
# 日間待機秒
SLEEP_BETWEEN_DAYS_SEC = 1.5
# 1日あたり最小件数
MIN_LISTINGS_PER_DAY = 20
# 最大ページ数
MAX_PAGES = 5
# スクロール
SCROLL_TIMES = 5
SCROLL_WAIT_SEC = 1.0
# ログ
LOGFILE_DEFAULT = "execute.log"

LOGFILE = os.environ.get("LOGFILE", LOGFILE_DEFAULT)
logging.basicConfig(filename=LOGFILE, level=logging.ERROR, format='[%(asctime)s] %(levelname)s: %(message)s')


# Edge ドライバを起動しトップ URL を開く
#
# 引数:
#   （なし）
#
# 戻り値:
#   WebDriver: Edge ドライバインスタンス
def _create_driver():
    # Edge 起動オプション（最大化・WebRTC無効・ログ抑制）
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-webrtc")
    options.add_argument("--log-level=3")
    service = Service(EDGE_DRIVER_NAME)
    driver = webdriver.Edge(service=service, options=options)
    driver.get(AIRBNB_TOP_URL)
    # ページ読み込み待ち
    time.sleep(SLEEP_AFTER_OPEN_SEC)
    return driver


# Cookie バナーを閉じる（失敗時は無視）
#
# 引数:
#   driver: Selenium WebDriver
#
# 戻り値:
#   None
def _maybe_accept_cookies(driver) -> None:
    try:
        # Cookieバナー用ボタンを探す
        btns = driver.find_elements(
            By.XPATH,
            '//button[contains(., "すべて承諾") or contains(., "すべてを承諾") or contains(., "同意") or contains(., "許可") or contains(., "Accept")]',
        )
        # 見つかったら一つ目をクリックして閉じる
        if btns:
            btns[0].click()
            time.sleep(1)
    except Exception:
        pass


# ポップアップ・モーダルを閉じる（失敗時は無視）
#
# 引数:
#   driver: Selenium WebDriver
#
# 戻り値:
#   None
def _close_popups(driver) -> None:
    try:
        # ポップアップ/モーダルのボタン候補を収集
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
        # 候補のボタンを順に試してクリック
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

    # ESC キーでモーダルを閉じる
    try:
        from selenium.webdriver.common.keys import Keys
        body = driver.find_element(By.TAG_NAME, "body")
        body.send_keys(Keys.ESCAPE)
        time.sleep(0.5)
    except Exception:
        pass

    try:
        overlays = driver.find_elements(
            By.XPATH,
            '//div[contains(@class, "overlay")] | '
            '//div[contains(@class, "backdrop")] | '
            '//div[contains(@role, "dialog")]//div[contains(@class, "close")]'
        )
        # オーバーレイ要素を順に試してクリックして閉じる
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


def _run_day_scrape(
    driver,
    checkin_date: date,
    min_listings_per_day: int,
    max_pages: int,
) -> Tuple[List[Dict[str, object]], str]:
    """1日分の物件リストを取得（ページネーション含む）"""
    checkout = checkin_date + timedelta(days=1)
    url = build_search_url(checkin_date, checkout)
    print(f"[{checkin_date.isoformat()}] open: {url}")
    driver.get(url)
    _maybe_accept_cookies(driver)
    _close_popups(driver)

    # 重複排除用（room_id または url を保持）
    all_details: List[Dict[str, object]] = []
    seen_room_ids: set = set()
    wait = WebDriverWait(driver, 30)

    try:
        page_num = 1
        # ページネーションしながら結果を収集
        while True:
            # ページ内の価格要素が読み込まれるまで待機
            wait.until(EC.presence_of_all_elements_located((By.XPATH, price_span_anywhere_xpath())))
            time.sleep(1)
            _close_popups(driver)

            # ページを複数回スクロールして遅延読み込みを促す
            for _ in range(SCROLL_TIMES):
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(SCROLL_WAIT_SEC)

            try:
                # 「もっと見る」ボタンがあればクリックして追加読み込み
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
                            print("  -> 「もっと見る」ボタンをクリック")
                            time.sleep(1)
                            break
                    except Exception:
                        continue
            except Exception:
                pass

            page_details = extract_price_details_from_cards(driver, checkin_date)

            before_count = len(all_details)
            # room_id があればそれで、なければ URL で重複判定。両方なければ追加
            for detail in page_details:
                detail_url = detail.get("listing_url", "")
                room_id = extract_room_id_from_url(detail_url)
                if room_id:
                    if room_id not in seen_room_ids:
                        seen_room_ids.add(room_id)
                        all_details.append(detail)
                elif detail_url:
                    url_key = f"url:{detail_url}"
                    if url_key not in seen_room_ids:
                        seen_room_ids.add(url_key)
                        all_details.append(detail)
                else:
                    all_details.append(detail)

            current_count = len(all_details)
            print(f"  -> ページ{page_num}: {current_count - before_count}件取得（累計: {current_count}件）")

            # 最小件数に達したら当日の収集を終了
            if current_count >= min_listings_per_day:
                print(f"  -> {min_listings_per_day}件以上取得できたため終了")
                break

            # 最大ページ数を超えたら終了
            if page_num >= max_pages:
                print(f"  -> 最大{max_pages}ページに達したため終了")
                break

            _close_popups(driver)

            try:
                current_url_before = driver.current_url
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight - 200);")
                time.sleep(0.5)

                # 次ページボタンの XPath 候補を順に試す
                next_button = None
                xpaths = [
                    "//nav[@aria-label='検索結果のページ割り']//a[@aria-label='次へ']",
                    "//nav[contains(@aria-label, 'ページ')]//a[@aria-label='次へ']",
                    "//a[@aria-label='次へ']",
                    "//nav//a[contains(@aria-label, '次') and not(@disabled)]",
                    "//a[contains(@aria-label, '次へ') and not(@disabled)]",
                    "//a[contains(@aria-label, 'Next') and not(@disabled)]",
                    "//nav//a[contains(@href, 'pagination_search=true')]",
                ]
                for xpath in xpaths:
                    try:
                        elements = driver.find_elements(By.XPATH, xpath)
                        for elem in elements:
                            disabled = elem.get_attribute("disabled") or elem.get_attribute("aria-disabled") == "true"
                            elem_href = elem.get_attribute("href") or ""
                            if elem_href and not disabled:
                                try:
                                    if elem.is_displayed():
                                        next_button = elem
                                        break
                                except Exception:
                                    next_button = elem
                                    break
                        if next_button:
                            break
                    except Exception:
                        continue

                if next_button:
                    try:
                        driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", next_button)
                        time.sleep(0.5)
                        href = next_button.get_attribute("href")
                        if href:
                            print(f"  -> ページ{page_num + 1}に移動（URL遷移）")
                            driver.get(href)
                            page_num += 1
                            time.sleep(1.5)
                        else:
                            next_button.click()
                            page_num += 1
                            print(f"  -> ページ{page_num}に移動（クリック）")
                            time.sleep(1.5)
                        time.sleep(0.5)
                        _close_popups(driver)
                    except Exception as click_error:
                        print(f"  -> 次ページボタンクリックエラー: {click_error}")
                        break
                else:
                    print(f"  -> 次ページボタンが見つからないため終了（現在: {current_count}件）")
                    break
            except Exception as e:
                print(f"  -> 次ページボタン検索エラー: {e}（現在: {current_count}件）")
                break
    except Exception:
        pass

    return all_details, url

# メイン処理。ドライバ起動→日ごとにスクレイプして CSV を出力
#
# 引数:
#   （なし）
#
# 戻り値:
#   None
def main() -> None:

    # 既存CSVを履歴に退避
    csv_module.backup_existing_csvs()
    # ドライバ起動とトップページオープン
    driver = _create_driver()
    _maybe_accept_cookies(driver)

    start = date.today()
    end = start + timedelta(days=DAYS_AHEAD)

    try:
        # CSVファイルを開きヘッダー行を書き込む
        with open(csv_module.OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            csv_module.write_avg_header(writer)
            # 明細CSVも同時に開く
            with open(csv_module.OUTPUT_DETAIL_CSV, "w", newline="", encoding="utf-8-sig") as f_detail:
                detail_writer = csv.writer(f_detail)
                csv_module.write_detail_header(detail_writer)

                # 日ごとにスクレイプを実行
                for d in range((end - start).days + 1):
                    checkin = start + timedelta(days=d)
                    # 1日分をスクレイプ
                    details, url = _run_day_scrape(
                        driver,
                        checkin,
                        MIN_LISTINGS_PER_DAY,
                        MAX_PAGES,
                    )

                    # 整数の価格だけを取り出して統計計算
                    prices = [x["price_yen"] for x in details if isinstance(x.get("price_yen"), int)]
                    count = len(prices)
                    avg_price = round(statistics.mean(prices)) if count > 0 else None
                    min_price = min(prices) if count > 0 else None
                    max_price = max(prices) if count > 0 else None
                    # 平均CSV・明細CSVへ書き込む
                    csv_module.write_avg_row(
                        writer,
                        checkin.isoformat(),
                        avg_price,
                        count,
                        min_price,
                        max_price,
                        url,
                    )
                    # 明細CSVへも書き込む
                    csv_module.write_detail_rows(detail_writer, checkin.isoformat(), details)

                    f.flush()
                    f_detail.flush()

                    print(f"  -> count={count}, avg={avg_price}")

                    # 次日の実行まで少し待つ
                    if d < (end - start).days:
                        time.sleep(SLEEP_BETWEEN_DAYS_SEC)
    finally:
        driver.quit()

    print(f"✅ {csv_module.OUTPUT_CSV} を出力しました")
    print(f"✅ {csv_module.OUTPUT_DETAIL_CSV} を出力しました")


if __name__ == "__main__":
    main()
