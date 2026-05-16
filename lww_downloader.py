import os
import json
import time
import subprocess
import re
from DrissionPage import ChromiumPage, ChromiumOptions

# 全局版本号升级为 3.2.0，切入 Edge 物理隔离架构
__version__ = "3.2.0-Edge隔离提纯版"

def load_config():
    with open('config.json', 'r', encoding='utf-8') as f:
        return json.load(f)

def push_to_github():
    print("\n📤 启动 GitHub 自动同步...")
    try:
        subprocess.run(["git", "add", "*.xml"], check=True)
        commit_msg = f"Auto-update feeds: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        subprocess.run(["git", "commit", "-m", commit_msg], check=True)
        subprocess.run(["git", "push"], check=True)
        print("✅ 同步成功！数据已推送至 GitHub Pages。")
    except subprocess.CalledProcessError as e:
        print("ℹ️ 未检测到新文献或同步无变动，跳过推送。")

def download_403_feeds():
    config = load_config()
    
    print("=" * 45)
    print(f"🚀 启动 LWW 强攻与回输管线 [v{__version__}]")
    print("=" * 45)
    
    co = ChromiumOptions()
    
    # 【物理隔离核心配置】：强行指定接管 macOS 下的 Microsoft Edge
    edge_path = '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'
    co.set_browser_path(edge_path)
    
    co.headless(False) 
    co.set_argument('--no-sandbox')    
    co.set_argument('--disable-gpu')   
    co.set_local_port(9666) # 挂载独立专属自动化端口          
    
    try:
        page = ChromiumPage(co)
    except Exception as e:
        print(f"💥 Edge 内核启动失败！请检查 Edge 是否正确安装。详情: {e}")
        return

    output_dir = './' 
    journals = config.get('lww_journals', [])
    updated_any = False 
    
    for journal in journals:
        name = journal['name']
        url = journal['url']
        output_path = os.path.join(output_dir, f"{name}.xml")
        
        print(f"\n📡 正在抓取: {name}")
        
        try:
            page.get(url)
            success = False
            
            for attempt in range(2):
                if success: break
                if attempt == 1:
                    page.refresh()
                    time.sleep(2) 
                
                for i in range(20):
                    raw_xml = page.html
                    if any(tag in raw_xml.lower() for tag in ["<rss", "<feed", "<?xml", "cdata"]):
                        success = True
                        break
                    
                    try:
                        cf_frame = page.get_frame('@src^https://challenges.cloudflare.com', timeout=0.5)
                        if cf_frame:
                            box = cf_frame.ele('.mark', timeout=0.5) or cf_frame.ele('t:label', timeout=0.5)
                            if box: box.click()
                    except: pass
                    time.sleep(1) 
            
            if success:
                # 暴力剥离浏览器强加的 HTML 网页外壳，提取纯净 XML
                match = re.search(r'(<\?xml|<rss|<feed)', raw_xml, re.IGNORECASE)
                if match:
                    raw_xml = raw_xml[match.start():] 
                    raw_xml = re.sub(r'</body>\s*</html>\s*$', '', raw_xml, flags=re.IGNORECASE)
                
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(raw_xml)
                print(f"✅ 成功提纯并存盘: {output_path}")
                updated_any = True
            else:
                print(f"❌ 抓取失败。")
                
        except Exception as e:
            print(f"⚠️ 异常: {e}")
            
    page.quit()
    
    if updated_any:
        push_to_github()
    
    print("\n" + "=" * 45)

if __name__ == "__main__":
    download_403_feeds()