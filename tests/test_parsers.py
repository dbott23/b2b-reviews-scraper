"""Unit tests for review parsers — use static HTML fixtures, no network calls."""

import json
import pytest

from src.scrapers.trustpilot import _parse_reviews as tp_parse
from src.scrapers.capterra import _parse_reviews as ct_parse
from src.scrapers.g2 import _parse_reviews as g2_parse


# ── Trustpilot ────────────────────────────────────────────────────────────────

TP_HTML = """
<html><body>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "LocalBusiness",
  "review": [
    {
      "@type": "Review",
      "name": "Great product",
      "reviewBody": "Works really well for our team.",
      "datePublished": "2025-03-15T00:00:00Z",
      "reviewRating": {"ratingValue": 5},
      "author": {"@type": "Person", "name": "Alice B."},
      "url": "https://www.trustpilot.com/reviews/abc123"
    },
    {
      "@type": "Review",
      "name": "Decent but pricey",
      "reviewBody": "Good features, wish it was cheaper.",
      "datePublished": "2025-02-01T00:00:00Z",
      "reviewRating": {"ratingValue": 3},
      "author": {"@type": "Person", "name": "Bob K."}
    }
  ]
}
</script>
</body></html>
"""


class TestTrustpilotParser:
    def test_extracts_two_reviews(self):
        records = tp_parse(TP_HTML, "Acme", "https://www.trustpilot.com/review/acme.com")
        assert len(records) == 2

    def test_fields_populated(self):
        r = tp_parse(TP_HTML, "Acme", "https://www.trustpilot.com/review/acme.com")[0]
        assert r["platform"] == "trustpilot"
        assert r["company"] == "Acme"
        assert r["rating"] == 5.0
        assert r["title"] == "Great product"
        assert "team" in r["body"]
        assert r["reviewer_name"] == "Alice B."
        assert r["date"] == "2025-03-15"
        assert r["verified"] is True

    def test_review_url_from_data(self):
        r = tp_parse(TP_HTML, "Acme", "https://www.trustpilot.com/review/acme.com")[0]
        assert r["review_url"] == "https://www.trustpilot.com/reviews/abc123"

    def test_review_url_fallback(self):
        r = tp_parse(TP_HTML, "Acme", "https://www.trustpilot.com/review/acme.com")[1]
        assert r["review_url"] == "https://www.trustpilot.com/review/acme.com"

    def test_null_fields_when_absent(self):
        r = tp_parse(TP_HTML, "Acme", "https://www.trustpilot.com/review/acme.com")[0]
        assert r["pros"] is None
        assert r["cons"] is None
        assert r["reviewer_title"] is None

    def test_empty_html_returns_empty_list(self):
        assert tp_parse("<html><body></body></html>", "Acme", "http://x.com") == []


# ── Capterra ──────────────────────────────────────────────────────────────────

CT_HTML = """
<html><body>
  <article class="review-card">
    <h3>Easy to use</h3>
    <span aria-label="4.5 stars out of 5"></span>
    <p class="body">We love this product.</p>
    <div class="pros">Very intuitive interface.</div>
    <div class="cons">Mobile app needs work.</div>
    <span class="reviewer">Carol D.</span>
    <time datetime="2025-04-10">April 10, 2025</time>
  </article>
  <article class="review-card">
    <h3>Solid tool</h3>
    <span aria-label="4 stars out of 5"></span>
    <p class="body">Does what it says.</p>
    <span class="reviewer">Dan E.</span>
    <time datetime="2025-01-20">Jan 20, 2025</time>
  </article>
</body></html>
"""


class TestCapterraParser:
    def test_extracts_two_reviews(self):
        records = ct_parse(CT_HTML, "Acme", "https://www.capterra.com/p/123/acme/")
        assert len(records) == 2

    def test_fields_populated(self):
        r = ct_parse(CT_HTML, "Acme", "https://www.capterra.com/p/123/acme/")[0]
        assert r["platform"] == "capterra"
        assert r["title"] == "Easy to use"
        assert r["rating"] == 4.5
        assert "love" in r["body"]
        assert r["pros"] == "Very intuitive interface."
        assert r["cons"] == "Mobile app needs work."
        assert r["date"] == "2025-04-10"

    def test_no_pros_cons_when_absent(self):
        r = ct_parse(CT_HTML, "Acme", "https://www.capterra.com/p/123/acme/")[1]
        assert r["pros"] is None
        assert r["cons"] is None

    def test_skips_cards_with_no_content(self):
        html = "<html><body><article class='review-card'></article></body></html>"
        assert ct_parse(html, "Acme", "http://x.com") == []


# ── G2 ────────────────────────────────────────────────────────────────────────

G2_HTML = """
<html><body>
  <div itemprop="review">
    <meta itemprop="name" content="Best PM tool we've used">
    <meta itemprop="ratingValue" content="5">
    <meta itemprop="datePublished" content="2025-05-01">
    <span itemprop="author">Eve F.</span>
    <span class="mt-4th">Product Manager</span>
    <div itemprop="reviewBody">Comprehensive and easy to set up.</div>
    <div data-field-name="like_best_answer">Automation features.</div>
    <div data-field-name="dislike_answer">Reporting could be better.</div>
  </div>
  <div itemprop="review">
    <meta itemprop="name" content="Good but expensive">
    <meta itemprop="ratingValue" content="3">
    <meta itemprop="datePublished" content="2025-03-10">
    <span itemprop="author">Frank G.</span>
    <div data-field-name="like_best_answer">Clean UI.</div>
    <div data-field-name="dislike_answer">Too pricey for small teams.</div>
  </div>
</body></html>
"""


class TestG2Parser:
    def test_extracts_two_reviews(self):
        records = g2_parse(G2_HTML, "Acme", "https://www.g2.com/products/acme/reviews")
        assert len(records) == 2

    def test_fields_populated(self):
        r = g2_parse(G2_HTML, "Acme", "https://www.g2.com/products/acme/reviews")[0]
        assert r["platform"] == "g2"
        assert r["title"] == "Best PM tool we've used"
        assert r["rating"] == 5.0
        assert r["reviewer_name"] == "Eve F."
        assert r["reviewer_title"] == "Product Manager"
        assert r["pros"] == "Automation features."
        assert r["cons"] == "Reporting could be better."
        assert r["date"] == "2025-05-01"
        assert r["verified"] is True

    def test_body_falls_back_to_pros_cons(self):
        r = g2_parse(G2_HTML, "Acme", "https://www.g2.com/products/acme/reviews")[1]
        assert r["body"]  # built from pros + cons when no itemprop=reviewBody

    def test_empty_html_returns_empty_list(self):
        assert g2_parse("<html><body></body></html>", "Acme", "http://x.com") == []
