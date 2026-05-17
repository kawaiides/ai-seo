"""Rewrite suggestions — auto-fix layer over the AEO checks.

Phase B of the roadmap. Two complementary halves:

- `headings`        — pure-deterministic restructure (no LLM).
- `direct_answer`   — LLM-driven rewrites with a Check A back-validation
                      loop so we don't return suggestions that themselves
                      fail the check they are supposed to fix.
"""
