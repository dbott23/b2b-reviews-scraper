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


def _is_hard_block(html: str) -> bool:
    """True if block can NEVER auto-resolve (CAPTCHA, IP ban). Bail immediately."""
    m = re.search(r"<title[^>]*>([^<]*)</title>", html[:3000], re.IGNORECASE)
    title = m.group(1).lower().strip() if m else ""
    return "attention required" in title or "access denied" in title or "403 forbidden" in title


def _is_challenge(html: str, url: str) -> bool:
    m = re.search(r"<title[^>]*>([^<]*)</title>", html[:3000], re.IGNORECASE)
    title = m.group(1).lower().strip() if m else ""
    snippet = html[:8000]
    return (
        any(s in title for s in _CHALLENGE_TITLES)
        or "__cf_chl_rt_tk" in url
        or "__cf_chl" in snippet
        or "cf-challenge" in snippet
        or "challenge-platform" in snippet
    )


async def _resolve_proxy(get_proxy_url) -> str | None:
    if not get_proxy_url:
        return None
    try:
        return await get_proxy_url() if asyncio.iscoroutinefunction(get_proxy_url) else get_proxy_url()
    except Exception:
        return None


async def _get_html(page, url: str, label: str, max_polls: int = 20) -> str:
    """Navigate and wait until we get real content (not a bot challenge page)."""
    html = ""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print(f"[{label}] goto failed: {e}", flush=True)

    prev_challenge = True
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
        if challenge and _is_hard_block(html):
            print(f"[{label}] hard block (Attention Required / Access Denied) — bailing immediately", flush=True)
            break
        if not challenge:
            if len(html) > 50000 or "__NEXT_DATA__" in html:
                break
            # CF just resolved but page still loading — wait for network idle
            if prev_challenge:
                print(f"[{label}] CF resolved, waiting for page load...", flush=True)
                prev_challenge = False  # only call networkidle once
                try:
                    await page.wait_for_load_state("networkidle", timeout=20000)
                except Exception:
                    pass
                continue
        else:
            prev_challenge = True
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


async def _wait_for_content(page, label: str, max_polls: int = 20) -> str:
    """Poll page.content() until we have large non-challenge HTML."""
    html = ""
    prev_challenge = True
    for poll in range(max_polls):
        try:
            html = await page.content()
            cur_url = page.url
        except Exception:
            html = ""
            cur_url = ""
        challenge = _is_challenge(html, cur_url)
        m = re.search(r"<title[^>]*>([^<]*)</title>", html[:3000], re.IGNORECASE)
        title = (m.group(1) if m else "?")[:60]
        print(f"[{label}] poll {poll}: html_len={len(html)}, title={title!r}, challenge={challenge}", flush=True)
        if challenge and _is_hard_block(html):
            print(f"[{label}] hard block (Attention Required / Access Denied) — bailing immediately", flush=True)
            break
        if not challenge:
            if len(html) > 50000 or "__NEXT_DATA__" in html:
                break
            if prev_challenge:
                print(f"[{label}] CF resolved, waiting for page load...", flush=True)
                prev_challenge = False  # only call networkidle once
                try:
                    await page.wait_for_load_state("networkidle", timeout=20000)
                except Exception:
                    pass
                continue
        else:
            prev_challenge = True
        await asyncio.sleep(4)
    return html


def _parse_reviews(html: str, company: str, product_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    records = []

    container = soup.select_one('[data-test-id="review-cards-container"]')
    if not container:
        return records

    cards = container.select(":scope > div")
    print(f"[capterra] found {len(cards)} review card divs in container", flush=True)

    for card in cards:
        # Reviewer name from profile pic alt (e.g., "Miguel J. D. avatar")
        pic = card.find(attrs={"data-testid": "reviewer-profile-pic"})
        reviewer_name = None
        if pic:
            alt = pic.get("alt", "")
            reviewer_name = alt.replace(" avatar", "").strip() or None

        # Rating from data-testid
        rating = None
        for testid in ("Overall Rating-rating", "rating"):
            rating_el = card.find(attrs={"data-testid": testid})
            if rating_el:
                m = re.search(r"(\d[\d.]*)", rating_el.get_text(strip=True) or rating_el.get("aria-label", ""))
                if m:
                    try:
                        rating = float(m.group(1))
                        break
                    except ValueError:
                        pass

        # Title — h3 or first heading
        title_el = card.find(["h3", "h2"])
        title = title_el.get_text(strip=True) if title_el else None

        # Pros/cons: look for elements containing "Pros" / "Cons" labels
        pros = cons = body = None
        for p in card.find_all("p"):
            text = p.get_text(strip=True)
            prev = p.find_previous_sibling()
            prev_text = prev.get_text(strip=True).lower() if prev else ""
            if "pros" in prev_text:
                pros = text
            elif "cons" in prev_text:
                cons = text

        # Body: largest paragraph or div text
        paragraphs = [el.get_text(strip=True) for el in card.find_all("p") if len(el.get_text(strip=True)) > 50]
        if paragraphs:
            body = max(paragraphs, key=len)

        date_el = card.find("time")
        date_str = date_el.get("datetime", "") if date_el else ""
        try:
            date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date().isoformat()
        except Exception:
            date = date_str or None

        review_link = card.find("a", href=lambda h: h and "/reviews/" in h)
        review_url = None
        if review_link:
            href = review_link.get("href", "")
            review_url = "https://www.capterra.com" + href if href.startswith("/") else href

        if not (title or body or reviewer_name):
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


def _extract_reviews_from_next_data(html: str, company: str, product_url: str) -> list[dict]:
    import json as _json
    nd_match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>', html, re.DOTALL)
    if not nd_match:
        print("[capterra] no __NEXT_DATA__", flush=True)
        return []
    try:
        nd = _json.loads(nd_match.group(1))
        pp = nd.get("props", {}).get("pageProps", {})
        print(f"[capterra] __NEXT_DATA__ pageProps keys: {list(pp.keys())}", flush=True)
        raw = pp.get("reviews") or pp.get("reviewList") or []
        print(f"[capterra] __NEXT_DATA__ reviews count: {len(raw)}", flush=True)
        if not raw:
            return []
        print(f"[capterra] first review keys: {list(raw[0].keys())}", flush=True)
        records = []
        for r in raw:
            rating_val = r.get("overallRating") or r.get("rating") or r.get("stars")
            date_str = r.get("publishDate") or r.get("publishedDate") or r.get("createdAt") or ""
            try:
                date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date().isoformat()
            except Exception:
                date = date_str or None
            reviewer = r.get("reviewer") or r.get("author") or {}
            records.append({
                "company": company,
                "platform": "capterra",
                "reviewer_name": reviewer.get("fullName") or reviewer.get("name") if isinstance(reviewer, dict) else str(reviewer),
                "reviewer_title": reviewer.get("title") if isinstance(reviewer, dict) else None,
                "reviewer_company_size": (reviewer.get("company") or {}).get("size") if isinstance(reviewer, dict) else None,
                "rating": float(rating_val) if rating_val else None,
                "title": r.get("title"),
                "body": r.get("description") or r.get("body") or r.get("text"),
                "pros": r.get("pros"),
                "cons": r.get("cons"),
                "date": date,
                "verified": bool(r.get("verified")),
                "helpful_count": r.get("helpfulCount"),
                "review_url": product_url,
                "product_url": product_url,
            })
        return records
    except Exception as e:
        print(f"[capterra] __NEXT_DATA__ parse error: {e}", flush=True)
        return []


async def _try_scrape(
    company: str,
    max_reviews: int,
    sort_by: str,
    min_rating: int | None,
    proxy: str | None,
    attempt: int,
) -> list[dict] | None:
    """Single scrape attempt. Returns None if blocked (should retry with new proxy)."""
    import json as _json
    records: list[dict] = []
    api_reviews: list[dict] = []
    search_url = f"https://www.capterra.com/search/?query={quote_plus(company)}"

    async with AsyncCamoufox(headless=True, proxy=parse_proxy(proxy), firefox_user_prefs=FF_PREFS, geoip=True) as browser:
        page = await browser.new_page()

        # Intercept API responses to capture reviews data loaded client-side
        async def _on_response(response) -> None:
            url = response.url
            if "review" not in url.lower() and "listing" not in url.lower():
                return
            if response.status != 200:
                return
            try:
                ct = (response.headers.get("content-type") or "")
                if "json" not in ct:
                    return
                data = await response.json()
                print(f"[capterra] API: {url} → {type(data).__name__} keys={list(data.keys()) if isinstance(data, dict) else len(data)}", flush=True)
                reviews_list = None
                if isinstance(data, list):
                    reviews_list = data
                elif isinstance(data, dict):
                    reviews_list = data.get("reviews") or data.get("reviewList") or data.get("data") or []
                if reviews_list and isinstance(reviews_list, list) and reviews_list:
                    print(f"[capterra] API captured {len(reviews_list)} reviews, first keys: {list(reviews_list[0].keys()) if reviews_list else []}", flush=True)
                    api_reviews.extend(reviews_list)
            except Exception as e:
                print(f"[capterra] API intercept error: {e}", flush=True)

        page.on("response", _on_response)

        # Step 1: load search page (usually no CF)
        html = await _get_html(page, search_url, "capterra-search", max_polls=15)
        if not html or _is_challenge(html, page.url):
            print(f"[capterra] attempt {attempt}: search blocked — retrying with new proxy", flush=True)
            return None

        product_url = _extract_product_url(html)
        if not product_url:
            print(f"[capterra] attempt {attempt}: no product link found", flush=True)
            return None

        if "/reviews" in product_url:
            reviews_base = product_url.rstrip("/") + "/"
        else:
            reviews_base = product_url.rstrip("/") + "/reviews/"
        ct_sort = SORT_MAP.get(sort_by, "most_recent")

        # Step 2: click the product link (sends proper Referer, avoids extra page.goto CF trigger)
        product_path = product_url.replace("https://www.capterra.com", "")
        try:
            link_el = await page.query_selector(f'a[href="{product_path}"], a[href="{product_url}"]')
            if link_el:
                print(f"[capterra] clicking product link: {product_path}", flush=True)
                await link_el.click()
            else:
                print(f"[capterra] no clickable link found, using goto: {product_url}", flush=True)
                await page.goto(product_url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"[capterra] click/goto failed: {e}, falling back to goto", flush=True)
            try:
                await page.goto(product_url, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                pass

        html = await _wait_for_content(page, "capterra-product", max_polls=20)
        if not html or _is_challenge(html, page.url):
            print(f"[capterra] attempt {attempt}: product page blocked — retrying with new proxy", flush=True)
            return None

        # Step 3: navigate to reviews page via click or goto
        page_num = 1
        while len(records) < max_reviews:
            url = f"{reviews_base}?sort={ct_sort}&page={page_num}"
            if min_rating:
                url += f"&rating={min_rating}"

            # CF cookies are already set from product page — goto to reviews URL should work
            html = await _get_html(page, url, "capterra-reviews")

            if not html or _is_challenge(html, page.url):
                print(f"[capterra] reviews page {page_num}: blocked — retrying with new proxy", flush=True)
                return None

            # Wait a moment for any API calls to complete
            await asyncio.sleep(3)

            page_records = _extract_reviews_from_next_data(html, company, product_url)
            print(f"[capterra] reviews page {page_num}: {len(page_records)} records from __NEXT_DATA__", flush=True)

            if not page_records and api_reviews:
                print(f"[capterra] using {len(api_reviews)} reviews from API intercept", flush=True)
                # Convert api_reviews to standard format (diagnostic first)
                page_records = api_reviews[:max_reviews]
                print(f"[capterra] first API review keys: {list(page_records[0].keys()) if page_records else []}", flush=True)

            if not page_records:
                # Fallback to CSS parsing
                page_records = _parse_reviews(html, company, product_url)
                print(f"[capterra] reviews page {page_num}: {len(page_records)} records (CSS fallback)", flush=True)

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
