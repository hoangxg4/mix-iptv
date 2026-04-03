import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import re
import os
import html
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
        self.required_tvg_ids = set()
        
        # [NEW] Bộ nhớ siêu việt để theo dõi EPG
        self.name_to_tvg_ids = {}   # Gom tất cả ID của 1 kênh
        self.id_to_metadata = {}    # Lưu tvg-name và tvg-logo xịn của ID đó
        self.fallback_metadata = {} # Lưu dự phòng nếu kênh ko có ID
        self.valid_ids_found = set()# ID nào có EPG thật mới được lưu vào đây
        
        self.unique_links = {}  
        
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
        
        # Nhặt lại các thẻ quan trọng nguyên bản
        id_match = re.search(r'tvg-id=["\']([^"\']+)["\']', extinf, re.I)
        name_match = re.search(r'tvg-name=["\']([^"\']+)["\']', extinf, re.I)
        logo_match = re.search(r'tvg-logo=["\']([^"\']+)["\']', extinf, re.I)

        found_id = id_match.group(1).strip() if id_match and id_match.group(1).strip() else ""
        # Nếu ko có tvg-name, lấy luôn tên chưa cắt (vd: VTV3 HD) làm tvg-name để app nhận diện
        found_name = name_match.group(1).strip() if name_match and name_match.group(1).strip() else raw_name
        found_logo = logo_match.group(1).strip() if logo_match and logo_match.group(1).strip() else ""

        if clean_name not in self.name_to_tvg_ids:
            self.name_to_tvg_ids[clean_name] = set()

        if found_id:
            self.name_to_tvg_ids[clean_name].add(found_id)
            self.required_tvg_ids.add(found_id)
            if found_id not in self.id_to_metadata:
                self.id_to_metadata[found_id] = {'tvg-name': found_name, 'tvg-logo': found_logo}
            elif not self.id_to_metadata[found_id]['tvg-logo'] and found_logo:
                self.id_to_metadata[found_id]['tvg-logo'] = found_logo

        if clean_name not in self.fallback_metadata:
            self.fallback_metadata[clean_name] = {'tvg-name': found_name, 'tvg-logo': found_logo}
        elif not self.fallback_metadata[clean_name]['tvg-logo'] and found_logo:
            self.fallback_metadata[clean_name]['tvg-logo'] = found_logo

        if url not in self.unique_links:
            self.unique_links[url] = {
                'url': url,
                'name': clean_name,
                'group': self.smart_grouping(raw_group, clean_name),
                'extra_tags': extra_tags
            }

    def check_single_link(self, data):
        clean_url, headers = self.parse_url_headers(data['url'])
        try:
            res = self.session.get(clean_url, headers=headers, stream=True, timeout=STREAM_TIMEOUT)
            if res.status_code == 200: return data
        except: pass
        return None

    def process_source(self, url):
        print(f"[*] Đang tải: {url[:50]}...")
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

    def generate_light_epg(self):
        if not self.required_tvg_ids or not self.epg_urls: return
        print(f"\n[*] Đang đào EPG... (Cần tìm {len(self.required_tvg_ids)} mã ID)", flush=True)
        root_out = ET.Element("tv")
        fully_found_ids = set()

        for epg_url in list(self.epg_urls):
            if len(fully_found_ids) >= len(self.required_tvg_ids): break
            try:
                res = self.session.get(epg_url, timeout=30)
                xml_data = gzip.decompress(res.content) if epg_url.endswith('.gz') else res.content
                root_in = ET.fromstring(xml_data)
                active_ids = set()
                
                for elem in root_in:
                    if elem.tag == 'channel':
                        ch_id = elem.get('id')
                        if ch_id in self.required_tvg_ids and ch_id not in fully_found_ids:
                            root_out.append(elem)
                            active_ids.add(ch_id)
                            fully_found_ids.add(ch_id)
                            self.valid_ids_found.add(ch_id) # Chỉ ID nào trúng tuyển mới lưu
                    elif elem.tag == 'programme' and elem.get('channel') in active_ids:
                        root_out.append(elem)
            except: pass

        tree = ET.ElementTree(root_out)
        tree.write(OUTPUT_EPG, encoding='utf-8', xml_declaration=True)

    def run(self):
        if not os.path.exists(SOURCE_FILE): return
        with open(SOURCE_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                parts = re.split(r'[,|]', line.strip(), 1)
                if len(parts) == 2 and parts[1].strip().startswith("http"):
                    self.process_source(parts[1].strip())

        working = []
        print(f"\n[*] Đang check sống/chết {len(self.unique_links)} links...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(self.check_single_link, d) for d in self.unique_links.values()]
            for f in concurrent.futures.as_completed(futures):
                res = f.result()
                if res: working.append(res)
        
        working.sort(key=self.get_sort_key)
        
        # Chạy tạo EPG TRƯỚC khi ghi file M3U để biết ID nào sống, ID nào chết
        self.generate_light_epg()
        
        # [QUAN TRỌNG] Trọng tài chọn ID xịn nhất cho từng đài
        best_tvg_id_for_name = {}
        for name, ids in self.name_to_tvg_ids.items():
            valid_ids = ids.intersection(self.valid_ids_found)
            if valid_ids: best_tvg_id_for_name[name] = list(valid_ids)[0]
            elif ids: best_tvg_id_for_name[name] = list(ids)[0]

        # XUẤT FILE M3U
        header = '#EXTM3U x-tvg-url="https://raw.githubusercontent.com/hoangxg4/mix-iptv/main/light_epg.xml"'
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(header + "\n")
            for ch in working:
                clean_name = ch['name']
                best_id = best_tvg_id_for_name.get(clean_name, "")
                
                # Truy xuất lại tên gốc và logo gốc theo đúng ID
                if best_id: meta = self.id_to_metadata.get(best_id, {})
                else: meta = self.fallback_metadata.get(clean_name, {})

                tvg_name = meta.get('tvg-name', clean_name)
                tvg_logo = meta.get('tvg-logo', '')

                logo_str = f' tvg-logo="{tvg_logo}"' if tvg_logo else ""
                id_str = f' tvg-id="{best_id}"' if best_id else ""
                name_str = f' tvg-name="{tvg_name}"' if tvg_name else f' tvg-name="{clean_name}"'
                
                # Nhóm lại dưới 1 cái tên duy nhất: {clean_name} ở đuôi
                clean_extinf = f'#EXTINF:-1{id_str}{name_str}{logo_str} group-title="{ch["group"]}",{clean_name}'
                
                f.write(clean_extinf + "\n")
                f.write(f"#EXTGRP:{ch['group']}\n")
                for t in ch['extra_tags']: f.write(t + "\n")
                f.write(ch['url'] + "\n")
                
        print(f"✅ Xong! Đã dọn sạch dẹp gọn và khôi phục EPG cho các kênh!")

if __name__ == "__main__":
    M3UBuilder().run()
