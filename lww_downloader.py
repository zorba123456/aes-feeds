import os
import json
import time
import subprocess
import re
from DrissionPage import ChromiumPage, ChromiumOptions

# 全局版本号升级为 3.3.2，加入 XML 尾部精准切割手术，彻底根除“Extra content”报错
__version__ = "3.3.2-XML精准切割版"

def force_kill_edge():
    """暴力清理所有 Edge 残流进程，防止 Mac 出现未响应僵尸图标"""
    print("🧹 正在执行环境大扫除 (强杀 Edge 残留进程)...")
    try:
        subprocess.run(['killall', '-9', 'Microsoft Edge'], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        time.sleep(1) 
    except Exception:
        pass

def load_config():
    """动态获取当前脚本所在的绝对目录，精准锁定同级目录下的 config.json"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(current_dir, 'config.json')
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def push_to_github():
    """自动化 Git 管线：深入 aes-feeds 子模块内部进行精确推送"""
    print("\n📤 启动 GitHub 自动同步 (LWW Feeds)...")
    
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    try:
        subprocess.run(["git", "add", "*.xml"], cwd=current_dir, check=True)
        commit_msg = f"Auto-update LWW feeds: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=current_dir, check=True)
        subprocess.run(["git", "push"], cwd=current_dir, check=True)
        print("✅ 同步成功！LWW 数据已成功推送至 aes-feeds 独立仓库。")
    except subprocess.CalledProcessError as e:
        print("ℹ️ 未检测到新文献或同步无变动，跳过推送。")

def download_403_feeds():
    config = load_config()
    
    print("=" * 45)
    print(f"🚀 启动 LWW 强攻与回输管线 [v{__version__}]")
    print("=" * 45)
    
    co = ChromiumOptions()
    edge_path = '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'
    co.set_browser_path(edge_path)
    
    co.headless(False) 
    co.set_argument('--no-sandbox')    
    co.set_argument('--disable-gpu')   
    co.set_local_port(9666) 
    
    try:
        page = ChromiumPage(co)
    except Exception as e:
        print(f"💥 Edge 内核启动失败！详情: {e}")
        return

    output_dir = os.path.dirname(os.path.abspath(__file__))
    journals = config.get('lww_journals', [])
    updated_any = False 
    
    for journal in journals:
        name = journal['name']
        url = journal['url']
        output_path = os.path.join(output_dir, f"{name}.xml")
        
        print(f"\n📡 正在抓取: {name}")
        
        try:
            page.get(url)
            success = False
            
            for attempt in range(2):
                if success: break
                if attempt == 1:
                    page.refresh()
                    time.sleep(2) 
                
                for i in range(20):
                    raw_xml = page.html
                    if any(tag in raw_xml.lower() for tag in ["<rss", "<feed", "<?xml", "cdata"]):
                        success = True
                        break
                    
                    try:
                        cf_frame = page.get_frame('@src^https://challenges.cloudflare.com', timeout=0.5)
                        if cf_frame:
                            box = cf_frame.ele('.mark', timeout=0.5) or cf_frame.ele('t:label', timeout=0.5)
                            if box: box.click()
                    except: pass
                    time.sleep(1) 
            
            if success:
                # 核心修复区：只截取 <rss> 到 </rss> 之间的干净肉体，切除所有尾部注入的毒瘤代码
                start_match = re.search(r'(<rss|<feed)', raw_xml, re.IGNORECASE)
                if start_match:
                    start_idx = start_match.start()
                    
                    # 寻找真正的闭合标签位置
                    raw_lower = raw_xml.lower()
                    end_rss = raw_lower.rfind('</rss>')
                    end_feed = raw_lower.rfind('</feed>')
                    
                    if end_rss != -1:
                        end_idx = end_rss + 6  # 加上 </rss> 的长度
                    elif end_feed != -1:
                        end_idx = end_feed + 7 # 加上 </feed> 的长度
                    else:
                        end_idx = len(raw_xml)
                        
                    pure_xml = raw_xml[start_idx:end_idx]
                    
                    # 暴力剥离可能存在的旧版xml声明，重新赋予标准头
                    pure_xml = re.sub(r'<\?xml.*?\?>', '', pure_xml, flags=re.IGNORECASE).strip()
                    raw_xml = '<?xml version="1.0" encoding="utf-8"?>\n' + pure_xml
                
                raw_xml = raw_xml.replace('\u2028', '\n').replace('\u2029', '\n')
                
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(raw_xml)
                print(f"✅ 成功完美提纯存盘: {output_path}")
                updated_any = True
            else:
                print(f"❌ 抓取失败。")
                
        except Exception as e:
            print(f"⚠️ 异常: {e}")
            
    page.quit()
    
    if updated_any:
        push_to_github()
    
    print("\n" + "=" * 45)

if __name__ == "__main__":
    force_kill_edge()
    download_403_feeds()
    force_kill_edge()