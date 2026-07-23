"""Tests for EPG raw caching, programme dedup, and channels.json output."""
import os
import json
import tempfile
import shutil
import hashlib
import xml.etree.ElementTree as ET
import pytest

# ---------------------------------------------------------------------------
# EPG Raw Save Tests
# ---------------------------------------------------------------------------


class TestEpgRawSave:
    """Tests for saving raw EPG XML to .cache/epg/ directory."""

    def test_save_epg_raw_creates_file(self, builder):
        """save_epg_raw writes XML data to .cache/epg/<url_hash>.xml."""
        url = "https://example.com/epg.xml.gz"
        xml_data = b"<tv><channel id='1'/></tv>"
        path = builder.save_epg_raw(url, xml_data)
        assert os.path.exists(path), f"Raw EPG file not created at {path}"
        with open(path, 'rb') as f:
            saved = f.read()
        assert saved == xml_data

    def test_save_epg_raw_url_hash(self, builder):
        """Filename should use SHA-256 of URL."""
        url1 = "https://example.com/epg1.xml"
        url2 = "https://example.com/epg2.xml"
        p1 = builder.save_epg_raw(url1, b"<tv/>")
        p2 = builder.save_epg_raw(url2, b"<tv/>")
        expected_hash1 = hashlib.sha256(url1.encode()).hexdigest()
        expected_hash2 = hashlib.sha256(url2.encode()).hexdigest()
        assert p1.endswith(f"{expected_hash1}.xml"), f"Expected hash {expected_hash1} in path {p1}"
        assert p2.endswith(f"{expected_hash2}.xml"), f"Expected hash {expected_hash2} in path {p2}"

    def test_save_epg_raw_directory_created(self, builder):
        """The .cache/epg/ directory should be created automatically."""
        import os
        epg_dir = os.path.join(builder.config['cache']['dir'], 'epg')
        # Remove it if exists
        if os.path.exists(epg_dir):
            shutil.rmtree(epg_dir)
        builder.save_epg_raw("https://example.com/epg.xml", b"<tv/>")
        assert os.path.isdir(epg_dir)


# ---------------------------------------------------------------------------
# Programme Dedup Tests
# ---------------------------------------------------------------------------


class TestProgrammeDedup:
    """Tests for deduplicating programme entries when merging EPG."""

    def test_dedup_same_channel_start(self, builder):
        """Programmes with same channel and start are deduped (only first kept)."""
        from epg import dedup_programmes
        prog1 = ET.fromstring(
            '<programme channel="vtv1" start="20260101000000 +0300" stop="20260101010000 +0300">'
            '<title lang="en">News</title></programme>'
        )
        prog2 = ET.fromstring(
            '<programme channel="vtv1" start="20260101000000 +0300" stop="20260101020000 +0300">'
            '<title lang="en">Extended News</title></programme>'
        )
        prog3 = ET.fromstring(
            '<programme channel="vtv2" start="20260101000000 +0300" stop="20260101010000 +0300">'
            '<title lang="en">Movie</title></programme>'
        )

        result = dedup_programmes([prog1, prog2, prog3])
        assert len(result) == 2, f"Expected 2 unique programmes, got {len(result)}"
        # prog1 (first occurrence) should be kept
        titles = [p.findtext('title') for p in result]
        assert 'News' in titles
        assert 'Movie' in titles

    def test_dedup_no_duplicates(self, builder):
        """All unique programmes are kept."""
        from epg import dedup_programmes
        progs = [
            ET.fromstring(f'<programme channel="ch{i}" start="20260101000000 +0300" '
                          f'stop="20260101010000 +0300"><title>Prog {i}</title></programme>')
            for i in range(5)
        ]
        result = dedup_programmes(progs)
        assert len(result) == 5

    def test_dedup_same_channel_different_start(self, builder):
        """Same channel but different start times are all kept."""
        from epg import dedup_programmes
        progs = [
            ET.fromstring('<programme channel="vtv1" start="20260101000000 +0300" '
                          'stop="20260101010000 +0300"><title>Prog 1</title></programme>'),
            ET.fromstring('<programme channel="vtv1" start="20260101010000 +0300" '
                          'stop="20260101020000 +0300"><title>Prog 2</title></programme>'),
        ]
        result = dedup_programmes(progs)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# channels.json Generation Tests
# ---------------------------------------------------------------------------


class TestChannelsJson:
    """Tests for generating channels.json in iptvschema.org format."""

    def test_generate_channels_json_basic_structure(self, builder_with_channels):
        """channels.json should have Provider > Groups > Channels structure."""
        from output import generate_channels_json
        generate_channels_json(builder_with_channels.final_playlist, builder_with_channels.output_channels)
        assert os.path.exists('channels.json'), "channels.json not created"
        with open('channels.json', 'r') as f:
            data = json.load(f)

        # Root provider fields
        assert 'id' in data
        assert 'name' in data
        assert 'groups' in data
        assert isinstance(data['groups'], list)

    def test_generate_channels_json_groups(self, builder_with_channels):
        """Channels should be grouped correctly by their group attribute."""
        from output import generate_channels_json
        generate_channels_json(builder_with_channels.final_playlist, builder_with_channels.output_channels)
        with open('channels.json', 'r') as f:
            data = json.load(f)

        group_names = [g['name'] for g in data['groups']]
        assert 'VTV' in group_names
        assert 'HTV' in group_names

    def test_generate_channels_json_channel_structure(self, builder_with_channels):
        """Each channel should have id, name, sources."""
        from output import generate_channels_json
        generate_channels_json(builder_with_channels.final_playlist, builder_with_channels.output_channels)
        with open('channels.json', 'r') as f:
            data = json.load(f)

        vtv_group = next(g for g in data['groups'] if g['name'] == 'VTV')
        assert len(vtv_group['channels']) > 0
        ch = vtv_group['channels'][0]
        assert 'id' in ch
        assert 'name' in ch
        assert 'sources' in ch
        assert len(ch['sources']) > 0

    def test_generate_channels_json_source_structure(self, builder_with_channels):
        """Each source should have contents with streams and stream_links."""
        from output import generate_channels_json
        generate_channels_json(builder_with_channels.final_playlist, builder_with_channels.output_channels)
        with open('channels.json', 'r') as f:
            data = json.load(f)

        all_channels = [ch for g in data['groups'] for ch in g['channels']]
        ch = all_channels[0]
        src = ch['sources'][0]
        assert 'contents' in src
        assert len(src['contents']) > 0
        content = src['contents'][0]
        assert 'streams' in content
        assert len(content['streams']) > 0
        stream = content['streams'][0]
        assert 'stream_links' in stream
        assert len(stream['stream_links']) > 0

    def test_generate_channels_json_stream_links(self, builder_with_channels):
        """Stream links should contain url and type."""
        from output import generate_channels_json
        generate_channels_json(builder_with_channels.final_playlist, builder_with_channels.output_channels)
        with open('channels.json', 'r') as f:
            data = json.load(f)

        all_channels = [ch for g in data['groups'] for ch in g['channels']]
        ch = all_channels[0]
        link = ch['sources'][0]['contents'][0]['streams'][0]['stream_links'][0]
        assert 'url' in link
        assert 'type' in link
        assert link['url'].startswith('http')

    def test_generate_channels_json_fallback_urls(self, builder_with_fallbacks):
        """Channels with fallback URLs should have multiple stream_links."""
        from output import generate_channels_json
        generate_channels_json(builder_with_fallbacks.final_playlist, builder_with_fallbacks.output_channels)
        with open('channels.json', 'r') as f:
            data = json.load(f)

        all_channels = [ch for g in data['groups'] for ch in g['channels']]
        ch = next(c for c in all_channels if c['name'] == 'VTV1')
        links = ch['sources'][0]['contents'][0]['streams'][0]['stream_links']
        assert len(links) == 2  # primary + 1 fallback
        # Primary should be default
        assert links[0]['default'] == True
        assert links[1]['default'] == False

    def test_generate_channels_json_tvg_metadata(self, builder_with_channels):
        """Channel should include tvg_id and tvg_logo in extras."""
        from output import generate_channels_json
        generate_channels_json(builder_with_channels.final_playlist, builder_with_channels.output_channels)
        with open('channels.json', 'r') as f:
            data = json.load(f)

        all_channels = [ch for g in data['groups'] for ch in g['channels']]
        # Check we have at least one channel with tvg metadata
        has_tvg = any('tvg_id' in ch and 'tvg_logo' in ch for ch in all_channels)
        assert has_tvg


# ---------------------------------------------------------------------------
# Config Tests (output_channels setting)
# ---------------------------------------------------------------------------


class TestChannelsJsonConfig:
    """Tests for output_channels config setting."""

    def test_default_config_has_output_channels(self):
        """DEFAULT_CONFIG should include output_channels setting."""
        from config import DEFAULT_CONFIG
        assert 'output_channels' in DEFAULT_CONFIG['general']
        assert DEFAULT_CONFIG['general']['output_channels'] == 'channels.json'

    def test_builder_initializes_output_channels(self, builder):
        """M3UBuilder stores output_channels from config."""
        assert builder.output_channels == 'channels.json'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def builder():
    """Create M3UBuilder with temp cache dir."""
    from m3u_builder import M3UBuilder
    cfg = {
        'general': {
            'source_file': 'sources.txt',
            'output_file': 'playlist.m3u',
            'output_epg': 'epg.xml.gz',
            'output_channels': 'channels.json',
            'timeout': 10,
            'stream_timeout': 3,
            'max_workers': 64,
            'spam_keywords': [],
        },
        'cache': {
            'enabled': True,
            'dir': '.cache',
            'epg_ttl': 3600,
            'source_ttl': 300,
            'link_ttl': 600,
        },
    }
    b = M3UBuilder(cfg)
    yield b
    # Cleanup
    for f in ['channels.json']:
        if os.path.exists(f):
            os.remove(f)
    cache_dir = b.config['cache']['dir']
    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)


@pytest.fixture
def builder_with_channels(builder):
    """Builder with populated channel data."""
    builder.final_playlist = [
        {
            'name': 'VTV1',
            'group': 'VTV',
            'url': 'https://example.com/vtv1.m3u8',
            'tvg_id': 'vtv1',
            'tvg_logo': 'https://example.com/vtv1.png',
            'final_id': 'vtv1',
            'final_logo': 'https://example.com/vtv1.png',
            'extra_tags': [],
            'fallback_urls': [],
        },
        {
            'name': 'VTV2',
            'group': 'VTV',
            'url': 'https://example.com/vtv2.m3u8',
            'tvg_id': 'vtv2',
            'tvg_logo': '',
            'final_id': 'vtv2',
            'final_logo': '',
            'extra_tags': [],
            'fallback_urls': [],
        },
        {
            'name': 'HTV7',
            'group': 'HTV',
            'url': 'https://example.com/htv7.m3u8',
            'tvg_id': 'htv7',
            'tvg_logo': 'https://example.com/htv7.png',
            'final_id': 'htv7',
            'final_logo': 'https://example.com/htv7.png',
            'extra_tags': [],
            'fallback_urls': [],
        },
    ]
    return builder


@pytest.fixture
def builder_with_fallbacks(builder):
    """Builder with channel that has fallback URLs."""
    builder.final_playlist = [
        {
            'name': 'VTV1',
            'group': 'VTV',
            'url': 'https://example.com/vtv1.m3u8',
            'tvg_id': 'vtv1',
            'tvg_logo': 'https://example.com/vtv1.png',
            'final_id': 'vtv1',
            'final_logo': 'https://example.com/vtv1.png',
            'extra_tags': [],
            'fallback_urls': ['https://backup.com/vtv1.m3u8'],
        },
    ]
    return builder
