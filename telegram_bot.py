import asyncio
import httpx
import logging
import base64

# 설정
TOKEN = "8663786689:AAGJ6eqW03DD_a9y4IKKY1rLDHbuYMRxfis"
API_URL = f"https://api.telegram.org/bot{TOKEN}"
SERVER_URL = "http://127.0.0.1:4825/chat"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def get_updates(client, offset=None):
    url = f"{API_URL}/getUpdates?timeout=30"
    if offset:
        url += f"&offset={offset}"
    try:
        res = await client.get(url, timeout=35)
        return res.json()
    except Exception as e:
        return None

async def send_message(client, chat_id, text):
    url = f"{API_URL}/sendMessage"
    try:
        await client.post(url, json={"chat_id": chat_id, "text": text})
    except Exception as e:
        logger.error(f"메시지 전송 실패: {e}")

async def ask_ai(client, message, image_data=None):
    try:
        payload = {"message": message, "stream": False}
        if image_data:
            payload["image_data"] = image_data
            
        res = await client.post(SERVER_URL, json=payload, timeout=300.0)
        if res.status_code == 200:
            data = res.json()
            return data.get("response", str(data))
        else:
            return f"❌ 서버 에러 ({res.status_code}): {res.text}"
    except Exception as e:
        return f"❌ 서버 연결 실패: {e}"

async def download_telegram_file(client, file_id):
    """텔레그램 서버에서 파일을 다운로드하여 Base64로 변환합니다."""
    try:
        # 1. 파일 경로 가져오기
        res = await client.get(f"{API_URL}/getFile?file_id={file_id}")
        file_info = res.json()
        file_path = file_info.get("result", {}).get("file_path")
        if not file_path:
            return None
        
        # 2. 실제 파일 다운로드
        file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
        file_res = await client.get(file_url)
        if file_res.status_code == 200:
            return base64.b64encode(file_res.content).decode("utf-8")
    except Exception as e:
        logger.error(f"Failed to download file: {e}")
    return None

async def run_bot():
    logger.info("🚀 비동기 텔레그램 봇 모드 시작!")
    last_update_id = None
    
    async with httpx.AsyncClient() as client:
        while True:
            updates = await get_updates(client, last_update_id)
            if updates and updates.get("ok"):
                for update in updates.get("result"):
                    last_update_id = update.get("update_id") + 1
                    
                    if "message" in update:
                        msg = update["message"]
                        chat_id = msg["chat"]["id"]
                        text = msg.get("text", "")
                        caption = msg.get("caption", "")
                        
                        image_data = None
                        final_prompt = text or caption or "이 이미지를 분석해줘."
                        
                        # 사진 파일 처리
                        if "photo" in msg:
                            file_id = msg["photo"][-1]["file_id"]
                            await send_message(client, chat_id, "📸 사진을 받았습니다. 분석을 시작합니다...")
                            image_data = await download_telegram_file(client, file_id)
                        
                        # 문서형태의 이미지 처리
                        elif "document" in msg and msg["document"].get("mime_type", "").startswith("image/"):
                            file_id = msg["document"]["file_id"]
                            image_data = await download_telegram_file(client, file_id)

                        if text or image_data:
                            if final_prompt == "/start":
                                await send_message(client, chat_id, "🧠 Connect AI 준비 완료! 사진을 보내시면 분석해 드립니다.")
                                continue
                            
                            asyncio.create_task(process_ai_request(client, chat_id, final_prompt, image_data))
            
            await asyncio.sleep(0.5)

async def process_ai_request(client, chat_id, user_text, image_data=None):
    """별도의 태스크로 AI 답변을 처리합니다."""
    ans = await ask_ai(client, user_text, image_data)
    logger.info(f"🤖 AI 답변 생성 완료")
    
    if not ans or len(str(ans).strip()) == 0:
        ans = "⚠️ AI가 답변을 생성하지 못했습니다."
        
    await send_message(client, chat_id, str(ans))

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass
