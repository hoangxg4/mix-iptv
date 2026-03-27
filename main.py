import requests
import re
import os
import html
import gzip
import xml.etree.ElementTree as ET
import concurrent.futures

# Cấu hình
SOURCE_FILE = "sources.txt"
OUTPUT_FILE = "playlist.m3u"
OUTPUT_EPG = "light_epg.xml"
TIMEOUT = 15
STREAM_TIMEOUT = 4 # Chỉ cho phép 4s để check link sống/chết
MAX_WORKERS = 30 # Chạy 30 luồng song song cho nhanh

class M3UBuilder:
    def __init__(self):
        self.epg_urls = set()
        self.required_tvg_ids = set()
        self.channels = {}  # { 'tenkenh': [{'extinf':.., 'extgrp':.., 'url':..}, ...] }
        self.final_channels = []
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})

    def clean_html(self, content: str) -> str:
        if any(tag in content.lower() for tag in ["<html", "<body", "<br"]):
            content = re.sub(r'<(br|p|div)\s*/?>', '\n', content, flags=re.IGNORECASE)
            content = re.sub(r'</(p|div)>', '\n', content, flags=re.IGNORECASE)
            content = re.sub(r'<[^>]+>', '', content)
            return html.unescape(content)
        return content

    def add_channel(self, extinf: str, extgrp: str, url: str):
        name_part = extinf.split(',')[-1].strip()
        norm_name = re.sub(r'[^a-zA-Z0-9]', '', name_part.lower())
        
        if len(norm_name) < 2 or re.search(r'[-=_*.]{3,}', name_part):
            return

        id_match = re.search(r'tvg-id=["\']([^"\']+)["\']', extinf)
        if id_match:
            self.required_tvg_ids.add(id_match.group(1))

        if norm_name not in self.channels:
            self.channels[norm_name] = []
        
        # Gom tất cả link trùng tên vào một mảng
        self.channels[norm_name].append({
            'extinf': extinf,
            'extgrp': extgrp,
            'url': url,
            'name': name_part
        })

    def process_url(self, source_name: str, url: str):
        print(f"[*] Đang tải danh sách từ: {source_name} ...")
        try:
            res = self.session.get(url, timeout=TIMEOUT)
            res.raise_for_status()
            content = self.clean_html(res.text)
            
            curr_extinf = ""
            curr_extgrp = ""

            for line in content.splitlines():
                line = line.strip()
                if not line: continue

                if line.startswith("#EXTM3U"):
                    tvg_match = re.search(r'(?:x-tvg-url|url-tvg)="([^"]*)"', line, re.IGNORECASE)
                    if tvg_match:
                        for epg in tvg_match.group(1).split(','):
                            if epg.strip(): self.epg_urls.add(epg.strip())
                    continue

                if line.startswith("#EXTINF"):
                    grp_match = re.search(r'group-title="([^"]*)"', line)
                    orig_group = grp_match.group(1) if grp_match else "Ungrouped"
                    new_group = f"{source_name} | {orig_group}"

                    if grp_match:
                        curr_extinf = re.sub(r'group-title="[^"]*"', f'group-title="{new_group}"', line)
                    else:
                        curr_extinf = line.replace("#EXTINF:", f'#EXTINF:-1 group-title="{new_group}",', 1)
                    curr_extgrp = f"#EXTGRP:{new_group}"
                
                elif line.startswith("#EXTGRP"):
                    continue
                    
                elif not line.startswith("#"):
                    if line.startswith(("http", "rtmp")) and curr_extinf:
                        self.add_channel(curr_extinf, curr_extgrp, line)
                        curr_extinf = ""
                        curr_extgrp = ""

        except Exception as e:
            print(f"  [!] Lỗi: {e}")

    def is_link_alive(self, url: str) -> bool:
        """Kiểm tra nhanh xem link có sống không (Không tải video)"""
        try:
            # stream=True giúp chỉ lấy header, không tải body video gây treo máy
            res = self.session.get(url, stream=True, timeout=STREAM_TIMEOUT)
            return res.status_code == 200
        except:
            return False

    def deduplicate_and_check_health(self):
        print(f"\n[*] Bắt đầu kiểm tra SỐNG/CHẾT cho {len(self.channels)} kênh...")
        print(f"[*] Đang chạy đa luồng ({MAX_WORKERS} workers) - Sẽ mất vài phút...")

        def process_channel(norm_name, channel_links):
            # Duyệt qua các link của kênh này, thấy link nào sống thì chốt luôn
            for data in channel_links:
                if self.is_link_alive(data['url']):
                    return data
            # Nếu xui quá tất cả đều chết, đành lấy tạm link đầu tiên
            return channel_links[0]

        # Chạy kiểm tra song song để tiết kiệm thời gian
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(process_channel, name, links) for name, links in self.channels.items()]
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result:
                    self.final_channels.append(result)

        print(f"✅ Lọc xong! Danh sách cuối cùng có {len(self.final_channels)} kênh.")

    def generate_light_epg(self):
        if not self.required_tvg_ids or not self.epg_urls: return None

        print(f"\n[*] Đang quét toàn bộ {len(self.epg_urls)} link EPG để tìm dữ liệu...")
        root_out = ET.Element("tv")
        fully_found_ids = set()

        for epg_url in list(self.epg_urls):
            if len(fully_found_ids) >= len(self.required_tvg_ids):
                print("  -> Đã tìm đủ EPG, dừng quét sớm!")
                break
                
            print(f"  -> Quét: {epg_url}")
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
                    elif elem.tag == 'programme' and elem.get('channel') in active_ids:
                        root_out.append(elem)
            except:
                pass

        tree = ET.ElementTree(root_out)
        tree.write(OUTPUT_EPG, encoding='utf-8', xml_declaration=True)
        return OUTPUT_EPG

    def run(self):
        if not os.path.exists(SOURCE_FILE): return

        with open(SOURCE_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                parts = re.split(r'[,|]', line.strip(), 1)
                if len(parts) == 2 and parts[1].strip().startswith("http"):
                    self.process_url(parts[0].strip(), parts[1].strip())

        # Gọi hàm lọc link sống/chết
        self.deduplicate_and_check_health()
        
        # Gọi hàm xử lý EPG
        self.generate_light_epg()
        
        # Ghi M3U
        header = '#EXTM3U x-tvg-url="https://raw.githubusercontent.com/hoangxg4/mix-iptv/main/light_epg.xml"'
        
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(header + "\n")
            for ch in self.final_channels:
                f.write(ch['extinf'] + "\n")
                f.write(ch['extgrp'] + "\n")
                f.write(ch['url'] + "\n")
            
        print("\n✅ Hoàn tất toàn bộ quy trình!")

if __name__ == "__main__":
    M3UBuilder().run()
