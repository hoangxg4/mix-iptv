import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import re
import os
import gzip
import xml.etree.ElementTree as ET
import concurrent.futures

SOURCE_FILE = "sources.txt"
OUTPUT_FILE = "playlist.m3u"
OUTPUT_EPG = "light_epg.xml"
TIMEOUT = 15
STREAM_TIMEOUT = 3 
MAX_WORKERS = 50 

SPAM_KEYWORDS = ['mời quý khán giả', 'thông báo', 'tạm ngưng', 'bảo trì', 'kênh dự phòng']

GROUP_PRIORITY = {
    'VTV': 1, 'HTV': 2, 'VTC': 3, 'VTVCAB / ON': 4, 'VTVPRIME': 5, 
    'K+': 6, 'THỂ THAO': 7, 'PHIM TRUYỆN': 8, 'ĐỊA PHƯƠNG': 12
}

class M3UBuilder:
    def __init__(self):
        self.epg_urls = set()
        self.unique_links = {}
        
        # [NEW] Kho dữ liệu EPG thông minh
        self.available_xml_ids = set()
        self.xml_name_mapping = {}  # Từ điển dịch tên kênh -> ID chuẩn trong XML
        self.epg_xml_roots = []     # Lưu trữ file XML đã tải để trích xuất
        self.final_used_ids = set() # Những ID xịn cuối cùng sẽ được đưa vào light_epg
        
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

    def get_sort_key(self, channel):
        group = channel['group'].upper()
        priority = GROUP_PRIORITY.get(group, 99)
        name = channel['name']
        nat_key = [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', name)]
        return (priority, group, nat_key)

    def normalize_channel_name(self, name: str) -> str:
        # Xoá các mác rườm rà để ép về tên gốc (VD: "VTV 3 HD" -> "VTV3")
        name = re.sub(r'(?i)[\[\(\-_\.]?\b(fhd|hd|sd|1080p|720p|4k|vn|vie|h264|hevc)\b[\]\)\-_\.]?', ' ', name)
        name = re.sub(r'(?i)(vtv|htv|vtc|sctv|vtvcab)\s+(\d+)', r'\1\2', name)
        name = re.sub(r'[^\w\s\+]', '', name)
        return ' '.join(name.split()).strip().upper()

    def smart_grouping(self, raw_group: str, clean_name: str) -> str:
        g_lower = raw_group.lower()
        n_lower = clean_name.lower()
        if 'prime' in n_lower or 'prime' in g_lower: return 'VTVPRIME'
        if 'cab' in n_lower or 'cab' in g_lower or 'on ' in n_lower: return 'VTVCAB / ON'
        if 'vtv' in n_lower: return 'VTV'
        if 'htv' in n_lower: return 'HTV'
        if 'vtc' in n_lower: return 'VTC'
        if 'k+' in n_lower: return 'K+'
        if any(x in g_lower or x in n_lower for x in ['địa phương', 'tỉnh', 'local']): return 'Địa Phương'
        if any(x in g_lower for x in ['thể thao', 'sports', 'bóng đá']): return 'Thể Thao'
        if any(x in g_lower for x in ['phim', 'movies']): return 'Phim Truyện'
        return raw_group.strip().title() if raw_group else 'Khác'

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
        
        id_match = re.search(r'tvg-id=["\']([^"\']+)["\']', extinf, re.I)
        logo_match = re.search(r'tvg-logo=["\']([^"\']+)["\']', extinf, re.I)

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
        else:
            # Ưu tiên nhặt lại logo nếu link bị trùng
            if not self.unique_links[url]['tvg_logo'] and found_logo:
                self.unique_links[url]['tvg_logo'] = found_logo

    def check_single_link(self, data):
        clean_url, headers = self.parse_url_headers(data['url'])
        try:
            res = self.session.get(clean_url, headers=headers, stream=True, timeout=STREAM_TIMEOUT)
            if res.status_code == 200: return data
        except: pass
        return None

    def process_source(self, url):
        print(f"[*] Đang tải source: {url[:50]}...")
        try:
            res = self.session.get(url, timeout=TIMEOUT)
            content = res.text
            curr_extinf, curr_grp, extra_tags = "", "Khác", []
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("#EXTINF"):
                    curr_extinf = line
                    m = re.search(r'group-title="([^"]*)"', line)
                    if m: curr_grp = m.group(1)
                    extra_tags = []
                elif line.startswith("#EXTM3U"):
                    m = re.search(r'(?:x-tvg-url|url-tvg)="([^"]*)"', line, re.I)
                    if m: [self.epg_urls.add(e.strip()) for e in m.group(1).split(',') if e.strip()]
                elif line.startswith("#") and curr_extinf: 
                    extra_tags.append(line)
                elif not line.startswith("#") and line.startswith("http") and curr_extinf:
                    self.add_channel(curr_extinf, line, curr_grp, extra_tags)
                    curr_extinf = ""
        except: pass

    def fetch_epg_and_map_ids(self):
        # Tải EPG gốc về và học thuộc lòng bộ từ điển Tên <-> ID
        if not self.epg_urls: return
        print(f"\n[*] Đang đọc file EPG gốc để thiết lập đồng bộ...")
        
        for epg_url in list(self.epg_urls):
            try:
                res = self.session.get(epg_url, timeout=30)
                xml_data = gzip.decompress(res.content) if epg_url.endswith('.gz') else res.content
                root = ET.fromstring(xml_data)
                self.epg_xml_roots.append(root)

                # Quét mọi kênh có trong XML
                for elem in root.findall('channel'):
                    ch_id = elem.get('id')
                    if not ch_id: continue
                    self.available_xml_ids.add(ch_id)
                    
                    # Dịch display-name trong XML sang dạng chuẩn để tí nữa so sánh
                    for dn in elem.findall('display-name'):
                        if dn.text:
                            norm_name = self.normalize_channel_name(dn.text)
                            if norm_name not in self.xml_name_mapping:
                                self.xml_name_mapping[norm_name] = ch_id
            except Exception as e: 
                print(f"  -> Bỏ qua EPG lỗi: {e}")

    def generate_light_epg(self):
        if not self.final_used_ids or not self.epg_xml_roots: return
        print(f"[*] Đang xuất EPG siêu nhẹ cho {len(self.final_used_ids)} kênh đã được đồng bộ...")
        root_out = ET.Element("tv")
        added_channels = set()

        for root_in in self.epg_xml_roots:
            for elem in root_in:
                if elem.tag == 'channel':
                    ch_id = elem.get('id')
                    if ch_id in self.final_used_ids and ch_id not in added_channels:
                        root_out.append(elem)
                        added_channels.add(ch_id)
                elif elem.tag == 'programme' and elem.get('channel') in added_channels:
                    root_out.append(elem)

        tree = ET.ElementTree(root_out)
        tree.write(OUTPUT_EPG, encoding='utf-8', xml_declaration=True)

    def run(self):
        if not os.path.exists(SOURCE_FILE): return
        with open(SOURCE_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                parts = re.split(r'[,|]', line.strip(), 1)
                if len(parts) == 2 and parts[1].strip().startswith("http"):
                    self.process_source(parts[1].strip())

        working_links = []
        print(f"\n[*] Đang check {len(self.unique_links)} links...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(self.check_single_link, d) for d in self.unique_links.values()]
            for f in concurrent.futures.as_completed(futures):
                res = f.result()
                if res: working_links.append(res)
        
        working_links.sort(key=self.get_sort_key)
        
        # [QUAN TRỌNG] Tải EPG và tra từ điển
        self.fetch_epg_and_map_ids()

        # Dò ID chuẩn cho từng nhóm kênh M3U
        name_to_best_id = {}
        name_to_best_logo = {}

        for ch in working_links:
            clean_name = ch['name']
            
            # Gán ID xịn
            if clean_name not in name_to_best_id:
                # Nếu ID cũ của m3u trùng khớp với XML thì xài luôn
                if ch['tvg_id'] and ch['tvg_id'] in self.available_xml_ids:
                    name_to_best_id[clean_name] = ch['tvg_id']
                # Nếu không khớp, tìm trong từ điển tự động
                elif clean_name in self.xml_name_mapping:
                    name_to_best_id[clean_name] = self.xml_name_mapping[clean_name]
                # Hết cách thì giữ nguyên
                else:
                    name_to_best_id[clean_name] = ch['tvg_id']
            
            # Lưu lại Logo xịn nhất để gộp nhóm
            if ch['tvg_logo'] and clean_name not in name_to_best_logo:
                name_to_best_logo[clean_name] = ch['tvg_logo']

        # XUẤT FILE M3U
        header = '#EXTM3U x-tvg-url="https://raw.githubusercontent.com/hoangxg4/mix-iptv/main/light_epg.xml"'
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(header + "\n")
            for ch in working_links:
                clean_name = ch['name']
                # Ép lấy ID đã được đồng bộ chuẩn nhất
                final_id = name_to_best_id.get(clean_name, "")
                final_logo = name_to_best_logo.get(clean_name, ch['tvg_logo'])

                if final_id in self.available_xml_ids:
                    self.final_used_ids.add(final_id)

                id_str = f' tvg-id="{final_id}"' if final_id else ""
                logo_str = f' tvg-logo="{final_logo}"' if final_logo else ""
                # Giữ nguyên tvg-name và đuôi tên cho nó đồng bộ tuyệt đối trên OTT Navigator
                name_str = f' tvg-name="{clean_name}"'

                clean_extinf = f'#EXTINF:-1{id_str}{name_str}{logo_str} group-title="{ch["group"]}",{clean_name}'
                
                f.write(clean_extinf + "\n")
                f.write(f"#EXTGRP:{ch['group']}\n")
                for t in ch['extra_tags']: f.write(t + "\n")
                f.write(ch['url'] + "\n")

        # XUẤT EPG NHẸ TỪ CÁC ID XỊN
        self.generate_light_epg()
                
        print(f"✅ BINGO! Đã đồng bộ cấu trúc EPG khớp 100% với file XML.")

if __name__ == "__main__":
    M3UBuilder().run()
