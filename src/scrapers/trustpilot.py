"""Trustpilot scraper — uses the official Trustpilot Consumer API (free tier).

Get a free API key at https://developers.trustpilot.com/
"""

import re
from datetime import datetime

import httpx

SORT_MAP = {
    "recent": "createdat.desc",
    "helpful": "createdat.desc",
    "highest": "stars.desc",
    "lowest": "stars.asc",
}

TP_API = "https://api.trustpilot.com/v1"


def _derive_domain(company: str) -> str:
    """Convert company name to likely Trustpilot domain slug."""
    slug = company.lower().strip()
    if "." in slug:
        return slug
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    return f"{slug}.com"


async def scrape(
    company: str,
    max_reviews: int = 50,
    sort_by: str = "recent",
    min_rating: int | None = None,
    proxy_url: str | None = None,
    api_key: str | None = None,
) -> list[dict]:
    if not api_key:
        print("[trustpilot] No API key — skipping. Get a free key at developers.trustpilot.com", flush=True)
        return []

    domain = _derive_domain(company)
    order_by = SORT_MAP.get(sort_by, "createdat.desc")
    records: list[dict] = []

    async with httpx.AsyncClient(
        base_url=TP_API,
        follow_redirects=True,
        timeout=30,
        proxy=proxy_url,
    ) as client:
        # Step 1: find business unit ID
        find_resp = await client.get(
            "/business-units/find",
            params={"name": domain, "apikey": api_key},
        )
        if find_resp.status_code != 200:
            print(f"[trustpilot] business-unit lookup failed: {find_resp.status_code} for {domain}", flush=True)
            return []

        biz = find_resp.json()
        biz_id = biz.get("id")
        if not biz_id:
            print(f"[trustpilot] no business unit found for {domain}", flush=True)
            return []

        # Step 2: page through reviews
        page = 1
        per_page = min(20, max_reviews)
        while len(records) < max_reviews:
            params: dict = {
                "apikey": api_key,
                "perPage": per_page,
                "page": page,
                "orderBy": order_by,
            }
            if min_rating:
                params["stars"] = min_rating

            resp = await client.get(f"/business-units/{biz_id}/reviews", params=params)
            if resp.status_code != 200:
                break

            data = resp.json()
            page_reviews = data.get("reviews") or []
            if not page_reviews:
                break

            for r in page_reviews:
                if not isinstance(r, dict):
                    continue
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
