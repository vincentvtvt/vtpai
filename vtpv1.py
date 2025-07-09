import logging
import re
import os
import requests
import anthropic
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
app = Flask(__name__)
app.logger.setLevel(logging.DEBUG)

CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-3-7-sonnet-20250219")
WASSENGER_API_KEY = os.getenv("WASSENGER_API_KEY")
WASSENGER_GROUP_ID = os.getenv("WASSENGER_GROUP_ID")
WASSENGER_DEVICE_ID = os.getenv("WASSENGER_DEVICE_ID")  # bot's own WhatsApp number

AIRTABLE_PAT = os.getenv("AIRTABLE_PAT")
AIRTABLE_BASE_ID = "appUkjxuY1a5HSSC3"
AIRTABLE_TABLE_NAME = "CustomerHistory"
AIRTABLE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}"
AIRTABLE_HEADERS = {"Authorization": f"Bearer {AIRTABLE_PAT}"}

BOOKING_KEYWORDS = ["预约", "book", "appointment", "预约时间"]
URL_PATTERN = re.compile(r'https?://\S+')

SYSTEM_PROMPT = """<instructions> … your full system prompt … </instructions>"""

claude_client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

def detect_language(text):
    return 'zh' if re.search(r'[\u4e00-\u9fff]', text) else 'en'

def split_message(text):
    return [text.strip()]

def send_whatsapp_reply(to, text):
    url = "https://api.wassenger.com/v1/messages"
    headers = {"Content-Type": "application/json", "Token": WASSENGER_API_KEY}
    payload = {"phone": to, "message": text, "device": WASSENGER_DEVICE_ID}
    try:
        requests.post(url, json=payload, headers=headers).raise_for_status()
        app.logger.info(f"Sent to {to}: {text}")
    except Exception as e:
        app.logger.error(f"Send error to {to}: {e}")

def notify_handover(phone, msg):
    note = f"[Handover] 客户 {phone} 提了预约: {msg}"
    send_whatsapp_reply(WASSENGER_GROUP_ID, note)

def fetch_airtable_history(receiver):
    receiver = receiver.lstrip('+')
    params = {
        "filterByFormula": f"{{Receiver}} = '{receiver}'",
        "sort[0][field]": "LastUpdated",
        "sort[0][direction]": "desc",
        "maxRecords": 1
    }
    try:
        resp = requests.get(AIRTABLE_URL, headers=AIRTABLE_HEADERS, params=params)
        resp.raise_for_status()
        records = resp.json().get("records", [])
        if not records:
            return ""
        return records[0]['fields'].get("History", "")
    except Exception as e:
        app.logger.error(f"Failed to fetch Airtable history: {e}")
        return ""

def save_message_to_airtable(sender, receiver, history_text):
    data = {
        "fields": {
            "Sender": sender,
            "Receiver": receiver,
            "History": history_text
        }
    }
    try:
        resp = requests.post(AIRTABLE_URL, headers=AIRTABLE_HEADERS, json=data)
        resp.raise_for_status()
        app.logger.info(f"Saved history for {receiver}")
    except Exception as e:
        app.logger.error(f"Failed to save message to Airtable: {e}")

def generate_claude_reply(bot_number, receiver, user_msg):
    prev_history = fetch_airtable_history(receiver)
    chat_log = prev_history.strip() + "\n" if prev_history else ""
    chat_log += f"Customer: {user_msg}\n"

    if not prev_history:
        lang = detect_language(user_msg)
        intro = (
            "Hi there! I’m Coco from Ventopia. Which area are you exploring today—\n1) E-commerce\n2) TikTok\n3) F&B\n4) Social media\n5) Website/Google Ads\n6) Store-Visit videos\n7) WeChat Commerce"
            if lang == 'en' else
            "你好！我是 Ventopia 的 Coco，请问你今天想了解哪方面的服务呢？\n1) 电商\n2) TikTok\n3) 餐饮\n4) 社交媒体\n5) 网站/谷歌广告\n6) 到店视频\n7) 微信商城"
        )
        chat_log += f"Bot: {intro}"
        save_message_to_airtable(bot_number, receiver, chat_log)
        return split_message(intro)

    # build messages for Claude
    history_lines = chat_log.strip().splitlines()
    messages = []
    for line in history_lines:
        if line.startswith("Customer:"):
            messages.append({"role": "user", "content": line.replace("Customer:", "").strip()})
        elif line.startswith("Bot:"):
            messages.append({"role": "assistant", "content": line.replace("Bot:", "").strip()})

    response = claude_client.messages.create(
        model=CLAUDE_MODEL,
        system=SYSTEM_PROMPT,
        messages=messages,
        max_tokens=8192
    )

    reply = ''.join(getattr(p, 'text', str(p)) for p in response.content).strip().replace('您', '你')
    chat_log += f"\nBot: {reply}"
    save_message_to_airtable(bot_number, receiver, chat_log)

    return split_message(reply)

@app.route('/webhook', methods=['POST'])
def webhook():
    payload = request.get_json(force=True) or {}
    app.logger.debug(f"Incoming payload: {payload}")
    if payload.get('event') != 'message:in:new':
        return jsonify({'status': 'ignored'}), 200

    data = payload.get('data', {})
    if data.get('meta', {}).get('isGroup'):
        return jsonify({'status': 'group_ignored'}), 200

    receiver = (data.get('fromNumber') or data.get('from', '').split('@')[0]).lstrip('+')
    msg = data.get('body', '').strip()
    if not receiver or not msg:
        return jsonify({'status': 'ignored'}), 200

    try:
        if any(k.lower() in msg.lower() for k in BOOKING_KEYWORDS):
            notify_handover(receiver, msg)
            ack = ('好的，马上帮你转接，请稍等~' if detect_language(msg) == 'zh' else 'Sure, connecting you now.')
            for part in split_message(ack):
                send_whatsapp_reply(receiver, part)
            return jsonify({'status': 'handover'}), 200

        if URL_PATTERN.search(msg):
            link = URL_PATTERN.search(msg).group()
            lang = detect_language(msg)
            analysis_prompt = (
                f"请根据SWOT分析这个网站：{link}，并给出简要概述。" if lang == 'zh' else
                f"Please analyze this website: {link} based on the SWOT framework and provide a brief summary."
            )
            resp = claude_client.messages.create(
                model=CLAUDE_MODEL,
                system=SYSTEM_PROMPT,
                messages=[{'role': 'user', 'content': analysis_prompt}],
                max_tokens=8192
            )
            text = ''.join(getattr(p, 'text', str(p)) for p in resp.content).strip().replace('您', '你')
            for part in split_message(text):
                send_whatsapp_reply(receiver, part)
            ask = ('现在在用哪些平台做推广？' if lang == 'zh' else 'Which platforms are you currently using?')
            for part in split_message(ask):
                send_whatsapp_reply(receiver, part)
            return jsonify({'status': 'ok'}), 200

        bot_number = WASSENGER_DEVICE_ID
        for part in generate_claude_reply(bot_number, receiver, msg):
            send_whatsapp_reply(receiver, part)
        return jsonify({'status': 'ok'}), 200

    except Exception as e:
        app.logger.exception(f"Webhook error: {e}")
        return jsonify({'status': 'error', 'reason': str(e)}), 500

@app.route('/', methods=['GET'])
@app.route('/health', methods=['GET'])
def health_check():
    return 'OK', 200

if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
