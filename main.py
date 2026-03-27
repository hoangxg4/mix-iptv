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
STREAM_TIMEOUT = 3 # Khắt khe hơn: Quá 3s không phản hồi = loại bỏ vì sẽ gây lag TV
MAX_WORKERS = 50 # Tăng số luồng lên 50 để vắt kiệt sức mạnh mạng của GitHub Actions

class M3UBuilder:
    def __init__(self):
        self.epg_urls = set()
        self.required_tvg_ids = set()
        self.unique_links = {}  # Đổi sang lọc theo URL: { 'http...': {'extinf':.., 'extgrp':..} }
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
        # 1. Bỏ qua các kênh có tên rác
        name_part = extinf.split(',')[-1].strip()
        if len(name_part) < 2 or re.search(r'[-=_*.]{3,}', name_part):
            return

        # 2. Thu thập tvg-id để làm EPG
        id_match = re.search(r'tvg-id=["\']([^"\']+)["\']', extinf)
        if id_match:
            self.required_tvg_ids.add(id_match.group(1))

        # 3. Lọc trùng lặp bằng URL (Giữ lại thông tin của link xuất hiện đầu tiên)
        if url not in self.unique_links:
            self.unique_links[url] = {
                'extinf': extinf,
                'extgrp': extgrp,
                'url': url
            }

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

    def check_single_link(self, data):
        """Hàm công nhân: Nhận 1 link, ping xem sống hay chết"""
        try:
            # Dùng stream=True để chỉ lấy header, không kéo video stream về gây tắc nghẽn
            res = self.session.get(data['url'], stream=True, timeout=STREAM_TIMEOUT)
            if res.status_code == 200:
                return data
        except:
            pass
        return None

    def fast_health_check(self):
        print(f"\n[*] Đã gom được {len(self.unique_links)} link UNIQUE. Bắt đầu check LIVE/DEAD...")
        print(f"[*] Đang chạy đa luồng ({MAX_WORKERS} workers) với timeout {STREAM_TIMEOUT}s...")

        # Sử dụng ThreadPoolExecutor để chạy song song 50 link cùng lúc
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # Map dữ liệu vào các luồng
            futures = [executor.submit(self.check_single_link, data) for url, data in self.unique_links.items()]
            
            # Quét kết quả trả về
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result:
                    self.final_channels.append(result)

        print(f"✅ Hoàn tất check! Số link SỐNG và cực MƯỢT giữ lại: {len(self.final_channels)}")

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

        # Check sức khỏe các link (Xử lý song song)
        self.fast_health_check()
        
        # Cắt tỉa EPG
        self.generate_light_epg()
        
        # Ghi file M3U cuối cùng
        header = '#EXTM3U x-tvg-url="https://raw.githubusercontent.com/hoangxg4/mix-iptv/main/light_epg.xml"'
        
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(header + "\n")
            for ch in self.final_channels:
                f.write(ch['extinf'] + "\n")
                f.write(ch['extgrp'] + "\n")
                f.write(ch['url'] + "\n")
            
        print("\n✅ Build thành công! Sẵn sàng push lên GitHub.")

if __name__ == "__main__":
    M3UBuilder().run()
