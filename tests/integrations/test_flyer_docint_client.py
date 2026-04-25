"""
M2 — FlyerDocIntClient

Covers:
  - Credential enforcement: RuntimeError when env vars missing
  - Explicit endpoint/api_key override
  - Locale passthrough to the SDK
  - analyze_layout_file raises FileNotFoundError before calling Azure
  - analyze_layout_file happy path (SDK monkeypatched) returns AzureLayoutResult
"""

from __future__ import annotations

from pathlib import Path

import pytest

from Grocery_Sense.integrations.flyer_docint_client import (
    AzureLayoutResult,
    FlyerDocIntClient,
)


# ---------------------------------------------------------------------------
# Fake Azure SDK doubles
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, d):
        self._d = d

    def as_dict(self):
        return self._d


class _FakePoller:
    def __init__(self, result_dict, operation_id="op-flyer-1"):
        self._result = _FakeResult(result_dict)
        self.details = {"operation_id": operation_id}

    def result(self):
        return self._result


class _FakeDocIntClient:
    """Stands in for azure.ai.documentintelligence.DocumentIntelligenceClient."""

    def __init__(self, endpoint=None, credential=None):
        self.endpoint = endpoint
        self.credential = credential
        self.calls = []

    def begin_analyze_document(self, model_id, body=None, locale=None):
        # Read the file to mimic SDK behaviour.
        data = body.read() if hasattr(body, "read") else b""
        self.calls.append({"model_id": model_id, "locale": locale, "bytes": len(data)})
        return _FakePoller({"pages": [{"lines": [{"content": "sample"}]}]})


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_missing_credentials_raises(self, monkeypatch):
        monkeypatch.delenv("DOCUMENTINTELLIGENCE_ENDPOINT", raising=False)
        monkeypatch.delenv("DOCUMENTINTELLIGENCE_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="Missing Azure"):
            FlyerDocIntClient()

    def test_explicit_args_override_env(self, monkeypatch):
        monkeypatch.delenv("DOCUMENTINTELLIGENCE_ENDPOINT", raising=False)
        monkeypatch.delenv("DOCUMENTINTELLIGENCE_API_KEY", raising=False)
        client = FlyerDocIntClient(endpoint="https://fake/", api_key="fake-key")
        assert client.endpoint == "https://fake/"
        assert client.api_key == "fake-key"

    def test_env_vars_populate_fields(self, monkeypatch):
        monkeypatch.setenv("DOCUMENTINTELLIGENCE_ENDPOINT", "https://env/")
        monkeypatch.setenv("DOCUMENTINTELLIGENCE_API_KEY", "env-key")
        client = FlyerDocIntClient()
        assert client.endpoint == "https://env/"
        assert client.api_key == "env-key"

    def test_locale_stored(self, monkeypatch):
        monkeypatch.setenv("DOCUMENTINTELLIGENCE_ENDPOINT", "https://env/")
        monkeypatch.setenv("DOCUMENTINTELLIGENCE_API_KEY", "env-key")
        client = FlyerDocIntClient(locale="fr-CA")
        assert client.locale == "fr-CA"


# ---------------------------------------------------------------------------
# analyze_layout_file
# ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch) -> FlyerDocIntClient:
    monkeypatch.setenv("DOCUMENTINTELLIGENCE_ENDPOINT", "https://fake/")
    monkeypatch.setenv("DOCUMENTINTELLIGENCE_API_KEY", "fake-key")
    c = FlyerDocIntClient()
    c.client = _FakeDocIntClient()
    return c


class TestAnalyzeLayoutFile:
    def test_missing_file_raises(self, client, tmp_path):
        missing = tmp_path / "nope.pdf"
        with pytest.raises(FileNotFoundError):
            client.analyze_layout_file(missing)

    def test_happy_path_returns_layout_result(self, client, tmp_path):
        sample = tmp_path / "flyer.pdf"
        sample.write_bytes(b"fake pdf bytes")

        result = client.analyze_layout_file(sample)

        assert isinstance(result, AzureLayoutResult)
        assert result.operation_id == "op-flyer-1"
        assert result.analyze_result == {"pages": [{"lines": [{"content": "sample"}]}]}
        # SDK was called with the correct model id and locale.
        assert client.client.calls[0]["model_id"] == "prebuilt-layout"
        assert client.client.calls[0]["locale"] == "en-US"
        assert client.client.calls[0]["bytes"] > 0
