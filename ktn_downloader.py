#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
Project: lit_auto_pipeline (aes-intel platform)
File: aes-feeds/ktn_downloader.py
Version: V1.0.7
=============================================================================
"""

import os
import sys
import xml.etree.ElementTree as ET
import requests
import feedparser
import json
import time
import hashlib
import re
import subprocess
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup

# ==================== 配置区域 ====================
KTN_RSS_URL = "https://kill-the-newsletter.com/feeds/uwgwyb1cnivki39x.xml"
OUTPUT_XML_PATH = "aes-feeds/ktn_cleaned_articles.xml"  
LOG_FILE_PATH = "aes-feeds/ktn_dedup_log.json"           
DEDUP_EXPIRE_DAYS = 60                         

PROXIES = {
    "http": "http://127.0.0.1:29758",
    "https": "http://127.0.0.1:29758"
}

LOCAL_BACKUP_XML = "aes-feeds/uwgwyb1cnivki39x.xml"
# ==================================================

def load_dedup_log():
    if os.path.exists(LOG_FILE_PATH):
        try:
            with open(LOG_FILE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_dedup_log(log_data):
    now = time.time()
    expire_sec = DEDUP_EXPIRE_DAYS * 24 * 3600
    cleaned_log = {k: v for k, v in log_data.items() if (now - v.get('ts', 0)) < expire_sec}
    with open(LOG_FILE_PATH, 'w', encoding='utf-8') as f:
        json.dump(cleaned_log, f, ensure_ascii=False, indent=2)

def clean_google_url(raw_url):
    if not raw_url: return ""
    if "scholar.google.com/scholar_url" in raw_url:
        parsed = urlparse(raw_url)
        queries = parse_qs(parsed.query)
        if 'url' in queries and queries['url']:
            raw_url = queries['url'][0]
    return raw_url.split('?')[0].split('#')[0].strip()

def extract_doi(h3_node):
    doi_link = h3_node.find('a', class_='pubmed_toolsDOI')
    if doi_link and 'href' in doi_link.attrs:
        href = doi_link.attrs['href']
        doi_match = re.search(r'doi\.org/(10\.\d{4,}/.+)$', href, re.I)
        if doi_match:
            return doi_match.group(1).lower().strip()
    return ""

def parse_mail_content(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    articles = []
    
    title_links = soup.find_all('a', class_='gse_alrt_title')
    if not title_links:
        return articles

    for link in title_links:
        try:
            title_text = re.sub(r'\s+', ' ', link.get_text().strip())
            raw_url = link.get('href', '')
            clean_url = clean_google_url(raw_url)
            
            parent_h3 = link.find_parent('h3')
            doi = extract_doi(parent_h3) if parent_h3 else ""
            
            journal_name = "Unknown Journal"
            snippet = ""
            
            parent_node = link.find_parent()
            if parent_node:
                meta_div = parent_node.find_next_sibling('div')
                if meta_div:
                    if 'gse_alrt_sni' not in meta_div.get('class', []):
                        author_journal = re.sub(r'\s+', ' ', meta_div.get_text().strip())
                        if " - " in author_journal:
                            parts = author_journal.split(" - ", 1)
                            if len(parts) >= 2:
                                journal_candidate = parts[1].strip()
                                j_name = journal_candidate.split(",")[0].strip()
                                if j_name and not j_name.isdigit():
                                    journal_name = j_name

                        sni_div = meta_div.find_next_sibling('div')
                        if sni_div and 'gse_alrt_sni' in sni_div.get('class', []):
                            snippet = re.sub(r'\s+', ' ', sni_div.get_text().strip())
                    else:
                        snippet = re.sub(r'\s+', ' ', meta_div.get_text().strip())

            if doi:
                fp = hashlib.md5(f"doi:{doi}".encode('utf-8')).hexdigest()
            elif clean_url:
                fp = hashlib.md5(f"url:{clean_url}".encode('utf-8')).hexdigest()
            else:
                combined = f"text:{title_text.lower()}_{journal_name.lower()}".replace(" ", "")
                fp = hashlib.md5(combined.encode('utf-8')).hexdigest()

            articles.append({
                "fingerprint": fp,
                "title": title_text,
                "url": clean_url if clean_url else raw_url,
                "journal": journal_name,
                "description": snippet
            })
        except Exception:
            continue
            
    return articles

def generate_rss_xml(articles_list):
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    
    ET.SubElement(channel, "title").text = "KTN Cleaned Academic Feeds"
    ET.SubElement(channel, "link").text = "https://github.com/zorba123456/aes-feeds"
    ET.SubElement(channel, "description").text = "文献级粒度降维后的无菌学术订阅源"
    
    current_utc_time = datetime.now(timezone.utc)
    ET.SubElement(channel, "lastBuildDate").text = current_utc_time.strftime('%a, %d %b %Y %H:%M:%S GMT')

    for art in articles_list:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = art['title']
        ET.SubElement(item, "link").text = art['url']
        ET.SubElement(item, "guid", isPermaLink="false").text = art['fingerprint']
        
        pub_date = art.get('pubDate', '')
        if pub_date:
            ET.SubElement(item, "pubDate").text = pub_date
        
        desc_content = f"来自期刊: {art['journal']}<br/><b>收录时间:</b> {pub_date}<br/><br/>{art['description']}"
        ET.SubElement(item, "description").text = desc_content
        
    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ", level=0)
    tree.write(OUTPUT_XML_PATH, encoding="utf-8", xml_declaration=True)

def git_push_feeds():
    print("📤 启动发布管线，正在自动推送到 GitHub...")
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        subprocess.run(["git", "add", "ktn_cleaned_articles.xml", "ktn_dedup_log.json"], cwd=current_dir, check=True)
        commit_msg = f"Auto-Update KTN Feeds (Add pubDate): {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=current_dir, check=True)
        subprocess.run(["git", "push"], cwd=current_dir, check=True)
        print("🚀 GitHub 仓库发布成功！带有时间戳的 KTN XML 现已生效。")
    except subprocess.CalledProcessError as e:
        print(f"⚠️ Git 自动推送过程中出现提示: {e}")
    except Exception as e:
        print(f"❌ 自动化发布失败: {e}")

def main():
    print("🚀 开始解析并提纯 KTN 邮件流 (V1.0.7 时间戳版)...")
    dedup_log = load_dedup_log()
    
    feed_text = ""
    try:
        print(f"🌐 正在请求 KTN 服务器...")
        response = requests.get(KTN_RSS_URL, proxies=PROXIES, timeout=12)
        if response.status_code == 200:
            feed_text = response.text
    except Exception as e:
        print(f"⚠️ 线上请求失败（网络异常）: {e}")
    
    if not feed_text:
        if os.path.exists(LOCAL_BACKUP_XML):
            with open(LOCAL_BACKUP_XML, 'r', encoding='utf-8') as f:
                feed_text = f.read()
        else:
            return

    feed = feedparser.parse(feed_text)
    all_valid_articles = []
    current_time_stamp = time.time()

    for entry in feed.entries:
        html_content = ""
        if 'content' in entry:
            html_content = entry.content[0].value
        elif 'summary' in entry:
            html_content = entry.summary
            
        if not html_content:
            continue
            
        mail_date = entry.get('published', entry.get('updated', datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')))

        extracted_articles = parse_mail_content(html_content)
        for art in extracted_articles:
            art['pubDate'] = mail_date 
            fp = art['fingerprint']
            if fp not in dedup_log and fp not in [a['fingerprint'] for a in all_valid_articles]:
                dedup_log[fp] = {
                    "title": art['title'],
                    "ts": current_time_stamp
                }
                all_valid_articles.append(art)

    print(f"✨ 提纯完成。本次发现全新增量文献数: {len(all_valid_articles)} 篇")

    if all_valid_articles:
        generate_rss_xml(all_valid_articles)
        save_dedup_log(dedup_log)
        git_push_feeds()
    else:
        print("⏸️ 没有新的文献增量，跳过推送。")

if __name__ == "__main__":
    main()