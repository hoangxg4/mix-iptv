import json
import requests
import re

# Tên file đầu ra
OUTPUT_FILE = "playlist.m3u"

def merge_m3u():
    # Mở đầu file m3u
    merged_content = "#EXTM3U\n"
    
    try:
        # Đọc danh sách nguồn từ file json mới
        with open('sources.json', 'r', encoding='utf-8') as f:
            sources = json.load(f)
            
        for source in sources:
            group_name = source.get('name', 'Ungrouped')
            url = source.get('url')
            
            if not url: continue

            try:
                print(f"Dang tai: {group_name} - {url}")
                response = requests.get(url, timeout=15)
                
                if response.status_code == 200:
                    content = response.text
                    lines = content.splitlines()
                    
                    for line in lines:
                        line = line.strip()
                        if not line: continue
                        
                        # Bỏ qua dòng header file #EXTM3U để tránh lặp
                        if line.startswith("#EXTM3U"):
                            continue
                            
                        # Xử lý dòng thông tin kênh #EXTINF
                        if line.startswith("#EXTINF"):
                            # Cách 1: Nếu đã có group-title, thay thế nó
                            if 'group-title="' in line:
                                line = re.sub(r'group-title="[^"]*"', f'group-title="{group_name}"', line)
                            # Cách 2: Nếu chưa có, chèn thêm vào sau #EXTINF:-1
                            else:
                                # Tìm vị trí dấu phẩy đầu tiên (ngăn cách info và tên kênh)
                                comma_index = line.find(',')
                                if comma_index != -1:
                                    # Chèn group-title trước dấu phẩy
                                    line = line[:comma_index] + f' group-title="{group_name}"' + line[comma_index:]
                                else:
                                    # Trường hợp format lạ, cứ nối vào đuôi
                                    line = line + f' group-title="{group_name}"'
                            
                            merged_content += line + "\n"
                            
                            # Thêm dòng #EXTGRP (hỗ trợ Kodi/TiviMate tốt hơn)
                            merged_content += f"#EXTGRP:{group_name}\n"
                        
                        # Bỏ qua dòng #EXTGRP cũ của file gốc (vì mình đã tạo cái mới ở trên)
                        elif line.startswith("#EXTGRP"):
                            continue
                            
                        # Đây là dòng link stream hoặc các metadata khác
                        else:
                            merged_content += line + "\n"
                            
                else:
                    print(f"Lỗi khi tải {url}: Status {response.status_code}")
            except Exception as e:
                print(f"Lỗi kết nối tới {url}: {e}")
                
        # Ghi ra file kết quả
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(merged_content)
            
        print("Đã gộp và phân nhóm file thành công!")
        
    except FileNotFoundError:
        print("Không tìm thấy file sources.json")
    except json.JSONDecodeError:
        print("Lỗi định dạng file sources.json")

if __name__ == "__main__":
    merge_m3u()
