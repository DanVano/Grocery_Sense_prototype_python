"""
Grocery_Sense.integrations.flipp_client

Flipp API client — stubbed out, not yet implemented.

To wire up a real flyer provider:
1. Add credentials to environment variables or config_store
2. Implement fetch_flyers_for_store() to call the real HTTP endpoint
3. Parse the response into the deal dict schema below and return it

Expected deal dict keys returned by fetch_flyers_for_store():
    title       : str          e.g. "Chicken Thighs Family Pack"
    description : str          optional longer description
    price_text  : str          e.g. "$5.99/kg"  (display string)
    unit_price  : float|None   numeric price per unit
    unit        : str          "kg", "lb", "each", etc.
    valid_from  : str          "YYYY-MM-DD"  (can be empty if unknown)
    valid_to    : str          "YYYY-MM-DD"  (can be empty if unknown)
"""

from __future__ import annotations

from typing import Any, Dict, List


class FlippClient:
    """Stub Flipp API client. Returns empty data until a real provider is wired in."""

    def fetch_flyers_for_store(
        self,
        store_name: str,
        postal_code: str,
    ) -> List[Dict[str, Any]]:
        """
        Fetch active flyer deals for a single store.

        Returns a list of deal dicts (see module docstring for schema).

        STUB — currently returns an empty list.
        Replace this body with the real HTTP call when credentials are available.
        """
        return []
