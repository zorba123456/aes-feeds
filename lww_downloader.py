import os
import json
import time
import subprocess
import re
from DrissionPage import ChromiumPage, ChromiumOptions

__version__ = "3.4.2-全量去噪版"

def clean_text_noise(text):
    if not text:
        return ""
    cleaned = text.replace('\ufffd', '').replace('\u0000', '')
    cleaned = re.sub(r'\?{2,}', '', cleaned)
    return re.sub(r'\s+', ' ', cleaned).strip()

def force_kill_edge():
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
        print("✅ 同步成功！LWW 数据已成功推送。")
    except subprocess.CalledProcessError:
        print("ℹ️ 未检测到新文献或同步无变动，跳过推送。")

def main():
    print("=" * 45)
    print(f"🚀 启动 LWW 强攻与提纯管线 [v{__version__}]")
    print("=" * 45)
    
    config = load_config()
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    co = ChromiumOptions()
    co.set_argument('--no-first-run')
    co.set_argument('--disable-gpu')
    
    page = ChromiumPage(co)
    updated_any = False
    
    for target in config.get("targets", []):
        name = target.get("name")
        rss_url = target.get("rss_url")
        output_filename = target.get("output_file")
        output_path = os.path.abspath(os.path.join(current_dir, "..", "aes-feeds", output_filename))
        
        print(f"\n📡 正在抓取: {name}")
        try:
            page.get(rss_url)
            time.sleep(3)
            
            raw_xml = page.html
            if "<rss" in raw_xml or "<channel" in raw_xml:
                # 强力剥离页面框架，提纯纯净 XML
                start_idx = raw_xml.find("<rss")
                if start_idx == -1:
                    start_idx = raw_xml.find("<feed")
                
                if start_idx != -1:
                    pure_xml = raw_xml[start_idx:]
                    end_idx = pure_xml.rfind("</rss>")
                    if end_idx != -1:
                        pure_xml = pure_xml[:end_idx+6]
                    else:
                        end_feed = pure_xml.rfind("</feed>")
                        if end_feed != -1:
                            pure_xml = pure_xml[:end_feed+7]

                    # 强力去噪，抹除标题和描述中的潜在乱码
                    pure_xml = clean_text_noise(pure_xml)

                    # 动态时间戳和期号校准补全逻辑
                    issue_info = "Ahead-of-Print"
                    try:
                        if "current" in output_filename:
                            page.get(rss_url.replace("currentissue.xml", ""))
                            time.sleep(2)
                            h2_text = page.ele("css:.wp-current-issue-volume").text
                            if h2_text:
                                issue_info = re.sub(r'\s+', ' ', h2_text).strip()
                    except Exception:
                        pass
                    
                    pub_date_str = time.strftime('%a, %d %b %Y %H:%M:%S GMT')
                    items = re.findall(r'<item>.*?</item>', pure_xml, re.DOTALL)
                    for item in items:
                        new_item = item
                        if "<pubDate>" not in item:
                            new_item = new_item.replace("</link>", f"</link>\n            <pubDate>{pub_date_str}</pubDate>")
                        
                        desc_match = re.search(r'<description>(.*?)</description>', item, re.DOTALL)
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
                with open(output_path, 'w', encoding='utf-8