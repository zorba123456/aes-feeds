#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
Project: lit_auto_pipeline (aes-intel platform)
File: aes-feeds/cnki_downloader.py
Version: V2.0.0 (DUAL-TRACK HYBRID SYSTEM)
Description:
    1. --mode rss: 快速静默的 RSS 提取逻辑。
    2. --mode web: 使用 Playwright 有头模式提取“当期目录”与“网络首发”。
       遇到滑块验证码时，发出提示音并给予长达 10 分钟的人工滑动容错时间。
    3. 支持全局基于 Hash 的去重机制。
=============================================================================
"""

import os
import xml.etree.ElementTree as ET
import json
import time
import hashlib
import re
import argparse
import subprocess
from datetime import datetime, timezone, timedelta

from bs4 import BeautifulSoup
import requests

__version__ = "V2.0.0"

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)

TARGETS_JSON_PATH = os.path.join(CURRENT_DIR, "cnki_targets.json")
LOG_FILE_PATH = os.path.join(CURRENT_DIR, "cnki_dedup_log.json")
DEDUP_EXPIRE_DAYS = 90
USER_DATA_DIR = os.path.join(PROJECT_ROOT, "cnki_playwright_profile")
PROXY_SERVER = "http://127.0.0.1:29758"
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

def generate_hash(journal_code, title):
    """基于期刊代码和标题生成唯一哈希，避免因 URL 中的动态 v 参数导致去重失效"""
    clean_title = clean_text_noise(title)
    clean_title = re.sub(r'^\[(?:网络首发|当期目录)\]\s*(?:\[[^\]]+\]\s*)?', '', clean_title).strip()
    raw = f"{journal_code.lower()}_{clean_title}".encode('utf-8')
    return hashlib.md5(raw).hexdigest()

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

def generate_rss_xml(items, journal_code, journal_name):
    """生成标准 RSS 2.0 XML 并写入文件 (支持与现有文件合并去重，限额 30 条，并输出新旧两套文件名兼容)"""
    filename = f"cnki_{journal_code.lower()}.xml"
    out_file = os.path.join(CURRENT_DIR, filename)
    filename_legacy = f"cnki_{journal_code.upper()}_cleaned.xml"
    out_file_legacy = os.path.join(CURRENT_DIR, filename_legacy)
    
    existing_items = []
    if os.path.exists(out_file):
        try:
            tree = ET.parse(out_file)
            root = tree.getroot()
            for item_el in root.findall(".//item"):
                t_el = item_el.find("title")
                l_el = item_el.find("link")
                d_el = item_el.find("description")
                p_el = item_el.find("pubDate")
                
                existing_items.append({
                    "title": t_el.text if t_el is not None else "",
                    "link": l_el.text if l_el is not None else "",
                    "description": d_el.text if d_el is not None else "",
                    "pubDate": p_el.text if p_el is not None else ""
                })
        except Exception as e:
            print(f"  ⚠️ 读取现有 XML 失败: {e}")
            
    # 合并新旧文献并基于纯标题去重
    seen_titles = set()
    merged_items = []
    
    def get_clean_title(t):
        t_clean = clean_text_noise(t)
        return re.sub(r'^\[(?:网络首发|当期目录)\]\s*(?:\[[^\]]+\]\s*)?', '', t_clean).strip()
        
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
            
    # 再添加已有的历史文献
    for item in existing_items:
        title = item.get("title", "")
        clean_key = get_clean_title(title)
        if clean_key and clean_key not in seen_titles:
            seen_titles.add(clean_key)
            link = item.get("link") or item.get("url") or ""
            if link and "link" not in item:
                item["link"] = link
            merged_items.append(item)
            
    # 截取前 30 条
    merged_items = merged_items[:30]
    
    # 构建新的 XML
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    
    ET.SubElement(channel, "title").text = f"CNKI - {journal_name}"
    ET.SubElement(channel, "link").text = f"https://navi.cnki.net/knavi/journals/{journal_code}/detail"
    ET.SubElement(channel, "description").text = f"知网文献推送: {journal_name}"
    ET.SubElement(channel, "pubDate").text = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    ET.SubElement(channel, "generator").text = f"Lit Auto Pipeline {__version__}"
    
    for item in merged_items:
        item_el = ET.SubElement(channel, "item")
        ET.SubElement(item_el, "title").text = item.get("title", "")
        ET.SubElement(item_el, "link").text = item.get("link", "")
        ET.SubElement(item_el, "description").text = item.get("description", "")
        ET.SubElement(item_el, "guid").text = item.get("link", "")
        
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
    """静默抓取 RSS 模式"""
    print("[RSS Mode] 开始执行静默 RSS 抓取...")
    dedup_log = load_dedup_log()
    headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}

    for code, info in targets.items():
        name = info.get("name", code)
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

def wait_for_captcha(page, code, name):
    """当出现验证码时，触发系统通知和弹窗置顶提醒，等待人工滑动"""
    print(f"⚠️ 触发安全验证: {name} ({code})")
    
    # 1. 发送 macOS 系统通知
    try:
        title = "知网安全验证码"
        subtitle = f"正在抓取: {name}"
        script = f'display notification "{subtitle}" with title "{title}" sound name "Glass"'
        subprocess.run(["osascript", "-e", script], check=False)
    except Exception:
        pass

    # 2. 自动置顶/激活 Edge 浏览器窗口
    try:
        subprocess.run(["osascript", "-e", 'tell application "Microsoft Edge" to activate'], check=False)
    except Exception:
        pass

    # 3. 异步弹出系统警报对话框 (避免阻塞 Captcha 自动检测)
    dialog_proc = None
    try:
        script = (
            f'display alert "知网安全验证码" '
            f'message "正在抓取期刊：{name} ({code})\\n请在打开的 Edge 浏览器中完成滑块验证。" '
            f'as warning buttons {{"已完成"}} default button "已完成" giving up after 600'
        )
        dialog_proc = subprocess.Popen(["osascript", "-e", script])
    except Exception:
        pass
        
    print("⏳ 等待人工滑过验证码 (最长等待 10 分钟)...")
    wait_start = time.time()
    success = False
    
    while time.time() - wait_start < 600:  # 10分钟
        # 如果弹窗进程已结束（用户点击了“已完成”或弹窗超时）
        if dialog_proc and dialog_proc.poll() is not None:
            pass
            
        try:
            # 检查页面是否仍然包含"安全验证"
            if "安全验证" not in page.content():
                print("✅ 验证码已通过！继续执行...")
                time.sleep(2)  # 等待重定向完成
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=15000)
                    page.wait_for_selector("#CataLogContent dd", timeout=15000)
                except Exception:
                    pass
                time.sleep(3)
                success = True
                break
        except Exception:
            pass
        
        time.sleep(2)
        
    # 如果弹窗还在运行，将其关闭
    if dialog_proc and dialog_proc.poll() is None:
        try:
            dialog_proc.terminate()
        except Exception:
            pass
            
    if not success:
        print("❌ 超时！10 分钟内未完成人工验证，跳过该期刊。")
        return False
    return True


def run_web_mode(targets):
    """深度网页抓取模式 (Playwright)"""
    print("[Web Mode] 开始执行深度网页抓取...")
    from playwright.sync_api import sync_playwright
    
    dedup_log = load_dedup_log()
    
    with sync_playwright() as p:
        # 必须是有头模式 headless=False，以便人工介入
        # 使用系统的 Microsoft Edge（用户指定的项目浏览器）
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
            # Edge 不可用时回退 Chromium
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

        for code, info in targets.items():
            name = info.get("name", code)
            
            # 只有设置了 web_scrape 为 True 的才进行网页抓取
            if not info.get("web_scrape", False):
                print(f"跳过 {name} (未开启 web_scrape)")
                continue
                
            url = f'https://navi.cnki.net/knavi/journals/{code}/detail?uniplatform=NZKPT'
            print(f"\n正在深度抓取 {name} ({code})...")
            
            try:
                page.goto(url, wait_until='domcontentloaded', timeout=30000)
                
                # 检查是否遇到验证码
                if "安全验证" in page.content():
                    success = wait_for_captcha(page, code, name)
                    if not success:
                        continue
                
                # 等待 AJAX 渲染期刊期数和目录
                try:
                    page.wait_for_selector('#CataLogContent dd', timeout=20000)
                except Exception:
                    print("  ⚠️ 等待 #CataLogContent 超时，可能暂无数据或触发了验证码")
                time.sleep(2) # 额外等待渲染完成
                
                # 1. 检测可用视图
                has_net_first = page.locator('#YearIssueTree dl#NetFirstYear').count() > 0
                has_printed = page.locator('#YearIssueTree a[id^="yq"]').count() > 0
                
                if not has_net_first and not has_printed:
                    print("  ⚠️ 未检测到任何期数或网络首发目录")
                    continue
                
                # 2. 判断当前默认选中视图
                is_net_first_active = False
                if has_net_first:
                    classes = page.locator('#YearIssueTree dl#NetFirstYear').get_attribute("class") or ""
                    is_net_first_active = "cur" in classes
                
                views_to_scrape = []
                if has_net_first:
                    views_to_scrape.append("网络首发")
                if has_printed:
                    views_to_scrape.append("当期目录")
                    
                # 调整抓取顺序以减少不必要的视图切换点击
                if len(views_to_scrape) == 2:
                    if is_net_first_active:
                        views_to_scrape = ["网络首发", "当期目录"]
                    else:
                        views_to_scrape = ["当期目录", "网络首发"]
                
                all_scraped_items = []
                
                # 3. 循环抓取各视图
                for view_name in views_to_scrape:
                    print(f"  -> 正在抓取视图: {view_name}...")
                    
                    if view_name == "网络首发":
                        # 如果当前页面并非网络首发，则需点击切换
                        current_classes = page.locator('#YearIssueTree dl#NetFirstYear').get_attribute("class") or ""
                        if "cur" not in current_classes:
                            print("     切换至 [网络首发]...")
                            page.locator('#YearIssueTree dl#NetFirstYear em').click()
                            time.sleep(2)
                            try:
                                page.wait_for_selector('#CataLogContent dd', timeout=15000)
                            except Exception:
                                pass
                            time.sleep(1)
                    else:  # view_name == "当期目录"
                        # 如果当前页面并非当期目录，则需点击切换到最新期数
                        # 判断当前是否选在网络首发
                        current_classes = page.locator('#YearIssueTree dl#NetFirstYear').get_attribute("class") or "" if has_net_first else ""
                        if "cur" in current_classes or len(all_scraped_items) > 0:
                            print("     切换至 [当期目录] (最新期数)...")
                            latest_issue_loc = page.locator('#YearIssueTree a[id^="yq"]').first
                            parent_dl = latest_issue_loc.locator("xpath=ancestor::dl")
                            dd_el = parent_dl.locator("dd")
                            if dd_el.is_hidden():
                                parent_dl.locator("dt").click()
                                time.sleep(1)
                            latest_issue_loc.click()
                            time.sleep(2)
                            try:
                                page.wait_for_selector('#CataLogContent dd', timeout=15000)
                            except Exception:
                                pass
                            time.sleep(1)
                            
                    # 解析当前视图内容
                    html = page.content()
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    # 提取期数名称
                    issue_el = soup.select_one('span.date-list')
                    issue_txt = issue_el.get_text(strip=True) if issue_el else '未知期数'
                    
                    elements = soup.select('#CataLogContent dd')
                    print(f"     发现 {len(elements)} 篇文献")
                    
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
                            enhanced_title = f"[当期目录] [{issue_txt}] {raw_title}"
                            pub_date = parse_cnki_pubdate(company_txt)
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
                
                # 4. 过滤新文献并去重
                seen_hashes_this_run = set()
                new_items = []
                for item in all_scraped_items:
                    h = item["hash"]
                    if h in dedup_log or h in seen_hashes_this_run:
                        continue
                    seen_hashes_this_run.add(h)
                    new_items.append(item)
                
                if new_items:
                    print(f"  => 汇总提取到 {len(new_items)} 篇新文献")
                    # 写入去重日志
                    for item in new_items:
                        dedup_log[item['hash']] = {"title": item['title'], "timestamp": time.time()}
                    # 写入 XML
                    generate_rss_xml(new_items, code, name)
                else:
                    print("  => 网页上无新文献")
                    
            except Exception as e:
                print(f"  ❌ 网页抓取异常: {e}")
                
        ctx.close()
        
    save_dedup_log(dedup_log)
    print("\n[Web Mode] 深度抓取完成！")

def main():
    parser = argparse.ArgumentParser(description="CNKI Downloader (Dual-Track)")
    parser.add_argument("--mode", choices=["rss", "web"], required=True, help="运行模式: rss (静默) 或 web (带弹窗)")
    args = parser.parse_args()
    
    targets = load_targets()
    if not targets:
        print(f"配置文件缺失或为空: {TARGETS_JSON_PATH}")
        return
        
    if args.mode == 'rss':
        run_rss_mode(targets)
    elif args.mode == 'web':
        run_web_mode(targets)

    # 执行完数据更新后，推送结果到 GitHub 远端仓库
    push_to_github()

if __name__ == "__main__":
    main()
