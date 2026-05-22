import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import re
import os
import gzip
import xml.etree.ElementTree as ET
import concurrent.futures
import logging

# Cấu hình Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

SOURCE_FILE = "sources.txt"
OUTPUT_FILE = "playlist.m3u"
OUTPUT_EPG = "light_epg.xml"

TIMEOUT = 10
STREAM_TIMEOUT = 2  
MAX_WORKERS = 64     

SPAM_KEYWORDS = ['mời quý khán giả', 'thông báo', 'tạm ngưng', 'bảo trì', 'kênh dự phòng', 'test']

GROUP_PRIORITY = {
    'VTV': 1, 'HTV': 2, 'VTC': 3, 'VTVCAB / ON': 4, 'VTVPRIME': 5, 
    'K+': 6, 'THỂ THAO': 7, 'PHIM TRUYỆN': 8, 'QUỐC TẾ': 9, 'ĐỊA PHƯƠNG': 10
}

# Tiền biên dịch Regex
RE_SPLIT_NAME = re.compile(r'[_\|]')
RE_CLEAN_TAGS = re.compile(r'(?i)[\[\(\-_\.]?\b(fhd|hd|sd|1080p|720p|4k|vn|vie|h264|hevc|clip|tv|fpt|sctv|vtc|local|chính|phụ)\b[\]\)\-_\.]?')
RE_FIX_BRANDS = re.compile(r'(?i)(vtv|htv|vtc|sctv|vtvcab|k\+)\s+(\d+)')
RE_SPECIAL_CHARS = re.compile(r'[^\w\s\+]')
RE_INTL = re.compile(r'\b(hbo|cinemax|axn|discovery|disney|cartoon|fox|warner|paramount|nat geo|fashion|fon|cnbc|cnn|bbc)\b')
RE_VTV_PRIME = re.compile(r'\bprime\b')
RE_VTV_NUM = re.compile(r'\bvtv\d*\b')
RE_HTV_NUM = re.compile(r'\bhtv\d*\b')
RE_VTC_NUM = re.compile(r'\bvtc\d*\b')
RE_VTVCAB = re.compile(r'\b(cab|vtvcab)\d*\b')
RE_ON = re.compile(r'\bon\b')
RE_LOCAL = re.compile(r'\b(địa phương|tỉnh|local)\b')
RE_SPORTS = re.compile(r'\b(thể thao|sports|bóng đá)\b')
RE_MOVIES = re.compile(r'\b(phim|movies|cinema)\b')
RE_TVG_ID = re.compile(r'tvg-id=["\']([^"\']+)["\']', re.I)
RE_TVG_LOGO = re.compile(r'tvg-logo=["\']([^"\']+)["\']', re.I)
RE_GROUP_TITLE = re.compile(r'group-title="([^"]*)"')
RE_TVG_URL = re.compile(r'(?:x-tvg-url|url-tvg)="([^"]*)"', re.I)
RE_NAT_KEY = re.compile(r'(\d+)')

class M3UBuilder:
    def __init__(self):
        self.epg_urls = set()
        self.unique_links = {}
        self.available_xml_ids = set()
        self.xml_name_mapping = {} # KHÔI PHỤC BỘ NHỚ MAP TÊN
        self.epg_xml_roots = []    
        self.final_used_ids = set()
        self.source_status = {}
        
        self.session = requests.Session()
        retries = Retry(total=2, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retries, pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

    def normalize_channel_name(self, name: str) -> str:
        name = RE_SPLIT_NAME.split(name)[0] 
        name = RE_CLEAN_TAGS.sub(' ', name)
        name = RE_FIX_BRANDS.sub(r'\1\2', name)
        name = RE_SPECIAL_CHARS.sub('', name)
        return ' '.join(name.split()).strip().upper()

    def smart_grouping(self, raw_group: str, clean_name: str) -> str:
        g_lower = raw_group.lower() if raw_group else ""
        n_lower = clean_name.lower()
        
        if RE_INTL.search(n_lower): return 'Quốc Tế'
        if RE_VTV_PRIME.search(n_lower) and RE_VTV_NUM.search(n_lower): return 'VTVPRIME'
        if RE_VTVCAB.search(n_lower) or RE_ON.search(n_lower): return 'VTVCAB / ON'
        if RE_VTV_NUM.search(n_lower): return 'VTV'
        if RE_HTV_NUM.search(n_lower): return 'HTV'
        if RE_VTC_NUM.search(n_lower): return 'VTC'
        if 'k+' in n_lower: return 'K+'
        
        if RE_LOCAL.search(g_lower) or RE_LOCAL.search(n_lower): return 'Địa Phương'
        if RE_SPORTS.search(g_lower) or RE_SPORTS.search(n_lower): return 'Thể Thao'
        if RE_MOVIES.search(g_lower) or RE_MOVIES.search(n_lower): return 'Phim Truyện'
        
        if raw_group and raw_group.strip() and raw_group.strip().lower() not in ['khác', 'other', 'undefined']:
            return raw_group.strip().title()
            
        return 'Khác'

    def get_sort_key(self, channel):
        group = channel['group'].upper()
        priority = GROUP_PRIORITY.get(group, 99)
        name = channel['name']
        nat_key = [int(c) if c.isdigit() else c.lower() for c in RE_NAT_KEY.split(name)]
        return (priority, group, nat_key)

    def parse_url_headers(self, url: str):
        headers = {'User-Agent': 'Mozilla/5.0'}
        clean_url = url
        if '|' in url:
            parts = url.split('|')
            clean_url = parts[0]
            for part in parts[1:]:
                if '=' in part:
                    k, v = part.split('=', 1)
                    headers[k.strip()] = v.strip()
        return clean_url, headers

    def add_channel(self, extinf: str, url: str, raw_group: str, extra_tags: list):
        raw_name = extinf.split(',')[-1].strip()
        clean_name = self.normalize_channel_name(raw_name)
        if len(clean_name) < 2 or any(spam in clean_name.lower() for spam in SPAM_KEYWORDS): return
        
        id_match = RE_TVG_ID.search(extinf)
        logo_match = RE_TVG_LOGO.search(extinf)

        found_id = id_match.group(1).strip() if id_match else ""
        found_logo = logo_match.group(1).strip() if logo_match else ""

        if url not in self.unique_links:
            self.unique_links[url] = {
                'url': url,
                'name': clean_name,
                'group': self.smart_grouping(raw_group, clean_name),
                'tvg_id': found_id,
                'tvg_logo': found_logo,
                'extra_tags': extra_tags
            }

    def check_single_link(self, data):
        clean_url, headers = self.parse_url_headers(data['url'])
        try:
            res = self.session.head(clean_url, headers=headers, timeout=STREAM_TIMEOUT, allow_redirects=True)
            if res.status_code < 400: return data
        except requests.RequestException:
            pass
        return None

    def process_source(self, url):
        try:
            res = self.session.get(url, timeout=TIMEOUT)
            res.raise_for_status()
            self.source_status[url] = True
            
            curr_extinf = ""
            curr_grp = ""
            extra_tags = []
            for line in res.text.splitlines():
                line = line.strip()
                if line.startswith("#EXTINF"):
                    curr_extinf = line
                    m = RE_GROUP_TITLE.search(line)
                    curr_grp = m.group(1) if m else ""
                    extra_tags = []
                elif line.startswith("#EXTM3U"):
                    m = RE_TVG_URL.search(line)
                    if m:
                        for e in m.group(1).split(','):
                            if e.strip(): self.epg_urls.add(e.strip())
                elif line.startswith("#") and curr_extinf: 
                    extra_tags.append(line)
                elif not line.startswith("#") and line.startswith("http") and curr_extinf:
                    self.add_channel(curr_extinf, line, curr_grp, extra_tags)
                    curr_extinf = ""
        except requests.RequestException as e:
            logger.warning(f"[DIE] Lỗi khi tải nguồn {url}: {e}")
            self.source_status[url] = False

    def _fetch_single_epg(self, epg_url):
        try:
            res = self.session.get(epg_url, timeout=20)
            xml_data = gzip.decompress(res.content) if epg_url.endswith('.gz') else res.content
            root = ET.fromstring(xml_data)
            
            local_ids = set()
            local_mapping = {}
            for elem in root.findall('channel'):
                ch_id = elem.get('id')
                if not ch_id: continue
                local_ids.add(ch_id)
                # Đọc tên kênh trong EPG để hỗ trợ map chính xác
                for dn in elem.findall('display-name'):
                    if dn.text:
                        norm_name = self.normalize_channel_name(dn.text)
                        if norm_name not in local_mapping:
                            local_mapping[norm_name] = ch_id
            return root, local_ids, local_mapping
        except Exception as e:
            logger.error(f"Lỗi xử lý EPG từ {epg_url}: {e}")
            return None

    def fetch_epg_and_map_ids(self):
        if not self.epg_urls: return
        logger.info("Đang tải và xử lý đa luồng EPG đồng thời...")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            results = executor.map(self._fetch_single_epg, list(self.epg_urls))
            
        for res in results:
            if res:
                root, local_ids, local_mapping = res
                self.epg_xml_roots.append(root)
                self.available_xml_ids.update(local_ids)
                self.xml_name_mapping.update(local_mapping)

    # HÀM MỚI: Khớp chính xác 100%, tuyệt đối không dùng difflib đoán bừa
    def get_best_id_match(self, clean_name, orig_id):
        # 1. Nếu có ID chuẩn từ nguồn -> Dùng luôn
        if orig_id in self.available_xml_ids: 
            return orig_id
        # 2. Nếu ID trống, kiểm tra xem tên kênh có nằm gọn trong EPG không
        if clean_name in self.xml_name_mapping: 
            return self.xml_name_mapping[clean_name]
        # 3. Không có thì bỏ qua, trả về rỗng
        return ""

    def run(self):
        if not os.path.exists(SOURCE_FILE):
            logger.error(f"Không tìm thấy file nguồn: {SOURCE_FILE}")
            return
            
        logger.info("Đang xử lý và dọn dẹp các liên kết trong sources.txt...")
        with open(SOURCE_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        unique_urls = []
        for line in lines:
            line = line.strip()
            if not line: continue
            raw_url = line.replace('[DIE]', '').strip()
            if raw_url.startswith("http") and raw_url not in unique_urls:
                unique_urls.append(raw_url)

        with open(SOURCE_FILE, 'w', encoding='utf-8') as f:
            for url in unique_urls:
                status_suffix = " [DIE]" if not self.source_status.get(url, True) else ""
                f.write(f"{url}{status_suffix}\n")

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            executor.map(self.process_source, unique_urls)

        working_links = []
        logger.info("Đang kiểm tra trạng thái các liên kết phát luồng (Stream links)...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(self.check_single_link, d) for d in self.unique_links.values()]
            for f in concurrent.futures.as_completed(futures):
                res = f.result()
                if res: working_links.append(res)
        
        working_links.sort(key=self.get_sort_key)
        self.fetch_epg_and_map_ids()

        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write('#EXTM3U x-tvg-url="https://raw.githubusercontent.com/hoangxg4/mix-iptv/main/light_epg.xml"\n')
            for ch in working_links:
                # Áp dụng logic nối EPG chuẩn
                final_id = self.get_best_id_match(ch['name'], ch['tvg_id'])
                if final_id: 
                    self.final_used_ids.add(final_id)
                
                line = f'#EXTINF:-1 tvg-id="{final_id}" tvg-name="{ch["name"]}" tvg-logo="{ch["tvg_logo"]}" group-title="{ch["group"]}",{ch["name"]}'
                f.write(line + "\n")
                f.write(f"#EXTGRP:{ch['group']}\n")
                for t in ch['extra_tags']: f.write(t + "\n")
                f.write(ch['url'] + "\n")

        if self.final_used_ids:
            logger.info("Đang trích xuất cấu trúc EPG tinh gọn...")
            root_out = ET.Element("tv")
            added_ch = set()
            for root_in in self.epg_xml_roots:
                for elem in root_in.findall('channel'):
                    ch_id = elem.get('id')
                    if ch_id in self.final_used_ids and ch_id not in added_ch:
                        root_out.append(elem)
                        added_ch.add(ch_id)
            for root_in in self.epg_xml_roots:
                for elem in root_in.findall('programme'):
                    if elem.get('channel') in added_ch:
                        root_out.append(elem)
                        
            tree = ET.ElementTree(root_out)
            ET.indent(tree, space="  ", level=0)
            tree.write(OUTPUT_EPG, encoding='utf-8', xml_declaration=True)

        logger.info("Đã khắc phục lỗi EPG và xuất EPG chuẩn thành công!")

if __name__ == "__main__":
    M3UBuilder().run()
