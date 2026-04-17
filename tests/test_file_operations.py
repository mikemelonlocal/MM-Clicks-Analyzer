"""Tests for preset save/load and file parsing."""

import io
import json

import pytest

from file_operations import (
    PRESET_SCHEMA_VERSION,
    _is_safe_preset_key,
    load_preset,
    save_preset,
)


class FakeFile:
    """Minimal stand-in for a Streamlit UploadedFile."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


class TestIsSafePresetKey:
    @pytest.mark.parametrize("key,expected", [
        ("baseline_cpl", True),
        ("bid_top", True),
        ("_private", False),
        ("__dunder__", False),
        ("bad-dash", False),
        ("1_starts_with_digit", False),
        ("has space", False),
        ("FormSubmitter:x", False),
        ("preset_up", False),
        ("", False),
        (42, False),
    ])
    def test_cases(self, key, expected):
        assert _is_safe_preset_key(key) is expected


class TestSavePreset:
    def test_envelope_structure(self):
        out = save_preset({"baseline_cpl": 25.0, "bid_top": 105})
        parsed = json.loads(out)
        assert parsed["_preset_version"] == PRESET_SCHEMA_VERSION
        assert "_saved_at" in parsed
        assert parsed["settings"] == {"baseline_cpl": 25.0, "bid_top": 105}

    def test_filters_unsafe_keys(self):
        out = save_preset({
            "good": 1,
            "_private": 2,
            "FormSubmitter:x": 3,
            "bad-key": 4,
        })
        settings = json.loads(out)["settings"]
        assert settings == {"good": 1}


class TestLoadPreset:
    def _sess(self):
        return {}

    def test_roundtrip(self):
        saved = save_preset({"baseline_cpl": 25.0, "bid_top": 105})
        sess = self._sess()
        ok = load_preset(FakeFile(saved.encode()), sess)
        assert ok is True
        assert sess["baseline_cpl"] == 25.0
        assert sess["bid_top"] == 105

    def test_legacy_flat_format(self):
        # Pre-envelope presets were just a flat dict
        flat = json.dumps({"baseline_cpl": 25.0, "bid_top": 105}).encode()
        sess = self._sess()
        ok = load_preset(FakeFile(flat), sess)
        assert ok is True
        assert sess == {"baseline_cpl": 25.0, "bid_top": 105}

    def test_incompatible_version_rejected(self):
        bad = json.dumps({
            "_preset_version": 99,
            "settings": {"baseline_cpl": 999},
        }).encode()
        sess = self._sess()
        ok = load_preset(FakeFile(bad), sess)
        assert ok is False
        assert sess == {}

    def test_oversize_rejected(self):
        huge = b"x" * 2_000_000
        sess = self._sess()
        ok = load_preset(FakeFile(huge), sess)
        assert ok is False

    def test_invalid_json_rejected(self):
        sess = self._sess()
        ok = load_preset(FakeFile(b"not json"), sess)
        assert ok is False

    def test_non_object_rejected(self):
        sess = self._sess()
        ok = load_preset(FakeFile(b"[1,2,3]"), sess)
        assert ok is False

    def test_warns_but_accepts_on_unknown_keys(self):
        """Unknown keys are skipped; valid keys still loaded."""
        payload = json.dumps({
            "_preset_version": PRESET_SCHEMA_VERSION,
            "settings": {"bid_top": 105, "bad-key": 1, "_private": 2},
        }).encode()
        sess = self._sess()
        ok = load_preset(FakeFile(payload), sess)
        assert ok is True
        assert sess == {"bid_top": 105}
