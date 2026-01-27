import time
from selenium import webdriver
from selenium.webdriver.edge.service import Service
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.by import By

# =========================
# Edge 設定
# =========================
options = Options()
options.add_argument("--start-maximized")
options.add_argument("--disable-webrtc")
options.add_argument("--log-level=3")

service = Service("msedgedriver.exe")
driver = webdriver.Edge(service=service, options=options)

try:
    # =========================
    # Airbnb 検索URL
    # =========================
    url = (
        "https://www.airbnb.jp/s/homes"
        "?flexible_trip_lengths%5B%5D=one_week"
        "&monthly_start_date=2026-02-01"
        "&monthly_length=3"
        "&monthly_end_date=2026-05-01"
        "&refinement_paths%5B%5D=%2Fhomes"
        "&location_search=NEARBY"
        "&center_lat=34.67"
        "&center_lng=135.5"
        "&date_picker_type=calendar"
        "&checkin=2026-01-23"
        "&checkout=2026-01-24"
        "&adults=4"
        "&source=structured_search_input_header"
        "&search_type=AUTOSUGGEST"
    )

    driver.get(url)

    # =========================
    # 手動ログイン待ち
    # =========================
    input("👉 Airbnbにログインしたら Enter を押してください")

    # =========================
    # 初期描画待ち
    # =========================
    time.sleep(10)

    # =========================
    # 無限スクロール（件数増やす）
    # =========================
    last_height = driver.execute_script("return document.body.scrollHeight")

    for i in range(6):
        print(f"スクロール {i+1} 回目")
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(4)

        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height

    # =========================
    # DOM（完成HTML）取得
    # =========================
    html = driver.page_source

    with open("airbnb_result.html", "w", encoding="utf-8") as f:
        f.write(html)

    print("✅ DOMを airbnb_result.html に保存しました")

finally:
    # =========================
    # ブラウザ閉じる（不要ならコメントアウト）
    # =========================
    driver.quit()
