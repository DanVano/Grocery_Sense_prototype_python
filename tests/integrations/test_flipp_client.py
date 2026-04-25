"""
M2 — FlippClient (stub)

The real Flipp API is not wired up; the client returns [] for every call.
Tests pin the current stub contract so the day a real client is wired
in, these break loudly and force a review.
"""

from __future__ import annotations

import pytest

from Grocery_Sense.integrations.flipp_client import FlippClient


@pytest.fixture
def client() -> FlippClient:
    return FlippClient()


class TestFlippClientStub:
    def test_returns_empty_list_for_any_input(self, client):
        assert client.fetch_flyers_for_store("Anywhere Mart", "V3J 0P6") == []

    def test_empty_args_still_return_empty(self, client):
        assert client.fetch_flyers_for_store("", "") == []

    def test_method_accepts_positional_args(self, client):
        # Signature must remain (store_name, postal_code).
        result = client.fetch_flyers_for_store("A", "B")
        assert isinstance(result, list)
