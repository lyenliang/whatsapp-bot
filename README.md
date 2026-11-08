# English

A WhatsApp bot usually uses the WhatsApp Business Platform (Cloud API) as its entry point. The backend is a webhook-driven, asynchronous message-processing system that connects to an LLM and internal business systems as needed.

## Typical architecture

```
User's WhatsApp
      ↕
Meta WhatsApp Cloud API
      │ webhook (HTTPS POST)          ▲ reply via Graph API
      ▼                               │
Webhook service ──→ Message queue ──→ Worker / conversation orchestrator
(verify signature, dedupe, return 200 fast)  │
                     ┌────────────────┼────────────────┐
                     ▼                ▼                ▼
            Conversation state     LLM / RAG       Internal systems
            (Redis / database)   (knowledge base)  (CRM, orders, etc.)
```

How a message is processed

1. Receive: when a user sends a message, Meta POSTs the event to your webhook.
2. Verify and enqueue: the service verifies the X-Hub-Signature-256 signature, puts the event on a queue, and returns 200 immediately.
3. Dedupe: the worker dedupes by message ID (wamid), because webhooks may be redelivered.
4. Load context: using the user's phone number (or WhatsApp user ID) as the key, load conversation state and recent history.
5. Generate a reply: decide the reply with rules or an LLM, querying the knowledge base or internal APIs if needed.
6. Send the reply: send the message through the Graph API; status updates such as sent, delivered, read, or failed then arrive via the webhook.

Main components

| Component | Purpose | Common choices |
|------|------|----------|
| WhatsApp access | Send and receive messages | Meta Cloud API, or a BSP such as Twilio or 360dialog |
| Webhook service | Receive and verify events | FastAPI, Express, Lambda |
| Message queue | Absorb spikes, retry, avoid webhook timeouts | SQS, RabbitMQ, Redis Streams |
| Conversation orchestrator | Choose between rule flows, LLM, or tools | Custom state machine, LangGraph, Rasa |
| State storage | Keep conversation state and history | Redis + PostgreSQL |
| Knowledge retrieval | Answer from company data | pgvector, Pinecone, OpenSearch |
| Media handling | Process images, voice, and documents | Object storage + OCR, speech-to-text |
| Human agents | Hand off on low confidence or complaints | Zendesk, Chatwoot, custom console |

WhatsApp-specific constraints

- 24-hour customer service window: you can reply freely within 24 hours of the user's last message; after that you can usually only send pre-approved template messages.
- Ordering and duplicates: webhooks are not guaranteed to be in order and may be redelivered, so processing must be idempotent and avoid handling the same user's messages concurrently.
- Burst messages: users often split one sentence into several messages, so bots usually wait a few seconds and merge them before sending to the LLM.
- Long processing: LLM responses are slow, so they shouldn't be processed synchronously inside the webhook request, nor streamed word by word.
- Media download: the webhook only provides a media ID; you must call the API again to get a time-limited download URL.
- AI usage policy: since January 2026, Meta restricts chatbots whose primary function is a general-purpose AI assistant; business uses such as customer service are not affected. Some regions may differ due to regulation, so check the latest terms before launch.

Unofficial approaches

Tools such as Baileys and whatsapp-web.js emulate WhatsApp Web and are common in personal projects. They skip the business onboarding process, but they violate the terms of service and risk getting the number banned, so they are not recommended for production.

Choosing by scale

- MVP: one FastAPI service + Cloud API + LLM + PostgreSQL is enough. Background tasks can replace the queue, but messages may be lost when the process restarts.
- Production: add a message queue, Redis, human handoff, and monitoring.
- Multiple departments or tenants: add a routing layer, tenant isolation, and separate knowledge bases.

## How it works

The bot is an HTTP server. WhatsApp doesn't connect to it directly; Meta relays messages in between:

```
User sends "時間"
  → Meta POSTs to your /webhook
  → do_POST verifies the signature, then returns 200
  → handle_payload extracts the message and dedupes it
  → reply_for decides the reply
  → send_text calls Meta's Graph API
  → the user receives the reply
```

| Code | Role |
|----------|------|
| `do_GET` | Proves to Meta that this URL is yours when you set up the webhook |
| `do_POST` | Receives messages and rejects forged requests using `APP_SECRET` |
| `is_new_message` | Prevents replying twice when Meta redelivers |
| `reply_for` | Decides the reply; to plug in an LLM, change only this |
| `send_text` | Sends the reply back to Meta |

To see the actual data format, start with test_bot.py. It plays the role of Meta sending webhooks and covers webhook verification, normal replies, redelivery, forged signatures, and status-update events. The outgoing request is only checked for format, not actually sent.

Connecting to real WhatsApp

1. Get the settings: from the Meta App dashboard, get a temporary Access Token, the Phone Number ID, and the App Secret. Choose any string for VERIFY_TOKEN.
2. Start the bot:
```
$env:WHATSAPP_TOKEN = "your token"
$env:PHONE_NUMBER_ID = "your Phone Number ID"
$env:APP_SECRET = "your App Secret"
$env:VERIFY_TOKEN = "any string"
python bot.py
```
3. Expose a public URL: Meta can't reach your localhost, so open another terminal and create an HTTPS tunnel:
```
cloudflared tunnel --url http://localhost:8000
```
   This requires cloudflared to be installed; ngrok also works.
4. Configure the webhook: in the Meta dashboard, go to "Use cases → Connect with customers through WhatsApp → Customize → Step 2. Production setup → Configure Webhooks", enter https://your-tunnel-url/webhook and the same VERIFY_TOKEN, click "Verify and save", then subscribe to messages under "Webhook fields" below. A quick tunnel's URL changes every time it restarts, so update it here when it does.
5. Publish the app: an unpublished app only receives the fake events sent by the dashboard's "Test" button; real messages from phones are not forwarded. Before publishing, fill in the Privacy Policy URL under "App settings → Basic".
6. Subscribe the WhatsApp Business Account (WABA) to the app: step 4 only sets which events the app receives. The WABA must also subscribe to the app before Meta forwards its messages. This doesn't always happen automatically, and only needs to be done once. The WABA ID is shown on the "Step 1. Try it out" page:
```
# Check: an empty data array means not subscribed yet
curl.exe "https://graph.facebook.com/v24.0/YOUR_WABA_ID/subscribed_apps" -H "Authorization: Bearer $env:WHATSAPP_TOKEN"
# Subscribe: returns {"success":true} on success
curl.exe -X POST "https://graph.facebook.com/v24.0/YOUR_WABA_ID/subscribed_apps" -H "Authorization: Bearer $env:WHATSAPP_TOKEN"
```
7. Test it: from a phone on the recipient list, send a message to the test number. If you sent a message but bot.py logs nothing, check cloudflared's request counters to see whether the request reached your machine (bot.py logs nothing when it returns 403 for a bad signature; if the port isn't 20241, try 20242–20245):
```
(Invoke-WebRequest http://127.0.0.1:20241/metrics -UseBasicParsing).Content -split "`n" | Select-String response_by_code
```

# 中文

WhatsApp Bot 一般會以 WhatsApp Business Platform（Cloud API） 為入口，後端則是由 webhook 驅動的非同步訊息處理系統，再視需求接上 LLM 和企業內部系統。

## 典型架構
```
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
```
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
```
$env:WHATSAPP_TOKEN = "你的權杖"
$env:PHONE_NUMBER_ID = "你的 Phone Number ID"
$env:APP_SECRET = "你的 App Secret"
$env:VERIFY_TOKEN = "自訂字串"
python bot.py
```
3. 開放公開網址： Meta 連不到你的 localhost，所以要另開一個終端機建立 HTTPS 通道：
cloudflared tunnel --url http://localhost:8000
   這需要先安裝 cloudflared；用 ngrok 也可以。
4. 設定 webhook： 在 Meta 後台「使用案例 → 透過 WhatsApp 聯繫 → 自訂 → 步驟 2. 生產設定 → 配置 Webhooks」填入 https://你的通道網址/webhook 和同一組 VERIFY_TOKEN，按「驗證並儲存」，再到下方的「Webhook 欄位」訂閱 messages。quick tunnel 的網址每次重開都會變，變了就要回來更新。
5. 發佈應用程式： 未發佈的應用程式只會收到主控板「測試」按鈕送出的假事件，手機傳的真實訊息不會轉送過來。發佈前要先在「應用程式設定 → 基本資料」填好隱私政策網址。
6. 讓 WhatsApp 商家帳號（WABA）訂閱應用程式： 第 4 步只設定了應用程式要收哪些事件，WABA 還要另外訂閱這個應用程式，Meta 才會把訊息送過來。這一步不一定會自動完成，只需做一次。WABA ID 在「步驟 1. 試試看」頁面的「訊息帳號編號」：
```
# 查詢：data 是空陣列就代表還沒訂閱
curl.exe "https://graph.facebook.com/v24.0/你的WABA_ID/subscribed_apps" -H "Authorization: Bearer $env:WHATSAPP_TOKEN"
# 訂閱：成功會回傳 {"success":true}
curl.exe -X POST "https://graph.facebook.com/v24.0/你的WABA_ID/subscribed_apps" -H "Authorization: Bearer $env:WHATSAPP_TOKEN"
```
7. 實際測試： 用已加入收件人名單的手機傳訊息給測試號碼。手機傳了訊息但 bot.py 沒有任何 log 時，可以查 cloudflared 的請求計數，確認請求有沒有到你的電腦（簽章錯誤回 403 時 bot.py 不會印 log；port 不是 20241 的話就試 20242–20245）：
```
(Invoke-WebRequest http://127.0.0.1:20241/metrics -UseBasicParsing).Content -split "`n" | Select-String response_by_code
```