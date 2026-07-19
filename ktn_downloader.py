#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
Project: lit_auto_pipeline (aes-intel platform)
File: ktn_downloader.py
Version: V2.3.0-MultiFetchGuard
Description:
    1. 彻底剔除关键词提取阶段的多重双引号噪声，确保呈现标准的 KTN_"关键词" 格式。
    2. 修复上游 [REPORT] 报盘中 keyword 携带半截引号导致入库解析错位的硬伤。
    3. 物理文件名严格锁定小写（ktn_*.xml），彻底根治 GitHub 区分大小写导致的 404。
    4. 修正了 VERSION 变量定义缺失导致的 NameError。
    5. 兼容 scholar.google.com.hk 等区域的 scholar_url 链接，修复 blepharoplasty 等子源缺失。
    6. RSS 条目标题 "keyword - new results" 作为关键词兜底；OPML 合并磁盘已有 ktn_*.xml。
    7. 母流拉取：直连优先 + 代理兜底 + 60s 超时 + 退避重试；备份过期 STALE 禁止 push。
=============================================================================
"""

import os
import sys
import glob
import socket
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

VERSION = "V2.3.0-MultiFetchGuard"

KTN_RSS_URL = "https://kill-the-newsletter.com/feeds/uwgwyb1cnivki39x.xml"
LOCAL_BACKUP_XML = os.path.join(os.environ.get("AES_OUT_DIR", BASE_DIR), "uwgwyb1cnivki39x.xml")

PROXY_HOST = "127.0.0.1"
PROXY_PORT = 29758
PROXY_SERVER = f"http://{PROXY_HOST}:{PROXY_PORT}"
PROXIES = {"http": PROXY_SERVER, "https": PROXY_SERVER}

FETCH_TIMEOUT = 60
MIN_FEED_BYTES = 100_000
BACKUP_STALE_SECS = 3600
MAX_FETCH_ATTEMPTS = 3
RETRY_DELAYS = (5, 10, 20)
FETCH_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AES-INTEL/2.0)"}
# ======================================================

def proxy_port_open(host=PROXY_HOST, port=PROXY_PORT, timeout=2):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((host, port)) == 0
    finally:
        sock.close()

def _fetch_once(use_proxy, timeout=FETCH_TIMEOUT):
    kwargs = {"timeout": timeout, "headers": FETCH_HEADERS}
    if use_proxy:
        kwargs["proxies"] = PROXIES
    response = requests.get(KTN_RSS_URL, **kwargs)
    if response.status_code == 200 and len(response.text) >= MIN_FEED_BYTES:
        return response.text, None
    return None, f"HTTP {response.status_code} size={len(response.text)}"

def fetch_ktn_feed():
    """直连优先、代理兜底，带退避重试。返回 (feed_text, meta)。"""
    routes = [("direct", False)]
    if proxy_port_open():
        routes.append(("proxy", True))
    else:
        print(f"ℹ️ 代理 {PROXY_HOST}:{PROXY_PORT} 未监听，仅尝试直连")

    errors = []
    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        for route_name, use_proxy in routes:
            try:
                text, err = _fetch_once(use_proxy)
                if text:
                    print(
                        f"✅ 母流拉取成功: route={route_name} "
                        f"attempt={attempt}/{MAX_FETCH_ATTEMPTS} size={len(text)}"
                    )
                    return text, {
                        "fresh": True,
                        "route": route_name,
                        "attempt": attempt,
                        "backup_age_secs": 0,
                    }
                errors.append(f"{route_name}: {err}")
            except Exception as exc:
                errors.append(f"{route_name}: {type(exc).__name__}: {exc}")
        if attempt < MAX_FETCH_ATTEMPTS:
            delay = RETRY_DELAYS[attempt - 1]
            if any("429" in e for e in errors):
                delay = max(delay, 30)
            print(f"⏳ 母流拉取第 {attempt} 轮失败，{delay}s 后重试...")
            time.sleep(delay)

    print("⚠️ 母流全部拉取路径失败:")
    for err in errors:
        print(f"   - {err}")

    if not os.path.exists(LOCAL_BACKUP_XML):
        return None, {"fresh": False, "stale": True, "backup_age_secs": None, "errors": errors}

    backup_age = time.time() - os.path.getmtime(LOCAL_BACKUP_XML)
    with open(LOCAL_BACKUP_XML, "r", encoding="utf-8") as f:
        backup_text = f.read()
    stale = backup_age > BACKUP_STALE_SECS
    age_h = backup_age / 3600
    print(
        f"⚠️ 回退本地备份 (age={age_h:.1f}h, stale={'是' if stale else '否'})"
    )
    return backup_text, {
        "fresh": False,
        "stale": stale,
        "backup_age_secs": backup_age,
        "errors": errors,
    }

def master_report_status(fetch_meta):
    if fetch_meta.get("fresh"):
        return "SUCCESS"
    if fetch_meta.get("stale"):
        return "STALE"
    return "DEGRADED"

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
    en_match = re.search(r'following new results for\s*\[(.*?)\]', text, re.IGNORECASE)
    if en_match: return en_match.group(1).strip()
    return None

def keyword_from_entry_title(entry_title):
    if not entry_title:
        return None
    title = entry_title.strip()
    m = re.match(r'^["\u201c\u201d]?(.*?)["\u201c\u201d]?\s*-\s*new results\s*$', title, re.IGNORECASE)
    if m:
        return m.group(1).strip().strip('"').strip()
    return None

def is_scholar_article_link(href):
    if not href:
        return False
    href_lower = href.lower()
    if "scholar.google" not in href_lower:
        return False
    return "scholar_url?" in href_lower or "/scholar?" in href_lower

def parse_single_mail(html_content, entry_title=None):
    soup = BeautifulSoup(html_content, 'html.parser')
    keyword = extract_scholar_keyword(soup)
    source_type = "Google Scholar"
    
    if not keyword:
        keyword = "Unknown_Source"
        source_type = "External"

    title_kw = keyword_from_entry_title(entry_title)
    if title_kw and (not keyword or keyword == "Unknown_Source"):
        keyword = title_kw
        source_type = "Google Scholar"

    # 🟢 进门级核心净化：在解析出关键词的第一时间，粉碎所有干扰的脏双引号，防止向下游传导
    keyword = keyword.replace('"', '').replace("'", '').replace('“', '').replace('”', '').strip()
    # 消除连续的双空格，将其规范为单空格
    keyword = re.sub(r'\s+', ' ', keyword)

    articles = []
    links = soup.find_all('a', href=True)
    
    for link in links:
        href = link['href']
        if is_scholar_article_link(href):
            try:
                title_text = clean_text_noise(link.get_text())
                if not title_text or title_text.lower() in ["[pdf]", "[html]", "获取全文", "cites"]:
                    continue
                
                # 过滤谷歌学术邮件中的快讯关键字搜索链接与退订管理链接
                title_clean = title_text.strip().strip('[]"“’”')
                if title_clean.lower() == keyword.lower() or title_clean.lower() in ["cancel alert", "取消快讯"]:
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
        safe_url = art['url'].replace('&', '&amp;')
        safe_desc = (art['description'] or '暂无摘要').replace('&', '&amp;')
        item_xml = f"""        <item>
            <title><![CDATA[{art['title']}]]></title>
            <link>{safe_url}</link>
            <guid isPermaLink="true">{safe_url}</guid>
            <pubDate>{art.get('pubDate', pub_date_str)}</pubDate>
            <description><![CDATA[📡 AES-INTEL 细分源监测 [来源: {keyword} @ {source_type}]<br><br><b>文献标题:</b> {art['title']}<br><b>上下文摘要:</b> {safe_desc}<br><b>源链接:</b> <a href="{safe_url}">点击跳转物理原文</a>]]></description>
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

def collect_existing_channel_meta():
    out_dir = os.environ.get("AES_OUT_DIR", BASE_DIR)
    meta = []
    for path in glob.glob(os.path.join(out_dir, "ktn_*.xml")):
        filename = os.path.basename(path)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            m = re.search(r'<title>([^<]+)</title>', content)
            display_title = m.group(1).strip() if m else filename
            meta.append((filename, display_title))
        except Exception:
            continue
    return meta

def merge_channel_meta(channel_meta_list):
    merged = {fn: dt for fn, dt in channel_meta_list}
    for fn, dt in collect_existing_channel_meta():
        merged.setdefault(fn, dt)
    return sorted(merged.items(), key=lambda x: x[1].lower())

def generate_opml_directory(channel_meta_list):
    channel_meta_list = merge_channel_meta(channel_meta_list)
    if not channel_meta_list:
        return
        
    opml_path = os.path.join(os.environ.get("AES_OUT_DIR", BASE_DIR), "ktn_channels_directory.opml")
    
    outline_items = []
    for filename, display_title in channel_meta_list:
        safe_title = display_title.replace('"', '&quot;')
        raw_github_url = f"https://raw.githubusercontent.com/zorba123456/aes-feeds/main/{filename}"
        
        item = f'            <outline text="{safe_title}" title="{safe_title}" type="rss" xmlUrl="{raw_github_url}" htmlUrl="https://github.com/zorba123456/aes-feeds"/>'
        outline_items.append(item)
        
    outline_body = "\n".join(outline_items)
    opml_content = f'''<?xml version="1.0" encoding="utf-8"?>
<opml version="2.0">
    <head>
        <title>AES-INTEL KTN 谷歌学术细分源总目录</title>
    </head>
    <body>
        <outline text="AES-INTEL 谷歌学术情报网" title="AES-INTEL 谷歌学术情报网">
{outline_body}
        </outline>
    </body>
</opml>'''

    with open(opml_path, 'w', encoding='utf-8') as f:
        f.write(opml_content)
    print(f"📦 [OPML 构建器] 成功存盘总目录文件: ktn_channels_directory.opml")

def main():
    print("=" * 65)
    print(f"🚀 启动 KTN 精准分流管线 ({VERSION})...")
    print(f"📂 工作目录: {BASE_DIR}")
    print("=" * 65)

    feed_text, fetch_meta = fetch_ktn_feed()
    if not feed_text:
        print("❌ 物理异常：无法获取线上流且无本地备份，KTN 退出。")
        print("[REPORT] CHANNEL=KTN ITEM=master_feed COUNT=0 STATUS=FAIL")
        return

    if fetch_meta.get("fresh"):
        with open(LOCAL_BACKUP_XML, "w", encoding="utf-8") as f:
            f.write(feed_text)

    master_status = master_report_status(fetch_meta)
    print(f"[REPORT] CHANNEL=KTN ITEM=master_feed COUNT=1 STATUS={master_status}")

    feed = feedparser.parse(feed_text)
    master_channels = {}

    for entry in feed.entries:
        html_content = entry.content[0].value if 'content' in entry else entry.get('summary', '')
        if not html_content: continue
            
        mail_date = entry.get('published', datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT'))
        keyword, source_type, extracted_articles = parse_single_mail(
            html_content, entry.get('title', '')
        )
        
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
            print(
                f"[REPORT] CHANNEL=KTN ITEM={keyword} COUNT={len(articles)} "
                f"STATUS={master_status if master_status != 'FAIL' else 'FAIL'}"
            )
        else:
            print(f"[REPORT] CHANNEL=KTN ITEM={keyword} COUNT=0 STATUS=FAIL")

    if channel_meta_list:
        generate_opml_directory(channel_meta_list)

    if master_status == "STALE":
        print("\n🛑 母流备份已过期 (STALE)，跳过 GitHub 推送以防假更新。")
        return

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