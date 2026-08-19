#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
Project: lit_auto_pipeline (aes-intel platform)
File: aes-feeds/cnki_downloader.py
Version: V2.1.1 (DUAL-TRACK HYBRID SYSTEM)
Description:
    1. --mode rss: 快速静默的 RSS 提取逻辑。
    2. --mode web: 使用 Playwright 有头模式提取“当期目录”与“网络首发”。
       遇到滑块验证码时，发出提示音并给予长达 10 分钟的人工滑动容错时间。
       改进验证码通过检测、逐刊超时跳过、关键步骤日志与调试页面 dump。
    3. 支持全局基于 Hash 的去重机制。
=============================================================================
"""

import os
import sys
import xml.etree.ElementTree as ET
import json
import time
import hashlib
import re
import argparse
import signal
import subprocess
from datetime import datetime, timezone, timedelta

from bs4 import BeautifulSoup
import requests

__version__ = "V2.1.1"

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)

TARGETS_JSON_PATH = os.path.join(CURRENT_DIR, "cnki_targets.json")
LOG_FILE_PATH = os.path.join(CURRENT_DIR, "cnki_dedup_log.json")
WEB_LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
DEDUP_EXPIRE_DAYS = 90
USER_DATA_DIR = os.path.join(PROJECT_ROOT, "cnki_playwright_profile")
PROXY_SERVER = "http://127.0.0.1:29758"
CAPTCHA_WAIT_SECS = 600
JOURNAL_TIMEOUT_SECS = 300
CAPTCHA_POLL_INTERVAL = 2
CAPTCHA_LOG_INTERVAL = 30
CATALOG_WAIT_SECS = 15
NET_FIRST_SWITCH_WAIT_SECS = 3
FEED_ITEM_LIMIT = 120  # XML/Inoreader 展示上限（当期目录+网络首发合并后）


def _log(msg):
    """带时间戳的即时日志，确保 cron/tee 场景下不丢输出。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def is_captcha_url(url):
    if not url:
        return False
    lower = url.lower()
    return "verify" in lower or "captcha" in lower


def is_captcha_title(title):
    return bool(title and "安全验证" in title)


def page_has_catalog(page):
    try:
        return page.locator("#CataLogContent dd").count() > 0
    except Exception:
        return False


def page_has_issue_tree(page):
    try:
        return page.locator("#YearIssueTree").count() > 0
    except Exception:
        return False


def page_has_captcha(page):
    """检测页面是否仍处于验证码拦截状态（避免 HTML 残留文案误判）。"""
    try:
        if is_captcha_url(page.url):
            return True
        if is_captcha_title(page.title()):
            return True
        if page.locator('#captcha, .verify-wrap, [class*="captcha"], [id*="captcha"]').count() > 0:
            return True
        if page_has_catalog(page) or page_has_issue_tree(page):
            return False
        content = page.content()
        return "安全验证" in content and "blockPuzzle" in content
    except Exception:
        return False


def captcha_passed(page):
    """验证码已通过：目录或期数树已出现，或已进入期刊详情页且无验证码。"""
    try:
        if page_has_catalog(page) or page_has_issue_tree(page):
            return True
        if "knavi/journals" in page.url and not page_has_captcha(page):
            title = page.title() or ""
            if title and "安全验证" not in title:
                return True
    except Exception:
        pass
    return False


def net_first_is_active(page):
    try:
        classes = page.locator('#YearIssueTree dl#NetFirstYear').get_attribute("class") or ""
        return "cur" in classes
    except Exception:
        return False


def catalog_item_count(page):
    try:
        return page.locator('#CataLogContent dd').count()
    except Exception:
        return 0


def _wait_for_catalog(page, timeout_secs, label):
    try:
        page.wait_for_selector('#CataLogContent dd', timeout=timeout_secs * 1000)
        return True
    except Exception as e:
        if catalog_item_count(page) == 0:
            return False
        _log(f"     ⚠️ {label}目录加载超时: {e}")
        return catalog_item_count(page) > 0


def _activate_net_first_view(page):
    """切换至网络首发视图；若无文献则快速返回 False，避免空等 15 秒。"""
    if net_first_is_active(page):
        return catalog_item_count(page) > 0
    _log("     切换至 [网络首发]...")
    page.locator('#YearIssueTree dl#NetFirstYear em').click()
    time.sleep(1)
    if _wait_for_catalog(page, NET_FIRST_SWITCH_WAIT_SECS, "网络首发"):
        return True
    _log("     跳过 [网络首发]（该刊暂无网络首发文献）")
    return False


def dump_debug_page(page, code, reason):
    os.makedirs(WEB_LOG_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    html_path = os.path.join(WEB_LOG_DIR, f"cnki_debug_{code}_{ts}.html")
    try:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(page.content())
        _log(f"  📄 调试页面已保存: {html_path} ({reason})")
    except Exception as e:
        _log(f"  ⚠️ 无法保存调试页面: {e}")


class JournalTimeout(Exception):
    pass


def _check_journal_timeout(journal_start, code, name, stage):
    elapsed = time.time() - journal_start
    if elapsed > JOURNAL_TIMEOUT_SECS:
        raise JournalTimeout(f"{name} ({code}) 在 [{stage}] 超过 {JOURNAL_TIMEOUT_SECS}s")


def clean_text_noise(text):
    if not text:
        return ""
    cleaned = text.replace('\ufffd', '').replace('\u0000', '')
    cleaned = re.sub(r'\?{2,}', '', cleaned)
    return re.sub(r'\s+', ' ', cleaned).strip()

def load_targets():
    """加载配置的目标期刊"""
    if os.path.exists(TARGETS_JSON_PATH):
        with open(TARGETS_JSON_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def get_clean_title(t):
    """去掉来源前缀与期数标签，得到用于去重/升级的裸标题。"""
    t_clean = clean_text_noise(t)
    return re.sub(r'^\[(?:网络首发|当期目录)\]\s*(?:\[[^\]]+\]\s*)?', '', t_clean).strip()

def generate_hash(journal_code, title):
    """基于期刊代码和标题生成唯一哈希，避免因 URL 中的动态 v 参数导致去重失效"""
    clean_title = get_clean_title(title)
    raw = f"{journal_code.lower()}_{clean_title}".encode('utf-8')
    return hashlib.md5(raw).hexdigest()

def cnki_issue_to_month(issue_txt):
    """当期目录期数 "2026年08期" → 到月字符串 "2026-08"（对齐 LWW 月/日自明字符串语义）。
    无期数/解析失败返回 None。"""
    if not issue_txt:
        return None
    m = re.search(r'(\d{4})年\s*(\d{1,2})\s*期', issue_txt)
    if not m:
        return None
    return f"{m.group(1)}-{int(m.group(2)):02d}"


def parse_cnki_pubdate(date_str):
    """
    解析知网的发布日期。
    网络首发格式通常为: "2026-05-12 07:15:34" 或 "2026-05-12"
    如果是印版页码 (如 "97-106")，则返回 None。
    """
    if not date_str:
        return None
    date_str = date_str.strip()
    
    # 使用正则匹配日期部分
    match = re.search(r'(\d{4}-\d{2}-\d{2})(?:\s+(\d{2}:\d{2}:\d{2}))?', date_str)
    if not match:
        return None
        
    date_part = match.group(1)
    time_part = match.group(2) or "00:00:00"
    
    try:
        dt = datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H:%M:%S")
        # 知网时间是北京时间 (UTC+8)，转换为 UTC
        tz_offset = timezone(timedelta(hours=8))
        dt_utc = dt.replace(tzinfo=tz_offset).astimezone(timezone.utc)
        return dt_utc.strftime("%a, %d %b %Y %H:%M:%S GMT")
    except Exception:
        return None

def load_dedup_log():
    """加载去重记录"""
    if os.path.exists(LOG_FILE_PATH):
        try:
            with open(LOG_FILE_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data
        except Exception:
            return {}
    return {}

def save_dedup_log(log_data):
    """保存去重记录并清理过期的Hash"""
    now = time.time()
    expire_secs = DEDUP_EXPIRE_DAYS * 24 * 3600
    cleaned_data = {}
    for k, v in log_data.items():
        ts_val = v.get("timestamp") or v.get("ts", 0)
        if (now - ts_val) < expire_secs:
            cleaned_data[k] = {
                "title": v.get("title", ""),
                "timestamp": ts_val,
                "ts": ts_val
            }
    with open(LOG_FILE_PATH, 'w', encoding='utf-8') as f:
        json.dump(cleaned_data, f, ensure_ascii=False, indent=2)

def filter_targets(targets, journal_code=None):
    """按期刊代码过滤 targets（大小写不敏感）。"""
    if not journal_code:
        return targets
    key = journal_code.strip().upper()
    if key not in targets:
        known = ", ".join(sorted(targets.keys()))
        raise SystemExit(f"未知期刊代码: {journal_code}（可选: {known}）")
    return {key: targets[key]}


def is_web_scrape_journal(info):
    return bool(info.get("web_scrape", False))


def is_standard_web_item(title):
    """深度抓取条目应带 [当期目录] 或 [网络首发] 前缀。"""
    if not title:
        return False
    return title.startswith("[当期目录]") or title.startswith("[网络首发]")


def reset_journal_feed(journal_code):
    """删除某刊 XML 并从去重日志移除其条目（新增期刊误跑 RSS 后重建用）。"""
    code = journal_code.strip().upper()
    code_lower = code.lower()
    xml_paths = [
        os.path.join(CURRENT_DIR, f"cnki_{code_lower}.xml"),
        os.path.join(CURRENT_DIR, f"cnki_{code}_cleaned.xml"),
    ]
    titles = []
    for path in xml_paths:
        if not os.path.exists(path):
            continue
        try:
            tree = ET.parse(path)
            for item_el in tree.findall(".//item"):
                t_el = item_el.find("title")
                if t_el is not None and t_el.text:
                    titles.append(t_el.text)
        except Exception as e:
            _log(f"  ⚠️ 读取 {path} 失败: {e}")
        os.remove(path)
        _log(f"  已删除 {os.path.basename(path)}")

    dedup_log = load_dedup_log()
    removed = 0
    for h, meta in list(dedup_log.items()):
        title = meta.get("title", "")
        if generate_hash(code, title) == h:
            dedup_log.pop(h, None)
            removed += 1
    if removed:
        save_dedup_log(dedup_log)
        _log(f"  已从去重日志移除 {removed} 条 {code} 记录")
    return len(titles)


def push_to_github():
    """将生成的 XML 和去重记录推送至 GitHub 独立仓库"""
    print("\n📤 启动 GitHub 自动同步 (CNKI Feeds)...")
    custom_env = os.environ.copy()
    custom_env["HTTP_PROXY"] = PROXY_SERVER
    custom_env["HTTPS_PROXY"] = PROXY_SERVER
    try:
        subprocess.run("git add cnki_*.xml cnki_dedup_log.json", cwd=CURRENT_DIR, check=True, shell=True)
        commit_msg = f"Auto-update CNKI feeds: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=CURRENT_DIR, check=True)
        subprocess.run(["git", "push"], cwd=CURRENT_DIR, env=custom_env, check=True)
        print("✅ 同步成功！CNKI 数据已成功推送至 aes-feeds 独立仓库。")
    except subprocess.CalledProcessError as e:
        print(f"ℹ️ 未检测到新文献或同步无变动，跳过推送。({e})")

def load_existing_feed_items(journal_code):
    """读取当前 XML 中的条目（用于升级时保留 guid/pubDate）。"""
    out_file = os.path.join(CURRENT_DIR, f"cnki_{journal_code.lower()}.xml")
    existing_items = []
    if not os.path.exists(out_file):
        return existing_items
    try:
        tree = ET.parse(out_file)
        for item_el in tree.getroot().findall(".//item"):
            t_el = item_el.find("title")
            l_el = item_el.find("link")
            d_el = item_el.find("description")
            p_el = item_el.find("pubDate")
            g_el = item_el.find("guid")
            existing_items.append({
                "title": t_el.text if t_el is not None else "",
                "link": l_el.text if l_el is not None else "",
                "description": d_el.text if d_el is not None else "",
                "pubDate": p_el.text if p_el is not None else "",
                "guid": g_el.text if g_el is not None else "",
            })
    except Exception as e:
        _log(f"  ⚠️ 读取现有 XML 失败: {e}")
    return existing_items


def _build_existing_index(existing_items):
    """按裸标题索引现有 Feed 条目。"""
    by_clean = {}
    for item in existing_items:
        ck = get_clean_title(item.get("title", ""))
        if ck:
            by_clean[ck] = item
    return by_clean


def _apply_net_first_upgrade_metadata(upgrade_item, existing_entry):
    """升级条目：保留 guid 与 pubDate，避免 Inoreader 当成新文章或改排序。"""
    if existing_entry.get("guid"):
        upgrade_item["guid"] = existing_entry["guid"]
    if existing_entry.get("pubDate"):
        upgrade_item["pubDate"] = existing_entry["pubDate"]


def classify_scraped_items(all_scraped_items, dedup_log, existing_items):
    """将爬取结果分为新文献与「网络首发→当期目录」升级文献。"""
    existing_by_clean = _build_existing_index(existing_items)
    upgrade_items = []
    new_items = []
    seen_hashes_this_run = set()

    for item in all_scraped_items:
        h = item["hash"]
        if h in seen_hashes_this_run:
            continue
        seen_hashes_this_run.add(h)

        title = item.get("title", "")
        if title.startswith("[当期目录]"):
            ck = get_clean_title(title)
            existing_entry = existing_by_clean.get(ck)
            old_title = dedup_log.get(h, {}).get("title", "") if h in dedup_log else ""
            if old_title.startswith("[当期目录]"):
                continue
            log_net_first = old_title.startswith("[网络首发]")
            xml_net_first = existing_entry and existing_entry.get("title", "").startswith("[网络首发]")
            if log_net_first or xml_net_first:
                if existing_entry:
                    _apply_net_first_upgrade_metadata(item, existing_entry)
                upgrade_items.append(item)
                continue

        if h in dedup_log:
            continue

        new_items.append(item)

    return new_items, upgrade_items


def generate_rss_xml(items, journal_code, journal_name, upgrade_items=None):
    """生成标准 RSS 2.0 XML 并写入文件 (支持与现有文件合并去重，限额 FEED_ITEM_LIMIT 条，并输出新旧两套文件名兼容)"""
    upgrade_items = upgrade_items or []
    filename = f"cnki_{journal_code.lower()}.xml"
    out_file = os.path.join(CURRENT_DIR, filename)
    filename_legacy = f"cnki_{journal_code.upper()}_cleaned.xml"
    out_file_legacy = os.path.join(CURRENT_DIR, filename_legacy)
    
    existing_items = load_existing_feed_items(journal_code)
            
    # 合并新旧文献并基于纯标题去重
    seen_titles = set()
    merged_items = []

    upgrade_map = {}
    for item in upgrade_items:
        ck = get_clean_title(item.get("title", ""))
        if ck:
            upgrade_map[ck] = item
        
    # 优先添加新抓取的文献
    for item in items:
        title = item.get("title", "")
        clean_key = get_clean_title(title)
        if clean_key and clean_key not in seen_titles:
            seen_titles.add(clean_key)
            link = item.get("link") or item.get("url") or ""
            if link and "link" not in item:
                item["link"] = link
            merged_items.append(item)
            
    # 再添加已有的历史文献（网络首发→当期：同位替换，保留 guid）
    for item in existing_items:
        title = item.get("title", "")
        clean_key = get_clean_title(title)
        if not clean_key:
            continue
        if clean_key in upgrade_map:
            if clean_key not in seen_titles:
                seen_titles.add(clean_key)
                merged_items.append(upgrade_map[clean_key])
            continue
        if clean_key not in seen_titles:
            seen_titles.add(clean_key)
            link = item.get("link") or item.get("url") or ""
            if link and "link" not in item:
                item["link"] = link
            merged_items.append(item)

    # 已掉出窗口的升级条目：插回列表（guid/pubDate 已在 classify 阶段从旧 XML 保留，若无则用爬取值）
    for clean_key, item in upgrade_map.items():
        if clean_key not in seen_titles:
            seen_titles.add(clean_key)
            merged_items.append(item)
            
    # 截取前 FEED_ITEM_LIMIT 条
    merged_items = merged_items[:FEED_ITEM_LIMIT]
    
    # 构建新的 XML
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    
    ET.SubElement(channel, "title").text = f"{journal_name} - CNKI Feeds"
    ET.SubElement(channel, "link").text = "https://github.com/zorba123456/aes-feeds"
    ET.SubElement(channel, "description").text = f"知网文献推送: {journal_name}"
    ET.SubElement(channel, "pubDate").text = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    ET.SubElement(channel, "generator").text = f"Lit Auto Pipeline {__version__}"
    
    for item in merged_items:
        item_el = ET.SubElement(channel, "item")
        ET.SubElement(item_el, "title").text = item.get("title", "")
        ET.SubElement(item_el, "link").text = item.get("link", "")
        ET.SubElement(item_el, "description").text = item.get("description", "")
        
        guid_val = item.get("guid") or item.get("hash") or generate_hash(journal_code, item.get("title", ""))
        ET.SubElement(item_el, "guid", isPermaLink="false").text = guid_val
        
        pub_date = item.get("pubDate")
        if pub_date:
            ET.SubElement(item_el, "pubDate").text = pub_date
            
    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ", level=0)
    
    # 同时写入两套文件名（小写标准版与大写cleaned兼容版）
    for path in [out_file, out_file_legacy]:
        tree.write(path, encoding="utf-8", xml_declaration=True)
        
    return filename

def run_rss_mode(targets):
    """静默抓取 RSS 模式（当前已禁用）

    cnki_targets.json 所有期刊均由 --mode web 深度爬取，RSS 模式不执行写入。
    保留此函数作为兜底：若 web 爬取长期失败，可临时将下方 return 注释掉恢复 RSS。
    """
    print("[RSS Mode] 所有 CNKI 期刊均使用 web 深度爬取，RSS 模式已跳过。")
    return

    # ↓↓↓ 以下为 RSS 兜底逻辑，正常情况不执行 ↓↓↓
    print("[RSS Mode] 开始执行静默 RSS 抓取...")
    dedup_log = load_dedup_log()
    headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}

    for code, info in targets.items():
        name = info.get("name", code)
        if is_web_scrape_journal(info):
            print(f"跳过 {name} ({code}): web_scrape 期刊仅由 --mode web 写 XML")
            continue
        rss_url = info.get("rss_url")
        if not rss_url:
            continue

        print(f"正在抓取 {name} ({code})...")
        try:
            r = requests.get(rss_url, headers=headers, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, 'xml')
            
            new_items = []
            for item in soup.find_all('item'):
                title = clean_text_noise(item.find('title').get_text(strip=True)) if item.find('title') else ''
                link = clean_text_noise(item.find('link').get_text(strip=True)) if item.find('link') else ''
                desc = item.find('description').get_text(strip=True) if item.find('description') else ''
                pubdate = item.find('pubDate').get_text(strip=True) if item.find('pubDate') else ''
                
                h = generate_hash(code, title)
                if h in dedup_log:
                    continue
                
                new_items.append({
                    "title": title,
                    "link": link,
                    "description": desc,
                    "pubDate": pubdate,
                    "hash": h
                })
            
            if new_items:
                print(f"  -> 发现 {len(new_items)} 篇新文献")
                # 写入本地去重日志
                for item in new_items:
                    dedup_log[item['hash']] = {"title": item['title'], "timestamp": time.time()}
                # 生成 XML
                generate_rss_xml(new_items, code, name)
            else:
                print("  -> 无新文献")
                
        except Exception as e:
            print(f"  ❌ 抓取失败: {e}")

    save_dedup_log(dedup_log)
    print("[RSS Mode] 执行完成！")

def wait_for_captcha(page, code, name, journal_start):
    """当出现验证码时，触发系统通知和弹窗置顶提醒，等待人工滑动"""
    _log(f"⚠️ 触发安全验证: {name} ({code})")

    try:
        subtitle = f"正在抓取: {name}"
        script = f'display notification "{subtitle}" with title "知网安全验证码" sound name "Glass"'
        subprocess.run(["osascript", "-e", script], check=False)
    except Exception:
        pass

    try:
        subprocess.run(["osascript", "-e", 'tell application "Microsoft Edge" to activate'], check=False)
    except Exception:
        pass

    _log(f"⏳ 等待人工滑过验证码 (最长等待 {CAPTCHA_WAIT_SECS // 60} 分钟)...")
    wait_start = time.time()
    last_progress_log = wait_start

    while time.time() - wait_start < CAPTCHA_WAIT_SECS:
        _check_journal_timeout(journal_start, code, name, "captcha_wait")

        try:
            if captcha_passed(page):
                _log("✅ 验证码已通过！等待页面加载...")
                time.sleep(2)
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=15000)
                    page.wait_for_selector("#CataLogContent dd, #YearIssueTree", timeout=15000)
                except Exception as e:
                    _log(f"  ⚠️ 验证码通过后等待目录加载: {e}")
                time.sleep(2)
                if captcha_passed(page):
                    return True
                _log("  ⚠️ 验证码似乎已通过但页面未就绪，继续等待...")
        except JournalTimeout:
            raise
        except Exception as e:
            _log(f"  ⚠️ 验证码检测异常: {e}")

        now = time.time()
        if now - last_progress_log >= CAPTCHA_LOG_INTERVAL:
            elapsed = int(now - wait_start)
            _log(f"  … 仍在等待验证码 ({elapsed}s / {CAPTCHA_WAIT_SECS}s)")
            last_progress_log = now

        time.sleep(CAPTCHA_POLL_INTERVAL)

    _log("❌ 超时！验证码等待时间内未完成人工验证，跳过该期刊。")
    dump_debug_page(page, code, "captcha_timeout")
    return False


def _scrape_journal_views(page, code, name, journal_start, has_net_first, has_printed, is_net_first_active):
    views_to_scrape = []
    if has_net_first:
        views_to_scrape.append("网络首发")
    if has_printed:
        views_to_scrape.append("当期目录")
    if len(views_to_scrape) == 2:
        if is_net_first_active:
            views_to_scrape = ["网络首发", "当期目录"]
        else:
            views_to_scrape = ["当期目录", "网络首发"]

    all_scraped_items = []
    for view_name in views_to_scrape:
        _check_journal_timeout(journal_start, code, name, f"view_{view_name}")
        _log(f"  -> 正在抓取视图: {view_name}...")

        if view_name == "网络首发":
            if not _activate_net_first_view(page):
                continue
        else:
            current_classes = page.locator('#YearIssueTree dl#NetFirstYear').get_attribute("class") or "" if has_net_first else ""
            if "cur" in current_classes or len(all_scraped_items) > 0:
                _log("     切换至 [当期目录] (最新期数)...")
                latest_issue_loc = page.locator('#YearIssueTree a[id^="yq"]').first
                parent_dl = latest_issue_loc.locator("xpath=ancestor::dl")
                dd_el = parent_dl.locator("dd")
                if dd_el.is_hidden():
                    parent_dl.locator("dt").click()
                    time.sleep(1)
                latest_issue_loc.click()
                time.sleep(2)
                _wait_for_catalog(page, CATALOG_WAIT_SECS, "当期")
                time.sleep(1)

        html = page.content()
        soup = BeautifulSoup(html, 'html.parser')
        issue_el = soup.select_one('span.date-list')
        issue_txt = issue_el.get_text(strip=True) if issue_el else '未知期数'
        elements = soup.select('#CataLogContent dd')
        _log(f"     发现 {len(elements)} 篇文献")

        for el in elements:
            a_tag = el.select_one('span.name a')
            if not a_tag:
                continue
            raw_title = clean_text_noise(a_tag.get_text(strip=True))
            link_href = a_tag.get('href', '')
            if link_href.startswith('/'):
                link = f"https://navi.cnki.net{link_href}"
            else:
                link = link_href
            author_tag = el.select_one('.author')
            author = clean_text_noise(author_tag.get_text(strip=True)) if author_tag else ''
            company_tag = el.select_one('.company')
            company_txt = company_tag.get('title', '').strip() if company_tag else ''
            if not company_txt and company_tag:
                company_txt = company_tag.get_text(strip=True)
            if view_name == "网络首发":
                enhanced_title = f"[网络首发] {raw_title}"
                pub_date = parse_cnki_pubdate(company_txt)
                desc = f"<b>期数：</b>网络首发<br><b>出版日期/发布时间：</b>{company_txt}<br><b>作者：</b>{author or '未标明'}"
            else:
                # 当期目录：去掉无意义的 "[当期目录]" 前缀（期数已由 [{issue_txt}] 体现）。
                # pub_date 改用"期数月"字符串（如 "2026年08期" → "2026-08"，对齐 LWW 月/日自明字符串语义），
                # 不再用 datetime.now() 兜底（那是 XML 生成时刻、是假日期）。无期数→None。
                enhanced_title = f"[{issue_txt}] {raw_title}"
                pub_date = cnki_issue_to_month(issue_txt)
                desc = f"<b>期数：</b>{issue_txt}<br><b>出版日期/页码：</b>{company_txt}<br><b>作者：</b>{author or '未标明'}"
            if not pub_date:
                pub_date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
            h = generate_hash(code, raw_title)
            all_scraped_items.append({
                "title": enhanced_title,
                "link": link,
                "description": desc,
                "pubDate": pub_date,
                "hash": h
            })
    return all_scraped_items


def run_web_mode(targets):
    """深度网页抓取模式 (Playwright)"""
    from playwright.sync_api import sync_playwright

    _log("[Web Mode] 开始执行深度网页抓取...")
    dedup_log = load_dedup_log()
    ctx = None

    try:
        with sync_playwright() as p:
            try:
                ctx = p.chromium.launch_persistent_context(
                    USER_DATA_DIR, headless=False, channel='msedge',
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--disable-background-timer-throttling',
                        '--disable-backgrounding-occluded-windows',
                        '--disable-renderer-backgrounding'
                    ]
                )
            except Exception:
                ctx = p.chromium.launch_persistent_context(
                    USER_DATA_DIR, headless=False,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--disable-background-timer-throttling',
                        '--disable-backgrounding-occluded-windows',
                        '--disable-renderer-backgrounding'
                    ]
                )
            page = ctx.new_page()
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            web_targets = list(targets.items())  # cnki_targets.json 所有期刊均为 web 深度爬取
            _log(f"共 {len(web_targets)} 本期刊待深度抓取")

            for idx, (code, info) in enumerate(web_targets, 1):
                name = info.get("name", code)
                journal_start = time.time()
                url = f'https://navi.cnki.net/knavi/journals/{code}/detail?uniplatform=NZKPT'
                _log(f"\n[{idx}/{len(web_targets)}] 正在深度抓取 {name} ({code})...")

                try:
                    _log(f"  导航至期刊页...")
                    page.goto(url, wait_until='domcontentloaded', timeout=30000)
                    _check_journal_timeout(journal_start, code, name, "goto")

                    if page_has_captcha(page):
                        if not wait_for_captcha(page, code, name, journal_start):
                            continue

                    _log("  等待目录渲染...")
                    try:
                        page.wait_for_selector('#CataLogContent dd, #YearIssueTree', timeout=20000)
                    except Exception as e:
                        _log(f"  ⚠️ 等待目录超时: {e}")
                        if page_has_captcha(page):
                            _log("  ⚠️ 目录超时且仍有验证码，尝试再次等待...")
                            if not wait_for_captcha(page, code, name, journal_start):
                                dump_debug_page(page, code, "catalog_timeout_with_captcha")
                                continue
                        else:
                            dump_debug_page(page, code, "catalog_timeout")
                    time.sleep(2)
                    _check_journal_timeout(journal_start, code, name, "catalog_wait")

                    has_net_first_ui = page.locator('#YearIssueTree dl#NetFirstYear').count() > 0
                    has_printed = page.locator('#YearIssueTree a[id^="yq"]').count() > 0
                    if not has_net_first_ui and not has_printed:
                        _log("  ⚠️ 未检测到任何期数或网络首发目录")
                        dump_debug_page(page, code, "no_issue_tree")
                        continue

                    is_net_first_active = has_net_first_ui and net_first_is_active(page)
                    has_net_first = has_net_first_ui
                    if has_net_first_ui and is_net_first_active and catalog_item_count(page) == 0:
                        has_net_first = False
                        _log("  ℹ️ 该刊无网络首发文献，跳过网络首发视图")

                    all_scraped_items = _scrape_journal_views(
                        page, code, name, journal_start,
                        has_net_first, has_printed, is_net_first_active
                    )

                    existing_feed_items = load_existing_feed_items(code)
                    new_items, upgrade_items = classify_scraped_items(
                        all_scraped_items, dedup_log, existing_feed_items
                    )

                    if new_items or upgrade_items:
                        if new_items:
                            _log(f"  => 汇总提取到 {len(new_items)} 篇新文献")
                        if upgrade_items:
                            _log(f"  => 网络首发→当期目录升级 {len(upgrade_items)} 篇（保留 guid/pubDate）")
                        for item in new_items:
                            dedup_log[item['hash']] = {"title": item['title'], "timestamp": time.time()}
                        for item in upgrade_items:
                            dedup_log[item['hash']] = {"title": item['title'], "timestamp": time.time()}
                        generate_rss_xml(new_items, code, name, upgrade_items=upgrade_items)
                    else:
                        _log("  => 网页上无新文献")

                    elapsed = int(time.time() - journal_start)
                    _log(f"  ✓ {name} 完成 ({elapsed}s)")

                except JournalTimeout as e:
                    _log(f"  ❌ 期刊超时，跳过: {e}")
                    dump_debug_page(page, code, "journal_timeout")
                except Exception as e:
                    _log(f"  ❌ 网页抓取异常: {e}")
                    dump_debug_page(page, code, "exception")

            if ctx:
                ctx.close()
                ctx = None
    finally:
        if ctx:
            try:
                ctx.close()
            except Exception:
                pass

    save_dedup_log(dedup_log)
    _log("[Web Mode] 深度抓取完成！")

def _install_signal_handlers():
    def _on_signal(signum, _frame):
        _log(f"收到信号 {signum}，正在安全退出...")
        sys.exit(128 + signum)

    for sig_name in ("SIGTERM", "SIGINT", "SIGUSR1"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            signal.signal(sig, _on_signal)


def main():
    parser = argparse.ArgumentParser(description="CNKI Downloader (Dual-Track)")
    parser.add_argument("--mode", choices=["rss", "web"], required=True, help="运行模式: rss (静默) 或 web (带弹窗)")
    parser.add_argument("--journal", metavar="CODE", help="仅处理指定期刊代码，如 YLMR")
    parser.add_argument(
        "--reset-journal", metavar="CODE",
        help="删除该刊 XML 并清去重后再抓取（与 --journal 联用）"
    )
    args = parser.parse_args()

    if args.mode == "web":
        _install_signal_handlers()

    targets = load_targets()
    if not targets:
        print(f"配置文件缺失或为空: {TARGETS_JSON_PATH}")
        return

    if args.reset_journal:
        _log(f"🔄 重置期刊 {args.reset_journal.upper()} 的 XML 与去重记录...")
        reset_journal_feed(args.reset_journal)

    try:
        targets = filter_targets(targets, args.journal)
    except SystemExit as e:
        print(e)
        return

    if args.mode == 'rss':
        run_rss_mode(targets)
    elif args.mode == 'web':
        run_web_mode(targets)

    push_to_github()

if __name__ == "__main__":
    main()
