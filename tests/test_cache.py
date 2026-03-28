"""Tests for resume cache service."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from hr_breaker.services.cache import ResumeCache
from hr_breaker.models import ResumeSource


class TestResumeCache:
    """Tests for ResumeCache."""

    def test_get_returns_none_for_corrupt_json(self, tmp_path):
        """Cache.get() should return None for corrupt JSON, not raise."""
        with patch.object(ResumeCache, '__init__', lambda self: None):
            cache = ResumeCache()
            cache.cache_dir = tmp_path

            # Write corrupt JSON
            corrupt_file = tmp_path / "abc123.json"
            corrupt_file.write_text("{invalid json")

            # Should return None, not raise JSONDecodeError
            result = cache.get("abc123")
            assert result is None

    def test_get_returns_none_for_invalid_schema(self, tmp_path):
        """Cache.get() should return None for invalid Pydantic schema."""
        with patch.object(ResumeCache, '__init__', lambda self: None):
            cache = ResumeCache()
            cache.cache_dir = tmp_path

            # Write valid JSON but invalid schema
            bad_schema = tmp_path / "def456.json"
            bad_schema.write_text('{"unknown_field": "value"}')

            # Should return None, not raise ValidationError
            result = cache.get("def456")
            assert result is None

    def test_get_returns_valid_resume(self, tmp_path):
        """Cache.get() returns resume for valid cache file."""
        with patch.object(ResumeCache, '__init__', lambda self: None):
            cache = ResumeCache()
            cache.cache_dir = tmp_path

            # Write valid cache
            source = ResumeSource(content="test resume", first_name="John")
            valid_file = tmp_path / f"{source.checksum}.json"
            valid_file.write_text(source.model_dump_json())

            result = cache.get(source.checksum)
            assert result is not None
            assert result.content == "test resume"
            assert result.first_name == "John"

    def test_get_returns_none_for_missing_file(self, tmp_path):
        """Cache.get() returns None for non-existent checksum."""
        with patch.object(ResumeCache, '__init__', lambda self: None):
            cache = ResumeCache()
            cache.cache_dir = tmp_path

            result = cache.get("nonexistent")
            assert result is None


    def test_get_marks_legacy_profile_synthesis_entries(self, tmp_path):
        """Legacy profile-synthesized pasted entries should be hidden as profile sources."""
        with patch.object(ResumeCache, '__init__', lambda self: None):
            cache = ResumeCache()
            cache.cache_dir = tmp_path
            legacy = {
                "content": "Profile: Jane Doe\n\nSelected evidence...",
                "first_name": "Jane",
                "timestamp": "2026-03-14T12:00:00",
            }
            legacy_file = tmp_path / "legacy-profile.json"
            legacy_file.write_text(json.dumps(legacy))

            result = cache.get("legacy-profile")
            assert result is not None
            assert result.source_type == "profile"
            assert result.source_profile_name == "Jane Doe"
