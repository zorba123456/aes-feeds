#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
Project: lit_auto_pipeline (aes-intel platform)
File: aes-feeds/lww_downloader.py
Version: V5.3.0 (Ovid TOC 网页直抓提纯版)
Description:
    1. 彻底停用 Ovid 官方 RSS API（避免数据库发布滞后以及深层数据库链接无法直接打开的问题）。
    2. 直接在官网公共 TOC（目录）子页面爬取当期和首发文献：
       - 当期页面：https://www.ovid.com/jnls/{short_name}/toc/current
       - 首发页面：https://www.ovid.com/jnls/{short_name}/toc/latest
    3. 解析 React Hydration 属性 (data-hydrate-props)，物理重塑为 100% 正确的公共官网 URL、
       完整的作者列表、准确的页码/出版时间，并将其写入合规的 XML 文件。
    4. 保留原有 Next.js / journals.lww.com 直抓逻辑作为后备，保障其它期刊稳定更新。
=============================================================================
"""

import os
import time
import subprocess
import re
import json
from datetime import datetime
import email.utils
from urllib.parse import urljoin
from email.utils import formatdate
from DrissionPage import ChromiumPage, ChromiumOptions
from bs4 import BeautifulSoup

__version__ = "5.4.0"

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

def get_clean_props(m):
    props_str = m.attr('data-hydrate-props')
    if props_str:
        try:
            return json.loads(props_str)
        except Exception:
            pass
    return None

def get_full_title(content):
    if isinstance(content, dict):
        full_text_list = content.get('fullText', [])
        parts = []
        for item in full_text_list:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(item.get('plainText', ''))
        return "".join(parts).strip()
    return str(content).strip()

def parse_card_metadata(card_text, journal_name):
    lines = [l.strip() for l in card_text.split('\n') if l.strip()]
    if not lines:
        return "Unknown Authors", "Unknown Issue", "Unknown Date", ""
        
    meta_line = None
    meta_idx = -1
    for idx, line in enumerate(lines):
        if '.' in line and any(keyword in line.lower() for keyword in ['surgery', 'skin', 'wound', 'craniofacial']):
            meta_line = line
            meta_idx = idx
            break
        if re.search(r'\b(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}\b', line, re.IGNORECASE):
            meta_line = line
            meta_idx = idx
            break
            
    if not meta_line:
        meta_line = lines[-1]
        meta_idx = len(lines) - 1
        
    authors = "Unknown Authors"
    for line in lines:
        if "Show More" in line:
            authors = line.replace("Show More", "").strip()
            break
    else:
        if meta_idx > 0:
            authors = lines[meta_idx - 1]
            
    issue = "Ahead of Print"
    pub_date = "Unknown Date"
    pages = ""
    
    if ':' in meta_line and '.' in meta_line:
        parts = meta_line.split('.', 1)
        date_block = parts[1].strip() if len(parts) > 1 else meta_line
        
        if ':' in date_block:
            date_part, issue_part = date_block.split(':', 1)
            pub_date = date_part.strip()
            
            issue_subparts = issue_part.strip().split(':')
            volume_issue = issue_subparts[0].strip()
            pages = issue_subparts[1].strip() if len(issue_subparts) > 1 else ""
            
            issue = f"Volume {volume_issue}"
    else:
        pub_date = meta_line.strip()
        issue = "Ahead of Print"
        
    return authors, issue, pub_date, pages

def parse_to_rfc822(date_str):
    if not date_str or "Unknown" in date_str:
        return email.utils.formatdate(time.time(), localtime=False, usegmt=True)
    date_str = date_str.strip()
    
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%B %Y", "%b %Y"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return email.utils.formatdate(dt.timestamp(), localtime=False, usegmt=True)
        except ValueError:
            pass
            
    return email.utils.formatdate(time.time(), localtime=False, usegmt=True)

def scrape_toc_page(page, url, journal_name):
    print(f"📡 Scraping TOC Page: {url}")
    page.get(url)
    for _ in range(30):
        if "Just a moment" not in page.title and "Cloudflare" not in page.title:
            break
        time.sleep(1)
    time.sleep(5)
    
    markers = page.eles('.js-omni-hydrate-marker')
    articles = []
    seen_urls = set()
    
    for m in markers:
        props = get_clean_props(m)
        if not props or not props.get('url'):
            continue
            
        url_val = props.get('url')
        if url_val in seen_urls:
            continue
        seen_urls.add(url_val)
        
        title_val = get_full_title(props.get('content'))
        an_val = props.get('accessionNumber')
        
        card_text = ""
        curr = m
        for depth in range(4):
            curr = curr.parent()
            if not curr:
                break
            class_str = str(curr.attr('class'))
            if 'omni-card-body' in class_str or 'collection-item' in class_str:
                card_text = curr.text.strip()
                break
        else:
            p2 = m.parent().parent()
            if p2:
                card_text = p2.text.strip()
                
        authors, issue, pub_date, pages = parse_card_metadata(card_text, journal_name)
        
        articles.append({
            'title': title_val,
            'link': url_val,
            'an': an_val,
            'authors': authors,
            'issue': issue,
            'pub_date': pub_date,
            'pages': pages
        })
        
    print(f"📦 Successfully parsed {len(articles)} articles from {url}")
    return articles

def main():
    print(f"=== [LWW] Start ({__version__}): {time.ctime()} ===")
    
    co = ChromiumOptions()
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    co.set_argument('--remote-debugging-port=9222') 
    co.set_browser_path('/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge')
    
    # 🟢 启用本地浏览器缓存持久化目录，复用 Cloudflare 的 cf_clearance 通行证与 Cookie
    profile_dir = os.path.join(BASE_DIR, "lww_browser_profile")
    co.set_user_data_path(profile_dir)
    
    page = ChromiumPage(co)
    updated_any = False
    has_failures = False
    
    targets = [
        {"name": "aswc_current_issue", "rss_url": "https://www.ovid.com/jnls/aswcjournal", "output_filename": "aswc_current_issue.xml", "web_scrape": True, "title": "Advances in Skin & Wound Care - Current Issue"},
        {"name": "aswc_latest_articles", "rss_url": "https://www.ovid.com/jnls/aswcjournal", "output_filename": "aswc_latest_articles.xml", "web_scrape": True, "title": "Advances in Skin & Wound Care - Latest Articles"},
        {"name": "annals_plast_surg_current", "rss_url": "https://www.ovid.com/jnls/annalsplasticsurgery", "output_filename": "annals_plast_surg_current.xml", "web_scrape": True, "title": "Annals of Plastic Surgery - Current Issue"},
        {"name": "annals_plast_surg_latest", "rss_url": "https://www.ovid.com/jnls/annalsplasticsurgery", "output_filename": "annals_plast_surg_latest.xml", "web_scrape": True, "title": "Annals of Plastic Surgery - Latest Articles"},
        {"name": "derm_surgery_ahead", "rss_url": "https://www.ovid.com/jnls/dermatologicsurgery", "output_filename": "derm_surgery_ahead.xml", "web_scrape": True, "title": "Dermatologic Surgery - Ahead of Print"},
        {"name": "derm_surgery_latest", "rss_url": "https://www.ovid.com/jnls/dermatologicsurgery", "output_filename": "derm_surgery_latest.xml", "web_scrape": True, "title": "Dermatologic Surgery - Latest Articles"},
        {"name": "j_craniofacial_surg_latest", "rss_url": "https://journals.lww.com/jcraniofacialsurgery/toc/latest", "output_filename": "j_craniofacial_surg_latest.xml", "web_scrape": True, "title": "Journal of Craniofacial Surgery - Latest Articles"},
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
                if "ovid.com" in rss_url:
                    # 🟢 Ovid 平台专属的新子页面 TOC 抓取逻辑
                    base_url = rss_url.rstrip('/')
                    journal_name = journal.get('title', name).split(' - ')[0]
                    
                    articles = []
                    if "current" in name:
                        toc_url = f"{base_url}/toc/current"
                        articles = scrape_toc_page(page, toc_url, journal_name)
                    else:
                        # 所有 latest, ahead, online_first 等目标，均严格只对齐 toc/latest，不进行跨版块融合
                        toc_url = f"{base_url}/toc/latest"
                        articles = scrape_toc_page(page, toc_url, journal_name)
                        
                    if articles:
                        items_xml = ""
                        for item in articles:
                            desc_html = f"<b>所属期数:</b> {item['issue']}<br><b>出版时间:</b> {item['pub_date']}"
                            if item.get('pages'):
                                desc_html += f"<br><b>页码/出版信息:</b> {item['pages']}"
                            if item.get('authors') and item['authors'] != "Unknown Authors":
                                desc_html += f"<br><b>作者:</b> {item['authors']}"
                            desc_html += f"<br><br><a href=\"{item['link']}\"></a>No description available."
                            
                            rfc_pub_date = parse_to_rfc822(item['pub_date'])
                            
                            item_xml = f"""
    <item>
      <link>{item['link']}</link>
      <title><![CDATA[{item['title']}]]></title>
      <description><![CDATA[{desc_html}]]></description>
      <pubDate>{rfc_pub_date}</pubDate>
    </item>"""
                            items_xml += item_xml
                            
                        channel_title = journal.get('title', name)
                        pub_date_str = formatdate(time.time(), localtime=False, usegmt=True)
                        pure_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<rss xmlns:prism="http://prismstandard.org/namespaces/1.2/basic/" version="2.0">
  <channel>
    <title><![CDATA[{channel_title}]]></title>
    <link>{rss_url}</link>
    <description><![CDATA[Auto-generated from Ovid TOC Web Scraper]]></description>
    <lastBuildDate>{pub_date_str}</lastBuildDate>
{items_xml}
  </channel>
</rss>"""
                        with open(output_path, 'w', encoding='utf-8') as f:
                            f.write(pure_xml)
                        print(f"✅ 成功完美合成存盘: {output_path}")
                        print(f"[REPORT] CHANNEL=LWW ITEM={name} COUNT={len(articles)} STATUS=SUCCESS")
                        updated_any = True
                    else:
                        print(f"❌ 网页提取失败，未检测到任何文章。")
                        print(f"[REPORT] CHANNEL=LWW ITEM={name} COUNT=0 STATUS=FAIL")
                        has_failures = True
                else:
                    # 🟢 原有 Next.js/journals.lww.com 直抓逻辑后备
                    page.get(rss_url)
                    for _ in range(30):
                        if "Just a moment" not in page.title and "Cloudflare" not in page.title:
                            break
                        time.sleep(1)
                    time.sleep(5)
                    
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
                        has_failures = True
            else:
                # 🟢 原始 XML 路由逻辑 (web_scrape=False)
                page.get(rss_url)
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
                    has_failures = True
                
        except Exception as e:
            print(f"⚠️ 运行时异常捕获: {e}")
            print(f"[REPORT] CHANNEL=LWW ITEM={name} COUNT=0 STATUS=FAIL")
            has_failures = True
            
    page.quit()
    
    if updated_any:
        push_to_github()
        
    if has_failures:
        try:
            msg = "部分 LWW/Ovid 期刊抓取遇到验证码拦截，请双击桌面「LWW一键验证」进行处理。"
            title = "LWW 爬虫验证提醒"
            subprocess.run(['osascript', '-e', f'display alert "{title}" message "{msg}" buttons {{"确定"}} default button "确定"'])
        except:
            pass

if __name__ == "__main__":
    main()