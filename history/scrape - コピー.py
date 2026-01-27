import csv
import re
import time
import statistics
from datetime import date, timedelta
from typing import Dict, List, Optional
from urllib.parse import quote
from selenium import webdriver
from selenium.webdriver.edge.service import Service
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# =========================
# 取得条件（ここを編集）
# =========================
# NOTE: AirbnbのUIを直接いじるより、条件付きURLに遷移する方が安定します。
# 目的: 今日〜約3か月先まで「1泊」の平均価格を日別にCSV出力
DESTINATION = "大阪市 此花区"  # 「此花区」で検索
ADULTS = 4
CHILDREN = 0
INFANTS = 0
PETS = 0

DAYS_AHEAD = 9  # 今日から何日先まで（約3か月=90日）
SLEEP_BETWEEN_DAYS_SEC = 1.5  # ブロック回避のため少し待つ
SCROLL_TIMES = 2  # 追加でスクロールして読み込む回数（0でしない）
SCROLL_WAIT_SEC = 1.5

# 価格フィルタ（必要なら）
PRICE_MIN = None  # 例: 8000
PRICE_MAX = None  # 例: 20000

OUTPUT_CSV = "konohana_daily_avg.csv"
OUTPUT_DETAIL_CSV = "konohana_daily_details.csv"

# =========================
# Edge設定
# =========================
options = Options()
options.add_argument("--start-maximized")
options.add_argument("--disable-webrtc")
options.add_argument("--log-level=3")

service = Service("msedgedriver.exe")
driver = webdriver.Edge(service=service, options=options)

# =========================
# Airbnbを開く
# =========================
driver.get("https://www.airbnb.jp/")
time.sleep(3)

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


def listing_anchors_xpath() -> str:
    # 例としてユーザーが貼ってくれた: <a target="listing_..." href="/rooms/...?...check_in=...&check_out=...">
    # なるべく広めに rooms/ を拾いつつ、listing_ の target があれば優先
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


def pick_price_from_text(text: str) -> Optional[int]:
    t = text.replace("\n", " ").strip()

    # “1泊”に紐づく価格を優先
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
    """テキストから定員（人数）を抽出。例: "6名" -> 6, "8 guests" -> 8"""
    t = text.replace("\n", " ").strip()
    # パターン: "○名", "○ guests", "○人"
    patterns = [
        r"(\d+)\s*名",
        r"(\d+)\s*guests?",
        r"(\d+)\s*人",
    ]
    for pat in patterns:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


def extract_title_from_element(elem) -> str:
    """要素からタイトル（物件名）を抽出"""
    # まず aria-label を試す
    aria_label = elem.get_attribute("aria-label") or ""
    if aria_label and len(aria_label) > 5:  # 短すぎるものは除外
        # "¥ 10,000 （1泊）" のような価格情報だけの場合はスキップ
        if "（1泊）" not in aria_label and "¥" not in aria_label:
            return aria_label.strip()
    
    # title 属性を試す
    title_attr = elem.get_attribute("title") or ""
    if title_attr and len(title_attr) > 5:
        return title_attr.strip()
    
    # カード内のタイトル要素を探す（data-testid="listing-card-title" など）
    try:
        title_elem = elem.find_element(By.XPATH, ".//div[contains(@data-testid, 'title')] | .//span[contains(@data-testid, 'title')] | .//h2 | .//h3")
        title_text = title_elem.text or ""
        if title_text and len(title_text) > 5:
            return title_text.strip()
    except Exception:
        pass
    
    # 親要素のテキストから最初の長い行を取得（タイトルっぽいもの）
    try:
        parent = elem.find_element(By.XPATH, "./ancestor::*[self::div or self::article][1]")
        lines = [line.strip() for line in parent.text.split("\n") if line.strip()]
        for line in lines:
            # 価格や「名」を含む行は除外
            if len(line) > 10 and "¥" not in line and "名" not in line and "guests" not in line.lower():
                return line
    except Exception:
        pass
    
    return ""


def extract_prices_from_cards() -> List[int]:
    details = extract_price_details_from_cards()
    return [d["price_yen"] for d in details if isinstance(d.get("price_yen"), int)]


def extract_price_details_from_cards() -> List[Dict[str, object]]:
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

            # 同じlistingを二重カウントしないよう、近傍の /rooms/ link でキー化
            href = None
            title = ""
            guests = None
            try:
                a = s.find_element(By.XPATH, "./ancestor::*[.//a[contains(@href,'/rooms/')]][1]//a[contains(@href,'/rooms/')][1]")
                href = a.get_attribute("href") or ""
                # タイトルと定員を取得
                container = a.find_element(By.XPATH, "./ancestor::*[self::div or self::article][1]")
                title = extract_title_from_element(a) or extract_title_from_element(container)
                container_text = container.text or ""
                guests = pick_guests_from_text(container_text)
            except Exception:
                href = None
                try:
                    container = s.find_element(By.XPATH, "./ancestor::*[self::div or self::article][1]")
                    title = extract_title_from_element(container)
                    container_text = container.text or ""
                    guests = pick_guests_from_text(container_text)
                except Exception:
                    pass

            # 定員が6名以上の場合は除外
            if guests is not None and guests >= 6:
                continue

            if href:
                by_href[href] = {"listing_url": href, "price_yen": p, "raw_label": label, "title": title}
            else:
                loose.append({"listing_url": "", "price_yen": p, "raw_label": label, "title": title, "idx": idx})

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
                
                # タイトルと定員を取得
                title = extract_title_from_element(a) or extract_title_from_element(container)
                container_text = container.text or ""
                guests = pick_guests_from_text(container_text)
            except Exception:
                p = pick_price_from_text(a.text)
                raw_label = ""
                title = extract_title_from_element(a)
                guests = pick_guests_from_text(a.text)
            
            # 定員が6名以上の場合は除外
            if guests is not None and guests >= 6:
                continue
            
            if p is not None:
                href = a.get_attribute("href") or ""
                if href:
                    by_href2[href] = {"listing_url": href, "price_yen": p, "raw_label": raw_label, "title": title}
                else:
                    loose2.append({"listing_url": "", "price_yen": p, "raw_label": raw_label, "title": title, "idx": idx})
        return list(by_href2.values()) + loose2

    # fallback: 旧方式（divカード候補）
    cards = driver.find_elements(By.XPATH, card_candidates_xpath())
    details: List[Dict[str, object]] = []
    for c in cards:
        p = pick_price_from_text(c.text)
        if p is not None:
            title = extract_title_from_element(c)
            guests = pick_guests_from_text(c.text)
            # 定員が6名以上の場合は除外
            if guests is not None and guests >= 6:
                continue
            details.append({"listing_url": "", "price_yen": p, "raw_label": "", "title": title})
    return details


wait = WebDriverWait(driver, 30)
maybe_accept_cookies()

start = date.today()
end = start + timedelta(days=DAYS_AHEAD)

with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
    # CSV（カンマ区切り）で出力。長時間処理でも結果が残るよう1日ごとに書き込みます。
    writer = csv.writer(f)
    writer.writerow(["checkin", "avg_price_yen", "count", "min_price_yen", "max_price_yen", "url"])
    with open(OUTPUT_DETAIL_CSV, "w", newline="", encoding="utf-8-sig") as f_detail:
        detail_writer = csv.writer(f_detail)
        detail_writer.writerow(["checkin", "price_yen", "listing_url", "raw_label", "title"])

        for d in range((end - start).days + 1):
            checkin = start + timedelta(days=d)
            checkout = checkin + timedelta(days=1)  # 1泊固定

            url = build_search_url(checkin, checkout)
            print(f"[{checkin.isoformat()}] open: {url}")
            driver.get(url)
            maybe_accept_cookies()

            # カードが出るまで待つ（0件の場合はタイムアウトするのでexceptで扱う）
            try:
                wait.until(
                    EC.presence_of_all_elements_located(
                        (
                            By.XPATH,
                            # 価格spanを起点に待つ方が安定
                            price_span_anywhere_xpath(),
                        )
                    )
                )
                time.sleep(2)

                # 追加読み込み（必要なら）
                for _ in range(SCROLL_TIMES):
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(SCROLL_WAIT_SEC)

                details = extract_price_details_from_cards()
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
                ])
            f_detail.flush()

            print(f"  -> count={count}, avg={avg_price}")
            time.sleep(SLEEP_BETWEEN_DAYS_SEC)

driver.quit()
print(f"✅ {OUTPUT_CSV} を出力しました")
print(f"✅ {OUTPUT_DETAIL_CSV} を出力しました")
