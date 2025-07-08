import logging
import os
import re
import random
import json
import requests
import anthropic
from flask import Flask, request, jsonify

# Logging configuration
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
# Also set Werkzeug (Flask's) logger to DEBUG so request logs show up
logging.getLogger('werkzeug').setLevel(logging.DEBUG)

app = Flask(__name__)
app.logger.setLevel(logging.DEBUG)

# Configuration (replace with your actual credentials)
CLAUDE_API_KEY = "sk-ant-..."
CLAUDE_MODEL = "claude-3-7-sonnet-20250219"
WASSENGER_API_KEY = os.environ.get("WASSENGER_API_KEY")  # Wassenger Token
WASSENGER_GROUP_ID = os.environ.get("WASSENGER_GROUP_ID")  # Your group WID

# Booking intent keywords
BOOKING_KEYWORDS = ["预约", "book", "appointment", "预约时间"]
# URL detection
URL_PATTERN = re.compile(r'https?://\S+')

# SPIN stages mapping
SPIN_STAGES = [
    {"id": 0, "role": "question"},
    {"id": 1, "role": "question"},
    {"id": 2, "role": "question"},
    {"id": 3, "role": "question"},
    {"id": 4, "role": "close"}
]

SYSTEM_PROMPT = """
You are Coco, Ventopia’s WhatsApp Sales Assistant. Use SPIN selling and these details:

SWOT:
- Strengths: Tiered pricing appeals broadly; in-house expertise; Xiaohongshu reach.
- Weaknesses: Basic excludes TikTok; higher tiers may deter micro-SMEs; complexity managing platforms.
- Opportunities: Rapid TikTok ad growth; Xiaohongshu commerce; clear upsell path.
- Threats: Algorithm changes; competitor bundles; ad fatigue without fresh content.

Packages:
1. Basic Digital Marketing — RM3,000 (Facebook & Instagram)
2. Advanced Digital Marketing — RM7,000 (TikTok & content creation)
3. All-Inclusive Social Suite — RM9,999 (TikTok + Xiaohongshu)

Style:
- Avoid using “您”; use “你” in Chinese.
- Mirror customer's tone politely.
- Split long replies into 2–3 consecutive chunks.
- Guide toward closing, not endless questions.
"""

# In-memory SPIN context per customer
SPIN_STATE = {}
SPIN_HISTORY = {}

# Initialize Claude client
claude_client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

# Helpers
def detect_language(text):
    return 'zh' if re.search(r'[\u4e00-\u9fff]', text) else 'en'

def split_message(text, max_parts=3):
    return [text.strip()]

# Send WhatsApp reply via Wassenger
def send_whatsapp_reply(to, text):
    url = "https://api.wassenger.com/v1/messages"
    headers = {"Content-Type": "application/json", "Token": WASSENGER_API_KEY}
    payload = {"group": to, "message": text} if "@" in to else {"phone": to, "message": text}
    try:
        resp = requests.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        app.logger.info(f"Sent to {to}: {text}")
    except Exception as e:
        app.logger.error(f"Send error to {to}: {e}")

# Notify team on booking handover
def notify_handover(phone, msg):
    note = f"[Handover] 客户 {phone} 提了预约: {msg}"
    send_whatsapp_reply(WASSENGER_GROUP_ID, note)

# Generate next reply via Claude
def generate_claude_reply(phone, user_msg):
    history = SPIN_HISTORY.setdefault(phone, [])
    history.append({'speaker':'user','text':user_msg})
    messages = [{'role': 'user' if t['speaker']=='user' else 'assistant','content':t['text']} for t in history]
    response = claude_client.messages.create(model=CLAUDE_MODEL, system=SYSTEM_PROMPT, messages=messages, max_tokens=200)
    raw = response.content
    reply = ''.join(getattr(p,'text',str(p)) for p in raw) if isinstance(raw,list) else str(raw)
    reply = reply.strip().replace('您','你')
    history.append({'speaker':'assistant','text':reply})
    idx = SPIN_STATE.get(phone,0)
    if SPIN_STAGES[idx]['role'] in ['question','close']:
        SPIN_STATE[phone] = idx+1
    return split_message(reply)

# Webhook endpoint
@app.route('/webhook', methods=['POST'])
def webhook():
    payload = request.get_json(force=True) or {}
    # Log entire request body for debugging
    app.logger.info("Wassenger payload received:\n%s", json.dumps(payload, indent=2, ensure_ascii=False))

    if payload.get('event') != 'message:in:new':
        return jsonify({'status':'ignored'}), 200
    data = payload.get('data',{})
    if data.get('meta',{}).get('isGroup'):
        return jsonify({'status':'group_ignored'}), 200

    phone = data.get('fromNumber') or data.get('from','').split('@')[0]
    msg = data.get('body','').strip()
    if not phone or not msg:
        return jsonify({'status':'ignored'}), 200

    # Booking override
    if any(k.lower() in msg.lower() for k in BOOKING_KEYWORDS):
        notify_handover(phone,msg)
        ack = '好的，马上帮你转接，请稍等~' if detect_language(msg)=='zh' else 'Sure, connecting you now.'
        for part in split_message(ack): send_whatsapp_reply(phone, part)
        return jsonify({'status':'handover'}), 200

    # Link handling first-step
    if URL_PATTERN.search(msg) and SPIN_STATE.get(phone,0)==0:
        link=URL_PATTERN.search(msg).group(); lang=detect_language(msg)
        analysis_prompt = (f"请根据SWOT分析这个网站：{link}，并给出简要概述。" if lang=='zh' else f"Please analyze this website: {link} based on the SWOT framework and provide a brief summary.")
        resp=claude_client.messages.create(model=CLAUDE_MODEL, system=SYSTEM_PROMPT, messages=[{'role':'user','content':analysis_prompt}], max_tokens=200)
        raw=resp.content
        text=''.join(getattr(p,'text',str(p)) for p in raw) if isinstance(raw,list) else str(raw)
        text=text.strip().replace('您','你')
        for part in split_message(text): send_whatsapp_reply(phone, part)
        ask='现在在用哪些平台做推广？' if lang=='zh' else 'Which platforms are you currently using?'
        SPIN_STATE[phone]=1
        for part in split_message(ask): send_whatsapp_reply(phone, part)
        return jsonify({'status':'ok'}),200

    # Normal SPIN flow
    for part in generate_claude_reply(phone,msg): send_whatsapp_reply(phone,part)
    return jsonify({'status':'ok'}),200

@app.route('/', methods=['GET'])
def health_check(): return 'OK',200

if __name__ == '__main__':
    port=int(os.environ.get('PORT',5000))
    app.run(host='0.0.0.0', port=port, debug=True)
