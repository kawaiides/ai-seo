"""Internationalisation (Phase F).

Three concerns split across this package:
  - `lang_detect` — pick the most likely ISO-639-1 language for a body of text.
  - `locale`      — locale registry + readability metric routing.
  - `prompts`     — locale-aware fan-out / rewrite prompt templates.

The package never imports a per-language spaCy model up-front; that
work is deferred to `app/services/nlp.get_nlp_for_locale(locale)` so
process boot remains cheap and locales without an installed model
degrade rather than crashing.
"""
