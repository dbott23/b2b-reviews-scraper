"""Capterra scraper — uses curl_cffi to bypass Cloudflare TLS fingerprinting."""

import asyncio
import re
from datetime import datetime
from urllib.parse import quote_plus

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

SORT_MAP = {
    "recent": "most_recent",
    "helpful": "most_helpful",
    "highest": "highest_rating",
    "lowest": "lowest_rating",
}


async def _fetch(session: AsyncSession, url: str) -> str:
    try:
        resp = await session.get(url, timeout=30)
        print(f"[capterra] GET {url} -> {resp.status_code}, html_len={len(resp.text)}", flush=True)
        print(f"[capterra] html preview: {resp.text[:400]}", flush=True)
        if resp.status_code == 200:
            return resp.text
    except Exception as e:
        print(f"[capterra] fetch error {url}: {e}", flush=True)
    return ""


async def _search_product_url(company: str, get_proxy_url=None) -> str | None:
    proxy = None
    if get_proxy_url:
        try:
            proxy = await get_proxy_url() if asyncio.iscoroutinefunction(get_proxy_url) else get_proxy_url()
        except Exception:
            pass

    if proxy:
        masked = proxy.split("@")[-1] if "@" in proxy else proxy
        print(f"[capterra] using proxy: ...@{masked}", flush=True)

    proxies = {"https": proxy, "http": proxy} if proxy else None
    async with AsyncSession(impersonate="chrome", proxies=proxies) as session:
        url = f"https://www.capterra.com/search/?query={quote_plus(company)}"
        html = await _fetch(session, url)

    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(pat in href for pat in ["/p/", "/reviews/", "/software/"]):
            print(f"[capterra] found product link: {href}", flush=True)
            return "https://www.capterra.com" + href if href.startswith("/") else href

    print("[capterra] no product link found in search HTML", flush=True)
    return None


def _parse_reviews(html: str, company: str, product_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    records = []

    for card in soup.select("[data-testid='review-card'], .review-card, article[class*='review']"):
        rating = None
        rating_el = card.select_one("[aria-label*='star'], [class*='rating']")
        if rating_el:
            m = re.search(r"(\d[\d.]*)", rating_el.get("aria-label", "") or rating_el.text)
            if m:
                try:
                    rating = float(m.group(1))
                except ValueError:
                    pass

        title_el = card.select_one("h3, [class*='title'], [class*='headline']")
        title = title_el.get_text(strip=True) if title_el else None

        body_el = card.select_one("[class*='body'], [class*='comment'], p")
        body = body_el.get_text(strip=True) if body_el else None
        pros = cons = None
        for label_el in card.select("[class*='pros'], [class*='cons']"):
            text = label_el.get_text(strip=True)
            label = label_el.get("class", [""])[0].lower()
            if "pros" in label:
                pros = text
            elif "cons" in label:
                cons = text

        reviewer_el = card.select_one("[class*='reviewer'], [class*='author']")
        reviewer_name = reviewer_el.get_text(strip=True) if reviewer_el else None

        date_el = card.select_one("time, [class*='date']")
        date_str = ""
        if date_el:
            date_str = date_el.get("datetime") or date_el.get_text(strip=True)
        try:
            date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date().isoformat()
        except Exception:
            date = date_str or None

        review_link = card.select_one("a[href*='/reviews/']")
        review_url = None
        if review_link:
            href = review_link.get("href", "")
            review_url = "https://www.capterra.com" + href if href.startswith("/") else href

        if not (title or body):
            continue

        records.append({
            "company": company,
            "platform": "capterra",
            "reviewer_name": reviewer_name,
            "reviewer_title": None,
            "reviewer_company_size": None,
            "rating": rating,
            "title": title,
            "body": body,
            "pros": pros,
            "cons": cons,
            "date": date,
            "verified": False,
            "helpful_count": None,
            "review_url": review_url or product_url,
            "product_url": product_url,
        })

    return records


async def scrape(
    company: str,
    max_reviews: int = 50,
    sort_by: str = "recent",
    min_rating: int | None = None,
    proxy_url: str | None = None,
    get_proxy_url=None,
    **_kwargs,
) -> list[dict]:
    records: list[dict] = []

    _get_proxy = get_proxy_url or ((lambda: proxy_url) if proxy_url else None)
    product_url = await _search_product_url(company, _get_proxy)
    if not product_url:
        return []

    proxy = None
    if _get_proxy:
        try:
            proxy = await _get_proxy() if asyncio.iscoroutinefunction(_get_proxy) else _get_proxy()
        except Exception:
            pass

    proxies = {"https": proxy, "http": proxy} if proxy else None

    if "/reviews" in product_url:
        reviews_url = product_url.rstrip("/") + "/"
    else:
        reviews_url = product_url.rstrip("/") + "/reviews/"
    ct_sort = SORT_MAP.get(sort_by, "most_recent")
    page_num = 1

    async with AsyncSession(impersonate="chrome", proxies=proxies) as session:
        while len(records) < max_reviews:
            url = f"{reviews_url}?sort={ct_sort}&page={page_num}"
            if min_rating:
                url += f"&rating={min_rating}"

            html = await _fetch(session, url)
            if not html:
                break

            page_records = _parse_reviews(html, company, product_url)
            if not page_records:
                break

            records.extend(page_records)
            page_num += 1
            await asyncio.sleep(1.5)

    return records[:max_reviews]
