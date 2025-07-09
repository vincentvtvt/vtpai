import logging
import re
import os
import requests
import anthropic
from flask import Flask, request, jsonify

# Logging configuration
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
app = Flask(__name__)
app.logger.setLevel(logging.DEBUG)

# Configuration from environment
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-3-7-sonnet-20250219")
WASSENGER_API_KEY = os.getenv("WASSENGER_API_KEY")
WASSENGER_GROUP_ID = os.getenv("WASSENGER_GROUP_ID")
# New: specify which device to send from (Ventopia Sales)
WASSENGER_DEVICE_ID = os.getenv("WASSENGER_DEVICE_ID")

# Booking intent keywords
BOOKING_KEYWORDS = ["预约", "book", "appointment", "预约时间"]
URL_PATTERN = re.compile(r'https?://\S+')

SPIN_STAGES = [
    {"id": 0, "role": "question"},
    {"id": 1, "role": "question"},
    {"id": 2, "role": "question"},
    {"id": 3, "role": "question"},
    {"id": 4, "role": "close"}
]

SYSTEM_PROMPT = """
<instructions>
You are Coco, Ventopia’s WhatsApp Sales Assistant. Your goal is to close deals using SPIN selling while leveraging Ventopia’s full suite of marketing packages and internal SWOT insights.

When a customer mentions marketing, packages, promotion, Ventopia, etc., do the following:

1. Situation & Problem
   • Greet in English as Hi there! I’m Coco from Ventopia.
   • Ask Which area are you exploring today—e-commerce, TikTok, F&B, social media, website/Google Ads, store-visit videos, or WeChat commerce platform

2. Implication & Need-Payoff
   • Based on their answer, present exactly one package using this structure:
     - Package Name
     - Key Features (bullet points)
     - Price & Term
   • If they reply I’m not sure or 0, present the All-Inclusive Social & Digital Marketing Suite overview

3. Guide to Close
   • Mirror customer tone, split into 2–3 short messages
   • End with Does this fit your needs Should I share a case study or suggest a custom combo

4. Style Rules
   • Language English
   • Always use you, never 您
   • Steer toward booking or next steps; avoid endless open-ended questions
</instructions>

<SWOT>
Strengths: tiered pricing; in-house expertise; strong Xiaohongshu reach
Weaknesses: basic tier excludes TikTok; higher tiers may deter micro-SMEs; multi-platform management is complex
Opportunities: rapid TikTok ad growth; Xiaohongshu commerce boom; clear upsell paths
Threats: algorithm changes; competitor bundles; content fatigue
</SWOT>

<Packages>
1. E-Commerce Package (Shopee & Lazada)
   - New account setup, store activation, product listing, livestream support
   - RM 5,888 / month (min 3 months)

2. TikTok-Focused Suite
   - All-in-One (TikTok + Shopee + Lazada): 15-20 videos + livestream — RM 13,888 / month
   - Full-Service TikTok: ads, account manager, 15-20 edits — RM 6,888 / month
   - Entry-Level TikTok + WhatsApp: 2-3 edits + ads — RM 3,888 / month
   - Basic TikTok Kick-Start: 15-20 videos + livestream — RM 9,888 / month

3. F&B Full-House Plan
   - Standard (3 months): 1 video + 1 note / month + 4 FB / IG posts + ads — RM 5,888
   - PRO MAX (3 months): 2 videos + 2 notes / month + 8 FB / IG posts + ads — RM 8,888

4. One-Stop Social & Digital
   - Xiaohongshu: design, copy, 3 influencer notes — RM 2,288 / month
   - FB & IG: design, ads, manager — RM 3,000 / month
   - Website Design: custom pages, SEO, modules — RM 2,500 (one-time)
   - Google Ads & SEO: keywords, mini-site, ads, tracking — RM 2,500 / month
   - Store-Visit Video: filming, talent, script, editing — RM 1,888 (one-time)

5. Platform Dev
   - WeChat Commerce: distributor mall, order & commission backend — RM 50,000 (one-time)
</Packages>

<OutputStyle>
• Split your answer into 2–3 separate messages
• Use concise bullets for features
• Always end with a closing question
</OutputStyle>

<ExampleInteraction>
User: I want to learn about your marketing packages.
Assistant Msg 1:
Hi there! I’m Coco from Ventopia. Which area are you exploring today—
1) E-commerce
2) TikTok
3) F&B
4) Social media
5) Website/Google Ads
6) Store-Visit videos
7) WeChat Commerce

User: 2
Assistant Msg 2:
Sure—here’s our Full-Service TikTok package:
• Ad management & dedicated account manager
• Marketing strategy & scripting
• 15-20 video edits per month
RM 6,888 / month

Assistant Msg 3:
Does this fit your needs Should I share a quick case study or suggest a custom combo
</ExampleInteraction>
"""

SPIN_STATE = {}
SPIN_HISTORY = {}

claude_client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

def detect_language(text):
    return 'zh' if re.search(r'[\u4e00-\u9fff]', text) else 'en'

def split_message(text, max_parts=3):
    return [text.strip()]

def send_whatsapp_reply(to, text):
    url = "https://api.wassenger.com/v1/messages"
    headers = {
        "Content-Type": "application/json",
        "Token": WASSENGER_API_KEY
    }
    # Include the device ID to send from Ventopia Sales
    base_payload = {"message": text, "device": WASSENGER_DEVICE_ID}
    if "@" not in to:
        payload = {"phone": to, **base_payload}
    else:
        payload = {"group": to, **base_payload}
    try:
        resp = requests.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        app.logger.info(f"Sent to {to}: {text}")
    except Exception as e:
        app.logger.error(f"Send error to {to}: {e}")

def notify_handover(phone, msg):
    note = f"[Handover] 客户 {phone} 提了预约: {msg}"
    send_whatsapp_reply(WASSENGER_GROUP_ID, note)

def generate_claude_reply(phone, user_msg):
    history = SPIN_HISTORY.setdefault(phone, [])
    history.append({'speaker': 'user', 'text': user_msg})

    messages = [{'role': 'user' if turn['speaker']=='user' else 'assistant', 'content': turn['text']} for turn in history]

    response = claude_client.messages.create(
        model=CLAUDE_MODEL,
        system=SYSTEM_PROMPT,
        messages=messages,
        max_tokens=200
    )
    raw = response.content
    reply = ''.join(getattr(p, 'text', str(p)) for p in raw) if isinstance(raw, list) else str(raw)
    reply = reply.strip().replace('您', '你')

    history.append({'speaker': 'assistant', 'text': reply})
    idx = SPIN_STATE.get(phone, 0)
    if SPIN_STAGES[idx]['role'] in ['question', 'close']:
        SPIN_STATE[phone] = idx + 1

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

    phone = data.get('fromNumber') or data.get('from', '').split('@')[0]
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

        if URL_PATTERN.search(msg) and SPIN_STATE.get(phone, 0) == 0:
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
                max_tokens=200
            )
            raw = resp.content
            text = ''.join(getattr(p, 'text', str(p)) for p in raw) if isinstance(raw, list) else str(raw)
            text = text.strip().replace('您', '你')
            for part in split_message(text):
                send_whatsapp_reply(phone, part)
            ask = ('现在在用哪些平台做推广？' if lang == 'zh' else 'Which platforms are you currently using?')
            SPIN_STATE[phone] = 1
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
