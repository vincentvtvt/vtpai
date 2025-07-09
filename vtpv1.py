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
WASSENGER_DEVICE_ID = os.getenv("WASSENGER_DEVICE_ID")  # bot’s number

AIRTABLE_PAT = os.getenv("AIRTABLE_PAT")
AIRTABLE_BASE_ID = "appUkjxuY1a5HSSC3"
AIRTABLE_TABLE_NAME = "CustomerHistory"
AIRTABLE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_NAME}"
AIRTABLE_HEADERS = {"Authorization": f"Bearer {AIRTABLE_PAT}"}

BOOKING_KEYWORDS = ["预约", "book", "appointment", "预约时间"]
URL_PATTERN = re.compile(r'https?://\\S+')

SYSTEM_PROMPT = """
<instructions> 
你是 Angela，Ventopia 的 WhatsApp 销售助理。你的目标是利用 SPIN 销售法结合 Ventopia 全套营销方案和内部 SWOT 洞察来促成成交。

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
User: I want to learn about your marketing packages. Assistant Msg 1: Hi there! 我是 Ventopia 的 Coco。你今天想了解哪个领域— 1) 电商 2) TikTok 3) 餐饮 4) 社交媒体 5) 网站/Google 广告 6) 到店视频 7) 微信商城
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
"""

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


def save_message_to_airtable(sender, Receiver, message, role):
    data = {
        "fields": {
            "Sender": sender,
            "Receiver": Receiver,
            "Message": message,
            "Role": role
        }
    }
    try:
        resp = requests.post(AIRTABLE_URL, headers=AIRTABLE_HEADERS, json=data)
        resp.raise_for_status()
        app.logger.info(f"Saved {role} message for {Receiver}")
    except Exception as e:
        app.logger.error(f"Failed to save message to Airtable: {e}")


def fetch_last_10_history(Receiver):
    Receiver = Receiver.lstrip('+')
    params = {
        "filterByFormula": f"{{Receiver}} = '{Receiver}'",
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


def generate_claude_reply(bot_number, Receiver, user_msg):
    save_message_to_airtable(bot_number, Receiver, user_msg, "user")

    messages = fetch_last_10_history(Receiver)

    if not messages:
        lang = detect_language(user_msg)
        intro = (
            "Hi there! I’m Coco from Ventopia. Which area are you exploring today—\n1) E-commerce\n2) TikTok\n3) F&B\n4) Social media\n5) Website/Google Ads\n6) Store-Visit videos\n7) WeChat Commerce"
            if lang == 'en' else
            "你好！我是 Ventopia 的 Coco，请问你今天想了解哪方面的服务呢？\n1) 电商\n2) TikTok\n3) 餐饮\n4) 社交媒体\n5) 网站/谷歌广告\n6) 到店视频\n7) 微信商城"
        )
        save_message_to_airtable(bot_number, Receiver, intro, "assistant")
        return split_message(intro)

    response = claude_client.messages.create(
        model=CLAUDE_MODEL,
        system=SYSTEM_PROMPT,
        messages=messages,
        max_tokens=8192
    )

    reply = ''.join(getattr(p, 'text', str(p)) for p in response.content).strip().replace('您', '你')
    save_message_to_airtable(bot_number, Receiver, reply, "assistant")
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

    Receiver = (data.get('fromNumber') or data.get('from', '').split('@')[0]).lstrip('+')
    msg = data.get('body', '').strip()
    if not Receiver or not msg:
        return jsonify({'status': 'ignored'}), 200

    try:
        if any(k.lower() in msg.lower() for k in BOOKING_KEYWORDS):
            notify_handover(Receiver, msg)
            ack = ('好的，马上帮你转接，请稍等~' if detect_language(msg) == 'zh' else 'Sure, connecting you now.')
            for part in split_message(ack):
                send_whatsapp_reply(Receiver, part)
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
                send_whatsapp_reply(Receiver, part)
            ask = ('现在在用哪些平台做推广？' if lang == 'zh' else 'Which platforms are you currently using?')
            for part in split_message(ask):
                send_whatsapp_reply(Receiver, part)
            return jsonify({'status': 'ok'}), 200

        bot_number = WASSENGER_DEVICE_ID
        for part in generate_claude_reply(bot_number, Receiver, msg):
            send_whatsapp_reply(Receiver, part)
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
