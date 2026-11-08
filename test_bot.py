"""扮演 Meta 打本機 webhook，不需要帳號也不會連外。執行：python test_bot.py"""
import hashlib
import hmac
import json
import queue
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import bot

bot.APP_SECRET = "test-secret"
bot.VERIFY_TOKEN = "test-verify"
bot.ACCESS_TOKEN = "test-token"
bot.PHONE_NUMBER_ID = "123"

# 攔截真正的發送動作，改放進佇列檢查
sent = queue.Queue()
real_send_text = bot.send_text
bot.send_text = lambda to, text: sent.put((to, text))

server = ThreadingHTTPServer(("127.0.0.1", 0), bot.WebhookHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_port}/webhook"


def status_of(request):
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, ""


def post(payload, secret="test-secret"):
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return status_of(urllib.request.Request(
        base, data=body, headers={"X-Hub-Signature-256": signature}))[0]


def incoming(message_id, text):
    """Meta webhook 的真實結構（省略部分欄位）。"""
    return {"entry": [{"changes": [{"value": {"messages": [{
        "from": "886912345678", "id": message_id, "type": "text", "text": {"body": text},
    }]}}]}]}


# 1. webhook 驗證：token 正確才回傳 challenge
assert status_of(f"{base}?hub.mode=subscribe&hub.verify_token=test-verify&hub.challenge=42") == (200, "42")
assert status_of(f"{base}?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=42")[0] == 403

# 2. 正常訊息：收到後回覆
assert post(incoming("wamid.1", "哈囉")) == 200
assert sent.get(timeout=2) == ("886912345678", "你說：哈囉")

# 3. 重送同一則訊息：不重複回覆
assert post(incoming("wamid.1", "哈囉")) == 200

# 4. 簽章錯誤：拒絕，也不回覆
assert post(incoming("wamid.2", "偽造"), secret="wrong") == 403

# 5. 狀態更新事件：不回覆
assert post({"entry": [{"changes": [{"value": {"statuses": [{"status": "read"}]}}]}]}) == 200

time.sleep(0.3)
assert sent.empty()

# 6. 規則回覆
assert bot.reply_for("時間").startswith("現在時間：")
assert bot.reply_for(None) == "目前只看得懂文字訊息。"

# 7. 送往 Graph API 的請求格式
captured = {}


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def fake_urlopen(request, timeout):
    captured["request"] = request
    return FakeResponse()


urllib.request.urlopen = fake_urlopen
real_send_text("886912345678", "hi")
request = captured["request"]
assert request.full_url == f"https://graph.facebook.com/{bot.GRAPH_VERSION}/123/messages"
assert request.get_header("Authorization") == "Bearer test-token"
assert json.loads(request.data) == {
    "messaging_product": "whatsapp",
    "to": "886912345678",
    "type": "text",
    "text": {"body": "hi"},
}

server.shutdown()
print("ALL PASSED")
