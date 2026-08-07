# 自動定錨管家(AutoAnchor-Notion)
整合金融大數據、即時匯率換算及雲端自動排程，達成免開電腦、多端輸入、自動同步的理財工具。

# 實作重點
- **雲端排程**：整合 GitHub Actions 與 Linux Cron Job，每日盤前自動執行。
- **跨國資產整合**：串接 API 獲取台股、美股與 ETF 即時數據。
- **匯率全自動通算**：海外股票即時匯率轉換為新台幣計價。
- **動態風險軌道**：根據近月(22天)歷史數據統計，計算個股合理買入下限與賣出上限。 
- **Notion多端同步**：自動更新至 Notion 資料庫，支援手機與電腦端查看。 

# 使用工具
- Python 3.x (Pandas, yfinance, Requests, ZoneInfo)
- GitHub Actions (CI / CD / Scheduling)
- Notion API

# 專案結構
- `.github/workflows/notion_sync.yml` : GitHub Actions 雲端排程自動化設定檔
- `Stock.py` : 金融數據抓取、演算法計算與 Notion API 動態對齊主程式

# 執行成果
<img width="1391" height="291" alt="stock" src="https://github.com/user-attachments/assets/9f2011a2-f21b-42b3-836d-395bece0a810" />



