import os
import sys 
import requests
import yfinance as yf
from datetime import datetime 
from zoneinfo import ZoneInfo

# ==================
# 1. 基礎設定
# ==================

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID = os.getenv("DATABASE_ID")

#  雲端找不到憑證，直接終止
if not NOTION_TOKEN or not DATABASE_ID:
    print("❌ 錯誤：找不到環境變數 NOTION_TOKEN 或 DATABASE_ID！")
    print("💡 請確認您的 GitHub Secrets 已經正確設定。")
    sys.exit(1)

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

# ==================
# 2. 功能函式定義
# ==================

def delete_old_notion_records(database_id, headers):
    """自動查詢並刪除超過 1 年（365天）未更新的 Notion 資料"""
    # 計算 365 天前的時間
    one_year_ago = (datetime.datetime.now() - datetime.timedelta(days=365)).isoformat()
    
    # 向 Notion 查詢超過 1 年未更新的資料
    query_url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    query_payload = {
        "filter": {
            "property": "最後更新時間", 
            "date": {
                "before": one_year_ago 
            }
        }
    }
    
    try:
        response = requests.post(query_url, json=query_payload, headers=headers)
        
        if response.status_code == 200:
            results = response.json().get("results", [])
            if not results:
                print("✨ 檢查完畢：目前資料庫中沒有超過 1 年的過期資料。")
                return
                
            print(f"🗑️ 檢查完畢：發現 {len(results)} 筆資料已超過 1 年未更新，準備刪除...")
            
            # 將過期的資料丟到垃圾桶 
            for page in results:
                page_id = page["id"]
                update_url = f"https://api.notion.com/v1/pages/{page_id}"
                delete_payload = {"archived": True}
                
                del_response = requests.patch(update_url, json=delete_payload, headers=headers)
                if del_response.status_code == 200:
                    print(f"✅ 已成功刪除過期頁面 ID: {page_id}")
                else:
                    print(f"❌ 刪除頁面 {page_id} 失敗: {del_response.text}")
        else:
            print(f"❌ 查詢過期資料失敗，API 回傳狀態碼: {response.status_code}, 錯誤訊息: {response.text}")
            
    except Exception as e:
        print(f"❌ 執行自動清理時發生未預期的錯誤: {e}")

def get_usdtwd_rate() -> float:
    """抓取即時美金兌台幣匯率，並附帶安全防錯機制"""
    try:
        fx = yf.Ticker("USDTWD=X")
        fx_hist = fx.history(period="1d")
        if not fx_hist.empty:
            rate = float(fx_hist["Close"].iloc[-1])
            print(f"💱 成功取得即時美金匯率：1 USD = {round(rate, 2)} TWD")
            return rate
    except Exception as e:
        print(f"⚠️ 抓取美金匯率失敗 ({e})，將啟動安全防錯，暫以原幣別計價。")
    return 1.0

def query_notion_stock(symbol: str) -> dict:
    """檢查 Notion 中是否已有該股票。有就回傳 page_id 資訊，沒有就回傳 None"""
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    payload = {
        "filter": {
            "property": "名稱",
            "title": {
                "equals": symbol
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
                
                # 需要算出已經有的股數，才做加減法
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
    """從 Notion 資料庫抓取目前已經建立的所有股票代號與股數"""
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
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

                # 股票格式為 "台積電 (2330.TW)" ->  "2330.TW"
                if "(" in raw_title and raw_title.endswith(")"):
                    symbol = raw_title.split("(")[-1].replace(")", "").strip()
                else:
                    # 在 Notion 新增代號，直接打 "2317" 或 "AAPL"
                    symbol = raw_title
                    # 輸入純數字，自動補上台股尾碼
                    if symbol and symbol[0].isdigit() and not symbol.endswith(".TW") and not symbol.endswith(".TWO"):
                        symbol = f"{symbol}.TW"
                        
                # 抓取購買股數
                shares = props.get("購買股數", {}).get("number", 0)
                shares = int(shares) if shares is not None else 0
                
                portfolio.append({
                    "symbol": symbol,
                    "shares": shares,
                    "page_id": page_id
                })
            return portfolio
    except Exception as e:
        print(f"❌ 自動讀取 Notion 發生錯誤: {e}")
    return portfolio

def get_stock_data(symbol: str, shares: int, fx_rate: float) -> dict:
    """透過 yfinance 抓取即時數據、月波動區間、配息並自動產生時間戳記"""
    print(f"🔍 正在抓取 {symbol} 的數據...")
    try:
        stock = yf.Ticker(symbol)
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
            
        # 判斷台股不乘以匯率，其餘國外資產統一換算。
        is_taiwan_stock = symbol.upper().endswith(".TW") or symbol.upper().endswith(".TWO")
        current_multiplier = 1.0 if is_taiwan_stock else fx_rate
        
        if not is_taiwan_stock and fx_rate != 1.0:
            print(f"💵 {symbol} 若為國外資產，所有財務標的自動乘上匯率換算新台幣...")

        # 全部同步乘上匯率
        current_price *= current_multiplier
        avg_22d *= current_multiplier
        lower_bound *= current_multiplier
        upper_bound *= current_multiplier
        
        latest_dividend_twd = 0.0
        frequency = 1    
        # 配息換算
        dividend_info_str = "暫無配息資料"
        actions = stock.actions
        if actions is not None and not actions.empty and "Dividends" in actions.columns:
            dividends = actions[actions["Dividends"] > 0]
        else:
            import pandas as pd
            dividends = pd.DataFrame()

        if not dividends.empty:
                latest_action_time = dividends.index[-1]
                latest_dividend = float(dividends["Dividends"].iloc[-1])
                
                # 配息金額也乘上匯率
                latest_dividend_twd = latest_dividend * current_multiplier
                dividend_date_str = latest_action_time.strftime("%m/%d")
                
                # 不顯示TWD(如果是國外股，已經有換算了)
                dividend_info_str = f"{round(latest_dividend_twd, 2)} ({dividend_date_str})"
        if not is_taiwan_stock:
                    frequency = 4  
        else:
            # 歷史紀錄過去 365 天內發放次數決定
            try:
                    past_year = actions.tail(10)
                    # 計算之前一整年有幾次配息
                    frequency = len(dividends[dividends.index > (datetime.now() - datetime.timedelta(days=365))])
                    if frequency == 0: frequency = 1
            except:
                    frequency = 1
                
            #計算公式：預估年領股利 = 單次配息(台幣) * 年化配息頻率 * 目前持有股數
        estimated_annual_payout_twd = latest_dividend_twd * frequency * shares

        # 改成台灣時間（UTC+8）
        taipei_time = datetime.now(ZoneInfo("Asia/Taipei"))
        update_time_str = taipei_time.strftime("%Y-%m-%d %H:%M")
        
        return {
            "symbol": symbol,
            "current_price": current_price,
            "avg_22d": avg_22d, 
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
    """將資料寫入 Notion。有 page_id 就更新，沒有就新增建立"""
    
    symbol = data.get("symbol", "")
    display_name = symbol
    if ".TW" in symbol:
        google_symbol = symbol.replace(".TW", ":TPE")
    else:
        import yfinance as yf
        stock = yf.Ticker(symbol)
        exchange = stock.info.get("exchange", "")    
        exchange_mapping = {
            "NMS": "NASDAQ",   # 納斯達克
            "NYQ": "NYSE",     # 紐約證券交易所
            "ASE": "NYSEAMEX", # 美國證券交易所
            "PCX": "NYSEARCA"  # 太平洋證券交易所
        }
        # 在對照表內就變換，沒有就維持原本的 exchange 名稱
        google_exchange = exchange_mapping.get(exchange, exchange)

        if exchange:
            google_symbol = f"{symbol}:{google_exchange}"
        else: 
            google_symbol = symbol

    google_url = f"https://google.com/finance/beta/quote/{google_symbol}?hl=zh-TW"

    # 1. 台股股票(可自行新增)
    TAIWAN_STOCK_CODES = { 
    "1101": "台泥", "1216": "統一", "1301": "台塑", "1303": "南亞", "1326": "台化", 
    "1402": "遠東新", "1590": "亞德客-KY", "1605": "華新", "2002": "中鋼", "2207": "和泰車", 
    "2301": "光寶科", "2303": "聯電", "2308": "台達電", "2317": "鴻海", "2327": "國巨", 
    "2330": "台積電", "2337": "旺宏", "2345": "智邦", "2357": "華碩", "2379": "瑞昱", 
    "2382": "廣達", "2395": "研華", "2408": "南亞科", "2412": "中華電", "2454": "聯發科", 
    "2603": "長榮", "2609": "陽明", "2615": "萬海", "2880": "華南金", "2881": "富邦金", 
    "2882": "國泰金", "2883": "開發金", "2884": "玉山金", "2885": "元大金", "2886": "兆豐金", 
    "2887": "台新金", "2890": "永豐金", "2891": "中信金", "2892": "第一金", "2912": "統一超", 
    "3008": "大立光", "3029": "零壹", "3034": "聯詠", "3037": "欣興", "3045": "台灣大", 
    "3231": "緯創", "3653": "健策", "3711": "日月光投控", "4904": "遠傳", "4915": "致伸", 
    "4938": "和碩", "5871": "中租-KY", "5876": "上海商銀", "5880": "合庫金", "6415": "矽力*-KY", 
    "6669": "緯穎", "0050": "元大台灣50", "0056": "元大高股息", "006208": "富邦台50", 
    "00878": "國泰永續高股息", "00981A": "主動統一台股增長", "00991A": "主動復華未來50", "00403A": "主動統一升級50",
    "00919": "群益台灣精選高息", "00929": "復華台灣科技優息", "00988A": "主動統一全球創新", "009816": "凱基台灣TOP 50",
    "00935": "野村臺灣新科技50", "00631L": "元大台灣50正2", "00406A": "主動中信台灣收益", "0052": "富邦科技",
    "00982A": "主動群益台灣強棒", "00992A": "主動群益科技創新", "00685L": "群益臺灣加權正2", "00713": "元大台灣高息低波", "00400A": "主動國泰動能高息",
    "00980A": "主動野村臺灣優選", "00918": "大華優利高填息30", "00888": "永豐台灣ESG", "00662": "富邦NASDAQ",
    "00984A": "主動安聯台灣高息" 
}


    try:
            symbol = data.get("symbol", "")
            display_name = stock.info.get("longName") or stock.info.get("shortName") or symbol
    except:
            symbol = data.get("symbol", "")
            display_name = symbol

    short_symbol = str(symbol.split('.')[0]).strip()
    is_taiwan_stock = '.TW' in symbol or short_symbol.isdigit() 

        # 查表與命名
    if short_symbol in TAIWAN_STOCK_CODES:
        stock_name = TAIWAN_STOCK_CODES[short_symbol]
        formatted_name = f"{stock_name} ({symbol})"
    else:
        # 美股或不在表裡的標的，保持原本格式
        if is_taiwan_stock:
            formatted_name = f"{display_name}"
        else:
            formatted_name = f"{display_name} ({symbol})"


    properties = {
        "名稱": {"title": [{"text": {"content": formatted_name}}]},
        "目前股價": {"number": round(data["current_price"], 2) if data.get("current_price") is not None else 0},
        "近月均價 ": {"number": round(data["avg_22d"], 2) if data.get("avg_22d") is not None else 0},
        "合理買入下限": {"number": round(data["lower_bound"], 2) if data.get("lower_bound") is not None else 0},
        "合理賣出上限": {"number": round(data["upper_bound"], 2) if data.get("upper_bound") is not None else 0},
        "最後更新時間": {"rich_text": [{"text": {"content": data.get("update_time")}}]},  
        "購買股數": {"number": data.get("shares", 0)},
        "配息資訊": {"rich_text": [{"text": {"content": data.get("dividend_info", "暫無資料")}}]},
        "預估年領股利": {"number": round(data.get("annual_payout", 0), 2)},
        "詳細資料連結":{ "files": [{"name": data.get("symbol", "查看行情"),"type": "external","external": {"url": google_url  # 實際的 Google Finance 長網址
            }
        }   
    ]
}
}
    if page_id:
        url = f"https://api.notion.com/v1/pages/{page_id}"
        payload = {"properties": properties}
        response = requests.patch(url, headers=HEADERS, json=payload)
        action_text = "自動更新"
    else:
        url = "https://api.notion.com/v1/pages"
        payload = {
            "parent": {"database_id": DATABASE_ID},
            "properties": properties
        }
        response = requests.post(url, headers=HEADERS, json=payload)
        action_text = "全新建立"

    if response.status_code in [200, 201]:
        print(f"✅ 成功{action_text} Notion 資料: {data['symbol']}")
    else:
        print(f"❌ {action_text} Notion 失敗: {response.status_code}, {response.text}")


# ==================
# 3. 主程式流程
# ==================

if __name__ == "__main__":
    print(f"\n=== 📈 新手投資盤前助手 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ===")
    
    # 先讀取即時美金匯率，若抓不到則會自動變回原幣別，確保主程式順利
    global_fx_rate = get_usdtwd_rate()
    portfolio = []
    # 判斷是否在 GitHub 雲端執行
    IS_GITHUB_ACTIONS = os.getenv("GITHUB_ACTIONS") == "true"

    if IS_GITHUB_ACTIONS:
        print("\n🤖 [雲端自動排程模式啟動] 正在全自動對接並更新 Notion 所有持股...")
        portfolio = fetch_all_notion_stocks()
    else:
        print("\n💡 提示：若直接按 Enter 鍵，程式將切換為「排程自動模式」，一鍵更新現有資料庫所有股票。")
        print("=== 請輸入要「新增/建立」的股票與股數（輸入 q 或直接 Enter 進入全自動更新模式） ===")
        manual_mode = False
        
        while True:
            symbol = input("輸入股票代號 (例如 2330.TW 或 AAPL): ").strip()
            if symbol.lower() in ['q', 'quit', '']:
                break

            # 輸入數字，自動補上.TW
            if symbol.isdigit():
                symbol = f"{symbol}.TW"
                print(f"🔹 找尋台股標的，已自動轉換為: {symbol}")

            # 手動輸入建立資料庫模式
            manual_mode = True  
            print(f"正在確認 Notion 資料庫中是否已有 {symbol}...")
            existing_stock = query_notion_stock(symbol)
        
            if existing_stock:
                page_id = existing_stock["page_id"]
                base_shares = existing_stock["current_shares"]
                print(f"💡 偵測到既有股票！目前 Notion 中的庫存股數為: {base_shares} 股")
                prompt_text = f" └─ 請輸入異動股數 (例如 +200、-500 或直接按 Enter 保持不變): "
            else:
                page_id = None
                base_shares = 0
                print(f"✨ 發現全新股票！")
                prompt_text = f" └─ 請輸入此新股的初始購買股數 (例如 1000): "

            while True:
                shares_input = input(prompt_text).strip()
            
                # 使用者直接按 Enter
                if not shares_input:
                    if page_id:
                        # 已有股票，繼續沿用
                        shares = base_shares
                        print(f"    ℹ️ 股數保持不變：{shares} 股")
                        break
                    else:
                        # 新股票：不允許按 Enter，必須輸入初始股數
                        print("⚠️ 新股票必須輸入初始購買股數！")
                        continue
                    
                try:
                    # 判斷是否為加減號開頭
                    is_plus = shares_input.startswith('+')
                    is_minus = shares_input.startswith('-')
                
                    # 移除非數字轉換為整數
                    clean_input = shares_input.replace('+', '').replace('-', '').strip()
                    val = int(clean_input)
                
                    if is_minus:
                        shares = base_shares - val
                    elif is_plus:
                        shares = base_shares + val
                    else:
                        if page_id:
                            # 輸入沒帶正負號的數字，當作加碼 "+200"
                            shares = base_shares + val
                            print(f"    ℹ️ 未輸入正負號，系統自動判定為加碼 +{val} 股")
                        else:
                            # 新股票如果輸入無正負號，直接當作初始值
                            shares = val

                    if shares < 0:
                        print(f"⚠️ 錯誤：扣除後總股數不能小於 0！（目前 Notion 庫存僅有 {base_shares} 股，無法扣除 {val} 股）")
                        continue

                    print(f"📝 異動後最新總股數將會變更為: {shares} 股")
                    break  # 成功輸入且計算完股數跳出
                
                except ValueError:
                    print("⚠️ 格式錯誤：請輸入正確的數字格式（如 +200、-100 或 500）。")

            # 將手動輸入的股票加入待處理
            portfolio.append({
                "symbol": symbol,
                "shares": shares,
                "page_id": page_id
            })
            print(f"🚀 已加入待處理佇列: {symbol}\n")

        # 若直接按 Enter 沒有手動輸入任何股票，則切換為自動更新模式
        if not manual_mode:
            print("\n🤖 [自動更新模式] 正在從 Notion 載入全部既有資料...")
            portfolio = fetch_all_notion_stocks()

    # 統一執行並同步更新
    if not portfolio:
        print("👋 資料庫中沒有任何標的可以處理，程式結束。")
    else:
        print(f"\n=== 🔄 開始批次處理與同步這 {len(portfolio)} 檔標的 ===")
        for item in portfolio:
            stock_data = get_stock_data(item["symbol"], item["shares"], global_fx_rate)
            if stock_data:
                upload_to_notion(stock_data, page_id=item["page_id"])
                
        print("\n🎉 全數同步完畢！請打開您的 Notion 查看資料庫。")
