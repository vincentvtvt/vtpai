import logging
import re
import os
import time
import requests
import anthropic
import json
from flask import Flask, request, jsonify
from enum import Enum

# ========== CONFIG ==========
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

claude_client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

URL_PATTERN = re.compile(r'(https?://[^\s,，。！]+)')
BOOKING_KEYWORDS = ["预约", "book", "appointment", "预约时间"]

SYSTEM_PROMPT = """<instructions> 
<instructions> 
你是 Richelle，Ventopia 的 WhatsApp 销售助理。你的目标是利用 SPIN 销售法结合 Ventopia 全套营销方案和内部 SWOT 洞察来促成成交。

当客户提到 marketing、packages、promotion、Ventopia 等时，请执行以下操作：

情况 & 问题
• 根据客户使用的语言问候，并在是新客户时介绍自己
• 询问：你今天想了解哪个领域—电商、TikTok、餐饮、社交媒体、网站/Google 广告、到店视频还是微信商城

影响 & 需求回报
• 根据客户的回答，呈现恰好一个套餐，结构如下：

套餐名称

主要特色（用项目符号）

价格 & 期限
• 如果客户回答 “我不确定” 或 “0”，则呈现《全方位社交和数字营销套装》概览

引导成交
• 模仿客户的语气，分成 2–3 条简短消息发送
• 以 “这个符合你的需求吗？需要我分享一个案例还是建议定制组合？” 收尾

风格规则
• 语言跟随客户最后回复的语言
• 始终使用 “你”，不要用 “您”
• 引导客户进行预约或下一步，避免无休止的开放式提问

</instructions>
<SWOT> 优势：分层定价；内部专业团队；小红书覆盖强 劣势：基础版不含 TikTok；高阶版本对微型 SME 有门槛；多平台管理复杂 机会：TikTok 广告快速增长；小红书电商爆发；明确的追加销售路径 威胁：算法变化；竞争者捆绑方案；内容疲劳 </SWOT> <Packages> 1. 电商套餐（Shopee & Lazada） - 新账号开通、店铺激活、商品上架、直播支持 - RM 5,888 / 月（至少 3 个月）
TikTok 聚焦套装

<Packages>
一站式（TikTok + Shopee + Lazada）：15-20 条视频 + 直播 — RM 13,888 / 月

TikTok 全方位运营：广告、专属经理、15-20 条剪辑 — RM 6,888 / 月

入门版 TikTok + WhatsApp：2-3 条剪辑 + 广告 — RM 3,888 / 月

基础版 TikTok 起步：15-20 条视频 + 直播 — RM 9,888 / 月

餐饮“客满”计划

标准版（3 个月）：每月 1 视频 + 1 笔记 + 4 条 FB/IG 帖子 + 广告 — RM 5,888

PRO MAX（3 个月）：每月 2 视频 + 2 笔记 + 8 条 FB/IG 帖子 + 广告 — RM 8,888

一站式社交 & 数字营销

小红书：设计、文案、3 篇达人笔记 — RM 2,288 / 月

FB & IG：设计、广告、经理 — RM 3,000 / 月

网站设计：定制页面、SEO、模块 — RM 2,500（一次性）

Google Ads & SEO：关键词、迷你站、广告、追踪 — RM 2,500 / 月

到店视频：拍摄、演员、脚本、剪辑 — RM 1,888（一次性）

平台开发

微信商城：分销商城、订单 & 佣金后台 — RM 50,000（一次性）

</Packages>
<OutputStyle> 
• 把你的回答拆分成 2–3 条独立消息 • 用简洁的项目符号呈现特色 
• 始终以一个引导性问题收尾 
</OutputStyle> 

<ExampleInteraction> 
User: I want to learn about your marketing packages. Assistant Msg 1: 你今天想了解哪个领域— 1) 电商 2) TikTok 3) 餐饮 4) 社交媒体 5) 网站/Google 广告 6) 到店视频 7) 微信商城
User: 2
Assistant Msg 2:
好的—这是我们的 TikTok 全方位运营套餐：
• 广告管理 & 专属客户经理
• 营销策略 & 脚本
• 每月 15-20 条视频剪辑
RM 6,888 / 月

Assistant Msg 3:
这个符合你的需求吗？需要我分享一个案例还是建议定制组合？
</ExampleInteraction>
</instructions>"""  # Replace with your complete prompt.

PROMPT_TEMPLATES = {
    'zh': {
        'name': "请问您的姓名或公司名？",
        'business_link': "请提供您的业务/品牌页面链接。",
        'objective': "请问您最主要想达成什么营销目标？"
    },
    'en': {
        'name': "May I have your name or company name?",
        'business_link': "Could you share your business or brand page link?",
        'objective': "What's your main marketing objective?"
    }
}

REQUIRED_FIELDS = ["name", "business_link", "objective"]

# ========= UTILS ==========
def detect_language(text):
    return 'zh' if re.search(r'[\u4e00-\u9fff]', text) else 'en'

def send_whatsapp_reply(to, text):
    url = "https://api.wassenger.com/v1/messages"
    headers = {"Content-Type": "application/json", "Token": WASSENGER_API_KEY}
    payload = {"phone": to, "message": text, "device": WASSENGER_DEVICE_ID}
    try:
        requests.post(url, json=payload, headers=headers).raise_for_status()
        app.logger.info(f"Sent to {to}: {text}")
    except Exception as e:
        app.logger.error(f"Send error to {to}: {e}")

def send_reply_with_delay(receiver, text, max_parts=3):
    # Splits long messages, sends with delay
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    if len(paras) > max_parts:
        merged = []
        per_part = len(paras) // max_parts
        extra = len(paras) % max_parts
        i = 0
        for _ in range(max_parts):
            take = per_part + (1 if extra > 0 else 0)
            merged.append("  \n\n".join(paras[i:i+take]))
            i += take
            if extra > 0:
                extra -= 1
        paras = merged
    for part in paras:
        send_whatsapp_reply(receiver, part)
        time.sleep(2)

def notify_handover(phone, msg):
    note = f"[Handover] 客户 {phone} 提了预约: {msg}"
    send_whatsapp_reply(WASSENGER_GROUP_ID, note)

def save_message_to_airtable(sender, receiver, message, role):
    data = {
        "fields": {
            "Sender": sender,
            "Receiver": receiver,
            "Message": message,
            "Role": role
        }
    }
    try:
        resp = requests.post(AIRTABLE_URL, headers=AIRTABLE_HEADERS, json=data)
        resp.raise_for_status()
        app.logger.info(f"Saved {role} message for {receiver}")
    except Exception as e:
        app.logger.error(f"Failed to save message to Airtable: {e}")

def fetch_last_10_history(receiver):
    receiver = receiver.lstrip('+')
    params = {
        "filterByFormula": f"{{Receiver}} = '{receiver}'",
        "sort[0][field]": "CreatedTime",
        "sort[0][direction]": "desc",
        "maxRecords": 10
    }
    try:
        resp = requests.get(AIRTABLE_URL, headers=AIRTABLE_HEADERS, params=params)
        resp.raise_for_status()
        records = resp.json().get("records", [])
        messages = []
        for r in reversed(records):  # oldest -> newest
            fields = r.get("fields", {})
            if fields.get("Role") == "user":
                messages.append({"role": "user", "content": fields.get("Message", "")})
            elif fields.get("Role") == "assistant":
                messages.append({"role": "assistant", "content": fields.get("Message", "")})
        return messages
    except Exception as e:
        app.logger.error(f"Failed to fetch history: {e}")
        return []

# ========= AI CONTEXT EXTRACTION ==========
def ai_extract_context_from_history(history):
    # Compose conversation text
    convo = ""
    for m in history:
        role = "User" if m["role"] == "user" else "Bot"
        convo += f"{role}: {m['content']}\n"
    prompt = (
        "You are a sales assistant. From the following WhatsApp chat history, extract as much information as possible in JSON with these keys:\n"
        "- name (customer or company name, if given)\n"
        "- business_link (any social/page link)\n"
        "- objective (customer's main marketing goal or intent)\n"
        "If info is missing, use null.\n\n"
        "Chat history:\n"
        "===\n"
        f"{convo}\n"
        "===\n"
        "Output only valid JSON."
    )
    response = claude_client.messages.create(
        model=CLAUDE_MODEL,
        system="",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=512
    )
    # Extract JSON robustly
    reply_text = ''.join(getattr(p, 'text', str(p)) for p in response.content)
    match = re.search(r'\{[\s\S]+\}', reply_text)
    if not match:
        return {}
    try:
        context = json.loads(match.group(0))
        return context
    except Exception as e:
        print("JSON parsing failed:", e)
        return {}

def get_missing_info(context):
    return [f for f in REQUIRED_FIELDS if not context.get(f)]

def ask_for_missing_info(missing, lang):
    prompts = PROMPT_TEMPLATES[lang]
    return "\n".join([prompts[f] for f in missing])

def clean_reply(response):
    text = ''.join(getattr(p, 'text', str(p)) for p in response.content).strip().replace('您', '你')
    return text

def build_swot_prompt(url, lang):
    if lang == 'zh':
        return f"请根据SWOT分析这个页面：{url}，并给出简要概述。"
    else:
        return f"Please analyze this page: {url} using SWOT and provide a brief summary."

def build_short_context(history, n=5):
    return history[-n:]

def send_handover_to_group(context):
    msg = (
        f"[Handover] 客户: {context.get('name','(未知)')}\n"
        f"页面: {context.get('business_link','(无链接)')}\n"
        f"目标: {context.get('objective','(未知)')}\n"
        f"——请跟进"
    )
    send_whatsapp_reply(WASSENGER_GROUP_ID, msg)

# ========= AGENT ROUTER ==========
class Agent(Enum):
    MANAGER = "manager"
    KNOWLEDGE = "knowledge"
    TOOLS_ANALYSIS = "tools_analysis"
    TOOLS_HANDOVER = "tools_handover"
    INFO_VALIDATOR = "info_validator"
    HUMAN = "human"

def manager_ai(user_msg, context):
    if info_validator_needed(context):
        return Agent.INFO_VALIDATOR
    elif is_booking_intent(user_msg):
        return Agent.TOOLS_HANDOVER
    elif contains_url(user_msg):
        return Agent.TOOLS_ANALYSIS
    elif is_package_or_service_query(user_msg):
        return Agent.KNOWLEDGE
    else:
        return Agent.KNOWLEDGE  # Default

def contains_url(msg):
    return bool(URL_PATTERN.search(msg))

def is_booking_intent(msg):
    return any(k.lower() in msg.lower() for k in BOOKING_KEYWORDS)

def is_package_or_service_query(msg):
    kw = ["套餐", "package", "service", "promotion", "优惠"]
    return any(k.lower() in msg.lower() for k in kw)

def info_validator_needed(context):
    return bool(get_missing_info(context))

# ========= AGENT LOGIC ==========
def knowledge_ai(history, user_msg, lang):
    messages = build_short_context(history, n=5)
    response = claude_client.messages.create(
        model=CLAUDE_MODEL,
        system=SYSTEM_PROMPT,
        messages=messages + [{'role': 'user', 'content': user_msg}],
        max_tokens=2048
    )
    return clean_reply(response)

def tools_analysis_ai(user_msg, url, lang):
    prompt = build_swot_prompt(url, lang)
    response = claude_client.messages.create(
        model=CLAUDE_MODEL,
        system=SYSTEM_PROMPT,
        messages=[{'role': 'user', 'content': prompt}],
        max_tokens=1024
    )
    analysis = clean_reply(response)
    return analysis

def tools_handover_ai(context):
    send_handover_to_group(context)
    msg = "已为你安排顾问跟进，请稍等。" if context.get('lang') == 'zh' else "Our consultant will contact you soon."
    return msg

def info_validator_ai(context, lang):
    missing = get_missing_info(context)
    if missing:
        return ask_for_missing_info(missing, lang)
    else:
        return None  # All info collected, ready for handover

# ========= MAIN WEBHOOK ==========
@app.route('/webhook', methods=['POST'])
def webhook():
    payload = request.get_json(force=True) or {}
    if payload.get('event') != 'message:in:new':
        return jsonify({'status': 'ignored'}), 200
    data = payload.get('data', {})
    if data.get('meta', {}).get('isGroup'):
        return jsonify({'status': 'group_ignored'}), 200
    receiver = (data.get('fromNumber') or data.get('from', '').split('@')[0]).lstrip('+')
    msg = data.get('body', '').strip()
    if not receiver or not msg:
        return jsonify({'status': 'ignored'}), 200

    bot_number = WASSENGER_DEVICE_ID
    save_message_to_airtable(bot_number, receiver, msg, "user")
    history = fetch_last_10_history(receiver)
    context = ai_extract_context_from_history(history)
    lang = detect_language(msg)
    context['lang'] = lang

    agent = manager_ai(msg, context)

    if agent == Agent.KNOWLEDGE:
        reply = knowledge_ai(history, msg, lang)
    elif agent == Agent.TOOLS_ANALYSIS:
        url = URL_PATTERN.search(msg).group()
        reply = tools_analysis_ai(msg, url, lang)
    elif agent == Agent.INFO_VALIDATOR:
        reply = info_validator_ai(context, lang)
        if not reply:
            reply = tools_handover_ai(context)
    elif agent == Agent.TOOLS_HANDOVER:
        reply = tools_handover_ai(context)
    else:
        reply = knowledge_ai(histor_
