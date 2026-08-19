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
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

__version__ = "5.5.0"

# ==================== 物理配置区域 ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROXY_SERVER = "http://127.0.0.1:29758"
# ======================================================


CAPTCHA_WAIT_SECS = 600

def wait_for_cloudflare(page, name):
    for _ in range(3):
        try:
            title = page.title()
        except Exception:
            title = ""
        if "Just a moment" not in title and "Cloudflare" not in title:
            return True
        time.sleep(1)
        
    print(f"⚠️ 触发安全验证: {name}")
    try:
        subtitle = f"正在抓取: {name}"
        script = f'display notification "{subtitle}" with title "LWW Cloudflare 验证" sound name "Glass"'
        subprocess.run(["osascript", "-e", script], check=False)
    except:
        pass

    try:
        subprocess.run(["osascript", "-e", 'tell application "Microsoft Edge" to activate'], check=False)
    except:
        pass

    print(f"⏳ 等待人工滑过验证 (最长等待 {CAPTCHA_WAIT_SECS // 60} 分钟)...")
    wait_start = time.time()
    
    while time.time() - wait_start < CAPTCHA_WAIT_SECS:
        try:
            title = page.title()
        except Exception:
            title = ""
        if "Just a moment" not in title and "Cloudflare" not in title:
            print("✅ 验证已通过！")
            time.sleep(2)
            return True
        time.sleep(2)
    print("❌ 超时！未完成人工验证。")
    return False

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

_MONTHS_FULL = r'(?:January|February|March|April|May|June|July|August|September|October|November|December)'
_MONTHS_ALL = r'(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)'


def parse_card_metadata(card_text, journal_name, title=""):
    """从 Ovid/LWW TOC 卡片文本提取 作者/期数/出版时间/页码。

    实测两种卡片元信息形态(PRS 官网, 2026-08-19):
      - latest/ahead(无期号):  "...{标题}{作者}Show More{Month D, YYYY}Article Action bar..."
               → 日期精确到天 (如 "August 17, 2026")，期数="Ahead of Print"
      - current/正式出版(有期号): "...{标题}{作者}Show More{期刊}. {Month} {Year}: {卷}({期}):{页码}Article..."
               → 日期精确到月 (如 "August 2026")，另含期号与页码

    旧实现只按 ':' 与 '.' 切 meta_line，对 latest 卡片整体失败 → 日期兜底成
    parse_to_rfc822 的当前时刻（生成时间），导致镜像 XML 出版时间成了"爬取时刻"。
    作者：卡片整体 = "{标题}{作者}Show More..."，作者 = 标题后、Show More 前那段。
    """
    authors = "Unknown Authors"
    # 作者区 = "Show More" 之前、去掉标题(若有)后的剩余
    pre_show_idx = card_text.find("Show More")
    pre = card_text[:pre_show_idx] if pre_show_idx > 0 else card_text
    if title and title in pre:
        # 去掉标题前缀(含其后可能紧跟的换行/空白)
        pre = pre.replace(title, "", 1).lstrip()
    pre = pre.strip()
    if pre:
        # 截断收尾于 ':'(期刊.期串) 或末尾空白
        pre = re.split(r'\s*:\s*', pre)[0].strip()
        pre = pre.rstrip(',')
        if pre:
            authors = pre

    issue = "Ahead of Print"
    pub_date = "Unknown Date"
    pages = ""

    # 形态①: current/正式出版 → "Month Year: 卷(期):页码"
    m_print = re.search(
        r'(%s)\s+(\d{4})\s*:\s*(\d+)\s*\(\s*(\d+)\s*\)\s*:\s*([0-9A-Za-z\-]+)' % _MONTHS_FULL,
        card_text, re.I)
    if m_print:
        month = m_print.group(1)
        pub_date = f"{month.capitalize()} {m_print.group(2)}"
        issue = f"Volume {m_print.group(3)}({m_print.group(4)})"
        pages = re.sub(r'\s*Article.*$', '', m_print.group(5)).strip().rstrip('.,')
        return authors, issue, pub_date, pages

    # 形态②: latest/ahead → "Show More {Month D, YYYY} Article"
    m_ahead = re.search(r'Show\s*More\s*(%s)\s+(\d{1,2}),\s+(\d{4})' % _MONTHS_ALL, card_text, re.I)
    if m_ahead:
        month = m_ahead.group(1)
        pub_date = f"{_canon_month(month)} {m_ahead.group(2)}, {m_ahead.group(3)}"
        return authors, issue, pub_date, pages

    return authors, issue, pub_date, pages


def _canon_month(tok: str) -> str:
    """把月份缩写/全拼统一成全拼首字母大写（如 'aug' -> 'August'）。"""
    full = {
        'jan': 'January', 'feb': 'February', 'mar': 'March', 'apr': 'April',
        'may': 'May', 'jun': 'June', 'jul': 'July', 'aug': 'August',
        'sep': 'September', 'oct': 'October', 'nov': 'November', 'dec': 'December',
    }
    t = tok.strip()[:3].lower()
    return full.get(t, tok)

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

def _set_per_page_and_maybe_paginate(page, url):
    """把 TOC 翻页栏的『每页条数』设到最大可见量。

    实测(LWW 官网, 2026-08-19):
      - 翻页栏是原生 <select>，选项 ['20','50','100']，select_option 可靠改选。
      - 页面 URL 用查询参数控制：`?pageSize={N}&page={M}`（翻页 <a> href 即此形态）。
      - 总量标记形如 "1 - 20 of 52 results"（current 有限；latest 是无限累加、数字无意义）。
      - current: 设 100 → 总量≤100 一次全量；总量>100 需再翻 1 页(第2页)补齐。
      - latest: 设 50 → 取第一页 50 条（覆盖单次更新量；不翻页、不看总量）。

    本函数只负责改选每页条数 + 判定是否需要翻第 2 页；翻页动作由调用方(scrape_toc_page)
    在抓完第 1 页后执行并把两页合并，避免"跳页后丢弃第 1 页"。
    返回 dict: {"paginate": bool, "target": int}——paginate=True 表示总量>target、需抓第 2 页。
    无 select（如视频页）返回 {"paginate": False, "target": None}。
    """
    try:
        sel = page.locator("select").first
        if sel.count() == 0:
            return {"paginate": False, "target": None}
        options = [o.text_content().strip() for o in sel.locator("option").all()]
        print(f"  [翻页] 每页条数下拉: {options}")
        # 读总量标记 "X - Y of N results"
        total = None
        try:
            body = page.locator("body").inner_text()
            m = re.search(r'(\d+)\s*[-\u2013]\s*\d+\s+of\s+(\d+)\s+results', body, re.I)
            if m:
                total = int(m.group(2))
                print(f"  [翻页] 总量标记: {m.group(0).strip()} (N={total})")
        except Exception:
            total = None

        is_current = "/toc/current" in url
        target = 100 if is_current else 50

        if str(target) in options:
            try:
                sel.select_option(value=str(target))
                page.wait_for_timeout(3500)
                print(f"  [翻页] 已改选每页 {target} 条")
            except Exception as e:
                print(f"  [翻页] select 改选失败: {e}")

        # current 且总量>target → 需翻第 2 页补齐
        need_paginate = bool(is_current and total is not None and total > target)
        if need_paginate:
            print(f"  [翻页] current 总量>{target}，需翻第 2 页补齐全量")
        return {"paginate": need_paginate, "target": target}
    except Exception as e:
        print(f"  [翻页] 处理异常(跳过,维持原状): {e}")
        return {"paginate": False, "target": None}


def journal_name_of_url(url):
    import re as _re
    m = _re.search(r'/(?:ovid\.com/jnls|journals\.lww\.com)/([^/]+)', url)
    return m.group(1) if m else "LWW"


def _extract_articles_from_page(page, journal_name, seen_urls, articles):
    """从当前页所有 .js-omni-hydrate-marker 解析条目，追加到 articles（按 url 去重）。"""
    markers = page.locator('.js-omni-hydrate-marker').all()
    added = 0
    for m in markers:
        props_str = m.get_attribute('data-hydrate-props')
        props = json.loads(props_str) if props_str else None
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
            try:
                curr = curr.locator('xpath=..')
            except Exception:
                break
            class_str = curr.get_attribute('class') or ""
            if 'omni-card-body' in class_str or 'collection-item' in class_str:
                card_text = curr.text_content() or ""
                break
        else:
            try:
                p2 = m.locator('xpath=../..')
                card_text = p2.text_content() or ""
            except Exception:
                card_text = ""

        authors, issue, pub_date, pages = parse_card_metadata(card_text, journal_name, title_val)
        articles.append({
            'title': title_val,
            'link': url_val,
            'an': an_val,
            'authors': authors,
            'issue': issue,
            'pub_date': pub_date,
            'pages': pages,
        })
        added += 1
    return added


def scrape_toc_page(page, url, journal_name):
    print(f"📡 Scraping TOC Page: {url}")
    page.goto(url, timeout=60000)
    if not wait_for_cloudflare(page, journal_name):
        return []
    time.sleep(5)

    # 改选每页条数 + 判定是否需翻第 2 页(current 总量>target)
    paginfo = _set_per_page_and_maybe_paginate(page, url)

    articles = []
    seen_urls = set()

    # 第 1 页
    n1 = _extract_articles_from_page(page, journal_name, seen_urls, articles)
    print(f"📦 第 1 页解析 {n1} 条")

    # current 总量>target → 翻第 2 页补齐并合并
    if paginfo.get("paginate"):
        target = paginfo.get("target") or 100
        page2_url = f"{url.split('?')[0]}?pageSize={target}&page=2"
        try:
            print(f"  [翻页] 翻第 2 页: {page2_url}")
            page.goto(page2_url, timeout=60000)
            if not wait_for_cloudflare(page, journal_name):
                print("  [翻页] 第2页触发验证，跳过补齐")
            page.wait_for_timeout(5000)
            n2 = _extract_articles_from_page(page, journal_name, seen_urls, articles)
            print(f"📦 第 2 页补充 {n2} 条（去重后总 {len(articles)} 条）")
        except Exception as e:
            print(f"  [翻页] 翻第2页失败(仅保留第1页 {len(articles)} 条): {e}")

    print(f"📦 Successfully parsed {len(articles)} articles from {url}")
    return articles

def main():
    print(f"=== [LWW] Start ({__version__}): {time.ctime()} ===\n")
    
    profile_dir = os.path.join(BASE_DIR, "lww_browser_profile")
    
    print("🚀 启动 Playwright 浏览器实例 (Edge)...")
    pw = sync_playwright().start()
    context = pw.chromium.launch_persistent_context(
        user_data_dir=profile_dir,
        channel="msedge",
        headless=False,
        args=[
            '--no-sandbox',
            '--disable-gpu',
            '--disable-background-timer-throttling',
            '--disable-backgrounding-occluded-windows',
            '--disable-renderer-backgrounding',
            '--disable-blink-features=AutomationControlled',
            '--disable-automation',
        ],
    )
    # 隐藏自动化标记，防止 reCAPTCHA 检测到自动化浏览器
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    """)
    page = context.new_page()
    updated_any = False
    has_failures = False
    
    targets = [
        {"name": "aswc_current_issue", "rss_url": "https://www.ovid.com/jnls/aswcjournal", "output_filename": "aswc_current_issue.xml", "web_scrape": True, "title": "Advances in Skin & Wound Care - Current Issue"},
        {"name": "aswc_latest_articles", "rss_url": "https://www.ovid.com/jnls/aswcjournal", "output_filename": "aswc_latest_articles.xml", "web_scrape": True, "title": "Advances in Skin & Wound Care - Latest Articles"},
        {"name": "annals_plast_surg_current", "rss_url": "https://www.ovid.com/jnls/annalsplasticsurgery", "output_filename": "annals_plast_surg_current.xml", "web_scrape": True, "title": "Annals of Plastic Surgery - Current Issue"},
        {"name": "annals_plast_surg_latest", "rss_url": "https://www.ovid.com/jnls/annalsplasticsurgery", "output_filename": "annals_plast_surg_latest.xml", "web_scrape": True, "title": "Annals of Plastic Surgery - Latest Articles"},
        {"name": "derm_surgery_current", "rss_url": "https://www.ovid.com/jnls/dermatologicsurgery", "output_filename": "derm_surgery_current.xml", "web_scrape": True, "title": "Dermatologic Surgery - Current Issue"},
        {"name": "derm_surgery_latest", "rss_url": "https://www.ovid.com/jnls/dermatologicsurgery", "output_filename": "derm_surgery_latest.xml", "web_scrape": True, "title": "Dermatologic Surgery - Latest Articles"},
        {"name": "j_craniofacial_surg_current", "rss_url": "https://journals.lww.com/jcraniofacialsurgery/toc/current", "output_filename": "j_craniofacial_surg_current.xml", "web_scrape": True, "title": "Journal of Craniofacial Surgery - Current Issue"},
        {"name": "j_craniofacial_surg_latest", "rss_url": "https://journals.lww.com/jcraniofacialsurgery/toc/latest", "output_filename": "j_craniofacial_surg_latest.xml", "web_scrape": True, "title": "Journal of Craniofacial Surgery - Latest Articles"},
        {"name": "j_craniofacial_surg_open_current", "rss_url": "https://www.ovid.com/jnls/jcso", "output_filename": "j_craniofacial_surg_open_current.xml", "web_scrape": True, "title": "Journal of Craniofacial Surgery Open - Current Issue"},
        {"name": "j_craniofacial_surg_open_latest", "rss_url": "https://www.ovid.com/jnls/jcso", "output_filename": "j_craniofacial_surg_open_latest.xml", "web_scrape": True, "title": "Journal of Craniofacial Surgery Open - Latest Articles"},
        {"name": "prs_current_issue", "rss_url": "https://journals.lww.com/plasreconsurg/toc/current", "output_filename": "prs_current_issue.xml", "web_scrape": True, "title": "Plastic and Reconstructive Surgery - Current Issue"},
        {"name": "prs_latest_articles", "rss_url": "https://journals.lww.com/plasreconsurg/toc/latest", "output_filename": "prs_latest_articles.xml", "web_scrape": True, "title": "Plastic and Reconstructive Surgery - Latest Articles"},
        {"name": "prs_video", "rss_url": "https://journals.lww.com/plasreconsurg/videos", "output_filename": "prs_video.xml", "web_scrape": True, "title": "Plastic and Reconstructive Surgery - Video"},
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
                # 平台统一：所有 TOC 源（ovid.com 或 journals.lww.com 的 current/latest）都走
                # scrape_toc_page（每页条数改选 + 总量翻页合并 + 真实出版日期）；仅视频页(/videos/)走旧 soup 分支。
                # 注：ovid 源 rss_url 不含 /toc/（main 内拼 /toc/current|latest），故按 "videos" 排除判定。
                is_toc_page = "videos" not in rss_url
                if is_toc_page:
                    # 🟢 TOC 抓取逻辑（Ovid + journals.lww.com 通用，含每页条数改选 + 总量翻页 + 真实出版日期）
                    base_url = rss_url.rstrip('/')
                    journal_name = journal.get('title', name).split(' - ')[0]
                    
                    articles = []
                    if "current" in name:
                        toc_url = f"{base_url}/toc/current" if not base_url.endswith('/toc/current') else base_url
                        articles = scrape_toc_page(page, toc_url, journal_name)
                    else:
                        # 所有 latest, ahead, online_first 等目标，均严格只对齐 toc/latest，不进行跨版块融合
                        toc_url = f"{base_url}/toc/latest" if not base_url.endswith('/toc/latest') else base_url
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
                    page.goto(rss_url)
                    if not wait_for_cloudflare(page, name):
                        has_failures = True
                        continue
                    time.sleep(5)
                    
                    raw_html = page.content()
                    soup = BeautifulSoup(raw_html, 'html.parser')
                    
                    items = []
                    if "videos" in rss_url:
                        # 🟢 Video Gallery 专属解析逻辑
                        cards = soup.find_all('div', class_='omni-card-body')
                        for card in cards:
                            title_a = card.find('h3', class_='omni-card__title').find('a') if card.find('h3', class_='omni-card__title') else None
                            if title_a:
                                title = title_a.get_text(strip=True)
                                href = title_a.get('href')
                                full_url = href if href.startswith('http') else urljoin(rss_url, href)
                                
                                date_str = "Unknown Date"
                                misc_div = card.find('div', class_='omni-card__misc')
                                if misc_div:
                                    text = misc_div.get_text(strip=True)
                                    if "Created" in text:
                                        date_str = text.split("Created")[-1].replace(":", "").strip()
                                        
                                items.append({
                                    'title': title,
                                    'link': full_url,
                                    'pub_date': date_str
                                })
                    else:
                        # 普通文献列表解析逻辑
                        for a in soup.find_all('a'):
                            href = a.get('href')
                            if href and ('/fulltext/' in href or '10.1097' in href):
                                title = a.get_text(strip=True)
                                if title and len(title) > 10 and 'PDF' not in title:
                                    full_url = href if href.startswith('http') else urljoin(rss_url, href)
                                    if not any(i['link'] == full_url for i in items):
                                        items.append({
                                            'title': title,
                                            'link': full_url,
                                            'pub_date': formatdate(time.time(), localtime=False, usegmt=True)
                                        })
                    
                    print(f"📦 网页抓取成功捕获 {len(items)} 个文献条目。")
                    if items:
                        pub_date_str = formatdate(time.time(), localtime=False, usegmt=True)
                        items_xml = ""
                        for item in items:
                            item_pub_date = item.get('pub_date', pub_date_str)
                            if "GMT" not in item_pub_date:
                                item_pub_date = parse_to_rfc822(item_pub_date)
                                
                            if "videos" in rss_url:
                                desc_html = f"<b>所属期数:</b> Video Gallery<br><b>出版时间:</b> {item_pub_date}<br><br><a href=\"{item['link']}\"></a>No description available."
                            else:
                                desc_html = f"<b>所属期数:</b> Ahead of Print<br><b>出版时间:</b> {pub_date_str}<br><br><a href=\"{item['link']}\"></a>No description available."
                                
                            item_xml = f"""
    <item>
      <link>{item['link']}</link>
      <title><![CDATA[{item['title']}]]></title>
      <description><![CDATA[{desc_html}]]></description>
      <pubDate>{item_pub_date}</pubDate>
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
                page.goto(rss_url)
                if not wait_for_cloudflare(page, name):
                    has_failures = True
                    continue
                time.sleep(8) 
                
                raw_html = page.content()
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
            
    # 清理：Playwright 的 context 由外层的 killall 处理，不在此优雅关闭
    try:
        pw.stop()
    except:
        pass
    # 多重清理，确保 Edge 不残留
    import time as _t; _t.sleep(1)
    try:
        subprocess.run(["pkill", "-9", "-f", "Microsoft Edge"], capture_output=True)
        subprocess.run(["killall", "-9", "msedge_crashpad_handler"], capture_output=True)
    except:
        pass
    
    if updated_any:
        push_to_github()
        


if __name__ == "__main__":
    main()