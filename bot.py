"""最小 WhatsApp chatbot：收 Meta webhook，依規則回覆文字。只用 Python 標準函式庫。"""
import hashlib
import hmac
import json
import logging
import os
import threading
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

GRAPH_VERSION = "v24.0"
MAX_BODY_BYTES = 3 * 1024 * 1024

ACCESS_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")      # 呼叫 Graph API 用
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "")  # 從哪個號碼發送
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "")        # 自訂字串，webhook 設定時用
APP_SECRET = os.environ.get("APP_SECRET", "")            # 驗證請求真的來自 Meta
PORT = int(os.environ.get("PORT", "8000"))

log = logging.getLogger("bot")

# ponytail: 只存在記憶體，重啟就忘且不能跨程序；多台部署時改用 Redis
_seen_ids = set()
_seen_lock = threading.Lock()


def is_new_message(message_id):
    """Meta 可能重送 webhook，同一則訊息只處理一次。"""
    with _seen_lock:
        if message_id in _seen_ids:
            return False
        if len(_seen_ids) >= 10000:
            _seen_ids.clear()
        _seen_ids.add(message_id)
        return True


def valid_signature(body, header):
    expected = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), (header or "").encode())


def reply_for(text):
    """機器人的「大腦」。要接 LLM 時，只需要改這個函式。"""
    if text is None:
        return "目前只看得懂文字訊息。"
    command = text.strip().lower()
    if command in ("hi", "hello", "你好"):
        return "你好！輸入「時間」查詢現在時間，或傳任何文字，我會照著回覆。"
    if command == "時間":
        return datetime.now().strftime("現在時間：%Y-%m-%d %H:%M")
    return f"你說：{text}"


def send_text(to, text):
    request = urllib.request.Request(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{PHONE_NUMBER_ID}/messages",
        data=json.dumps({
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text},
        }).encode(),
        headers={
            "Authorization": f"Bearer {ACCESS_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10):
            pass
    except urllib.error.HTTPError as e:
        # Meta 的錯誤內容會說明原因，例如權杖過期或收件人不在測試名單
        log.error("發送失敗 %s：%s", e.code, e.read().decode(errors="replace"))


def handle_payload(payload):
    """webhook 也會送 delivered、read 等狀態更新；那些事件沒有 messages，會被略過。"""
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for message in change.get("value", {}).get("messages", []):
                if is_new_message(message["id"]):
                    text = message["text"]["body"] if message.get("type") == "text" else None
                    log.info("收到訊息 id=%s type=%s", message["id"], message.get("type"))
                    send_text(message["from"], reply_for(text))


class WebhookHandler(BaseHTTPRequestHandler):
    def _respond(self, status, body=b""):
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        """在 Meta 後台設定 webhook 時，Meta 會打這裡確認網址是你的。"""
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        token = query.get("hub.verify_token", [""])[0]
        if (url.path == "/webhook"
                and query.get("hub.mode", [""])[0] == "subscribe"
                and hmac.compare_digest(token.encode(), VERIFY_TOKEN.encode())):
            self._respond(200, query.get("hub.challenge", [""])[0].encode())
        else:
            self._respond(403)

    def do_POST(self):
        """使用者每傳一則訊息，Meta 就 POST 到這裡。"""
        if urlsplit(self.path).path != "/webhook":
            return self._respond(404)
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 <= length <= MAX_BODY_BYTES:
            return self._respond(413)
        body = self.rfile.read(length)
        if not valid_signature(body, self.headers.get("X-Hub-Signature-256")):
            return self._respond(403)
        try:
            payload = json.loads(body)
        except ValueError:
            return self._respond(400)

        # 先回 200 再處理，避免 Meta 因等太久而重送
        # ponytail: 程序在這之後當掉訊息就會遺失，也不保證同一使用者的順序；正式環境改用佇列
        self._respond(200)
        try:
            handle_payload(payload)
        except Exception:
            log.exception("處理 webhook 失敗")

    def log_message(self, *args):
        pass  # 不記錄請求網址，避免 verify token 出現在 log


def main():
    missing = [name for name, value in {
        "WHATSAPP_TOKEN": ACCESS_TOKEN,
        "PHONE_NUMBER_ID": PHONE_NUMBER_ID,
        "VERIFY_TOKEN": VERIFY_TOKEN,
        "APP_SECRET": APP_SECRET,
    }.items() if not value]
    if missing:
        raise SystemExit("缺少環境變數：" + ", ".join(missing))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("Webhook 監聽中：http://localhost:%s/webhook", PORT)
    ThreadingHTTPServer(("", PORT), WebhookHandler).serve_forever()


if __name__ == "__main__":
    main()
