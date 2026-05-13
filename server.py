"""
Connect AI MLX — Local LLM Bridge Server
Apple Silicon (M1-M5) 전용 | mlx-lm 기반
"""

import asyncio
import json
import os
import time
from pathlib import Path

try:
    import mlx.core as mx
    mx.set_default_device(mx.gpu)
    print("✅ MLX Metal context initialized in main thread.")
except ImportError:
    mx = None
from typing import AsyncIterator, Optional, List, Dict

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Cookie
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from llm_router import LLMRouter
from p_reinforce import PReinforceGardener

import json
from datetime import datetime

# ── User Management Logic ──────────────────────────────────────────────────
USERS_FILE = "users.json"
HISTORY_FILE = "chat_history.json"

class UserRegister(BaseModel):
    email: str
    password: str
    name: str
    nickname: str

class UserLogin(BaseModel):
    email: str
    password: str

def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_users(users):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)

def save_to_history(role: str, message: str):
    try:
        history = []
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
        history.append({"role": role, "content": message, "timestamp": datetime.now().isoformat()})
        if len(history) > 50: history = history[-50:]
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except: pass

def get_history():
    if not os.path.exists(HISTORY_FILE): return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except: return []

app = FastAPI(title="Connect AI MLX Server", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# UI 파일이 담길 static 폴더 연결
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.post("/register")
async def register(req: UserRegister):
    users = load_users()
    if req.email in users:
        raise HTTPException(status_code=400, detail="이미 존재하는 이메일입니다.")
    users[req.email] = {"password": req.password, "name": req.name, "nickname": req.nickname}
    save_users(users)
    return {"status": "ok", "message": "회원가입 성공!"}

@app.post("/login")
async def login(req: UserLogin):
    users = load_users()
    user = users.get(req.email)
    if not user or user["password"] != req.password:
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 틀렸습니다.")
    return {"status": "ok", "nickname": user["nickname"], "name": user["name"]}

@app.get("/history")
async def history_api():
    return get_history()

# ── Invitation Logic ────────────────────────────────────────────────────────
INVITE_CODE = "gemma-connect-ai" 

@app.get("/")
async def root(request: Request, code: Optional[str] = None, authorized: Optional[str] = Cookie(None)):
    """초대 코드가 있거나 이미 인증된 쿠키가 있는 사용자만 진입을 허용합니다."""
    # 1. 이미 쿠키로 인증된 경우
    if authorized == "true":
        return FileResponse("static/indexd.html")

    # 2. 초대 코드가 일치하는 경우 (최초 진입)
    if code == INVITE_CODE:
        response = FileResponse("static/indexd.html")
        response.set_cookie(key="authorized", value="true", max_age=60*60*24*7) 
        return response
    
    # 3. 인증 실패 시 차단 화면
    return HTMLResponse(content=f"""
        <body style="background:#0f1115; color:white; display:flex; flex-direction:column; align-items:center; justify-content:center; height:100vh; font-family:sans-serif;">
            <div style="background:#16191f; padding:40px; border-radius:24px; border:1px solid rgba(255,255,255,0.1); text-align:center; box-shadow: 0 20px 40px rgba(0,0,0,0.5);">
                <div style="font-size:48px; margin-bottom:20px;">🔒</div>
                <h1 style="color:#378ADD; margin:0; font-size:24px;">Invitation Required</h1>
                <p style="color:#94a3b8; margin:20px 0; line-height:1.6;">이 서비스는 비공개로 운영되고 있습니다.<br>선생님께 받은 <b>초대용 전용 링크</b>를 통해 접속해 주세요.</p>
                <div style="margin-top:30px; padding-top:20px; border-top:1px solid rgba(255,255,255,0.05); font-size:11px; color:rgba(255,255,255,0.2); letter-spacing:1px;">CONNECT AI SECURITY AGENT</div>
            </div>
        </body>
    """, status_code=403)

@app.get("/status")
async def status():
    """서버 상태 및 현재 로드된 모델 정보를 반환합니다."""
    return {
        "message": "🧠 Connect AI MLX Server is running!",
        "status": "online",
        "loaded_model": router._current or "None"
    }

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

router = LLMRouter()
gardener = PReinforceGardener()

@app.on_event("startup")
async def startup_event():
    """서버 시작 시 텔레그램 봇 실행 및 기본 모델 자동 로드"""
    try:
        # 1. 텔레그램 봇 실행
        from telegram_bot import run_bot
        asyncio.create_task(run_bot())
        print("🚀 Telegram Bot Bridge activated!")
        
        # 2. 기본 모델 및 어시스턴트(Draft) 모델 ID 설정
        DEFAULT_MODEL = "mlx-community/gemma-4-26b-a4b-it-4bit"
        ASSISTANT_MODEL = "mlx-community/gemma-4-26B-A4B-it-assistant-bf16"
        
        print(f"⏳ Auto-loading models (Speculative Decoding Enabled):")
        print(f"   - Target: {DEFAULT_MODEL}")
        print(f"   - Draft:  {ASSISTANT_MODEL}")
        
        asyncio.create_task(router.load_model(DEFAULT_MODEL, draft_model_id=ASSISTANT_MODEL))
        
    except Exception as e:
        print(f"⚠️ Startup sequence failed: {e}")


# ── Request / Response Models ──────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    model: Optional[str] = None
    max_tokens: int = 2048
    temperature: float = 0.2
    stream: bool = True
    context: Optional[str] = None
    image_data: Optional[str] = None     # Base64 이미지 데이터 (VLM용)


class LoadModelRequest(BaseModel):
    model_id: str                         # HuggingFace repo id 또는 로컬 경로
    adapter_path: Optional[str] = None   # LoRA adapter (선택)


class GardenRequest(BaseModel):
    raw_data: str
    category: Optional[str] = None       # 10_Wiki / 00_Raw / Skills


# ── Health & Info ──────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": "2.0.0",
        "current_model": router.current_model_id,
        "loaded_models": router.loaded_model_ids,
        "device": "Apple Silicon MLX",
    }


@app.get("/models")
async def list_models():
    """HuggingFace 추천 모델 목록 및 로드 상태 반환"""
    recommended = [
        # Qwen Series
        {"id": "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit",  "name": "Qwen 2.5 Coder 7B", "tag": "coding",  "size": "4.3GB"},
        {"id": "mlx-community/Qwen2.5-7B-Instruct-4bit",        "name": "Qwen 2.5 7B",       "tag": "general", "size": "4.3GB"},
        
        # Llama Series
        {"id": "mlx-community/Llama-3.2-3B-Instruct-4bit",      "name": "Llama 3.2 3B",      "tag": "light",   "size": "2.0GB"},
        {"id": "mlx-community/Llama-3.1-8B-Instruct-4bit",      "name": "Llama 3.1 8B",      "tag": "general", "size": "4.7GB"},
        
        # Gemma Series
        {"id": "google/gemma-4-E4B",                            "name": "Gemma 4 E4B (Latest)", "tag": "next-gen", "size": "Next-Gen"},
        {"id": "mlx-community/gemma-2-9b-it-4bit",              "name": "Gemma 2 9B",        "tag": "balanced","size": "5.4GB"},
        {"id": "mlx-community/gemma-2-2b-it-4bit",              "name": "Gemma 2 2B",        "tag": "ultra-light", "size": "1.6GB"},

        # Reasoning
        {"id": "mlx-community/DeepSeek-R1-Distill-Qwen-7B-4bit","name": "DeepSeek R1 (7B)",  "tag": "reasoning","size": "4.3GB"},
    ]
    return {
        "recommended": recommended,
        "loaded": router.loaded_model_ids,
        "current": router.current_model_id,
    }


# ── Model Management ───────────────────────────────────────────────────────────

@app.post("/models/load")
async def load_model(req: LoadModelRequest):
    """모델 로드 (이미 로드됐으면 캐시에서 즉시 반환)"""
    try:
        msg = await router.load_model(req.model_id, req.adapter_path)
        return {"status": "ok", "message": msg, "current": router.current_model_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/models/switch/{model_id:path}")
async def switch_model(model_id: str):
    """이미 로드된 모델 중 활성 모델 전환 (즉시, 재로드 없음)"""
    try:
        router.switch_model(model_id)
        return {"status": "ok", "current": router.current_model_id}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not loaded. Call /models/load first.")


@app.delete("/models/unload/{model_id:path}")
async def unload_model(model_id: str):
    """모델 언로드 → 메모리 해제"""
    router.unload_model(model_id)
    return {"status": "ok", "unloaded": model_id}


# ── Chat / Completion ──────────────────────────────────────────────────────────

@app.post("/chat")
async def chat(req: ChatRequest):
    if not router.current_model_id:
        raise HTTPException(status_code=400, detail="No model loaded. Call /models/load first.")

    if req.model and req.model != router.current_model_id:
        if req.model not in router.loaded_model_ids:
            raise HTTPException(status_code=404, detail=f"Model '{req.model}' not loaded.")
        router.switch_model(req.model)

    # 2. 지식 강화 (RAG) 적용
    context = req.context or ""
    try:
        # PReinforceGardener를 사용하여 관련 지식 추출
        knowledge_context = gardener.get_relevant_context(req.message)
        if knowledge_context:
            context += f"\n\n[LOCAL KNOWLEDGE BASE]\n{knowledge_context}"
            print(f"📖 Context reinforced with local knowledge.")
    except Exception as e:
        print(f"⚠️ Knowledge reinforcement skipped: {e}")

    # 히스토리에 사용자 메시지 저장
    save_to_history("user", req.message)

    if req.stream:
        return StreamingResponse(
            _stream_chat(req, context, req.image_data),
            media_type="text/event-stream",
            headers={"X-Model": router.current_model_id},
        )
    else:
        # 3. 대화 생성 (히스토리 포함하여 맥락 유지)
        recent_history = get_history()[-6:] # 최근 3쌍의 대화
        history_context = "\n".join([f"{m['role']}: {m['content']}" for m in recent_history])
        full_context = f"{history_context}\n{context}" if context else history_context
        
        result = await router.generate(req.message, full_context, req.max_tokens, req.temperature, req.image_data)
        
        # 히스토리에 AI 응답 저장
        save_to_history("assistant", str(result))
        
        return JSONResponse(content={"response": str(result)})

@app.get("/history")
async def fetch_history():
    """웹 화면에서 이전 대화를 불러올 수 있도록 히스토리를 반환합니다."""
    return get_history()

async def _stream_chat(req: ChatRequest, context: str = "", image_data: str = None) -> AsyncIterator[str]:
    full_response = ""
    async for chunk in router.stream_generate(req.message, context, req.max_tokens, req.temperature, image_data):
        clean_chunk = chunk
        # mlx-vlm chunks might be objects or contain metadata strings
        if hasattr(chunk, "text"):
            clean_chunk = chunk.text
        elif isinstance(chunk, str) and "text='" in chunk:
            try:
                clean_chunk = chunk.split("text='")[1].split("', token=")[0].replace('\\n', '\n').replace('\\\\n', '\n')
            except:
                pass
        
        yield f"data: {json.dumps({'chunk': clean_chunk, 'model': router.current_model_id}, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


# ── P-Reinforce Knowledge Gardener ────────────────────────────────────────────

@app.post("/garden")
async def garden(req: GardenRequest):
    """Raw 데이터를 P-Reinforce 구조로 자동 분류·저장"""
    result = await gardener.process(req.raw_data, req.category)
    return result


@app.get("/garden/tree")
async def garden_tree():
    """지식 정원 파일트리 반환"""
    return gardener.get_tree()


# ── Claude Code Integration ──────────────────────────────────────────────────

class ClaudeRequest(BaseModel):
    prompt: str
    file_path: Optional[str] = None

@app.post("/claude")
async def run_claude(req: ClaudeRequest):
    """로컬 Gemma 모델을 사용하여 Claude Code와 유사한 에이전트 기능을 수행합니다."""
    if not router.current_model_id:
        return {"status": "error", "error": "먼저 모델(Gemma 4 등)을 로드해 주세요."}

    # Claude의 페르소나와 능력을 모방하는 시스템 프롬프트
    CLAUDE_AGENT_PROMPT = """You are acting as 'Claude Code', a world-class AI coding agent.
Your goal is to provide expert-level architectural advice, bug fixes, and code improvements.
IMPORTANT: When asked to create or write code, always provide COMPLETE, production-ready source code.
Always use markdown code blocks with the correct language identifier (e.g., ```python, ```html, ```javascript, ```css).
This is crucial because the user will download these blocks as actual files.
Be analytical, precise, and practical. If multiple files are needed, provide each one in its own code block.
Always respond in a professional and helpful tone."""

    # 파일 컨텍스트 구성
    context = ""
    if req.file_path:
        # 파일 내용을 읽어올 수 있다면 읽어오기 (현재는 frontend에서 context를 줄 수도 있음)
        # 여기선 간단히 텍스트 요청으로 처리
        context = f"Target File: {req.file_path}"

    try:
        # 로컬 라우터를 통해 생성 (시스템 프롬프트를 강화하여 전달)
        full_prompt = f"{CLAUDE_AGENT_PROMPT}\n\nUser Request: {req.prompt}"
        
        # 스트리밍이 아닌 일반 생성으로 결과 반환
        result = await router.generate(
            message=full_prompt,
            context=context,
            max_tokens=2048,
            temperature=0.2
        )
        
        return {"status": "ok", "output": str(result)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── Entry Point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("🧠 Connect AI MLX Server starting on http://localhost:4825")
    uvicorn.run(app, host="0.0.0.0", port=4825, log_level="info")
