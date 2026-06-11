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

    def test_spam_keywords_empty(self, builder):
        # spam_keywords is empty — filtering relies on content validation
        from main import SPAM_KEYWORDS
        assert len(SPAM_KEYWORDS) == 0


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

    def test_vietnam_today_in_vtv(self, builder):
        assert builder.smart_grouping("", "VIETNAM TODAY") == "VTV"
        assert builder.smart_grouping("", "VIET NAM TODAY") == "VTV"


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

    def test_prefer_branded_id_over_wrong_non_numeric(self, builder):
        """VTV1 should match 'vtv1hd' (brand-aware) not 'TV1' (wrong brand)."""
        builder.epg_id_map = {
            "vtv1hd": "vtv1hd",
            "tv1": "TV1",
        }
        builder.xml_name_mapping = {
            "VTV1": "TV1",  # Korean EPG: name=VTV1 → id=TV1
            "VTV1 HD": "vtv1hd",  # vnepg.site: name=VTV1 HD → id=vtv1hd
        }
        # Tier 2: "VTV1" in xml_name_mapping → "TV1" (non-numeric, no brand) → text_fallback
        # Tier 3: "vtv1hd" in epg_id_map matches "vtv1" + "hd" → return "vtv1hd"
        result = builder.get_best_id_match("VTV1", "vtv1hd.VN")
        assert result == "vtv1hd", f"Expected vtv1hd, got {result}"

    def test_wrong_brand_non_numeric_fallback(self, builder):
        """When no branded EPG ID exists, non-numeric wrong-brand is still better than numeric."""
        builder.epg_id_map = {}
        builder.xml_name_mapping = {
            "VTV1": "TV1",
        }
        result = builder.get_best_id_match("VTV1", "")
        assert result == "TV1", f"Expected TV1 (non-numeric fallback), got {result}"
