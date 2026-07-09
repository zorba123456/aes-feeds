#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
Project: lit_auto_pipeline (aes-intel platform)
File: aes-feeds/lww_downloader.py
Version: V5.2.0 (Ovid RSS 与 网页双栖重构版)
Description:
    1. 针对 Ovid 平台期刊（Annals, ASWC, Derm, JCSO），结合官方 RSS 提取完美元数据，
       并结合 Homepage Scrape 补充 Ahead of Print (Latest) 文献。
    2. 生成符合 public Ovid journal website 规范的 URL，自动在用户终端及 Inoreader 中正常跳转至全文/摘要页面。
    3. 保留 journals.lww.com 期刊的原始 HTML 解析逻辑作为安全后备。
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

__version__ = "5.2.0"

# ==================== 物理配置区域 ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROXY_SERVER = "http://127.0.0.1:29758"

# Ovid 期刊短域名与代码映射
OVID_JOURNALS = {
    "aswcjournal": {
        "code": "00129334",
        "short_name": "aswcjournal"
    },
    "annalsplasticsurgery": {
        "code": "00000637",
        "short_name": "annalsplasticsurgery"
    },
    "dermatologicsurgery": {
        "code": "00042728",
        "short_name": "dermatologicsurgery"
    },
    "jcso": {
        "code": "02273970",
        "short_name": "jcso"
    }
}
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

def parse_to_rfc822(date_str):
    if not date_str:
        return email.utils.formatdate(time.time(), localtime=False, usegmt=True)
    date_str = date_str.strip()
    
    # 尝试解析常见日期格式并转换为标准 RFC-822
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%B %Y", "%b %Y"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return email.utils.formatdate(dt.timestamp(), localtime=False, usegmt=True)
        except ValueError:
            pass
            
    return email.utils.formatdate(time.time(), localtime=False, usegmt=True)

def main():
    print(f"=== [LWW] Start ({__version__}): {time.ctime()} ===")
    
    co = ChromiumOptions()
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    co.set_argument('--remote-debugging-port=9222') 
    co.set_browser_path('/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge')
    
    page = ChromiumPage(co)
    updated_any = False
    
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
                    # 🟢 Ovid 平台专属的新 Next.js 与 RSS 双栖解析逻辑
                    journal_key = rss_url.rstrip('/').split('/')[-1]
                    journal_info = OVID_JOURNALS.get(journal_key)
                    if not journal_info:
                        raise ValueError(f"Unknown Ovid journal short name in URL: {rss_url}")
                        
                    code = journal_info['code']
                    short_name = journal_info['short_name']
                    
                    # 1. 抓取 Ovid 官方 Current Issue RSS XML
                    rss_api_url = f"https://ovidsp.ovid.com/rss/journals/{code}/current.rss"
                    print(f"📡 Fetching Ovid official RSS XML: {rss_api_url}")
                    page.get(rss_api_url)
                    for _ in range(30):
                        if "Just a moment" not in page.title and "Cloudflare" not in page.title:
                            break
                        time.sleep(1)
                    time.sleep(3)
                    
                    pre = page.ele('tag:pre')
                    rss_xml = pre.text if pre else page.html
                    
                    rss_soup = BeautifulSoup(rss_xml, 'xml')
                    rss_channel = rss_soup.find('channel')
                    
                    # 解析当期 issue 文章列表
                    rss_articles = []
                    issue_title = "Current Issue"
                    if rss_channel:
                        channel_t = rss_channel.find('title')
                        if channel_t:
                            channel_t_str = channel_t.text
                            if '.' in channel_t_str:
                                issue_title = channel_t_str.split('.', 1)[1].strip()
                            else:
                                issue_title = channel_t_str.strip()
                                
                        for item in rss_channel.find_all('item'):
                            t_tag = item.find('title')
                            title_text = t_tag.text.strip() if t_tag else ""
                            
                            guid_tag = item.find('guid')
                            guid_text = guid_tag.text.strip() if guid_tag else ""
                            
                            link_tag = item.find('link')
                            link_text = link_tag.text.strip() if link_tag else ""
                            
                            an_match = re.search(r'AN=([a-zA-Z0-9\-]+)', guid_text or link_text)
                            an = an_match.group(1) if an_match else ""
                            
                            desc_tag = item.find('description')
                            desc_text = desc_tag.text.strip() if desc_tag else ""
                            
                            if title_text and an:
                                public_url = f"https://www.ovid.com/jnls/{short_name}/fulltext/{an}"
                                pub_date_val = re.sub(r'Volume\s+\d+\([^)]+\)\s*', '', issue_title).strip()
                                rss_articles.append({
                                    'title': title_text,
                                    'link': public_url,
                                    'an': an,
                                    'issue': issue_title,
                                    'pub_date': pub_date_val,
                                    'description': desc_text
                                })
                    
                    print(f"📦 Official RSS returned {len(rss_articles)} articles for {issue_title}.")
                    
                    # 2. 若目标为 latest 或 ahead，抓取官网以补充 Ahead of Print 文献
                    aop_articles = []
                    if "current" not in name:
                        print(f"📡 Scraping Ovid homepage for Ahead of Print: {rss_url}")
                        page.get(rss_url)
                        for _ in range(30):
                            if "Just a moment" not in page.title and "Cloudflare" not in page.title:
                                break
                            time.sleep(1)
                        time.sleep(5)
                        
                        markers = page.eles('.js-omni-hydrate-marker')
                        seen_urls = set()
                        for m in markers:
                            props_str = m.attr('data-hydrate-props')
                            if not props_str:
                                continue
                            try:
                                props = json.loads(props_str)
                                url_val = props.get('url')
                                an_val = props.get('accessionNumber')
                                
                                # Ahead of Print 它的 accessionNumber 含有 -990000000-
                                if url_val and an_val and "-990000000-" in an_val and url_val not in seen_urls:
                                    seen_urls.add(url_val)
                                    
                                    content = props.get('content')
                                    title_val = content.get('fullText', [None])[0] if isinstance(content, dict) else str(content)
                                    
                                    # 抓取外层卡片文字来提取日期和摘要
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
                                            
                                    # 提取发布时间
                                    pub_date = None
                                    date_match = re.search(r'\b(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}\b', card_text, re.IGNORECASE)
                                    if date_match:
                                        pub_date = date_match.group(0)
                                    else:
                                        date_match2 = re.search(r'\b(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}\b', card_text, re.IGNORECASE)
                                        if date_match2:
                                            pub_date = date_match2.group(0)
                                            
                                    if not pub_date:
                                        pub_date = formatdate(time.time(), localtime=False, usegmt=True)
                                        
                                    # 提取摘要段落（过滤掉期刊名行）
                                    abstract = ""
                                    lines = card_text.split('\n')
                                    start_abstract = False
                                    abstract_lines = []
                                    for line in lines:
                                        if start_abstract:
                                            abstract_lines.append(line.strip())
                                        if '.' in line and any(keyword in line.lower() for keyword in ['surgery', 'skin', 'wound', 'craniofacial']):
                                            start_abstract = True
                                            
                                    if abstract_lines:
                                        abstract = "\n".join(abstract_lines).strip()
                                    else:
                                        abstract = "No description available."
                                        
                                    aop_articles.append({
                                        'title': title_val,
                                        'link': f"https://www.ovid.com/jnls/{short_name}/fulltext/{an_val}",
                                        'an': an_val,
                                        'issue': 'Ahead of Print',
                                        'pub_date': pub_date,
                                        'description': abstract
                                    })
                            except Exception as ex:
                                print(f"  Warning parsing hydrate marker: {ex}")
                                
                        print(f"📦 Scraped {len(aop_articles)} Ahead of Print articles from homepage.")
                        
                    # 3. 按不同订阅需求，组装结果
                    final_articles = []
                    if "current" in name:
                        final_articles = rss_articles
                    elif "ahead" in name:
                        final_articles = aop_articles
                    else:
                        # latest 融合版 (AOP 靠前，保证新鲜度)
                        final_articles = aop_articles + rss_articles
                        
                    if final_articles:
                        items_xml = ""
                        for item in final_articles:
                            desc_content = item['description']
                            desc_html = f"<b>所属期数:</b> {item['issue']}<br><b>出版时间:</b> {item['pub_date']}<br><br>{desc_content}"
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
    <description><![CDATA[Auto-generated from Ovid Feeds Pipeline]]></description>
    <lastBuildDate>{pub_date_str}</lastBuildDate>
{items_xml}
  </channel>
</rss>"""
                        with open(output_path, 'w', encoding='utf-8') as f:
                            f.write(pure_xml)
                        print(f"✅ 成功完美合成存盘: {output_path}")
                        print(f"[REPORT] CHANNEL=LWW ITEM={name} COUNT={len(final_articles)} STATUS=SUCCESS")
                        updated_any = True
                    else:
                        print(f"❌ 网页提取失败，未检测到任何文章。")
                        print(f"[REPORT] CHANNEL=LWW ITEM={name} COUNT=0 STATUS=FAIL")
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
                
        except Exception as e:
            print(f"⚠️ 运行时异常捕获: {e}")
            print(f"[REPORT] CHANNEL=LWW ITEM={name} COUNT=0 STATUS=FAIL")
            
    page.quit()
    
    if updated_any:
        push_to_github()

if __name__ == "__main__":
    main()