"""Tests that preserve normalize_channel_name, smart_grouping, get_best_id_match logic."""
import pytest
from main import M3UBuilder


@pytest.fixture
def builder():
    return M3UBuilder()


class TestNormalizeChannelName:
    """Must preserve exactly the original normalization logic."""

    def test_basic_normalize(self, builder):
        assert builder.normalize_channel_name("VTV1") == "VTV1"

    def test_strip_hd_suffix(self, builder):
        assert builder.normalize_channel_name("VTV1HD") == "VTV1"

    def test_strip_fhd_suffix(self, builder):
        assert builder.normalize_channel_name("HTV7FHD") == "HTV7"

    def test_strip_sd_suffix(self, builder):
        assert builder.normalize_channel_name("VTV3SD") == "VTV3"

    def test_split_on_pipe(self, builder):
        assert builder.normalize_channel_name("VTV1|1080p") == "VTV1"

    def test_split_on_underscore(self, builder):
        assert builder.normalize_channel_name("VTV1_HD") == "VTV1"

    def test_clean_tags_vn(self, builder):
        assert "VTV1" in builder.normalize_channel_name("VTV1 [VN]")

    def test_fix_brands_vtv(self, builder):
        assert builder.normalize_channel_name("VTV 1") == "VTV1"

    def test_fix_brands_htv(self, builder):
        assert builder.normalize_channel_name("HTV 7") == "HTV7"

    def test_fix_brands_vtc(self, builder):
        # Note: RE_CLEAN_TAGS strips 'vtc' before RE_FIX_BRANDS runs
        result = builder.normalize_channel_name("VTC 3")
        assert result == "3"

    def test_vv_to_vtv(self, builder):
        # VV prefix removal: VVTV1 → VTV + TV1 = VTVTV1 (existing behavior)
        assert builder.normalize_channel_name("VVTV1") == "VTVTV1"

    def test_trim_whitespace(self, builder):
        assert builder.normalize_channel_name("  VTV1  ") == "VTV1"

    def test_remove_special_chars(self, builder):
        result = builder.normalize_channel_name("VTV1!!!")
        assert result == "VTV1"

    def test_spam_keywords_matches_lower(self, builder):
        # normalize_channel_name doesn't filter spam (add_channel does)
        # But spam keywords exist in the class/settings scope
        from main import SPAM_KEYWORDS
        assert "test" in [s.lower() for s in SPAM_KEYWORDS]


class TestSmartGrouping:
    """Must preserve exactly the original grouping logic."""

    def test_intl_channel(self, builder):
        assert builder.smart_grouping("", "HBO") == "Quốc Tế"

    def test_vtv_prime(self, builder):
        assert builder.smart_grouping("", "VTV1 PRIME") == "VTVPRIME"

    def test_vtv_channel(self, builder):
        assert builder.smart_grouping("", "VTV1") == "VTV"

    def test_htv_channel(self, builder):
        assert builder.smart_grouping("", "HTV7") == "HTV"

    def test_vtc_channel(self, builder):
        assert builder.smart_grouping("", "VTC3") == "VTC"

    def test_kplus_channel(self, builder):
        assert builder.smart_grouping("", "K+") == "K+"

    def test_vtvcab_channel(self, builder):
        assert builder.smart_grouping("", "VTVCAB1") == "VTVCAB / ON"

    def test_on_channel(self, builder):
        assert builder.smart_grouping("", "ON VTV") == "VTVCAB / ON"

    def test_local_group(self, builder):
        assert builder.smart_grouping("Địa Phương", "Some Channel") == "Địa Phương"

    def test_sports_group(self, builder):
        # Using a name that doesn't match VTV/HTV/VTC rules first
        assert builder.smart_grouping("", "SPORTS CHANNEL") == "Thể Thao"

    def test_movies_group(self, builder):
        # Using a name that doesn't match higher-priority rules
        assert builder.smart_grouping("", "CINEMA WORLD") == "Phim Truyện"

    def test_raw_group_preserved(self, builder):
        assert builder.smart_grouping("Giải Trí", "Some Channel") == "Giải Trí"

    def test_fallback_to_khac(self, builder):
        assert builder.smart_grouping("", "UNKNOWN") == "Khác"


class TestGetBestIdMatch:
    """Must preserve exactly the original EPG ID matching logic."""

    @pytest.fixture
    def builder_with_epg(self, builder):
        builder.epg_id_map = {
            "vtv1": "vtv1",
            "vtv2.vn": "vtv2.vn",
            "htv7": "htv7",
            "htv7hd": "htv7hd",
        }
        builder.xml_name_mapping = {
            "VTV1": "vtv1",
            "VTV2": "vtv2.vn",
            "HTV7": "htv7",
        }
        return builder

    def test_exact_orig_id_match(self, builder_with_epg):
        assert builder_with_epg.get_best_id_match("VTV1", "vtv1") == "vtv1"

    def test_clean_name_match(self, builder_with_epg):
        assert builder_with_epg.get_best_id_match("VTV1", "") == "vtv1"

    def test_fuzzy_id_match(self, builder_with_epg):
        assert builder_with_epg.get_best_id_match("HTV7", "") == "htv7"

    def test_no_match_returns_empty(self, builder_with_epg):
        assert builder_with_epg.get_best_id_match("XYZ", "") == ""
