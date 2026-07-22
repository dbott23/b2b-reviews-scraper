# B2B Reviews Scraper — Next Steps

## Current Status

| Platform   | Status | Notes |
|------------|--------|-------|
| Trustpilot | ✅ Working | 50 reviews/company in ~30s via `__NEXT_DATA__` JSON. Handles AWS WAF auto-resolve. |
| Capterra   | ❌ Blocked | Cloudflare JS challenge ("Just a moment…") never resolves on Apify's residential proxy IP range. All 3 retry attempts fail. |
| G2         | ❌ Blocked | DataDome bot detection. Not properly attempted — falls back to 0 reviews. |

The Trustpilot scraper lives in `src/scrapers/trustpilot.py` and is published separately
as `trustpilot-reviews-scraper` at `/Users/darren/Documents/apify-actors/trustpilot-reviews-scraper`.


## Why Capterra Fails

Capterra uses Cloudflare Turnstile on product pages (`/p/{id}/{name}/`). The search page
(`/search/?query=...`) loads fine, but clicking into any product page triggers a CF challenge
that camoufox (Firefox anti-detect browser) cannot solve on Apify's residential proxy IPs.

The exit IPs from Apify's residential proxy pool appear to be flagged by Capterra's CF — this
is common when many people scrape the same site through the same proxy provider.

The scraper currently:
1. Loads search page (works fine)
2. Clicks product link → CF challenge fires
3. Polls up to 20 times (80 seconds) for CF to auto-resolve
4. Returns None → retries with new proxy IP (up to 3 times)
5. All 3 attempts fail → returns 0 reviews

### Fix Option A — `curl_cffi` (try first, lowest effort)
Replace the camoufox browser with `curl_cffi`, which impersonates Chrome's exact TLS/HTTP2
fingerprint. CF often passes it without a JS challenge because it looks like real Chrome at
the network layer. No browser overhead, much faster.

```python
# Install: pip install curl_cffi
from curl_cffi.requests import AsyncSession

async with AsyncSession(impersonate="chrome124") as s:
    r = await s.get("https://www.capterra.com/p/61368/Salesforce/reviews/",
                    headers={"Referer": "https://www.capterra.com/search/?query=Salesforce"})
    html = r.text
```

If this returns the real page HTML (look for `data-test-id="review-cards-container"`), the
approach works and you can replace the whole camoufox flow with this much simpler code.
The review parsing logic in `_parse_reviews()` stays the same.

### Fix Option B — Better proxy provider
Swap Apify's residential proxies for Brightdata or Oxylabs. Their IP pools are fresher and
less flagged. You'd accept a proxy URL as actor input instead of using Apify's built-in proxy.
Cost: ~$15-20/GB. More complex setup for users.

### Fix Option C — Paid anti-bot browser service
Use Browserless.io or Rebrowser instead of camoufox. These are managed browser services
with built-in CF bypass. Cost ~$10-50/month. Highest reliability but adds an external dependency.


## Why G2 Fails

G2 uses DataDome bot detection. The current `src/scrapers/g2.py` uses camoufox and checks
for DataDome blocks, but doesn't actually solve the challenge — it just stops.

### Fix Option A — `curl_cffi`
Same as Capterra: try Chrome TLS impersonation first. DataDome is also fingerprint-based.
G2 reviews page: `https://www.g2.com/products/salesforce/reviews`

### Fix Option B — G2 has structured data
G2 embeds `application/ld+json` schema markup on review pages. If curl_cffi gets the HTML
through, parsing is straightforward — look for `<script type="application/ld+json">` tags
with `"@type": "Review"`.


## How to Test a Fix

Once you have a candidate approach, test it locally first before deploying:

```bash
cd /Users/darren/Documents/apify-actors/b2b-reviews-scraper
python3 -c "
import asyncio
from src.scrapers.capterra import scrape
results = asyncio.run(scrape('Salesforce', max_reviews=5))
print(f'Got {len(results)} reviews')
for r in results[:2]:
    print(r['reviewer_name'], r['rating'], r['title'][:50])
"
```

Then:
1. `git add . && git commit -m "description" && git push origin main`
2. Trigger a build: `POST https://api.apify.com/v2/acts/TwomhLf1Y5aai71Og/builds?token=TOKEN&version=0.0&tag=latest`
3. Poll build until `SUCCEEDED`
4. Trigger a run: `POST https://api.apify.com/v2/acts/TwomhLf1Y5aai71Og/runs?token=TOKEN&timeout=600`
   with body `{"companies":["Salesforce"],"platforms":["capterra"],"max_reviews_per_platform":5}`
5. Check logs: `GET https://api.apify.com/v2/logs/{RUN_ID}?token=TOKEN`
6. Check results: get dataset ID from run, then `GET https://api.apify.com/v2/datasets/{DATASET_ID}/items?token=TOKEN`

API token: store securely, don't commit to git.
Actor ID: `TwomhLf1Y5aai71Og`
Git remote: `git@dbott23-GitHub:dbott23/b2b-reviews-scraper.git`


## Output Schema (all platforms use the same fields)

| Field | Type | Notes |
|-------|------|-------|
| `company` | string | The input company name |
| `platform` | string | `trustpilot`, `capterra`, or `g2` |
| `reviewer_name` | string/null | |
| `reviewer_title` | string/null | Job title (Capterra/G2 only) |
| `reviewer_company_size` | string/null | Company size (G2 only) |
| `rating` | float/null | 1.0–5.0 |
| `title` | string/null | Review headline |
| `body` | string/null | Main review text |
| `pros` | string/null | Capterra/G2 only |
| `cons` | string/null | Capterra/G2 only |
| `date` | string/null | ISO date `YYYY-MM-DD` |
| `verified` | bool | |
| `helpful_count` | int/null | G2 only |
| `review_url` | string | Direct link to the review |
| `product_url` | string | Product page on the review site |


## Trustpilot-Only Actor (published)

The working version is at:
`/Users/darren/Documents/apify-actors/trustpilot-reviews-scraper`

To publish it on Apify:
1. Create a new actor on apify.com
2. Connect it to a new GitHub repo (e.g. `dbott23/trustpilot-reviews-scraper`)
3. Copy the files from the trustpilot-reviews-scraper directory into that repo
4. Push to main → Apify auto-builds
5. Test a run in the Apify console
6. Set pricing (pay-per-result works well: charge per review scraped)
