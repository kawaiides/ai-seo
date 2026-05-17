"""GEO (Generative Engine Optimization) — Phase C.2.

Probes generative answer engines (ChatGPT / Perplexity / Gemini) for a
target query, parses the URLs they say they would cite, and tracks
whether the customer's site appears in those citations over time.

v0 deliberately uses a single-provider probe (`OpenAIChatProbe`) and
asks the model to list URLs it would cite — no live web search. v1 will
swap in real citation-aware APIs (Perplexity's `/chat/completions` with
`return_citations=true`, Brave Search + Gemini grounding, etc.); the
`GEOProbe` Protocol keeps the rest of the system decoupled from that
swap.
"""
