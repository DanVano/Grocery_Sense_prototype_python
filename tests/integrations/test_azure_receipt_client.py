"""
M2 — AzureReceiptClient

Covers:
  - Credentials required at construction (RuntimeError)
  - Explicit args and env vars both work
  - Locale stored
  - analyze_receipt_file retry logic:
      * 429 / 500 / 502 / 503 are retriable
      * 400 / 401 / 403 / 404 bail immediately
      * ServiceRequestError (network) retried
      * max_attempts respected before raising
      * operation_id falls back to a synthesized value when SDK omits one
  - analyze_and_save_json writes the raw JSON to disk
  - FileNotFoundError raised before any SDK call when file is missing
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from azure.core.exceptions import HttpResponseError, ServiceRequestError

from Grocery_Sense.integrations.azure_docint_client import (
    AzureReceiptClient,
    AzureReceiptResult,
)


# ---------------------------------------------------------------------------
# Doubles for the Azure SDK
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, d):
        self._d = d

    def as_dict(self):
        return self._d


class _FakePoller:
    def __init__(self, result_dict, *, operation_id="op-abc"):
        self._result = _FakeResult(result_dict)
        self.details = {"operation_id": operation_id}

    def result(self):
        return self._result


class _FakeSdkClient:
    """
    Accepts a list where each entry is either an Exception (raised) or a
    _FakePoller (returned) per call.
    """

    def __init__(self, sequence):
        self._seq = list(sequence)
        self.calls = []

    def begin_analyze_document(self, model_id, body=None, locale=None):
        # Drain the body so the real bytes are read, mirroring SDK behaviour.
        if hasattr(body, "read"):
            body.read()
        self.calls.append({"model_id": model_id, "locale": locale})
        if not self._seq:
            raise AssertionError("SDK called more times than test set up")
        thing = self._seq.pop(0)
        if isinstance(thing, Exception):
            raise thing
        return thing


def _http_err(status: int) -> HttpResponseError:
    """HttpResponseError requires a response kw; we construct bare and set status."""
    err = HttpResponseError(message=f"fake {status}")
    err.status_code = status
    return err


@pytest.fixture(autouse=True)
def _kill_sleep(monkeypatch):
    """time.sleep() is called between retries — short-circuit it in tests."""
    monkeypatch.setattr(time, "sleep", lambda *_args, **_kw: None)


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("DOCUMENTINTELLIGENCE_ENDPOINT", "https://fake/")
    monkeypatch.setenv("DOCUMENTINTELLIGENCE_API_KEY", "fake-key")


@pytest.fixture
def sample_file(tmp_path) -> Path:
    p = tmp_path / "receipt.jpg"
    p.write_bytes(b"fake bytes")
    return p


def _install(client: AzureReceiptClient, sequence) -> _FakeSdkClient:
    fake = _FakeSdkClient(sequence)
    client.client = fake
    return fake


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_missing_credentials_raises(self, monkeypatch):
        monkeypatch.delenv("DOCUMENTINTELLIGENCE_ENDPOINT", raising=False)
        monkeypatch.delenv("DOCUMENTINTELLIGENCE_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="Missing Azure"):
            AzureReceiptClient()

    def test_explicit_args_work(self, monkeypatch):
        monkeypatch.delenv("DOCUMENTINTELLIGENCE_ENDPOINT", raising=False)
        monkeypatch.delenv("DOCUMENTINTELLIGENCE_API_KEY", raising=False)
        client = AzureReceiptClient(endpoint="https://x/", api_key="y")
        assert client.endpoint == "https://x/"

    def test_locale_stored(self, creds):
        client = AzureReceiptClient(locale="fr-CA")
        assert client.locale == "fr-CA"


# ---------------------------------------------------------------------------
# analyze_receipt_file
# ---------------------------------------------------------------------------


class TestAnalyzeReceiptFile:
    def test_happy_path(self, creds, sample_file):
        client = AzureReceiptClient()
        fake = _install(client, [_FakePoller({"documents": [{"fields": {}}]})])

        op_id, result = client.analyze_receipt_file(sample_file)
        assert op_id == "op-abc"
        assert result == {"documents": [{"fields": {}}]}
        assert fake.calls[0]["model_id"] == "prebuilt-receipt"

    def test_missing_file_raises_before_sdk_call(self, creds, tmp_path):
        client = AzureReceiptClient()
        fake = _install(client, [])  # no sequence — any call would assert
        with pytest.raises(FileNotFoundError):
            client.analyze_receipt_file(tmp_path / "nope.jpg")
        assert fake.calls == []

    def test_synthesizes_operation_id_when_sdk_omits(self, creds, sample_file):
        client = AzureReceiptClient()
        poller = _FakePoller({"documents": []}, operation_id="")
        _install(client, [poller])

        op_id, _ = client.analyze_receipt_file(sample_file)
        assert op_id.startswith("op_")  # format: op_<timestamp>_<stem>
        assert "receipt" in op_id

    @pytest.mark.parametrize("status", [429, 500, 502, 503])
    def test_retriable_statuses_retry(self, creds, sample_file, status):
        client = AzureReceiptClient()
        fake = _install(
            client,
            [_http_err(status), _FakePoller({"documents": []})],
        )
        op_id, _ = client.analyze_receipt_file(sample_file, max_attempts=3, base_delay=0)
        assert op_id == "op-abc"
        assert len(fake.calls) == 2

    @pytest.mark.parametrize("status", [400, 401, 403, 404])
    def test_non_retriable_statuses_bail_immediately(self, creds, sample_file, status):
        client = AzureReceiptClient()
        fake = _install(client, [_http_err(status)])
        with pytest.raises(HttpResponseError):
            client.analyze_receipt_file(sample_file, max_attempts=3, base_delay=0)
        # Only one attempt — no retry on 4xx auth/client errors.
        assert len(fake.calls) == 1

    def test_service_request_error_retried(self, creds, sample_file):
        client = AzureReceiptClient()
        fake = _install(
            client,
            [ServiceRequestError("network down"), _FakePoller({"documents": []})],
        )
        op_id, _ = client.analyze_receipt_file(sample_file, max_attempts=3, base_delay=0)
        assert op_id == "op-abc"
        assert len(fake.calls) == 2

    def test_exhausts_attempts_then_raises(self, creds, sample_file):
        client = AzureReceiptClient()
        _install(
            client,
            [_http_err(500), _http_err(500), _http_err(500)],
        )
        with pytest.raises(HttpResponseError):
            client.analyze_receipt_file(sample_file, max_attempts=3, base_delay=0)


# ---------------------------------------------------------------------------
# analyze_and_save_json
# ---------------------------------------------------------------------------


class TestAnalyzeAndSaveJson:
    def test_writes_json_to_disk(self, creds, sample_file, tmp_path):
        client = AzureReceiptClient()
        _install(
            client,
            [_FakePoller({"documents": [{"fields": {"x": 1}}]}, operation_id="op-123")],
        )

        out_dir = tmp_path / "out"
        result = client.analyze_and_save_json(sample_file, out_dir)

        assert isinstance(result, AzureReceiptResult)
        assert result.operation_id == "op-123"
        assert result.saved_json_path.exists()
        assert result.saved_json_path.parent == out_dir

        # Filename format: {safe_stem}__{op_id}.json
        assert "receipt" in result.saved_json_path.name
        assert "op-123" in result.saved_json_path.name

        on_disk = json.loads(result.saved_json_path.read_text(encoding="utf-8"))
        assert on_disk == {"documents": [{"fields": {"x": 1}}]}

    def test_creates_output_dir_when_missing(self, creds, sample_file, tmp_path):
        client = AzureReceiptClient()
        _install(client, [_FakePoller({"documents": []})])

        out_dir = tmp_path / "nested" / "deeper"
        assert not out_dir.exists()

        client.analyze_and_save_json(sample_file, out_dir)
        assert out_dir.is_dir()
