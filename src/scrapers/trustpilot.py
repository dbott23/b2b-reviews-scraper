"""Trustpilot scraper.

Primary: official Consumer API (free key at developers.trustpilot.com) — structured, reliable.
Fallback: Playwright web scraper — works without a key, less structured.
"""

import asyncio
import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup
from camoufox.async_api import AsyncCamoufox

SORT_MAP_API = {
    "recent": "createdat.desc",
    "helpful": "createdat.desc",
    "highest": "stars.desc",
    "lowest": "stars.asc",
}

TP_API = "https://api.trustpilot.com/v1"


def _derive_domain(company: str) -> str:
    slug = company.lower().strip()
    if "." in slug:
        return slug
    slug = re.sub(r"[^a-z0-9-]", "", slug.replace(" ", ""))
    return f"{slug}.com"


def _extract_next_data_reviews(next_data: dict, company: str, product_url: str) -> list[dict]:
    """Extract reviews from Trustpilot's embedded __NEXT_DATA__ JSON (most reliable)."""
    pp = (next_data or {}).get("props", {}).get("pageProps", {})
    # Reviews can be at several paths depending on page version
    raw = (
        pp.get("reviews")
        or pp.get("reviewsList")
        or (pp.get("businessUnit") or {}).get("reviews")
        or []
    )
    records = []
    for r in raw:
        rating_val = r.get("rating") or r.get("stars")
        date_str = r.get("dates", {}).get("publishedDate") or r.get("createdAt") or ""
        try:
            date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date().isoformat()
        except Exception:
            date = date_str or None

        consumer = r.get("consumer") or {}
        labels = r.get("labels") or {}
        verified = bool(labels.get("verification") or labels.get("verified"))

        review_id = r.get("id") or ""
        review_url = f"https://www.trustpilot.com/reviews/{review_id}" if review_id else product_url

        records.append({
            "company": company,
            "platform": "trustpilot",
            "reviewer_name": consumer.get("displayName"),
            "reviewer_title": None,
            "reviewer_company_size": None,
            "rating": float(rating_val) if rating_val else None,
            "title": r.get("title"),
            "body": r.get("text"),
            "pros": None,
            "cons": None,
            "date": date,
            "verified": verified,
            "helpful_count": None,
            "review_url": review_url,
            "product_url": product_url,
        })
    return records


def _parse_web_reviews(html: str, company: str, product_url: str) -> list[dict]:
    """Fallback: parse Trustpilot HTML with BeautifulSoup."""
    soup = BeautifulSoup(html, "html.parser")
    records = []

    # Try JSON-LD structured data first — more stable than CSS classes
    for script in soup.select("script[type='application/ld+json']"):
        try:
            import json
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") != "Review":
                    continue
                rating_val = (item.get("reviewRating") or {}).get("ratingValue")
                date_str = item.get("datePublished") or ""
                try:
                    date = datetime.fromisoformat(date_str).date().isoformat()
                except Exception:
                    date = date_str or None
                author = item.get("author") or {}
                records.append({
                    "company": company,
                    "platform": "trustpilot",
                    "reviewer_name": author.get("name") if isinstance(author, dict) else str(author),
                    "reviewer_title": None,
                    "reviewer_company_size": None,
                    "rating": float(rating_val) if rating_val else None,
                    "title": item.get("name"),
                    "body": item.get("reviewBody"),
                    "pros": None,
                    "cons": None,
                    "date": date,
                    "verified": False,
                    "helpful_count": None,
                    "review_url": product_url,
                    "product_url": product_url,
                })
        except Exception:
            continue

    if records:
        return records

    # Last resort: CSS selectors
    for card in soup.select("[data-service-review-card-paper], article[class*='reviewCard']"):
        rating = None
        star_el = card.select_one("[class*='star-rating'] img, [data-service-review-rating]")
        if star_el:
            src = star_el.get("src", "") or star_el.get("data-service-review-rating", "")
            m = re.search(r"(\d)", src)
            if m:
                rating = float(m.group(1))

        title_el = card.select_one("h2")
        title = title_el.get_text(strip=True) if title_el else None

        body_el = card.select_one("p")
        body = body_el.get_text(strip=True) if body_el else None

        name_el = card.select_one("[class*='consumerName'], [data-consumer-name-typography]")
        reviewer_name = name_el.get_text(strip=True) if name_el else None

        date_el = card.select_one("time")
        date_str = date_el.get("datetime", "") if date_el else ""
        try:
            date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date().isoformat()
        except Exception:
            date = date_str or None

        if not (title or body):
            continue

        records.append({
            "company": company,
            "platform": "trustpilot",
            "reviewer_name": reviewer_name,
            "reviewer_title": None,
            "reviewer_company_size": None,
            "rating": rating,
            "title": title,
            "body": body,
            "pros": None,
            "cons": None,
            "date": date,
            "verified": False,
            "helpful_count": None,
            "review_url": product_url,
            "product_url": product_url,
        })

    return records


async def _scrape_web(
    company: str,
    max_reviews: int,
    sort_by: str,
    min_rating: int | None,
    get_proxy_url=None,
) -> list[dict]:
    import json as _json
    domain = _derive_domain(company)
    sort_param = {"recent": "recency", "highest": "stars_desc", "lowest": "stars_asc"}.get(sort_by, "recency")
    product_url = f"https://www.trustpilot.com/review/{domain}"
    records: list[dict] = []

    proxy = None
    if get_proxy_url:
        try:
            proxy = await get_proxy_url() if asyncio.iscoroutinefunction(get_proxy_url) else get_proxy_url()
        except Exception:
            pass

    if proxy:
        masked = proxy.split("@")[-1] if "@" in proxy else proxy
        print(f"[trustpilot] using proxy: ...@{masked}", flush=True)
    else:
        print("[trustpilot] no proxy — direct connection", flush=True)

    page_num = 1
    proxy_opts = {"server": proxy} if proxy else None
    async with AsyncCamoufox(headless=True, proxy=proxy_opts, firefox_user_prefs={"security.sandbox.content.level": 0}) as browser:
        page = await browser.new_page()

        while len(records) < max_reviews:
            url = f"{product_url}?sort={sort_param}&page={page_num}"
            html = ""
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                print(f"[trustpilot] goto failed page {page_num}: {e}", flush=True)

            for poll in range(15):
                try:
                    html = await page.content()
                except Exception:
                    html = ""
                print(f"[trustpilot] poll {poll}: url={page.url}, html_len={len(html)}", flush=True)
                if html and len(html) > 500:
                    break
                await asyncio.sleep(4)

            print(f"[trustpilot] html preview: {html[:300]}", flush=True)

            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>', html, re.DOTALL)
            if m:
                try:
                    next_data = _json.loads(m.group(1))
                    pp = (next_data or {}).get("props", {}).get("pageProps", {})
                    print(f"[trustpilot] __NEXT_DATA__ found, pageProps keys: {list(pp.keys())}", flush=True)
                    page_records = _extract_next_data_reviews(next_data, company, product_url)
                    print(f"[trustpilot] extracted {len(page_records)} records from __NEXT_DATA__", flush=True)
                except Exception as e:
                    print(f"[trustpilot] __NEXT_DATA__ parse error: {e}", flush=True)
                    page_records = _parse_web_reviews(html, company, product_url)
            else:
                print("[trustpilot] no __NEXT_DATA__ found, falling back to HTML parse", flush=True)
                page_records = _parse_web_reviews(html, company, product_url)
                print(f"[trustpilot] extracted {len(page_records)} records from HTML", flush=True)

            if not page_records:
                break

            records.extend(page_records)
            page_num += 1
            await asyncio.sleep(1.5)

    return records[:max_reviews]


async def _scrape_api(
    company: str,
    max_reviews: int,
    sort_by: str,
    min_rating: int | None,
    api_key: str,
) -> list[dict]:
    domain = _derive_domain(company)
    order_by = SORT_MAP_API.get(sort_by, "createdat.desc")
    records: list[dict] = []

    async with httpx.AsyncClient(base_url=TP_API, follow_redirects=True, timeout=30) as client:
        find_resp = await client.get("/business-units/find", params={"name": domain, "apikey": api_key})
        if find_resp.status_code != 200:
            return []
        biz_id = find_resp.json().get("id")
        if not biz_id:
            return []

        product_url = f"https://www.trustpilot.com/review/{domain}"
        page = 1
        per_page = min(20, max_reviews)

        while len(records) < max_reviews:
            params: dict = {"apikey": api_key, "perPage": per_page, "page": page, "orderBy": order_by}
            if min_rating:
                params["stars"] = min_rating

            resp = await client.get(f"/business-units/{biz_id}/reviews", params=params)
            if resp.status_code != 200:
                break

            page_reviews = resp.json().get("reviews") or []
            if not page_reviews:
                break

            for r in page_reviews:
                date_str = r.get("createdAt") or ""
                try:
                    date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date().isoformat()
                except Exception:
                    date = date_str or None

                consumer = r.get("consumer") or {}
                rating_val = r.get("stars")
                records.append({
                    "company": company,
                    "platform": "trustpilot",
                    "reviewer_name": consumer.get("displayName"),
                    "reviewer_title": None,
                    "reviewer_company_size": None,
                    "rating": float(rating_val) if rating_val else None,
                    "title": r.get("title"),
                    "body": r.get("text"),
                    "pros": None,
                    "cons": None,
                    "date": date,
                    "verified": bool((r.get("labels") or {}).get("verification")),
                    "helpful_count": None,
                    "review_url": f"https://www.trustpilot.com/reviews/{r.get('id', '')}",
                    "product_url": f"https://www.trustpilot.com/review/{domain}",
                })

            if len(page_reviews) < per_page:
                break
            page += 1

    return records[:max_reviews]


async def scrape(
    company: str,
    max_reviews: int = 50,
    sort_by: str = "recent",
    min_rating: int | None = None,
    proxy_url: str | None = None,
    api_key: str | None = None,
    get_proxy_url=None,
    **_kwargs,
) -> list[dict]:
    if api_key:
        return await _scrape_api(company, max_reviews, sort_by, min_rating, api_key)
    _get_proxy = get_proxy_url or ((lambda: proxy_url) if proxy_url else None)
    return await _scrape_web(company, max_reviews, sort_by, min_rating, _get_proxy)
