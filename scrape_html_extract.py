"""DOM からの物件情報抽出。XPath・カード解析。"""

import re
from datetime import date
from typing import Dict, List, Optional

from selenium.webdriver.common.by import By

from scrape_html_parse import (
    get_price_threshold,
    pick_bedrooms_from_text,
    pick_beds_from_text,
    pick_guests_from_text,
    pick_price_from_text,
    pick_rating_from_text,
    pick_reviews_count_from_text,
)

PRICE_MIN = 0
RATING_MIN = 4.8
REVIEWS_COUNT_MIN = 20


# 物件カードのリンク（/rooms/ を含む a 要素）を拾う XPath
#
# 引数:
#   （なし）
#
# 戻り値:
#   str: XPath 文字列
def listing_anchors_xpath() -> str:
    return (
        '//a[contains(@href, "/rooms/") and contains(@target, "listing_")]'
        ' | //a[contains(@href, "/rooms/") and @aria-labelledby]'
        ' | //a[contains(@href, "/rooms/")]'
    )


# カード候補要素の XPath（DOM 変更に備え複数パターンを列挙）
#
# 引数:
#   （なし）
#
# 戻り値:
#   str: XPath 文字列
def card_candidates_xpath() -> str:
    return (
        listing_anchors_xpath()
        + ' | //div[@itemprop="itemListElement"]'
        + ' | //div[@data-testid="card-container"]'
        + ' | //div[contains(@data-testid, "property-card")]'
    )




# カード内の価格 span の XPath（aria-label に「1泊」を含む）
#
# 引数:
#   （なし）
#
# 戻り値:
#   str: XPath 文字列
def price_span_in_card_xpath() -> str:
    return (
        './/span[@aria-label and ('
        'contains(@aria-label, "（1泊）") or contains(@aria-label, "1泊") or contains(@aria-label, "/泊")'
        ')]'
    )


# ページ全体の価格 span の XPath
#
# 引数:
#   （なし）
#
# 戻り値:
#   str: XPath 文字列
def price_span_anywhere_xpath() -> str:
    return (
        '//span[@aria-label and ('
        'contains(@aria-label, "（1泊）") or contains(@aria-label, "1泊") or contains(@aria-label, "/泊")'
        ')]'
    )


# コンテナから物件詳細（ゲスト数・寝室・ベッド・レビュー・評価・補足）を抽出
#
# 引数:
#   container: Selenium WebElement（カードコンテナ）
#
# 戻り値:
#   Dict: guests, bedrooms, beds, reviews_count, rating, subtitle
def extract_listing_details_from_container(container) -> Dict[str, Optional[object]]:
    details = {
        "guests": None,
        "bedrooms": None,
        "beds": None,
        "reviews_count": None,
        "rating": None,
        "subtitle": None,
    }
    try:
        container_text = container.text or ""
        container_html = container.get_attribute("innerHTML") or ""
        # adults= を含む URL からゲスト数を取得
        try:
            href_elem = container.find_element(By.XPATH, ".//a[contains(@href, '/rooms/')] | .//meta[@itemprop='url']")
            href = href_elem.get_attribute("href") or href_elem.get_attribute("content") or ""
            if href and "adults=" in href:
                adults_match = re.search(r'adults=(\d+)', href)
                if adults_match:
                    details["guests"] = int(adults_match.group(1))
        except Exception:
            pass

        # DOM中の要素からゲスト数を探す
        if not details["guests"]:
            try:
                guest_elements = container.find_elements(By.XPATH,
                    ".//span[contains(text(), '人') or contains(text(), 'guests') or contains(text(), '名')] | "
                    ".//div[contains(text(), '人') or contains(text(), 'guests') or contains(text(), '名')] | "
                    ".//span[contains(@aria-label, '人') or contains(@aria-label, 'guests') or contains(@aria-label, '名')] | "
                    ".//div[contains(@aria-label, '人') or contains(@aria-label, 'guests')] | "
                    ".//span[contains(@data-testid, 'guest')] | "
                    ".//div[contains(@data-testid, 'guest')]"
                )
                # 候補要素を順に解析して最初に見つかった値を採用
                for elem in guest_elements:
                    elem_text = elem.text or ""
                    aria_label = elem.get_attribute("aria-label") or ""
                    if not details["guests"]:
                        details["guests"] = pick_guests_from_text(elem_text) or pick_guests_from_text(aria_label)
            except Exception:
                pass
            if not details["guests"]:
                details["guests"] = pick_guests_from_text(container_text) or pick_guests_from_text(container_html)

        # サブタイトル行から寝室数を取得（優先）
        try:
            subtitle_elem = container.find_element(By.XPATH, ".//div[@data-testid='listing-card-subtitle'] | .//span[@data-testid='listing-card-subtitle']")
            subtitle_text = subtitle_elem.text or ""
            if subtitle_text:
                details["bedrooms"] = pick_bedrooms_from_text(subtitle_text)
        except Exception:
            pass

        # 寝室数を複数候補から抽出
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

        try:
            subtitle_elem = container.find_element(By.XPATH, ".//div[@data-testid='listing-card-subtitle'] | .//span[@data-testid='listing-card-subtitle']")
            subtitle_text = subtitle_elem.text or ""
            if subtitle_text:
                details["beds"] = pick_beds_from_text(subtitle_text)
        except Exception:
            pass

        # ベッド数を候補要素やテキストから抽出
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

        # 価格行からレビュー件数・評価を試しに抽出
        try:
            price_row = container.find_element(By.XPATH, ".//div[@data-testid='price-availability-row']")
            price_row_text = price_row.text or price_row.get_attribute("innerText") or price_row.get_attribute("textContent") or ""
            if price_row_text:
                details["reviews_count"] = pick_reviews_count_from_text(price_row_text)
                if not details["rating"]:
                    details["rating"] = pick_rating_from_text(price_row_text)
        except Exception:
            pass
        if not details["reviews_count"]:
            details["reviews_count"] = pick_reviews_count_from_text(container_text) or pick_reviews_count_from_text(container_html)

        if not details["rating"]:
            try:
                price_row = container.find_element(By.XPATH, ".//div[@data-testid='price-availability-row']")
                rating_spans = price_row.find_elements(By.XPATH, ".//span[@aria-hidden='true'] | .//span[contains(text(), '★') or contains(text(), '⭐')]")
                for span in rating_spans:
                    span_text = span.text or span.get_attribute("innerText") or span.get_attribute("textContent") or ""
                    if not details["rating"]:
                        details["rating"] = pick_rating_from_text(span_text)
                    if not details["reviews_count"]:
                        details["reviews_count"] = pick_reviews_count_from_text(span_text)
            except Exception:
                pass

        if not details["rating"]:
            try:
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

        if not details["rating"]:
            rating_candidates = []
            rating_matches = re.findall(r'\b([1-5](?:\.\d+)?)\b', container_text)
            for match in rating_matches:
                try:
                    val = float(match)
                    if 0 < val <= 5.0:
                        rating_candidates.append(val)
                except ValueError:
                    pass
            if rating_candidates:
                details["rating"] = max(rating_candidates)
            else:
                details["rating"] = pick_rating_from_text(container_text) or pick_rating_from_text(container_html)

        try:
            subtitle_elem = container.find_element(By.XPATH, ".//div[@data-testid='listing-card-subtitle'] | .//span[@data-testid='listing-card-subtitle']")
            subtitle_text = subtitle_elem.text or ""
            if subtitle_text:
                details["subtitle"] = subtitle_text.strip()
        except Exception:
            pass

        if not details["subtitle"]:
            lines = container_text.split("\n")
            for line in lines:
                line = line.strip()
                if ("ベッド" in line or "bed" in line.lower()) and ("寝室" in line or "bedroom" in line.lower()):
                    details["subtitle"] = line
                    break
    except Exception:
        pass
    return details


# 要素からタイトル（物件名）を抽出
#
# 引数:
#   elem: Selenium WebElement
#
# 戻り値:
#   str: 物件名（取得不可時は空文字）
def extract_title_from_element(elem) -> str:
    """要素から物件タイトルと思われるテキストを抽出して返す"""
    try:
        # まずは data-testid の名前候補を探す
        name_elems = elem.find_elements(By.XPATH, ".//span[@data-testid='listing-card-name'] | .//div[@data-testid='listing-card-name']")
        for name_elem in name_elems:
            name_text = name_elem.text or ""
            if name_text and len(name_text) > 5:
                return name_text.strip()
    except Exception:
        pass

    try:
        # タイトル候補のXPathを順に試す
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

    try:
        # aria-labelにタイトル風の文字列があれば返す
        aria_label = elem.get_attribute("aria-label") or ""
        if aria_label and len(aria_label) > 5:
            if "（1泊）" not in aria_label and "¥" not in aria_label and "1泊" not in aria_label:
                return aria_label.strip()
    except Exception:
        pass

    try:
        # title属性にまともな文字列があれば返す
        title_attr = elem.get_attribute("title") or ""
        if title_attr and len(title_attr) > 5 and "¥" not in title_attr and "1泊" not in title_attr:
            return title_attr.strip()
    except Exception:
        pass

    try:
        # meta要素のitemprop=nameから取得
        meta_elem = elem.find_element(By.XPATH, ".//meta[@itemprop='name']")
        meta_content = meta_elem.get_attribute("content") or ""
        if meta_content and len(meta_content) > 5:
            return meta_content.strip()
    except Exception:
        pass

    try:
        # 各種タイトル候補要素を順に見て短すぎたり価格表記でないものを返す
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
            title_text = title_elem.text or title_elem.get_attribute("innerText") or title_elem.get_attribute("textContent") or ""
            if title_text:
                title_text = title_text.strip()
                if len(title_text) >= 3 and "¥" not in title_text and "1泊" not in title_text and "（1泊）" not in title_text:
                    return title_text
    except Exception:
        pass

    try:
        parent = elem.find_element(By.XPATH, "./ancestor::*[self::div or self::article or self::section][1]")
        lines = [line.strip() for line in parent.text.split("\n") if line.strip()]
        for line in lines:
            if len(line) > 10 and "¥" not in line and "名" not in line and "guests" not in line.lower() and "1泊" not in line:
                return line
    except Exception:
        pass
    return ""


# 検索結果ページから価格・URL・タイトル・レビュー等を1件ずつ抽出。閾値超過・RATING_MIN以下・REVIEWS_COUNT_MIN未満は除外
#
# 引数:
#   driver: Selenium WebDriver
#   checkin_date (date): 対象日
#
# 戻り値:
#   List[Dict]: 各件の price_yen, listing_url, title, guests, bedrooms 等
def extract_price_details_from_cards(driver, checkin_date: date) -> List[Dict[str, object]]:
    """検索結果ページから価格・URL・タイトル・レビュー等を1件ずつ抽出"""
    # 日付に応じた閾値（閾値超過は除外）
    price_threshold = get_price_threshold(checkin_date)
        spans = driver.find_elements(By.XPATH, price_span_anywhere_xpath())
    if spans:
        # aria-label や text から価格を取得。閾値超過・RATING_MIN以下・REVIEWS_COUNT_MIN未満はスキップ
        by_href: Dict[str, Dict[str, object]] = {}
        loose: List[Dict[str, object]] = []
        # 各spanについて価格やタイトル等を解析
        for idx, s in enumerate(spans):
            label = s.get_attribute("aria-label") or ""
            p = pick_price_from_text(label) or pick_price_from_text(s.text)
            if p is None:
                continue
            if PRICE_MIN is not None and p < PRICE_MIN:
                continue
            if p >= price_threshold:
                continue

            # 親コンテナから URL・タイトル・詳細を取得
            href = None
            title = ""
            listing_details = {}
            try:
                try:
                    container = s.find_element(By.XPATH, "./ancestor::*[@data-testid='card-container'][1]")
                except Exception:
                    try:
                        container = s.find_element(By.XPATH, "./ancestor::*[self::div or self::article][.//span[@aria-label and (contains(@aria-label,'（1泊）') or contains(@aria-label,'1泊') or contains(@aria-label,'/泊'))]][1]")
                    except Exception:
                        container = s.find_element(By.XPATH, "./ancestor::*[self::div or self::article][1]")

                try:
                    a = container.find_element(By.XPATH, ".//a[contains(@href,'/rooms/')][1]")
                    href = a.get_attribute("href") or ""
                except Exception:
                    try:
                        a = s.find_element(By.XPATH, "./ancestor::*[.//a[contains(@href,'/rooms/')]][1]//a[contains(@href,'/rooms/')][1]")
                        href = a.get_attribute("href") or ""
                    except Exception:
                        href = None

                title = extract_title_from_element(container)
                if not title and href:
                    try:
                        a = container.find_element(By.XPATH, ".//a[contains(@href,'/rooms/')][1]")
                        title = extract_title_from_element(a)
                    except Exception:
                        pass

                listing_details = extract_listing_details_from_container(container)
                rating = listing_details.get("rating")
                if rating is not None and rating <= RATING_MIN:
                    continue
                reviews_count = listing_details.get("reviews_count")
                if reviews_count is not None and reviews_count < REVIEWS_COUNT_MIN:
                    continue
            except Exception:
                href = None
                try:
                    try:
                        container = s.find_element(By.XPATH, "./ancestor::*[@data-testid='card-container'][1]")
                    except Exception:
                        try:
                            container = s.find_element(By.XPATH, "./ancestor::*[self::div or self::article][.//span[@aria-label and (contains(@aria-label,'（1泊）') or contains(@aria-label,'1泊') or contains(@aria-label,'/泊'))]][1]")
                        except Exception:
                            container = s.find_element(By.XPATH, "./ancestor::*[self::div or self::article][1]")
                    title = extract_title_from_element(container)
                    listing_details = extract_listing_details_from_container(container)
                    rating = listing_details.get("rating")
                    if rating is not None and rating <= RATING_MIN:
                        continue
                    reviews_count = listing_details.get("reviews_count")
                    if reviews_count is not None and reviews_count < REVIEWS_COUNT_MIN:
                        continue
                except Exception:
                    title = ""
                    listing_details = {}

            if href:
                by_href[href] = {"listing_url": href, "price_yen": p, "raw_label": label, "title": title, **listing_details}
            else:
                loose.append({"listing_url": "", "price_yen": p, "raw_label": label, "title": title, "idx": idx, **listing_details})

        if by_href or loose:
            return list(by_href.values()) + loose

    # spansで候補が得られなければリンクアンカーから抽出
    anchors = driver.find_elements(By.XPATH, listing_anchors_xpath())
    by_href2: Dict[str, Dict[str, object]] = {}
    loose2: List[Dict[str, object]] = []

    if anchors:
        # リンクアンカーを順に解析して価格を抽出
        for idx, a in enumerate(anchors):
            try:
                try:
                    container = a.find_element(By.XPATH, "./ancestor::*[@data-testid='card-container' or (self::div or self::article)][.//span[@aria-label and (contains(@aria-label,'（1泊）') or contains(@aria-label,'1泊') or contains(@aria-label,'/泊'))]][1]")
                except Exception:
                    container = a.find_element(By.XPATH, "./ancestor::*[self::div or self::article][.//span[@aria-label and (contains(@aria-label,'（1泊）') or contains(@aria-label,'1泊') or contains(@aria-label,'/泊'))]][1]")

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

                if p is not None and PRICE_MIN is not None and p < PRICE_MIN:
                    continue
                if p is not None and p >= price_threshold:
                    continue

                title = extract_title_from_element(container)
                if not title:
                    title = extract_title_from_element(a)

                listing_details = extract_listing_details_from_container(container)
                rating = listing_details.get("rating")
                if rating is not None and rating <= RATING_MIN:
                    continue
                reviews_count = listing_details.get("reviews_count")
                if reviews_count is not None and reviews_count < REVIEWS_COUNT_MIN:
                    continue
            except Exception:
                p = pick_price_from_text(a.text)
                raw_label = ""
                title = extract_title_from_element(a)
                listing_details = {}
                if p is not None and p >= price_threshold:
                    continue

            if p is not None:
                href = a.get_attribute("href") or ""
                if href:
                    by_href2[href] = {"listing_url": href, "price_yen": p, "raw_label": raw_label, "title": title, **listing_details}
                else:
                    loose2.append({"listing_url": "", "price_yen": p, "raw_label": raw_label, "title": title, "idx": idx, **listing_details})
        return list(by_href2.values()) + loose2

    # 最終手段: ページ内のカード候補から価格を抽出
    cards = driver.find_elements(By.XPATH, card_candidates_xpath())
    details: List[Dict[str, object]] = []
    # カード候補を順に解析して価格等を抽出
    for c in cards:
        p = pick_price_from_text(c.text)
        if p is not None:
            if PRICE_MIN is not None and p < PRICE_MIN:
                continue
            if p >= price_threshold:
                continue
            title = extract_title_from_element(c)
            listing_details = extract_listing_details_from_container(c)
            rating = listing_details.get("rating")
            if rating is not None and rating <= RATING_MIN:
                continue
            reviews_count = listing_details.get("reviews_count")
            if reviews_count is not None and reviews_count < REVIEWS_COUNT_MIN:
                continue
            details.append({"listing_url": "", "price_yen": p, "raw_label": "", "title": title, **listing_details})
    return details
