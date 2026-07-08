#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
Project: lit_auto_pipeline (aes-intel platform)
File: aes-feeds/lww_downloader.py
Version: V4.2.2 (原装 URL 物理恢复版)
Description:
    1. 彻底弃用无效的 oai 测试路径，100% 物理回滚至 config.json 中的原装 OAKS.Journals 官方接口。
    2. 移除 headless 隐身模式，开启可视窗进行强攻，让你亲眼确认不再是 500 报错页。
    3. 保留 9222 端口通信保护，防止在 Shell 调度中卡死。
=============================================================================
"""

import os
import time
import subprocess
import re
from urllib.parse import urljoin
from email.utils import formatdate
from DrissionPage import ChromiumPage, ChromiumOptions
from bs4 import BeautifulSoup

__version__ = "5.0.0-Nextjs与Ovid双栖直抓版"

# ==================== 物理配置区域 ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROXY_SERVER = "http://127.0.0.1:29758"
# ======================================================

def push_to_github():
    print("\n📤 启动 GitHub 自动同步 (LWW Feeds)...")
    custom_env = os.environ.copy()
    custom_env["HTTP_PROXY"] = PROXY_SERVER
    custom_env["HTTPS_PROXY"] = PROXY_SERVER
    
    try:
        subprocess.run(["git", "add", "annals_*.xml", "aswc_*.xml", "derm_*.xml", "j_*.xml", "prs_*.xml"], cwd=BASE_DIR, check=True)
        commit_msg = f"Auto-update LWW feeds: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=BASE_DIR, check=True)
        subprocess.run(["git", "push"], cwd=BASE_DIR, env=custom_env, check=True)
        print("✅ 同步成功！LWW 提纯数据已安全送达 GitHub 独立仓库。")
    except Exception as e:
        print(f"ℹ️ 发布管线返回: 无变更或推送被跳过 ({e})")

def main():
    print(f"=== [LWW] Start ({__version__}): {time.ctime()} ===")
    
    co = ChromiumOptions()
    # 🟢 移除 '--headless' 隐身模式，强制弹窗，让你肉眼看到真正的 XML 数据
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    co.set_argument('--remote-debugging-port=9222') 
    co.set_browser_path('/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge')
    
    page = ChromiumPage(co)
    updated_any = False
    
    # 🟢 混合模式：已改版的用直抓 (web_scrape=True)，未改版的用原装 XML 接口 (web_scrape=False)
    targets = [
        {"name": "aswc_current_issue", "rss_url": "https://www.ovid.com/jnls/aswcjournal", "output_filename": "aswc_current_issue.xml", "web_scrape": True, "title": "Advances in Skin & Wound Care - Current Issue"},
        {"name": "aswc_latest_articles", "rss_url": "https://www.ovid.com/jnls/aswcjournal", "output_filename": "aswc_latest_articles.xml", "web_scrape": True, "title": "Advances in Skin & Wound Care - Latest Articles"},
        {"name": "annals_plast_surg_current", "rss_url": "https://www.ovid.com/jnls/annalsplasticsurgery", "output_filename": "annals_plast_surg_current.xml", "web_scrape": True, "title": "Annals of Plastic Surgery - Current Issue"},
        {"name": "annals_plast_surg_latest", "rss_url": "https://www.ovid.com/jnls/annalsplasticsurgery", "output_filename": "annals_plast_surg_latest.xml", "web_scrape": True, "title": "Annals of Plastic Surgery - Latest Articles"},
        {"name": "derm_surgery_ahead", "rss_url": "https://www.ovid.com/jnls/dermatologicsurgery", "output_filename": "derm_surgery_ahead.xml", "web_scrape": True, "title": "Dermatologic Surgery - Ahead of Print"},
        {"name": "derm_surgery_latest", "rss_url": "https://www.ovid.com/jnls/dermatologicsurgery", "output_filename": "derm_surgery_latest.xml", "web_scrape": True, "title": "Dermatologic Surgery - Latest Articles"},
        {"name": "j_craniofacial_surg_latest", "rss_url": "https://journals.lww.com/jcraniofacialsurgery/toc/latest", "output_filename": "j_craniofacial_surg_latest.xml", "web_scrape": True, "title": "Journal of Craniofacial Surgery - Latest Articles"},
        {"name": "j_craniofacial_surg_web_latest", "rss_url": "https://journals.lww.com/jcraniofacialsurgery/toc/latest", "output_filename": "j_craniofacial_surg_web_latest.xml", "web_scrape": True, "title": "Journal of Craniofacial Surgery - Web Latest"},
        {"name": "j_craniofacial_surg_open_latest", "rss_url": "https://www.ovid.com/jnls/jcso", "output_filename": "j_craniofacial_surg_open_latest.xml", "web_scrape": True, "title": "Journal of Craniofacial Surgery Open - Latest Articles"},
        {"name": "prs_video", "rss_url": "https://journals.lww.com/plasreconsurg/toc/latest", "output_filename": "prs_video.xml", "web_scrape": True, "title": "Plastic and Reconstructive Surgery - Video"},
        {"name": "prs_current_issue", "rss_url": "https://journals.lww.com/plasreconsurg/toc/current", "output_filename": "prs_current_issue.xml", "web_scrape": True, "title": "Plastic and Reconstructive Surgery - Current Issue"},
        {"name": "prs_latest_articles", "rss_url": "https://journals.lww.com/plasreconsurg/toc/latest", "output_filename": "prs_latest_articles.xml", "web_scrape": True, "title": "Plastic and Reconstructive Surgery - Latest Articles"},
        {"name": "prs_online_first", "rss_url": "https://journals.lww.com/plasreconsurg/toc/latest", "output_filename": "prs_online_first.xml", "web_scrape": True, "title": "Plastic and Reconstructive Surgery - Online First"},
        {"name": "prs_go_current_issue", "rss_url": "https://journals.lww.com/prsgo/toc/current", "output_filename": "prs_go_current_issue.xml", "web_scrape": True, "title": "Plastic and Reconstructive Surgery Global Open - Current Issue"},
        {"name": "prs_go_latest_articles", "rss_url": "https://journals.lww.com/prsgo/toc/latest", "output_filename": "prs_go_latest_articles.xml", "web_scrape": True, "title": "Plastic and Reconstructive Surgery Global Open - Latest Articles"}
    ]
    
    for journal in targets:
        name = journal['name']
        rss_url = journal['rss_url']
        output_filename = journal['output_filename']
        output_path = os.path.join(BASE_DIR, output_filename)
        
        print(f"\n📡 正在抓取期刊源: {name} ...")
        
        try:
            if journal.get("web_scrape", False):
                page.get(rss_url)
                # 等待绕过 Cloudflare
                for _ in range(30):
                    if "Just a moment" not in page.title and "Cloudflare" not in page.title:
                        break
                    time.sleep(1)
                time.sleep(5)  # 额外等待 Next.js hydration
                
                raw_html = page.html
                soup = BeautifulSoup(raw_html, 'html.parser')
                
                items = []
                for a in soup.find_all('a'):
                    href = a.get('href')
                    if href and ('/fulltext/' in href or '10.1097' in href):
                        title = a.get_text(strip=True)
                        if title and len(title) > 10 and 'PDF' not in title:
                            full_url = href if href.startswith('http') else urljoin(rss_url, href)
                            if not any(i['link'] == full_url for i in items):
                                items.append({'title': title, 'link': full_url})
                
                print(f"📦 网页抓取成功捕获 {len(items)} 个文献条目。")
                if items:
                    pub_date_str = formatdate(time.time(), localtime=False, usegmt=True)
                    items_xml = ""
                    for item in items:
                        item_xml = f"""
    <item>
      <link>{item['link']}</link>
      <title><![CDATA[{item['title']}]]></title>
      <description><![CDATA[<b>所属期数:</b> Ahead of Print<br><b>出版时间:</b> {pub_date_str}<br><br><a href="{item['link']}"></a>No description available.]]></description>
      <pubDate>{pub_date_str}</pubDate>
    </item>"""
                        items_xml += item_xml
                        
                    channel_title = journal.get('title', name)
                    pure_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<rss xmlns:prism="http://prismstandard.org/namespaces/1.2/basic/" version="2.0">
  <channel>
    <title><![CDATA[{channel_title}]]></title>
    <link>{rss_url}</link>
    <description><![CDATA[Auto-generated from Web Scrape]]></description>
    <lastBuildDate>{pub_date_str}</lastBuildDate>
{items_xml}
  </channel>
</rss>"""
                    with open(output_path, 'w', encoding='utf-8') as f:
                        f.write(pure_xml)
                    print(f"✅ 成功完美合成存盘: {output_path}")
                    print(f"[REPORT] CHANNEL=LWW ITEM={name} COUNT={len(items)} STATUS=SUCCESS")
                    updated_any = True
                else:
                    print(f"❌ 网页提取失败，未检测到任何文章链接。")
                    print(f"[REPORT] CHANNEL=LWW ITEM={name} COUNT=0 STATUS=FAIL")
            else:
                page.get(rss_url)
                
                # 为原始 XML 分支也添加 Cloudflare 绕过等待
                for _ in range(30):
                    if "Just a moment" not in page.title and "Cloudflare" not in page.title:
                        break
                    time.sleep(1)
                time.sleep(8) 
                
                raw_html = page.html
                
                xml_match = re.search(r'<rss.*?</rss>', raw_html, re.DOTALL | re.IGNORECASE)
                
                if xml_match:
                    pure_xml = xml_match.group(0)
                    pure_xml = re.sub(r'xmlns:prism=""', 'xmlns:prism="http://prismstandard.org/namespaces/1.2/basic/"', pure_xml)
                    
                    items = re.findall(r'<item>.*?</item>', pure_xml, re.DOTALL)
                    print(f"📦 成功捕获 {len(items)} 个文献条目。正在注入所属期数与出版时间...")
                    
                    for item in items:
                        new_item = item
                        
                        vol_m = re.search(r'<prism:volume>(.*?)</prism:volume>', item)
                        num_m = re.search(r'<prism:number>(.*?)</prism:number>', item)
                        pub_m = re.search(r'<pubDate>(.*?)</pubDate>', item)
                        
                        vol_str = vol_m.group(1) if vol_m else ""
                        num_str = num_m.group(1) if num_m else ""
                        pub_date_str = pub_m.group(1) if pub_m else "Unknown Date"
                        
                        issue_info = f"Vol. {vol_str} No. {num_str}" if (vol_str or num_str) else "Ahead of Print"
                        
                        desc_match = re.search(r'<description>(.*?)</description>', new_item, re.DOTALL)
                        if desc_match:
                            original_desc = desc_match.group(1)
                            clean_inner = re.sub(r'<!\[CDATA\[|\]\]>', '', original_desc)
                            if not clean_inner.strip():
                                clean_inner = "No description available."
                            
                            new_desc = f"<![CDATA[<b>所属期数:</b> {issue_info}<br><b>出版时间:</b> {pub_date_str}<br><br>{clean_inner.strip()}]]>"
                            new_item = new_item.replace(f"<description>{original_desc}</description>", f"<description>{new_desc}</description>")
                            pure_xml = pure_xml.replace(item, new_item)
                    
                    raw_xml = '<?xml version="1.0" encoding="utf-8"?>\n' + pure_xml
                    raw_xml = raw_xml.replace('\u2028', '\n').replace('\u2029', '\n')
                    
                    with open(output_path, 'w', encoding='utf-8') as f:
                        f.write(raw_xml)
                    print(f"✅ 成功完美提纯存盘: {output_path}")
                    print(f"[REPORT] CHANNEL=LWW ITEM={name} COUNT={len(items)} STATUS=SUCCESS")
                    updated_any = True
                else:
                    print(f"❌ 页面提取失败，未检测到合规的 XML 根节点。")
                    print(f"[REPORT] CHANNEL=LWW ITEM={name} COUNT=0 STATUS=FAIL")
                
        except Exception as e:
            print(f"⚠️ 运行时异常捕获: {e}")
            print(f"[REPORT] CHANNEL=LWW ITEM={name} COUNT=0 STATUS=FAIL")
            
    page.quit()
    
    if updated_any:
        push_to_github()

if __name__ == "__main__":
    main()