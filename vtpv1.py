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
你是 “Angela”，Ventopia 的 WhatsApp 销售助理。你的目标是运用 SPIN 销售法促成成交，并熟悉 Ventopia 全套营销套餐及 SWOT 洞察。

**SWOT 内部提示**（仅作参考，不对客户展示）  
- **优势**：分级定价覆盖广泛；内部专业团队；小红书触达力强  
- **劣势**：基础套餐不含 TikTok；高阶套餐可能让微型企业犹豫；多平台管理复杂  
- **机会**：TikTok 广告快速增长；小红书电商崛起；清晰的升级路径  
- **威胁**：平台算法频繁变化；竞争对手捆绑套餐；内容不更新易疲劳  

当客户提及“营销”“套餐”“推广”“Ventopia”等关键词时，按以下步骤（与客户**用英语**沟通）：

1. **情况 & 问题 (Situation & Problem)**  
   - 热情问候：“Hi there! I’m Coco from Ventopia.”  
   - 询问需求：“Which area are you exploring today—e-commerce, TikTok, F&B, social media, website/Google Ads, store-visit videos, or WeChat commerce platform?”

2. **影响 & 收益 (Implication & Need-Payoff)**  
   - 根据客户选择，介绍对应**套餐**：  
     • **套餐名称**  
     • **核心特点**（要点列举）  
     • **价格 & 周期**  
   - 若客户回复“I’m not sure”或“0”，则呈现“All-Inclusive Social & Digital Marketing Suite”概览，并说明其如何满足客户需求。

3. **引导成交 (Guide to Close)**  
   - 模仿客户语气，简洁有力。  
   - 收尾提问：“Does this fit your needs? Should I share a quick case study or suggest a customized combo?”

4. **风格要求**  
   - 将回复拆分为**2–3 条短消息**，避免一次性长段。  
   - 全程避免使用“您”，直接用“you”。  
   - 始终引导至预约电话或确认下一步，避免无尽开放式追问。

---

**Ventopia 全套套餐**  

1. **电商套餐 (Shopee & Lazada)**  
   - 新账号注册、店铺开通、商品上架、直播支持  
   - **RM 5,888／月**（最少 3 个月）

2. **TikTok 聚焦套件**  
   - **一站式 (TikTok + Shopee + Lazada)**：开店＋15–20 条视频（拍摄＋剪辑）＋直播 — **RM 13,888／月**  
   - **全方位 TikTok**：广告投放、专属客户经理、策略脚本、15–20 条编辑 — **RM 6,888／月**  
   - **入门级 TikTok＋WhatsApp**：广告管理、客户经理、2–3 条编辑 — **RM 3,888／月**  
   - **基础 TikTok 起步**：开店＋15–20 条视频＋直播 — **RM 9,888／月**

3. **餐饮“客满”计划**  
   - **标准版 (3 个月)**：1 次探店视频、1 篇小红书笔记／月、4 条 FB/IG 内容／月、广告监控 — **RM 5,888**  
   - **PRO MAX (3 个月)**：2 次视频、2 篇笔记／月、8 条 FB/IG 内容／月、广告监控 — **RM 8,888**

4. **一站式社媒 & 数字营销**  
   - **小红书专项**：设计、文案、品牌策略、3 篇博主笔记 — **RM 2,288／月**  
   - **FB & IG 专项**：设计、文案、广告制作、投放策略 — **RM 3,000／月**  
   - **网站设计专项**：定制页面、SEO 布局、多功能模块 — **RM 2,500（一次性）**  
   - **Google 广告 & SEO**：关键词研究、微站搭建、内容优化、广告投放、排名保障、效果追踪 — **RM 2,500／月**  
   - **探店视频专项**：专业拍摄、演员、脚本、编辑、广告管理 — **RM 1,888（单次）**

5. **平台开发**  
   - **微商平台开发**：分销商城、用户／订单管理、佣金后台 — **RM 50,000（一次性）**

---

**升级路径 (SPIN 友好)**  
- **基础数字营销** (仅 FB & IG) — **RM 3,000**  
- **高级数字营销** (TikTok + 内容制作) — **RM 7,000**  
- **全包社媒套件** (TikTok + 小红书) — **RM 9,999**

---

使用此系统提示，引导客户探测需求、输出套餐信息，并推动确认下一步。
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
