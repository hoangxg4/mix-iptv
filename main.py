import json
import requests
import re

OUTPUT_FILE = "playlist.m3u"

def merge_m3u():
    channels_content = ""
    epg_urls = set()
    
    try:
        with open('sources.json', 'r', encoding='utf-8') as f:
            sources = json.load(f)
            
        for source in sources:
            source_name = source.get('name', 'Other') # Tên Source (ví dụ: FPT, Viettel)
            url = source.get('url')
            
            if not url: continue

            try:
                print(f"Dang tai: {source_name}")
                response = requests.get(url, timeout=15)
                
                if response.status_code == 200:
                    content = response.text
                    lines = content.splitlines()
                    
                    for line in lines:
                        line = line.strip()
                        if not line: continue
                        
                        # 1. LẤY EPG (Giữ nguyên logic cũ)
                        if line.startswith("#EXTM3U"):
                            tvg_match = re.search(r'(?:x-tvg-url|url-tvg)="([^"]*)"', line, re.IGNORECASE)
                            if tvg_match:
                                found_urls = tvg_match.group(1).split(',')
                                for epg in found_urls:
                                    if epg.strip():
                                        epg_urls.add(epg.strip())
                            continue
                        
                        # 2. XỬ LÝ THÔNG TIN KÊNH
                        if line.startswith("#EXTINF"):
                            # --- LOGIC MỚI: GROUP IN GROUP ---
                            
                            # B1: Tìm xem group gốc tên là gì
                            original_group = "No Group" # Mặc định nếu ko có
                            match = re.search(r'group-title="([^"]*)"', line)
                            if match:
                                original_group = match.group(1)
                            
                            # B2: Tạo tên group mới theo dạng: "SOURCE | GROUP CŨ"
                            # Bạn có thể đổi dấu "|" thành "-" hoặc ">>" tùy thích
                            new_group_full = f"{source_name} | {original_group}"
                            
                            # B3: Thay thế vào dòng lệnh
                            if 'group-title="' in line:
                                # Nếu đã có group-title, thay nội dung bên trong
                                line = re.sub(r'group-title="[^"]*"', f'group-title="{new_group_full}"', line)
                            else:
                                # Nếu chưa có, chèn thêm vào
                                comma_index = line.find(',')
                                if comma_index != -1:
                                    line = line[:comma_index] + f' group-title="{new_group_full}"' + line[comma_index:]
                                else:
                                    line = line + f' group-title="{new_group_full}"'
                            
                            channels_content += line + "\n"
                            
                            # Luôn cập nhật #EXTGRP để tương thích tốt nhất
                            channels_content += f"#EXTGRP:{new_group_full}\n"
                        
                        elif line.startswith("#EXTGRP"):
                            # Bỏ qua dòng EXTGRP cũ vì mình đã tự tạo dòng mới ở trên
                            continue
                        else:
                            channels_content += line + "\n"
                            
                else:
                    print(f"Lỗi tải {url}: {response.status_code}")
            except Exception as e:
                print(f"Lỗi kết nối {url}: {e}")

        # 3. GHI FILE
        header = "#EXTM3U"
        if epg_urls:
            combined_epg = ",".join(epg_urls)
            header += f' x-tvg-url="{combined_epg}"'
        
        final_content = header + "\n" + channels_content

        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(final_content)
            
        print(f"Đã gộp thành công! (Chế độ Group in Group)")
        
    except Exception as e:
        print(f"Có lỗi xảy ra: {e}")

if __name__ == "__main__":
    merge_m3u()
