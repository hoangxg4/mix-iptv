import requests
import re
import os

# Cấu hình file
SOURCE_FILE = "sources.txt"
OUTPUT_FILE = "playlist.m3u"

def merge_m3u():
    channels_content = ""
    epg_urls = set()
    
    # Kiểm tra file nguồn tồn tại không
    if not os.path.exists(SOURCE_FILE):
        print(f"Không tìm thấy file {SOURCE_FILE}")
        return

    try:
        # Đọc file text từng dòng
        with open(SOURCE_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        for line in lines:
            line = line.strip()
            # Bỏ qua dòng trống hoặc comment
            if not line or line.startswith("#"):
                continue
                
            # Tách Tên và URL (Hỗ trợ cả dấu phẩy và dấu gạch đứng)
            if "," in line:
                parts = line.split(",", 1)
            elif "|" in line:
                parts = line.split("|", 1)
            else:
                print(f"Bỏ qua dòng sai định dạng: {line}")
                continue
                
            source_name = parts[0].strip()
            url = parts[1].strip()
            
            if not url.startswith("http"):
                continue

            try:
                print(f"Dang tai: {source_name} ...")
                response = requests.get(url, timeout=15)
                
                if response.status_code == 200:
                    content = response.text
                    file_lines = content.splitlines()
                    
                    for f_line in file_lines:
                        f_line = f_line.strip()
                        if not f_line: continue
                        
                        # 1. LẤY EPG
                        if f_line.startswith("#EXTM3U"):
                            tvg_match = re.search(r'(?:x-tvg-url|url-tvg)="([^"]*)"', f_line, re.IGNORECASE)
                            if tvg_match:
                                found_urls = tvg_match.group(1).split(',')
                                for epg in found_urls:
                                    if epg.strip():
                                        epg_urls.add(epg.strip())
                            continue
                        
                        # 2. XỬ LÝ KÊNH VÀ GROUP
                        if f_line.startswith("#EXTINF"):
                            # Lấy group gốc
                            original_group = "Ungrouped"
                            grp_match = re.search(r'group-title="([^"]*)"', f_line)
                            if grp_match:
                                original_group = grp_match.group(1)
                            
                            # Tạo group mới: TÊN NGUỒN | GROUP GỐC
                            new_group = f"{source_name} | {original_group}"
                            
                            # Thay thế hoặc thêm group-title
                            if 'group-title="' in f_line:
                                f_line = re.sub(r'group-title="[^"]*"', f'group-title="{new_group}"', f_line)
                            else:
                                comma_index = f_line.find(',')
                                if comma_index != -1:
                                    f_line = f_line[:comma_index] + f' group-title="{new_group}"' + f_line[comma_index:]
                                else:
                                    f_line = f_line + f' group-title="{new_group}"'
                            
                            channels_content += f_line + "\n"
                            # Thêm #EXTGRP để chắc chắn app nhận diện
                            channels_content += f"#EXTGRP:{new_group}\n"
                        
                        elif f_line.startswith("#EXTGRP"):
                            continue
                        else:
                            channels_content += f_line + "\n"
                else:
                    print(f" -> Lỗi tải: {response.status_code}")
            except Exception as e:
                print(f" -> Lỗi kết nối: {e}")

        # 3. GHI FILE KẾT QUẢ
        header = "#EXTM3U"
        if epg_urls:
            combined_epg = ",".join(epg_urls)
            header += f' x-tvg-url="{combined_epg}"'
        
        final_content = header + "\n" + channels_content

        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(final_content)
            
        print("Đã gộp file thành công!")
        
    except Exception as e:
        print(f"Có lỗi hệ thống: {e}")

if __name__ == "__main__":
    merge_m3u()
