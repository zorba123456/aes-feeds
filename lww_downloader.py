#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LWW Journals RSS Downloader & Refiner
Version: V3.4.2 (DrissionPage Standard Core + CDATA Protection)
Description: 基于 Edge 浏览器自动化驱动，完美穿透 Cloudflare 防火墙。
             升级防坍塌清洗机制，确保多次跑批或数据异常时描述节点不发生嵌套。
"""

import os
import json
import time
import subprocess
import re
from DrissionPage import ChromiumPage, ChromiumOptions

__version__ = "3.4.2-全量去重正式版"

def force_kill_edge():
    """清理 Edge 残留进程"""
    print("🧹 正在执行环境大扫除 (强杀 Edge 残留进程)...")
    try:
        subprocess.run(['killall', '-9', 'Microsoft Edge'], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        time.sleep(1) 
    except Exception:
        pass

def load_config():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(current_dir, 'config.json')
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def push_to_github():
    print("\n📤 启动 GitHub 自动同步 (LWW Feeds)...")
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    try:
        subprocess.run(["git", "add", "*.xml"], cwd=current_dir, check=True)
        commit_msg = f"Auto-update LWW feeds: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=current_dir, check=True)
        subprocess.run(["git", "push"], cwd=current_dir, check=True)
        print("✅ 同步成功！LWW 数据已成功推送远端。")
    except Exception as e:
        print(f"❌ Git 同步失败: {e}")

def main():
    force_kill_edge()
    config = load_config()
    
    # 初始化 DrissionPage 配置（沿用 v3.4.1 绝对稳定的本地配置）
    co = ChromiumOptions()
    co.set_argument('--headless')
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    
    # 强制绑定本地 Mac 的真实 Edge 路径，确保护照绿灯
    co.set_browser_path('/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge')
    
    page = ChromiumPage(co)
    updated_any = False
    
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    for journal in config['journals']:
        name = journal['name']
        rss_url = journal['rss_url']
        output_filename = journal['output_filename']
        output_path = os.path.join(current_dir, output_filename)
        
        print(f"\n📡 正在抓取期刊源: {name} ...")
        
        try:
            page.get(rss_url)
            time.sleep(3) # 留出缓冲时间过 WAF 盾
            
            raw_html = page.html
            
            # 从 HTML 页面中精准提取 XML 原生代码块
            xml_match = re.search(r'<rss.*?</rss>', raw_html, re.DOTALL | re.IGNORECASE)
            
            if xml_match:
                pure_xml = xml_match.group(0)
                
                # 修复可能存在的空命名空间破损
                pure_xml = re.sub(r'xmlns:prism=""', 'xmlns:prism="http://prismstandard.org/namespaces/1.2/basic/"', pure_xml)
                
                # 抓取所有的 item 节点进行学术提纯
                items = re.findall(r'<item>.*?</item>', pure_xml, re.DOTALL)
                print(f"📦 成功捕获 {len(items)} 个文献条目。正在注入所属期数与出版时间...")
                
                for item in items:
                    new_item = item
                    
                    # 提取 LWW 独有的引文属性
                    vol_m = re.search(r'<prism:volume>(.*?)</prism:volume>', item)
                    num_m = re.search(r'<prism:number>(.*?)</prism:number>', item)
                    pub_m = re.search(r'<pubDate>(.*?)</pubDate>', item)
                    
                    vol_str = vol_m.group(1) if vol_m else ""
                    num_str = num_m.group(1) if num_m else ""
                    pub_date_str = pub_m.group(1) if pub_m else "Unknown Date"
                    
                    issue_info = f"Vol. {vol_str} No. {num_str}" if (vol_str or num_str) else "Ahead of Print"
                    
                    # =======================================================
                    # 🛠️ V3.4.2 核心去重防御补丁：完美收拢 description
                    # =======================================================
                    desc_match = re.search(r'<description>(.*?)</description>', new_item, re.DOTALL)
                    if desc_match:
                        original_desc = desc_match.group(1)
                        
                        # 强行剔除内容内部可能由于多次跑批残存的 CDATA 标签，杜绝嵌套坍塌
                        clean_inner = re.sub(r'<!\[CDATA\[|\]\]>', '', original_desc)
                        if not clean_inner.strip():
                            clean_inner = "No description available."
                        
                        # 构建单层、结构严丝合缝的干净新外壳
                        new_desc = f"<![CDATA[<b>所属期数:</b> {issue_info}<br><b>出版时间:</b> {pub_date_str}<br><br>{clean_inner.strip()}]]>"
                        
                        new_item = new_item.replace(f"<description>{original_desc}</description>", f"<description>{new_desc}</description>")
                        pure_xml = pure_xml.replace(item, new_item)
                
                # 规范化组装标准 RSS 头部骨架
                raw_xml = '<?xml version="1.0" encoding="utf-8"?>\n' + pure_xml
                raw_xml = raw_xml.replace('\u2028', '\n').replace('\u2029', '\n')
                
                # 落地到本地物理文件系统
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(raw_xml)
                print(f"✅ 成功完美提纯存盘: {output_path}")
                updated_any = True
            else:
                print(f"❌ 页面提取失败，未检测到合规的 XML 根节点。")
                
        except Exception as e:
            print(f"⚠️ 运行时异常捕获: {e}")
            
    page.quit()
    
    # 数据发生物理变更时，自动调用上方的纯图形/静默同步机制上云
    if updated_any:
        push_to_github()

if __name__ == "__main__":
    main()