WhatsApp Bot 一般會以 WhatsApp Business Platform（Cloud API） 為入口，後端則是由 webhook 驅動的非同步訊息處理系統，再視需求接上 LLM 和企業內部系統。

## 典型架構

使用者 WhatsApp
      ↕
Meta WhatsApp Cloud API
      │ webhook (HTTPS POST)          ▲ Graph API 發送回覆
      ▼                               │
Webhook 服務 ──→ 訊息佇列 ──→ Worker / 對話協調層
（驗簽、去重、快速回 200）             │
                     ┌────────────────┼────────────────┐
                     ▼                ▼                ▼
               對話狀態與紀錄       LLM / RAG       內部系統
              (Redis / 資料庫)     (知識庫)      (CRM、訂單等)

一則訊息的處理流程

1. 接收： 使用者傳訊息後，Meta 會把事件 POST 到你的 webhook。
2. 驗證與入列： 服務驗證 X-Hub-Signature-256 簽章，將事件放入佇列，並立即回 200。
3. 去重： Worker 以訊息 ID（wamid）去重，因為 webhook 可能重送。
4. 載入上下文： 以使用者電話號碼（或 WhatsApp 使用者 ID）為 key，讀取對話狀態和近期紀錄。
5. 產生回覆： 依規則或 LLM 決定回覆，必要時查知識庫或呼叫內部 API。
6. 發送回覆： 透過 Graph API 發送訊息，再由 webhook 接收 sent、delivered、read 或 failed 等狀態更新。

主要元件

| 元件 | 用途 | 常見選擇 |
|------|------|----------|
| WhatsApp 接入 | 收發訊息 | Meta Cloud API，或 Twilio、360dialog 等 BSP |
| Webhook 服務 | 接收和驗證事件 | FastAPI、Express、Lambda |
| 訊息佇列 | 削峰、重試、避免 webhook 逾時 | SQS、RabbitMQ、Redis Streams |
| 對話協調層 | 決定走規則流程、LLM 或工具 | 自寫狀態機、LangGraph、Rasa |
| 狀態儲存 | 保存對話狀態和紀錄 | Redis + PostgreSQL |
| 知識檢索 | 根據企業資料回答 | pgvector、Pinecone、OpenSearch |
| 媒體處理 | 處理圖片、語音和文件 | 物件儲存 + OCR、語音轉文字 |
| 真人客服 | 在低信心或客訴時轉接 | Zendesk、Chatwoot、自建後台 |

WhatsApp 特有的限制

- 24 小時客服視窗： 使用者最後一次傳訊後的 24 小時內可自由回覆，超過後通常只能發送預先審核的範本訊息。
- 順序和重複： webhook 不保證順序，也可能重送，所以要做到冪等，並避免同一使用者的訊息被同時處理。
- 連發訊息： 使用者常把一句話拆成幾則傳送，通常會等待幾秒、合併後再送進 LLM。
- 長時間處理： LLM 回應較慢，不適合在 webhook 請求內同步處理，也不適合靠串流逐字顯示。
- 媒體下載： webhook 只提供媒體 ID，要再透過 API 取得有時效的下載網址。
- AI 使用政策： Meta 自 2026 年 1 月起限制以通用 AI 助理為主要功能的 chatbot，客服等業務用途則不受此限。部分地區可能因監管而有差異，上線前請確認最新條款。

非官方做法

Baileys、whatsapp-web.js 這類工具是模擬 WhatsApp Web，常見於個人專案。它們不需要走商用申請流程，但違反服務條款，號碼有被封鎖的風險，不建議用於正式產品。

依規模選擇

- MVP： 一個 FastAPI 服務 + Cloud API + LLM + PostgreSQL 就夠了。用背景工作取代佇列也可以，但程序重啟時可能遺失訊息。
- 正式環境： 加上訊息佇列、Redis、真人轉接和監控。
- 多個部門或租戶： 再加上路由層、租戶隔離和獨立的知識庫。

## 運作方式

bot 本身是一個 HTTP 伺服器，WhatsApp 不會直接連到它，而是由 Meta 在中間轉送：

使用者傳「時間」
  → Meta POST 到你的 /webhook
  → do_POST 驗證簽章，然後回 200
  → handle_payload 取出訊息並去重
  → reply_for 決定回覆內容
  → send_text 呼叫 Meta 的 Graph API
  → 使用者收到回覆

| 程式位置 | 作用 |
|----------|------|
| `do_GET` | 在你設定 webhook 時，向 Meta 確認這個網址是你的 |
| `do_POST` | 接收訊息，並用 `APP_SECRET` 擋掉偽造的請求 |
| `is_new_message` | 避免 Meta 重送時回覆兩次 |
| `reply_for` | 決定回覆內容，之後要接 LLM 只需改這裡 |
| `send_text` | 把回覆送回 Meta |

想看實際的資料格式，可以從 test_bot.py 開始讀。它會扮演 Meta 發送 webhook，並涵蓋 webhook 驗證、正常回覆、重送、偽造簽章和狀態更新事件。發送請求的部分只檢查了格式，沒有真的送出。

接上真正的 WhatsApp

1. 取得設定值： 從 Meta App 後台取得臨時 Access Token、Phone Number ID 和 App Secret。VERIFY_TOKEN 則自己設定一個字串。
2. 啟動 bot：
cd C:\Users\brian.lee\Downloads\whatsapp-bot
$env:WHATSAPP_TOKEN = "你的權杖"
$env:PHONE_NUMBER_ID = "你的 Phone Number ID"
$env:APP_SECRET = "你的 App Secret"
$env:VERIFY_TOKEN = "自訂字串"
python bot.py
3. 開放公開網址： Meta 連不到你的 localhost，所以要另開一個終端機建立 HTTPS 通道：
cloudflared tunnel --url http://localhost:8000
   這需要先安裝 cloudflared；用 ngrok 也可以。
4. 設定 webhook： 在 Meta 後台的 WhatsApp 設定中填入 https://你的通道網址/webhook 和同一組 VERIFY_TOKEN，然後訂閱 messages。
5. 實際測試： 用已加入測試名單的手機傳訊息給測試號碼。