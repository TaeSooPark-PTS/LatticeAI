"""
Lattice AI — MCP Registry data & pure helper functions.

Extracted from server.py to reduce module size.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import httpx
from fastapi import HTTPException

# ── MCP Registry (built-in tool definitions) ─────────────────────────────────

MCP_REGISTRY = [
    {
        "id": "presentations",
        "name": "Presentations MCP",
        "category": "PPT / slides",
        "install_mode": "bundled",
        "description": "PowerPoint, Google Slides용 발표자료를 만들고 렌더링 검수까지 이어갑니다.",
        "keywords": ["ppt", "powerpoint", "slides", "slide", "deck", "presentation", "발표", "피피티", "프레젠테이션", "슬라이드", "제안서"],
        "capabilities": ["PPTX 생성", "슬라이드 구조화", "차트 중심 스토리", "렌더링 검수"],
    },
    {
        "id": "documents",
        "name": "Documents MCP",
        "category": "Docs / reports",
        "install_mode": "bundled",
        "description": "Word 문서, 보고서, 계약서 초안, 문서 redline 및 시각 검수를 처리합니다.",
        "keywords": ["docx", "word", "docs", "document", "report", "문서", "보고서", "계약서", "기획서", "레포트"],
        "capabilities": ["DOCX 생성", "문서 편집", "코멘트/수정", "PDF 렌더 확인"],
    },
    {
        "id": "spreadsheets",
        "name": "Spreadsheets MCP",
        "category": "Sheets / data",
        "install_mode": "bundled",
        "description": "Excel/CSV/Google Sheets형 데이터 분석, 수식, 표, 차트를 만듭니다.",
        "keywords": ["xlsx", "excel", "spreadsheet", "sheet", "csv", "data", "엑셀", "스프레드시트", "표", "데이터", "차트"],
        "capabilities": ["XLSX 생성", "수식/서식", "데이터 분석", "차트"],
    },
    {
        "id": "browser",
        "name": "Browser MCP",
        "category": "Web / dashboard QA",
        "install_mode": "bundled",
        "description": "로컬 웹앱, 대시보드, 폼, 페이지 렌더링을 브라우저에서 확인합니다.",
        "keywords": ["dashboard", "web", "website", "frontend", "ui", "browser", "localhost", "대시보드", "웹", "사이트", "프론트", "화면", "검수"],
        "capabilities": ["로컬 페이지 열기", "스크린샷", "DOM 검사", "UI 회귀 확인"],
    },
    {
        "id": "chrome",
        "name": "Chrome MCP",
        "category": "Browser / authenticated web",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/chrome",
        "external_url": "codex://plugins/chrome",
        "description": "사용자 Chrome 프로필, 로그인 세션, 기존 탭을 활용하는 브라우저 자동화 브리지입니다.",
        "keywords": ["chrome", "browser", "cookie", "session", "login", "크롬", "브라우저", "로그인", "세션", "탭"],
        "capabilities": ["Chrome 탭 확인", "로그인 세션 활용", "프로필 기반 웹 자동화"],
    },
    {
        "id": "computer-use",
        "name": "내 컴퓨터 MCP",
        "category": "Desktop / Mac UI",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/computer-use",
        "external_url": "codex://plugins/computer-use",
        "description": "사용자의 허용을 받아 이 컴퓨터의 파일, 화면, 앱 작업을 돕는 브리지입니다.",
        "keywords": ["computer use", "desktop", "mac", "click", "type", "scroll", "내 컴퓨터", "컴퓨터", "맥", "앱", "클릭", "타이핑"],
        "capabilities": ["Mac 앱 UI 조작", "스크린샷 기반 상태 확인", "클릭/입력/스크롤"],
    },
    {
        "id": "filesystem",
        "name": "Workspace Files MCP",
        "category": "Files / coding",
        "install_mode": "builtin",
        "description": "프로젝트 파일 읽기/쓰기, 검색, 코드 생성, 로컬 preview URL 생성을 수행합니다.",
        "keywords": ["code", "coding", "file", "folder", "project", "build", "deploy", "구현", "코드", "파일", "폴더", "프로젝트", "빌드", "배포"],
        "capabilities": ["파일 생성", "코드 검색", "빌드 스크립트", "배포 스크립트"],
    },
    {
        "id": "google-drive",
        "name": "Google Drive Connector",
        "category": "File sharing",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/google-drive",
        "external_url": "https://chatgpt.com/connectors",
        "description": "Drive/Docs/Sheets/Slides 파일 공유, 검색, 협업 워크플로에 사용합니다.",
        "keywords": ["share", "sharing", "drive", "google drive", "file share", "공유", "파일공유", "드라이브", "구글드라이브", "협업"],
        "capabilities": ["파일 공유", "Drive 검색", "Google Docs/Sheets/Slides 연결"],
    },
    {
        "id": "github",
        "name": "GitHub Connector",
        "category": "Code hosting",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/github",
        "external_url": "https://github.com/apps",
        "description": "저장소, 이슈, PR, CI 확인과 코드 배포 워크플로를 연결합니다.",
        "keywords": ["github", "repo", "repository", "pr", "pull request", "issue", "ci", "깃허브", "저장소", "이슈", "배포"],
        "capabilities": ["PR 확인", "이슈 탐색", "CI 확인", "릴리즈 준비"],
    },
    {
        "id": "slack",
        "name": "Slack Connector",
        "category": "Team sharing",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/slack",
        "external_url": "https://chatgpt.com/connectors",
        "description": "팀 채널에 결과 공유, 논의 요약, 알림 워크플로를 연결합니다.",
        "keywords": ["slack", "message", "team", "notify", "공유", "알림", "메시지", "슬랙", "팀"],
        "capabilities": ["채널 공유", "메시지 작성", "협업 알림"],
    },
    {
        "id": "obsidian-memory",
        "name": "Obsidian Memory Vault",
        "category": "Memory / knowledge",
        "install_mode": "builtin",
        "description": "Lattice AI의 장기 기억을 Obsidian 호환 Markdown vault에 저장하고 검색합니다.",
        "keywords": ["memory", "remember", "obsidian", "vault", "knowledge", "기억", "메모리", "옵시디언", "지식", "노트"],
        "capabilities": ["Markdown vault 저장", "장기 기억 검색", "Obsidian URI 힌트", "프로젝트 로그"],
    },
    {
        "id": "voice-whisper",
        "name": "Voice STT (Whisper Local)",
        "category": "Voice / speech-to-text",
        "install_mode": "pip",
        "pip_packages": ["openai-whisper"],
        "description": "로컬 음성 인식(STT) 파이프라인용 Whisper 런타임을 설치합니다.",
        "keywords": ["voice", "speech", "stt", "whisper", "audio", "음성", "인식", "자막", "전사"],
        "capabilities": ["로컬 STT 런타임", "오디오 전사 워크플로 준비"],
    },
    {
        "id": "voice-speechrecognition",
        "name": "Voice STT (SpeechRecognition)",
        "category": "Voice / speech-to-text",
        "install_mode": "pip",
        "pip_packages": ["SpeechRecognition"],
        "description": "가벼운 음성 인식 실험용 SpeechRecognition 패키지를 설치합니다.",
        "keywords": ["voice", "speech", "recognition", "stt", "microphone", "음성", "마이크", "받아쓰기"],
        "capabilities": ["STT 파이썬 패키지", "마이크 입력 인식 실험"],
    },
    {
        "id": "audio-pydub",
        "name": "Audio Processing (PyDub)",
        "category": "Voice / audio processing",
        "install_mode": "pip",
        "pip_packages": ["pydub"],
        "description": "오디오 파일 분할/정규화/포맷 변환 워크플로용 패키지를 설치합니다.",
        "keywords": ["audio", "pydub", "wav", "mp3", "전처리", "오디오", "변환"],
        "capabilities": ["오디오 전처리", "세그먼트 분할", "포맷 변환"],
    },
    {
        "id": "threejs-workflow",
        "name": "3D Workflow (Three.js)",
        "category": "3D / interactive web",
        "install_mode": "bundled",
        "description": "브라우저 검수 + 코드 생성 흐름으로 Three.js 기반 3D 화면을 구현/검증합니다.",
        "keywords": ["3d", "three", "threejs", "webgl", "scene", "3차원", "쓰리제이에스", "렌더링"],
        "capabilities": ["Three.js 코드 생성", "3D 씬 검수", "브라우저 상호작용 테스트"],
    },
    {
        "id": "figma",
        "name": "Figma Connector",
        "category": "Design / handoff",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/figma",
        "external_url": "https://chatgpt.com/connectors",
        "description": "디자인 파일 참조, 컴포넌트 규칙 확인, 구현 핸드오프를 연결합니다.",
        "keywords": ["figma", "design", "handoff", "컴포넌트", "디자인", "피그마"],
        "capabilities": ["디자인 참조", "핸드오프 워크플로", "컴포넌트 맵핑"],
    },
    {
        "id": "notion",
        "name": "Notion Connector",
        "category": "Knowledge / docs",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/notion",
        "external_url": "https://chatgpt.com/connectors",
        "description": "노션 문서/DB와 연동해 구현 노트, 회의 요약, 지식 관리 워크플로를 만듭니다.",
        "keywords": ["notion", "wiki", "docs", "database", "노션", "위키", "문서", "지식관리"],
        "capabilities": ["페이지 검색", "문서 작성 보조", "지식 동기화"],
    },
    {
        "id": "linear",
        "name": "Linear Connector",
        "category": "Project / issue tracking",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/linear",
        "external_url": "https://chatgpt.com/connectors",
        "description": "이슈 상태 확인, 우선순위 정리, 릴리즈 태스크 연결에 사용합니다.",
        "keywords": ["linear", "issue", "project", "sprint", "이슈", "태스크", "프로젝트"],
        "capabilities": ["이슈 조회", "작업 우선순위", "릴리즈 트래킹"],
    },
    {
        "id": "gmail",
        "name": "Gmail Connector",
        "category": "Communication / email",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/gmail",
        "external_url": "https://chatgpt.com/connectors",
        "description": "이메일 요약, 답장 초안, 업무 메일 정리에 사용합니다.",
        "keywords": ["gmail", "email", "mail", "inbox", "메일", "지메일", "이메일"],
        "capabilities": ["메일 검색", "요약", "답장 초안"],
    },
    {
        "id": "google-calendar",
        "name": "Google Calendar Connector",
        "category": "Scheduling / calendar",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/google-calendar",
        "external_url": "https://chatgpt.com/connectors",
        "description": "일정 확인, 미팅 슬롯 탐색, 일정 생성 워크플로를 연결합니다.",
        "keywords": ["calendar", "schedule", "meeting", "구글캘린더", "일정", "미팅"],
        "capabilities": ["일정 조회", "빈 시간 탐색", "이벤트 생성"],
    },
    {
        "id": "outlook-email",
        "name": "Outlook Email Connector",
        "category": "Communication / email",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/outlook-email",
        "external_url": "https://chatgpt.com/connectors",
        "description": "Outlook 메일함 연동, 메일 검색/초안/요약 워크플로를 제공합니다.",
        "keywords": ["outlook", "email", "mail", "아웃룩", "메일"],
        "capabilities": ["메일 검색", "요약", "초안 작성"],
    },
    {
        "id": "outlook-calendar",
        "name": "Outlook Calendar Connector",
        "category": "Scheduling / calendar",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/outlook-calendar",
        "external_url": "https://chatgpt.com/connectors",
        "description": "Outlook 일정 연동으로 회의 준비/시간 조율 작업을 진행합니다.",
        "keywords": ["outlook calendar", "calendar", "schedule", "아웃룩 캘린더", "일정"],
        "capabilities": ["일정 조회", "회의 준비", "시간 조율"],
    },
    {
        "id": "teams",
        "name": "Microsoft Teams Connector",
        "category": "Team collaboration",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/teams",
        "external_url": "https://chatgpt.com/connectors",
        "description": "팀 대화 컨텍스트 기반 업무 자동화와 협업 공유를 지원합니다.",
        "keywords": ["teams", "microsoft teams", "chat", "협업", "팀즈"],
        "capabilities": ["팀 대화 공유", "협업 흐름 연결"],
    },
    {
        "id": "sharepoint",
        "name": "SharePoint Connector",
        "category": "Enterprise files",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/sharepoint",
        "external_url": "https://chatgpt.com/connectors",
        "description": "SharePoint 문서 저장소를 검색/참조하는 엔터프라이즈 워크플로를 지원합니다.",
        "keywords": ["sharepoint", "document", "enterprise", "문서", "셰어포인트"],
        "capabilities": ["문서 검색", "사내 파일 참조"],
    },
    {
        "id": "canva",
        "name": "Canva Connector",
        "category": "Design / visuals",
        "install_mode": "connector",
        "connector_url": "/mcp/connectors/canva",
        "external_url": "https://chatgpt.com/connectors",
        "description": "디자인 템플릿 기반 이미지/슬라이드 작업을 연동합니다.",
        "keywords": ["canva", "design", "poster", "card", "캔바", "디자인"],
        "capabilities": ["디자인 템플릿", "이미지 제작 워크플로"],
    },
    # ── 데이터베이스 ─────────────────────────────────────────────────────────
    {
        "id": "mcp-postgres",
        "name": "PostgreSQL MCP",
        "category": "Database",
        "install_mode": "npm",
        "package": "@modelcontextprotocol/server-postgres",
        "description": "PostgreSQL 데이터베이스에 연결해 쿼리 실행, 스키마 탐색, 데이터 분석을 수행합니다.",
        "keywords": ["postgres", "postgresql", "database", "sql", "db", "데이터베이스", "쿼리"],
        "capabilities": ["SQL 쿼리 실행", "스키마 탐색", "테이블 분석"],
        "env_vars": [{"name": "POSTGRES_CONNECTION_STRING", "description": "postgresql://user:pass@host:5432/db"}],
    },
    {
        "id": "mcp-sqlite",
        "name": "SQLite MCP",
        "category": "Database",
        "install_mode": "npm",
        "package": "@modelcontextprotocol/server-sqlite",
        "description": "로컬 SQLite 파일에 쿼리를 실행하고 데이터를 탐색합니다.",
        "keywords": ["sqlite", "database", "sql", "local", "로컬", "데이터베이스"],
        "capabilities": ["SQLite 쿼리", "테이블 탐색", "데이터 집계"],
        "env_vars": [{"name": "SQLITE_DB_PATH", "description": "/path/to/database.db"}],
    },
    # ── 검색 / 웹 ────────────────────────────────────────────────────────────
    {
        "id": "mcp-brave-search",
        "name": "Brave Search MCP",
        "category": "Search / web",
        "install_mode": "npm",
        "package": "@modelcontextprotocol/server-brave-search",
        "description": "Brave Search API로 실시간 웹 검색 결과를 가져옵니다.",
        "keywords": ["search", "web", "brave", "websearch", "검색", "웹검색"],
        "capabilities": ["실시간 웹 검색", "뉴스 검색", "이미지 검색"],
        "env_vars": [{"name": "BRAVE_API_KEY", "description": "Brave Search API 키 (search.brave.com)"}],
    },
    {
        "id": "mcp-tavily",
        "name": "Tavily Search MCP",
        "category": "Search / web",
        "install_mode": "npm",
        "package": "tavily-mcp",
        "description": "AI 최적화 웹 검색 엔진 Tavily로 고품질 검색 결과를 가져옵니다.",
        "keywords": ["search", "tavily", "ai search", "검색", "AI검색"],
        "capabilities": ["AI 최적화 검색", "요약 검색 결과"],
        "env_vars": [{"name": "TAVILY_API_KEY", "description": "app.tavily.com에서 발급"}],
    },
    {
        "id": "mcp-puppeteer",
        "name": "Puppeteer MCP",
        "category": "Browser automation",
        "install_mode": "npm",
        "package": "@modelcontextprotocol/server-puppeteer",
        "description": "Puppeteer로 브라우저를 제어하고 웹 스크래핑, 스크린샷, 자동화를 수행합니다.",
        "keywords": ["puppeteer", "browser", "scraping", "screenshot", "automation", "스크래핑", "자동화"],
        "capabilities": ["웹 스크래핑", "스크린샷", "폼 자동화", "클릭/입력"],
    },
    # ── 배포 / 인프라 ─────────────────────────────────────────────────────────
    {
        "id": "mcp-vercel",
        "name": "Vercel MCP",
        "category": "Deployment",
        "install_mode": "npm",
        "package": "@vercel/mcp-adapter",
        "description": "Vercel 프로젝트 배포 상태 확인, 로그 조회, 환경 변수 관리를 수행합니다.",
        "keywords": ["vercel", "deploy", "deployment", "serverless", "배포", "버셀"],
        "capabilities": ["배포 상태 확인", "로그 조회", "환경 변수 관리"],
        "env_vars": [{"name": "VERCEL_API_TOKEN", "description": "Vercel 계정 토큰"}],
    },
    {
        "id": "mcp-cloudflare",
        "name": "Cloudflare MCP",
        "category": "Deployment / CDN",
        "install_mode": "npm",
        "package": "@cloudflare/mcp-server-cloudflare",
        "description": "Cloudflare Workers, KV, R2, D1 등 Cloudflare 서비스를 관리합니다.",
        "keywords": ["cloudflare", "workers", "cdn", "kv", "r2", "클라우드플레어"],
        "capabilities": ["Workers 배포", "KV/R2 관리", "DNS 조회", "D1 쿼리"],
        "env_vars": [{"name": "CLOUDFLARE_API_TOKEN", "description": "Cloudflare API 토큰"}],
    },
    {
        "id": "mcp-docker",
        "name": "Docker MCP",
        "category": "Infrastructure",
        "install_mode": "npm",
        "package": "docker-mcp",
        "description": "Docker 컨테이너 목록 조회, 실행/중지, 로그 확인을 수행합니다.",
        "keywords": ["docker", "container", "devops", "도커", "컨테이너", "인프라"],
        "capabilities": ["컨테이너 관리", "이미지 조회", "로그 확인", "실행/중지"],
    },
    # ── SaaS / 결제 ───────────────────────────────────────────────────────────
    {
        "id": "mcp-stripe",
        "name": "Stripe MCP",
        "category": "Payments",
        "install_mode": "npm",
        "package": "@stripe/agent-toolkit",
        "description": "Stripe 결제, 고객, 구독, 인보이스를 조회하고 관리합니다.",
        "keywords": ["stripe", "payment", "billing", "subscription", "결제", "스트라이프"],
        "capabilities": ["결제 조회", "고객 관리", "구독 확인", "인보이스"],
        "env_vars": [{"name": "STRIPE_SECRET_KEY", "description": "Stripe Secret Key (sk_...)"}],
    },
    {
        "id": "mcp-supabase",
        "name": "Supabase MCP",
        "category": "Database / BaaS",
        "install_mode": "npm",
        "package": "@supabase/mcp-server-supabase",
        "description": "Supabase 프로젝트의 DB 쿼리, Auth 관리, Storage 파일 접근을 수행합니다.",
        "keywords": ["supabase", "database", "auth", "storage", "supabase", "슈퍼베이스"],
        "capabilities": ["DB 쿼리", "Auth 사용자 조회", "Storage 파일 관리"],
        "env_vars": [
            {"name": "SUPABASE_URL", "description": "https://xxx.supabase.co"},
            {"name": "SUPABASE_SERVICE_ROLE_KEY", "description": "service_role 키"},
        ],
    },
    {
        "id": "mcp-hubspot",
        "name": "HubSpot MCP",
        "category": "CRM / marketing",
        "install_mode": "npm",
        "package": "@hubspot/mcp-server",
        "description": "HubSpot CRM의 연락처, 딜, 캠페인 데이터를 조회하고 분석합니다.",
        "keywords": ["hubspot", "crm", "marketing", "sales", "허브스팟", "CRM"],
        "capabilities": ["연락처 조회", "딜 파이프라인", "캠페인 분석"],
        "env_vars": [{"name": "HUBSPOT_ACCESS_TOKEN", "description": "HubSpot Private App 토큰"}],
    },
    # ── AI / 메모리 ───────────────────────────────────────────────────────────
    {
        "id": "mcp-memory",
        "name": "Memory MCP (공식)",
        "category": "Memory / knowledge",
        "install_mode": "npm",
        "package": "@modelcontextprotocol/server-memory",
        "description": "대화 간 지속 메모리를 저장하고 검색하는 공식 MCP 서버입니다.",
        "keywords": ["memory", "remember", "knowledge", "기억", "메모리", "지식"],
        "capabilities": ["장기 기억 저장", "메모리 검색", "엔티티 추적"],
    },
    {
        "id": "mcp-sequential-thinking",
        "name": "Sequential Thinking MCP",
        "category": "AI / reasoning",
        "install_mode": "npm",
        "package": "@modelcontextprotocol/server-sequential-thinking",
        "description": "복잡한 문제를 단계별로 분해해 추론하는 사고 흐름 도구입니다.",
        "keywords": ["reasoning", "thinking", "chain of thought", "추론", "사고"],
        "capabilities": ["단계별 추론", "문제 분해", "사고 흐름 추적"],
    },
    # ── 커뮤니케이션 ──────────────────────────────────────────────────────────
    {
        "id": "mcp-discord",
        "name": "Discord MCP",
        "category": "Communication",
        "install_mode": "npm",
        "package": "discord-mcp",
        "description": "Discord 서버 채널 메시지 전송, 읽기, 관리 자동화를 수행합니다.",
        "keywords": ["discord", "message", "channel", "디스코드", "메시지", "알림"],
        "capabilities": ["메시지 전송", "채널 읽기", "알림 자동화"],
        "env_vars": [{"name": "DISCORD_BOT_TOKEN", "description": "Discord Bot 토큰"}],
    },
    {
        "id": "mcp-telegram",
        "name": "Telegram MCP",
        "category": "Communication",
        "install_mode": "npm",
        "package": "telegram-mcp",
        "description": "Telegram 봇을 통한 메시지 전송, 수신, 알림 자동화를 수행합니다.",
        "keywords": ["telegram", "bot", "message", "텔레그램", "봇", "메시지"],
        "capabilities": ["메시지 전송/수신", "알림 자동화", "그룹 관리"],
        "env_vars": [{"name": "TELEGRAM_BOT_TOKEN", "description": "BotFather에서 발급한 토큰"}],
    },
    # ── 개발 도구 ─────────────────────────────────────────────────────────────
    {
        "id": "mcp-everything",
        "name": "Everything MCP (테스트)",
        "category": "Developer tools",
        "install_mode": "npm",
        "package": "@modelcontextprotocol/server-everything",
        "description": "MCP 연결 테스트용 모든 기능이 포함된 데모 서버입니다.",
        "keywords": ["test", "demo", "everything", "테스트", "개발"],
        "capabilities": ["MCP 기능 테스트", "프로토타입"],
    },
]

# ── Remote MCP Registry (registry.modelcontextprotocol.io) ───────────────────
_REMOTE_REGISTRY_CACHE: List[Dict] = []
_REMOTE_REGISTRY_FETCHED_AT: Optional[datetime] = None
_REMOTE_REGISTRY_TTL = timedelta(hours=1)
_REMOTE_REGISTRY_URL = "https://registry.modelcontextprotocol.io/v0/servers"
_LOCAL_IDS = {e["id"] for e in MCP_REGISTRY}

async def _fetch_remote_mcp_registry() -> List[Dict]:
    global _REMOTE_REGISTRY_CACHE, _REMOTE_REGISTRY_FETCHED_AT
    now = datetime.now()
    if _REMOTE_REGISTRY_FETCHED_AT and (now - _REMOTE_REGISTRY_FETCHED_AT) < _REMOTE_REGISTRY_TTL:
        return _REMOTE_REGISTRY_CACHE
    try:
        result: List[Dict] = []
        cursor = None
        async with httpx.AsyncClient(timeout=10.0) as client:
            while True:
                params: Dict = {"limit": 100}
                if cursor:
                    params["cursor"] = cursor
                resp = await client.get(_REMOTE_REGISTRY_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
                for s in data.get("servers", []):
                    srv = s["server"]
                    meta = s.get("_meta", {}).get("io.modelcontextprotocol.registry/official", {})
                    if not meta.get("isLatest", True):
                        continue
                    pkg = next(
                        (p for p in srv.get("packages", [])
                         if p.get("transport", {}).get("type") == "stdio"
                         and p.get("registryType") in ("npm", "pypi")),
                        None,
                    )
                    if not pkg:
                        continue
                    entry_id = srv["name"].replace("/", "-").replace(".", "-")
                    if entry_id in _LOCAL_IDS:
                        continue
                    result.append({
                        "id": entry_id,
                        "name": srv.get("title") or srv["name"],
                        "category": "MCP Registry",
                        "install_mode": pkg["registryType"],
                        "package": pkg["identifier"],
                        "package_version": pkg.get("version"),
                        "description": srv.get("description", ""),
                        "keywords": [],
                        "capabilities": [],
                        "source": "registry",
                        "homepage": (srv.get("repository") or {}).get("url"),
                    })
                cursor = data.get("nextCursor")
                if not cursor:
                    break
        _REMOTE_REGISTRY_CACHE = result
        _REMOTE_REGISTRY_FETCHED_AT = now
        logging.info("Fetched %d stdio MCP servers from remote registry", len(result))
    except Exception as e:
        logging.warning("Failed to fetch remote MCP registry: %s", e)
    return _REMOTE_REGISTRY_CACHE

async def _get_combined_registry() -> List[Dict]:
    remote = await _fetch_remote_mcp_registry()
    return MCP_REGISTRY + remote

# ── Anthropic Skills Marketplace (Apache 2.0) ─────────────────────────────────
_MARKETPLACE_RAW = "https://raw.githubusercontent.com/anthropics/claude-plugins-official/main"
_MARKETPLACE_API = "https://api.github.com/repos/anthropics/claude-plugins-official/contents"

# 검증된 서드파티 skills 소스 (Apache-2.0 / MIT)
_THIRD_PARTY_SKILL_SOURCES: List[Dict] = [
    {
        "plugin": "adobe-for-creativity", "author": "Adobe", "license": "Apache-2.0",
        "repo": "adobe/skills", "branch": "main",
        "plugin_path": "plugins/creative-cloud/adobe-for-creativity",
        "category": "design",
    },
    {
        "plugin": "airtable", "author": "Airtable", "license": "MIT",
        "repo": "Airtable/skills", "branch": "main",
        "plugin_path": "plugins/airtable",
        "category": "productivity",
    },
    {
        "plugin": "auth0", "author": "Auth0", "license": "Apache-2.0",
        "repo": "auth0/agent-skills", "branch": "main",
        "plugin_path": "plugins/auth0",
        "category": "security",
    },
    {
        "plugin": "expo", "author": "Expo", "license": "MIT",
        "repo": "expo/skills", "branch": "main",
        "plugin_path": "plugins/expo",
        "category": "development",
    },
    {
        "plugin": "logfire", "author": "Pydantic", "license": "MIT",
        "repo": "pydantic/skills", "branch": "main",
        "plugin_path": "plugins/logfire",
        "category": "monitoring",
    },
]

# 검증된 레포 라이선스 맵 (GitHub API 없이 빠르게 조회)
_KNOWN_REPO_LICENSES: Dict[str, str] = {
    # Apache-2.0
    "adobe/skills": "Apache-2.0", "awslabs/agent-plugins": "Apache-2.0",
    "auth0/agent-skills": "Apache-2.0", "aws/agent-toolkit-for-aws": "Apache-2.0",
    "carta/plugins": "Apache-2.0", "circlefin/skills": "Apache-2.0",
    "clickhouse/clickhouse-docs": "Apache-2.0", "cloudflare/agents": "Apache-2.0",
    "cockroachdb/claude-code": "Apache-2.0", "codspeed-hq/codspeed-claude": "Apache-2.0",
    "DataDog/datadog-claude-code": "Apache-2.0", "datahub-project/datahub-skills": "Apache-2.0",
    "neondatabase/agent-skills": "Apache-2.0", "PagerDuty/pd-ai-agents-plugins": "Apache-2.0",
    "getpostman/postman-mcp-server": "Apache-2.0", "qdrant/qdrant-skills": "Apache-2.0",
    "rootlyhq/rootly-plugins": "Apache-2.0", "snowflake-labs/snowflake-claude": "Apache-2.0",
    "sumup/sumup-claude": "Apache-2.0", "zilliz-labs/zilliz-skills": "Apache-2.0",
    "mercadopago/mercadopago-claude-marketplace": "Apache-2.0",
    # MIT
    "Airtable/skills": "MIT", "endorlabs/ai-plugins": "MIT",
    "apollographql/apollo-claude-skills": "MIT", "appwrite/skills": "MIT",
    "atlan-inc/claude-code-skills": "MIT", "boxer/boxerbox": "MIT",
    "buildkite/claude-code": "MIT", "coderabbitai/coderabbit-skills": "MIT",
    "CrowdStrike/crowdstrike-skills": "MIT", "microsoft/Dataverse-skills": "MIT",
    "duckdb/duckdb-skills": "MIT", "expo/skills": "MIT",
    "intercom/intercom-skills": "MIT", "pydantic/skills": "MIT",
    "mapbox/mapbox-skills": "MIT", "mintlify/mintlify-skills": "MIT",
    "miroapp/miro-ai": "MIT", "netlify/netlify-skills": "MIT",
    "pinecone-io/pinecone-skills": "MIT", "railwayapp/railway-skills": "MIT",
    "resend/resend-skills": "MIT", "sanity-io/sanity-skills": "MIT",
    "getsentry/sentry-ai-skills": "MIT", "Shopify/liquid-skills": "MIT",
    "slackapi/slack-skills": "MIT", "stripe/stripe-skills": "MIT",
    "twilio-labs/twilio-skills": "MIT", "workos/workos-skills": "MIT",
    "zoom/zoom-skills": "MIT", "aws-samples/sample-claude-code-plugins-for-startups": "MIT-0",
}

_SKILLS_MARKETPLACE_CACHE: List[Dict] = []
_SKILLS_MARKETPLACE_FETCHED_AT: Optional[datetime] = None
_SKILLS_MARKETPLACE_TTL = timedelta(hours=1)

def _extract_skill_desc(skill_md: str, fallback: str) -> str:
    for line in skill_md.splitlines():
        if line.startswith("description:"):
            return line.split(":", 1)[1].strip()
    return fallback

async def _fetch_plugin_skills(client: httpx.AsyncClient, source: Dict) -> List[Dict]:
    """단일 소스에서 skill 목록을 fetch해 반환"""
    repo, branch, plugin_path = source["repo"], source["branch"], source["plugin_path"]
    raw_base = f"https://raw.githubusercontent.com/{repo}/{branch}"
    api_base = f"https://api.github.com/repos/{repo}/contents"
    homepage_base = f"https://github.com/{repo}/tree/{branch}"

    dir_resp = await client.get(f"{api_base}/{plugin_path}/skills")
    if dir_resp.status_code != 200:
        return []
    skill_dirs = [f["name"] for f in dir_resp.json() if f["type"] == "dir"]

    skills: List[Dict] = []
    for skill_name in skill_dirs:
        skill_md_url = f"{raw_base}/{plugin_path}/skills/{skill_name}/SKILL.md"
        sm_resp = await client.get(skill_md_url)
        if sm_resp.status_code != 200:
            continue
        skills.append({
            "plugin":       source["plugin"],
            "skill":        skill_name,
            "category":     source.get("category", "development"),
            "description":  _extract_skill_desc(sm_resp.text, source.get("description", "")),
            "skill_md_url": skill_md_url,
            "homepage":     f"{homepage_base}/{plugin_path}/skills/{skill_name}",
            "license":      source["license"],
            "author":       source["author"],
        })
    return skills

async def _fetch_skills_marketplace() -> List[Dict]:
    global _SKILLS_MARKETPLACE_CACHE, _SKILLS_MARKETPLACE_FETCHED_AT
    now = datetime.now()
    if _SKILLS_MARKETPLACE_FETCHED_AT and (now - _SKILLS_MARKETPLACE_FETCHED_AT) < _SKILLS_MARKETPLACE_TTL:
        return _SKILLS_MARKETPLACE_CACHE
    try:
        result: List[Dict] = []
        async with httpx.AsyncClient(timeout=15.0) as client:
            # ── Anthropic 공식 skills (Apache-2.0) ──────────────────────────
            mp_resp = await client.get(f"{_MARKETPLACE_RAW}/.claude-plugin/marketplace.json")
            mp_resp.raise_for_status()
            marketplace_json = mp_resp.json()
            anthropic_plugins = [
                p for p in marketplace_json.get("plugins", [])
                if (p.get("author") or {}).get("name") == "Anthropic"
                and isinstance(p.get("source"), str)
                and p["source"].startswith("./")
            ]
            for plugin in anthropic_plugins:
                plugin_path = plugin["source"].lstrip("./")
                result.extend(await _fetch_plugin_skills(client, {
                    "plugin":      plugin["name"],
                    "author":      "Anthropic",
                    "license":     "Apache-2.0",
                    "repo":        "anthropics/claude-plugins-official",
                    "branch":      "main",
                    "plugin_path": plugin_path,
                    "category":    plugin.get("category", "development"),
                    "description": plugin.get("description", ""),
                }))
            # ── 검증된 서드파티 skills ────────────────────────────────────────
            for source in _THIRD_PARTY_SKILL_SOURCES:
                result.extend(await _fetch_plugin_skills(client, source))

        _SKILLS_MARKETPLACE_CACHE = result
        _SKILLS_MARKETPLACE_FETCHED_AT = now
        logging.info("Fetched %d skills from marketplace (%d sources)",
                     len(result), len(anthropic_plugins) + len(_THIRD_PARTY_SKILL_SOURCES))
    except Exception as e:
        logging.warning("Failed to fetch skills marketplace: %s", e)
    return _SKILLS_MARKETPLACE_CACHE

# ── Plugin Directory ──────────────────────────────────────────────────────────
_PLUGIN_DIRECTORY_CACHE: List[Dict] = []
_PLUGIN_DIRECTORY_FETCHED_AT: Optional[datetime] = None
_PLUGIN_DIRECTORY_TTL = timedelta(hours=1)
_OPEN_LICENSES = {"Apache-2.0", "MIT", "MIT-0", "CC-BY-4.0"}
_REPO_LICENSE_CACHE: Dict[str, str] = {}

async def _get_repo_license(client: httpx.AsyncClient, repo: str) -> str:
    if repo in _REPO_LICENSE_CACHE:
        return _REPO_LICENSE_CACHE[repo]
    if repo in _KNOWN_REPO_LICENSES:
        _REPO_LICENSE_CACHE[repo] = _KNOWN_REPO_LICENSES[repo]
        return _KNOWN_REPO_LICENSES[repo]
    try:
        r = await client.get(f"https://api.github.com/repos/{repo}", timeout=5.0)
        lic = (r.json().get("license") or {}).get("spdx_id", "") if r.status_code == 200 else ""
    except Exception:
        lic = ""
    _REPO_LICENSE_CACHE[repo] = lic
    return lic

async def _fetch_plugin_directory() -> List[Dict]:
    global _PLUGIN_DIRECTORY_CACHE, _PLUGIN_DIRECTORY_FETCHED_AT
    now = datetime.now()
    if _PLUGIN_DIRECTORY_FETCHED_AT and (now - _PLUGIN_DIRECTORY_FETCHED_AT) < _PLUGIN_DIRECTORY_TTL:
        return _PLUGIN_DIRECTORY_CACHE
    try:
        result: List[Dict] = []
        async with httpx.AsyncClient(timeout=15.0) as client:
            mp_resp = await client.get(f"{_MARKETPLACE_RAW}/.claude-plugin/marketplace.json")
            mp_resp.raise_for_status()
            plugins = mp_resp.json().get("plugins", [])

            for p in plugins:
                author = (p.get("author") or {}).get("name", "")
                src = p.get("source", {})

                # Anthropic 같은 레포 플러그인 → Apache-2.0 확인됨
                if isinstance(src, str) and src.startswith("./") and author == "Anthropic":
                    plugin_path = src.lstrip("./")
                    result.append({
                        "name":        p["name"],
                        "description": p.get("description", ""),
                        "category":    p.get("category", ""),
                        "author":      author,
                        "license":     "Apache-2.0",
                        "homepage":    p.get("homepage") or f"https://github.com/anthropics/claude-plugins-official/tree/main/{plugin_path}",
                        "source_type": "anthropic",
                    })
                    continue

                # 외부 레포 플러그인 → 라이선스 확인
                if not isinstance(src, dict):
                    continue
                repo_url = src.get("url", "").replace("https://github.com/", "").replace(".git", "").split("/tree/")[0]
                if not repo_url:
                    continue
                license_id = await _get_repo_license(client, repo_url)
                if license_id not in _OPEN_LICENSES:
                    continue
                result.append({
                    "name":        p["name"],
                    "description": p.get("description", ""),
                    "category":    p.get("category", ""),
                    "author":      author or repo_url.split("/")[0],
                    "license":     license_id,
                    "homepage":    p.get("homepage") or f"https://github.com/{repo_url}",
                    "source_type": "third-party",
                })

        _PLUGIN_DIRECTORY_CACHE = result
        _PLUGIN_DIRECTORY_FETCHED_AT = now
        logging.info("Fetched plugin directory: %d open-source plugins", len(result))
    except Exception as e:
        logging.warning("Failed to fetch plugin directory: %s", e)
    return _PLUGIN_DIRECTORY_CACHE

# ─────────────────────────────────────────────────────────────────────────────

SKILLS_DIR = Path(__file__).resolve().parent / "skills"

async def install_skill(plugin: str, skill: str) -> Dict:
    marketplace = await _fetch_skills_marketplace()
    entry = next((s for s in marketplace if s["plugin"] == plugin and s["skill"] == skill), None)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Skill '{plugin}/{skill}' not found in marketplace")
    skill_dir = SKILLS_DIR / skill
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md_path = skill_dir / "SKILL.md"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(entry["skill_md_url"])
        resp.raise_for_status()
        content = resp.text
    # 출처 표기 (Apache-2.0 / MIT 공통)
    repo_hint = entry.get("homepage", "")
    attribution = f"<!-- Source: {repo_hint}, {entry['license']} -->\n"
    if not content.startswith("<!--"):
        content = attribution + content
    skill_md_path.write_text(content, encoding="utf-8")
    risk_path = skill_dir / "risk.json"
    if not risk_path.exists():
        risk_path.write_text(json.dumps({
            "risk": "read", "destructive": False,
            "shell": False, "network": False,
            "auto_approve": True, "sandbox": "workspace", "rollback": "none"
        }, indent=2), encoding="utf-8")
    return {
        "status":  "installed",
        "plugin":  plugin,
        "skill":   skill,
        "path":    str(skill_dir),
        "license": entry["license"],
        "author":  entry["author"],
    }
