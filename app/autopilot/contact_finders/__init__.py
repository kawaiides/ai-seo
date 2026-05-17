"""Pluggable contact-discovery backends.

Each finder implements the `ContactFinder` Protocol declared in
`app.autopilot.contact_finder`. Production uses a composite chain that
prefers cheap signals (homepage `mailto:`) before burning paid API
credits (Hunter.io).
"""

from __future__ import annotations

from app.autopilot.contact_finders.composite import CompositeContactFinder
from app.autopilot.contact_finders.hunter import HunterContactFinder

__all__ = ["CompositeContactFinder", "HunterContactFinder"]
