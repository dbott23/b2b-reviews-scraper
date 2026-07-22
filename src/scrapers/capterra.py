"""Capterra scraper — uses camoufox (Firefox + anti-detection) to bypass Cloudflare."""

import asyncio
import re
from datetime import datetime
from urllib.parse import quote_plus

from bs4 import BeautifulSoup
from camoufox.async_api import AsyncCamoufox

from src.scrapers._proxy import parse_proxy

SORT_MAP = {
    "recent": "most_recent",
    "helpful": "most_helpful",
    "highest": "highest_rating",
    "lowest": "lowest_rating",
}

FF_PREFS = {"security.sandbox.content.level": 0}
_CHALLENGE_TITLES = ("just a moment", "verifying connection", "verifying you are human", "attention required", "please wait", "access denied", "403 forbidden", "enable javascript")


def _is_challenge(html: str, url: str) -> bool:
    m = re.search(r"<title[^>]*>([^<]*)</title>", html[:3000], re.IGNORECASE)
    title = m.group(1).lower().strip() if m else ""
    return (
        any(s in title for s in _CHALLENGE_TITLES)
        or "__cf_chl_rt_tk" in url
    )


async def _resolve_proxy(get_proxy_url) -> str | None:
    if not get_proxy_url:
        return None
    try:
        return await get_proxy_url() if asyncio.iscoroutinefunction(get_proxy_url) else get_proxy_url()
    except Exception:
        return None


async def _get_html(page, url: str, label: str, max_polls: int = 40) -> str:
    """Navigate and wait until we get real content (not a bot challenge page)."""
    html = ""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print(f"[{label}] goto failed: {e}", flush=True)

    for poll in range(max_polls):
        try:
            html = await page.content()
            cur_url = page.url
        except Exception:
            html = ""
            cur_url = url
        challenge = _is_challenge(html, cur_url)
        m = re.search(r"<title[^>]*>([^<]*)</title>", html[:3000], re.IGNORECASE)
        title = (m.group(1) if m else "?")[:60]
        print(f"[{label}] poll {poll}: html_len={len(html)}, title={title!r}, challenge={challenge}", flush=True)
        if html and len(html) > 50000 and not challenge:
            break
        await asyncio.sleep(4)
    return html


def _extract_product_url(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(pat in href for pat in ["/p/", "/reviews/", "/software/"]):
            print(f"[capterra] found product link: {href}", flush=True)
            return "https://www.capterra.com" + href if href.startswith("/") else href
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


async def _try_scrape(
    company: str,
    max_reviews: int,
    sort_by: str,
    min_rating: int | None,
    proxy: str | None,
    attempt: int,
) -> list[dict] | None:
    """Single scrape attempt. Returns None if blocked at search phase (should retry with new proxy)."""
    records: list[dict] = []
    search_url = f"https://www.capterra.com/search/?query={quote_plus(company)}"

    async with AsyncCamoufox(headless=True, proxy=parse_proxy(proxy), firefox_user_prefs=FF_PREFS, geoip=True) as browser:
        page = await browser.new_page()

        html = await _get_html(page, search_url, "capterra-search", max_polls=15)
        if not html or _is_challenge(html, page.url):
            print(f"[capterra] attempt {attempt}: search blocked by CF/WAF — will retry with new proxy", flush=True)
            return None

        product_url = _extract_product_url(html)
        if not product_url:
            print(f"[capterra] attempt {attempt}: no product link found", flush=True)
            return None

        if "/reviews" in product_url:
            reviews_url = product_url.rstrip("/") + "/"
        else:
            reviews_url = product_url.rstrip("/") + "/reviews/"
        ct_sort = SORT_MAP.get(sort_by, "most_recent")
        page_num = 1

        while len(records) < max_reviews:
            url = f"{reviews_url}?sort={ct_sort}&page={page_num}"
            if min_rating:
                url += f"&rating={min_rating}"

            html = await _get_html(page, url, "capterra-reviews")
            if not html or _is_challenge(html, page.url):
                print(f"[capterra] reviews page {page_num}: no content or still challenge — retrying with new proxy", flush=True)
                return None

            import json as _json
            nd_match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>', html, re.DOTALL)
            if nd_match:
                try:
                    nd = _json.loads(nd_match.group(1))
                    pp = nd.get("props", {}).get("pageProps", {})
                    print(f"[capterra] __NEXT_DATA__ pageProps keys: {list(pp.keys())}", flush=True)
                    reviews_nd = pp.get("reviews") or pp.get("reviewList") or []
                    print(f"[capterra] __NEXT_DATA__ reviews count: {len(reviews_nd)}", flush=True)
                    if reviews_nd:
                        print(f"[capterra] first review keys: {list(reviews_nd[0].keys())}", flush=True)
                except Exception as e:
                    print(f"[capterra] __NEXT_DATA__ parse error: {e}", flush=True)
            else:
                print("[capterra] no __NEXT_DATA__", flush=True)
                idx = html.lower().find("review")
                if idx >= 0:
                    print(f"[capterra] review context: {html[max(0,idx-50):idx+300]}", flush=True)
                testids = re.findall(r'data-testid=["\']([^"\']+)["\']', html[:200000])
                print(f"[capterra] data-testid values: {list(set(testids))[:20]}", flush=True)

            page_records = _parse_reviews(html, company, product_url)
            print(f"[capterra] reviews page {page_num}: {len(page_records)} records parsed", flush=True)
            if not page_records:
                break

            records.extend(page_records)
            page_num += 1
            await asyncio.sleep(1.5)

    return records[:max_reviews]


async def scrape(
    company: str,
    max_reviews: int = 50,
    sort_by: str = "recent",
    min_rating: int | None = None,
    proxy_url: str | None = None,
    get_proxy_url=None,
    **_kwargs,
) -> list[dict]:
    _get_proxy = get_proxy_url or ((lambda: proxy_url) if proxy_url else None)

    for attempt in range(1, 4):
        proxy = await _resolve_proxy(_get_proxy)
        if proxy:
            masked = proxy.split("@")[-1] if "@" in proxy else proxy
            print(f"[capterra] attempt {attempt}: using proxy ...@{masked}", flush=True)

        result = await _try_scrape(company, max_reviews, sort_by, min_rating, proxy, attempt)
        if result is not None:
            return result
        if attempt < 3:
            print(f"[capterra] retrying with fresh proxy (attempt {attempt + 1}/3)...", flush=True)
            await asyncio.sleep(2)

    return []
