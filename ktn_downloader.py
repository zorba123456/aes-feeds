#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
Project: lit_auto_pipeline (aes-intel platform)
File: ktn_downloader.py
Version: V2.2.8-GatedStable
Description:
    1. 彻底剔除关键词提取阶段的多重双引号噪声，确保呈现标准的 KTN_"关键词" 格式。
    2. 修复上游 [REPORT] 报盘中 keyword 携带半截引号导致入库解析错位的硬伤。
    3. 物理文件名严格锁定小写（ktn_*.xml），彻底根治 GitHub 区分大小写导致的 404。
    4. 修正了 VERSION 变量定义缺失导致的 NameError。
=============================================================================
"""

import os
import sys
import requests
import feedparser
import time
import re
import subprocess
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup

# ==================== 物理配置区域 ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

VERSION = "V2.2.8-GatedStable" # 变量定义前置，彻底杜绝 NameError

KTN_RSS_URL = "https://kill-the-newsletter.com/feeds/uwgwyb1cnivki39x.xml"
LOCAL_BACKUP_XML = os.path.join(os.environ.get("AES_OUT_DIR", BASE_DIR), "uwgwyb1cnivki39x.xml")

PROXY_SERVER = "http://127.0.0.1:29758"
PROXIES = {
    "http": PROXY_SERVER,
    "https": PROXY_SERVER
}
# ======================================================

def clean_text_noise(text):
    if not text: return ""
    cleaned = text.replace('\ufffd', '').replace('\u0000', '')
    cleaned = re.sub(r'\?{2,}', '', cleaned)
    return re.sub(r'\s+', ' ', cleaned).strip()

def sanitize_filename(name):
    if not name: return "unknown"
    s = name.replace('"', '').replace("'", '').replace('“', '').replace('”', '').strip()
    s = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fa5]+', '_', s)
    return s.lower().strip('_')

def extract_scholar_keyword(html_body):
    text = html_body.get_text()
    zh_match = re.search(r'因为您关注了\s*\[(.*?)\]\s*的新搜索结果', text)
    if zh_match: return zh_match.group(1).strip()
    en_match = re.search(r'following new results for\s*\[(.*?)\]', text)
    if en_match: return en_match.group(1).strip()
    return None

def parse_single_mail(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    keyword = extract_scholar_keyword(soup)
    source_type = "Google Scholar"
    
    if not keyword:
        keyword = "Unknown_Source"
        source_type = "External"

    # 🟢 进门级核心净化：在解析出关键词的第一时间，粉碎所有干扰的脏双引号，防止向下游传导
    keyword = keyword.replace('"', '').replace("'", '').replace('“', '').replace('”', '').strip()
    # 消除连续的双空格，将其规范为单空格
    keyword = re.sub(r'\s+', ' ', keyword)

    articles = []
    links = soup.find_all('a', href=True)
    
    for link in links:
        href = link['href']
        if "scholar.google.com/scholar_url" in href or "scholar.google.com/scholar?" in href:
            try:
                title_text = clean_text_noise(link.get_text())
                if not title_text or title_text.lower() in ["[pdf]", "[html]", "获取全文", "cites"]:
                    continue
                
                raw_url = href
                if "scholar_url?" in href:
                    parsed_url = urlparse(href)
                    qs = parse_qs(parsed_url.query)
                    if 'url' in qs: raw_url = qs['url'][0]
                
                parent_text = ""
                p_tag = link.find_parent(['p', 'div'])
                if p_tag: parent_text = clean_text_noise(p_tag.get_text())

                articles.append({
                    "title": title_text,
                    "url": raw_url,
                    "description": parent_text
                })
            except Exception:
                continue
                
    return keyword, source_type, articles

def write_channel_xml(keyword, source_type, articles):
    if not articles:
        return None
        
    pub_date_str = datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
    rss_items = []
    
    for art in articles:
        item_xml = f"""        <item>
            <title><![CDATA[{art['title']}]]></title>
            <link>{art['url']}</link>
            <guid isPermaLink="true">{art['url']}</guid>
            <pubDate>{art.get('pubDate', pub_date_str)}</pubDate>
            <description><![CDATA[📡 AES-INTEL 细分源监测 [来源: {keyword} @ {source_type}]<br><br><b>文献标题:</b> {art['title']}<br><b>上下文摘要:</b> {art['description'] if art['description'] else '暂无摘要'}<br><b>源链接:</b> <a href="{art['url']}">点击跳转物理原文</a>]]></description>
        </item>"""
        rss_items.append(item_xml)

    safe_name = sanitize_filename(keyword)
    filename = f"ktn_{safe_name}.xml"
    out_dir = os.environ.get("AES_OUT_DIR", BASE_DIR)
    output_path = os.path.join(out_dir, filename)
    
    # 重新规范标准化输出
    display_title = f'KTN_"{keyword}" @ {source_type}'
    
    rss_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
    <channel>
        <title>{display_title}</title>
        <link>https://github.com/zorba123456/aes-feeds</link>
        <description>动态提纯通道: {display_title}</description>
        <lastBuildDate>{pub_date_str}</lastBuildDate>
        {"".join(rss_items)}
    </channel>
</rss>"""

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(rss_xml)
    print(f"  ├─ ✅ 物理映射存盘成功: {filename} -> ({display_title})")
    return filename, display_title

def generate_opml_directory(channel_meta_list):
    if not channel_meta_list:
        return
        
    opml_path = os.path.join(os.environ.get("AES_OUT_DIR", BASE_DIR), "ktn_channels_directory.opml")
    
    outline_items = []
    for filename, display_title in channel_meta_list:
        safe_title = display_title.replace('"', '&quot;')
        raw_github_url = f"https://raw.githubusercontent.com/zorba123456/aes-feeds/main/{filename}"
        
        item = f'            <outline text="{safe_title}" title="{safe_title}" type="rss" xmlUrl="{raw_github_url}" htmlUrl="https://github.com/zorba123456/aes-feeds"/>'
        outline_items.append(item)
        
    opml_content = f"""<?xml version="1.0" encoding="utf-8"?>
<opml version="2.0">
    <head>
        <title>AES-INTEL KTN 谷歌学术细分源总目录</title>
    </head>
    <body>
        <outline text="AES-INTEL 谷歌学术情报网" title="AES-INTEL 谷歌学术情报网">
{"\n".join(outline_items)}
        </outline>
    </body>
</opml>"""

    with open(opml_path, 'w', encoding='utf-8') as f:
        f.write(opml_content)
    print(f"📦 [OPML 构建器] 成功存盘总目录文件: ktn_channels_directory.opml")

def main():
    print("=" * 65)
    print(f"🚀 启动 KTN 精准分流管线 ({VERSION})...")
    print(f"📂 工作目录: {BASE_DIR}")
    print("=" * 65)

    feed_text = ""
    try:
        response = requests.get(KTN_RSS_URL, proxies=PROXIES, timeout=30)
        if response.status_code == 200:
            feed_text = response.text
            with open(LOCAL_BACKUP_XML, 'w', encoding='utf-8') as f:
                f.write(feed_text)
    except Exception as e:
        print(f"⚠️ 网络拉取异常: {e}")
    
    if not feed_text and os.path.exists(LOCAL_BACKUP_XML):
        with open(LOCAL_BACKUP_XML, 'r', encoding='utf-8') as f:
            feed_text = f.read()

    if not feed_text:
        print("❌ 物理异常：无法获取线上流且无本地备份，KTN 退出。")
        print("[REPORT] CHANNEL=KTN ITEM=master_feed COUNT=0 STATUS=FAIL")
        return

    feed = feedparser.parse(feed_text)
    master_channels = {}

    for entry in feed.entries:
        html_content = entry.content[0].value if 'content' in entry else entry.get('summary', '')
        if not html_content: continue
            
        mail_date = entry.get('published', datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT'))
        keyword, source_type, extracted_articles = parse_single_mail(html_content)
        
        if not extracted_articles: continue
            
        for art in extracted_articles: art['pubDate'] = mail_date
            
        key_bucket = (keyword, source_type)
        if key_bucket not in master_channels:
            master_channels[key_bucket] = []
        master_channels[key_bucket].extend(extracted_articles)

    print(f"📡 分析出当前混合池中包含 {len(master_channels)} 个明确的监测对象")

    channel_meta_list = []
    for (keyword, source_type), articles in master_channels.items():
        res = write_channel_xml(keyword, source_type, articles)
        if res:
            filename, display_title = res
            channel_meta_list.append((filename, display_title))
            # 🟢 完美修复：此时输出的 ITEM 字段将是百分百纯净、无空格多余引号的标准化字段
            print(f"[REPORT] CHANNEL=KTN ITEM={keyword} COUNT={len(articles)} STATUS=SUCCESS")
        else:
            print(f"[REPORT] CHANNEL=KTN ITEM={keyword} COUNT=0 STATUS=FAIL")

    if channel_meta_list:
        generate_opml_directory(channel_meta_list)

    print("\n📤 正在自动推送细分流与总目录到 GitHub...")
    custom_env = os.environ.copy()
    custom_env["HTTP_PROXY"] = PROXY_SERVER
    custom_env["HTTPS_PROXY"] = PROXY_SERVER
    
    try:
        subprocess.run(["git", "add", "-A"], cwd=BASE_DIR, check=True)
        commit_msg = f"Auto-Update KTN Target-Channels & OPML Directory: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=BASE_DIR, check=True)
        subprocess.run(["git", "push"], cwd=BASE_DIR, env=custom_env, check=True)
        print("🚀 GitHub 自动化网络数据同步成功！")
    except subprocess.CalledProcessError:
        print(f"ℹ️ 发布管线返回: 无变更或推送被跳过。")

if __name__ == "__main__":
    main()