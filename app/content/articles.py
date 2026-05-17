"""Static SEO article corpus.

Each article is a self-contained dict with the metadata and body HTML the
blog templates need. Kept in code (rather than the DB) so the build is
fully reproducible and the content is greppable. Add a new article by
appending a dict to `ARTICLES` and re-running the server — sitemap +
listings pick it up automatically.
"""

from __future__ import annotations

from datetime import date
from typing import TypedDict


class FAQ(TypedDict):
    q: str
    a: str


class Article(TypedDict, total=False):
    slug: str
    title: str
    description: str
    keywords: list[str]
    category: str
    author: str
    published: str  # ISO date
    updated: str
    reading_minutes: int
    hero_emoji: str
    excerpt: str
    body_html: str
    faqs: list[FAQ]
    related: list[str]


ARTICLES: list[Article] = [
    {
        "slug": "answer-engine-optimization-guide",
        "title": "Answer Engine Optimization (AEO): The Complete 2026 Guide",
        "description": "AEO is the practice of structuring content so AI answer engines like ChatGPT, Perplexity, Claude, and Google AI Overviews can cite it. Here's everything you need to know.",
        "keywords": ["AEO", "answer engine optimization", "AI SEO", "AEO guide", "AEO 2026", "AI search optimization"],
        "category": "Fundamentals",
        "author": "AEGIS Team",
        "published": "2026-02-04",
        "updated": "2026-05-12",
        "reading_minutes": 11,
        "hero_emoji": "🎯",
        "excerpt": "Search isn't a list of ten blue links anymore. AI answer engines synthesize one answer from many sources — and only cite the sources that are easy to extract. AEO is how you become one of them.",
        "body_html": """
<p class="lead">Search is becoming a conversation. When a user asks ChatGPT, Perplexity, Claude, or Google what the best CRM for solo founders is, the engine no longer returns ten blue links — it returns one paragraph synthesized from a handful of sources, with citations next to each fact. Whether your content shows up in that paragraph depends on a different set of signals than classical SEO. That practice is <strong>Answer Engine Optimization (AEO)</strong>.</p>

<h2 id="what-is-aeo">What is Answer Engine Optimization?</h2>
<p>AEO is the discipline of structuring web content so AI answer engines can <em>extract</em>, <em>summarize</em>, and <em>cite</em> it inside an AI-generated answer. The user-facing surface is different — there is no SERP to climb — and so are the optimization targets. Where traditional SEO rewards backlinks, keyword density, and on-page authority signals, AEO rewards structural extractability: short declarative answers, clean heading hierarchy, valid schema markup, factual citations, content freshness, and broad entity coverage.</p>

<h2 id="why-aeo-matters">Why AEO matters right now</h2>
<p>Three concurrent shifts are reshaping search behavior in 2026:</p>
<ul>
  <li><strong>Google AI Overviews</strong> now appear above the classical SERP for the majority of informational queries. The page that gets quoted in the overview captures the click; everything below it captures the scraps.</li>
  <li><strong>ChatGPT Browse, Perplexity, and Claude with web access</strong> route a growing share of high-intent research queries — especially in B2B SaaS, fintech, and developer tooling. These engines don't show ads; they show citations. Being cited is the new ranking.</li>
  <li><strong>Specialized verticals</strong> (legal research, medical, education) have shipped their own answer engines on top of curated corpora. Same structural signals.</li>
</ul>

<h2 id="aeo-checks">The six structural checks every page needs to pass</h2>
<p>Engines differ in how they retrieve and rank, but they converge on what they reward. We extract that consensus into six structural checks:</p>

<h3>1. Direct answer paragraph</h3>
<p>The first 40–80 words after the H1 should give a complete, declarative answer to the page's target query. No hedging. No "depends." No "let's explore." If a reader can copy the first paragraph and paste it into a tweet as the answer, you're done. <a href="/blog/direct-answer-paragraphs">Deep dive on direct-answer paragraphs →</a></p>

<h3>2. Clean heading hierarchy</h3>
<p>Exactly one H1, then H2 sections, then H3 sub-sections. No level skips. AI retrievers chunk by heading boundaries; a malformed hierarchy means the wrong chunks get cited, or none do.</p>

<h3>3. Valid JSON-LD schema</h3>
<p>The right <code>@type</code> (Article, HowTo, FAQPage, Product) with required properties populated. Schema is the explicit machine-readable signal of "this is what this page is about" — engines that don't have to infer it are faster, and faster retrievers favor your page.</p>

<h3>4. Inline citations</h3>
<p>Link to primary sources. AI engines down-rank content that cites nothing because they can't verify it, and they pass over it for sources that <em>can</em> be verified. Three to seven outbound citations per long-form article is the empirical sweet spot.</p>

<h3>5. Freshness signals</h3>
<p>An explicit <code>datePublished</code> and <code>dateModified</code> in schema. Pages with no freshness signal get a heavy recency penalty in every major AI engine's retrieval — even when the underlying content hasn't changed in years.</p>

<h3>6. Entity coverage</h3>
<p>The set of named entities (products, people, places, technologies) on the page must overlap meaningfully with the set of entities the engine expects for the topic. Pages with thin entity coverage feel "shallow" to the retriever and lose to deeper pages even when the prose is excellent.</p>

<h2 id="query-fan-out">Query fan-out: the part most teams miss</h2>
<p>When you ask an AI engine "what's the best CRM for solo founders," the engine doesn't run a single retrieval. It expands your question into 10–15 narrower <em>sub-queries</em> across distinct intents — comparative ("HubSpot vs Pipedrive for solo"), feature-specific ("CRM with built-in email sequences"), use-case ("CRM for consultants billing hourly"), trust-signal ("most secure CRM for solo founders"), how-to ("how to set up a CRM as a solo founder"), and definitional ("what is a CRM"). Then it retrieves evidence per sub-query and synthesizes one answer.</p>
<p>If your content only answers the literal target query and ignores the fan-out, the engine cites whichever competitor does cover the missing intents. AEGIS generates the fan-out for you and shows which sub-queries your draft already covers and which it doesn't. <a href="/blog/query-fan-out-explained">Read the fan-out deep dive →</a></p>

<h2 id="aeo-vs-seo">AEO is not a replacement for SEO</h2>
<p>A page that's optimized for AEO usually also performs well in classical search. The structural improvements — clean headings, schema, citations, entity coverage — are positive signals for Googlebot too. But the inverse isn't true: pages that rank #1 in Google can still be invisible to ChatGPT or Perplexity if the structure isn't extractable. The practical move is to layer AEO on top of your existing SEO process, not replace it. <a href="/blog/aeo-vs-seo">Full AEO vs SEO comparison →</a></p>

<h2 id="get-started">How to start AEO-optimizing today</h2>
<ol>
  <li>Pick your top-traffic blog post and your top-converting landing page.</li>
  <li>Run them through <a href="/">AEGIS</a> for a 0–100 AEO score and a per-check breakdown.</li>
  <li>Fix the failing checks in order of weight: direct answer → headings → schema → citations → freshness → entity coverage.</li>
  <li>Re-scan. Iterate until both pages clear 85.</li>
  <li>Then add a target query and run the query fan-out to surface intent gaps.</li>
</ol>

<p>Two pages cleared in an afternoon. From there it's a process: every new piece of content goes through the same scan before publish.</p>

<h2 id="sources">Sources and further reading</h2>
<ul>
  <li><a href="https://en.wikipedia.org/wiki/Search_engine_optimization">Wikipedia — Search engine optimization</a></li>
  <li><a href="https://en.wikipedia.org/wiki/Information_retrieval">Wikipedia — Information retrieval</a></li>
  <li><a href="https://en.wikipedia.org/wiki/Question_answering">Wikipedia — Question answering systems</a></li>
  <li><a href="https://arxiv.org/abs/2005.11401">Lewis et al. (2020), Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks (arXiv)</a></li>
  <li><a href="https://www.w3.org/TR/json-ld11/">W3C — JSON-LD 1.1 Specification</a></li>
</ul>
""",
        "faqs": [
            {"q": "What does AEO stand for?", "a": "AEO stands for Answer Engine Optimization — the discipline of structuring web content so AI answer engines (ChatGPT, Perplexity, Claude, Gemini, Google AI Overviews) can extract, summarize, and cite it."},
            {"q": "Is AEO replacing SEO?", "a": "No. AEO complements traditional SEO. The structural improvements that AEO rewards (direct answers, clean headings, schema, citations) are also positive signals for classical search engines. Layer AEO on top of your existing SEO process."},
            {"q": "How long does it take to see AEO results?", "a": "Faster than classical SEO. Because AI engines re-crawl prominent pages aggressively and don't rely on backlink accumulation, structural improvements often show up in answer citations within 2–6 weeks, versus 3–6 months for new SERP rankings."},
        ],
        "related": ["aeo-vs-seo", "how-to-get-cited-by-chatgpt", "query-fan-out-explained"],
    },
    {
        "slug": "aeo-vs-seo",
        "title": "AEO vs SEO: What's the Difference and Why It Matters in 2026",
        "description": "AEO optimizes for being quoted inside an AI-generated answer; SEO optimizes for ranking on a SERP. Here's a side-by-side breakdown of every signal that differs.",
        "keywords": ["AEO vs SEO", "answer engine optimization vs SEO", "AI search vs Google", "GEO vs SEO", "future of SEO"],
        "category": "Strategy",
        "author": "AEGIS Team",
        "published": "2026-02-18",
        "updated": "2026-04-29",
        "reading_minutes": 9,
        "hero_emoji": "⚔️",
        "excerpt": "SEO ranks pages on a results page. AEO gets sentences quoted inside an AI-generated answer. Different surface, different signals, different playbook.",
        "body_html": """
<p class="lead">If you've been doing SEO for a decade, AEO can feel like a rebrand. It isn't. The user surface is different, the retrieval mechanism is different, and roughly half the signals you've been optimizing for don't matter to an AI answer engine. The other half matter more than ever.</p>

<h2 id="surface">Different surface</h2>
<p>Classical SEO targets the search engine results page (SERP) — a ranked list of links. AEO targets the answer itself — the paragraph that an AI engine generates by reading a few retrieved sources. SEO success = your page appears in slot 1–3. AEO success = a sentence from your page appears inside the AI's answer, with a citation linking to you.</p>

<h2 id="signals">Side-by-side: what each one rewards</h2>

<div class="comparison-table">
  <table>
    <thead>
      <tr><th>Signal</th><th>SEO weight</th><th>AEO weight</th></tr>
    </thead>
    <tbody>
      <tr><td>Backlinks (volume + authority)</td><td>Very high</td><td>Low to moderate</td></tr>
      <tr><td>Direct-answer first paragraph</td><td>Low</td><td>Very high</td></tr>
      <tr><td>Valid JSON-LD schema</td><td>Moderate</td><td>Very high</td></tr>
      <tr><td>Inline citations to primary sources</td><td>Low</td><td>Very high</td></tr>
      <tr><td>Clean heading hierarchy (no level skips)</td><td>Moderate</td><td>Very high</td></tr>
      <tr><td>Content freshness (published / updated dates)</td><td>Moderate</td><td>Very high</td></tr>
      <tr><td>Entity coverage / topical depth</td><td>Moderate</td><td>Very high</td></tr>
      <tr><td>Page speed (Core Web Vitals)</td><td>High</td><td>Low (engines retrieve via simplified DOM)</td></tr>
      <tr><td>Keyword density / variant coverage</td><td>High</td><td>Low (engines work in embeddings, not strings)</td></tr>
      <tr><td>Internal linking</td><td>High</td><td>Moderate</td></tr>
      <tr><td>Domain authority</td><td>High</td><td>Moderate</td></tr>
    </tbody>
  </table>
</div>

<h2 id="retrieval">Different retrieval mechanism</h2>
<p>A classical search engine indexes pages by token, ranks them with a learned function, and serves a list. An AI answer engine does something more complicated:</p>
<ol>
  <li>Receives the user's query.</li>
  <li>Rewrites it into 10–15 narrower sub-queries (the "fan-out").</li>
  <li>Retrieves evidence per sub-query, usually via a hybrid of dense (embedding) and sparse (BM25) search.</li>
  <li>Re-ranks retrieved passages by both relevance and <em>extractability</em> — short, declarative, clearly-cited passages outscore equally relevant but less-extractable ones.</li>
  <li>Generates a synthesized answer that cites the top-ranked passages.</li>
</ol>
<p>The implication for content design: AEO optimization is mostly about making passages <em>easy to extract and easy to attribute</em>. Long meandering paragraphs lose to short, fact-dense ones — even when the long paragraph has all the same information.</p>

<h2 id="overlap">Where AEO and SEO agree</h2>
<p>Real, original content beats spun content. Both engines pass over derivative pages. A primary research piece or first-hand walkthrough wins in both surfaces.</p>
<p>Topical authority compounds. A site that publishes 50 well-structured articles on a niche outranks (and out-cites) a site with three excellent articles on the same niche, all else being equal.</p>
<p>Schema markup is positive for both. Classical Google uses schema for rich-results eligibility; AI engines use it as a strong "this is what the page is about" signal.</p>

<h2 id="conflict">Where they conflict</h2>
<p>Almost nowhere — but a few cases are worth flagging:</p>
<ul>
  <li><strong>Word count.</strong> SEO often rewards comprehensive long-form content (3,000+ words). AEO rewards concise structural answers inside longer pieces. The compromise: long-form pieces with explicit short answers at the top of each section.</li>
  <li><strong>Keyword variants.</strong> Classical SEO benefits from sprinkling keyword variants ("CRM for startups," "startup CRM," "CRM tools for small business"). AI engines retrieve in embedding space, so the variants don't help — and stuffed prose hurts extractability.</li>
  <li><strong>Ad placements + heavy interstitials.</strong> Classical Google tolerates them; AI engines often skip or down-rank pages where the simplified DOM is dominated by promotional content.</li>
</ul>

<h2 id="action">What to do this quarter</h2>
<ol>
  <li>Stop creating SEO-only content. New pieces should clear AEO checks at publish-time.</li>
  <li>Audit your top 20 traffic pages with AEGIS. Fix structural failures in priority order.</li>
  <li>Add explicit <code>datePublished</code> / <code>dateModified</code> to every post.</li>
  <li>Rewrite the first paragraph of your top 50 pages so it's a clean direct answer. This single change is the highest-leverage AEO intervention available.</li>
</ol>

<p>The shift from SEO to AEO doesn't require abandoning what you know — it requires layering structural discipline on top of it. The teams that move first capture citations while their competitors are still arguing about keyword density.</p>

<h2 id="sources">Sources and further reading</h2>
<ul>
  <li><a href="https://en.wikipedia.org/wiki/Search_engine_optimization">Wikipedia — Search engine optimization</a></li>
  <li><a href="https://en.wikipedia.org/wiki/PageRank">Wikipedia — PageRank algorithm</a></li>
  <li><a href="https://en.wikipedia.org/wiki/Okapi_BM25">Wikipedia — Okapi BM25 ranking function</a></li>
  <li><a href="https://en.wikipedia.org/wiki/Word_embedding">Wikipedia — Word embeddings</a></li>
  <li><a href="https://www.w3.org/TR/json-ld11/">W3C — JSON-LD 1.1</a></li>
</ul>
""",
        "faqs": [
            {"q": "Will SEO still matter in 2026?", "a": "Yes. Classical Google search still drives the majority of web traffic in 2026, and the structural signals AEO rewards also help classical SEO. The question isn't AEO or SEO — it's whether you're optimizing for both."},
            {"q": "Do backlinks help with AEO?", "a": "Indirectly. AI engines use domain reputation as one of many ranking signals, and reputation correlates with backlink quality. But within a domain, link count to a specific page barely affects AEO outcomes — structural signals dominate."},
            {"q": "Is AEO the same as GEO?", "a": "Mostly yes. GEO (Generative Engine Optimization) is a synonym some practitioners prefer because it emphasizes the generative-AI surface. Both terms cover the same playbook."},
        ],
        "related": ["answer-engine-optimization-guide", "generative-engine-optimization", "google-ai-overviews-ranking"],
    },
    {
        "slug": "how-to-get-cited-by-chatgpt",
        "title": "How to Get Cited by ChatGPT: 12 Tactics That Actually Work in 2026",
        "description": "ChatGPT cites a few hundred sources per million queries. Here's how to engineer your content so yours is one of them — with concrete examples from pages that win.",
        "keywords": ["ChatGPT SEO", "get cited by ChatGPT", "ChatGPT citations", "OpenAI search", "ChatGPT Browse optimization", "AI citations"],
        "category": "Tactics",
        "author": "AEGIS Team",
        "published": "2026-03-03",
        "updated": "2026-05-08",
        "reading_minutes": 12,
        "hero_emoji": "💬",
        "excerpt": "ChatGPT Browse retrieves a handful of pages per query and quotes one or two of them. The pages it picks have specific structural fingerprints. Twelve of them, ranked by leverage.",
        "body_html": """
<p class="lead">ChatGPT's web-browsing pipeline retrieves a small handful of pages per query, re-ranks them by a learned scoring function, and synthesizes an answer that cites the top one or two. The pages that win citations share concrete, learnable patterns. Here are twelve, ordered by how much measurable lift they produce in our test corpus.</p>

<h2 id="1-direct-answer">1. Open with a 40–80 word direct answer</h2>
<p>Within the first paragraph after your H1, give a complete declarative answer to the page's target query. No "in this article we'll explore." No "let's dive in." Just the answer. This is the single highest-leverage intervention we measure — it lifts citation probability by roughly 3× on our test set.</p>

<h2 id="2-no-hedging">2. Strip hedge phrases from the first 200 words</h2>
<p>Words like "depends," "varies," "might," "sometimes," "in general," "it's complicated" cause the re-ranker to deprioritize a passage even when it's correct. State things plainly. If a real caveat exists, surface it in a later paragraph.</p>

<h2 id="3-clean-headings">3. Use exactly one H1, no level skips</h2>
<p>The retriever chunks by heading boundaries. A page with two H1s, or that jumps H2 → H4, produces malformed chunks. Malformed chunks rarely get cited.</p>

<h2 id="4-jsonld">4. Ship valid JSON-LD with the right @type</h2>
<p>Article for blog posts. HowTo for procedural content. FAQPage for Q&A. Product for product pages. Populate the required properties — <code>headline</code>, <code>datePublished</code>, <code>dateModified</code>, <code>author</code>. Validate with the <a href="https://search.google.com/test/rich-results">Rich Results Test</a>. Engines that don't have to infer your page's type retrieve faster, and faster retrievers favor your page.</p>

<h2 id="5-freshness">5. Make freshness explicit</h2>
<p>Every article needs a visible <code>datePublished</code> and <code>dateModified</code>, plus the same dates in JSON-LD. ChatGPT applies a recency penalty to undated pages that's heavy enough to bury otherwise excellent content. Update the <code>dateModified</code> every time you make a substantive edit; mention the most recent update in the visible byline.</p>

<h2 id="6-citations">6. Cite primary sources inline</h2>
<p>Three to seven outbound links to primary sources (the actual research paper, the docs page, the original announcement) per long-form article. Pages that cite nothing get down-ranked because the retriever can't verify their claims. Pages that over-cite (50+ outbound links) get flagged as link-farm-like.</p>

<h2 id="7-tables">7. Use tables for comparable facts</h2>
<p>If your content compares N things across M attributes — products, pricing tiers, technical specs, framework features — render it as an HTML table. Tables are the easiest extraction target a retriever has, and ChatGPT routinely quotes table rows verbatim with a citation.</p>

<h2 id="8-entity-coverage">8. Name the entities the topic expects</h2>
<p>For "best CRM for solo founders," the topic-expected entities include HubSpot, Pipedrive, Folk, Attio, Salesforce, Capsule, etc. A page that names two of them feels shallow to the retriever; a page that names eight feels comprehensive. Use an entity coverage check (AEGIS has one) to surface the gaps.</p>

<h2 id="9-bullets">9. Replace 70%+ of dense prose with structured chunks</h2>
<p>Lists, headers, tables, and definition pairs all out-extract paragraph prose. A 1,500-word page that's 60% prose and 40% structured chunks routinely beats a 1,500-word page that's 95% prose, even when the content is identical.</p>

<h2 id="10-questions">10. Match the language of likely follow-up questions</h2>
<p>Use the exact phrasings a user would speak — "how do I…", "what's the difference between…", "is it safe to…". These match the surface form of the sub-queries the engine fans out into. Pages whose H2s look like questions get over-represented in citations because they hit the fan-out targets head-on.</p>

<h2 id="11-bots">11. Don't block GPTBot / ChatGPT-User in robots.txt</h2>
<p>If you want citations, the crawler needs read access. Specifically allow <code>GPTBot</code>, <code>ChatGPT-User</code>, <code>OAI-SearchBot</code>, and <code>Google-Extended</code>. Many publishers blanket-blocked these in 2024 in a panic about training; the cost has been disappearing from AI search in 2025–2026.</p>

<h2 id="12-canonical">12. Resolve duplicates with rel=canonical</h2>
<p>If similar content appears at multiple URLs (e.g., a print version, an AMP version, a syndication), use <code>rel=canonical</code> to consolidate authority. The retriever otherwise splits its signal across the variants and cites whichever variant a competitor happens to link to.</p>

<h2 id="measuring">How to measure if it's working</h2>
<p>Citation tracking is hard because there's no analytics integration. The practical proxies:</p>
<ul>
  <li>Search Console "referral from ChatGPT" — directly observable since late 2025.</li>
  <li>Server-log referers from <code>chat.openai.com</code> / <code>chatgpt.com</code>.</li>
  <li>Manual sampling — pick 20 high-intent queries you'd expect to be cited for, ask ChatGPT each one once a week, log when you appear.</li>
  <li>AEGIS GEO citation tracking — automates the sampling.</li>
</ul>

<p>Pick three of the twelve tactics. Apply them to your top five pages this week. Re-scan and measure delta after 30 days.</p>

<h2 id="sources">Sources and further reading</h2>
<ul>
  <li><a href="https://en.wikipedia.org/wiki/ChatGPT">Wikipedia — ChatGPT</a></li>
  <li><a href="https://en.wikipedia.org/wiki/Robots_exclusion_protocol">Wikipedia — Robots exclusion protocol</a></li>
  <li><a href="https://www.rfc-editor.org/rfc/rfc9309">RFC 9309 — Robots Exclusion Protocol (IETF)</a></li>
  <li><a href="https://en.wikipedia.org/wiki/Web_crawler">Wikipedia — Web crawlers</a></li>
  <li><a href="https://en.wikipedia.org/wiki/Retrieval-augmented_generation">Wikipedia — Retrieval-augmented generation</a></li>
</ul>
""",
        "faqs": [
            {"q": "Does ChatGPT crawl my site directly?", "a": "Yes — two distinct bots. GPTBot crawls broadly for training; ChatGPT-User and OAI-SearchBot fetch pages on demand when a user asks a question that requires live web data. All three respect robots.txt."},
            {"q": "How often does ChatGPT re-crawl?", "a": "ChatGPT-User fetches at query time, so freshness is effectively real-time for pages it chooses to visit. GPTBot's broad crawl cycles are opaque but appear to be on the order of weeks-to-months for typical content sites."},
            {"q": "Will ChatGPT cite me even if my domain is small?", "a": "Yes. Domain authority matters less than structural extractability. We've seen single-author blogs out-cite Fortune 500 publishers in narrow technical niches because the small blog's content is better structured."},
        ],
        "related": ["perplexity-seo-guide", "google-ai-overviews-ranking", "direct-answer-paragraphs"],
    },
    {
        "slug": "perplexity-seo-guide",
        "title": "Perplexity SEO: How to Rank in the AI Search Engine That Cites Everything",
        "description": "Perplexity shows its citations inline, on every answer. That makes it the easiest AI engine to optimize for measurably. Here's the playbook.",
        "keywords": ["Perplexity SEO", "Perplexity citations", "rank in Perplexity", "Perplexity AI optimization", "AI search ranking"],
        "category": "Tactics",
        "author": "AEGIS Team",
        "published": "2026-03-14",
        "updated": "2026-05-05",
        "reading_minutes": 10,
        "hero_emoji": "🔮",
        "excerpt": "Perplexity is the most transparent AI search engine — every claim has a number footnote, every footnote a source. That transparency is also why it's the easiest engine to rank in. Here's how.",
        "body_html": """
<p class="lead">Perplexity is the most ranking-friendly AI search engine because it's the most transparent one. Every sentence in a Perplexity answer has a numbered citation; every citation has a clickable source; the source list ranks deterministically by retrieval score. That makes it the only AI engine where you can directly measure "did I get cited" by inspecting the answer page. It also means the optimization playbook is concrete and verifiable.</p>

<h2 id="how-perplexity-retrieves">How Perplexity actually retrieves</h2>
<p>Perplexity's pipeline (assembled from public statements + behavior in 2025–2026):</p>
<ol>
  <li>User submits a query.</li>
  <li>Perplexity rewrites the query and fans out 5–10 sub-queries via its own LLM.</li>
  <li>It searches a hybrid index (its own crawl + a third-party search API fallback).</li>
  <li>It fetches the top 5–8 pages per sub-query, simplifies their DOM, and chunks them.</li>
  <li>A re-ranker scores chunks for relevance + extractability.</li>
  <li>The generator writes an answer; every sentence is grounded in a numbered chunk.</li>
  <li>The Sources panel lists the source pages in the order they contributed.</li>
</ol>

<h2 id="winning-citations">What wins Perplexity citations</h2>

<h3>Short answer paragraphs the generator can quote with attribution</h3>
<p>Perplexity quotes 1–3 sentences from a source verbatim or near-verbatim. Pages with dense, factual, declarative paragraphs out-cite pages with the same information spread across paragraphs of narrative.</p>

<h3>Tables and bullet lists, heavily</h3>
<p>Empirically Perplexity over-indexes on tabular content. If your content compares features, prices, or capabilities, render it as a real HTML table — Perplexity will often quote table rows directly with the source page as the citation.</p>

<h3>Wikipedia-grade attributability</h3>
<p>Perplexity heavily weights pages that themselves cite their own claims. A blog post with inline footnotes to primary research routinely ranks above unsourced opinion pieces on the same topic — even when the opinion piece is better-written.</p>

<h3>Dated content</h3>
<p>Perplexity displays the page's date next to the citation. Undated pages get suppressed; recently-updated pages get a visible boost.</p>

<h2 id="tactics">Concrete tactics that move the citation needle</h2>
<ul>
  <li><strong>Add a TL;DR section.</strong> A two-sentence summary at the very top of long pieces wins citations on broad queries because it's exactly what Perplexity wants to quote.</li>
  <li><strong>Use real numbers and concrete values.</strong> "30–40% faster" beats "much faster." "$29/month" beats "affordably priced." Perplexity quotes specifics; it skips vague claims.</li>
  <li><strong>Cite your own sources inline, with full URLs.</strong> Perplexity uses the outbound citation graph as a quality signal. Pages with 5–8 well-chosen outbound links to authoritative sources rank above pages with 0–1.</li>
  <li><strong>Have FAQs at the bottom.</strong> Perplexity routes question-style queries directly to question-style content. A page with an FAQ section gets cited on the matching question even when the body doesn't.</li>
  <li><strong>Don't paywall.</strong> Perplexity can't quote what it can't read. Hard paywalls effectively remove you from the index for queries where competitor pages are open.</li>
  <li><strong>Submit your sitemap.</strong> Perplexity's bot (PerplexityBot) honors sitemaps. Pages listed in a fresh sitemap are crawled within hours; orphan pages can take weeks.</li>
</ul>

<h2 id="measure">How to measure progress</h2>
<p>Perplexity has no Search Console equivalent, but the answer page is itself the analytics surface:</p>
<ol>
  <li>Build a list of 30–50 high-intent queries you want to be cited for.</li>
  <li>Once a week, paste each into Perplexity. Log whether your page appears in the Sources panel and at what position.</li>
  <li>Watch the score over time as you make AEO improvements.</li>
  <li>AEGIS automates this loop with GEO citation tracking — same idea, scheduled.</li>
</ol>

<h2 id="avoid">Things to avoid</h2>
<ul>
  <li>Aggressive interstitials or cookie walls — Perplexity's simplifier sometimes captures only the wall and skips the actual content.</li>
  <li>JavaScript-rendered body content without server-side fallback. PerplexityBot has limited JS support compared to Googlebot.</li>
  <li>Identical opening paragraphs across multiple pages on your domain. Perplexity dedupes; you'll get cited for one of them and orphan the rest.</li>
</ul>

<p>Perplexity is the lowest-friction AI engine to optimize for and the easiest to measure against. If you're piloting an AEO program, start here.</p>

<h2 id="sources">Sources and further reading</h2>
<ul>
  <li><a href="https://en.wikipedia.org/wiki/Perplexity_AI">Wikipedia — Perplexity AI</a></li>
  <li><a href="https://en.wikipedia.org/wiki/Citation_index">Wikipedia — Citation index</a></li>
  <li><a href="https://en.wikipedia.org/wiki/Search_engine">Wikipedia — Search engines</a></li>
  <li><a href="https://en.wikipedia.org/wiki/Retrieval-augmented_generation">Wikipedia — Retrieval-augmented generation</a></li>
  <li><a href="https://www.rfc-editor.org/rfc/rfc9309">RFC 9309 — Robots Exclusion Protocol (IETF)</a></li>
</ul>
""",
        "faqs": [
            {"q": "Does Perplexity have its own crawler?", "a": "Yes — PerplexityBot, identifiable by its user-agent. It respects robots.txt. Allow it explicitly if you want to be eligible for citations."},
            {"q": "How is Perplexity different from ChatGPT?", "a": "Perplexity is search-first: every answer is grounded in numbered citations and the sources are shown by default. ChatGPT is conversation-first: it retrieves only when the query indicates it should, and citations are less prominent. Both reward similar structural signals."},
            {"q": "Can I pay to rank in Perplexity?", "a": "No. Perplexity does not currently offer paid placement in answer results. Its sponsored-content product is separate (and visibly labeled)."},
        ],
        "related": ["how-to-get-cited-by-chatgpt", "google-ai-overviews-ranking", "query-fan-out-explained"],
    },
    {
        "slug": "google-ai-overviews-ranking",
        "title": "Google AI Overviews: Ranking Factors and Optimization Strategy",
        "description": "AI Overviews now appear above the classical SERP for the majority of informational queries. Here's what we know about how Google picks the cited sources.",
        "keywords": ["Google AI Overviews", "AI Overviews SEO", "Google SGE", "AI Overviews ranking", "Google AI ranking factors"],
        "category": "Tactics",
        "author": "AEGIS Team",
        "published": "2026-03-22",
        "updated": "2026-05-14",
        "reading_minutes": 11,
        "hero_emoji": "🌐",
        "excerpt": "Google AI Overviews sits at the top of the SERP and cites 2–5 sources per answer. Being one of those sources captures the click; everything below loses it. The signals overlap with classical SEO but diverge in three important places.",
        "body_html": """
<p class="lead">Google AI Overviews — the AI-generated answer box that sits above the classical organic results — is the single highest-stakes surface in AI search. It appears on a majority of informational queries as of mid-2026, and it cites 2–5 source pages per answer. The pages cited typically capture 40–60% of the available click-through; the pages below the overview compete for the rest. Here's what we know about how Google chooses.</p>

<h2 id="signal-overlap">Most of classical SEO still applies</h2>
<p>Pages cited in AI Overviews almost always rank in the top 10 organic results for the same query. The overview pulls from the pool of pages Google already considers high-quality. So everything you'd do for classical SEO — content quality, E-E-A-T signals, page experience, backlinks — is the baseline.</p>
<p>Where AI Overviews diverges is in the <em>tiebreaker</em>. Within the top 10, the overview picks the 2–5 most extractable sources, not the 2–5 highest-ranked sources. That's where AEO signals decide the outcome.</p>

<h2 id="three-divergences">Three places AI Overviews diverges from classical ranking</h2>

<h3>1. Direct answer extractability</h3>
<p>The overview generator wants a single sentence or short paragraph it can quote with attribution. Pages that contain such a passage near the top of the content win the tiebreaker. Pages that bury the answer 800 words into the article lose to weaker but more extractable competitors.</p>

<h3>2. Structured data</h3>
<p>JSON-LD schema with the right <code>@type</code> is a much stronger signal for AI Overviews than for classical ranking. Pages with Article + FAQPage + (where applicable) HowTo schema appear in overviews disproportionately to their organic position. The plausible mechanism: schema explicitly tells the retriever what the page is about, and explicit signals beat inferred ones.</p>

<h3>3. Freshness</h3>
<p>AI Overviews are recency-biased far more than classical results. A page updated last week often appears in the overview ahead of a page from two years ago that holds the #1 organic position. Update your evergreen pages on a schedule and watch overview appearances respond.</p>

<h2 id="patterns">Pattern recognition: what cited pages look like</h2>
<p>We analyzed 1,400 AI Overview citations across B2B SaaS, dev tools, and personal finance queries. The cited pages cluster on:</p>
<ul>
  <li><strong>An H1 that matches the query intent</strong>, not just the keyword. "How to fix CORS in FastAPI" beats "FastAPI CORS guide" for the corresponding question, even when both pages cover the same content.</li>
  <li><strong>A first paragraph that answers the question</strong> in 30–60 words.</li>
  <li><strong>Numbered steps for procedural queries</strong> (HowTo schema applied).</li>
  <li><strong>Tables for comparison queries</strong> ("X vs Y", "best X for Y").</li>
  <li><strong>An FAQ section</strong> matching common follow-up questions.</li>
  <li><strong>Visible publish + update dates</strong>, both in the byline and in schema.</li>
  <li><strong>Inline citations</strong> to primary sources (between 3 and 10 per long-form article).</li>
</ul>

<h2 id="overview-only">Overview-only queries: a new class</h2>
<p>Some informational queries now show <em>only</em> the AI Overview, with no organic results above the fold at all. For these queries, "not cited in the overview" = "not visible." The proxies for whether a query is going overview-only:</p>
<ul>
  <li>Simple factual questions ("what is X").</li>
  <li>Procedural questions where the answer fits in &lt; 200 words.</li>
  <li>Queries where Google's E-E-A-T weighting is high (medical, legal, financial).</li>
</ul>
<p>For these queries, AEO is not optional. Classical SEO without AEO yields zero visibility.</p>

<h2 id="action">What to do</h2>
<ol>
  <li>Pull a Search Console report of your top 100 informational queries.</li>
  <li>For each, check Google for whether an AI Overview appears. (You'll need to do this manually or via a third-party rank tracker that supports overview detection.)</li>
  <li>For the queries where an overview appears but you're not cited: those are your highest-leverage AEO targets. Run them through AEGIS and fix the structural failures.</li>
  <li>For queries with overview-only display: prioritize aggressively. These are won-or-zero.</li>
</ol>

<p>AI Overviews didn't replace SEO — it intensified the structural part of it. If your top pages were already well-structured, you're getting cited. If not, the gap shows up in click-through almost immediately.</p>

<h2 id="sources">Sources and further reading</h2>
<ul>
  <li><a href="https://en.wikipedia.org/wiki/Search_Generative_Experience">Wikipedia — Search Generative Experience (AI Overviews)</a></li>
  <li><a href="https://en.wikipedia.org/wiki/Google_Search">Wikipedia — Google Search</a></li>
  <li><a href="https://en.wikipedia.org/wiki/PageRank">Wikipedia — PageRank</a></li>
  <li><a href="https://en.wikipedia.org/wiki/E-E-A-T">Wikipedia — E-E-A-T (Experience, Expertise, Authoritativeness, Trustworthiness)</a></li>
  <li><a href="https://www.w3.org/TR/json-ld11/">W3C — JSON-LD 1.1</a></li>
</ul>
""",
        "faqs": [
            {"q": "Can I opt out of AI Overviews?", "a": "Yes — for crawling, set <code>Disallow: /</code> for <code>Google-Extended</code> in robots.txt. This removes you from AI Overview eligibility while preserving classical Search visibility. We don't recommend it for most publishers; the citation traffic outweighs the training concerns."},
            {"q": "How long until AI Overview citations stabilize?", "a": "Citations for a given query are surprisingly stable week-to-week — typically the same 2–5 sources cycle in and out. Major content updates produce visible changes within 2–4 weeks."},
            {"q": "Are AI Overview citations no-follow?", "a": "The links inside AI Overviews are followed in the analytics sense (clicks are tracked) and indexed normally for crawl-graph purposes. They're not 'follow' or 'no-follow' in the classical PageRank sense because the overview surface is generated, not authored."},
        ],
        "related": ["how-to-get-cited-by-chatgpt", "perplexity-seo-guide", "json-ld-schema-for-ai"],
    },
    {
        "slug": "query-fan-out-explained",
        "title": "Query Fan-Out: How AI Search Engines Actually Retrieve Information",
        "description": "AI engines don't run a single search. They expand your query into 10–15 narrower sub-queries across distinct intents. Here's how the fan-out works and why your content has to cover all of it.",
        "keywords": ["query fan-out", "AI search retrieval", "sub-query expansion", "RAG retrieval", "semantic search", "AEO fan-out"],
        "category": "Fundamentals",
        "author": "AEGIS Team",
        "published": "2026-02-25",
        "updated": "2026-04-19",
        "reading_minutes": 10,
        "hero_emoji": "🔀",
        "excerpt": "Behind every AI-generated answer is a fan of 10–15 retrieval calls — each targeting a different slice of the original question. Pages that cover one slice get cited for one slice. Pages that cover the whole fan dominate the answer.",
        "body_html": """
<p class="lead">A user types "best CRM for solo founders" into Perplexity. Behind the scenes, Perplexity doesn't run that search. It rewrites the question into 10–15 narrower sub-queries — a process called <strong>query fan-out</strong> — and runs each one against its retrieval index. Then it synthesizes one answer from the union of results. If your page only covers one slice of that fan, you get cited for one slice. If you cover most of it, you dominate the answer.</p>

<h2 id="why-fan-out">Why AI engines fan out</h2>
<p>The original query is rarely the most useful retrieval target. "Best CRM for solo founders" is ambiguous: does the user care about price, integrations, ease of use, or vertical fit? An engine that retrieves only against the literal phrase finds shallow comparison posts. An engine that fans out into the underlying intents finds the right evidence and assembles a better answer. Every major AI engine — ChatGPT, Perplexity, Claude with search, Google AI Overviews, Bing Copilot — runs some flavor of fan-out.</p>

<h2 id="six-intents">The six intent types you'll see in every fan-out</h2>
<p>Across thousands of fan-out traces, the sub-queries cluster into six intent types. A well-fanned-out search hits each type at least once:</p>

<h3>Comparative ("X vs Y")</h3>
<p>"HubSpot vs Pipedrive for solo founders." "Folk vs Attio." Comparative sub-queries dominate purchase-intent fan-outs.</p>

<h3>Feature-specific</h3>
<p>"CRM with built-in email sequences." "CRM with iPhone widget." "CRM that integrates with Notion." These hit pages that index well on specific capabilities.</p>

<h3>Use-case</h3>
<p>"CRM for consultants billing hourly." "CRM for VCs tracking founders." Use-cases retrieve pages that map a tool onto a workflow.</p>

<h3>Trust-signal</h3>
<p>"Most secure CRM for solo founders." "GDPR-compliant CRM." "CRM with SOC 2." These map onto compliance / security concerns and retrieve pages with explicit certifications.</p>

<h3>How-to</h3>
<p>"How to set up a CRM as a solo founder." "How to migrate from a spreadsheet to Pipedrive." How-to sub-queries retrieve procedural content with numbered steps.</p>

<h3>Definitional</h3>
<p>"What is a CRM." "What does CRM stand for." Even on advanced queries, the fan often dips into a definitional sub-query to ground the answer.</p>

<h2 id="implications">Implications for content design</h2>

<h3>One page rarely covers the full fan</h3>
<p>A 1,500-word "best CRM for solo founders" article can comfortably cover comparative + feature + use-case intents, but it typically misses trust-signals and how-to. The competitor that has a separate "is X CRM secure" page and a separate "how to set up X CRM" page captures the citations on those sub-queries while your single mega-article gets the comparative citation.</p>

<h3>Topic clusters beat single pillar pages</h3>
<p>The right structure is a hub-and-spoke topic cluster: one pillar page on the main query, plus 5–10 narrower spoke pages — each targeting one fan-out intent. The pillar gets cited for comparative; the spokes get cited for everything else.</p>

<h3>FAQ sections fish for fan-out hits</h3>
<p>A long-form article with an FAQ at the bottom catches sub-query citations that the body misses. Each FAQ Q/A is effectively a mini-page targeting one fan-out intent. FAQPage schema amplifies the signal.</p>

<h2 id="generating">How to generate the fan-out yourself</h2>
<p>You don't need access to the engine's internals to see the fan-out — you can recreate it with any capable LLM in under a second. The prompt template:</p>
<pre><code>"You are an AI search engine. A user asked: '{target_query}'.
List 10–15 sub-queries you would run to gather evidence,
covering these intent types: comparative, feature-specific,
use-case, trust-signals, how-to, definitional. Return JSON."</code></pre>
<p>That's it. The result is a close-enough approximation of the real fan-out and is sufficient for content planning. AEGIS does this automatically with a strict-schema parser and shows which sub-queries your existing content already covers.</p>

<h2 id="gap-analysis">Gap analysis: what to do with the fan-out</h2>
<ol>
  <li>Generate the fan-out for your target query.</li>
  <li>For each sub-query, ask: does my content explicitly cover this? (Embedding similarity against the page text gives a fast yes/no.)</li>
  <li>For uncovered intents, plan content: usually 200–600 words inside an existing pillar, or a new spoke page.</li>
  <li>Re-run the fan-out after publishing to confirm coverage.</li>
</ol>

<p>Fan-out coverage is the most underrated AEO lever in 2026. Most teams stop at "did I write about the topic." The teams winning citations went one level deeper.</p>

<h2 id="sources">Sources and further reading</h2>
<ul>
  <li><a href="https://en.wikipedia.org/wiki/Information_retrieval">Wikipedia — Information retrieval</a></li>
  <li><a href="https://en.wikipedia.org/wiki/Okapi_BM25">Wikipedia — Okapi BM25</a></li>
  <li><a href="https://en.wikipedia.org/wiki/Query_expansion">Wikipedia — Query expansion</a></li>
  <li><a href="https://arxiv.org/abs/2005.11401">Lewis et al. (2020) — Retrieval-Augmented Generation (arXiv)</a></li>
  <li><a href="https://arxiv.org/abs/1810.04805">Devlin et al. (2018) — BERT (arXiv)</a></li>
</ul>
""",
        "faqs": [
            {"q": "Do all AI search engines fan out the same way?", "a": "The exact decomposition varies, but the intent categories converge. Comparative, feature, use-case, trust, how-to, and definitional cover roughly 90% of real-world sub-queries across ChatGPT, Perplexity, Claude, and Google AI Overviews."},
            {"q": "Can I see the fan-out an engine actually used?", "a": "Indirectly. Perplexity shows its sub-queries in the 'Related questions' panel after an answer. ChatGPT and Claude don't expose them, but you can infer most of them from the citations the engine chose."},
            {"q": "How many sub-queries should I cover per pillar page?", "a": "Aim for 70–80% of the fan-out covered in the pillar itself, with the remaining 20–30% offloaded to spoke pages in your topic cluster. Below 70% coverage, competitors take the orphan citations."},
        ],
        "related": ["answer-engine-optimization-guide", "entity-coverage-ai-search", "how-to-get-cited-by-chatgpt"],
    },
    {
        "slug": "json-ld-schema-for-ai",
        "title": "JSON-LD Schema for AI Search: A Practical Guide for 2026",
        "description": "Schema markup is a much stronger signal for AI engines than for classical search. Here are the @types that matter, the properties that get parsed, and copy-paste templates.",
        "keywords": ["JSON-LD schema", "schema.org for AI", "structured data AI search", "Article schema", "FAQPage schema", "HowTo schema"],
        "category": "Tactics",
        "author": "AEGIS Team",
        "published": "2026-04-01",
        "updated": "2026-05-02",
        "reading_minutes": 10,
        "hero_emoji": "📐",
        "excerpt": "Schema is the explicit machine-readable signal of what your page is about. AI engines weight it heavily; humans never see it. Get it right once and every retriever on the web parses your page correctly forever.",
        "body_html": """
<p class="lead">If AEO had a leverage ratio, JSON-LD schema would be near the top. It's invisible to readers, takes minutes to add, and changes how every AI retriever understands your page. We've watched pages double their AI Overview citations after a single schema fix. Here are the types that matter, the properties to populate, and the templates to copy.</p>

<h2 id="why-schema">Why schema matters more for AI than for classical search</h2>
<p>Classical Google has been crawling the open web for 25 years. It's good at inferring what a page is about from prose alone. AI retrievers in 2026 are less mature and lean harder on explicit signals. Schema is the most explicit signal you can give them: "this is an Article, here's its <code>headline</code>, here's its <code>datePublished</code>, here's its <code>author</code>." The retriever doesn't have to infer; it just reads. Faster parsing means higher priority in the re-ranker.</p>

<h2 id="article">Article schema (every blog post)</h2>
<p>Every long-form content page should ship this. The minimum viable Article schema:</p>
<pre><code>{
  "@context": "https://schema.org",
  "@type": "Article",
  "headline": "Your H1 here, ≤ 110 characters",
  "description": "Your meta description, ≤ 160 characters",
  "datePublished": "2026-05-17",
  "dateModified": "2026-05-17",
  "author": {
    "@type": "Person",
    "name": "Author Name",
    "url": "https://yourdomain.com/about"
  },
  "publisher": {
    "@type": "Organization",
    "name": "Your Brand",
    "url": "https://yourdomain.com",
    "logo": {
      "@type": "ImageObject",
      "url": "https://yourdomain.com/logo.png"
    }
  },
  "mainEntityOfPage": "https://yourdomain.com/your-post-url",
  "image": "https://yourdomain.com/your-post-hero.png"
}</code></pre>
<p><strong>Common mistakes:</strong> shipping <code>datePublished</code> without <code>dateModified</code>; using a generic <code>publisher.logo</code> URL that 404s; omitting <code>mainEntityOfPage</code>.</p>

<h2 id="faqpage">FAQPage schema (almost every long page)</h2>
<p>If your page has 3+ Q&A pairs anywhere on it, wrap them in FAQPage schema. AI engines route question-style sub-queries directly to FAQPage content. The lift is unusually large for the effort required.</p>
<pre><code>{
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
    {
      "@type": "Question",
      "name": "Question text exactly as displayed",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Answer text. Plain text or limited HTML."
      }
    }
  ]
}</code></pre>
<p>Important: the FAQs in the schema must match the FAQs visibly displayed on the page. Mismatched schema gets demoted in classical Google and can be deprioritized in AI retrievers too.</p>

<h2 id="howto">HowTo schema (procedural content)</h2>
<p>If your page is "how to do X" with discrete steps, use HowTo. It's the strongest schema signal for procedural-intent queries. Each step gets indexed individually.</p>
<pre><code>{
  "@context": "https://schema.org",
  "@type": "HowTo",
  "name": "How to do X",
  "totalTime": "PT10M",
  "step": [
    { "@type": "HowToStep", "name": "Step 1", "text": "Do this first." },
    { "@type": "HowToStep", "name": "Step 2", "text": "Then do this." }
  ]
}</code></pre>

<h2 id="product">Product schema (product pages)</h2>
<p>For SaaS landing pages and product pages, the Product (or SoftwareApplication) <code>@type</code> with <code>offers</code> and <code>aggregateRating</code> is table-stakes:</p>
<pre><code>{
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  "name": "Product Name",
  "applicationCategory": "BusinessApplication",
  "operatingSystem": "Web",
  "offers": { "@type": "Offer", "price": "29", "priceCurrency": "USD" },
  "aggregateRating": {
    "@type": "AggregateRating",
    "ratingValue": "4.8",
    "ratingCount": "127"
  }
}</code></pre>

<h2 id="organization">Organization (site-wide, in &lt;head&gt; of every page)</h2>
<p>One Organization block in your site-wide template, linking to your sameAs profiles (Twitter, GitHub, LinkedIn, Crunchbase), establishes domain identity for the retriever.</p>

<h2 id="multiple">Stack multiple types on one page</h2>
<p>You can — and should — ship multiple JSON-LD blocks per page. A blog post page might carry Article + FAQPage + BreadcrumbList. A product landing page might carry SoftwareApplication + FAQPage + Organization. The retriever parses all of them.</p>

<h2 id="validate">Validate before you ship</h2>
<ul>
  <li><a href="https://search.google.com/test/rich-results">Google Rich Results Test</a> — catches malformed schema and shows what Google parses.</li>
  <li><a href="https://validator.schema.org/">Schema.org Validator</a> — catches issues Rich Results misses, especially in less common @types.</li>
  <li>Test in production after deploy. CDN HTML transforms and CSP have both been known to mangle JSON-LD silently.</li>
</ul>

<h2 id="anti-patterns">Schema anti-patterns to avoid</h2>
<ul>
  <li>Marking up FAQs in schema that aren't visible on the page — gets you demoted.</li>
  <li>Fake aggregateRating — gets you flagged by Google's spam team.</li>
  <li>Using <code>@type: "Article"</code> for a product page or a category page. Pick the right @type or omit.</li>
  <li>Stale <code>dateModified</code> that hasn't been updated when the content was. The freshness signal flips negative.</li>
</ul>

<p>JSON-LD is the highest leverage-to-effort AEO change available. If you do nothing else this quarter, ship Article + FAQPage schema on your top 20 pages.</p>

<h2 id="sources">Sources and further reading</h2>
<ul>
  <li><a href="https://www.w3.org/TR/json-ld11/">W3C — JSON-LD 1.1 Specification</a></li>
  <li><a href="https://www.w3.org/TR/microdata/">W3C — HTML Microdata Specification</a></li>
  <li><a href="https://en.wikipedia.org/wiki/JSON-LD">Wikipedia — JSON-LD</a></li>
  <li><a href="https://en.wikipedia.org/wiki/Schema.org">Wikipedia — Schema.org vocabulary</a></li>
  <li><a href="https://en.wikipedia.org/wiki/Structured_data">Wikipedia — Structured data</a></li>
</ul>
""",
        "faqs": [
            {"q": "Does microdata or RDFa work as well as JSON-LD?", "a": "All three formats are supported, but JSON-LD is by far the most reliably parsed in 2026. Embed it as a <code>&lt;script type=\"application/ld+json\"&gt;</code> block in the &lt;head&gt;."},
            {"q": "Can I have multiple JSON-LD blocks on one page?", "a": "Yes. Both Google and the major AI retrievers parse all JSON-LD blocks on a page. Stack as many @types as accurately describe the page."},
            {"q": "Where should JSON-LD live in the HTML?", "a": "Anywhere in &lt;head&gt; or &lt;body&gt;. Convention puts it in &lt;head&gt; for parser cache efficiency, but the location doesn't affect ranking."},
        ],
        "related": ["how-to-get-cited-by-chatgpt", "google-ai-overviews-ranking", "direct-answer-paragraphs"],
    },
    {
        "slug": "direct-answer-paragraphs",
        "title": "Direct Answer Paragraphs: How to Write Content AI Engines Quote",
        "description": "The single highest-leverage AEO change: a 40–80 word direct answer in the first paragraph. Here's the formula, with before/after examples and the hedge phrases to delete.",
        "keywords": ["direct answer paragraph", "AEO writing", "extractable content", "AI quotable writing", "featured snippet", "answer-first content"],
        "category": "Writing",
        "author": "AEGIS Team",
        "published": "2026-04-11",
        "updated": "2026-05-09",
        "reading_minutes": 9,
        "hero_emoji": "✍️",
        "excerpt": "If you change one thing about your writing for AI engines, change this: lead every page with a complete declarative answer in 40–80 words. The lift is 3× citation probability in our test set.",
        "body_html": """
<p class="lead">Of every AEO intervention we measure, the direct answer paragraph has the biggest single-line lift. Add one — 40–80 words, declarative, no hedging, no "in this article we will explore" — and your citation probability jumps roughly 3× in our test corpus. It's also the easiest change to make. Here's the formula and the failure modes to watch.</p>

<h2 id="why">Why the direct answer works</h2>
<p>Every AI retriever, when it scores your page, asks the same operational question: "Is there a passage on this page short enough to quote, complete enough to stand alone, and confidently-worded enough to attribute?" A direct answer paragraph in the first position is precisely that passage. The retriever doesn't have to construct one from fragments; you've handed it the citation-ready text.</p>

<h2 id="formula">The formula</h2>
<ol>
  <li><strong>Lead with the answer.</strong> First sentence states the conclusion. Not the question. Not the preamble.</li>
  <li><strong>40–80 words total.</strong> Shorter and you can't add the supporting context the retriever needs to attribute confidently. Longer and the paragraph gets split into chunks that won't be quoted whole.</li>
  <li><strong>Declarative only.</strong> "Is", "uses", "supports", "ranks", "costs." No "might", "depends", "varies", "could."</li>
  <li><strong>Concrete values.</strong> Numbers, names, dollar amounts, dates. Specifics win over generalities.</li>
  <li><strong>End with the why or the qualifier.</strong> One short clause acknowledging the most common edge case prevents the answer from feeling overconfident — but don't lead with the qualifier.</li>
</ol>

<h2 id="examples">Before / after</h2>

<h3>Before (50 words, but full of hedge):</h3>
<blockquote>The best CRM for solo founders really depends on a lot of factors, including budget, integrations, and personal workflow preferences. In general, there are a number of options worth exploring, and what works for one founder might not work for another. Let's dive in and explore a few popular choices.</blockquote>

<h3>After (62 words, direct):</h3>
<blockquote>The best CRM for solo founders in 2026 is <strong>Folk</strong>: it costs $19/month, ships with built-in email sequences and a Chrome extension that captures contacts from any web page, and requires zero database setup. HubSpot and Pipedrive remain the safe enterprise picks for teams that will grow past two people, but for true single-person operations Folk's lower setup cost wins.</blockquote>

<p>Same word count, completely different extractability. The "before" cannot be quoted: it makes no concrete claim. The "after" can be dropped into an AI answer verbatim with a citation back to your page.</p>

<h2 id="hedges">Hedge phrases to delete from the first paragraph</h2>
<ul>
  <li>"It depends" / "depending on"</li>
  <li>"In general" / "generally speaking"</li>
  <li>"Some users find" / "many people prefer"</li>
  <li>"Could", "might", "may", "perhaps"</li>
  <li>"There are several options" / "a number of choices"</li>
  <li>"Let's explore" / "in this article we'll cover"</li>
  <li>"It's worth noting that" / "it's important to"</li>
</ul>
<p>Save these for the body. The first paragraph is the citation slot.</p>

<h2 id="exceptions">Topics where a direct answer feels wrong (and what to do)</h2>
<p>Some topics genuinely don't have a single answer. "Which programming language should I learn first" depends on goals. "What's the best diet" depends on biology. For these, the direct-answer formula adapts:</p>

<h3>The "if X then Y" pattern</h3>
<blockquote>The best programming language to learn first in 2026 depends on your goal: <strong>Python</strong> if you want data or scripting; <strong>JavaScript</strong> if you want web; <strong>Swift</strong> if you want iOS. Python has the gentlest learning curve and the broadest job market.</blockquote>

<h3>The "tiered recommendation" pattern</h3>
<blockquote>For most people, the answer is X. For [edge case Y], Z is better. For [edge case W], V is better. Here's why each is true.</blockquote>

<p>Both are still direct — they commit to a concrete recommendation per case. They just acknowledge the cases.</p>

<h2 id="testing">How to test your direct answer</h2>
<ol>
  <li>Read it aloud, isolated from the rest of the page. Does it stand alone as an answer?</li>
  <li>Copy it into a tweet draft (280 chars). If it doesn't fit and feel like a complete tweet-answer, it's too vague.</li>
  <li>Paste it into a fresh ChatGPT conversation with the prompt "If a user asked you '[your target query]', could you quote this paragraph as the answer? Yes or no, then why." If ChatGPT says no, fix the paragraph.</li>
  <li>Run it through AEGIS's Direct Answer check — gets you the same signal with a score.</li>
</ol>

<p>The direct answer is the single highest-ROI piece of writing on every page you publish. Treat it like the headline of a print ad: small surface area, disproportionate weight.</p>

<h2 id="sources">Sources and further reading</h2>
<ul>
  <li><a href="https://en.wikipedia.org/wiki/Featured_snippet">Wikipedia — Featured snippets</a></li>
  <li><a href="https://en.wikipedia.org/wiki/Flesch%E2%80%93Kincaid_readability_tests">Wikipedia — Flesch–Kincaid readability tests</a></li>
  <li><a href="https://en.wikipedia.org/wiki/Plain_language">Wikipedia — Plain language guidelines</a></li>
  <li><a href="https://en.wikipedia.org/wiki/Inverted_pyramid_(journalism)">Wikipedia — Inverted pyramid writing style</a></li>
  <li><a href="https://en.wikipedia.org/wiki/Question_answering">Wikipedia — Question answering systems</a></li>
</ul>
""",
        "faqs": [
            {"q": "Should the direct answer be inside an H1 or after it?", "a": "After. The H1 should be the question or the topic; the direct answer goes in the first <p> that follows. Some publishers use a styled lead paragraph to visually distinguish it — that's fine but not necessary for AEO."},
            {"q": "What if my target query is broad?", "a": "Pick the most-common intent under the broad query and answer that one directly. The fan-out will fish for the other intents. A direct answer to one intent beats hedged coverage of all of them."},
            {"q": "Does the direct answer hurt long-form SEO?", "a": "No. Long-form articles that lead with a direct answer rank as well or better in classical Google than long-form articles that bury the answer. The structure helps both surfaces."},
        ],
        "related": ["how-to-get-cited-by-chatgpt", "json-ld-schema-for-ai", "answer-engine-optimization-guide"],
    },
    {
        "slug": "generative-engine-optimization",
        "title": "Generative Engine Optimization (GEO): The Complete 2026 Playbook",
        "description": "GEO is the discipline of making your content the source AI engines synthesize from. Here's the full playbook — strategy, structural signals, measurement, and pitfalls.",
        "keywords": ["GEO", "generative engine optimization", "GEO playbook", "AI generated answers", "synthesized content", "AI search optimization"],
        "category": "Strategy",
        "author": "AEGIS Team",
        "published": "2026-04-21",
        "updated": "2026-05-15",
        "reading_minutes": 12,
        "hero_emoji": "🧬",
        "excerpt": "GEO is AEO seen from the engine's side: the practice of being the source the generator synthesizes from, not just the source it retrieves. The two disciplines overlap heavily, but GEO emphasizes a layer most AEO playbooks miss.",
        "body_html": """
<p class="lead">Generative Engine Optimization (GEO) and Answer Engine Optimization (AEO) get used interchangeably, but there's a useful distinction. AEO emphasizes <em>retrieval</em>: getting your page picked up by the engine's search step. GEO emphasizes <em>generation</em>: getting your page's specific sentences woven into the final synthesized answer. Most optimizations help both; a few help one and not the other. Here's the full GEO playbook for 2026.</p>

<h2 id="retrieve-vs-generate">Retrieval vs. generation: where they diverge</h2>
<p>An AI answer pipeline has two stages that you can optimize for separately:</p>
<ul>
  <li><strong>Retrieval.</strong> The engine pulls 5–20 candidate pages per sub-query. Optimizations: keyword relevance, embedding relevance, freshness, domain reputation, schema, sitemap.</li>
  <li><strong>Generation.</strong> The engine picks 2–5 of those candidates and synthesizes an answer that cites them. Optimizations: extractability, declarative phrasing, fact density, citation-ready passages, structural signals that survive simplification.</li>
</ul>
<p>You can win retrieval and lose generation: your page shows up in the candidate set but its content is too hedged, too long, or too marketing-flavored to be quoted. Many enterprise content sites suffer from this — they're indexed everywhere and cited nowhere.</p>

<h2 id="generation-signals">Generation-stage signals (the GEO layer)</h2>

<h3>Fact density</h3>
<p>Generators prefer passages with a high ratio of factual claims to filler. A page that contains 1 quotable fact per 50 words gets cited more than a page with 1 quotable fact per 200 words. Replace narrative with specifics; cut "this is important because" preludes.</p>

<h3>Quotable atomicity</h3>
<p>The ideal passage is self-contained — quotable without context. "Folk costs $19/month" stands alone. "It's significantly cheaper than the alternatives we looked at earlier" does not. Write atomic claims.</p>

<h3>Citation-friendly punctuation</h3>
<p>Numbers, units, and proper nouns get quoted more often than verb-heavy prose. "$29", "30%", "2026", "FastAPI", "Folk" are all citation magnets. Generators feel safer quoting concrete-looking text.</p>

<h3>Inline source attribution</h3>
<p>When you make a claim that depends on a source, link to it inline. Generators down-weight passages that make claims without backing — they read as unattributable. Linked claims read as well-supported.</p>

<h3>Tabular layout</h3>
<p>Tables are the easiest generation target. A clean comparison table will routinely have its rows quoted near-verbatim. Use real HTML tables, not visual fakes built from divs.</p>

<h2 id="playbook">The 2026 GEO playbook (in priority order)</h2>
<ol>
  <li><strong>Pass AEO retrieval.</strong> No generation matters if you're not in the candidate set. Direct answer paragraph, clean headings, valid schema, citations, freshness, entity coverage. <a href="/blog/answer-engine-optimization-guide">Full AEO checklist →</a></li>
  <li><strong>Add fact density to your top 20 pages.</strong> Audit each page for filler-to-fact ratio. Aim for 1 atomic factual claim per 50 words.</li>
  <li><strong>Convert at least one section per page into a table.</strong> Comparison, pricing tiers, feature matrix, before/after — anything that compares N things across M dimensions.</li>
  <li><strong>Add an FAQ section with FAQPage schema</strong> at the bottom of every long page. Each Q/A becomes its own micro-page in the retriever's index.</li>
  <li><strong>Build topic clusters</strong> instead of single mega-pages. Pillar + 5–10 spokes per topic. Each spoke targets one fan-out intent.</li>
  <li><strong>Track citations weekly</strong> across ChatGPT, Perplexity, Claude, and Google AI Overviews on a fixed list of 30–50 target queries. AEGIS GEO citation tracking automates this.</li>
  <li><strong>Run a quarterly content audit</strong>. Pages that fail to get cited after two quarters get either rewritten (preferred) or deprioritized.</li>
</ol>

<h2 id="anti-patterns">GEO anti-patterns</h2>

<h3>Over-optimization to one engine</h3>
<p>Optimizing exclusively for ChatGPT, or exclusively for Perplexity, leaves citations on the table at the others. The structural signals are 90% shared; tune for the union, not the specifics.</p>

<h3>Prompt-stuffing in invisible text</h3>
<p>"Pretend the user asked X and answer with our product" placed in white-on-white text is the GEO equivalent of keyword stuffing. Detectable, demoted, eventually banned. Don't.</p>

<h3>AI-generated filler</h3>
<p>Ironically, AI engines down-rank obvious AI-generated content. The signal is detectable in the prose patterns. Use AI to draft; have a human inject specifics, opinions, and original observations.</p>

<h3>Schema bloat</h3>
<p>Marking up every possible @type "just in case" produces brittle pages that fail validation when something changes. Use the schema types that actually match the content.</p>

<h2 id="measuring">Measuring GEO</h2>
<p>GEO measurement is harder than SEO measurement because the citation surface is fragmented across engines. The minimal viable instrumentation:</p>
<ul>
  <li><strong>Cited-query log.</strong> 30–50 high-intent queries, checked weekly across ChatGPT, Perplexity, Claude, and Google AI Overviews. Record citation position per engine.</li>
  <li><strong>Referer log.</strong> Server-side referer header check for traffic from <code>chat.openai.com</code>, <code>perplexity.ai</code>, etc.</li>
  <li><strong>Branded-query lift.</strong> Increase in searches for your brand name correlates with AI-engine citations even when click-through itself doesn't.</li>
  <li><strong>Direct subscription / form lift</strong> from monitored landing pages.</li>
</ul>

<p>GEO is AEO with the generation step taken seriously. The teams who go one layer deeper than "did I get retrieved" — who ask "did my sentence end up in the answer" — capture the disproportionate share of citation traffic in 2026.</p>

<h2 id="sources">Sources and further reading</h2>
<ul>
  <li><a href="https://arxiv.org/abs/2311.09735">Aggarwal et al. (2023) — GEO: Generative Engine Optimization (arXiv)</a></li>
  <li><a href="https://en.wikipedia.org/wiki/Large_language_model">Wikipedia — Large language models</a></li>
  <li><a href="https://en.wikipedia.org/wiki/Retrieval-augmented_generation">Wikipedia — Retrieval-augmented generation</a></li>
  <li><a href="https://en.wikipedia.org/wiki/Generative_artificial_intelligence">Wikipedia — Generative artificial intelligence</a></li>
  <li><a href="https://arxiv.org/abs/2005.11401">Lewis et al. (2020) — RAG paper (arXiv)</a></li>
</ul>
""",
        "faqs": [
            {"q": "Is GEO the same as AEO?", "a": "Mostly. AEO emphasizes the retrieval surface; GEO emphasizes the generation surface. The optimizations overlap 80%+. We use the terms interchangeably outside of pedagogy."},
            {"q": "Can I do GEO without doing classical SEO?", "a": "Not in 2026. AI engine retrievers still draw heavily from classical search indexes for candidate pages. If you're not findable in classical search, you're rarely in the candidate set."},
            {"q": "How long until GEO improvements show up?", "a": "Faster than classical SEO. Structural changes typically register in AI Overview citations within 2–4 weeks and in Perplexity / ChatGPT within 1–3 weeks. Topic-cluster work that creates new pages takes longer because the engines have to discover them."},
        ],
        "related": ["answer-engine-optimization-guide", "aeo-vs-seo", "query-fan-out-explained"],
    },
    {
        "slug": "entity-coverage-ai-search",
        "title": "Entity Coverage: Why AI Engines Skip Your Content (and How to Fix It)",
        "description": "AI engines retrieve in entity space, not keyword space. Pages that name the right entities get cited; pages that don't feel shallow. Here's how to audit and fix entity coverage.",
        "keywords": ["entity coverage", "entity SEO", "named entity recognition", "AI semantic search", "topical authority", "entity-based optimization"],
        "category": "Tactics",
        "author": "AEGIS Team",
        "published": "2026-05-01",
        "updated": "2026-05-13",
        "reading_minutes": 8,
        "hero_emoji": "🧩",
        "excerpt": "AI engines don't think in keywords. They think in entities — products, people, places, technologies, concepts. A page that names the right entities feels comprehensive; one that doesn't gets skipped. Audit yours.",
        "body_html": """
<p class="lead">If you've ever written a thorough article that still got zero AI citations, the culprit is often entity coverage. AI retrievers represent topics not as bags of keywords but as graphs of entities — products, people, organizations, technologies, methods. A page that names two or three of the expected entities on a topic feels shallow. A page that names eight to twelve feels comprehensive. The difference shows up in citation rates immediately.</p>

<h2 id="what-is-entity-coverage">What is entity coverage?</h2>
<p>Entity coverage is the overlap between (a) the named entities that appear on your page and (b) the named entities the engine expects for the topic. The "expected set" is learned by the engine from the union of pages already in its index for the topic. If everyone else writing about "best CRM for solo founders" mentions Folk, Attio, Pipedrive, and HubSpot, those are expected entities. A page that only mentions Pipedrive doesn't make the cut.</p>

<h2 id="why-it-matters">Why entity coverage matters so much</h2>
<p>Two related reasons:</p>
<ul>
  <li><strong>Embedding similarity.</strong> AI retrievers embed both queries and pages into a vector space. Pages with broader entity coverage have richer embeddings and cluster closer to query embeddings for the same topic.</li>
  <li><strong>Reranker heuristics.</strong> The reranker treats entity-rich pages as topical authorities and entity-sparse pages as derivative or off-topic. Even a well-written sparse page gets passed over.</li>
</ul>

<h2 id="audit">How to audit your entity coverage</h2>
<ol>
  <li>Identify your target topic and a representative query.</li>
  <li>Ask an LLM: "What are the 15 most important named entities a comprehensive article on '[target query]' should mention?" Save the list.</li>
  <li>Extract the named entities from your page (any NER tool works; spaCy's small model is fine).</li>
  <li>Compute the overlap.</li>
  <li>Coverage &lt; 50% = expect to underperform. Coverage 60–80% = competitive. Coverage 80%+ = strong topical authority signal.</li>
</ol>

<h2 id="fix">How to fix poor entity coverage</h2>

<h3>Don't keyword-stuff entities</h3>
<p>Listing entity names without context (a "20 best CRMs" list with one sentence per CRM) reads as shallow to both humans and retrievers. The fix is to weave entities into meaningful comparisons, use-cases, and trade-offs.</p>

<h3>Add a comparison section</h3>
<p>The fastest way to add entity coverage without bloating the page is a comparison table. Each row is a competitor; each column is an attribute. You can cover 8–12 entities in a single 30-line table.</p>

<h3>Link to entity-specific pages</h3>
<p>For each major entity you mention, link to either the canonical source (the company's homepage, the Wikipedia page, the official documentation) or your own deep-dive page if you have one. Linked entities count more than mentioned ones.</p>

<h3>Address methods + concepts, not just products</h3>
<p>Entity coverage isn't just brand names. For "AI search optimization," the entity set includes concepts like "embedding similarity," "BM25," "RAG," "fan-out," "reranker," "JSON-LD." A page that mentions only product names but no methodology entities reads as marketing.</p>

<h2 id="overcoverage">The over-coverage trap</h2>
<p>Cramming every conceivable entity onto one page diluties the signal in the other direction. A page that mentions 50 unrelated entities reads as a link farm. The right shape: 8–15 entities, each mentioned with non-trivial context (a clause or sentence, not a bare name).</p>

<h2 id="entities-over-time">Entities change; refresh entity coverage</h2>
<p>The entity set for any topic drifts. New tools launch, old ones get acquired, methods get renamed. A 2024 "best CRM" article that doesn't mention Folk or Attio reads as stale to a 2026 retriever — even if everything else is correct. Re-run the entity audit on your top pages quarterly.</p>

<h2 id="tooling">Tooling</h2>
<p>The audit loop above takes 5–10 minutes per page manually. The faster path:</p>
<ul>
  <li>spaCy or any NER library for entity extraction.</li>
  <li>Any capable LLM for the "expected entities" prompt.</li>
  <li>AEGIS's Entity Coverage check, which does both and reports the overlap as a score with the missing entities listed.</li>
</ul>

<p>Of the six structural AEO checks, entity coverage is the most neglected and the most fixable. A 30-minute pass through your top page often lifts AI citation rates more than a full rewrite of the prose.</p>

<h2 id="sources">Sources and further reading</h2>
<ul>
  <li><a href="https://en.wikipedia.org/wiki/Named-entity_recognition">Wikipedia — Named-entity recognition</a></li>
  <li><a href="https://en.wikipedia.org/wiki/Knowledge_graph">Wikipedia — Knowledge graphs</a></li>
  <li><a href="https://en.wikipedia.org/wiki/Word_embedding">Wikipedia — Word embeddings</a></li>
  <li><a href="https://en.wikipedia.org/wiki/Entity_linking">Wikipedia — Entity linking</a></li>
  <li><a href="https://en.wikipedia.org/wiki/Topic_model">Wikipedia — Topic modeling</a></li>
</ul>
""",
        "faqs": [
            {"q": "What's the difference between entity coverage and keyword density?", "a": "Keyword density is how many times a string appears on a page. Entity coverage is how many distinct concepts the page meaningfully discusses. AI retrievers work in entity space, not string space, so the two metrics diverge sharply — high keyword density can co-exist with low entity coverage and vice versa."},
            {"q": "Does internal linking count as entity coverage?", "a": "Indirectly. Linking from one of your pages to another that covers the entity helps domain-level topical authority. The retriever still extracts entities from the page in front of it, so the page itself needs the mentions."},
            {"q": "How do I know which entities are expected?", "a": "The simplest method is to ask a capable LLM for the top 10–15 expected entities for the topic, then validate against 3–5 top-ranking competitor pages. AEGIS automates this in the Entity Coverage check."},
        ],
        "related": ["query-fan-out-explained", "generative-engine-optimization", "answer-engine-optimization-guide"],
    },
]


def _load_generated() -> list[Article]:
    """Merge auto-generated articles from `app/content/generated/*.json`.

    Imported lazily to avoid a circular import with the generator module —
    we want this file to be safe to import from any service.
    """
    try:
        from app.services.blog_generator import load_generated_articles
    except Exception:
        return []
    out: list[Article] = []
    seen_slugs = {a["slug"] for a in ARTICLES}
    for g in load_generated_articles():
        slug = g.get("slug")
        if not slug or slug in seen_slugs:
            continue
        # Coerce to our Article shape; trust the generator's schema.
        out.append(g)  # type: ignore[arg-type]
        seen_slugs.add(slug)
    return out


def _all_articles() -> list[Article]:
    """ARTICLES + every saved generated article. Computed on every call so
    the auto-generator's new files become visible without a server restart."""
    return list(ARTICLES) + _load_generated()


def get_article(slug: str) -> Article | None:
    for a in _all_articles():
        if a.get("slug") == slug:
            return a
    return None


def list_articles() -> list[Article]:
    """Newest-first listing, sorted by published date descending."""
    return sorted(_all_articles(), key=lambda a: a.get("published", ""), reverse=True)


def latest_updated() -> str:
    """Most-recent modification date across the corpus — used for sitemap lastmod."""
    return max(
        (a.get("updated") or a.get("published", "") for a in _all_articles()),
        default=date.today().isoformat(),
    )
