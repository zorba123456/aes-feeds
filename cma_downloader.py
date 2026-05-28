#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
Project: lit_auto_pipeline (aes-intel platform)
File: aes-feeds/cma_downloader.py
Version: V6.4.0 (增量指纹去重 + Git Glob 修复版)
Description:
    1. 固化对齐黄金原版 da0b950 的核心 Bs4 选择器与 pageNo 翻页参数。
    2. 保持原生直连模式（无网页代理），确保完全攻克 net::ERR_CONNECTION_CLOSED 闪退硬伤。
    3. 隔离全局环境变量，确保 Git 物理推送挂载本地代理、网页抓取使用纯净国内直连。
    4. [V6.4.0 新增] 文章链接列表 MD5 指纹对比：内容未变时跳过写文件和 commit，
       彻底消除因 lastBuildDate 时间戳每次变化导致的无意义爆炸式提交。
    5. [V6.4.0 修复] git add 改用 shell=True，确保 glob 通配符在子进程中正确展开。
=============================================================================
"""
import os
import time
import hashlib
import subprocess
import re
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

__version__ = "6.4.0-增量指纹去重版"

# ==================== 物理配置区域 ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROXY_SERVER = "http://127.0.0.1:29758"
# ======================================================

def push_to_github():
    print("\n📤 启动 GitHub 自动同步 (CMA Feeds)...")
    # 🟢 严格防污染环境：将本地代理仅作用于 Git 同步管线，确保国内直连抓取互不干扰
    custom_env = os.environ.copy()
    custom_env["HTTP_PROXY"] = PROXY_SERVER
    custom_env["HTTPS_PROXY"] = PROXY_SERVER
    try:
        # 🟢 [V6.4.0 修复] 使用 shell=True 确保 cma_*.xml glob 通配符被 Shell 正确展开
        subprocess.run("git add cma_*.xml", cwd=BASE_DIR, check=True, shell=True)
        commit_msg = f"Auto-update CMA feeds: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=BASE_DIR, check=True)
        subprocess.run(["git", "push"], cwd=BASE_DIR, env=custom_env, check=True)
        print("✅ 同步成功！CMA 数据已成功推送至 aes-feeds 独立仓库。")
    except subprocess.CalledProcessError:
        print("ℹ️ 未检测到新文献或同步无变动，跳过推送。")

def _compute_links_fingerprint(links: list) -> str:
    """计算文章链接列表的 MD5 指纹，用于判断内容是否真正变化"""
    content = "|".join(sorted(links))
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


def fetch_cma_journal(playwright_context, base_url, journal_name, output_filename):
    print(f"\n📡 正在抓取: {journal_name}")
    
    page = playwright_context.new_page()
    rss_items = []
    collected_links = []  # 🟢 [V6.4.0] 收集链接用于指纹对比
    page_num = 1
    
    while True:
        sep = "&" if "?" in base_url else "?"
        # 🟢 严格对齐黄金原版参数：是 pageNo= 而绝非 page=
        page_url = f"{base_url}{sep}pageNo={page_num}"
        print(f"  ├─ 探测第 {page_num} 页... -> {page_url}")
        
        try:
            # 🟢 严格保持 da0b950 的加载控制
            page.goto(page_url, wait_until="networkidle", timeout=45000)
            time.sleep(3.0) 
            html_content = page.content()
            soup = BeautifulSoup(html_content, 'html.parser')
        except Exception as e:
            print(f"❌ 页面加载或网络渲染超时: {e}")
            break

        # 🟢 严格恢复黄金原版的核心特征块搜捕选择器
        blocks = soup.select('div.s_searchResult_li, li.s_searchResult_li')
        valid_count = 0
        seen_links = set()

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
                collected_links.append(link)  # 🟢 记录链接

                node_text = node.get_text(" ", strip=True)
                
                pub_date_xml = ""
                display_date = "未知时间"
                date_match = re.search(r'(\d{4}-\d{2}-\d{2})', node_text)
                if date_match:
                    display_date = date_match.group(1)
                    try:
                        dt = datetime.strptime(display_date, "%Y-%m-%d")
                        pub_date_xml = f"<pubDate>{dt.strftime('%a, %d %b %Y 00:00:00 GMT')}</pubDate>"
                    except ValueError:
                        pass
                
                issue_info = "最新优先发表"
                issue_match = re.search(r'(\d{4}年\d+卷\d+期)', node_text)
                if not issue_match:
                    issue_match = re.search(r'(\d{4},\s*\d+\(\d+\))', node_text)
                if issue_match:
                    issue_info = issue_match.group(1)

                authors = "本刊编辑部"
                author_tags = node.select('.author_sec a.linkuser') or node.select('a.linkuser')
                if author_tags:
                    authors = ", ".join([auth.get_text(strip=True) for auth in author_tags if auth.get_text(strip=True)])
                
                abstract = "无摘要"
                abs_tag = node.select_one('.s_searchResult_li_info')
                if abs_tag:
                    abstract = abs_tag.get_text(strip=True)

                item_xml = f"""
        <item>
            <title><![CDATA[{title}]]></title>
            <link>{link}</link>
            <guid isPermaLink="false">{link}</guid>
            <author><![CDATA[{authors}]]></author>
            {pub_date_xml}
            <description><![CDATA[<b>期数：</b>{issue_info}<br><b>出版日期：</b>{display_date}<br><b>作者：</b>{authors}<br><br><b>摘要：</b>{abstract}]]></description>
        </item>"""
                rss_items.append(item_xml)
                seen_links.add(link)
                valid_count += 1
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
                collected_links.append(link)  # 🟢 记录链接（兜底路径）

                item_xml = f"""
        <item>
            <title><![CDATA[{title}]]></title>
            <link>{link}</link>
            <guid isPermaLink="false">{link}</guid>
            <author><![CDATA[本刊编辑部]]></author>
            <description><![CDATA[<b>期数：</b>最新捕获<br><b>作者：</b>本刊编辑部<br><br><b>摘要：</b>无摘要]]></description>
        </item>"""
                rss_items.append(item_xml)
                seen_links.add(link)
                valid_count += 1

        print(f"成功捕获 {valid_count} 篇真实文献")
        break 

    page.close()

    if not rss_items:
        print(f"[REPORT] CHANNEL=CMA ITEM={journal_name} COUNT=0 STATUS=FAIL")
        return False

    # 🟢 [V6.4.0] 指纹对比：只有文章链接集合真正改变时才写盘
    output_path = os.path.abspath(os.path.join(BASE_DIR, output_filename))
    new_fingerprint = _compute_links_fingerprint(collected_links)
    old_fingerprint = _read_existing_fingerprint(output_path)

    if new_fingerprint == old_fingerprint:
        print(f"  ├─ ✅ 内容无变化（指纹一致: {new_fingerprint[:8]}...），跳过写盘。")
        print(f"[REPORT] CHANNEL=CMA ITEM={journal_name} COUNT=0 STATUS=SUCCESS")
        return False  # 返回 False 表示无需提交

    print(f"  ├─ 🔄 检测到内容变化（{old_fingerprint[:8] or 'NEW'} → {new_fingerprint[:8]}），写盘更新...")

    tz = timezone(timedelta(hours=8))
    pub_date_str = datetime.now(tz).strftime("%a, %d %b %Y %H:%M:%S +0800")
    
    # 🟢 精准对齐融入大写 KTN_ 识别前缀，确保 Inoreader 完美过滤
    display_title = f"KTN_\"{journal_name}\" @ CMA"
    # 🟢 [V6.4.0] 在 XML 注释中嵌入指纹，供下次对比读取（不影响 RSS 解析）
    rss_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<!--CMA-FINGERPRINT:{new_fingerprint}-->
<rss version="2.0">
    <channel>
        <title>{display_title}</title>
        <link>{base_url}</link>
        <description>{journal_name} - 自动聚合源</description>
        <lastBuildDate>{pub_date_str}</lastBuildDate>
        {"".join(rss_items)}
    </channel>
</rss>"""

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(rss_xml)
    
    print(f"  └─ ✅ 成功存盘: {output_path}，RSS 结构闭合。")
    print(f"[REPORT] CHANNEL=CMA ITEM={journal_name} COUNT={len(rss_items)} STATUS=SUCCESS")
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
    
    with sync_playwright() as p:
        # 🟢 保持原生无网页代理直连有头模式，彻底根除 net::ERR_CONNECTION_CLOSED
        browser = p.chromium.launch(
            channel="msedge", 
            headless=False
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        for target in targets:
            if fetch_cma_journal(context, target['url'], target['name'], target['filename']):
                updated_any = True
        browser.close()
        
    if updated_any:
        push_to_github()
        
    print("\n" + "=" * 65)