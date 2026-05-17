"""Score every blog article via /api/aeo/analyze + /api/fanout/generate.

Renders each article as a full HTML document (mirroring how the live page
serves it) and POSTs it to the running AEGIS server. Prints the AEO score,
band, failed checks, and fan-out coverage per article so we can iterate
content quality against the actual scorer.

Usage:
    .venv/bin/python tools/score_articles.py [--no-fanout]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.content.articles import list_articles  # noqa: E402

BASE_URL = "http://127.0.0.1:8000"


def render_article_html(article: dict) -> str:
    """Render an Article as the same HTML the blog page would serve.

    Keeps the JSON-LD blocks + datePublished / dateModified + FAQs so the
    AEO checks see the same structural signals readers do."""
    site_url = "https://aegis-autopilot.com"
    canonical = f"{site_url}/blog/{article['slug']}"

    article_ld = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": article["title"],
        "description": article["description"],
        "datePublished": article["published"],
        "dateModified": article.get("updated") or article["published"],
        "author": {"@type": "Organization", "name": article.get("author", "AEGIS Team")},
        "publisher": {"@type": "Organization", "name": "AEGIS Autopilot", "url": site_url},
        "mainEntityOfPage": canonical,
        "keywords": ", ".join(article.get("keywords", [])),
    }
    faq_ld = None
    if article.get("faqs"):
        faq_ld = {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": f["q"],
                    "acceptedAnswer": {"@type": "Answer", "text": f["a"]},
                }
                for f in article["faqs"]
            ],
        }

    faq_section = ""
    if article.get("faqs"):
        faq_items = "\n".join(
            f"<h3>{f['q']}</h3>\n<p>{f['a']}</p>" for f in article["faqs"]
        )
        faq_section = f"<section><h2>Frequently asked</h2>\n{faq_items}\n</section>"

    head_blocks = [
        f"<title>{article['title']}</title>",
        f'<meta name="description" content="{article["description"]}">',
        f'<link rel="canonical" href="{canonical}">',
        f'<script type="application/ld+json">{json.dumps(article_ld)}</script>',
    ]
    if faq_ld:
        head_blocks.append(
            f'<script type="application/ld+json">{json.dumps(faq_ld)}</script>'
        )

    head_html = "\n".join(head_blocks)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
{head_html}
</head>
<body>
<article>
  <header>
    <h1>{article['title']}</h1>
    <p><time datetime="{article['published']}">Published {article['published']}</time>
       — <time datetime="{article.get('updated') or article['published']}">Updated {article.get('updated') or article['published']}</time>
       · By {article.get('author', 'AEGIS Team')} · {article.get('reading_minutes', 8)} min read</p>
  </header>
  {article['body_html']}
  {faq_section}
</article>
</body>
</html>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fanout", action="store_true", help="Skip the LLM fan-out call (faster, free).")
    ap.add_argument("--slug", help="Score a single article by slug.")
    args = ap.parse_args()

    articles = list_articles()
    if args.slug:
        articles = [a for a in articles if a["slug"] == args.slug]

    summary: list[dict] = []

    with httpx.Client(timeout=120) as client:
        try:
            client.get(f"{BASE_URL}/api/health").raise_for_status()
        except Exception as e:
            print(f"ERROR: server not reachable at {BASE_URL} ({e})")
            return 1

        for a in articles:
            print(f"\n{'=' * 68}")
            print(f"📝 {a['slug']}")
            print(f"   {a['title']}")
            print('=' * 68)
            html = render_article_html(a)

            r = client.post(
                f"{BASE_URL}/api/aeo/analyze",
                json={"input_type": "text", "input_value": html},
            )
            if r.status_code != 200:
                print(f"   ⚠ AEO {r.status_code}: {r.text[:200]}")
                continue
            data = r.json()
            score = data["aeo_score"]
            band = data["band"]
            failed = [c for c in data["checks"] if not c["passed"]]
            passed = [c for c in data["checks"] if c["passed"]]

            print(f"   AEO Score: {score}/100   Band: {band}")
            print(f"   Passed: {len(passed)}/{len(data['checks'])}")
            for c in failed:
                print(f"   ✗ {c['name']} — {c['score']}/{c['max_score']}")
                if c.get("recommendation"):
                    rec = c["recommendation"]
                    print(f"     → {rec[:140]}")

            row = {
                "slug": a["slug"],
                "score": score,
                "band": band,
                "failed": [c["check_id"] for c in failed],
            }

            if not args.no_fanout and a.get("keywords"):
                target = a["keywords"][0]
                fr = client.post(
                    f"{BASE_URL}/api/fanout/generate",
                    json={
                        "target_query": target,
                        "existing_content": html,
                    },
                )
                if fr.status_code == 200:
                    fd = fr.json()
                    gap = fd.get("gap_summary") or {}
                    cov = gap.get("coverage_percent", 0)
                    missing = gap.get("missing_types", [])
                    print(f"   Fan-out (target: \"{target}\"): {cov}% covered ({gap.get('covered', 0)}/{gap.get('total', 0)})")
                    if missing:
                        print(f"     Missing intent types: {', '.join(missing)}")
                    row["fanout_coverage"] = cov
                    row["fanout_missing"] = missing
                else:
                    print(f"   ⚠ Fan-out {fr.status_code}: {fr.text[:200]}")
                time.sleep(0.5)  # gentle on the LLM

            summary.append(row)

    print("\n" + "=" * 68)
    print("SUMMARY")
    print("=" * 68)
    print(f"{'slug':<42} {'score':>6} {'fanout':>7}")
    for r in summary:
        cov = r.get("fanout_coverage", "—")
        cov_s = f"{cov}%" if isinstance(cov, int) else str(cov)
        print(f"{r['slug']:<42} {r['score']:>6} {cov_s:>7}")

    avg = sum(r["score"] for r in summary) / len(summary) if summary else 0
    print(f"\nAverage AEO score: {avg:.1f}/100")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
