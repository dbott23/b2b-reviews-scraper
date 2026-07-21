"""Capterra scraper — uses Playwright (real browser) for Cloudflare-protected pages."""

import asyncio
import re
from datetime import datetime
from urllib.parse import quote_plus

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from src.scrapers._stealth import apply_stealth

SORT_MAP = {
    "recent": "most_recent",
    "helpful": "most_helpful",
    "highest": "highest_rating",
    "lowest": "lowest_rating",
}


async def _resolve_proxy(get_proxy_url) -> str | None:
    if not get_proxy_url:
        return None
    try:
        return await get_proxy_url() if asyncio.iscoroutinefunction(get_proxy_url) else get_proxy_url()
    except Exception:
        return None


async def _search_product_url(company: str, get_proxy_url=None) -> str | None:
    proxy = await _resolve_proxy(get_proxy_url)
    if proxy:
        masked = proxy.split("@")[-1] if "@" in proxy else proxy
        print(f"[capterra] search using proxy: ...@{masked}", flush=True)

    async with async_playwright() as pw:
        launch_args = [
            "--no-sandbox", "--disable-setuid-sandbox",
            "--disable-blink-features=AutomationControlled",
        ]
        browser = await pw.chromium.launch(headless=True, args=launch_args)
        # No custom user_agent — use Playwright's default to avoid version mismatch
        ctx_opts: dict = {}
        if proxy:
            ctx_opts["proxy"] = {"server": proxy}
        context = await browser.new_context(**ctx_opts)
        page = await context.new_page()
        await apply_stealth(page)

        url = f"https://www.capterra.com/search/?query={quote_plus(company)}"
        html = ""
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"[capterra] goto failed: {e}", flush=True)

        # Poll for content — Cloudflare challenge may take time to resolve
        for poll in range(15):
            try:
                html = await page.content()
            except Exception:
                html = ""
            print(f"[capterra] poll {poll}: url={page.url}, html_len={len(html)}", flush=True)
            if html and len(html) > 500:
                break
            await asyncio.sleep(4)

        final_url = page.url
        await browser.close()

    print(f"[capterra] final URL: {final_url}, html length: {len(html)}", flush=True)
    print(f"[capterra] html preview: {html[:500]}", flush=True)

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
        # Rating
        rating = None
        rating_el = card.select_one("[aria-label*='star'], [class*='rating']")
        if rating_el:
            m = re.search(r"(\d[\d.]*)", rating_el.get("aria-label", "") or rating_el.text)
            if m:
                try:
                    rating = float(m.group(1))
                except ValueError:
                    pass

        # Title
        title_el = card.select_one("h3, [class*='title'], [class*='headline']")
        title = title_el.get_text(strip=True) if title_el else None

        # Body / pros / cons
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

        # Reviewer
        reviewer_el = card.select_one("[class*='reviewer'], [class*='author']")
        reviewer_name = reviewer_el.get_text(strip=True) if reviewer_el else None

        # Date
        date_el = card.select_one("time, [class*='date']")
        date_str = ""
        if date_el:
            date_str = date_el.get("datetime") or date_el.get_text(strip=True)
        try:
            date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date().isoformat()
        except Exception:
            date = date_str or None

        # Review URL
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

    _get_proxy = get_proxy_url or (lambda: proxy_url) if proxy_url else None
    product_url = await _search_product_url(company, _get_proxy)
    if not product_url:
        return []

    proxy = await _resolve_proxy(_get_proxy)
    async with async_playwright() as pw:
        launch_args = [
            "--no-sandbox", "--disable-setuid-sandbox",
            "--disable-blink-features=AutomationControlled",
        ]
        browser = await pw.chromium.launch(headless=True, args=launch_args)
        # No custom user_agent — use Playwright's default to avoid version mismatch
        ctx_opts: dict = {}
        if proxy:
            ctx_opts["proxy"] = {"server": proxy}
        context = await browser.new_context(**ctx_opts)
        page = await context.new_page()
        await apply_stealth(page)

        # Build reviews URL — product_url may already contain /reviews/
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

            html = ""
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                print(f"[capterra] reviews goto failed page {page_num}: {e}", flush=True)

            # Poll for content — Cloudflare challenge may take time to resolve
            for poll in range(10):
                try:
                    html = await page.content()
                except Exception:
                    html = ""
                print(f"[capterra] reviews poll {poll}: url={page.url}, html_len={len(html)}", flush=True)
                if html and len(html) > 500:
                    break
                await asyncio.sleep(4)

            if not html:
                break

            print(f"[capterra] reviews html preview: {html[:300]}", flush=True)
            page_records = _parse_reviews(html, company, product_url)

            if not page_records:
                break

            records.extend(page_records)
            page_num += 1
            await asyncio.sleep(1.5)

        await browser.close()

    return records[:max_reviews]
