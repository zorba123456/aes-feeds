#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
Project: lit_auto_pipeline (aes-intel platform)
File: aes-feeds/cma_downloader.py
Version: V6.5.0 (CMA 30条滚动历史去重版)
Description:
    1. 固化对齐黄金原版 da0b950 的核心 Bs4 选择器与 pageNo 翻页参数。
    2. 保持原生直连模式（无网页代理），确保完全攻克 net::ERR_CONNECTION_CLOSED 闪退硬伤。
    3. 隔离全局环境变量，确保 Git 物理推送挂载本地代理、网页抓取使用纯净国内直连。
    4. [V6.5.0 新增] 引入 cma_dedup_log.json 持久化去重，实现与 CNKI 一致的
       30条滚动历史去重机制，防止大量历史/优先出版文献一次性刷爆 Inoreader。
=============================================================================
"""
import os
import time
import json
import hashlib
import subprocess
import re
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

__version__ = "6.5.0-滚动历史去重版"

# ==================== 物理配置区域 ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROXY_SERVER = "http://127.0.0.1:29758"
LOG_FILE_PATH = os.path.join(BASE_DIR, "cma_dedup_log.json")
DEDUP_EXPIRE_DAYS = 180
# ======================================================

def push_to_github():
    print("\n📤 启动 GitHub 自动同步 (CMA Feeds & Dedup Log)...")
    custom_env = os.environ.copy()
    custom_env["HTTP_PROXY"] = PROXY_SERVER
    custom_env["HTTPS_PROXY"] = PROXY_SERVER
    try:
        # 同时提交 XML 和去重日志
        subprocess.run("git add cma_*.xml cma_dedup_log.json", cwd=BASE_DIR, check=True, shell=True)
        commit_msg = f"Auto-update CMA feeds: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=BASE_DIR, check=True)
        subprocess.run(["git", "push"], cwd=BASE_DIR, env=custom_env, check=True)
        print("✅ 同步成功！CMA 数据已成功推送至 aes-feeds 独立仓库。")
    except subprocess.CalledProcessError:
        print("ℹ️ 未检测到新文献或同步无变动，跳过推送。")

def _compute_links_fingerprint(links: list) -> str:
    """计算文章链接列表的 MD5 指纹，包含 schema_version v3 确保升级时强制重新写盘"""
    schema_version = "v3"
    content = "|".join(sorted(links)) + f"|schema:{schema_version}"
    return hashlib.md5(content.encode('utf-8')).hexdigest()

def _read_existing_fingerprint(output_path: str) -> str:
    """从已存盘的 XML 文件中读取上次的 MD5 指纹注释"""
    try:
        with open(output_path, 'r', encoding='utf-8') as f:
            first_lines = f.read(512)
        m = re.search(r'<!--CMA-FINGERPRINT:([a-f0-9]{32})-->', first_lines)
        return m.group(1) if m else ""
    except Exception:
        return ""

def load_dedup_log():
    """加载去重记录"""
    if os.path.exists(LOG_FILE_PATH):
        try:
            with open(LOG_FILE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_dedup_log(log_data):
    """保存去重记录并清理过期的Hash"""
    now = time.time()
    expire_secs = DEDUP_EXPIRE_DAYS * 24 * 3600
    cleaned_data = {}
    for k, v in log_data.items():
        ts_val = v.get("timestamp") or 0
        if (now - ts_val) < expire_secs:
            cleaned_data[k] = {
                "title": v.get("title", ""),
                "timestamp": ts_val
            }
    with open(LOG_FILE_PATH, 'w', encoding='utf-8') as f:
        json.dump(cleaned_data, f, ensure_ascii=False, indent=2)

def parse_blocks_dict(soup, base_url, is_priority=False):
    """
    解析文献特征块，提取标题、链接、期数、出版日期、作者和摘要。
    is_priority: 是否是优先出版的文献，若是则在前缀加 [优先出版]
    返回: list of dict
    """
    articles = []
    seen_links = set()
    
    blocks = soup.select('div.s_searchResult_li, li.s_searchResult_li')
    if blocks:
        for node in blocks:
            title = ""
            link = ""
            
            a_tags = node.select('a[href*="/cmaid/"], a[href*="/article/"]')
            for a in a_tags:
                t = a.get('title') or a.get_text(" ", strip=True)
                if t and len(t) > 2 and not any(kw in t for kw in ["下载全文", "阅读全文", "PDF下载", "在线客服"]):
                    title = t
                    link = a.get('href', '')
                    break  
            
            if not title or not link:
                continue
                
            if link.startswith('/'):
                link = "https://www.yiigle.com" + link
            elif not link.startswith('http'):
                link = base_url

            if link in seen_links:
                continue
            seen_links.add(link)

            node_text = node.get_text(" ", strip=True)
            
            pub_date_gmt = ""
            display_date = "未知时间"
            date_match = re.search(r'(\d{4}-\d{2}-\d{2})', node_text)
            if date_match:
                display_date = date_match.group(1)
                try:
                    dt = datetime.strptime(display_date, "%Y-%m-%d")
                    pub_date_gmt = dt.strftime("%a, %d %b %Y 00:00:00 GMT")
                except ValueError:
                    pass
            
            issue_info = "最新优先发表" if is_priority else "未知期数"
            issue_match = re.search(r'(\d{4}年\d+卷\d+期)', node_text)
            if not issue_match:
                issue_match = re.search(r'(\d{4},\s*\d+\(\d+\))', node_text)
            if issue_match:
                issue_info = issue_match.group(1)

            if is_priority:
                enhanced_title = f"[优先出版] {title}"
            else:
                enhanced_title = title

            authors = "本刊编辑部"
            author_tags = node.select('.author_sec a.linkuser') or node.select('a.linkuser')
            if author_tags:
                authors = ", ".join([auth.get_text(strip=True) for auth in author_tags if auth.get_text(strip=True)])
            
            abstract = "无摘要"
            abs_tag = node.select_one('.s_searchResult_li_info')
            if abs_tag:
                abstract = abs_tag.get_text(strip=True)

            desc = f"<b>期数：</b>{issue_info}<br><b>出版日期：</b>{display_date}<br><b>作者：</b>{authors}<br><br><b>摘要：</b>{abstract}"
            
            articles.append({
                "title": enhanced_title,
                "link": link,
                "author": authors,
                "pubDate": pub_date_gmt,
                "description": desc
            })
    else:
        # 🟢 严格维持原版兜底搜捕
        all_a_tags = soup.select('a[href*="/cmaid/"], a[href*="/article/"]')
        for a_tag in all_a_tags:
            title = a_tag.get('title') or a_tag.get_text(" ", strip=True)
            link = a_tag.get('href', '') or ''
            
            if not title or len(title.strip()) < 2 or any(kw in title for kw in ["下载全文", "阅读全文", "PDF下载", "在线客服"]):
                continue
                
            if link.startswith('/'):
                link = "https://www.yiigle.com" + link
            elif not link.startswith('http'):
                link = base_url

            if link in seen_links:
                continue
            seen_links.add(link)

            if is_priority:
                enhanced_title = f"[优先出版] {title}"
                desc = "<b>期数：</b>最新优先发表<br><b>作者：</b>本刊编辑部<br><br><b>摘要：</b>无摘要"
            else:
                enhanced_title = title
                desc = "<b>期数：</b>最新捕获<br><b>作者：</b>本刊编辑部<br><br><b>摘要：</b>无摘要"

            articles.append({
                "title": enhanced_title,
                "link": link,
                "author": "本刊编辑部",
                "pubDate": "",
                "description": desc
            })
            
    return articles

def fetch_cma_journal(playwright_context, base_url, journal_name, output_filename, dedup_log):
    print(f"\n📡 正在抓取: {journal_name}")
    
    # 1. 加载并解析本地已有的 XML feed，补充进入 dedup_log 种子，并提取已有的 items
    output_path = os.path.abspath(os.path.join(BASE_DIR, output_filename))
    existing_items = []
    existing_links = set()
    if os.path.exists(output_path):
        try:
            import xml.etree.ElementTree as ET
            tree = ET.parse(output_path)
            root = tree.getroot()
            for item_el in root.findall(".//item"):
                t_el = item_el.find("title")
                l_el = item_el.find("link")
                d_el = item_el.find("description")
                p_el = item_el.find("pubDate")
                a_el = item_el.find("author")
                
                link = l_el.text if l_el is not None else ""
                if link:
                    existing_items.append({
                        "title": t_el.text if t_el is not None else "",
                        "link": link,
                        "description": d_el.text if d_el is not None else "",
                        "pubDate": p_el.text if p_el is not None else "",
                        "author": a_el.text if a_el is not None else ""
                    })
                    existing_links.add(link)
                    # 种子注入：已在 XML 里的视为已读，防止重复被拉取
                    if link not in dedup_log:
                        dedup_log[link] = {
                            "title": t_el.text if t_el is not None else "",
                            "timestamp": time.time()
                        }
        except Exception as e:
            print(f"  ⚠️ 读取现有 XML 失败: {e}")

    page = playwright_context.new_page()
    
    # 2. 抓取第一页常规“期刊目录”
    page_url = base_url
    print(f"  ├─ 探测期刊目录... -> {page_url}")
    
    try:
        page.goto(page_url, wait_until="networkidle", timeout=45000)
        time.sleep(3.0) 
        html_content = page.content()
        soup = BeautifulSoup(html_content, 'html.parser')
    except Exception as e:
        print(f"❌ 页面加载或网络渲染超时: {e}")
        page.close()
        return False

    normal_items = parse_blocks_dict(soup, base_url, is_priority=False)
    print(f"  ├─ 期刊目录抓取完成，成功捕获 {len(normal_items)} 篇文献")

    # 3. 探测并点击“优先出版”Tab
    priority_items = []
    try:
        publish_tab = page.locator('.publish_sec span:has-text("优先出版")')
        if publish_tab.count() == 0:
            publish_tab = page.locator('span:has-text("优先出版")').first
        
        if publish_tab.count() > 0 and publish_tab.is_visible():
            print("  ├─ 发现 [优先出版] Tab，正在点击切换...")
            publish_tab.click()
            time.sleep(3.0)
            
            html_content_priority = page.content()
            soup_priority = BeautifulSoup(html_content_priority, 'html.parser')
            
            priority_items = parse_blocks_dict(soup_priority, base_url, is_priority=True)
            print(f"  ├─ [优先出版] 抓取完成，成功捕获 {len(priority_items)} 篇文献")
        else:
            print("  ├─ 未发现 [优先出版] Tab，跳过。")
    except Exception as e:
        print(f"  ⚠️ 尝试点击 [优先出版] 发生异常: {e}")
        
    page.close()

    # 合并抓取结果
    all_scraped = []
    seen_scraped_links = set()
    for item in normal_items + priority_items:
        link = item['link']
        if link not in seen_scraped_links:
            all_scraped.append(item)
            seen_scraped_links.add(link)

    # 4. 判断并识别真正的新增文献
    new_items = []
    for item in all_scraped:
        link = item['link']
        if link not in dedup_log:
            new_items.append(item)

    # 特殊处理：如果 dedup_log 为空（首次加载且没有任何去重信息），
    # 为防止一下子在 RSS 中暴增上百条旧数据，把除前 30 条以外的所有内容全部做“已读”静默记录
    is_first_init = (len(dedup_log) <= len(existing_links))
    if is_first_init and len(new_items) > 30:
        print(f"  ├─ ⚠️ 首次初始化抓取，检测到大量的优先出版数据 ({len(new_items)} 篇)。")
        print("  ├─ 自动截取最新的 20 篇作为初始推送，其余静默标记为已读。")
        # 按原本顺序，排在前面的（网页渲染靠前的）最新
        silent_read_items = new_items[20:]
        new_items = new_items[:20]
        for item in silent_read_items:
            dedup_log[item['link']] = {
                "title": item['title'],
                "timestamp": time.time() - 3600  # 稍微错开时间
            }

    if new_items:
        print(f"  ├─ 发现真正新增文献 {len(new_items)} 篇 (未曾在历史中推送)")
        for item in new_items:
            dedup_log[item['link']] = {
                "title": item['title'],
                "timestamp": time.time()
            }
    else:
        print("  ├─ 未发现新文献")

    # 5. 合并新项和已有历史项 (新项置顶)
    merged_items = []
    seen_merged = set()
    for item in new_items:
        link = item['link']
        if link not in seen_merged:
            merged_items.append(item)
            seen_merged.add(link)
    for item in existing_items:
        link = item['link']
        if link not in seen_merged:
            merged_items.append(item)
            seen_merged.add(link)

    # 截取最新的前 30 条作为 Feed 最终输出
    merged_items = merged_items[:30]

    if not merged_items:
        print(f"[REPORT] CHANNEL=CMA ITEM={journal_name} COUNT=0 STATUS=FAIL")
        return False

    # 6. 计算最终生成的 Feed 链接指纹
    final_links = [item['link'] for item in merged_items]
    new_fingerprint = _compute_links_fingerprint(final_links)
    old_fingerprint = _read_existing_fingerprint(output_path)

    if new_fingerprint == old_fingerprint:
        print(f"  ├─ ✅ XML 内容无变化（指纹一致: {new_fingerprint[:8]}...），跳过写盘。")
        print(f"[REPORT] CHANNEL=CMA ITEM={journal_name} COUNT=0 STATUS=SUCCESS")
        return False

    print(f"  ├─ 🔄 检测到内容变化（{old_fingerprint[:8] or 'NEW'} → {new_fingerprint[:8]}），写盘更新...")

    tz = timezone(timedelta(hours=8))
    pub_date_str = datetime.now(tz).strftime("%a, %d %b %Y %H:%M:%S +0800")
    display_title = f"KTN_\"{journal_name}\" @ CMA"
    
    rss_items_xml = []
    for item in merged_items:
        pub_date_xml = f"<pubDate>{item['pubDate']}</pubDate>" if item['pubDate'] else ""
        item_xml = f"""
        <item>
            <title><![CDATA[{item['title']}]]></title>
            <link>{item['link']}</link>
            <guid isPermaLink="false">{item['link']}</guid>
            <author><![CDATA[{item['author']}]]></author>
            {pub_date_xml}
            <description><![CDATA[{item['description']}]]></description>
        </item>"""
        rss_items_xml.append(item_xml)

    rss_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<!--CMA-FINGERPRINT:{new_fingerprint}-->
<rss version="2.0">
    <channel>
        <title>{display_title}</title>
        <link>{base_url}</link>
        <description>{journal_name} - 自动聚合源</description>
        <lastBuildDate>{pub_date_str}</lastBuildDate>
        {"".join(rss_items_xml)}
    </channel>
</rss>"""

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(rss_xml)
    
    print(f"  └─ ✅ 成功存盘: {output_path}，RSS 结构闭合。")
    print(f"[REPORT] CHANNEL=CMA ITEM={journal_name} COUNT={len(new_items)} STATUS=SUCCESS")
    return True

if __name__ == "__main__":
    print("=" * 65)
    print(f"🚀 启动 CMA 中华医学会抓取管线 [{__version__}]")
    print(f"📂 锚定工作目录: {BASE_DIR}")
    print("=" * 65)
    
    targets = [
        {"name": "中华整形外科杂志", "url": "https://www.yiigle.com/Journal/ZHZXWKZZ", "filename": "cma_plastics.xml"},
        {"name": "中华皮肤科杂志", "url": "https://www.yiigle.com/Journal/ZHPFKZZ", "filename": "cma_dermatology.xml"},
        {"name": "中华医学美学美容杂志", "url": "https://www.yiigle.com/Journal/ZHYXMXMRZZ", "filename": "cma_aesthetics.xml"}
    ]
    
    updated_any = False
    dedup_log = load_dedup_log()
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="msedge", 
            headless=False,
            args=[
                '--disable-background-timer-throttling',
                '--disable-backgrounding-occluded-windows',
                '--disable-renderer-backgrounding'
            ]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        for target in targets:
            if fetch_cma_journal(context, target['url'], target['name'], target['filename'], dedup_log):
                updated_any = True
        browser.close()
        
    if updated_any:
        save_dedup_log(dedup_log)
        push_to_github()
        
    print("\n" + "=" * 65)