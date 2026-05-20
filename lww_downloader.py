import os
import json
import time
import subprocess
import re
from DrissionPage import ChromiumPage, ChromiumOptions

__version__ = "3.4.1-全量正式版"

def force_kill_edge():
    """清理 Edge 残流进程"""
    print("🧹 正在执行环境大扫除 (强杀 Edge 残留进程)...")
    try:
        subprocess.run(['killall', '-9', 'Microsoft Edge'], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        time.sleep(1) 
    except Exception:
        pass

def load_config():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(current_dir, 'config.json')
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def push_to_github():
    print("\n📤 启动 GitHub 自动同步 (LWW Feeds)...")
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    try:
        subprocess.run(["git", "add", "*.xml"], cwd=current_dir, check=True)
        commit_msg = f"Auto-update LWW feeds: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=current_dir, check=True)
        subprocess.run(["git", "push"], cwd=current_dir, check=True)
        print("✅ 同步成功！LWW 数据已成功推送至 aes-feeds 独立仓库。")
    except subprocess.CalledProcessError:
        print("ℹ️ 未检测到新文献或同步无变动，跳过推送。")

def download_403_feeds():
    config = load_config()
    
    print("=" * 45)
    print(f"🚀 启动 LWW 强攻与提纯管线 [v{__version__}]")
    print("=" * 45)
    
    co = ChromiumOptions()
    edge_path = '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'
    co.set_browser_path(edge_path)
    
    co.headless(False) 
    co.set_argument('--no-sandbox')    
    co.set_argument('--disable-gpu')   
    co.set_local_port(9666) 
    
    try:
        page = ChromiumPage(co)
    except Exception as e:
        print(f"💥 Edge 内核启动失败！详情: {e}")
        return

    output_dir = os.path.dirname(os.path.abspath(__file__))
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
                start_match = re.search(r'(<rss|<feed)', raw_xml, re.IGNORECASE)
                if start_match:
                    start_idx = start_match.start()
                    
                    raw_lower = raw_xml.lower()
                    end_rss = raw_lower.rfind('</rss>')
                    end_feed = raw_lower.rfind('</feed>')
                    
                    if end_rss != -1:
                        end_idx = end_rss + 6  
                    elif end_feed != -1:
                        end_idx = end_feed + 7 
                    else:
                        end_idx = len(raw_xml)
                        
                    pure_xml = raw_xml[start_idx:end_idx]
                    pure_xml = re.sub(r'<\?xml.*?\?>', '', pure_xml, flags=re.IGNORECASE).strip()

                    items = re.findall(r'<item>.*?</item>', pure_xml, re.DOTALL)
                    for item in items:
                        new_item = item
                        
                        # 提取 citation
                        citation_match = re.search(r'<citation><!\[CDATA\[(.*?)\]\]></citation>', new_item, re.DOTALL)
                        if not citation_match:
                            citation_match = re.search(r'<citation>(.*?)</citation>', new_item, re.DOTALL)
                            
                        issue_info = "最新优先发表"
                        if citation_match:
                            citation_text = citation_match.group(1)
                            issue_match = re.search(r'(\d+\s*\([^)]+\)[^.]*)', citation_text)
                            if issue_match:
                                issue_info = issue_match.group(1).strip()
                            else:
                                issue_info = citation_text.split('doi:')[0].strip()

                        # 提取 pubDate
                        pub_date_match = re.search(r'<pubDate>(.*?)</pubDate>', new_item)
                        pub_date_str = pub_date_match.group(1) if pub_date_match else "未知时间"

                        # 注入 description
                        desc_match = re.search(r'<description>(.*?)</description>', new_item, re.DOTALL)
                        if desc_match:
                            original_desc = desc_match.group(1)
                            if original_desc.startswith('<![CDATA[') and original_desc.endswith(']]>'):
                                inner_desc = original_desc[9:-3]
                                new_desc = f"<![CDATA[<b>所属期数:</b> {issue_info}<br><b>出版时间:</b> {pub_date_str}<br><br>{inner_desc}]]>"
                            else:
                                new_desc = f"<![CDATA[<b>所属期数:</b> {issue_info}<br><b>出版时间:</b> {pub_date_str}<br><br>{original_desc}]]>"
                            
                            new_item = new_item.replace(f"<description>{original_desc}</description>", f"<description>{new_desc}</description>")
                            pure_xml = pure_xml.replace(item, new_item)

                    raw_xml = '<?xml version="1.0" encoding="utf-8"?>\n' + pure_xml
                
                raw_xml = raw_xml.replace('\u2028', '\n').replace('\u2029', '\n')
                
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(raw_xml)
                print(f"✅ 成功完美提纯存盘: {output_path}")
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
    force_kill_edge()
    download_403_feeds()
    force_kill_edge()