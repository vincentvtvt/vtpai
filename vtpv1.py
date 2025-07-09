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
WASSENGER_DEVICE_ID = os.getenv("WASSENGER_DEVICE_ID")

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

def split_message(text, max_parts=3):
    return [text.strip()]

def send_whatsapp_reply(to, text):
    url = "https://api.wassenger.com/v1/messages"
    headers = {"Content-Type": "application/json", "Token": WASSENGER_API_KEY}
    base_payload = {"message": text, "device": WASSENGER_DEVICE_ID}
    if "@" not in to:
        payload = {"phone": to, **base_payload}
    else:
        payload = {"group": to, **base_payload}
    try:
        requests.post(url, json=payload, headers=headers).raise_for_status()
        app.logger.info(f"Sent to {to}: {text}")
    except Exception as e:
        app.logger.error(f"Send error to {to}: {e}")

def notify_handover(phone, msg):
    note = f"[Handover] 客户 {phone} 提了预约: {msg}"
    send_whatsapp_reply(WASSENGER_GROUP_ID, note)

def fetch_airtable_history(phone):
    phone = phone.lstrip('+')
    params = {
        "filterByFormula": f"{{Phone}} = '{phone}'",
        "sort[0][field]": "LastUpdated",
        "sort[0][direction]": "desc",
        "maxRecords": 1
    }
    try:
        resp = requests.get(AIRTABLE_URL, headers=AIRTABLE_HEADERS, params=params)
        resp.raise_for_status()
        records = resp.json().get("records", [])
        if not records:
            return []
        history_text = records[0]['fields'].get("History", "")
        history_lines = history_text.split("\\n")
        history = []
        for line in history_lines:
            if line.startswith("Customer:"):
                history.append({"speaker": "user", "text": line.replace("Customer:", "").strip()})
            elif line.startswith("Bot:"):
                history.append({"speaker": "assistant", "text": line.replace("Bot:", "").strip()})
        return history
    except Exception as e:
        app.logger.error(f"Failed to fetch Airtable history: {e}")
        return []

def save_message_to_airtable(phone, history_text):
    data = {
        "fields": {
            "Phone": phone,
            "History": history_text,
            "LastUpdated": ""  # let Airtable auto-fill current time if you have formula field
        }
    }
    try:
        requests.post(AIRTABLE_URL, headers=AIRTABLE_HEADERS, json=data).raise_for_status()
    except Exception as e:
        app.logger.error(f"Failed to save message to Airtable: {e}")

def generate_claude_reply(phone, user_msg):
    history = fetch_airtable_history(phone)
    history.append({"speaker": "user", "text": user_msg})

    if len(history) == 1:  # only user_msg just appended
        lang = detect_language(user_msg)
        intro = ("Hi there! I’m Coco from Ventopia. Which area are you exploring today—\n1) E-commerce\n2) TikTok\n3) F&B\n4) Social media\n5) Website/Google Ads\n6) Store-Visit videos\n7) WeChat Commerce" if lang == 'en' else "你好！我是 Ventopia 的 Coco，请问你今天想了解哪方面的服务呢？\n1) 电商\n2) TikTok\n3) 餐饮\n4) 社交媒体\n5) 网站/谷歌广告\n6) 到店视频\n7) 微信商城")
        save_message_to_airtable(phone, f"Customer: {user_msg}\\nBot: {intro}")
        return split_message(intro)

    messages = [{"role": 'user' if h['speaker']=='user' else 'assistant', "content": h['text']} for h in history]
    response = claude_client.messages.create(model=CLAUDE_MODEL, system=SYSTEM_PROMPT, messages=messages, max_tokens=8192)
    reply = ''.join(getattr(p, 'text', str(p)) for p in response.content).strip().replace('您', '你')

    # Rebuild history_text for Airtable
    history_text = ""
    for h in history:
        prefix = "Customer" if h['speaker'] == 'user' else "Bot"
        history_text += f"{prefix}: {h['text']}\\n"
    history_text += f"Bot: {reply}"

    save_message_to_airtable(phone, history_text)
    return split_message(reply)

@app.route('/webhook', methods=['POST'])
def webhook():
    payload = request.get_json(force=True) or {}
    app.logger.debug(f"Incoming Wassenger payload: {payload}")
    if payload.get('event') != 'message:in:new':
        return jsonify({'status': 'ignored'}), 200

    data = payload.get('data', {})
    if data.get('meta', {}).get('isGroup'):
        return jsonify({'status': 'group_ignored'}), 200

    phone = (data.get('fromNumber') or data.get('from', '').split('@')[0]).lstrip('+')
    msg = data.get('body', '').strip()
    if not phone or not msg:
        return jsonify({'status': 'ignored'}), 200

    try:
        if any(k.lower() in msg.lower() for k in BOOKING_KEYWORDS):
            notify_handover(phone, msg)
            ack = ('好的，马上帮你转接，请稍等~' if detect_language(msg) == 'zh' else 'Sure, connecting you now.')
            for part in split_message(ack):
                send_whatsapp_reply(phone, part)
            return jsonify({'status': 'handover'}), 200

        if URL_PATTERN.search(msg):
            link = URL_PATTERN.search(msg).group()
            lang = detect_language(msg)
            analysis_prompt = (f"请根据SWOT分析这个网站：{link}，并给出简要概述。" if lang == 'zh' else f"Please analyze this website: {link} based on the SWOT framework and provide a brief summary.")
            resp = claude_client.messages.create(model=CLAUDE_MODEL, system=SYSTEM_PROMPT, messages=[{'role': 'user', 'content': analysis_prompt}], max_tokens=8192)
            text = ''.join(getattr(p, 'text', str(p)) for p in resp.content).strip().replace('您', '你')
            for part in split_message(text):
                send_whatsapp_reply(phone, part)
            ask = ('现在在用哪些平台做推广？' if lang == 'zh' else 'Which platforms are you currently using?')
            for part in split_message(ask):
                send_whatsapp_reply(phone, part)
            return jsonify({'status': 'ok'}), 200

        for part in generate_claude_reply(phone, msg):
            send_whatsapp_reply(phone, part)
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
