"""Tests for async conversion of M3UBuilder."""
import os
import tempfile
import pytest


@pytest.mark.asyncio
async def test_m3ubuilder_initializes():
    """M3UBuilder can be instantiated (eventually async)."""
    from main import M3UBuilder
    builder = M3UBuilder()
    assert builder.epg_urls == set()
    assert builder.unique_links == {}
    assert builder.epg_id_map == {}
    assert builder.final_used_ids == set()


@pytest.mark.asyncio
async def test_parse_url_headers_simple():
    """parse_url_headers returns clean URL with default headers."""
    from main import M3UBuilder
    builder = M3UBuilder()
    url, headers = builder.parse_url_headers("https://example.com/stream")
    assert url == "https://example.com/stream"
    assert headers['User-Agent'] == 'Mozilla/5.0'


@pytest.mark.asyncio
async def test_parse_url_headers_with_params():
    """parse_url_headers extracts custom headers from URL."""
    from main import M3UBuilder
    builder = M3UBuilder()
    url, headers = builder.parse_url_headers(
        "https://example.com/stream|User-Agent=Custom|Referer=https://x.com"
    )
    assert url == "https://example.com/stream"
    assert headers['User-Agent'] == 'Custom'
    assert headers['Referer'] == 'https://x.com'
