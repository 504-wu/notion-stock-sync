import os
import sys
import requests
import yfinance as yf
from datetime import datetime, timedelta

# ==========================================
# 1. 基礎設定 (請確保 Token 與 ID 填寫正確)
# ==========================================
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID = os.getenv("DATABASE_ID")

# 🏠 本地執行防禦：如果在電腦本地執行沒讀到環境變數，自動套用預設值，確保按 F5 直接能跑
if not NOTION_TOKEN:
    NOTION_TOKEN = "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"
if not DATABASE_ID:
    DATABASE_ID = "39c633f262fa80718fb3fb525d95d505"

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

# ==========================================
# 2. 功能函式定義
# ==========================================

def get_usdtwd_rate() -> float:
    """抓取即時美金兌台幣匯率"""
    try:
        fx = yf.Ticker("USDTWD=X")
        fx_hist = fx.history(period="1d")
        if not fx_hist.empty:
            rate = float(fx_hist["Close"].iloc[-1])
            print(f"💱 成功取得即時美金匯率：1 USD = {round(rate, 2)} TWD")
            return rate
    except Exception as e:
        print(f"⚠️ 抓取美金匯率失敗 ({e})，暫以原幣別計價。")
    return 1.0

def query_notion_stock(symbol: str) -> dict:
    """檢查 Notion 中是否已有該股票。有就回傳 page_id 與目前的購買股數，沒有就回傳 None"""
    url = f"https://notion.com{DATABASE_ID}/query"
    payload = {
        "filter": {
            "property": "名稱",
            "title": {
                "contains": symbol
            }
        }
    }
    try:
        response = requests.post(url, headers=HEADERS, json=payload)
        if response.status_code == 200:
            results = response.json().get("results", [])
            if results:
                page = results[0]
                props = page.get("properties", {})
                
                current_shares = props.get("購買股數", {}).get("number", 0)
                current_shares = int(current_shares) if current_shares is not None else 0
                
                return {
                    "page_id": page["id"],
                    "current_shares": current_shares
                }
    except Exception as e:
        print(f"⚠️ 查詢 Notion 發生錯誤: {e}")
    return None

def fetch_all_notion_stocks() -> list:
    """從 Notion 資料庫抓取目前裡面已經建立的所有股票代號與股數"""
    url = f"https://notion.com{DATABASE_ID}/query"
    portfolio = []
    try:
        response = requests.post(url, headers=HEADERS)
        if response.status_code == 200:
            results = response.json().get("results", [])
            for page in results:
                page_id = page["id"]
                props = page.get("properties", {})
                
                title_list = props.get("名稱", {}).get("title", [])
                if not title_list:
                    continue
                raw_title = title_list[0].get("text", {}).get("content", "").strip()
                if not raw_title:
                    continue
                
                # 自動從美化名稱中還原原始代號，例如 "🇹🇼 台積電 (2330.TW)" -> "2330.TW"
                if "(" in raw_title and raw_title.endswith(")"):
                    symbol = raw_title.split("(")[-1].replace(")", "").strip()
                else:
                    symbol = raw_title
                
                shares = props.get("購買股數", {}).get("number", 0)
                shares = int(shares) if shares is not None else 0
                
                portfolio.append({
                    "symbol": symbol,
                    "shares": shares,
                    "page_id": page_id
                })
            return portfolio
    except Exception as e:
        print(f"❌ 全自動讀取 Notion 發生錯誤: {e}")
    return portfolio

def get_stock_data(symbol: str, shares: int, fx_rate: float) -> dict:
    """透過 yfinance 抓取數據、計算波動區間，並利用自訂字典將台股代碼完美中文化"""
    try:
        stock = yf.Ticker(symbol)
        
        # 🎯 你的自訂台股大字典對照邏輯
        TAIWAN_STOCK_CODES = {
            "1101": "台泥", "1216": "統一", "1301": "台塑", "1303": "南亞",
            "1326": "台化", "1402": "遠東新", "1590": "亞德客-KY", "1605": "華新",
            "2002": "中鋼", "2207": "和泰車", "2301": "光寶科", "2303": "聯電",
            "2308": "台達電", "2317": "鴻海", "2327": "國巨", "2330": "台積電",
            "2345": "智邦", "2357": "華碩", "2379": "瑞昱", "2382": "廣達",
            "2395": "研華", "2408": "南亞科", "2412": "中華電", "2454": "聯發科",
            "2603": "長榮", "2609": "陽明", "2615": "萬海", "2880": "華南金",
            "2881": "富邦金", "2882": "國泰金", "2883": "開發金", "2884": "玉山金",
            "2885": "元大金", "2886": "兆豐金", "2887": "台新金", "2890": "永豐金",
            "2891": "中信金", "2892": "第一金", "2912": "統一超", "3008": "大立光",
            "3034": "聯詠", "3037": "欣興", "3045": "台灣大", "3231": "緯創",
            "3653": "健策", "3711": "日月光投控", "4904": "遠傳", "4938": "和碩",
            "5871": "中租-KY", "5876": "上海商銀", "5880": "合庫金", "6415": "矽力*-KY",
            "6669": "緯穎", "0050": "元大台灣50", "0056": "元大高股息", "00878": "國泰永續高股息"
        }

        hist = stock.history(period="3mo")
        if hist.empty or len(hist) < 22:
            print(f"❌ 找不到 {symbol} 的歷史數據或交易日不足 22 天")
            return None
            
        last_22_days = hist.tail(22).ffill()
        avg_22d = float(last_22_days["Close"].mean())
        std_22d = float(last_22_days["Close"].std())
        current_price = float(last_22_days["Close"].iloc[-1])
            
        lower_bound = avg_22d - (1.5 * std_22d)
        upper_bound = avg_22d + (1.5 * std_22d)
        
        if lower_bound >= current_price or upper_bound <= current_price or std_22d == 0:
            lower_bound = avg_22d * 0.95
            upper_bound = avg_22d * 1.05
            
        is_taiwan_stock = symbol.upper().endswith(".TW") or symbol.upper().endswith(".TWO")
        current_multiplier = 1.0 if is_taiwan_stock else fx_rate

        current_price *= current_multiplier
        avg_22d *= current_multiplier
        lower_bound *= current_multiplier
        upper_bound *= current_multiplier
            
        # 處理配息與估算年度總配息
        dividend_info_str = "暫無配息資料"
        estimated_annual_payout_twd = 0.0
        actions = stock.actions
        if actions is not None and not actions.empty:
            dividends = actions[actions["Dividends"] > 0]
            if not dividends.empty:
                latest_action_time = dividends.index[-1]
                latest_dividend_raw = float(dividends["Dividends"].iloc[-1])
                latest_dividend_twd = latest_dividend_raw * current_multiplier
                dividend_date_str = latest_action_time.strftime("%m/%d")
                currency_note = " 元" if is_taiwan_stock else " TWD"
                dividend_info_str = f"{round(latest_dividend_twd, 2)}{currency_note} ({dividend_date_str})"
                
                frequency = 4 if not is_taiwan_stock else 1
                if is_taiwan_stock:
                    try:
                        frequency = len(dividends[dividends.index > (datetime.now() - timedelta(days=365))])
                        if frequency == 0: frequency = 1
                    except:
                        frequency = 1
                estimated_annual_payout_twd = latest_dividend_twd * frequency * shares

        # 切出純數字簡稱 (例如 2330.TW -> 2330)
        short_symbol = symbol.split('.')[0] if '.' in symbol else symbol
        
        # 進行中文化命名
        if short_symbol in TAIWAN_STOCK_CODES:
            formatted_name = f"🇹🇼 {TAIWAN_STOCK_CODES[short_symbol]} ({symbol})"
        else:
            try:
                display_name = stock.info.get("longName") or stock.info.get("shortName") or symbol
                formatted_name = f"🇺🇸 {display_name} ({symbol})"
            except:
                formatted_name = f"📈 {symbol}"

        update_time_str = datetime.now().strftime("%Y-%m-%dT%H:%M:00+08:00")
        
        return {
            "symbol": symbol,
            "display_name": formatted_name,
            "current_price": current_price,
            "avg_5d": avg_22d, 
            "lower_bound": lower_bound,
            "upper_bound": upper_bound,
            "shares": shares,
            "dividend_info": dividend_info_str,
            "annual_payout": estimated_annual_payout_twd,
            "update_time": update_time_str
        }
    except Exception as e:
        print(f"❌ 抓取 {symbol} 失敗: {e}")
    return None

def upload_to_notion(data: dict, page_id: str = None):
    """將數據寫入 Notion"""
    yahoo_url = f"https://yahoo.com{data['symbol']}"
    properties = {
        "名稱": {"title": [{"text": {"content": data.get("display_name")}}]},
        "目前股價/指數": {"number": round(data["current_price"], 2) if data.get("current_price") is not None else 0},
        "5日參考均價": {"number": round(data["avg_5d"], 2) if data.get("avg_5d") is not None else 0},
        "合理買入下限": {"number": round(data["lower_bound"], 2) if data.get("lower_bound") is not None else 0},
        "合理賣出上限": {"number": round(data["upper_bound"], 2) if data.get("upper_bound") is not None else 0},
        "最後更新時間": {"date": {"start": data.get("update_time")}},  
        "購買股數": {"number": data.get("shares", 0)},
        "配息資訊": {"rich_text": [{"text": {"content": data.get("dividend_info", "暫無資料")}}]},
        "預估年領股利": {"number": round(data.get("annual_payout", 0), 2)},
        "詳細資料連結": {"url": yahoo_url}  
    }

    if page_id:
        url = f"https://notion.com{page_id}"
        payload = {"properties": properties}
        response = requests.patch(url, headers=HEADERS, json=payload)
        action_text = "自動更新"
    else:
        url = "https://notion.com"
        payload = {
            "parent": {"database_id": DATABASE_ID},
            "properties": properties
        }
        response = requests.post(url, headers=HEADERS, json=payload)
        action_text = "全新建立"

    if response.status_code in:
        print(f"✅ 成功{action_text} Notion 資料: {data['symbol']}")
    else:
        print(f"❌ {action_text} Notion 失敗: {response.status_code}, {response.text}")


# ==========================================
# 3. 主程式混合模組流程
# ==========================================
if __name__ == "__main__":
    print(f"\n=== 📈 散戶長期投資盤前助手 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ===")
    
    global_fx_rate = get_usdtwd_rate()
    portfolio = []
    
    # 🤖 智能環境判斷：如果是 GitHub Actions 自動執行，就切換到全自動更新名單模式
    IS_GITHUB_ACTIONS = os.getenv("GITHUB_ACTIONS") == "true"
    
    if IS_GITHUB_ACTIONS:
        print("\n🤖 [雲端自動排程模式啟動] 正在全自動對接並更新 Notion 所有持股...")
        portfolio = fetch_all_notion_stocks()
    else:
        # 🏠 本地執行：完整保留你原本好用的互動輸入系統！
        print("\n💡 提示：若直接按 Enter 鍵，程式將切換為「一鍵更新現有資料庫所有股票」。")
        print("=== 請輸入要「新增或調整庫存」的股票（輸入 q 或直接 Enter 結束手動輸入） ===")
        manual_mode = False
        
        while True:
            symbol = input("請輸入股票代號 (台股直接輸數字如 2330 / 美股輸 AAPL): ").strip()
            if symbol.lower() in ['q', 'quit', '']:
                break
                
            if symbol.isdigit():
                symbol = f"{symbol}.TW"
                print(f"🔹 偵測為台股標的，已自動轉換為: {symbol}")
                
            manual_mode = True
            print(f"正在檢查 Notion 資料庫中是否已有 {symbol}...")
            existing_stock = query_notion_stock(symbol)
            
            if existing_stock:
                page_id = existing_stock["page_id"]
                base_shares = existing_stock["current_shares"]
                print(f"💡 偵測到既有股票！目前 Notion 中的庫存股數為: {base_shares} 股")
                prompt_text = " └─ 請輸入異動股數 (例如 +200、-500 或直接按 Enter 保持不變): "
            else:
                page_id = None
                base_shares = 0
                print("✨ 發現全新股票！")
                prompt_text = " └─ 請輸入此新股的初始購買股數 (例如 1000): "
                
            while True:
                shares_input = input(prompt_text).strip()
                if not shares_input:
                    if page_id:
                        shares = base_shares
                        print(f"    ℹ️ 維持原庫存: {shares} 股")
                        break
                    else:
                        print("    ⚠️ 錯誤：全新建立的股票初始股數不能為空！")
                        continue
                        
                try:
                    is_plus = shares_input.startswith('+')
                    is_minus = shares_input.startswith('-')
                    clean_input = shares_input.replace('+', '').replace('-', '').strip()
                    val = int(clean_input)
                    
                    if is_minus:
                        shares = base_shares - val
                    elif is_plus:
                        shares = base_shares + val
                    else:
                        shares = base_shares + val if page_id else val
                        
                    if shares < 0:
                        print("    ⚠️ 錯誤：扣除後總股數不能小於 0！")
                        continue
                        
                    print(f"    📈 異動後最新總股數將會變更為: {shares} 股")
                    break
                except ValueError:
                    print("    ⚠️ 格式錯誤：請輸入正確的數字格式。")
                    
            portfolio.append({
                "symbol": symbol,
                "shares": shares,
                "page_id": page_id
            })
            print(f"🚀 已加入待處理佇列: {symbol}\n")
            
        if not manual_mode:
            print("\n🤖 [自動更新模式] 正在從 Notion 載入全部既有資料...")
            portfolio = fetch_all_notion_stocks()

    # 🚀 統一執行批次同步更新
    if not portfolio:
        print("👋 資料庫中沒有任何標的可以處理，程式結束。")
    else:
        print(f"\n=== 🔄 開始批次處理與同步這 {len(portfolio)} 檔標的 ===")
        for item in portfolio:
            stock_data = get_stock_data(item["symbol"], item["shares"], global_fx_rate)
            if stock_data:
                # 修正：確保傳入對應項目的 page_id，而不是手動模式最後一筆殘留的變數
                upload_to_notion(stock_data, page_id=item["page_id"])
                
        print("\n🎉 全數同步完畢！請打開您的 Notion 查看資料庫。")

