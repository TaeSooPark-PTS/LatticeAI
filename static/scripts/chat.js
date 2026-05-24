/* Lattice AI — chat.html scripts */

const chatViewport = document.getElementById('chat-viewport');
        const userInput = document.getElementById('user-input');
        const sendBtn = document.getElementById('send-btn');
        const historyContainer = document.getElementById('history-container');
        const emptyState = document.getElementById('empty-state');
        const vpcHeaderPill = document.getElementById('vpc-header-pill');
        let currentImageData = null;
        let currentImageMime = 'image/png';
        let currentUserNickname = "Guest";
        let currentUserEmail = "";
        let isAdmin = false;

        // ── 멀티 LLM 파이프라인 상태 ──
        let pipelineConfig = { planning: null, executing: null, reviewing: null };
        let pipelineActive = false; // true이면 전송 시 pipeline 모드

        function openPipelineModal() {
            document.getElementById('pipeline-overlay').style.display = 'flex';
            loadPipelineModelOptions();
        }
        function closePipelineModal() {
            document.getElementById('pipeline-overlay').style.display = 'none';
        }
        function resetPipeline() {
            pipelineConfig = { planning: null, executing: null, reviewing: null };
            pipelineActive = false;
            ['planning','executing','reviewing'].forEach(p =>
                document.getElementById(`pipeline-${p}-select`).value = '');
            updatePipelineBadge();
        }
        function savePipelineAndClose() {
            pipelineConfig = {
                planning:  document.getElementById('pipeline-planning-select').value  || null,
                executing: document.getElementById('pipeline-executing-select').value || null,
                reviewing: document.getElementById('pipeline-reviewing-select').value || null,
            };
            pipelineActive = true;
            updatePipelineBadge();
            closePipelineModal();
        }
        function updatePipelineBadge() {
            const card = document.getElementById('pipeline-ops-card');
            const val  = document.getElementById('ops-pipeline-value');
            const meta = document.getElementById('ops-pipeline-meta');
            if (!card) return;
            if (pipelineActive) {
                card.style.borderColor = 'rgba(34,211,160,0.35)';
                card.style.boxShadow = '0 0 0 1px rgba(34,211,160,0.18)';
                const pLabel = pipelineConfig.planning  ? pipelineConfig.planning.split('/').pop()  : '—';
                const eLabel = pipelineConfig.executing ? pipelineConfig.executing.split('/').pop() : '—';
                const rLabel = pipelineConfig.reviewing ? pipelineConfig.reviewing.split('/').pop() : '—';
                if (val)  val.textContent  = 'Pipeline ON';
                if (meta) meta.textContent = `P:${pLabel} E:${eLabel} R:${rLabel}`;
            } else {
                card.style.borderColor = '';
                card.style.boxShadow   = '';
                if (val)  val.textContent  = t('ops_pipeline_value');
                if (meta) meta.textContent = t('ops_pipeline_meta');
            }
        }
        async function loadPipelineModelOptions() {
            let models = [];
            try {
                const res = await apiFetch('/models');
                const data = await res.json();
                const loaded = data.loaded || [];
                const cloud  = data.providers || [];
                loaded.forEach(m => models.push({ id: m, label: `[로컬] ${m.split('/').pop()}` }));
                cloud.forEach(m => {
                    if (m.available !== false)
                        models.push({ id: m.id || m.model_id, label: `[클라우드] ${m.name || m.id}` });
                });
            } catch(e) { /* silent */ }
            ['planning','executing','reviewing'].forEach(phase => {
                const sel = document.getElementById(`pipeline-${phase}-select`);
                const cur = pipelineConfig[phase] || '';
                sel.innerHTML = '<option value="">현재 로드된 모델 (기본)</option>';
                models.forEach(m => {
                    const opt = document.createElement('option');
                    opt.value = m.id; opt.textContent = m.label;
                    if (m.id === cur) opt.selected = true;
                    sel.appendChild(opt);
                });
            });
        }

        // ── 플랜 승인 카드 렌더링 ──
        async function renderPlanApprovalCard(bubble, data) {
            const plan  = data.plan || {};
            const steps = plan.steps || [];
            const pM = data.planning_model  || '현재 모델';
            const eM = data.executing_model || '현재 모델';
            const rM = data.reviewing_model || '현재 모델';
            const contextId = data.context_id;

            let stepsHtml = steps.map((s,i) =>
                `<li>${escapeHtml(s.description || s.action || JSON.stringify(s))}</li>`).join('');

            bubble.innerHTML = `
                <div class="plan-approval-card">
                    <h4>📋 플래닝 완료 — 실행 전 확인해주세요</h4>
                    ${plan.goal ? `<p style="color:var(--text);font-size:13px;margin:0 0 10px"><b>목표:</b> ${escapeHtml(plan.goal)}</p>` : ''}
                    <ol>${stepsHtml || '<li>(단계 없음)</li>'}</ol>
                    <div class="plan-meta">
                        🧠 Planning: <b>${escapeHtml(compactModelName(pM))}</b> &nbsp;·&nbsp;
                        ⚙️ Executing: <b>${escapeHtml(compactModelName(eM))}</b> &nbsp;·&nbsp;
                        🔍 Reviewing: <b>${escapeHtml(compactModelName(rM))}</b>
                    </div>
                    <div class="plan-approval-actions">
                        <button class="plan-approve-btn" onclick="resumeAgent('${contextId}', this)">
                            <i class="ti ti-player-play"></i> ✅ Done — 실행 시작
                        </button>
                        <button class="plan-cancel-btn" onclick="cancelAgent('${contextId}', this)">❌ 취소</button>
                    </div>
                </div>`;
        }

        async function resumeAgent(contextId, btn) {
            btn.disabled = true;
            btn.textContent = '⚙️ 실행 중...';
            const card = btn.closest('.plan-approval-card');
            try {
                const res = await apiFetch('/agent/resume', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        context_id: contextId,
                        approved: true,
                        executing_model: pipelineConfig.executing || null,
                        reviewing_model: pipelineConfig.reviewing || null,
                    })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || `서버 오류 (${res.status})`);
                const bubble = btn.closest('.bubble');
                renderAiBubble(bubble, data.response || '완료되었습니다.');
                const files = data.created_files || [];
                files.forEach(f => renderFileDownloadCard(f.filename, f.path, f.bytes || 0));
            } catch(e) {
                card.innerHTML += `<p style="color:var(--danger);font-size:12px;margin-top:8px">${escapeHtml(safeErrorMessage(e))}</p>`;
                btn.disabled = false;
                btn.textContent = '다시 시도';
            }
        }

        async function cancelAgent(contextId, btn) {
            await apiFetch('/agent/resume', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ context_id: contextId, approved: false })
            }).catch(()=>{});
            btn.closest('.bubble').innerHTML = '<span style="color:var(--muted)">작업이 취소되었습니다.</span>';
        }
        let latestVpcConfig = null;
        const mirroredHistoryKeys = new Set();
        const API_BASE = window.location.protocol === 'file:' ? 'http://localhost:4825' : '';
        const CONVERSATION_KEY = 'ltcai.currentConversationId';
        const CONVERSATION_STARTED_KEY = 'ltcai.currentConversationStartedAt';
        let currentConversationId = localStorage.getItem(CONVERSATION_KEY) || createConversationId();
        localStorage.setItem(CONVERSATION_KEY, currentConversationId);
        if (!localStorage.getItem(CONVERSATION_STARTED_KEY)) {
            localStorage.setItem(CONVERSATION_STARTED_KEY, new Date().toISOString());
        }

        function apiFetch(path, options = {}) {
            const headers = { ...(options.headers || {}) };
            return fetch(`${API_BASE}${path}`, { credentials: 'include', ...options, headers });
        }

        function createConversationId() {
            if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
            return `chat-${Date.now()}-${Math.random().toString(16).slice(2)}`;
        }

        // --- VS Code Extension Bridge ---
        let vscode = null;
        try {
            if (typeof acquireVsCodeApi === 'function') {
                vscode = acquireVsCodeApi();
                console.log("VS Code API Acquired");
            }
        } catch (e) { /* Not in VS Code */ }

        window.addEventListener('message', event => {
            const message = event.data;
            if (message.type === 'prefill') {
                userInput.value = message.text;
                userInput.focus();
            }
        });

        async function logout() {
            try { await apiFetch('/logout', { method: 'POST' }); } catch (_) {}
            localStorage.removeItem('ltcai_user_email');
            localStorage.removeItem('ltcai_user_nickname');
            localStorage.removeItem('ltcai_is_admin');
            window.location.href = '/account';
        }

        function toggleSidebar() {
            document.body.classList.toggle('sidebar-open');
        }
        function closeSidebar() {
            document.body.classList.remove('sidebar-open');
        }

        function openDataGraph() {
            window.location.href = `${API_BASE}/graph`;
        }

        const I18N = {
            ko: {
                // 인증
                login_title: 'Lattice AI', login_sub: '내 PC에서 시작하는 개인 AI 워크스페이스',
                ph_email: '이메일 주소', ph_password: '비밀번호', btn_login: '로그인',
                no_account: '계정이 없으신가요?', go_register: '회원가입',
                register_title: '계정 만들기', register_sub: 'Lattice AI 워크스페이스에 참여하세요',
                ph_new_pw: '비밀번호 (4자 이상)', ph_pw_confirm: '비밀번호 확인',
                ph_fullname: '이름', ph_nick: '닉네임',
                btn_register: '가입하기', have_account: '이미 계정이 있나요?', go_login: '로그인',
                // 헤더 / 사이드바
                logout: '로그아웃', admin_dashboard: '관리자 대시보드',
                my_status: '내 상태 보기', auto_setup: '자동 설정',
                nav_home: '홈', nav_chat: '채팅', nav_knowledge: '지식 그래프',
                nav_pipeline: '파이프라인', nav_files: '내 컴퓨터',
                nav_model_status: '모델 상태', nav_runtime: '런타임 설정',
                nav_advanced_settings: '고급 설정',
                history_search_ph: '대화 검색...', new_chat: 'New Chat',
                history_section: '대화', history_empty: '아직 저장된 대화가 없습니다.',
                new_conversation: '새 대화', previous_history: '이전 대화 기록',
                confirm_delete_chat: '이 대화를 삭제할까요?',
                home_greeting: '안녕하세요, {name}님',
                home_greeting_short: '안녕하세요',
                ops_ai_model: 'AI 모델', ops_local_runtime: '로컬 런타임',
                ops_admin_network: '관리자 네트워크', ops_admin_security: '관리자 보안',
                ops_pipeline_value: '멀티 LLM 파이프라인',
                ops_pipeline_meta: 'Plan → Execute → Review 모델 설정',
                home_ai_status: 'AI 상태', home_model_loading: '모델 로딩 중...',
                home_no_model: '모델 없음', home_memory: '메모리',
                home_change_model: '모델 변경', home_nodes: '노드', home_edges: '연결',
                home_open_graph: '그래프 보기', home_installable_tools: '설치 가능한 도구',
                home_start_setup: '설정 시작', home_recent_chats: '최근 채팅',
                home_view_all: '전부 보기', home_no_chats: '대화 기록이 없습니다',
                home_recent_files: '최근 파일', home_open_files: '파일 열기', home_no_files: '파일이 없습니다',
                chat_intro_title: 'Lattice AI',
                chat_intro_desc: '로컬 모델, 파일, 지식 그래프, 멀티모달 작업을 한 대화 흐름에서 연결하는 개인 AI 워크스페이스입니다.',
                chat_cap_file: '파일 생성', chat_cap_knowledge: '지식 정리', chat_cap_runtime: '로컬 런타임',
                // 계정 모달
                tab_profile: '프로필', tab_password: '비밀번호',
                label_name: '이름', label_nickname: '닉네임',
                label_cur_pw: '현재 비밀번호', label_new_pw: '새 비밀번호', label_new_pw2: '새 비밀번호 확인',
                ph_name: '이름', ph_nickname: '닉네임', ph_cur_pw: '현재 비밀번호',
                ph_new_pw2: '새 비밀번호 재입력',
                btn_save: '저장', btn_change: '변경', btn_cancel: '취소',
                // ops 스트립
                vpc_not_set: '설정 안 됨', vpc_click_to_set: '클릭하여 VPC 연결 설정',
                security_monitor: '민감정보 감시', admin_dashboard_access: '관리자 대시보드 접근', admin_has_rights: '관리자 권한 있음',
                // 빈 화면
                empty_title: '무엇을 만들까요?',
                empty_sub: '파일, 대화, 지식 그래프, 파이프라인을 한 곳에서 이어가세요.',
                chip_file: '파일 생성 · 코드 초안', chip_vpc: '보안 설정 확인', chip_kb: '지식 정리',
                chip_file_prompt: '보고서 초안을 만들어줘',
                chip_vpc_prompt: '보안 설정을 점검해줘',
                chip_kb_prompt: '이 내용을 지식베이스에 정리해줘',
                // 입력창
                ph_input: 'Lattice AI에게 작업을 지시하세요...',
                // 파일 툴바
                create_file: '파일 만들기', local_files: '로컬 파일',
                // 워크스페이스 / 모드
                workspace_title: '워크스페이스 선택',
                workspace_sub: '사용 목적에 맞는 시작 공간을 고르면 홈 화면과 기본 도구가 그 흐름에 맞춰 정리됩니다.',
                workspace_personal: '개인 워크스페이스',
                workspace_personal_sub: '개인 프로젝트, 로컬 파일, 지식베이스 중심',
                workspace_company: '회사 워크스페이스',
                workspace_company_sub: 'SSO, 보안 정책, 팀 운영 대시보드 중심',
                workspace_note: '선택한 워크스페이스는 이후 홈 화면과 메뉴 구성에 반영됩니다.',
                mode_title: '모드 선택',
                mode_sub: '작업 성격에 맞춰 Lattice AI의 화면 밀도와 기본 프롬프트를 전환합니다.',
                mode_default: '기본 모드',
                mode_default_sub: '대화, 파일 생성, 지식 정리를 한 화면에서',
                mode_advanced: '고급 모드',
                mode_advanced_sub: '모델 상태, 런타임 설정, 고급 설정',
                mode_admin: '관리자 모드',
                mode_admin_sub: '운영자용 관리자 대시보드',
                // 패널 제목
                model_switcher: '모델 스위처',
                model_switcher_sub: '실행 엔진을 설치하고, 엔진에 맞는 local/cloud LLM을 선택합니다.',
                // 권한 다이얼로그
                perm_title: '파일 접근 요청', btn_deny: '거부', btn_allow: '허용',
            },
            en: {
                // Auth
                login_title: 'Lattice AI', login_sub: 'Your personal AI workspace starts on this PC',
                ph_email: 'Email address', ph_password: 'Password', btn_login: 'Log in',
                no_account: "Don't have an account?", go_register: 'Sign up',
                register_title: 'Create Account', register_sub: 'Join the Lattice AI workspace',
                ph_new_pw: 'Password (min. 4 chars)', ph_pw_confirm: 'Confirm password',
                ph_fullname: 'Full name', ph_nick: 'Nickname',
                btn_register: 'Sign up', have_account: 'Already have an account?', go_login: 'Log in',
                // Header / Sidebar
                logout: 'Logout', admin_dashboard: 'Admin Dashboard',
                my_status: 'My Status', auto_setup: 'Auto Setup',
                nav_home: 'Home', nav_chat: 'Chat', nav_knowledge: 'Knowledge Graph',
                nav_pipeline: 'Pipeline', nav_files: 'My Computer',
                nav_model_status: 'Model Status', nav_runtime: 'Runtime Settings',
                nav_advanced_settings: 'Advanced Settings',
                history_search_ph: 'Search chats...', new_chat: 'New Chat',
                history_section: 'Chats', history_empty: 'No saved chats yet.',
                new_conversation: 'New chat', previous_history: 'Previous chat history',
                confirm_delete_chat: 'Delete this chat?',
                home_greeting: 'Hello, {name}',
                home_greeting_short: 'Hello',
                ops_ai_model: 'AI model', ops_local_runtime: 'Local runtime',
                ops_admin_network: 'Admin Network', ops_admin_security: 'Admin Security',
                ops_pipeline_value: 'Multi-LLM Pipeline',
                ops_pipeline_meta: 'Plan → Execute → Review model setup',
                home_ai_status: 'AI Status', home_model_loading: 'Loading model...',
                home_no_model: 'No model', home_memory: 'Memory',
                home_change_model: 'Change Model', home_nodes: 'Nodes', home_edges: 'Edges',
                home_open_graph: 'Open Graph', home_installable_tools: 'Installable Tools',
                home_start_setup: 'Start Setup', home_recent_chats: 'Recent Chats',
                home_view_all: 'View All', home_no_chats: 'No chat history yet',
                home_recent_files: 'Recent Files', home_open_files: 'Open Files', home_no_files: 'No files yet',
                chat_intro_title: 'Lattice AI',
                chat_intro_desc: 'A personal AI workspace that connects local models, files, knowledge graphs, and multimodal work in one conversation flow.',
                chat_cap_file: 'File creation', chat_cap_knowledge: 'Knowledge organizing', chat_cap_runtime: 'Local runtime',
                // Account modal
                tab_profile: 'Profile', tab_password: 'Password',
                label_name: 'Name', label_nickname: 'Nickname',
                label_cur_pw: 'Current Password', label_new_pw: 'New Password', label_new_pw2: 'Confirm New Password',
                ph_name: 'Name', ph_nickname: 'Nickname', ph_cur_pw: 'Current password',
                ph_new_pw2: 'Confirm new password',
                btn_save: 'Save', btn_change: 'Change', btn_cancel: 'Cancel',
                // Ops strip
                vpc_not_set: 'Not configured', vpc_click_to_set: 'Click to set up VPC',
                security_monitor: 'Sensitive data monitor', admin_dashboard_access: 'Admin dashboard access', admin_has_rights: 'Has admin rights',
                // Empty state
                empty_title: 'What would you like to build?',
                empty_sub: 'Bring files, chat, knowledge graph, and pipelines into one workspace.',
                chip_file: 'Create file · Code draft', chip_vpc: 'Review security settings', chip_kb: 'Organize knowledge',
                chip_file_prompt: 'Draft a report for me',
                chip_vpc_prompt: 'Review my security settings',
                chip_kb_prompt: 'Organize this into my knowledge base',
                // Input
                ph_input: 'Ask Lattice AI anything...',
                // File toolbar
                create_file: 'Create file', local_files: 'Local files',
                // Workspace / mode
                workspace_title: 'Select Workspace',
                workspace_sub: 'Choose a starting space so the home view and default tools match how you work.',
                workspace_personal: 'Personal Workspace',
                workspace_personal_sub: 'Personal projects, local files, and knowledge base',
                workspace_company: 'Company Workspace',
                workspace_company_sub: 'SSO, security policy, and team operations',
                workspace_note: 'Your workspace choice shapes the Home view and available menus.',
                mode_title: 'Mode Select',
                mode_sub: 'Switch Lattice AI density and defaults based on the job.',
                mode_default: 'Default Mode',
                mode_default_sub: 'Chat, file creation, and knowledge in one view',
                mode_advanced: 'Advanced Mode',
                mode_advanced_sub: 'Model status, runtime settings, and advanced settings',
                mode_admin: 'Admin Mode',
                mode_admin_sub: 'Admin dashboard for operators',
                // Panel titles
                model_switcher: 'Model Switcher',
                model_switcher_sub: 'Install a runtime engine and select a local/cloud LLM.',
                // Permission dialog
                perm_title: 'File Access Request', btn_deny: 'Deny', btn_allow: 'Allow',
            }
        };
        let currentLang = localStorage.getItem('ltcai_lang') || 'ko';

        function t(key) { return (I18N[currentLang] || I18N.ko)[key] || key; }

        function applyI18n() {
            document.documentElement.lang = currentLang;
            document.querySelectorAll('[data-i18n]').forEach(el => {
                el.textContent = t(el.dataset.i18n);
            });
            document.querySelectorAll('[data-i18n-ph]').forEach(el => {
                el.placeholder = t(el.dataset.i18nPh);
            });
            // 언어 선택기 active 표시 업데이트
            ['auth', 'header'].forEach(prefix => {
                ['ko', 'en'].forEach(lang => {
                    const el = document.getElementById(`${prefix}-lang-${lang}`);
                    if (el) el.classList.toggle('active', lang === currentLang);
                });
            });
            const authBtn = document.getElementById('auth-lang-btn');
            if (authBtn) authBtn.textContent = `🌐 ${currentLang === 'ko' ? '한국어' : 'English'}`;
            const headerBtn = document.getElementById('lang-btn');
            if (headerBtn) headerBtn.innerHTML = `<i class="ti ti-language"></i> Language: ${currentLang === 'ko' ? '한국어' : 'English'}`;
            const historySearch = document.getElementById('history-search-input');
            if (historySearch) historySearch.placeholder = t('history_search_ph');
            const newChatBtn = document.getElementById('new-chat-btn');
            if (newChatBtn) newChatBtn.innerHTML = `<i class="ti ti-plus"></i> ${t('new_chat')}`;
            updateStaticHomeCopy();
        }

        function updateStaticHomeCopy() {
            const setText = (selector, value) => {
                const el = typeof selector === 'string' ? document.querySelector(selector) : selector;
                if (el) el.textContent = value;
            };
            const setHtml = (selector, value) => {
                const el = document.querySelector(selector);
                if (el) el.innerHTML = value;
            };
            setHtml('#home-ai-card .hdc-title', `<i class="ti ti-cpu-2"></i> ${t('home_ai_status')}`);
            setHtml('#home-kg-card .hdc-title', `<i class="ti ti-chart-dots-3"></i> ${t('nav_knowledge')}`);
            setHtml('#home-setup-card .hdc-title', `<i class="ti ti-settings-automation"></i> ${t('auto_setup')}`);
            setText('#home-ai-card .hdc-stat-row:nth-child(1) .hdc-stat-label', t('home_memory'));
            setHtml('#home-ai-card .hdc-btn', `<i class="ti ti-refresh"></i> ${t('home_change_model')}`);
            setText('#home-kg-card .hdc-count-item:nth-child(1) label', t('home_nodes'));
            setText('#home-kg-card .hdc-count-item:nth-child(3) label', t('home_edges'));
            setHtml('#home-kg-card .hdc-btn', `<i class="ti ti-arrow-right"></i> ${t('home_open_graph')}`);
            setText('#home-setup-card .hdc-setup-count label', t('home_installable_tools'));
            setHtml('#home-setup-card .hdc-btn', `<i class="ti ti-player-play"></i> ${t('home_start_setup')}`);
            const opsLabels = document.querySelectorAll('.ops-label');
            if (opsLabels[0]) opsLabels[0].textContent = t('nav_model_status');
            if (opsLabels[1]) opsLabels[1].textContent = t('nav_pipeline');
            if (opsLabels[2]) opsLabels[2].textContent = t('ops_admin_network');
            if (opsLabels[3]) opsLabels[3].textContent = t('ops_admin_security');
            const opsModel = document.getElementById('ops-model');
            if (opsModel && !opsModel.title) opsModel.textContent = t('ops_ai_model');
            const opsModelMeta = document.getElementById('ops-model-meta');
            if (opsModelMeta && !opsModelMeta.dataset.loaded) opsModelMeta.textContent = t('ops_local_runtime');
            if (!pipelineActive) {
                const pipelineValue = document.getElementById('ops-pipeline-value');
                const pipelineMeta = document.getElementById('ops-pipeline-meta');
                if (pipelineValue) pipelineValue.textContent = t('ops_pipeline_value');
                if (pipelineMeta) pipelineMeta.textContent = t('ops_pipeline_meta');
            }
            setText('#chat-empty-title', t('chat_intro_title'));
            setText('#chat-empty-desc', t('chat_intro_desc'));
            const caps = document.querySelectorAll('#chat-capability-row span');
            [t('chat_cap_file'), t('chat_cap_knowledge'), t('chat_cap_runtime')].forEach((label, index) => {
                if (caps[index]) caps[index].textContent = label;
            });
            document.querySelectorAll('.hdr-head').forEach(head => {
                const icon = head.querySelector('i')?.outerHTML || '';
                const isFiles = head.closest('.hdr-panel')?.querySelector('#home-recent-files');
                const span = head.querySelector('span');
                const button = head.querySelector('button');
                if (span) span.innerHTML = `${icon} ${isFiles ? t('home_recent_files') : t('home_recent_chats')}`;
                if (button) button.textContent = isFiles ? t('home_open_files') : t('home_view_all');
            });
            const emptyChats = document.querySelector('#home-recent-chats .hdr-empty');
            if (emptyChats) emptyChats.textContent = t('home_no_chats');
            const emptyFiles = document.querySelector('#home-recent-files .hdr-empty');
            if (emptyFiles) emptyFiles.textContent = t('home_no_files');
        }

        function toggleLangMenu(pickerId) {
            const menu = document.getElementById(`${pickerId}-menu`);
            if (!menu) return;
            const isOpen = menu.classList.contains('open');
            document.querySelectorAll('.lang-picker-menu').forEach(m => m.classList.remove('open'));
            if (!isOpen) menu.classList.add('open');
        }

        function setLang(lang) {
            currentLang = lang;
            localStorage.setItem('ltcai_lang', lang);
            document.querySelectorAll('.lang-picker-menu').forEach(m => m.classList.remove('open'));
            applyI18n();
            updateWorkspaceModeUi();
            loadHistory();
        }

        document.addEventListener('click', (e) => {
            if (!e.target.closest('.lang-picker')) {
                document.querySelectorAll('.lang-picker-menu').forEach(m => m.classList.remove('open'));
            }
        });

        function scopedStorageKey(name) {
            return currentUserEmail ? `ltcai_${name}_${currentUserEmail}` : `ltcai_${name}`;
        }

        function readPreference(name, fallback = '') {
            return localStorage.getItem(scopedStorageKey(name)) || localStorage.getItem(`ltcai_${name}`) || fallback;
        }

        function writePreference(name, value) {
            localStorage.setItem(`ltcai_${name}`, value);
            localStorage.setItem(scopedStorageKey(name), value);
        }

        function getWorkspace() {
            return readPreference('workspace', 'personal') === 'company' ? 'company' : 'personal';
        }

        function setWorkspacePreference(kind) {
            writePreference('workspace', kind === 'company' ? 'company' : 'personal');
        }

        function getCurrentMode() {
            const mode = readPreference('mode', 'default');
            if (mode === 'admin' && !isAdmin) return 'default';
            return ['default', 'advanced', 'admin'].includes(mode) ? mode : 'default';
        }

        function setModePreference(mode) {
            writePreference('mode', ['default', 'advanced', 'admin'].includes(mode) ? mode : 'default');
        }

        function onboardingComplete() {
            return localStorage.getItem(scopedStorageKey('onboarding_complete')) === 'true';
        }

        function setOnboardingComplete() {
            localStorage.setItem('ltcai_onboarding_complete', 'true');
            localStorage.setItem(scopedStorageKey('onboarding_complete'), 'true');
        }

        function workspaceLabel(kind) {
            if (kind === 'company') return currentLang === 'ko' ? '회사 워크스페이스' : 'Company Workspace';
            return currentLang === 'ko' ? '개인 워크스페이스' : 'Personal Workspace';
        }

        function modeLabel(mode) {
            if (mode === 'advanced') return currentLang === 'ko' ? '고급 모드' : 'Advanced Mode';
            if (mode === 'admin') return currentLang === 'ko' ? '관리자 모드' : 'Admin Mode';
            return currentLang === 'ko' ? '기본 모드' : 'Default Mode';
        }

        const BASE_NAV_ITEMS = [
            { id: 'home', icon: 'ti-home', labelKey: 'nav_home' },
            { id: 'chat', icon: 'ti-message-circle', labelKey: 'nav_chat' },
            { id: 'knowledge', icon: 'ti-chart-dots-3', labelKey: 'nav_knowledge' },
            { id: 'pipeline', icon: 'ti-git-branch', labelKey: 'nav_pipeline' },
            { id: 'files', icon: 'ti-device-desktop', labelKey: 'nav_files' },
            { id: 'status', icon: 'ti-info-circle', labelKey: 'my_status' },
        ];

        const ADVANCED_NAV_ITEMS = [
            { id: 'model-status', icon: 'ti-cpu-2', labelKey: 'nav_model_status' },
            { id: 'runtime', icon: 'ti-adjustments-cog', labelKey: 'nav_runtime' },
            { id: 'advanced-settings', icon: 'ti-settings', labelKey: 'nav_advanced_settings' },
        ];

        function navItemsForMode(mode) {
            if (mode === 'advanced') return [...BASE_NAV_ITEMS, ...ADVANCED_NAV_ITEMS];
            // 관리자 모드: 내 상태 보기 뒤에 고급 3개 + 관리자 대시보드
            if (mode === 'admin') return [
                ...BASE_NAV_ITEMS,
                ...ADVANCED_NAV_ITEMS,
                { id: 'admin-dashboard', icon: 'ti-shield-lock', labelKey: 'admin_dashboard' },
            ];
            return BASE_NAV_ITEMS;
        }

        function renderModeSegmented() {
            const wrap = document.getElementById('mode-segmented');
            if (!wrap) return;
            const mode = getCurrentMode();
            const modes = [
                { id: 'default', label: modeLabel('default') },
                { id: 'advanced', label: modeLabel('advanced') },
                { id: 'admin', label: modeLabel('admin'), adminOnly: true },
            ].filter(item => !item.adminOnly || isAdmin);
            wrap.innerHTML = modes.map(item => `
                <button type="button" role="tab" class="${item.id === mode ? 'active' : ''}"
                    onclick="selectMode('${item.id}')" aria-selected="${item.id === mode ? 'true' : 'false'}">
                    ${item.label}
                </button>
            `).join('');
        }

        function renderSidebarNav() {
            const nav = document.getElementById('side-nav');
            if (!nav) return;
            const mode = getCurrentMode();
            const items = navItemsForMode(mode);
            nav.innerHTML = items.map((item, index) => `
                <button class="reference-nav-item ${index === 0 ? 'active' : ''}" data-nav-id="${item.id}" onclick="runNavAction('${item.id}')">
                    <i class="ti ${item.icon}"></i><span>${t(item.labelKey)}</span>
                </button>
            `).join('');
        }

        function markActiveNav(id) {
            document.querySelectorAll('.reference-nav-item').forEach(btn => {
                btn.classList.toggle('active', btn.dataset.navId === id);
            });
        }

        function runNavAction(id) {
            markActiveNav(id);
            closeSidebar();
            if (id === 'home') showHome();
            else if (id === 'chat') showChat();
            else if (id === 'knowledge') openDataGraph();
            else if (id === 'pipeline') openPipelineModal();
            else if (id === 'files') openLocalBrowser();
            else if (id === 'computer') openCuPanel();
            else if (id === 'status') openStatusPanel();
            else if (id === 'model-status') openStatusPanel();
            else if (id === 'runtime') openModelPanel();
            else if (id === 'advanced-settings') openAdvancedSettingsPanel();
            else if (id === 'admin-dashboard') openAdminPanel();
        }

        function focusChatInput() {
            const input = document.getElementById('user-input');
            if (input) input.focus();
        }

        function showHome() {
            markActiveNav('home');
            focusChatInput();
            // 홈 뷰: 채팅 전용 사이드바 숨기기
            const layout = document.querySelector('.app-layout');
            if (layout) layout.dataset.view = 'home';
            _loadHomeDashboard();
        }

        async function _loadHomeDashboard() {
            const mode = getCurrentMode();

            // 자동 설정 카드: 고급/관리자 모드만
            const setupCard = document.getElementById('home-setup-card');
            if (setupCard) setupCard.style.display = (mode === 'advanced' || mode === 'admin') ? 'flex' : 'none';

            // 모델 + sysinfo 병렬 fetch
            try {
                const [healthRes, sysRes] = await Promise.all([
                    apiFetch('/health'),
                    apiFetch('/local/sysinfo'),
                ]);
                const health = healthRes.ok ? await healthRes.json() : {};
                const sys    = sysRes.ok    ? await sysRes.json()    : {};

                // AI 상태 카드
                const modelName = health.current_model
                    ? compactModelName(health.current_model)
                    : t('home_no_model');
                const badge = document.getElementById('home-model-badge');
                if (badge) badge.textContent = modelName;

                _setBar('home-mem-bar', 'home-mem-pct', sys.ram_pct ?? 0);
                _setBar('home-cpu-bar', 'home-cpu-pct', sys.cpu_pct ?? 0);
                _setBar('home-gpu-bar', 'home-gpu-pct', sys.gpu_mem_pct ?? 0);
            } catch (_) {}

            // 지식 그래프 stats
            try {
                const kgRes = await apiFetch('/knowledge-graph/stats');
                if (kgRes.ok) {
                    const kg = await kgRes.json();
                    // nodes/edges는 {type: count} 딕셔너리 — 합계로 변환
                    const sumObj = v => v && typeof v === 'object'
                        ? Object.values(v).reduce((a, b) => a + (Number(b) || 0), 0)
                        : (Number(v) || 0);
                    const nodeEl = document.getElementById('home-kg-nodes');
                    const edgeEl = document.getElementById('home-kg-edges');
                    if (nodeEl) nodeEl.textContent = sumObj(kg.nodes);
                    if (edgeEl) edgeEl.textContent = sumObj(kg.edges);
                }
            } catch (_) {}

            // 최근 채팅
            try {
                const convRes = await apiFetch('/history/conversations');
                if (convRes.ok) {
                    const convs = await convRes.json();
                    const el = document.getElementById('home-recent-chats');
                    if (el) {
                        const items = (convs.conversations || convs || []).slice(0, 5);
                        el.innerHTML = items.length
                            ? items.map(c => `
                                <div class="hdr-item" onclick="loadConversation('${escapeHtml(c.id || '')}')">
                                    <i class="ti ti-message-circle"></i>
                                    <span>${escapeHtml(c.title || c.id || t('nav_chat'))}</span>
                                    <small>${escapeHtml(c.updated_at ? new Date(c.updated_at).toLocaleDateString(currentLang === 'ko' ? 'ko-KR' : 'en-US') : '')}</small>
                                </div>`).join('')
                            : `<div class="hdr-empty">${t('home_no_chats')}</div>`;
                    }
                }
            } catch (_) {}
            updateStaticHomeCopy();
        }

        function _setBar(barId, labelId, pct) {
            const bar   = document.getElementById(barId);
            const label = document.getElementById(labelId);
            const val   = Math.min(100, Math.max(0, pct));
            if (bar)   bar.style.width = val + '%';
            if (label) label.textContent = val + '%';
        }

        function showChat() {
            // 채팅 뷰: 사이드바 채팅 섹션 다시 표시, 홈 대시보드 숨기기
            const layout = document.querySelector('.app-layout');
            if (layout) layout.dataset.view = 'chat';
            markActiveNav('chat');
            const hasMessages = chatViewport && chatViewport.querySelectorAll('.message').length > 0;
            // 메시지 있으면 empty-state 숨김, 없으면 chat-empty-hint 표시 (home-dash는 CSS로 숨겨짐)
            if (emptyState) emptyState.style.display = hasMessages ? 'none' : 'block';
            focusChatInput();
        }

        function openAdvancedSettingsPanel() {
            document.getElementById('advanced-settings-overlay')?.classList.add('open');
        }

        function closeAdvancedSettingsPanel() {
            document.getElementById('advanced-settings-overlay')?.classList.remove('open');
        }

        function updateWorkspaceModeUi() {
            const workspace = getWorkspace();
            const mode = getCurrentMode();
            document.body.dataset.mode = mode;
            document.querySelector('.app-layout')?.setAttribute('data-mode', mode);
            const workspaceText = workspaceLabel(workspace);
            const sidebarWorkspace = document.getElementById('sidebar-workspace-label');
            const userWorkspace = document.getElementById('user-workspace-display');
            if (sidebarWorkspace) sidebarWorkspace.textContent = workspaceText;
            if (userWorkspace) userWorkspace.textContent = workspaceText;
            const welcome = document.getElementById('home-welcome-title');
            if (welcome) {
                welcome.textContent = currentUserNickname
                    ? t('home_greeting').replace('{name}', currentUserNickname)
                    : t('home_greeting_short');
            }
            const homeSub = document.getElementById('home-welcome-sub');
            if (homeSub) homeSub.textContent = `${workspaceLabel(workspace)} - ${modeLabel(mode)}`;
            document.querySelectorAll('.workspace-card').forEach((card, index) => {
                const kind = index === 1 ? 'company' : 'personal';
                card.classList.toggle('selected', kind === workspace);
            });
            ['default', 'advanced', 'admin'].forEach(item => {
                const el = document.getElementById(`mode-card-${item}`);
                if (el) {
                    el.classList.toggle('selected', item === mode);
                    if (item === 'admin') el.style.display = isAdmin ? '' : 'none';
                }
            });
            renderModeSegmented();
            renderSidebarNav();
        }

        function maybeShowWorkspaceModal() {
            updateWorkspaceModeUi();
            startOnboardingIfNeeded();
        }

        function selectWorkspace(kind) {
            setWorkspacePreference(kind);
            document.getElementById('workspace-modal-overlay')?.classList.remove('open');
            updateWorkspaceModeUi();
            openModeSelector();
        }

        function openModeSelector() {
            updateWorkspaceModeUi();
            document.getElementById('mode-modal-overlay')?.classList.add('open');
        }

        function closeModeSelector() {
            document.getElementById('mode-modal-overlay')?.classList.remove('open');
        }

        function selectMode(mode) {
            if (mode === 'admin' && !isAdmin) {
                showToast(currentLang === 'ko' ? '관리자 권한이 없습니다.' : 'Admin access required.');
                mode = 'default';
            }
            setModePreference(mode);
            updateWorkspaceModeUi();
            closeModeSelector();
            const input = document.getElementById('user-input');
            if (input && !input.value.trim()) {
                input.placeholder = mode === 'advanced'
                    ? (currentLang === 'ko' ? '무엇을 구현하거나 고칠까요?' : 'What should we build or fix?')
                    : t('ph_input');
            }
            // 홈 뷰인 경우 대시보드 카드 즉시 갱신 (자동 설정 카드 표시/숨김)
            const layout = document.querySelector('.app-layout');
            if (layout && layout.dataset.view === 'home') {
                _loadHomeDashboard();
            }
        }

        const ONBOARDING_STEPS = [
            'Workspace Select',
            'PC Environment Analysis',
            'Recommendation Result',
            'Auto Setup / Verification',
            'Mode Select',
        ];
        let onboardingStepIndex = 0;
        let onboardingEnv = null;
        let onboardingRecs = null;
        let onboardingSelectedModel = null;

        function startOnboardingIfNeeded(force = false) {
            updateWorkspaceModeUi();
            if (!force && onboardingComplete()) {
                document.getElementById('onboarding-overlay')?.classList.remove('open');
                return;
            }
            document.getElementById('onboarding-overlay')?.classList.add('open');
            renderOnboardingWorkspace();
        }

        function renderOnboardingShell(stepIndex, bodyHtml, actionsHtml = '') {
            onboardingStepIndex = stepIndex;
            const steps = document.getElementById('onboarding-steps');
            if (steps) {
                steps.innerHTML = ONBOARDING_STEPS.map((label, index) => `
                    <div class="onboarding-step ${index < stepIndex ? 'done' : ''} ${index === stepIndex ? 'active' : ''}">
                        ${escapeHtml(label)}
                    </div>
                `).join('');
            }
            const body = document.getElementById('onboarding-body');
            const actions = document.getElementById('onboarding-actions');
            if (body) body.innerHTML = bodyHtml;
            if (actions) actions.innerHTML = actionsHtml;
        }

        function renderOnboardingWorkspace() {
            renderOnboardingShell(0, `
                <h2 id="onboarding-title">워크스페이스 선택</h2>
                <p>먼저 Lattice AI를 어떤 환경으로 사용할지 선택하세요. 선택한 값은 이후 홈 화면과 메뉴 구성에 반영됩니다.</p>
                <div class="onboarding-choice-grid">
                    <button class="onboarding-choice" onclick="chooseOnboardingWorkspace('personal')">
                        <i class="ti ti-user"></i>
                        <h3>개인 워크스페이스</h3>
                        <p>개인 프로젝트, 로컬 파일, 로컬 AI, 지식 그래프, 파이프라인, 내 컴퓨터 중심으로 구성합니다.</p>
                    </button>
                    <button class="onboarding-choice" onclick="chooseOnboardingWorkspace('company')">
                        <i class="ti ti-building-skyscraper"></i>
                        <h3>회사 워크스페이스</h3>
                        <p>SSO, 조직 정책, 팀 권한, 관리자 대시보드, 보안 정책 중심으로 구성합니다.</p>
                    </button>
                </div>
            `);
        }

        function chooseOnboardingWorkspace(kind) {
            setWorkspacePreference(kind);
            updateWorkspaceModeUi();
            renderPcAnalysis();
        }

        async function renderPcAnalysis() {
            renderOnboardingShell(1, `
                <h2>내 PC에 가장 잘 맞는 AI 환경을 찾아드릴게요.</h2>
                <p>OS, CPU, GPU, RAM, 디스크 여유 공간, 로컬 런타임, 가속 환경을 확인하고 있습니다.</p>
                <div class="scan-pulse">
                    <div class="scan-spinner"></div>
                    <span>PC 환경 분석 중...</span>
                </div>
                <div class="analysis-grid" id="onboarding-analysis-grid"></div>
            `);
            try {
                const res = await apiFetch('/setup/scan');
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || '환경 분석 실패');
                onboardingEnv = data.environment || {};
                onboardingRecs = data.recommendations || {};
                renderPcAnalysisResults(onboardingEnv);
            } catch (e) {
                renderOnboardingShell(1, `
                    <h2>환경 분석을 완료하지 못했습니다.</h2>
                    <p>잠시 후 다시 시도해 주세요.</p>
                `, `<button class="onboarding-primary" onclick="renderPcAnalysis()">다시 분석하기</button>`);
            }
        }

        function renderPcAnalysisResults(env) {
            const chip = env.chip || {};
            const gpu = env.gpu || env.acceleration || {};
            const mlx = env.mlx || {};
            const tools = env.tools || {};
            const rows = [
                ['OS', `${env.os || 'Unknown'} ${env.os_version || ''}`.trim()],
                ['CPU', chip.name || env.cpu || 'Unknown'],
                ['GPU', gpu.name || gpu.device || (chip.is_apple_silicon ? 'Apple GPU / Metal' : '확인 필요')],
                ['RAM', env.ram_gb ? `${env.ram_gb} GB` : '확인 필요'],
                ['디스크 여유 공간', env.disk_free_gb ? `${env.disk_free_gb} GB` : '확인 필요'],
                ['로컬 런타임', mlx.available ? 'MLX 준비됨' : tools.ollama ? 'Ollama 감지됨' : '설치 또는 연결 필요'],
                ['가속 환경', chip.is_apple_silicon ? 'Apple Silicon / Metal' : 'CPU 중심 실행'],
            ];
            const grid = document.getElementById('onboarding-analysis-grid');
            if (grid) {
                grid.innerHTML = rows.map(([label, value]) => `
                    <div class="analysis-row">
                        <strong>${escapeHtml(label)}</strong>
                        <p>${escapeHtml(value || '확인 필요')}</p>
                    </div>
                `).join('');
            }
            renderOnboardingShell(1, `
                <h2>PC 환경 분석이 끝났습니다.</h2>
                <p>확인한 정보를 바탕으로 가장 적합한 로컬 AI 실행 환경을 추천합니다.</p>
                <div class="analysis-grid">
                    ${rows.map(([label, value]) => `
                        <div class="analysis-row">
                            <strong>${escapeHtml(label)}</strong>
                            <p>${escapeHtml(value || '확인 필요')}</p>
                        </div>
                    `).join('')}
                </div>
            `, `<button class="onboarding-primary" onclick="renderRecommendationResult()">추천 결과 보기</button>`);
        }

        function getOnboardingRecommendedModel() {
            const models = onboardingRecs?.models || [];
            const selected = models.find(item => item.checked && !item.disabled && (item.model_id || item.action?.model_id))
                || models.find(item => !item.disabled && (item.model_id || item.action?.model_id));
            const zero = onboardingRecs?.summary?.zero_config || onboardingEnv?.zero_config?.recommend || {};
            const modelId = selected?.model_id || selected?.action?.model_id || zero.model_id || 'mlx-community/Llama-3.2-3B-Instruct-4bit';
            const engineItem = (onboardingRecs?.engines || []).find(item => item.checked && !item.disabled);
            const runtime = engineItem?.name || (zero.runtime === 'mlx' ? 'MLX' : zero.runtime) || 'MLX';
            return {
                modelId,
                label: selected?.name || compactModelName(modelId),
                engine: 'local_mlx',
                runtime,
                reasonSource: selected?.subtitle || selected?.tag || '',
            };
        }

        function onboardingRecommendation() {
            const selected = getOnboardingRecommendedModel();
            const ram = Number(onboardingEnv?.ram_gb || 0);
            const disk = Number(onboardingEnv?.disk_free_gb || 0);
            const performance = ram >= 32 ? '대형 모델과 멀티태스크에 적합' : ram >= 16 ? '중형 모델과 문서 작업에 적합' : '가벼운 모델과 기본 채팅에 적합';
            const purpose = getWorkspace() === 'company'
                ? '팀 권한, 보안 정책, 관리자 대시보드와 함께 쓰는 업무용 AI 워크스페이스'
                : '개인 프로젝트, 로컬 파일, 지식 그래프, 파이프라인을 연결하는 개인 AI 워크스페이스';
            const reason = [
                onboardingEnv?.os || '현재 OS',
                ram ? `RAM ${ram}GB` : '',
                disk ? `디스크 ${disk}GB 여유` : '',
                onboardingEnv?.chip?.is_apple_silicon ? 'Apple Silicon 가속 가능' : '현재 장치 기준'
            ].filter(Boolean).join(' · ');
            return {
                model: selected.modelId,
                runtime: selected.runtime,
                performance,
                purpose,
                reason: [reason, selected.reasonSource].filter(Boolean).join(' · '),
                selected,
            };
        }

        function renderRecommendationResult() {
            const rec = onboardingRecommendation();
            renderOnboardingShell(2, `
                <h2>추천 결과</h2>
                <p>분석 결과에 맞춰 시작 설정을 골랐습니다. 아래 설정으로 바로 설치와 검증을 진행할 수 있습니다.</p>
                <div class="recommendation-grid">
                    <article class="recommendation-card">
                        <h3>추천 모델</h3>
                        <p>${escapeHtml(rec.model)}</p>
                    </article>
                    <article class="recommendation-card">
                        <h3>추천 런타임</h3>
                        <p>${escapeHtml(rec.runtime)}</p>
                    </article>
                    <article class="recommendation-card">
                        <h3>예상 성능</h3>
                        <p>${escapeHtml(rec.performance)}</p>
                    </article>
                    <article class="recommendation-card">
                        <h3>사용 목적</h3>
                        <p>${escapeHtml(rec.purpose)}</p>
                    </article>
                    <article class="recommendation-card" style="grid-column:1/-1">
                        <h3>추천 이유</h3>
                        <p>${escapeHtml(rec.reason || '현재 PC 환경과 선택한 워크스페이스 기준으로 추천했습니다.')}</p>
                    </article>
                </div>
            `, `
                <button class="onboarding-secondary" onclick="renderOnboardingCustomModelSelect()">개인이 원하는 설정으로 시작</button>
                <button class="onboarding-primary" onclick="runOnboardingSetup()">추천 설정으로 시작하기</button>
            `);
        }

        function recommendedSetupItems() {
            const recs = onboardingRecs || {};
            return [...(recs.components || []), ...(recs.engines || []), ...(recs.models || [])]
                .filter(item => item.checked && !item.disabled)
                .map(item => ({ id: item.id, name: item.name, action: item.action || null }));
        }

        async function renderOnboardingCustomModelSelect() {
            renderOnboardingShell(2, `
                <h2>원하는 모델 선택</h2>
                <p>사용할 로컬 모델을 직접 선택하세요. 선택하면 다운로드와 로드까지 바로 진행합니다.</p>
                <div class="scan-pulse">
                    <div class="scan-spinner"></div>
                    <span>선택 가능한 모델을 불러오는 중...</span>
                </div>
            `, `<button class="onboarding-secondary" onclick="renderRecommendationResult()">추천 결과로 돌아가기</button>`);

            try {
                const res = await apiFetch('/engines');
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || '모델 목록을 불러오지 못했습니다.');
                const engines = (data.engines || [])
                    .filter(engine => ['local', 'local-server'].includes(engine.kind) && (engine.models || []).length);

                const engineHtml = engines.map(engine => {
                    const models = (engine.models || []).filter(model => model && model.id);
                    if (!models.length) return '';
                    return `
                        <article class="onboarding-model-card">
                            <h3>${escapeHtml(engine.name || engine.id)}</h3>
                            <div class="onboarding-model-grid">
                                ${models.map(model => onboardingModelOptionHtml(model, engine)).join('')}
                            </div>
                        </article>
                    `;
                }).filter(Boolean).join('');

                renderOnboardingShell(2, `
                    <h2>원하는 모델 선택</h2>
                    <p>사용할 로컬 모델을 직접 선택하세요. 선택하면 다운로드와 로드까지 바로 진행합니다.</p>
                    <div class="onboarding-model-list">
                        ${engineHtml || '<div class="sensitivity-preview">선택 가능한 로컬 모델이 없습니다.</div>'}
                    </div>
                `, `<button class="onboarding-secondary" onclick="renderRecommendationResult()">추천 결과로 돌아가기</button>`);
            } catch (e) {
                renderOnboardingShell(2, `
                    <h2>모델 목록을 불러오지 못했습니다.</h2>
                    <p>잠시 후 다시 시도해 주세요.</p>
                `, `
                    <button class="onboarding-secondary" onclick="renderRecommendationResult()">추천 결과로 돌아가기</button>
                    <button class="onboarding-primary" onclick="renderOnboardingCustomModelSelect()">다시 불러오기</button>
                `);
            }
        }

        function onboardingModelOptionHtml(model, engine) {
            const disabled = engine.supported === false || model.available === false;
            const modelId = model.id || '';
            const payload = encodeURIComponent(JSON.stringify({
                modelId,
                engine: engine.id || '',
                label: model.name || compactModelName(modelId),
                runtime: engine.name || engine.id || '로컬 런타임',
            }));
            const badge = disabled
                ? (engine.reason || model.requires || '현재 환경에서 사용할 수 없음')
                : [model.size, model.tag || model.family].filter(Boolean).join(' · ');
            return `
                <button class="onboarding-model-option" ${disabled ? 'disabled' : ''} onclick="runOnboardingCustomModelSetup('${payload}')">
                    <strong>${escapeHtml(model.name || compactModelName(modelId))}</strong>
                    <span>${escapeHtml(modelId)}</span>
                    <span>${escapeHtml(badge || '다운로드 후 로드')}</span>
                </button>
            `;
        }

        function runOnboardingCustomModelSetup(encodedPayload) {
            const payload = JSON.parse(decodeURIComponent(encodedPayload || '%7B%7D'));
            const selection = {
                modelId: payload.modelId,
                engine: payload.engine || 'local_mlx',
                label: payload.label || compactModelName(payload.modelId),
                runtime: payload.runtime || payload.engine || '로컬 런타임',
            };
            onboardingSelectedModel = selection;
            return runOnboardingModelSetup(selection);
        }

        async function runOnboardingSetup() {
            const selection = getOnboardingRecommendedModel();
            onboardingSelectedModel = selection;
            return runOnboardingModelSetup(selection);
        }

        async function runOnboardingModelSetup(selection) {
            const modelId = selection?.modelId || '';
            const engine = selection?.engine || 'local_mlx';
            renderOnboardingShell(3, `
                <h2>자동 설정과 검증을 진행합니다.</h2>
                <p>${escapeHtml(selection?.label || compactModelName(modelId))} 모델을 준비하고 사용할 수 있는지 확인합니다.</p>
                <div class="setup-log-list" id="onboarding-setup-log">
                    <div class="setup-log-row" id="setup-row-engine">
                        <i class="ti ti-loader-2"></i>
                        <span>${escapeHtml(selection?.runtime || engine)} 확인 중...</span>
                    </div>
                    <div class="setup-log-row" id="setup-row-download">
                        <i class="ti ti-loader-2"></i>
                        <span>모델 다운로드 대기 중...</span>
                    </div>
                    <div class="setup-log-row" id="setup-row-load">
                        <i class="ti ti-loader-2"></i>
                        <span>모델 로드 대기 중...</span>
                    </div>
                    <div class="onboarding-progress-meta" id="onboarding-progress-meta">${escapeHtml(modelId)}</div>
                </div>
            `);

            try {
                let finalData = null;
                const resp = await apiFetch('/engines/prepare-model/stream', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ model: modelId, engine, user_email: currentUserEmail || null }),
                });
                if (!resp.ok) {
                    const raw = await resp.text();
                    let detail = raw || '설치 스트림을 시작하지 못했습니다.';
                    try {
                        const parsed = JSON.parse(raw);
                        detail = parsed.detail || detail;
                    } catch (_) {}
                    throw new Error(detail);
                }
                if (!resp.body) throw new Error('설치 스트림을 시작하지 못했습니다.');

                await readModelPrepareStream(resp, (event, data) => {
                    if (event === 'progress') {
                        updateOnboardingModelProgress(data, selection);
                        return;
                    }
                    if (event === 'done') {
                        finalData = data;
                        return;
                    }
                    if (event === 'error') {
                        const detail = typeof data?.detail === 'string'
                            ? data.detail
                            : JSON.stringify(data?.detail || data);
                        throw new Error(detail || '모델 준비에 실패했습니다.');
                    }
                });
                if (!finalData) throw new Error('모델 준비 응답이 비어 있습니다.');
                await loadModelStatus();
                renderOnboardingSetupDone(selection, finalData);
            } catch (e) {
                const log = document.getElementById('onboarding-setup-log');
                if (log) {
                    log.innerHTML += `
                        <div class="setup-log-row error">
                            <i class="ti ti-alert-triangle"></i>
                            <span>설정 중 문제가 발생했습니다. 다시 시도해 주세요.</span>
                        </div>`;
                }
                document.getElementById('onboarding-actions').innerHTML =
                    `<button class="onboarding-primary" onclick="runOnboardingModelSetup(onboardingSelectedModel)">다시 시도</button>`;
            }
        }

        function setOnboardingSetupRow(id, status, message) {
            const row = document.getElementById(id);
            if (!row) return;
            const done = status === 'done';
            const failed = status === 'error';
            row.classList.toggle('done', done);
            row.classList.toggle('error', failed);
            row.innerHTML = `
                <i class="ti ${failed ? 'ti-alert-triangle' : done ? 'ti-check' : 'ti-loader-2'}"></i>
                <span>${escapeHtml(message)}</span>
            `;
        }

        function updateOnboardingModelProgress(progress = {}, selection = {}) {
            const stage = progress.stage || '';
            const percent = Number(progress.percent);
            const percentText = Number.isFinite(percent) ? `${Math.round(percent)}%` : '진행 중';
            const total = Number(progress.total_bytes || 0);
            const downloaded = Number(progress.downloaded_bytes || 0);
            const eta = Number(progress.eta_seconds);
            const etaText = Number.isFinite(eta) ? formatDownloadEta(eta) : '계산 중';
            const bytesText = total > 0
                ? `${formatDownloadBytes(downloaded)} / ${formatDownloadBytes(total)} · ${etaText}`
                : etaText;
            const detail = progress.detail || progress.file || selection.modelId || '';
            const meta = document.getElementById('onboarding-progress-meta');
            if (meta) meta.textContent = [percentText, bytesText, detail].filter(Boolean).join(' · ');

            if (stage === 'engine') {
                setOnboardingSetupRow('setup-row-engine', percent >= 10 ? 'done' : 'running', progress.message || '실행 엔진 확인 중...');
                return;
            }
            if (stage === 'download') {
                setOnboardingSetupRow('setup-row-engine', 'done', `${selection.runtime || '실행 엔진'} 준비 완료`);
                setOnboardingSetupRow('setup-row-download', percent >= 100 ? 'done' : 'running', `${progress.message || '모델 다운로드 중입니다.'} ${percentText}`);
                return;
            }
            if (stage === 'server' || stage === 'load') {
                setOnboardingSetupRow('setup-row-engine', 'done', `${selection.runtime || '실행 엔진'} 준비 완료`);
                setOnboardingSetupRow('setup-row-download', 'done', '모델 다운로드 확인 완료');
                setOnboardingSetupRow('setup-row-load', 'running', progress.message || '모델 로드 중...');
                return;
            }
            if (stage === 'done') {
                setOnboardingSetupRow('setup-row-engine', 'done', `${selection.runtime || '실행 엔진'} 준비 완료`);
                setOnboardingSetupRow('setup-row-download', 'done', '모델 다운로드 확인 완료');
                setOnboardingSetupRow('setup-row-load', 'done', '모델 로드 완료');
            }
        }

        function updateOnboardingInstallEvent(ev) {
            if (!ev || ev.status === 'complete') return;
            const row = document.getElementById(`setup-row-${ev.id}`);
            if (!row) return;
            const done = ['done', 'skipped', 'ready'].includes(ev.status);
            const failed = ev.status === 'error';
            row.classList.toggle('done', done);
            row.classList.toggle('error', failed);
            row.innerHTML = `
                <i class="ti ${failed ? 'ti-alert-triangle' : done ? 'ti-check' : 'ti-loader-2'}"></i>
                <span>${escapeHtml(ev.msg || (done ? '완료' : '진행 중...'))}</span>
            `;
        }

        function renderOnboardingSetupDone(selection = {}, finalData = {}) {
            const loaded = finalData.current || selection.modelId || '선택한 모델';
            renderOnboardingShell(3, `
                <h2>설정과 검증이 완료되었습니다.</h2>
                <p>이제 사용할 화면 밀도를 선택하면 Home으로 이동합니다.</p>
                <div class="setup-log-list">
                    <div class="setup-log-row done"><i class="ti ti-check"></i><span>${escapeHtml(compactModelName(loaded))} 로드 완료</span></div>
                    <div class="setup-log-row done"><i class="ti ti-check"></i><span>워크스페이스 설정 저장 완료</span></div>
                </div>
            `, `<button class="onboarding-primary" onclick="renderOnboardingModeSelect()">모드 선택으로 이동</button>`);
        }

        function renderOnboardingModeSelect() {
            const adminCard = isAdmin ? `
                <button class="onboarding-mode" onclick="finishOnboarding('admin')">
                    <i class="ti ti-shield-lock"></i>
                    <h3>관리자 모드</h3>
                    <p>운영자와 관리자를 위한 관리자 대시보드를 사용합니다.</p>
                </button>` : '';
            renderOnboardingShell(4, `
                <h2>모드 선택</h2>
                <p>처음 사용할 화면 구성을 선택하세요. 이후 상단 토글에서 바로 바꿀 수 있습니다.</p>
                <div class="onboarding-mode-grid">
                    <button class="onboarding-mode" onclick="finishOnboarding('default')">
                        <i class="ti ti-layout-dashboard"></i>
                        <h3>기본 모드</h3>
                        <p>일반 사용자를 위한 홈, 채팅, 파일, 지식 그래프, 파이프라인 중심 화면입니다.</p>
                    </button>
                    <button class="onboarding-mode" onclick="finishOnboarding('advanced')">
                        <i class="ti ti-terminal-2"></i>
                        <h3>고급 모드</h3>
                        <p>모델 상태, 런타임 설정, 고급 설정까지 함께 다룹니다.</p>
                    </button>
                    ${adminCard}
                </div>
            `);
        }

        function finishOnboarding(mode) {
            selectMode(mode);
            setOnboardingComplete();
            document.getElementById('onboarding-overlay')?.classList.remove('open');
            showHome();
        }

        function switchAcctTab(tab) {
            ['profile', 'password'].forEach(t => {
                document.getElementById(`tab-${t}`).classList.toggle('active', t === tab);
                document.getElementById(`panel-${t}`).classList.toggle('active', t === tab);
            });
        }
        async function openAcctModal() {
            ['profile-msg', 'pw-msg'].forEach(id => {
                const el = document.getElementById(id);
                el.textContent = ''; el.className = 'pw-msg';
            });
            ['pw-cur', 'pw-new', 'pw-new2'].forEach(id => document.getElementById(id).value = '');
            switchAcctTab('profile');
            try {
                const res = await fetch('/account/profile');
                if (res.ok) {
                    const data = await res.json();
                    document.getElementById('profile-name').value = data.name || '';
                    document.getElementById('profile-nickname').value = data.nickname || '';
                }
            } catch {}
            document.getElementById('acct-modal-overlay').classList.add('open');
        }
        function closeAcctModal() {
            document.getElementById('acct-modal-overlay').classList.remove('open');
        }
        document.addEventListener('click', (e) => {
            const overlay = document.getElementById('acct-modal-overlay');
            if (e.target === overlay) closeAcctModal();
        });
        async function submitProfileChange() {
            const name = document.getElementById('profile-name').value.trim();
            const nickname = document.getElementById('profile-nickname').value.trim();
            const msg = document.getElementById('profile-msg');
            const btn = document.getElementById('profile-submit-btn');
            if (!name || !nickname) {
                msg.textContent = '이름과 닉네임을 입력해주세요.';
                msg.className = 'pw-msg error';
                return;
            }
            btn.disabled = true; btn.textContent = '저장 중...';
            try {
                const res = await fetch('/account/profile', {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name, nickname })
                });
                const data = await res.json();
                if (res.ok) {
                    currentUserNickname = data.nickname;
                    localStorage.setItem('ltcai_user_nickname', data.nickname);
                    document.getElementById('user-nickname-display').innerText = data.nickname;
                    const av = document.getElementById('user-avatar-initial');
                    if (av) av.textContent = (data.nickname || 'G')[0].toUpperCase();
                    msg.textContent = '✅ 프로필이 변경되었습니다.';
                    msg.className = 'pw-msg success';
                    setTimeout(closeAcctModal, 1500);
                } else {
                    msg.textContent = data.detail || '저장 실패';
                    msg.className = 'pw-msg error';
                }
            } catch {
                msg.textContent = '서버 연결 실패';
                msg.className = 'pw-msg error';
            } finally {
                btn.disabled = false; btn.textContent = '저장';
            }
        }
        async function submitPwChange() {
            const cur = document.getElementById('pw-cur').value;
            const nw = document.getElementById('pw-new').value;
            const nw2 = document.getElementById('pw-new2').value;
            const msg = document.getElementById('pw-msg');
            const btn = document.getElementById('pw-submit-btn');
            if (!cur || !nw || !nw2) {
                msg.textContent = '모든 항목을 입력해주세요.';
                msg.className = 'pw-msg error';
                return;
            }
            if (nw !== nw2) {
                msg.textContent = '새 비밀번호가 일치하지 않습니다.';
                msg.className = 'pw-msg error';
                return;
            }
            if (nw.length < 4) {
                msg.textContent = '새 비밀번호는 4자 이상이어야 합니다.';
                msg.className = 'pw-msg error';
                return;
            }
            btn.disabled = true; btn.textContent = '변경 중...';
            try {
                const res = await fetch('/account/change-password', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ current_password: cur, new_password: nw })
                });
                const data = await res.json();
                if (res.ok) {
                    msg.textContent = '✅ 비밀번호가 변경되었습니다.';
                    msg.className = 'pw-msg success';
                    setTimeout(closeAcctModal, 1500);
                } else {
                    msg.textContent = data.detail || '변경 실패';
                    msg.className = 'pw-msg error';
                }
            } catch {
                msg.textContent = '서버 연결 실패';
                msg.className = 'pw-msg error';
            } finally {
                btn.disabled = false; btn.textContent = '변경';
            }
        }

        function adminHeaders() {
            return {
                'Content-Type': 'application/json',
                'X-Admin-Email': currentUserEmail,
            };
        }

        function vpcHealthText(config) {
            if (!config) return '대기';
            if (config.vpn_status === 'connected' || config.peering_status === 'active') return '연결됨';
            if (config.vpn_status === 'standby') return '대기';
            return config.vpn_status || config.peering_status || '설정 필요';
        }

        function renderVpcStatus(config) {
            latestVpcConfig = config;
            const provider = config.provider || 'VPC';
            const region = config.region || '-';
            const cidr = config.cidr_block || '-';
            const endpoint = config.endpoint || 'private endpoint pending';
            const health = vpcHealthText(config);
            document.getElementById('ops-vpc').textContent = `${provider} ${region}`;
            document.getElementById('ops-vpc-meta').textContent = `${cidr} · ${endpoint}`;
            if (vpcHeaderPill) {
                vpcHeaderPill.classList.add('vpc-ready');
                vpcHeaderPill.innerHTML = `<i class="ti ti-network"></i> VPC ${escapeHtml(health)}`;
            }
        }

        function compactModelName(modelId) {
            if (!modelId) return '모델 로드 대기';
            const clean = String(modelId).replaceAll('mlx-community/', '');
            const parts = clean.split('_').filter(Boolean);
            const primary = parts[0] || clean;
            if (primary.length <= 42) return primary;
            return `${primary.slice(0, 24)}...${primary.slice(-12)}`;
        }

        function modelOptionHtml(model, group, engine) {
            const engineMissing = engine && !engine.installed;
            const isLocalEngine = engine && (engine.kind === 'local' || engine.kind === 'local-server');
            const unsupported = engine?.supported === false;
            const keyMissing = model.available === false;
            const verifyFailed = engine?.kind === 'cloud' && model.verified === false;
            const verifyUnknown = engine?.kind === 'cloud' && model.verified == null;
            const supportsPull = model.pullable === true;
            const needsPull = supportsPull && engine.installed && model.pulled === false;
            const isUnavailable = unsupported || (!isLocalEngine && engineMissing) || keyMissing || verifyFailed;
            const badge = unsupported ? '현재 환경 미지원'
                : engineMissing && isLocalEngine ? '설치 후 자동 로드'
                : engineMissing ? '엔진 설치 필요'
                : needsPull ? '다운로드 후 자동 로드'
                : keyMissing ? `필요: ${model.requires || 'API key'}`
                : verifyFailed ? `실패: ${model.verify_reason || '검증 실패'}`
                : verifyUnknown ? '실사용 테스트 필요'
                : (model.tag || group);
            const icon = isUnavailable ? 'ti-lock' : (engineMissing || needsPull) ? 'ti-cloud-download' : verifyUnknown ? 'ti-activity' : 'ti-switch-3';
            const cls = (engineMissing || needsPull) && isLocalEngine ? ' needs-pull' : '';
            const action = isLocalEngine
                ? `prepareAndLoadModel('${encodeURIComponent(model.id)}', '${engine?.id || ''}')`
                : `loadSelectedModel('${encodeURIComponent(model.id)}', '${engine?.id || ''}')`;
            return `
                <button class="model-option${cls}" ${isUnavailable ? 'disabled' : ''} onclick="${action}">
                    <div>
                        <strong>${escapeHtml(model.name || compactModelName(model.id))}</strong>
                        <span>${escapeHtml(model.id)} · ${escapeHtml(badge)}</span>
                    </div>
                    <i class="ti ${icon}"></i>
                </button>
            `;
        }

        function normalizedFamily(model) {
            const raw = `${model?.family || ''} ${model?.name || ''} ${model?.id || ''}`.toLowerCase();
            if (raw.includes('gpt')) return 'GPT';
            if (raw.includes('claude')) return 'Claude';
            if (raw.includes('grok')) return 'Grok';
            if (raw.includes('gemini')) return 'Gemini';
            if (raw.includes('mistral') || raw.includes('mixtral')) return 'Mistral';
            if (raw.includes('qwen')) return 'Qwen';
            if (raw.includes('llama')) return 'Llama';
            if (raw.includes('gemma')) return 'Gemma';
            if (raw.includes('phi')) return 'Phi';
            if (raw.includes('deepseek')) return 'DeepSeek';
            return (model?.family || '기타');
        }

        function renderEngineModelGroups(engine) {
            const models = engine.models || [];
            if (!models.length) return '<div class="sensitivity-preview">등록된 모델이 없습니다.</div>';

            const grouped = {};
            const familyOrder = [];
            for (const model of models) {
                const family = normalizedFamily(model);
                if (!grouped[family]) {
                    grouped[family] = [];
                    familyOrder.push(family);
                }
                grouped[family].push(model);
            }

            return familyOrder.map((family, idx) => {
                const variants = grouped[family];
                return `
                    <details class="family-group">
                        <summary class="family-summary">
                            <span>${escapeHtml(family)}</span>
                            <span class="family-count">${variants.length} variants</span>
                        </summary>
                        <div class="family-models">
                            ${variants.map(model => modelOptionHtml(model, engine.kind, engine)).join('')}
                        </div>
                    </details>
                `;
            }).join('');
        }

        function engineCardHtml(engine) {
            const waitingForServer = engine.id === 'lmstudio' && engine.installed && engine.server_ready === false;
            const unsupported = engine.supported === false;
            const state = unsupported ? '미지원' : waitingForServer ? '서버 대기' : engine.installed ? '설치됨' : '설치 필요';
            const needsApiKey = engine.kind === 'cloud' && !!engine.requires;
            const installButton = !unsupported && !needsApiKey && engine.installable && !engine.installed
                ? `<button class="admin-action" onclick="installEngine('${engine.id}')"><i class="ti ti-download"></i> 설치</button>`
                : '';
            const requirement = needsApiKey
                ? `<div class="api-key-form">
                    <input type="password" id="apikey-${engine.id}" class="api-key-input"
                        placeholder="${escapeHtml(engine.requires)} 입력" autocomplete="off"
                        onkeydown="if(event.key==='Enter')saveApiKey('${engine.id}','${engine.requires}')">
                    <button class="admin-action" onclick="saveApiKey('${engine.id}','${engine.requires}')">
                        <i class="ti ti-key"></i> 저장 후 사용
                    </button>
                   </div>`
                : '';
            return `
                <div class="engine-card">
                    <div class="engine-head">
                        <div>
                            <strong>${escapeHtml(engine.name)} <span class="role-badge">${escapeHtml(engine.kind)}</span></strong>
                            <span>${escapeHtml(engine.description || '')}</span>
                            ${requirement}
                            ${engine.note ? `<span>${escapeHtml(engine.note)}</span>` : ''}
                        </div>
                        <div class="engine-actions">
                            <span class="engine-state ${engine.installed && !waitingForServer && !unsupported ? 'ready' : ''}">${state}</span>
                            ${installButton}
                        </div>
                    </div>
                    ${renderEngineModelGroups(engine)}
                </div>
            `;
        }

        let modelPanelFilter = 'local';
        let cachedEngineList = [];

        function renderModelPanelList() {
            const modelList = document.getElementById('model-list');
            const localEngines = cachedEngineList.filter(engine => engine.kind === 'local' || engine.kind === 'local-server');
            const cloudEngines = cachedEngineList.filter(engine => engine.kind === 'cloud');
            const isLocal = modelPanelFilter === 'local';
            const target = isLocal ? localEngines : cloudEngines;
            const emptyText = isLocal ? '등록된 로컬 엔진이 없습니다.' : '등록된 클라우드 엔진이 없습니다.';

            modelList.innerHTML = `
                <div class="model-group-title">EXECUTION ENGINES</div>
                <div class="model-filter">
                    <button class="model-filter-btn ${isLocal ? 'active' : ''}" onclick="setModelPanelFilter('local')">Local LLM</button>
                    <button class="model-filter-btn ${!isLocal ? 'active' : ''}" onclick="setModelPanelFilter('cloud')">Cloud LLM</button>
                </div>
                ${!isLocal ? `
                    <div style="display:flex;justify-content:flex-end;margin:-2px 0 8px;">
                        <button class="admin-action" onclick="verifyCloudModels(true)"><i class="ti ti-activity"></i> Cloud 실사용 테스트</button>
                    </div>
                ` : ''}
                ${target.length ? target.map(engineCardHtml).join('') : `<div class="sensitivity-preview">${emptyText}</div>`}
            `;
        }

        function setModelPanelFilter(filter) {
            modelPanelFilter = filter === 'cloud' ? 'cloud' : 'local';
            renderModelPanelList();
        }

        async function openModelPanel() {
            document.getElementById('model-overlay').style.display = 'flex';
            document.getElementById('model-list').innerHTML = '<div class="sensitivity-preview">실행 엔진과 모델 목록을 불러오는 중입니다...</div>';
            try {
                const res = await apiFetch('/engines');
                if (!res.ok) throw new Error('엔진 목록을 불러오지 못했습니다.');
                const data = await res.json();
                cachedEngineList = data.engines || [];
                renderModelPanelList();
            } catch (e) {
                document.getElementById('model-list').innerHTML = `<div class="sensitivity-preview">${escapeHtml(e.message)}</div>`;
            }
        }

        async function verifyCloudModels(force = true) {
            const modelList = document.getElementById('model-list');
            modelList.innerHTML = `<div class="sensitivity-preview">Cloud 모델 실사용 테스트 중입니다... (provider별로 수 초~수십 초)</div>`;
            try {
                const res = await apiFetch('/engines/verify-cloud', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ force })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || 'Cloud 실사용 테스트 실패');
                await openModelPanel();
                addMessage('ai', `Cloud 모델 실사용 테스트를 완료했습니다. 실패한 모델은 잠금 상태로 표시됩니다.`);
            } catch (e) {
                modelList.innerHTML = `
                    <div class="sensitivity-preview">${escapeHtml(e.message)}</div>
                    <button class="admin-action" onclick="openModelPanel()" style="margin-top: 12px;">목록으로 돌아가기</button>
                `;
            }
        }

        function closeModelPanel() {
            document.getElementById('model-overlay').style.display = 'none';
        }

        async function installEngine(engineId) {
            document.getElementById('model-list').innerHTML = `<div class="sensitivity-preview">${escapeHtml(engineId)} 엔진을 설치하는 중입니다. 네트워크 상태에 따라 시간이 걸릴 수 있습니다...</div>`;
            try {
                const res = await apiFetch('/engines/install', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ engine: engineId })
                });
                const data = await res.json();
                if (!res.ok || data.returncode !== 0) {
                    throw new Error(data.detail || data.stderr || '엔진 설치에 실패했습니다.');
                }
                await openModelPanel();
            } catch (e) {
                document.getElementById('model-list').innerHTML = `
                    <div class="sensitivity-preview">${escapeHtml(e.message)}</div>
                    <button class="admin-action" onclick="openModelPanel()" style="margin-top: 12px;">목록으로 돌아가기</button>
                `;
            }
        }

        async function loadSelectedModel(encodedId, engine = '') {
            const modelId = decodeURIComponent(encodedId);
            document.getElementById('model-list').innerHTML = `<div class="sensitivity-preview">${escapeHtml(compactModelName(modelId))} 로드 중입니다...</div>`;
            try {
                const res = await apiFetch('/models/load', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ model_id: modelId, engine, user_email: currentUserEmail || null })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || '모델 로드에 실패했습니다.');
                closeModelPanel();
                await loadModelStatus();
                addMessage('ai', `모델을 <b>${escapeHtml(compactModelName(data.current || modelId))}</b>로 전환했습니다.`);
            } catch (e) {
                document.getElementById('model-list').innerHTML = `
                    <div class="sensitivity-preview">${escapeHtml(e.message)}</div>
                    <button class="admin-action" onclick="openModelPanel()" style="margin-top: 12px;">목록으로 돌아가기</button>
                `;
            }
        }

        async function saveApiKey(engineId, envKey) {
            const input = document.getElementById(`apikey-${engineId}`);
            const key = input ? input.value.trim() : '';
            if (!key) { input && input.focus(); return; }
            document.getElementById('model-list').innerHTML = `<div class="sensitivity-preview">${escapeHtml(envKey)} 저장 중...</div>`;
            try {
                const res = await apiFetch('/setup/set-api-key', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ provider: engineId, key, user_email: currentUserEmail || null })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || 'API 키 저장 실패');
                await openModelPanel();
                addMessage('ai', `<b>${escapeHtml(engineId.toUpperCase())}</b> API 키가 저장되었습니다. 이제 모델을 선택하세요.`);
            } catch (e) {
                document.getElementById('model-list').innerHTML = `
                    <div class="sensitivity-preview">${escapeHtml(e.message)}</div>
                    <button class="admin-action" onclick="openModelPanel()" style="margin-top: 12px;">목록으로 돌아가기</button>
                `;
            }
        }

        function formatDownloadBytes(bytes) {
            const value = Number(bytes || 0);
            if (!Number.isFinite(value) || value <= 0) return '0 B';
            const units = ['B', 'KB', 'MB', 'GB', 'TB'];
            let size = value;
            let unit = 0;
            while (size >= 1024 && unit < units.length - 1) {
                size /= 1024;
                unit += 1;
            }
            const digits = size >= 10 || unit === 0 ? 0 : 1;
            return `${size.toFixed(digits)} ${units[unit]}`;
        }

        function formatDownloadEta(seconds) {
            const value = Number(seconds);
            if (!Number.isFinite(value) || value < 0) return '계산 중';
            if (value <= 3) return '곧 완료';
            if (value < 60) return `${Math.ceil(value)}초 남음`;
            if (value < 3600) {
                const minutes = Math.floor(value / 60);
                const secs = Math.ceil(value % 60);
                return secs ? `${minutes}분 ${secs}초 남음` : `${minutes}분 남음`;
            }
            const hours = Math.floor(value / 3600);
            const minutes = Math.ceil((value % 3600) / 60);
            return minutes ? `${hours}시간 ${minutes}분 남음` : `${hours}시간 남음`;
        }

        function renderModelDownloadProgress(displayName) {
            document.getElementById('model-list').innerHTML = `
                <div class="model-download-card">
                    <div class="model-download-head">
                        <div>
                            <strong>${escapeHtml(displayName)}</strong>
                            <span id="model-download-stage">모델 준비를 시작합니다.</span>
                        </div>
                        <div id="model-download-percent" class="model-download-percent">0%</div>
                    </div>
                    <div class="model-download-track">
                        <div id="model-download-fill" class="model-download-fill"></div>
                    </div>
                    <div class="model-download-meta">
                        <div>
                            <span>다운로드</span>
                            <strong id="model-download-bytes">용량 확인 중</strong>
                        </div>
                        <div>
                            <span>남은 시간</span>
                            <strong id="model-download-eta">계산 중</strong>
                        </div>
                    </div>
                    <div id="model-download-detail" class="model-download-detail">
                        엔진 설치, 모델 다운로드, 서버 시작, 로드까지 자동으로 진행합니다. 첫 실행은 수 분이 걸릴 수 있습니다.
                    </div>
                </div>
            `;
        }

        function updateModelDownloadProgress(progress = {}) {
            const percentEl = document.getElementById('model-download-percent');
            const fillEl = document.getElementById('model-download-fill');
            const stageEl = document.getElementById('model-download-stage');
            const bytesEl = document.getElementById('model-download-bytes');
            const etaEl = document.getElementById('model-download-eta');
            const detailEl = document.getElementById('model-download-detail');
            if (!percentEl || !fillEl || !stageEl || !bytesEl || !etaEl || !detailEl) return;

            const parsedPercent = Number(progress.percent);
            const percent = Number.isFinite(parsedPercent) ? Math.max(0, Math.min(100, parsedPercent)) : null;
            const downloaded = Number(progress.downloaded_bytes || 0);
            const total = Number(progress.total_bytes || 0);
            const eta = Number(progress.eta_seconds);
            const isDone = progress.stage === 'done' || percent >= 100;

            percentEl.textContent = percent == null ? '계산 중' : `${Math.round(percent)}%`;
            fillEl.style.width = `${percent == null ? 42 : percent}%`;
            fillEl.classList.toggle('indeterminate', Boolean(progress.indeterminate) && percent == null);
            stageEl.textContent = progress.message || '모델 준비 중입니다.';
            bytesEl.textContent = total > 0
                ? `${formatDownloadBytes(downloaded)} / ${formatDownloadBytes(total)}`
                : isDone ? '완료' : '용량 확인 중';
            etaEl.textContent = isDone ? '완료' : Number.isFinite(eta) ? formatDownloadEta(eta) : '계산 중';
            detailEl.textContent = progress.detail || progress.file || '진행 상태를 확인하는 중입니다.';
        }

        async function readModelPrepareStream(response, onEvent) {
            const reader = response.body.getReader();
            const decoder = new TextDecoder('utf-8');
            let buffer = '';

            const dispatchBlock = (block) => {
                const lines = block.split('\n');
                let event = 'message';
                const dataLines = [];
                for (const line of lines) {
                    if (!line || line.startsWith(':')) continue;
                    const separator = line.indexOf(':');
                    const field = separator >= 0 ? line.slice(0, separator) : line;
                    const rawValue = separator >= 0 ? line.slice(separator + 1) : '';
                    const value = rawValue.startsWith(' ') ? rawValue.slice(1) : rawValue;
                    if (field === 'event') event = value || event;
                    if (field === 'data') dataLines.push(value);
                }
                if (!dataLines.length) return;
                const rawData = dataLines.join('\n');
                const data = rawData === '[DONE]' ? rawData : JSON.parse(rawData);
                onEvent(event, data);
            };

            while (true) {
                const { value, done } = await reader.read();
                if (value) {
                    buffer += decoder.decode(value, { stream: !done });
                    buffer = buffer.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
                    let boundary = buffer.indexOf('\n\n');
                    while (boundary !== -1) {
                        const block = buffer.slice(0, boundary);
                        buffer = buffer.slice(boundary + 2);
                        dispatchBlock(block);
                        boundary = buffer.indexOf('\n\n');
                    }
                }
                if (done) break;
            }
            if (buffer.trim()) dispatchBlock(buffer.trim());
        }

        async function prepareAndLoadModel(encodedId, engine = '') {
            const modelId = decodeURIComponent(encodedId);
            const displayName = compactModelName(modelId);
            renderModelDownloadProgress(displayName);
            try {
                let finalData = null;
                const res = await apiFetch('/engines/prepare-model/stream', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ model: modelId, engine, user_email: currentUserEmail || null })
                });
                if (!res.ok) {
                    const raw = await res.text();
                    let detail = raw || '모델 준비에 실패했습니다.';
                    try {
                        const parsed = JSON.parse(raw);
                        detail = parsed.detail || detail;
                    } catch (_) {}
                    throw new Error(detail);
                }
                if (!res.body) throw new Error('진행률 스트림을 열 수 없습니다.');

                await readModelPrepareStream(res, (event, data) => {
                    if (event === 'progress') {
                        updateModelDownloadProgress(data);
                        return;
                    }
                    if (event === 'done') {
                        finalData = data;
                        return;
                    }
                    if (event === 'error') {
                        const detail = typeof data?.detail === 'string'
                            ? data.detail
                            : JSON.stringify(data?.detail || data);
                        throw new Error(detail || '모델 준비에 실패했습니다.');
                    }
                });
                if (!finalData) throw new Error('모델 준비 응답이 비어 있습니다.');
                closeModelPanel();
                await loadModelStatus();
                addMessage('ai', `<b>${escapeHtml(compactModelName(finalData.current || modelId))}</b> 로드 되었습니다.`);
            } catch (e) {
                document.getElementById('model-list').innerHTML = `
                    <div class="sensitivity-preview">${escapeHtml(e.message)}</div>
                    <button class="admin-action" onclick="openModelPanel()" style="margin-top: 12px;">목록으로 돌아가기</button>
                `;
            }
        }

        async function pullAndLoadModel(encodedId, engine = '') {
            return prepareAndLoadModel(encodedId, engine);
        }

        function fillVpcForm(config) {
            if (!config) return;
            document.getElementById('vpc-provider').value = config.provider || '';
            document.getElementById('vpc-region').value = config.region || '';
            document.getElementById('vpc-cidr').value = config.cidr_block || '';
            document.getElementById('vpc-endpoint').value = config.endpoint || '';
            document.getElementById('vpc-vpn').value = config.vpn_status || '';
            document.getElementById('vpc-peering').value = config.peering_status || '';
            document.getElementById('vpc-subnets').value = (config.private_subnets || []).join(', ');
            document.getElementById('vpc-notes').value = config.notes || '';
            document.getElementById('vpc-save-status').textContent = config.updated_at
                ? `마지막 저장: ${new Date(config.updated_at).toLocaleString()}`
                : '기본 VPC 프로필을 사용 중입니다.';
        }

        async function loadVpcStatus() {
            try {
                const res = await apiFetch('/vpc/status');
                if (!res.ok) throw new Error('VPC 상태를 불러오지 못했습니다.');
                const config = await res.json();
                latestVpcConfig = config;
                renderVpcStatus(config);
                fillVpcForm(config);
                fillVpcPanelForm(config);
            } catch (e) {
                document.getElementById('ops-vpc').textContent = 'VPC 확인 실패';
                document.getElementById('ops-vpc-meta').textContent = e.message;
            }
        }

        async function saveVpcConfig() {
            if (!isAdmin) return;
            const payload = {
                provider: document.getElementById('vpc-provider').value.trim(),
                region: document.getElementById('vpc-region').value.trim(),
                cidr_block: document.getElementById('vpc-cidr').value.trim(),
                endpoint: document.getElementById('vpc-endpoint').value.trim(),
                vpn_status: document.getElementById('vpc-vpn').value.trim(),
                peering_status: document.getElementById('vpc-peering').value.trim(),
                private_subnets: document.getElementById('vpc-subnets').value.split(',').map(item => item.trim()).filter(Boolean),
                notes: document.getElementById('vpc-notes').value.trim()
            };
            document.getElementById('vpc-save-status').textContent = '저장 중입니다...';
            const res = await apiFetch('/admin/vpc', {
                method: 'PATCH',
                headers: adminHeaders(),
                body: JSON.stringify(payload)
            });
            if (!res.ok) {
                document.getElementById('vpc-save-status').textContent = '저장에 실패했습니다.';
                return;
            }
            const config = await res.json();
            renderVpcStatus(config);
            fillVpcForm(config);
        }

        async function loadModelStatus() {
            try {
                const res = await apiFetch('/health');
                if (!res.ok) return;
                const data = await res.json();
                const modelEl = document.getElementById('ops-model');
                modelEl.textContent = compactModelName(data.current_model);
                modelEl.title = data.current_model || '';
                const metaEl = document.getElementById('ops-model-meta');
                metaEl.dataset.loaded = 'true';
                metaEl.textContent = `${data.device || t('ops_local_runtime')} · ${data.loaded_models?.length || 0} loaded`;
            } catch (e) { }
        }

        async function openAdminPanel() {
            if (!isAdmin) {
                showToast(currentLang === 'ko' ? '관리자 권한이 없습니다.' : 'Admin access required.');
                return;
            }
            sessionStorage.setItem('ltcai_admin_handoff', JSON.stringify({
                email: currentUserEmail || '',
                nickname: currentUserNickname || '',
                is_admin: isAdmin ? 'true' : 'false',
            }));
            window.location.href = `${API_BASE || ''}/admin`;
        }

        function showToast(msg) {
            let t = document.getElementById('ltcai-toast');
            if (!t) {
                t = document.createElement('div');
                t.id = 'ltcai-toast';
                t.style.cssText = 'position:fixed;bottom:28px;left:50%;transform:translateX(-50%);background:#1e2330;color:#f8fafc;border:1px solid rgba(255,255,255,0.12);border-radius:10px;padding:10px 18px;font-size:13px;font-weight:600;z-index:9999;box-shadow:0 8px 24px rgba(0,0,0,0.4);pointer-events:none;transition:opacity .2s;';
                document.body.appendChild(t);
            }
            t.textContent = msg;
            t.style.opacity = '1';
            clearTimeout(t._timer);
            t._timer = setTimeout(() => { t.style.opacity = '0'; }, 2200);
        }

        function closeAdminPanel() {
            document.getElementById('admin-overlay').style.display = 'none';
        }

        // ── VPC Panel ────────────────────────────────────────
        function fillVpcPanelForm(config) {
            if (!config) return;
            document.getElementById('vp-provider').value = config.provider || 'AWS';
            document.getElementById('vp-region').value = config.region || '';
            document.getElementById('vp-cidr').value = config.cidr_block || '';
            document.getElementById('vp-endpoint').value = config.endpoint || '';
            document.getElementById('vp-vpn').value = config.vpn_status || '';
            document.getElementById('vp-peering').value = config.peering_status || '';
            document.getElementById('vp-subnets').value = (config.private_subnets || []).join(', ');
            document.getElementById('vp-notes').value = config.notes || '';
            document.getElementById('vp-save-status').textContent = config.updated_at
                ? `마지막 저장: ${new Date(config.updated_at).toLocaleString()}`
                : '';
            // activate tab
            const prov = (config.provider || 'AWS').trim();
            document.querySelectorAll('.vpc-tab-btn').forEach(btn => {
                btn.classList.toggle('active', btn.textContent === prov);
            });
            // update status bar
            const vpnStatus = (config.vpn_status || 'standby').toLowerCase();
            const dot = document.getElementById('vpc-dot');
            const txt = document.getElementById('vpc-status-text');
            dot.className = 'vpc-status-dot ' + (vpnStatus === 'connected' ? 'connected' : vpnStatus === 'error' ? 'error' : 'standby');
            txt.textContent = `${config.provider || 'VPC'} · ${config.region || '—'} · VPN ${config.vpn_status || 'standby'}`;
        }

        async function openVpcPanel() {
            document.getElementById('vpc-overlay').style.display = 'flex';
            try {
                const res = await apiFetch('/vpc/status');
                if (res.ok) fillVpcPanelForm(await res.json());
            } catch (e) {}
        }

        function closeVpcPanel() {
            document.getElementById('vpc-overlay').style.display = 'none';
        }

        function selectVpcProvider(name, btn) {
            document.querySelectorAll('.vpc-tab-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById('vp-provider').value = name;
            const hints = {
                AWS:    { region: 'ap-northeast-2', endpoint: 'ltcai.internal', cidr: '10.42.0.0/16' },
                GCP:    { region: 'asia-northeast3', endpoint: 'ltcai.internal', cidr: '10.128.0.0/20' },
                Azure:  { region: 'koreacentral',    endpoint: 'ltcai.internal', cidr: '10.0.0.0/16' },
                Custom: { region: '',                endpoint: '',               cidr: '' },
            };
            const h = hints[name] || hints.Custom;
            if (!document.getElementById('vp-region').value) document.getElementById('vp-region').value = h.region;
            if (!document.getElementById('vp-cidr').value)   document.getElementById('vp-cidr').value   = h.cidr;
            if (!document.getElementById('vp-endpoint').value) document.getElementById('vp-endpoint').value = h.endpoint;
        }

        async function saveVpcFromPanel() {
            const payload = {
                provider: document.getElementById('vp-provider').value.trim(),
                region: document.getElementById('vp-region').value.trim(),
                cidr_block: document.getElementById('vp-cidr').value.trim(),
                endpoint: document.getElementById('vp-endpoint').value.trim(),
                vpn_status: document.getElementById('vp-vpn').value.trim(),
                peering_status: document.getElementById('vp-peering').value.trim(),
                private_subnets: document.getElementById('vp-subnets').value.split(',').map(s => s.trim()).filter(Boolean),
                notes: document.getElementById('vp-notes').value.trim()
            };
            document.getElementById('vp-save-status').textContent = '저장 중...';
            try {
                const res = await apiFetch('/admin/vpc', {
                    method: 'PATCH',
                    headers: adminHeaders(),
                    body: JSON.stringify(payload)
                });
                if (!res.ok) throw new Error((await res.json()).detail || '저장 실패');
                const config = await res.json();
                fillVpcPanelForm(config);
                renderVpcStatus(config);
                latestVpcConfig = config;
                document.getElementById('vp-save-status').textContent = '저장되었습니다.';
            } catch (e) {
                document.getElementById('vp-save-status').textContent = e.message;
            }
        }

        // ── Status Summary Panel ─────────────────────────────
        async function openStatusPanel() {
            document.getElementById('status-overlay').style.display = 'flex';
            document.getElementById('status-panel-body').innerHTML = '<div class="sensitivity-preview">상태를 불러오는 중...</div>';
            try {
                const mode = getCurrentMode();
                const [healthRes] = await Promise.all([apiFetch('/health')]);
                const health = healthRes.ok ? await healthRes.json() : null;

                const currentModel = health?.current_model || '(없음)';
                const device       = health?.device        || 'local';
                const loadedCount  = health?.loaded_models?.length || 0;
                const baseHtml = `
                    <div class="status-section">
                        <div class="status-section-title">워크스페이스</div>
                        <div class="status-row">
                            <span class="status-row-label">유형</span>
                            <span class="status-row-value">${escapeHtml(workspaceLabel(getWorkspace()))}</span>
                        </div>
                        <div class="status-row">
                            <span class="status-row-label">모드</span>
                            <span class="status-row-value">${escapeHtml(modeLabel(mode))}</span>
                        </div>
                        <div class="status-row">
                            <span class="status-row-label">사용자</span>
                            <span class="status-row-value">${escapeHtml(currentUserNickname || 'Guest')}</span>
                        </div>
                    </div>

                    <div class="status-section">
                        <div class="status-section-title">계정</div>
                        <div class="status-row">
                            <span class="status-row-label">이메일</span>
                            <span class="status-row-value" style="font-size:12px">${escapeHtml(currentUserEmail || '—')}</span>
                        </div>
                        <div class="status-row">
                            <span class="status-row-label">관리자</span>
                            <span class="status-badge ${isAdmin ? 'ok' : 'warn'}">${isAdmin ? '권한 있음' : '권한 없음'}</span>
                        </div>
                    </div>`;

                const advancedHtml = mode === 'default' ? '' : `
                    <div class="status-section">
                        <div class="status-section-title">모델 상태</div>
                        <div class="status-row">
                            <span class="status-row-label">현재 모델</span>
                            <span class="status-row-value">${escapeHtml(compactModelName(currentModel))}</span>
                        </div>
                        <div class="status-row">
                            <span class="status-row-label">런타임</span>
                            <span class="status-row-value">${escapeHtml(device)}</span>
                        </div>
                        <div class="status-row">
                            <span class="status-row-label">로드된 모델</span>
                            <span class="status-row-value">${loadedCount}개</span>
                        </div>
                    </div>`;

                const actionHtml = mode === 'default' ? '' : `
                    <div style="display:flex;gap:8px;margin-top:4px;flex-wrap:wrap">
                        <button class="admin-action" onclick="closeStatusPanel();openModelPanel()">모델 상태 보기</button>
                        ${isAdmin ? `<button class="admin-action" onclick="closeStatusPanel();openAdminPanel()">관리자 대시보드</button>` : ''}
                    </div>`;

                document.getElementById('status-panel-body').innerHTML = baseHtml + advancedHtml + actionHtml;
            } catch (e) {
                document.getElementById('status-panel-body').innerHTML = `<div class="sensitivity-preview">${escapeHtml(safeErrorMessage(e))}</div>`;
            }
        }

        function closeStatusPanel() {
            document.getElementById('status-overlay').style.display = 'none';
        }

        // ── 파일 생성 패널 ────────────────────────────────────
        const FILE_TYPE_META = {
            docx: { icon: 'ti-file-word',        label: 'Word 문서 (DOCX)', ext: '.docx', color: '#2b5eb8' },
            xlsx: { icon: 'ti-file-spreadsheet',  label: 'Excel 스프레드시트 (XLSX)', ext: '.xlsx', color: '#1d7044' },
            pptx: { icon: 'ti-presentation',      label: 'PowerPoint 프레젠테이션 (PPTX)', ext: '.pptx', color: '#c43e1c' },
            pdf:  { icon: 'ti-file-type-pdf',     label: 'PDF 문서', ext: '.pdf', color: '#e0342a' },
        };
        let _currentFileType = 'docx';

        function openFileCreate(type) {
            _currentFileType = type;
            const meta = FILE_TYPE_META[type];
            document.getElementById('file-create-title').innerHTML = `<i class="ti ${meta.icon}" style="color:${meta.color}"></i> ${meta.label} 만들기`;
            document.getElementById('file-create-desc').textContent = '제목과 내용을 입력하면 AI가 파일을 생성합니다.';
            document.getElementById('file-create-status').textContent = '';

            let formHtml = `
                <div class="admin-field full">
                    <label>파일명</label>
                    <input id="fc-filename" class="admin-input" placeholder="document${meta.ext}" value="">
                </div>
                <div class="admin-field full">
                    <label>제목</label>
                    <input id="fc-title" class="admin-input" placeholder="문서 제목">
                </div>`;

            if (type === 'xlsx') {
                formHtml += `
                <div class="admin-field full">
                    <label>시트 이름</label>
                    <input id="fc-sheet" class="admin-input" placeholder="Sheet1" value="Sheet1">
                </div>
                <div class="admin-field full">
                    <label>데이터 (CSV 형식)</label>
                    <textarea id="fc-body" class="admin-textarea" placeholder="이름,나이,직업&#10;홍길동,30,개발자&#10;김철수,25,디자이너"></textarea>
                </div>`;
            } else if (type === 'pptx') {
                formHtml += `
                <div class="admin-field full">
                    <label>슬라이드 내용 (슬라이드 제목:: 내용1 / 내용2 형식으로 줄 구분)</label>
                    <textarea id="fc-body" class="admin-textarea" placeholder="소개:: Lattice AI란? / 로컬 MLX 기반 에이전트&#10;기능:: 파일 생성 / 로컬 파일 제어 / MCP 연결"></textarea>
                </div>`;
            } else {
                formHtml += `
                <div class="admin-field full">
                    <label>내용</label>
                    <textarea id="fc-body" class="admin-textarea" placeholder="문서 내용을 입력하세요..."></textarea>
                </div>`;
            }
            document.getElementById('file-create-form').innerHTML = formHtml;
            document.getElementById('file-create-overlay').style.display = 'flex';
        }

        function closeFileCreate() {
            document.getElementById('file-create-overlay').style.display = 'none';
        }

        function _formatBytes(b) {
            if (b < 1024) return b + ' B';
            if (b < 1024 * 1024) return (b / 1024).toFixed(1) + ' KB';
            return (b / (1024*1024)).toFixed(1) + ' MB';
        }

        function _fileIcon(ext) {
            const m = { '.docx':'ti-file-word', '.xlsx':'ti-file-spreadsheet', '.pptx':'ti-presentation', '.pdf':'ti-file-type-pdf' };
            return m[ext] || 'ti-file';
        }

        function renderFileDownloadCard(filename, path, bytes) {
            const ext = '.' + filename.split('.').pop();
            const icon = _fileIcon(ext);
            const div = document.createElement('div');
            div.className = 'file-download-card';
            div.innerHTML = `
                <div class="file-icon"><i class="ti ${icon}"></i></div>
                <div class="file-info">
                    <div class="file-name">${escapeHtml(filename)}</div>
                    <div class="file-meta">${_formatBytes(bytes)} · 생성 완료</div>
                </div>
                <a class="file-dl-btn" href="${API_BASE}/tools/download?path=${encodeURIComponent(path)}" download="${escapeHtml(filename)}">
                    <i class="ti ti-download"></i> 다운로드
                </a>`;
            chatViewport.appendChild(div);
            chatViewport.scrollTop = chatViewport.scrollHeight;
        }

        async function submitFileCreate() {
            const type = _currentFileType;
            const filename = (document.getElementById('fc-filename')?.value.trim()) || `document.${type}`;
            const title = document.getElementById('fc-title')?.value.trim() || '';
            const body  = document.getElementById('fc-body')?.value.trim() || '';
            const status = document.getElementById('file-create-status');
            status.textContent = '생성 중...';

            try {
                let endpoint, payload;
                if (type === 'docx') {
                    endpoint = '/tools/create_docx';
                    payload = { title, body, filename: filename.endsWith('.docx') ? filename : filename + '.docx' };
                } else if (type === 'pdf') {
                    endpoint = '/tools/create_pdf';
                    payload = { title, body, filename: filename.endsWith('.pdf') ? filename : filename + '.pdf' };
                } else if (type === 'xlsx') {
                    endpoint = '/tools/create_xlsx';
                    const rows = body.split('\n').map(line => line.split(',').map(c => c.trim()));
                    payload = { rows, filename: filename.endsWith('.xlsx') ? filename : filename + '.xlsx', sheet_name: document.getElementById('fc-sheet')?.value.trim() || 'Sheet1' };
                } else if (type === 'pptx') {
                    endpoint = '/tools/create_pptx';
                    const slides = body.split('\n').filter(Boolean).map(line => {
                        const [slideTitle, rest] = line.split('::');
                        const bullets = (rest || '').split('/').map(s => s.trim()).filter(Boolean);
                        return { title: slideTitle.trim(), bullets };
                    });
                    payload = { title, slides, filename: filename.endsWith('.pptx') ? filename : filename + '.pptx' };
                }

                const res = await apiFetch(endpoint, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || '생성 실패');

                closeFileCreate();
                renderFileDownloadCard(filename, data.path, data.bytes);
                addMessage('ai', `<b>${escapeHtml(filename)}</b> 파일이 생성되었습니다. 아래 다운로드 버튼으로 저장하세요.`);
            } catch(e) {
                status.textContent = e.message;
            }
        }


        // 권한 요청 Promise 핸들러
        let _permResolve = null;

        function requestPermission(path, action, actionLabel) {
            return new Promise(resolve => {
                _permResolve = resolve;
                document.getElementById('perm-title').textContent = '파일 접근 요청';
                document.getElementById('perm-path').textContent = path;
                document.getElementById('perm-desc').textContent = `AI가 아래 경로에 대한 "${actionLabel}" 작업을 요청합니다. 허용하시겠습니까?`;
                document.getElementById('perm-overlay').style.display = 'flex';
            });
        }

        function resolvePermission(allowed) {
            document.getElementById('perm-overlay').style.display = 'none';
            if (_permResolve) { _permResolve(allowed); _permResolve = null; }
        }

        let _localCurrentPath = '~';

        async function openLocalBrowser() {
            document.getElementById('local-browser-overlay').style.display = 'flex';
            await localNav(_localCurrentPath || '~');
        }

        function closeLocalBrowser() {
            document.getElementById('local-browser-overlay').style.display = 'none';
        }

        async function localNav(path) {
            _localCurrentPath = path;
            document.getElementById('local-breadcrumb').textContent = path;
            await browseLocalPath(path);
        }

        async function localNavUp() {
            const parts = _localCurrentPath.replace(/\/$/, '').split('/');
            if (parts.length <= 1) return;
            parts.pop();
            await localNav(parts.join('/') || '/');
        }

        async function getLocalApprovalToken(path, action = 'read', content = '') {
            const endpoint = action === 'write' ? '/local/write' : action === 'list' ? '/local/list' : '/local/read';
            const payload = action === 'write'
                ? { path, content, approved: false }
                : { path, approved: false };
            const probe = await apiFetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const probeData = await probe.json();
            if (!probeData.permission_required) return probeData.approval_token || '';
            const allowed = await requestPermission(probeData.path, probeData.action, probeData.action_label);
            if (!allowed) return '';
            const token = probeData.approval_token || '';
            if (!token) return '';
            const approval = await apiFetch(`/permissions/approve/${encodeURIComponent(token)}`, { method: 'POST' });
            if (!approval.ok) {
                const data = await approval.json().catch(() => ({}));
                throw new Error(data.detail || '파일 접근 승인 실패');
            }
            return token;
        }

        async function browseLocalPath(path) {
            path = path ?? _localCurrentPath;
            const resultEl = document.getElementById('local-browser-result');
            resultEl.innerHTML = '<div class="sensitivity-preview">불러오는 중...</div>';

            const approvalToken = await getLocalApprovalToken(path, 'list');
            if (!approvalToken) {
                resultEl.innerHTML = '<div class="sensitivity-preview">접근이 거부되었습니다.</div>';
                return;
            }

            try {
                const res = await apiFetch('/local/list', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path, approved: true, approval_token: approvalToken })
                });
                const data = await res.json();
                if (!res.ok || data.error) throw new Error(data.error || data.detail || '오류');
                const listing = data.result ?? data;
                _localCurrentPath = listing.path ?? path;
                document.getElementById('local-breadcrumb').textContent = _localCurrentPath;
                renderLocalListing(listing, resultEl);
            } catch(e) {
                resultEl.innerHTML = `<div class="sensitivity-preview">${escapeHtml(e.message)}</div>`;
            }
        }

        function renderLocalListing(data, container) {
            if (!data.items?.length) {
                container.innerHTML = '<div class="sensitivity-preview">비어 있는 폴더입니다.</div>';
                return;
            }
            container.innerHTML = `<div style="display:flex;flex-direction:column;gap:3px">
                ${data.items.map(item => {
                    const isDir = item.type === 'directory';
                    const enc = encodeURIComponent(item.path);
                    return `<div style="display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:6px;border:1px solid var(--border);background:var(--surface-2);cursor:pointer"
                         onclick="${isDir ? `localNav('${item.path.replace(/'/g,"\\'")}')` : `readLocalFile('${enc}')`}">
                        <i class="ti ${isDir ? 'ti-folder' : 'ti-file'}" style="color:${isDir ? '#f0a500' : 'var(--muted)'}"></i>
                        <span style="flex:1;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(item.name)}</span>
                        ${item.size !== null ? `<span style="color:var(--faint);font-size:11px;flex-shrink:0">${_formatBytes(item.size)}</span>` : ''}
                    </div>`;
                }).join('')}
            </div>`;
        }

        async function navigateLocalPath(encodedPath) {
            await localNav(decodeURIComponent(encodedPath));
        }

        let _editorCurrentPath = '';

        const IMAGE_EXTS  = new Set(['png','jpg','jpeg','gif','webp','bmp','svg','ico','tiff','heic']);
        const VIDEO_EXTS  = new Set(['mp4','mov','webm','avi','mkv','m4v']);
        const DOC_EXTS    = new Set(['pdf','docx','xlsx','pptx','doc','xls','ppt','csv','md','txt']);
        const ARCHIVE_EXTS= new Set(['zip','dmg','pkg','gz','tar','rar','7z','iso']);
        const BINARY_EXTS = new Set(['exe','bin','dll','so','dylib','db','sqlite','mp3','wav','aac','flac']);

        async function readLocalFile(encodedPath) {
            const path = decodeURIComponent(encodedPath);
            const resultEl = document.getElementById('local-browser-result');
            const ext = path.split('.').pop().toLowerCase();
            const readApprovalToken = await getLocalApprovalToken(path, 'read');
            if (!readApprovalToken) {
                resultEl.innerHTML = '<div class="sensitivity-preview">접근이 거부되었습니다.</div>';
                return;
            }
            const localServeUrl = `/local/serve?path=${encodeURIComponent(path)}&approval_token=${encodeURIComponent(readApprovalToken)}`;

            // 이미지 파일: 미리보기 + AI 전송
            if (IMAGE_EXTS.has(ext)) {
                resultEl.innerHTML = `
                    <div style="text-align:center;padding:12px">
                        <img src="${localServeUrl}" alt="${escapeHtml(path.split('/').pop())}"
                             style="max-width:100%;max-height:280px;border-radius:8px;border:1px solid var(--border)"
                             onerror="this.parentElement.innerHTML='<div style=color:var(--faint)>이미지 미리보기 불가</div>'">
                        <div style="color:var(--faint);font-size:11px;margin-top:8px">${escapeHtml(path.split('/').pop())}</div>
                        <div style="display:flex;gap:8px;margin-top:12px;justify-content:center">
                            <button class="admin-action" style="font-size:12px;flex:1"
                                onclick="sendImageFileToChat('${path.replace(/\\/g,'\\\\').replace(/'/g,"\\'")}')">
                                <i class='ti ti-send'></i> AI에게 보내기
                            </button>
                            <button class="status-btn" style="font-size:12px"
                                onclick="navigator.clipboard.writeText('${path.replace(/'/g,"\\'")}')">
                                <i class='ti ti-copy'></i> 경로 복사
                            </button>
                        </div>
                    </div>`;
                return;
            }

            // 동영상: HTML5 플레이어
            if (VIDEO_EXTS.has(ext)) {
                resultEl.innerHTML = `
                    <div style="text-align:center;padding:8px">
                        <video controls style="max-width:100%;max-height:280px;border-radius:8px;border:1px solid var(--border)"
                               src="${localServeUrl}">지원하지 않는 형식</video>
                        <div style="color:var(--faint);font-size:11px;margin-top:6px">${escapeHtml(path.split('/').pop())}</div>
                        <button class="status-btn" style="font-size:12px;margin-top:8px"
                            onclick="sendArchiveToChat('${path.replace(/'/g,"\\'")}','video')">
                            <i class='ti ti-send'></i> AI에게 경로 보내기
                        </button>
                    </div>`;
                return;
            }

            // PDF: 브라우저 내장 뷰어 + AI 전송 (페이지 이미지 + 텍스트)
            if (ext === 'pdf') {
                const safePath = path.replace(/\\/g,'\\\\').replace(/'/g,"\\'");
                resultEl.innerHTML = `
                    <div style="display:flex;flex-direction:column;gap:10px">
                        <embed src="${localServeUrl}#toolbar=0"
                               type="application/pdf"
                               style="width:100%;height:340px;border-radius:8px;border:1px solid var(--border)">
                        <div style="display:flex;gap:8px">
                            <button class="admin-action" style="flex:1;font-size:12px"
                                onclick="sendPdfToAI('${safePath}')">
                                <i class='ti ti-brain'></i> AI에게 보내기 (이미지+텍스트)
                            </button>
                            <button class="status-btn" style="flex:1;font-size:12px"
                                onclick="openPdfTextEditor('${safePath}')">
                                <i class='ti ti-text-size'></i> 텍스트만 보기
                            </button>
                        </div>
                        <div id="pdf-send-status" style="font-size:12px;color:var(--faint);text-align:center"></div>
                    </div>`;
                return;
            }

            // 기타 문서(DOCX/XLSX 등): 텍스트 추출 후 에디터로
            if (DOC_EXTS.has(ext)) {
                resultEl.innerHTML = '<div class="sensitivity-preview">문서 읽는 중...</div>';
                try {
                    const res = await apiFetch('/tools/read_document', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ path, approval_token: readApprovalToken })
                    });
                    const data = await res.json();
                    if (!res.ok || data.error) throw new Error(data.error || data.detail || '읽기 실패');
                    const text = (data.result ?? data).content ?? '';
                    _editorCurrentPath = path;
                    document.getElementById('editor-filepath').textContent = path;
                    document.getElementById('file-editor-content').value = text;
                    document.getElementById('editor-status').textContent = '';
                    document.getElementById('local-browser-overlay').style.display = 'none';
                    document.getElementById('file-editor-overlay').style.display = 'flex';
                } catch(e) {
                    resultEl.innerHTML = `
                        <div class="sensitivity-preview">⚠️ 문서 읽기 실패: ${escapeHtml(e.message)}<br>
                            <button class="admin-action" style="margin-top:10px;font-size:12px"
                                onclick="sendArchiveToChat('${path.replace(/'/g,"\\'")}','document')">
                                <i class='ti ti-send'></i> AI에게 경로 보내기
                            </button>
                        </div>`;
                }
                return;
            }

            // 압축/디스크 이미지: 파일 정보 + AI 경로 전송
            if (ARCHIVE_EXTS.has(ext)) {
                resultEl.innerHTML = `
                    <div class="sensitivity-preview" style="text-align:center">
                        <i class="ti ti-archive" style="font-size:36px;color:var(--accent-2);display:block;margin-bottom:8px"></i>
                        <div style="font-size:13px;font-weight:600">${escapeHtml(path.split('/').pop())}</div>
                        <div style="color:var(--faint);font-size:11px;margin-top:4px">${ext.toUpperCase()} 파일 — 직접 열기 불가</div>
                        <button class="admin-action" style="margin-top:12px;font-size:12px"
                            onclick="sendArchiveToChat('${path.replace(/'/g,"\\'")}','archive')">
                            <i class='ti ti-send'></i> AI에게 보내기 (내용 분석 요청)
                        </button>
                    </div>`;
                return;
            }

            // 기타 바이너리: 차단
            if (BINARY_EXTS.has(ext)) {
                resultEl.innerHTML = `<div class="sensitivity-preview">⚠️ 바이너리 파일은 열 수 없습니다.<br><span style="color:var(--faint);font-size:11px">${escapeHtml(path)}</span></div>`;
                return;
            }

            resultEl.innerHTML = '<div class="sensitivity-preview">읽는 중...</div>';

            try {
                const res = await apiFetch('/local/read', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path, approved: true, approval_token: readApprovalToken })
                });
                const data = await res.json();
                if (!res.ok || data.error) throw new Error(data.error || data.detail || '오류');
                const content = (data.result ?? data).content ?? '';
                // 에디터 열기
                _editorCurrentPath = path;
                document.getElementById('editor-filepath').textContent = path;
                document.getElementById('file-editor-content').value = content;
                document.getElementById('editor-status').textContent = '';
                document.getElementById('local-browser-overlay').style.display = 'none';
                document.getElementById('file-editor-overlay').style.display = 'flex';
            } catch(e) {
                resultEl.innerHTML = `<div class="sensitivity-preview">${escapeHtml(e.message)}</div>`;
            }
        }

        function closeFileEditor() {
            document.getElementById('file-editor-overlay').style.display = 'none';
        }

        async function saveLocalFile() {
            const path = _editorCurrentPath;
            const content = document.getElementById('file-editor-content').value;
            const statusEl = document.getElementById('editor-status');
            statusEl.style.color = 'var(--accent)';
            statusEl.textContent = '저장 중...';

            const approvalToken = await getLocalApprovalToken(path, 'write', content);
            if (!approvalToken) {
                statusEl.style.color = 'var(--danger)';
                statusEl.textContent = '저장 취소됨';
                return;
            }

            try {
                const res = await apiFetch('/local/write', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path, content, approved: true, approval_token: approvalToken })
                });
                const data = await res.json();
                if (!res.ok || data.error) throw new Error(data.error || data.detail || '오류');
                statusEl.style.color = 'var(--accent)';
                statusEl.textContent = '✓ 저장 완료';
                setTimeout(() => { statusEl.textContent = ''; }, 2500);
            } catch(e) {
                statusEl.style.color = 'var(--danger)';
                statusEl.textContent = '저장 실패: ' + e.message;
            }
        }

        async function sendPdfToAI(path) {
            const statusEl = document.getElementById('pdf-send-status');
            if (statusEl) statusEl.textContent = '페이지 렌더링 중...';
            try {
                const approvalToken = await getLocalApprovalToken(path, 'read');
                if (!approvalToken) throw new Error('파일 접근이 거부되었습니다.');
                // 1. 페이지 이미지 가져오기
                const res = await apiFetch('/tools/pdf_pages?path=' + encodeURIComponent(path) + '&approval_token=' + encodeURIComponent(approvalToken));
                const data = await res.json();
                const pages = data.pages || [];

                // 2. 텍스트도 추출
                const textRes = await apiFetch('/tools/read_document', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path, approval_token: approvalToken })
                });
                const textData = await textRes.json();
                const text = (textData.result ?? textData).content ?? '';

                closeLocalBrowser();

                if (pages.length > 0) {
                    // 첫 페이지 이미지를 채팅 첨부로 설정
                    const firstPage = pages[0];
                    const blob = await (await fetch(`data:image/png;base64,${firstPage.b64}`)).blob();
                    setImagePreviewFromBlob(blob);
                }

                // 텍스트 + 페이지 수 안내 메시지
                const name = path.split('/').pop();
                const pageInfo = pages.length > 0 ? ` (${data.total}페이지, 첫 페이지 이미지 첨부됨)` : '';
                userInput.value = `다음 PDF 문서를 분석해줘: ${name}${pageInfo}\n\n[추출된 텍스트]\n${text.slice(0, 3000)}`;
                userInput.focus();
            } catch(e) {
                if (statusEl) { statusEl.style.color = 'var(--danger)'; statusEl.textContent = '실패: ' + e.message; }
            }
        }

        async function openPdfTextEditor(path) {
            const resultEl = document.getElementById('local-browser-result');
            resultEl.innerHTML = '<div class="sensitivity-preview">텍스트 추출 중...</div>';
            try {
                const approvalToken = await getLocalApprovalToken(path, 'read');
                if (!approvalToken) throw new Error('파일 접근이 거부되었습니다.');
                const res = await apiFetch('/tools/read_document', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path, approval_token: approvalToken })
                });
                const data = await res.json();
                const text = (data.result ?? data).content ?? '';
                _editorCurrentPath = path;
                document.getElementById('editor-filepath').textContent = path + '  [텍스트 추출]';
                document.getElementById('file-editor-content').value = text;
                document.getElementById('editor-status').textContent = '⚠️ 이미지/표 등 비텍스트 요소는 표시되지 않을 수 있습니다';
                document.getElementById('editor-status').style.color = 'var(--accent-2)';
                document.getElementById('local-browser-overlay').style.display = 'none';
                document.getElementById('file-editor-overlay').style.display = 'flex';
            } catch(e) {
                resultEl.innerHTML = `<div class="sensitivity-preview">텍스트 추출 실패: ${escapeHtml(e.message)}</div>`;
            }
        }

        function sendArchiveToChat(path, type) {
            closeLocalBrowser();
            const name = path.split('/').pop();
            const prompts = {
                archive: `${path} 파일의 내용을 분석해줘. (압축 파일이면 내부 목록을 보여주고, 필요하면 압축 해제해줘)`,
                video:   `${path} 영상 파일에 대해 알 수 있는 정보를 알려줘.`,
                document:`${path} 문서를 읽고 요약해줘.`,
            };
            userInput.value = prompts[type] || `${path} 파일을 분석해줘.`;
            userInput.focus();
        }

        async function sendImageFileToChat(path) {
            try {
                const approvalToken = await getLocalApprovalToken(path, 'read');
                if (!approvalToken) throw new Error('파일 접근이 거부되었습니다.');
                const res = await apiFetch('/local/serve?path=' + encodeURIComponent(path) + '&approval_token=' + encodeURIComponent(approvalToken));
                if (!res.ok) throw new Error('이미지 로드 실패');
                const blob = await res.blob();
                closeLocalBrowser();
                setImagePreviewFromBlob(blob);
                userInput.value = `이 이미지를 분석해줘: ${path.split('/').pop()}`;
                userInput.focus();
            } catch(e) {
                alert('이미지를 불러올 수 없습니다: ' + e.message);
            }
        }

        function sendFileToChat() {
            const content = document.getElementById('file-editor-content').value;
            const name = _editorCurrentPath.split('/').pop();
            closeFileEditor();
            userInput.value = `다음 파일(${name}) 내용을 분석하거나 수정해줘:\n\n\`\`\`\n${content.slice(0, 4000)}\n\`\`\``;
            userInput.focus();
        }

        function renderAdminStats(summary) {
            const stats = [
                ['전체 사용자', summary.total_users],
                ['활성 사용자', summary.active_users],
                ['관리자', summary.admin_users],
                ['전체 메시지', summary.total_messages],
                ['사용자 메시지', summary.user_messages],
                ['AI 응답', summary.assistant_messages],
                ['최근 로그', summary.last_message_at ? new Date(summary.last_message_at).toLocaleString() : '-']
            ];
            document.getElementById('admin-stats').innerHTML = stats.map(([label, value]) => `
                <div class="admin-stat">
                    <span>${escapeHtml(label)}</span>
                    <strong>${escapeHtml(value)}</strong>
                </div>
            `).join('');
        }

        function renderAdminUsers(users) {
            document.getElementById('admin-users').innerHTML = `
                <table class="admin-table">
                    <thead>
                        <tr>
                            <th>이메일</th>
                            <th>이름</th>
                            <th>별명</th>
                            <th>권한</th>
                            <th>상태</th>
                            <th>관리</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${users.map(user => `
                            <tr>
                                <td>${escapeHtml(user.email)}</td>
                                <td>${escapeHtml(user.name || '-')}</td>
                                <td>${escapeHtml(user.nickname || '-')}</td>
                                <td><span class="role-badge">${escapeHtml(user.role)}</span></td>
                                <td>${user.disabled ? '비활성' : '활성'}</td>
                                <td>
                                    <div class="admin-actions">
                                        <button class="admin-action" onclick="setUserRole('${encodeURIComponent(user.email)}', '${user.role === 'admin' ? 'user' : 'admin'}')">
                                            ${user.role === 'admin' ? '권한 해제' : '관리자 지정'}
                                        </button>
                                        <button class="admin-action" onclick="setUserDisabled('${encodeURIComponent(user.email)}', ${!user.disabled})">
                                            ${user.disabled ? '활성화' : '비활성화'}
                                        </button>
                                        <button class="admin-action danger" onclick="deleteUser('${encodeURIComponent(user.email)}')">삭제</button>
                                    </div>
                                </td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            `;
        }

        function renderSensitivityReport(report) {
            const summary = report.summary || {};
            const severity = summary.severity_counts || {};
            const fields = summary.field_counts || {};
            const users = summary.user_counts || {};
            const fieldTags = Object.entries(fields).map(([label, count]) => `
                <span class="sensitivity-tag medium">${escapeHtml(label)} ${escapeHtml(count)}</span>
            `).join('');
            const userTags = Object.entries(users).map(([label, count]) => `
                <span class="sensitivity-tag high">${escapeHtml(label)} ${escapeHtml(count)}</span>
            `).join('');
            document.getElementById('sensitivity-summary').innerHTML = `
                <span class="sensitivity-tag high">위험 ${escapeHtml(summary.risky_messages || 0)}</span>
                <span class="sensitivity-tag low">준수 ${escapeHtml(summary.compliant_messages || 0)}</span>
                <span class="sensitivity-tag medium">위험률 ${escapeHtml(summary.risk_rate || 0)}%</span>
                <span class="sensitivity-tag high">높음 ${escapeHtml(severity.high || 0)}</span>
                <span class="sensitivity-tag medium">중간 ${escapeHtml(severity.medium || 0)}</span>
                <span class="sensitivity-tag low">낮음 ${escapeHtml(severity.low || 0)}</span>
                ${fieldTags}
                ${userTags}
            `;
            document.getElementById('risk-fields').innerHTML = renderSensitivityItems(report.risk_fields || [], true);
            document.getElementById('compliance-fields').innerHTML = renderSensitivityItems(report.compliance_fields || [], false);
        }

        function renderSensitivityItems(items, risky) {
            if (!items.length) {
                return `<div class="sensitivity-item"><div class="sensitivity-preview">${risky ? '감지된 위험 필드가 없습니다.' : '준수 항목이 없습니다.'}</div></div>`;
            }
            return items.slice().reverse().map(item => {
                const labels = risky ? item.labels : item.compliance_fields;
                const labelTags = (labels || []).map(label => `
                    <span class="sensitivity-tag ${item.sensitivity || 'low'}">${escapeHtml(label)}</span>
                `).join('');
                return `
                    <div class="sensitivity-item">
                        <div class="sensitivity-meta">
                            <span class="sensitivity-tag">${escapeHtml(item.user_nickname || 'Unknown')}</span>
                            <span class="sensitivity-tag">${escapeHtml(item.user_email || '사용자 미기록')}</span>
                            <span class="sensitivity-tag">${escapeHtml(item.role || '-')}</span>
                            <span class="sensitivity-tag ${item.sensitivity || 'low'}">${escapeHtml(item.sensitivity || 'none')}</span>
                            ${labelTags}
                        </div>
                        <div class="sensitivity-preview">${escapeHtml(item.preview || '')}</div>
                    </div>
                `;
            }).join('');
        }

        async function loadAdminDashboard() {
            try {
                const [summaryRes, usersRes, sensitivityRes] = await Promise.all([
                    apiFetch('/admin/summary', { headers: adminHeaders() }),
                    apiFetch('/admin/users', { headers: adminHeaders() }),
                    apiFetch('/admin/sensitivity', { headers: adminHeaders() })
                ]);
                if (!summaryRes.ok || !usersRes.ok || !sensitivityRes.ok) throw new Error('관리자 정보를 불러오지 못했습니다.');
                renderAdminStats(await summaryRes.json());
                renderSensitivityReport(await sensitivityRes.json());
                renderAdminUsers(await usersRes.json());
                fillVpcForm(latestVpcConfig);
            } catch (e) {
                document.getElementById('admin-users').innerHTML = `<p style="color: var(--danger);">${escapeHtml(e.message)}</p>`;
            }
        }

        async function setUserRole(encodedEmail, role) {
            await apiFetch(`/admin/users/${encodedEmail}`, {
                method: 'PATCH',
                headers: adminHeaders(),
                body: JSON.stringify({ role })
            });
            await loadAdminDashboard();
        }

        async function setUserDisabled(encodedEmail, disabled) {
            await apiFetch(`/admin/users/${encodedEmail}`, {
                method: 'PATCH',
                headers: adminHeaders(),
                body: JSON.stringify({ disabled })
            });
            await loadAdminDashboard();
        }

        async function deleteUser(encodedEmail) {
            if (!confirm('이 사용자를 삭제할까요?')) return;
            await apiFetch(`/admin/users/${encodedEmail}`, {
                method: 'DELETE',
                headers: adminHeaders()
            });
            await loadAdminDashboard();
        }

        userInput.addEventListener('input', function () {
            this.style.height = 'auto';
            this.style.height = (this.scrollHeight) + 'px';
        });

        function previewImage(input) {
            if (input.files && input.files[0]) {
                const reader = new FileReader();
                reader.onload = (e) => {
                    const dataUrl = e.target?.result || '';
                    if (typeof dataUrl !== 'string' || !dataUrl.includes(',')) return;
                    const mimeMatch = dataUrl.match(/^data:(image\/[a-zA-Z0-9.+-]+);base64,/);
                    currentImageMime = (mimeMatch && mimeMatch[1]) ? mimeMatch[1] : 'image/png';
                    document.getElementById('img-preview').src = dataUrl;
                    document.getElementById('preview-area').style.display = 'flex';
                    currentImageData = dataUrl.split(',')[1];
                };
                reader.readAsDataURL(input.files[0]);
            }
        }

        function removeImage() {
            document.getElementById('image-input').value = "";
            document.getElementById('preview-area').style.display = 'none';
            currentImageData = null;
            currentImageMime = 'image/png';
        }

        // 코드 데이터를 안전하게 보관할 저장소
        const codeRegistry = {};
        let codeCounter = 0;

        function escapeHtml(value) {
            return String(value)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#039;');
        }

        function genericProcessingError() {
            return currentLang === 'ko'
                ? '처리 중 문제가 발생했습니다. 다시 시도해 주세요.'
                : 'Something went wrong while processing. Please try again.';
        }

        const INTERNAL_LOG_PATTERNS = [
            /최대\s*재시도/i,
            /마지막\s*비판/i,
            /\bcritic\b/i,
            /\bretry\b/i,
            /\bTODO\b/i,
            /final action/i,
            /JSON\s*파싱/i,
            /작업\s*목록/i,
            /내부\s*실행\s*상태/i,
            /agent\s*log/i,
            /state_history/i,
            /transcript/i,
            /Traceback/i,
            /No model loaded/i,
            /Call \/models\/load/i,
        ];

        function sanitizeAssistantText(text) {
            const raw = String(text || '');
            if (INTERNAL_LOG_PATTERNS.some(pattern => pattern.test(raw))) {
                return genericProcessingError();
            }
            return raw;
        }

        function safeErrorMessage(err) {
            const raw = err?.message || String(err || '');
            if (!raw || INTERNAL_LOG_PATTERNS.some(pattern => pattern.test(raw))) return genericProcessingError();
            if (/서버 오류|server error|failed|error|오류/i.test(raw)) return genericProcessingError();
            return raw;
        }

        // Marked.js 설정: 코드 블록에 다운로드 버튼 추가
        const renderer = new marked.Renderer();
        renderer.code = function (code, lang) {
            const rawCode = typeof code === 'object' ? code.text : code;
            const codeLang = typeof code === 'object' ? code.lang : lang;
            const codeId = `code-ref-${codeCounter++}`;
            codeRegistry[codeId] = rawCode; // 저장소에 보관

            const safeExt = String(codeLang || 'txt').replace(/[^a-zA-Z0-9_-]/g, '') || 'txt';
            const fileName = `code.${safeExt}`;

            return `
                <div class="code-container">
                    <div class="code-header">
                        <span class="code-lang">${escapeHtml(codeLang || 'text')}</span>
                        <div class="code-actions">
                            <button onclick="copyCode('${codeId}', this)" class="copy-btn-ui">
                                <i class="ti ti-copy" style="font-size:12px;"></i> Copy
                            </button>
                            <button onclick="downloadCode('${codeId}', '${fileName}')" class="download-btn-ui">
                                <i class="ti ti-download" style="font-size:12px;"></i> Download
                            </button>
                        </div>
                    </div>
                    <pre><code>${escapeHtml(rawCode)}</code></pre>
                </div>
            `;
        };
        marked.setOptions({ renderer, breaks: true });

        async function copyCode(codeId, button) {
            const code = codeRegistry[codeId];
            if (!code) return;
            try {
                await navigator.clipboard.writeText(code);
                const original = button.innerHTML;
                button.innerHTML = '<i class="ti ti-check" style="font-size:12px;"></i> Copied';
                setTimeout(() => { button.innerHTML = original; }, 1200);
            } catch (e) {
                alert('복사에 실패했습니다.');
            }
        }

        // 수리된 다운로드 함수
        function downloadCode(codeId, fileName) {
            const code = codeRegistry[codeId];
            if (!code) return;

            // 1. VS Code 환경인 경우 확장 프로그램으로 전달
            if (vscode) {
                vscode.postMessage({
                    type: 'download',
                    fileName: fileName,
                    content: code
                });
                return;
            }

            // 2. 일반 브라우저 환경
            const blob = new Blob([code], { type: 'text/plain' });
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = fileName;
            document.body.appendChild(a); // 반드시 추가되어야 함
            a.click(); // 실제 클릭 발생
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
        }

        function addMessage(role, text, imageData = null, senderLabel = null) {
            if (emptyState) emptyState.style.display = 'none';
            const msgDiv = document.createElement('div');
            msgDiv.className = `message ${role}`;
            const senderName = senderLabel || (role === 'user' ? currentUserNickname : "Lattice AI");
            const displayText = role === 'ai' ? sanitizeAssistantText(text) : text;

            let formattedContent = "";
            if (role === 'ai') {
                formattedContent = marked.parse(displayText);
            } else {
                formattedContent = escapeHtml(displayText).replace(/\n/g, '<br>');
            }

            let content = `
                <div class="sender-label">${senderName}</div>
                <div class="bubble">${formattedContent}</div>
            `;
            if (imageData && role === 'user') {
                content = `<img src="data:${currentImageMime};base64,${imageData}" class="img-attachment">` + content;
            }
            msgDiv.innerHTML = content;
            chatViewport.appendChild(msgDiv);
            chatViewport.scrollTop = chatViewport.scrollHeight;
        }

        function renderAiBubble(bubble, text) {
            bubble.innerHTML = marked.parse(sanitizeAssistantText(text) || '');
            chatViewport.scrollTop = chatViewport.scrollHeight;
        }

        function mcpStatusLabel(item) {
            if (item.status === 'needs_auth') return '인증 필요';
            if (item.installed) return '활성';
            return '설치';
        }

        function renderMcpRecommendations(items) {
            if (!items || !items.length) return null;
            if (emptyState) emptyState.style.display = 'none';

            const wrap = document.createElement('div');
            wrap.className = 'mcp-recommend-wrap';

            const dropdownId = `mcp-drop-${Date.now()}`;
            wrap.innerHTML = `
                <button class="mcp-recommend-btn" onclick="toggleMcpDropdown('${dropdownId}', this)">
                    <i class="ti ti-puzzle"></i>
                    MCP(tool) 추천
                    <span class="mcp-count-badge">${items.length}</span>
                    <i class="ti ti-chevron-down mcp-chevron"></i>
                </button>
                <div class="mcp-dropdown" id="${dropdownId}">
                    ${items.map(item => `
                        <div class="mcp-dropdown-item">
                            <div class="mcp-dropdown-item-info">
                                <strong>${escapeHtml(item.name)}</strong>
                                <span>${escapeHtml(item.description)}</span>
                            </div>
                            <button class="mcp-install-btn" onclick="installMcp('${encodeURIComponent(item.id)}', this)">
                                ${escapeHtml(mcpStatusLabel(item))}
                            </button>
                        </div>
                    `).join('')}
                </div>
            `;
            chatViewport.appendChild(wrap);
            chatViewport.scrollTop = chatViewport.scrollHeight;
            return wrap;
        }

        function toggleMcpDropdown(id, btn) {
            const dropdown = document.getElementById(id);
            const isOpen = dropdown.classList.toggle('open');
            btn.classList.toggle('open', isOpen);
        }

        async function recommendMcpForPrompt(text) {
            try {
                const res = await apiFetch('/mcp/recommend', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ query: text, limit: 4 })
                });
                if (!res.ok) return;
                const data = await res.json();
                renderMcpRecommendations(data.recommendations || []);
            } catch (e) { }
        }

        async function installMcp(encodedId, button) {
            const mcpId = decodeURIComponent(encodedId);
            const original = button.innerHTML;
            button.innerHTML = '설치 중';
            button.disabled = true;
            try {
                const res = await apiFetch('/mcp/install', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ mcp_id: mcpId })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || 'MCP 설치에 실패했습니다.');
                button.innerHTML = data.status === 'needs_auth' ? '인증 필요' : '활성화됨';
                const connectorLink = data.connector_url
                    ? `<br><a href="${escapeHtml(data.connector_url)}" target="_blank">커넥터 설정 열기</a>`
                    : '';
                addMessage('ai', `<b>${escapeHtml(data.name)}</b> ${escapeHtml(data.message || 'MCP가 활성화되었습니다.')}${connectorLink}`);
            } catch (e) {
                button.innerHTML = original;
                button.disabled = false;
                addMessage('ai', e.message || 'MCP 설치에 실패했습니다.');
            }
        }

        function clearChatViewport() {
            chatViewport.querySelectorAll('.message, .mcp-recommend-wrap, .file-card').forEach(el => el.remove());
            if (emptyState) emptyState.style.display = 'block';
            chatViewport.scrollTop = 0;
        }

        function startNewChat() {
            // 채팅 뷰로 전환
            const layout = document.querySelector('.app-layout');
            if (layout) layout.dataset.view = 'chat';
            markActiveNav('chat');
            currentConversationId = createConversationId();
            localStorage.setItem(CONVERSATION_KEY, currentConversationId);
            localStorage.setItem(CONVERSATION_STARTED_KEY, new Date().toISOString());
            clearChatViewport();
            loadHistory();
            userInput.focus();
        }

        async function clearCurrentConversation() {
            const startedAt = encodeURIComponent(localStorage.getItem(CONVERSATION_STARTED_KEY) || '');
            const res = await apiFetch(`/history/conversations/${encodeURIComponent(currentConversationId)}?started_at=${startedAt}`, {
                method: 'DELETE'
            });
            if (!res.ok) throw new Error('현재 대화방 기록을 지우지 못했습니다.');
            localStorage.setItem(CONVERSATION_STARTED_KEY, new Date().toISOString());
            clearChatViewport();
            await loadHistory();
            userInput.focus();
        }

        async function clearAllConversations() {
            const res = await apiFetch('/history', { method: 'DELETE' });
            if (!res.ok) throw new Error('전체 대화 기록을 지우지 못했습니다.');
            currentConversationId = createConversationId();
            localStorage.setItem(CONVERSATION_KEY, currentConversationId);
            localStorage.setItem(CONVERSATION_STARTED_KEY, new Date().toISOString());
            window.__legacyConversationMessages = {};
            mirroredHistoryKeys.clear();
            clearChatViewport();
            await loadHistory();
            userInput.focus();
        }

        async function handleSlashCommand(text) {
            const command = text.trim().toLowerCase();
            if (command === '/clear') {
                await clearCurrentConversation();
                return true;
            }
            if (command === '/clear_all') {
                await clearAllConversations();
                return true;
            }
            return false;
        }

        function titleFromHistoryItem(item) {
            const content = String(item?.content || '').replace(/\s+/g, ' ').trim();
            return content.slice(0, 48) || t('new_conversation');
        }

        function groupLegacyHistory(history) {
            const conversations = [];
            const messagesById = {};
            const currentStartedAt = localStorage.getItem(CONVERSATION_STARTED_KEY) || '';
            (history || []).forEach((item, index) => {
                let id = item.conversation_id;
                if (!id) {
                    const timestamp = item.timestamp || '';
                    id = timestamp >= currentStartedAt ? currentConversationId : 'legacy-previous-history';
                }
                if (!messagesById[id]) {
                    messagesById[id] = [];
                    conversations.push({
                        id,
                        title: id === 'legacy-previous-history' ? t('previous_history') : titleFromHistoryItem(item),
                        updated_at: item.timestamp || '',
                        message_count: 0
                    });
                }
                messagesById[id].push(item);
                const conv = conversations.find(entry => entry.id === id);
                conv.updated_at = item.timestamp || conv.updated_at;
                conv.message_count += 1;
                if (item.role === 'user' && id !== 'legacy-previous-history') conv.title = titleFromHistoryItem(item);
            });
            window.__legacyConversationMessages = messagesById;
            return conversations.sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)));
        }

        async function fetchConversations() {
            const groupedRes = await apiFetch('/history/conversations');
            if (groupedRes.ok) return groupedRes.json();

            const rawRes = await apiFetch('/history');
            if (!rawRes.ok) return [];
            return groupLegacyHistory(await rawRes.json());
        }

        async function openConversation(conversationId) {
            closeSidebar();
            try {
                let data = null;
                const res = await apiFetch(`/history/conversations/${encodeURIComponent(conversationId)}`);
                if (res.ok) {
                    data = await res.json();
                } else if (window.__legacyConversationMessages?.[conversationId]) {
                    data = { id: conversationId, messages: window.__legacyConversationMessages[conversationId] };
                } else {
                    const rawRes = await apiFetch('/history');
                    if (rawRes.ok) {
                        groupLegacyHistory(await rawRes.json());
                        data = { id: conversationId, messages: window.__legacyConversationMessages?.[conversationId] || [] };
                    }
                }
                if (!data || !data.messages?.length) throw new Error('대화를 불러오지 못했습니다.');
                currentConversationId = data.id;
                localStorage.setItem(CONVERSATION_KEY, currentConversationId);
                clearChatViewport();
                const messages = data.messages || [];
                messages.forEach(item => {
                    const role = item.role === 'assistant' ? 'ai' : 'user';
                    const sender = item.role === 'assistant'
                        ? 'Lattice AI'
                        : (item.user_nickname || currentUserNickname || 'User');
                    addMessage(role, item.content || '', null, sender);
                });
                if (!messages.length && emptyState) emptyState.style.display = 'block';
                loadHistory();
            } catch (e) {
                addMessage('ai', e.message || '이전 대화를 불러오지 못했습니다.');
            }
        }

        function renderHistoryItems(conversations) {
            if (!conversations.length) {
                historyContainer.innerHTML = `<div class="history-section-label">${t('history_section').toUpperCase()}</div><div class="history-empty">${t('history_empty')}</div>`;
                return;
            }
            historyContainer.innerHTML = `<div class="history-section-label">${t('history_section').toUpperCase()}</div>` + conversations.map(item => `
                <div class="history-item ${item.id === currentConversationId ? 'active' : ''}" data-conversation-id="${escapeHtml(item.id)}" title="${escapeHtml(item.title || '')}">
                    <i class="ti ti-message-2"></i>
                    <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis">${escapeHtml(item.title || t('new_conversation'))}</span>
                    <span class="history-item-del" onclick="event.stopPropagation();deleteConversation('${escapeHtml(item.id)}')"><i class="ti ti-trash"></i></span>
                </div>
            `).join('');
            historyContainer.querySelectorAll('.history-item').forEach(item => {
                item.onclick = () => openConversation(item.dataset.conversationId);
            });
        }

        async function loadHistory() {
            try {
                const conversations = await fetchConversations();
                renderHistoryItems(conversations);
                return conversations;
            } catch (e) { }
            return [];
        }

        let _searchDebounce = null;
        async function onHistorySearch(q) {
            clearTimeout(_searchDebounce);
            if (!q.trim()) { loadHistory(); return; }
            _searchDebounce = setTimeout(async () => {
                try {
                    const res = await apiFetch(`/history/search?q=${encodeURIComponent(q)}`);
                    if (!res.ok) return;
                    const data = await res.json();
                    const results = (data.results || []).map(r => ({
                        id: r.conversation_id,
                        title: r.title || t('new_conversation'),
                    }));
                    renderHistoryItems(results);
                } catch {}
            }, 300);
        }

        async function deleteConversation(conversationId) {
            if (!confirm(t('confirm_delete_chat'))) return;
            try {
                await apiFetch(`/history/conversations/${encodeURIComponent(conversationId)}`, { method: 'DELETE' });
                if (currentConversationId === conversationId) startNewChat();
                loadHistory();
            } catch {}
        }

        async function restoreCurrentConversation() {
            const urlParams = new URLSearchParams(window.location.search);
            const openId = urlParams.get('open_conversation');
            if (openId) {
                history.replaceState(null, '', window.location.pathname);
                currentConversationId = openId;
                localStorage.setItem(CONVERSATION_KEY, currentConversationId);
                await openConversation(currentConversationId);
                return;
            }
            await loadHistory();
        }

        async function syncTelegramHistory() {
            try {
                const res = await apiFetch('/history');
                if (!res.ok) return;
                const history = await res.json();
                for (const item of history) {
                    if (item.source !== 'telegram') continue;
                    const key = `${item.timestamp || ''}:${item.role}:${item.content}`;
                    if (mirroredHistoryKeys.has(key)) continue;
                    mirroredHistoryKeys.add(key);
                    const role = item.role === 'assistant' ? 'ai' : 'user';
                    const sender = item.role === 'assistant'
                        ? 'Lattice AI'
                        : (item.user_nickname || 'Telegram');
                    addMessage(role, item.content || '', null, sender);
                }
                loadHistory();
            } catch (e) { }
        }

        async function sendToAgent(text, extraCtx = '') {
            sendBtn.disabled = true;
            const aiMsgDiv = document.createElement('div');
            aiMsgDiv.className = 'message ai';
            const modeLabel = pipelineActive
                ? '⚙ 파이프라인 모드'
                : '⚙ 에이전트 모드';
            aiMsgDiv.innerHTML = `<div class="sender-label">Lattice AI <span style="color:var(--accent);font-size:11px">${modeLabel}</span></div><div class="bubble">${pipelineActive ? '📋 계획 수립 중입니다...' : '파일을 생성하고 있습니다...'}</div>`;
            chatViewport.appendChild(aiMsgDiv);
            chatViewport.scrollTop = chatViewport.scrollHeight;
            const bubble = aiMsgDiv.querySelector('.bubble');

            try {
                const agentMsg = extraCtx ? `${extraCtx}\n\n${text}` : text;
                const res = await apiFetch('/agent', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        message: agentMsg,
                        conversation_id: currentConversationId,
                        max_steps: 4,
                        temperature: 0.1,
                        user_email: currentUserEmail,
                        user_nickname: currentUserNickname,
                        human_in_loop: pipelineActive,
                        planning_model:  pipelineActive ? (pipelineConfig.planning  || null) : null,
                        executing_model: pipelineActive ? (pipelineConfig.executing || null) : null,
                        reviewing_model: pipelineActive ? (pipelineConfig.reviewing || null) : null,
                    })
                });

                let data;
                try { data = await res.json(); } catch { data = {}; }

                if (!res.ok) throw new Error(data.detail || `서버 오류 (${res.status})`);

                // Pipeline mode: show plan for approval
                if (data.status === 'waiting_approval') {
                    await renderPlanApprovalCard(bubble, data);
                    loadHistory();
                    return;
                }

                renderAiBubble(bubble, data.response || '완료되었습니다.');

                const files = data.created_files || [];
                files.forEach(f => renderFileDownloadCard(f.filename, f.path, f.bytes || 0));

                loadHistory();
            } catch(e) {
                bubble.innerHTML = `<span style="color:#e05c5c">${escapeHtml(safeErrorMessage(e))}</span>`;
            } finally {
                sendBtn.disabled = false;
            }
        }

        // ── 문서 첨부 ──────────────────────────────────────────────
        let attachedDocFile = null;
        let attachedDocContent = null; // extracted text from uploaded doc

        function attachDocument(input) {
            const file = input.files[0];
            if (!file) return;
            attachedDocFile = file;
            attachedDocContent = null;
            const row = document.getElementById('attach-preview-row');
            row.style.display = 'flex';
            row.innerHTML = `
                <div class="attach-chip">
                    <i class="ti ti-file-text"></i>
                    <span>${escapeHtml(file.name)}</span>
                    <button onclick="removeAttachedDoc()" title="제거">×</button>
                </div>
                <span style="font-size:11px;color:var(--muted);align-self:center">첨부됨 — 전송 시 AI가 파일을 읽습니다</span>`;
            input.value = '';
        }

        function removeAttachedDoc() {
            attachedDocFile = null;
            attachedDocContent = null;
            const row = document.getElementById('attach-preview-row');
            row.style.display = 'none';
            row.innerHTML = '';
        }

        async function uploadAttachedDoc(file) {
            const form = new FormData();
            form.append('file', file);
            const qs = currentConversationId ? `?conversation_id=${encodeURIComponent(currentConversationId)}` : '';
            const res = await apiFetch(`/upload/document${qs}`, { method: 'POST', body: form });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || '파일 업로드 실패');
            }
            return res.json();
        }

        // ── 언어 감지 (클라이언트) ─────────────────────────────────
        function detectLang(text) {
            let ko = 0, zh = 0;
            for (const c of text) {
                const cp = c.codePointAt(0);
                if (cp >= 0xAC00 && cp <= 0xD7A3) ko++;
                else if (cp >= 0x4E00 && cp <= 0x9FFF) zh++;
            }
            const total = Math.max(text.length, 1);
            if (ko / total > 0.05) return 'ko';
            if (zh / total > 0.05) return 'zh';
            return 'en';
        }

        function langHint(lang) {
            if (lang === 'ko') return '사용자가 한국어로 대화하고 있습니다. 한국어로 답변하세요.';
            if (lang === 'zh') return '用户正在使用中文交流，请用中文回答。';
            return 'The user is conversing in English. Respond in English.';
        }

        const FILE_KEYWORDS = [
            'docx','xlsx','pptx','pdf','파일 만들','문서 만들','만들어줘','만들어 줘',
            'word','excel','powerpoint','ppt','피피티','엑셀','스프레드시트','프레젠테이션',
            '보고서 만들','기획서','제안서','이력서','계약서','파일 생성','문서 생성',
        ];
        const PROJECT_BUILD_KEYWORDS = [
            '프로젝트', '앱 만들', '웹앱', '웹 app', 'react', 'vite', 'next.js', 'nextjs',
            'vue', 'svelte', 'frontend', 'backend', '서버 만들', 'api 만들', '코드 작성',
            '개발해', '구현해', 'scaffold', 'boilerplate', 'build', 'compile', 'typecheck',
            '테스트 돌려', 'npm run build', '빌드해', '배포해', 'deploy',
            'installer', 'install file', '.pkg', '.exe', 'pkg', 'exe', 'electron', 'electron-builder',
            '설치파일', '설치 파일', '실행파일', '패키징'
        ];
        const DATA_ANALYSIS_KEYWORDS = [
            '데이터 분석', '분석해', '통계', '인사이트', '추세', '리포트', '요약표',
            'csv', 'xlsx', 'tsv', '매출 데이터', '로그 분석', 'pivot', '회귀', '상관관계',
            'data analysis', 'analyze', 'insight', 'trend', 'statistics', 'dataset'
        ];

        function _isFileRequest(text) {
            const t = text.toLowerCase();
            return FILE_KEYWORDS.some(k => t.includes(k));
        }

        function _isProjectOrBuildRequest(text) {
            const t = text.toLowerCase();
            return PROJECT_BUILD_KEYWORDS.some(k => t.includes(k));
        }

        function _isDataAnalysisRequest(text) {
            const t = text.toLowerCase();
            return DATA_ANALYSIS_KEYWORDS.some(k => t.includes(k));
        }

        function _isComputerUseRequest(text) {
            const t = text.toLowerCase();
            const controlTargets = [
                'computer use', 'desktop', 'screen', 'chrome', 'safari', 'browser',
                '컴퓨터', '데스크탑', '화면', '크롬', '사파리', '브라우저'
            ];
            const controlVerbs = [
                'click', 'type', 'scroll', 'open', 'launch', 'press', 'drag',
                '클릭', '타이핑', '스크롤', '열어', '켜', '실행', '눌러', '드래그'
            ];
            const hasTarget = controlTargets.some(k => t.includes(k));
            const hasVerb = controlVerbs.some(k => t.includes(k));
            return hasTarget && hasVerb;
        }

        function _isFileOrProjectRequest(text, hasDocAttachment = false) {
            if (hasDocAttachment) return true;
            return _isFileRequest(text) || _isProjectOrBuildRequest(text);
        }

        function _isNetworkStatusRequest(text) {
            const t = text.toLowerCase();
            const hasIp = /(^|[^a-z0-9])ip([^a-z0-9]|$)|아이피|ip\s*주소|아이피\s*주소|ipconfig|ifconfig|네트워크/.test(t);
            const asksCurrent = ['내', '현재', '지금', 'local', '로컬', '주소', 'address', '뭐', '알려', '확인'].some(k => t.includes(k));
            return hasIp && asksCurrent;
        }

        function _isCurrentUrlRequest(text) {
            const t = text.toLowerCase();
            const hasUrl = ['url', '주소', '링크', 'address'].some(k => t.includes(k));
            const asksCurrent = ['현재', '지금', '여기', '접속', '페이지', '브라우저', '알려', '뭐'].some(k => t.includes(k));
            return hasUrl && asksCurrent;
        }

        async function openPermissionSettings(permissionId) {
            try {
                await apiFetch(`/permissions/open/${encodeURIComponent(permissionId)}`, { method: 'POST' });
            } catch (e) {}
        }

        function renderPermissionHelp(message, permissionId = 'accessibility') {
            const label = permissionId === 'automation' ? '자동화 권한 설정 열기'
                : permissionId === 'screen' ? '화면 기록 권한 설정 열기'
                : '손쉬운 사용 권한 설정 열기';
            addMessage('ai', `${escapeHtml(message)}<br><br><button class="mcp-install-btn" onclick="openPermissionSettings('${permissionId}')">${label}</button>`);
        }

        async function sendCurrentUrl(text) {
            sendBtn.disabled = true;
            const currentUrl = window.location.href;
            addMessage('ai', `현재 페이지 URL: ${escapeHtml(currentUrl)}`);
            sendBtn.disabled = false;
            userInput.focus();
        }

        async function sendNetworkStatus(text) {
            sendBtn.disabled = true;
            const aiMsgDiv = document.createElement('div');
            aiMsgDiv.className = 'message ai';
            aiMsgDiv.innerHTML = `<div class="sender-label">Lattice AI</div><div class="bubble">네트워크 정보를 확인하고 있습니다...</div>`;
            chatViewport.appendChild(aiMsgDiv);
            chatViewport.scrollTop = chatViewport.scrollHeight;
            const bubble = aiMsgDiv.querySelector('.bubble');
            try {
                const res = await apiFetch('/tools/network_status');
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || '네트워크 정보를 확인하지 못했습니다.');
                const info = data.result || {};
                const lines = [
                    `내부 IP: ${info.local_ip || '확인 안 됨'}`,
                    `외부 IP: ${info.public_ip || '확인 안 됨'}`,
                    `호스트명: ${info.hostname || '확인 안 됨'}`
                ];
                if (info.local_ips && Object.keys(info.local_ips).length) {
                    lines.push('', '인터페이스:', ...Object.entries(info.local_ips).map(([name, ip]) => `- ${name}: ${ip}`));
                }
                lines.push('', info.note || '');
                renderAiBubble(bubble, lines.join('\n'));
            } catch (e) {
                renderAiBubble(bubble, e.message || '네트워크 정보를 확인하지 못했습니다.');
            } finally {
                sendBtn.disabled = false;
                userInput.focus();
            }
        }

        async function sendToComputerUse(text) {
            sendBtn.disabled = true;
            const aiMsgDiv = document.createElement('div');
            aiMsgDiv.className = 'message ai';
            aiMsgDiv.innerHTML = `<div class="sender-label">Lattice AI <span style="color:var(--accent);font-size:11px">내 컴퓨터</span></div><div class="bubble">내 컴퓨터 작업을 준비하고 있습니다...</div>`;
            chatViewport.appendChild(aiMsgDiv);
            chatViewport.scrollTop = chatViewport.scrollHeight;
            const bubble = aiMsgDiv.querySelector('.bubble');

            try {
                const resp = await apiFetch('/cu/agent', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        task: text,
                        conversation_id: currentConversationId,
                        max_steps: 15,
                        temperature: 0.1
                    })
                });
                if (!resp.ok) {
                    const data = await resp.json().catch(() => ({}));
                    throw new Error(data.detail || `내 컴퓨터 오류 (${resp.status})`);
                }
                const reader = resp.body.getReader();
                const decoder = new TextDecoder();
                let buf = '';
                let finalMessage = '';
                let failed = false;
                while (true) {
                    const {done, value} = await reader.read();
                    if (done) break;
                    buf += decoder.decode(value, {stream: true});
                    const parts = buf.split('\n\n');
                    buf = parts.pop();
                    for (const part of parts) {
                        const eventLine = part.split('\n').find(l => l.startsWith('event: '));
                        const dataLine = part.split('\n').find(l => l.startsWith('data: '));
                        if (!dataLine) continue;
                        const evtName = eventLine ? eventLine.slice(7) : '';
                        const d = JSON.parse(dataLine.slice(6));
                        if (evtName === 'tool_error' || evtName === 'error') failed = true;
                        else if (evtName === 'final') finalMessage = d.message || '작업을 완료했습니다.';
                    }
                }
                renderAiBubble(bubble, failed && !finalMessage ? genericProcessingError() : (finalMessage || '작업을 완료했습니다.'));
                loadHistory();
            } catch (e) {
                const msg = e.message || '내 컴퓨터 작업에 실패했습니다.';
                if (msg.includes('권한') || msg.toLowerCase().includes('permission') || msg.toLowerCase().includes('not authorized')) {
                    renderAiBubble(bubble, '');
                    renderPermissionHelp(msg, 'accessibility');
                } else {
                    renderAiBubble(bubble, safeErrorMessage(e));
                }
            } finally {
                sendBtn.disabled = false;
                userInput.focus();
            }
        }

        let _isSending = false;
        async function sendMessage() {
            const text = userInput.value.trim();
            if (!text && !currentImageData && !attachedDocFile) return;
            if (_isSending) return;
            if (text && !currentImageData && !attachedDocFile && ['/clear', '/clear_all'].includes(text.toLowerCase())) {
                userInput.value = '';
                userInput.style.height = 'auto';
                sendBtn.disabled = true;
                try {
                    await handleSlashCommand(text);
                } catch (e) {
                    addMessage('ai', e.message || '대화 기록 삭제에 실패했습니다.');
                } finally {
                    sendBtn.disabled = false;
                }
                return;
            }
            addMessage('user', text, currentImageData);
            const capturedImage = currentImageData;
            const capturedDocFile = attachedDocFile;
            userInput.value = ''; removeImage(); removeAttachedDoc();
            userInput.style.height = 'auto';
            _isSending = true;
            sendBtn.disabled = true;

            try {
            if (!capturedImage && !capturedDocFile && text && _isNetworkStatusRequest(text)) {
                await sendNetworkStatus(text);
                return;
            }

            if (!capturedImage && !capturedDocFile && text && _isCurrentUrlRequest(text)) {
                await sendCurrentUrl(text);
                return;
            }

            if (text && getCurrentMode() !== 'default') recommendMcpForPrompt(text);

            if (vscode) {
                vscode.postMessage({ type: 'send', text: text, image_data: capturedImage });
                return;
            }

            // 문서 첨부가 있으면 먼저 업로드해서 내용 추출
            let docContext = '';
            if (capturedDocFile) {
                try {
                    const docData = await uploadAttachedDoc(capturedDocFile);
                    const preview = (docData.content || '').slice(0, 8000);
                    docContext = `\n\n[첨부 파일: ${docData.original_filename}]\n${preview}`;
                } catch(e) {
                    docContext = `\n\n[파일 첨부 실패: ${e.message}]`;
                }
            }

            // 언어 감지
            const lang = detectLang(text);
            const langCtx = langHint(lang);

            const wantsFileOrProject = !capturedImage && text && _isFileOrProjectRequest(text, Boolean(capturedDocFile));
            const wantsDataAnalysis = !capturedImage && text && _isDataAnalysisRequest(text);
            const wantsComputerUse = !capturedImage && !capturedDocFile && text && _isComputerUseRequest(text);

            // 충돌 시 사용자 선택: 파일 생성(/agent) vs 컴퓨터 제어(/cu/agent)
            if (wantsFileOrProject && wantsComputerUse) {
                const useComputer = confirm('요청을 어떻게 처리할까요?\n확인: 내 컴퓨터를 직접 제어(설치/실행)\n취소: 채팅에서 프로젝트 파일 생성(다운로드)');
                if (useComputer) {
                    await sendToComputerUse(text);
                } else {
                    await sendToAgent(text + docContext, langCtx);
                }
                return;
            }

            // 파일/프로젝트 생성 요청은 /agent를 우선
            if (wantsFileOrProject) {
                await sendToAgent(text + docContext, langCtx);
                return;
            }

            // 데이터 분석 요청도 /agent 우선 (파일 읽기/명령 실행/산출물 생성 가능)
            if (wantsDataAnalysis) {
                await sendToAgent(text + docContext, langCtx);
                return;
            }

            // 명시적 컴퓨터 제어 요청만 /cu/agent로 라우팅
            if (wantsComputerUse) {
                await sendToComputerUse(text);
                return;
            }

            const aiMsgDiv = document.createElement('div');
            aiMsgDiv.className = 'message ai';
            aiMsgDiv.innerHTML = `<div class="sender-label">Lattice AI</div><div class="bubble">생각 중입니다...</div>`;
            chatViewport.appendChild(aiMsgDiv);

            try {
                if (document.getElementById('preview-area').style.display !== 'none' && !capturedImage) {
                    throw new Error('이미지 인식이 누락되었습니다. 파일 버튼으로 한 번 다시 선택해 주세요.');
                }
                const chatMsg = text + docContext;
                const chatCtx = langCtx + docContext;
                const res = await apiFetch('/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        message: chatMsg,
                        conversation_id: currentConversationId,
                        client_url: window.location.href,
                        context: chatCtx,
                        image_data: capturedImage,
                        stream: !capturedImage,
                        max_tokens: 2048,
                        user_email: currentUserEmail,
                        user_nickname: currentUserNickname
                    })
                });
                if (!res.ok) {
                    const data = await res.json().catch(() => ({}));
                    const err = new Error(data.detail || '응답을 생성하지 못했습니다.');
                    err.status = res.status;
                    throw err;
                }

                if (capturedImage) {
                    const data = await res.json();
                    chatViewport.removeChild(aiMsgDiv);
                    addMessage('ai', data.response || data.detail || '응답을 생성하지 못했습니다.');
                    loadHistory();
                    return;
                }

                const bubble = aiMsgDiv.querySelector('.bubble');
                const reader = res.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';
                let fullText = '';

                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;
                    buffer += decoder.decode(value, { stream: true });
                    const events = buffer.split('\n\n');
                    buffer = events.pop();

                    for (const event of events) {
                        const line = event.split('\n').find(item => item.startsWith('data: '));
                        if (!line) continue;
                        const payload = line.slice(6);
                        if (payload === '[DONE]') {
                            buffer = '';
                            break;
                        }
                        const data = JSON.parse(payload);
                        fullText += data.chunk || '';
                        renderAiBubble(bubble, fullText);
                    }
                }

                renderAiBubble(bubble, fullText || '응답을 생성하지 못했습니다.');
                loadHistory();
            } catch (e) {
                if (aiMsgDiv.parentNode === chatViewport) chatViewport.removeChild(aiMsgDiv);
                addMessage('ai', safeErrorMessage(e));
            }
            } finally {
                _isSending = false;
                sendBtn.disabled = false;
                userInput.focus();
            }
        }

        sendBtn.onclick = sendMessage;
        userInput.onkeydown = (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } };

        function setImagePreviewFromBlob(blob) {
            if (!blob) return;
            const reader = new FileReader();
            reader.onload = function(event) {
                const dataUrl = event.target?.result || '';
                if (typeof dataUrl !== 'string' || !dataUrl.includes(',')) return;
                const mimeMatch = dataUrl.match(/^data:(image\/[a-zA-Z0-9.+-]+);base64,/);
                currentImageMime = (mimeMatch && mimeMatch[1]) ? mimeMatch[1] : 'image/png';
                document.getElementById('img-preview').src = dataUrl;
                document.getElementById('preview-area').style.display = 'flex';
                currentImageData = dataUrl.split(',')[1];
            };
            reader.readAsDataURL(blob);
        }

        async function handleClipboardPaste(e) {
            const clipboard = e.clipboardData;
            if (!clipboard) {
                await tryClipboardReadFallback();
                return;
            }

            if (clipboard.items && clipboard.items.length) {
                for (let i = 0; i < clipboard.items.length; i++) {
                    const item = clipboard.items[i];
                    if (item.type && item.type.startsWith('image/')) {
                        e.preventDefault();
                        setImagePreviewFromBlob(item.getAsFile());
                        return;
                    }
                }
            }

            if (clipboard.files && clipboard.files.length) {
                for (let i = 0; i < clipboard.files.length; i++) {
                    const file = clipboard.files[i];
                    if (file.type && file.type.startsWith('image/')) {
                        e.preventDefault();
                        setImagePreviewFromBlob(file);
                        return;
                    }
                }
            }
            await tryClipboardReadFallback();
        }

        async function tryClipboardReadFallback() {
            if (!navigator.clipboard || !navigator.clipboard.read) return;
            try {
                const items = await navigator.clipboard.read();
                for (const item of items) {
                    const imageType = item.types.find(type => type.startsWith('image/'));
                    if (imageType) {
                        const blob = await item.getType(imageType);
                        setImagePreviewFromBlob(blob);
                        return;
                    }
                }
            } catch (_) {}
        }

        document.addEventListener('paste', handleClipboardPaste);
        userInput.addEventListener('paste', handleClipboardPaste);
        window.addEventListener('focus', tryClipboardReadFallback);

        document.getElementById('new-chat-btn').onclick = startNewChat;
        applyI18n();
        updateWorkspaceModeUi();
        // 초기 뷰: 홈 대시보드 (로그인 완료 후 showHome() 으로도 호출되지만 즉시 적용)
        (function initView() {
            const layout = document.querySelector('.app-layout');
            if (layout) layout.dataset.view = 'home';
            _loadHomeDashboard();
        })();

        // Session check — redirect to /account if not logged in
        (async function restoreSession() {
            try {
                const res = await apiFetch('/account/profile');
                if (res.ok) {
                    const data = await res.json();
                    currentUserEmail = data.email;
                    currentUserNickname = data.nickname || data.name || data.email;
                    isAdmin = Boolean(data.is_admin);
                    localStorage.setItem('ltcai_user_email', currentUserEmail);
                    localStorage.setItem('ltcai_user_nickname', currentUserNickname);
                    localStorage.setItem('ltcai_is_admin', isAdmin ? 'true' : 'false');
                    document.getElementById('user-nickname-display').innerText = currentUserNickname;
                    const av = document.getElementById('user-avatar-initial');
                    if (av) av.textContent = (currentUserNickname || 'G')[0].toUpperCase();
                    const adminBtn = document.getElementById('admin-btn');
                    if (adminBtn) adminBtn.style.display = isAdmin ? 'flex' : 'none';
                    document.getElementById('security-admin-meta').textContent = isAdmin ? t('admin_has_rights') : t('admin_dashboard_access');
                    updateWorkspaceModeUi();
                    startOnboardingIfNeeded();
                } else {
                    window.location.href = '/account';
                }
            } catch (_) {
                window.location.href = '/account';
            }
        })();

        (async function applyRuntimeFeatures() {
            try {
                const res = await apiFetch('/runtime_features');
                if (res.ok) {
                    const f = await res.json();
                    if (!f.graph_enabled) {
                        const btn = document.getElementById('data-graph-btn');
                        if (btn) btn.style.display = 'none';
                    }
                }
            } catch (_) {}
        })();

        loadModelStatus();
        loadVpcStatus();
        restoreCurrentConversation();
        syncTelegramHistory();
        setInterval(syncTelegramHistory, 2500);

        // ── 내 컴퓨터 ──────────────────────────────────────────────────
        let cuAgentRunning = false;
        let cuAgentAbort = null;

        async function openCuPanel() {
            document.getElementById('cu-overlay').style.display = 'flex';
            await cuRefreshStatus();
        }

        function closeCuPanel() {
            document.getElementById('cu-overlay').style.display = 'none';
        }

        async function cuRefreshStatus() {
            const dot = document.getElementById('cu-status-dot');
            const txt = document.getElementById('cu-status-text');
            try {
                const r = await apiFetch('/cu/status');
                const d = await r.json();
                if (d.available) {
                    dot.style.background = 'var(--accent)';
                    txt.textContent = `준비됨 — ${d.screen_size.width}×${d.screen_size.height}`;
                    const opsValue = document.getElementById('cu-ops-value');
                    if (opsValue) opsValue.textContent = `${d.screen_size.width}×${d.screen_size.height} 준비됨`;
                } else {
                    dot.style.background = 'var(--danger)';
                    txt.textContent = `pyautogui 없음 — ${d.reason || ''}`;
                }
            } catch(e) {
                dot.style.background = 'var(--faint)';
                txt.textContent = '서버 연결 실패';
            }
        }

        async function cuTakeScreenshot() {
            const spinner = document.getElementById('cu-screenshot-spinner');
            const img = document.getElementById('cu-screenshot-img');
            const placeholder = document.getElementById('cu-screenshot-placeholder');
            const meta = document.getElementById('cu-screenshot-meta');
            spinner.style.display = 'flex';
            try {
                const r = await apiFetch('/cu/screenshot');
                const d = await r.json();
                if (d.screenshot_b64) {
                    img.src = 'data:image/png;base64,' + d.screenshot_b64;
                    img.style.display = 'block';
                    placeholder.style.display = 'none';
                    meta.textContent = `${d.screen_width}×${d.screen_height} · ${(d.bytes/1024).toFixed(0)}KB`;
                    cuLog('📸 화면 캡처 완료');
                } else {
                    cuLog('❌ 캡처 실패: ' + JSON.stringify(d));
                }
            } catch(e) {
                cuLog('❌ ' + e.message);
            } finally {
                spinner.style.display = 'none';
            }
        }

        async function cuManualClick() {
            const x = parseInt(document.getElementById('cu-click-x').value);
            const y = parseInt(document.getElementById('cu-click-y').value);
            if (isNaN(x) || isNaN(y)) return;
            cuLog(`🖱️ 클릭 (${x}, ${y})`);
            try {
                await apiFetch('/cu/click', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({x,y})});
                cuLog('✅ 클릭 완료');
            } catch(e) { cuLog('❌ ' + e.message); }
        }

        async function cuManualType() {
            const text = document.getElementById('cu-type-text').value;
            if (!text) return;
            cuLog(`⌨️ 입력: "${text.slice(0,30)}"`);
            try {
                await apiFetch('/cu/type', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})});
                cuLog('✅ 입력 완료');
            } catch(e) { cuLog('❌ ' + e.message); }
        }

        async function cuManualKey() {
            const key = document.getElementById('cu-key-input').value.trim();
            if (!key) return;
            cuLog(`🔑 키: ${key}`);
            try {
                await apiFetch('/cu/key', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key})});
                cuLog('✅ 완료');
            } catch(e) { cuLog('❌ ' + e.message); }
        }

        function cuLog(msg) {
            const log = document.getElementById('cu-log');
            const line = document.createElement('div');
            line.textContent = `[${new Date().toLocaleTimeString('ko')}] ${msg}`;
            log.appendChild(line);
            log.scrollTop = log.scrollHeight;
        }

        async function cuRunAgent() {
            const task = document.getElementById('cu-task-input').value.trim();
            if (!task) return;
            if (cuAgentRunning) return;
            const showScreenshots = /스크린샷|캡처|화면\s*(봐|보|확인)|screenshot|capture|screen/i.test(task);
            cuAgentRunning = true;
            document.getElementById('cu-run-btn').style.display = 'none';
            document.getElementById('cu-stop-btn').style.display = 'flex';
            cuLog('🚀 작업 시작: ' + task);

            const controller = new AbortController();
            cuAgentAbort = controller;

            try {
                const resp = await apiFetch('/cu/agent', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({task, max_steps: 15, temperature: 0.1}),
                    signal: controller.signal,
                });
                const reader = resp.body.getReader();
                const decoder = new TextDecoder();
                let buf = '';
                while (true) {
                    const {done, value} = await reader.read();
                    if (done) break;
                    buf += decoder.decode(value, {stream: true});
                    const parts = buf.split('\n\n');
                    buf = parts.pop();
                    for (const part of parts) {
                        const lines = part.split('\n');
                        let evtName = '', evtData = '';
                        for (const l of lines) {
                            if (l.startsWith('event: ')) evtName = l.slice(7);
                            if (l.startsWith('data: ')) evtData = l.slice(6);
                        }
                        if (!evtData) continue;
                        try {
                            const d = JSON.parse(evtData);
                            if (evtName === 'action') cuLog(`⚡ [${d.step}] ${d.action}`);
                            else if (evtName === 'screenshot') {
                                if (showScreenshots) {
                                    const img = document.getElementById('cu-screenshot-img');
                                    img.src = 'data:image/png;base64,' + d.screenshot_b64;
                                    img.style.display = 'block';
                                    document.getElementById('cu-screenshot-placeholder').style.display = 'none';
                                    cuLog(`📸 [${d.step}] 화면 캡처`);
                                }
                            } else if (evtName === 'result') cuLog(`✅ [${d.step}] ${d.action} 완료`);
                            else if (evtName === 'tool_error') cuLog(`❌ [${d.step}] ${d.error}`);
                            else if (evtName === 'final') { cuLog('🎉 완료: ' + d.message); }
                            else if (evtName === 'error') cuLog('❌ 오류: ' + d.error);
                            else if (evtName === 'done') cuLog(`✅ 총 ${d.steps}단계 완료`);
                        } catch(e) {}
                    }
                }
            } catch(e) {
                if (e.name !== 'AbortError') cuLog('❌ 연결 오류: ' + e.message);
                else cuLog('⛔ 중단됨');
            } finally {
                cuAgentRunning = false;
                cuAgentAbort = null;
                document.getElementById('cu-run-btn').style.display = 'flex';
                document.getElementById('cu-stop-btn').style.display = 'none';
            }
        }

        function cuStopAgent() {
            if (cuAgentAbort) cuAgentAbort.abort();
        }

// ── Setup Wizard ─────────────────────────────────────────────────────────

    let _wizStep  = 0;
    let _wizEnv   = null;
    let _wizRecs  = null;
    let _wizItems = [];   // items selected by user in step 2

    function openSetupWizard() {
        document.getElementById('setup-overlay').classList.add('open');
        _runStep1();
    }

    function closeSetupWizard() {
        document.getElementById('setup-overlay').classList.remove('open');
    }

    // Prevent click-through on overlay background
    document.getElementById('setup-overlay').addEventListener('click', e => {
        if (e.target === document.getElementById('setup-overlay')) closeSetupWizard();
    });

    // ── Step indicators ───────────────────────────────────────────────────────
    function _setStep(n) {
        _wizStep = n;
        for (let i = 1; i <= 3; i++) {
            const el = document.getElementById(`wstep-${i}`);
            el.classList.remove('active', 'done');
            if (i < n) el.classList.add('done');
            if (i === n) el.classList.add('active');
        }
        for (let i = 1; i <= 2; i++) {
            const sep = document.getElementById(`wsep-${i}`);
            sep.classList.toggle('done', i < n);
        }
    }

    // ── Helpers ───────────────────────────────────────────────────────────────
    function _body()      { return document.getElementById('wizard-body'); }
    function _footInfo(t) { document.getElementById('wiz-footer-info').textContent = t; }
    function _footBtns(html) { document.getElementById('wiz-btn-row').innerHTML = html; }
    function _subtitle(t)    { document.getElementById('wiz-subtitle').textContent = t; }

    function _badgeClass(status) {
        const map = { installed:'installed', active:'active', ready:'ready',
                      available:'available', '설치됨':'installed', '설치 필요':'available',
                      '기본 탑재':'active', '준비됨':'ready', '인증 필요':'auth',
                      '설치 가능':'available', '설치 불가':'disabled-b' };
        return map[status] || 'available';
    }

    // ── Step 1: Scan ─────────────────────────────────────────────────────────
    async function _runStep1() {
        _setStep(1);
        _subtitle('PC 환경을 분석하고 있습니다...');
        _body().innerHTML = `
            <div class="scan-pulse" id="scan-pulse">
                <div class="scan-spinner"></div>
                <span>환경 분석 중...</span>
            </div>
            <div class="scan-grid" id="scan-grid"></div>`;
        _footInfo('잠시만 기다려 주세요');
        _footBtns('');

        try {
            const res = await fetch('/setup/scan');
            const data = await res.json();
            _wizEnv  = data.environment;
            _wizRecs = data.recommendations;
            _renderScanResults(_wizEnv);
        } catch (e) {
            _body().innerHTML = `<p style="color:var(--danger);padding:20px">환경 분석 실패: ${escapeHtml(String(e))}</p>`;
            _footBtns(`<button class="wbtn wbtn-primary" onclick="_runStep1()">다시 시도</button>`);
        }
    }

    function _renderScanResults(env) {
        document.getElementById('scan-pulse').style.display = 'none';

        const chip = env.chip || {};
        const mlx  = env.mlx  || {};
        const tools= env.tools || {};
        const keys = env.api_keys || {};

        const mlxLabel = mlx.available
            ? (mlx.mlx_lm && mlx.mlx_vlm ? 'MLX-LM · MLX-VLM 설치됨' : mlx.mlx_lm ? 'MLX-LM 설치됨' : '부분 설치')
            : '미설치';

        const cloudKeys = Object.entries(keys).filter(([,v]) => v).map(([k]) => k.toUpperCase());

        const rows = [
            { icon: chip.is_apple_silicon ? '🍎' : '🖥️',
              label: 'CPU · 칩',
              value: chip.name || 'Unknown',
              ok: true },
            { icon: '🧠', label: '메모리', value: `${env.ram_gb} GB`, ok: env.ram_gb >= 4 },
            { icon: '💾', label: '여유 디스크', value: `${env.disk_free_gb} GB`, ok: env.disk_free_gb >= 5 },
            { icon: mlx.available ? '✅' : '⚠️', label: 'MLX', value: mlxLabel, ok: mlx.available },
            { icon: tools.ollama ? '✅' : '○',  label: 'Ollama', value: tools.ollama ? '설치됨' : '미설치', ok: true },
            { icon: tools.brew   ? '✅' : '○',  label: 'Homebrew', value: tools.brew ? '설치됨' : '미설치', ok: true },
            { icon: cloudKeys.length ? '✅' : '○', label: 'Cloud API',
              value: cloudKeys.length ? cloudKeys.join(', ') : '없음', ok: true },
            { icon: env.os === 'Darwin' ? '🍎' : '🐧',
              label: '운영체제',
              value: `${env.os} ${env.os_version || ''}`.trim(), ok: true },
        ];

        const grid = document.getElementById('scan-grid');
        rows.forEach((row, i) => {
            const el = document.createElement('div');
            el.className = 'scan-row';
            el.innerHTML = `
                <span class="scan-icon">${row.icon}</span>
                <div>
                    <div class="scan-label">${escapeHtml(row.label)}</div>
                    <div class="scan-value">${escapeHtml(row.value)}</div>
                </div>`;
            grid.appendChild(el);
            setTimeout(() => el.classList.add('visible'), 80 + i * 90);
        });

        const zero = env.zero_config || {};
        const zeroRec = zero.recommend || {};
        const zeroPlan = zero.plan || {};
        if (zeroRec.model_id || zeroRec.runtime || zeroRec.backend) {
            const planCount = (zeroPlan.steps || []).length;
            const el = document.createElement('div');
            el.className = 'scan-row zero-config-row';
            el.innerHTML = `
                <span class="scan-icon">⚙️</span>
                <div>
                    <div class="scan-label">Zero-Config 추천</div>
                    <div class="scan-value">${escapeHtml([zeroRec.runtime, zeroRec.backend, zeroRec.model_id].filter(Boolean).join(' · '))}</div>
                    <div class="scan-value" style="margin-top:4px;color:var(--faint)">${planCount ? escapeHtml(`${planCount}개 설치/검증 단계 준비됨`) : '추가 설치 단계 없음'}</div>
                </div>`;
            grid.appendChild(el);
            setTimeout(() => el.classList.add('visible'), 80 + rows.length * 90);
        }

        const sum = _wizRecs?.summary || {};
        const zeroSummary = sum.zero_config || {};
        const recSuffix = zeroSummary.model_id ? ` · ${zeroSummary.model_id}` : '';
        _subtitle(`${escapeHtml(chip.name || 'Unknown')} · RAM ${env.ram_gb}GB · 여유 ${env.disk_free_gb}GB${escapeHtml(recSuffix)}`);
        _footInfo(`추천 최대 모델 크기: ${sum.max_model_gb || '?'}GB`);
        setTimeout(() => {
            _footBtns(`
                <button class="wbtn wbtn-ghost" onclick="closeSetupWizard()">취소</button>
                <button class="wbtn wbtn-primary" onclick="_runStep2()">추천 항목 보기 →</button>`);
        }, 800 + rows.length * 90);
    }

    // ── Step 2: Select ───────────────────────────────────────────────────────
    function _runStep2() {
        _setStep(2);
        _subtitle('설치 · 연결할 항목을 선택하세요');

        const recs = _wizRecs;
        if (!recs) { _runStep1(); return; }

        const sum = recs.summary || {};
        let html = '';

        const renderSection = (title, items, colClass) => {
            if (!items || !items.length) return '';
            const cards = items.map(item => {
                const isChecked  = item.checked  ? ' checked' : '';
                const isDisabled = item.disabled ? ' disabled' : '';
                const isRec = item.priority === 'recommended';
                const badgeCls = _badgeClass(item.badge || item.status);
                return `
                <div class="rec-item${isChecked}${isDisabled}"
                     id="ri_${escapeHtml(item.id)}"
                     onclick="${item.disabled ? '' : `_toggleItem('${item.id}')`}">
                    <div class="rec-checkbox">${item.checked && !item.disabled ? '✓' : ''}</div>
                    <div class="rec-item-body">
                        <div class="rec-item-header">
                            <span class="rec-item-name">${escapeHtml(item.name)}</span>
                            <span class="rec-badge ${badgeCls}">${escapeHtml(item.badge || item.status || '')}</span>
                            ${isRec ? '<span class="rec-recommended">추천</span>' : ''}
                        </div>
                        <div class="rec-item-sub">${escapeHtml(item.subtitle || '')}</div>
                        ${item.size_gb ? `<div class="rec-item-sub" style="margin-top:5px;color:var(--faint)">크기: ${item.size_gb}GB${item.disabled ? ' (RAM 부족)' : ''}</div>` : ''}
                    </div>
                </div>`;
            }).join('');
            return `<div class="rec-section-title">${title}</div>
                    <div class="rec-grid ${colClass || ''}">${cards}</div>`;
        };

        html += renderSection('필수 구성요소', recs.components);
        html += renderSection('엔진 (로컬 · 클라우드)', recs.engines);
        html += renderSection('모델 — 로컬 실행 (RAM 기준 필터)', recs.models);
        html += renderSection('MCP — 도구 연결', recs.mcps);

        _body().innerHTML = html;

        const allItems = [...(recs.components||[]), ...(recs.engines||[]), ...(recs.models||[]), ...(recs.mcps||[])];
        _wizItems = allItems.filter(i => i.checked && !i.disabled);

        _footInfo('');
        _updateStep2Footer();
    }

    function _toggleItem(id) {
        const recs = _wizRecs;
        const allItems = [...(recs.components||[]), ...(recs.engines||[]), ...(recs.models||[]), ...(recs.mcps||[])];
        const item = allItems.find(i => i.id === id);
        if (!item || item.disabled) return;

        item.checked = !item.checked;
        const el = document.getElementById(`ri_${id}`);
        if (!el) return;
        el.classList.toggle('checked', item.checked);
        el.querySelector('.rec-checkbox').textContent = item.checked ? '✓' : '';

        _wizItems = allItems.filter(i => i.checked && !i.disabled);
        _updateStep2Footer();
    }

    function _updateStep2Footer() {
        const n = _wizItems.length;
        _footInfo(n > 0 ? `${n}개 항목 선택됨` : '항목을 선택해 주세요');
        _footBtns(`
            <button class="wbtn wbtn-ghost" onclick="_runStep1()">← 뒤로</button>
            <button class="wbtn wbtn-primary" onclick="_runStep3()" ${n === 0 ? 'disabled' : ''}>
                설치 · 연결 시작 →
            </button>`);
    }

    // ── Step 3: Install ──────────────────────────────────────────────────────
    async function _runStep3() {
        _setStep(3);
        _subtitle('선택한 항목을 자동으로 설치 · 연결합니다');

        const items = _wizItems;
        if (!items.length) { _runStep2(); return; }

        // Build install list UI
        const listHtml = items.map(item => `
            <div class="install-row" id="ir_${escapeHtml(item.id)}">
                <div class="install-status-icon" id="ii_${escapeHtml(item.id)}">⏳</div>
                <div class="install-row-body">
                    <div class="install-row-name">${escapeHtml(item.name)}</div>
                    <div class="install-row-msg" id="im_${escapeHtml(item.id)}">대기 중...</div>
                </div>
            </div>`).join('');

        _body().innerHTML = `<div class="install-list">${listHtml}</div>`;
        _footInfo('설치 중에는 창을 닫지 마세요');
        _footBtns('');

        // Payload
        const payload = items.map(item => ({
            id:     item.id,
            name:   item.name,
            action: item.action || null,
        }));

        try {
            const resp = await fetch('/setup/install', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ items: payload }),
            });

            const reader = resp.body.getReader();
            const dec    = new TextDecoder();
            let buf = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buf += dec.decode(value, { stream: true });
                const lines = buf.split('\n');
                buf = lines.pop() ?? '';
                for (const line of lines) {
                    if (!line.startsWith('data: ')) continue;
                    const raw = line.slice(6).trim();
                    if (raw === '[DONE]') continue;
                    try { _handleInstallEvent(JSON.parse(raw)); } catch {}
                }
            }

            _showComplete();
        } catch (e) {
            _footBtns(`
                <button class="wbtn wbtn-ghost" onclick="_runStep2()">← 뒤로</button>
                <button class="wbtn wbtn-primary" onclick="_runStep3()">다시 시도</button>`);
            _footInfo(`오류: ${escapeHtml(String(e))}`);
        }
    }

    function _handleInstallEvent(ev) {
        if (ev.status === 'complete') return;

        const id = ev.id;
        if (!id) return;

        const rowEl = document.getElementById(`ir_${id}`);
        const iconEl= document.getElementById(`ii_${id}`);
        const msgEl = document.getElementById(`im_${id}`);
        if (!rowEl || !iconEl || !msgEl) return;

        rowEl.className = `install-row status-${ev.status}`;

        const icons = {
            starting: '⏳', running: '<span class="install-mini-spinner"></span>',
            progress: '🔄', done: '✅', error: '❌',
            auth: '🔗', waiting: '⏸️', skipped: '✓', loading: '<span class="install-mini-spinner"></span>',
        };
        iconEl.innerHTML = icons[ev.status] || '⏳';
        msgEl.textContent = ev.msg || '';

        if (ev.auth_url) {
            msgEl.innerHTML += `<br><a href="${escapeHtml(ev.auth_url)}" target="_blank"
                style="color:var(--accent-3);font-size:11px;">인증 페이지 열기 ↗</a>`;
        }
    }

    function _showComplete() {
        _subtitle('설정 완료!');
        _footInfo('');
        _footBtns(`<button class="wbtn wbtn-primary" onclick="closeSetupWizard();loadModelStatus()">완료 ✓</button>`);
    }

// ── MCP 관리 모달 ────────────────────────────────────────────────────────
    let _mcpCurrentTab = 'registry';

    async function openMcpModal() {
        if (getCurrentMode() === 'default') {
            showToast('고급 모드에서 사용할 수 있습니다.');
            return;
        }
        document.getElementById('mcp-modal-overlay').classList.add('open');
        await renderMcpModal(_mcpCurrentTab);
    }

    function closeMcpModal() {
        document.getElementById('mcp-modal-overlay').classList.remove('open');
    }

    function switchMcpTab(tab, btn) {
        _mcpCurrentTab = tab;
        document.querySelectorAll('.mcp-tab').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        renderMcpModal(tab);
    }

    async function renderMcpModal(tab) {
        tab = tab || _mcpCurrentTab || 'registry';
        const body = document.getElementById('mcp-modal-body');
        body.innerHTML = '<div style="color:var(--faint);font-size:13px;text-align:center;padding:24px">로딩 중...</div>';

        if (tab === 'claude-code') {
            await renderMcpClaudeCode(body);
        } else if (tab === 'custom') {
            await renderMcpCustom(body);
        } else {
            await renderMcpRegistry(body);
        }
    }

    async function renderMcpRegistry(body) {
        try {
            const [installedRes, toolsRes] = await Promise.all([
                apiFetch('/mcp/installed'),
                apiFetch('/mcp/tools'),
            ]);
            const installedData = installedRes.ok ? await installedRes.json() : { installed: [] };
            const toolsData = toolsRes.ok ? await toolsRes.json() : { installed_mcps: [] };

            const allMcps = Array.isArray(installedData?.installed)
                ? installedData.installed
                : (Array.isArray(toolsData?.installed_mcps) ? toolsData.installed_mcps : []);
            const installedItems = allMcps.filter(mcp => mcp.installed);
            const availableItems = allMcps.filter(mcp => !mcp.installed);

            let html = '';

            if (installedItems.length) {
                html += '<div class="mcp-section-label">설치됨</div>';
                html += installedItems.map(mcp => `
                    <div class="mcp-item">
                        <div class="mcp-item-icon">${mcp.icon || '🔌'}</div>
                        <div class="mcp-item-info">
                            <div class="mcp-item-name">${escapeHtml(mcp.name || mcp.id)}</div>
                            <div class="mcp-item-desc">${escapeHtml(mcp.description || '')}</div>
                        </div>
                        <span class="mcp-item-status">활성</span>
                    </div>
                `).join('');
            }

            if (availableItems.length) {
                html += '<div class="mcp-section-label">설치 가능</div>';
                html += availableItems.map(mcp => `
                    <div class="mcp-item" id="mcp-item-${escapeHtml(mcp.id)}">
                        <div class="mcp-item-icon">${mcp.icon || '🔌'}</div>
                        <div class="mcp-item-info">
                            <div class="mcp-item-name">${escapeHtml(mcp.name || mcp.id)}</div>
                            <div class="mcp-item-desc">${escapeHtml(mcp.description || '')}</div>
                        </div>
                        <button class="mcp-install-btn" onclick="installMcp('${escapeHtml(mcp.id)}')">설치</button>
                    </div>
                `).join('');
            }

            if (!html) html = '<div style="color:var(--faint);font-size:13px;text-align:center;padding:24px">사용 가능한 MCP 서버가 없습니다.</div>';
            body.innerHTML = html;
        } catch (e) {
            body.innerHTML = `<div style="color:#ff6b6b;font-size:13px;text-align:center;padding:24px">로드 실패: ${escapeHtml(e.message)}</div>`;
        }
    }

    async function renderMcpClaudeCode(body) {
        try {
            const res = await apiFetch('/mcp/claude-code-servers');
            const data = res.ok ? await res.json() : { servers: [] };
            const servers = data.servers || [];

            if (!servers.length) {
                body.innerHTML = `
                    <div style="color:var(--faint);font-size:13px;text-align:center;padding:32px 24px;">
                        <div style="font-size:28px;margin-bottom:10px">🤖</div>
                        <div><strong>Claude Code MCP 없음</strong></div>
                        <div style="margin-top:6px;font-size:11px">~/.claude/settings.json에 mcpServers 항목이 없습니다.</div>
                        <div style="margin-top:4px;font-size:11px">Claude Code에서 MCP를 설치하면 여기에 자동으로 표시됩니다.</div>
                    </div>`;
                return;
            }

            let html = '<div class="mcp-section-label">Claude Code에서 설치된 MCP</div>';
            html += servers.map(srv => `
                <div class="mcp-item">
                    <div class="mcp-item-icon">${srv.icon || '🤖'}</div>
                    <div class="mcp-item-info">
                        <div class="mcp-item-name" style="display:flex;align-items:center;gap:6px;">
                            ${escapeHtml(srv.name)}
                            <span class="mcp-source-badge claude-code">Claude Code</span>
                        </div>
                        <div class="mcp-item-desc" title="${escapeHtml(srv.package || '')}">${escapeHtml(srv.package || '')}</div>
                        ${srv.env_vars && srv.env_vars.length ? `<div class="mcp-item-desc" style="margin-top:2px">ENV: ${escapeHtml(srv.env_vars.map(e=>e.name).join(', '))}</div>` : ''}
                    </div>
                    <span class="mcp-item-status">활성</span>
                </div>
            `).join('');
            body.innerHTML = html;
        } catch (e) {
            body.innerHTML = `<div style="color:#ff6b6b;font-size:13px;text-align:center;padding:24px">로드 실패: ${escapeHtml(e.message)}</div>`;
        }
    }

    async function renderMcpCustom(body) {
        // Load existing custom MCPs
        let existingHtml = '';
        try {
            const res = await apiFetch('/mcp/custom');
            const data = res.ok ? await res.json() : { custom: [] };
            const customs = data.custom || [];
            if (customs.length) {
                existingHtml = '<div class="mcp-section-label">직접 추가한 MCP</div>';
                existingHtml += customs.map(c => `
                    <div class="mcp-item" id="mcp-custom-item-${escapeHtml(c.id)}">
                        <div class="mcp-item-icon">${c.icon || '🔌'}</div>
                        <div class="mcp-item-info">
                            <div class="mcp-item-name" style="display:flex;align-items:center;gap:6px;">
                                ${escapeHtml(c.name)}
                                <span class="mcp-source-badge custom">Custom</span>
                            </div>
                            <div class="mcp-item-desc" title="${escapeHtml(c.package||'')}">${escapeHtml(c.package||'')}</div>
                            ${c.description ? `<div class="mcp-item-desc">${escapeHtml(c.description)}</div>` : ''}
                        </div>
                        <button class="mcp-delete-btn" onclick="deleteCustomMcp('${escapeHtml(c.id)}')">삭제</button>
                    </div>
                `).join('');
            }
        } catch {}

        body.innerHTML = existingHtml + `
            <div class="mcp-section-label" style="margin-top:${existingHtml?'16px':'4px'}">새 MCP 추가</div>
            <div class="mcp-add-form">
                <div>
                    <label>이름 *</label>
                    <input id="mcp-add-name" type="text" placeholder="예: my-database" />
                </div>
                <div>
                    <label>패키지 / 명령어 *</label>
                    <input id="mcp-add-package" type="text" placeholder="예: @company/mcp-server 또는 npx mcp-server" />
                    <div class="field-hint">npm 패키지명 또는 실행 명령어</div>
                </div>
                <div>
                    <label>설명</label>
                    <input id="mcp-add-desc" type="text" placeholder="이 MCP가 하는 일 (선택)" />
                </div>
                <div>
                    <label>필요한 환경변수 (쉼표 구분)</label>
                    <input id="mcp-add-envs" type="text" placeholder="예: API_KEY, BASE_URL" />
                    <div class="field-hint">서버 실행 시 필요한 env var 이름들</div>
                </div>
                <div style="display:flex;align-items:center;gap:8px;">
                    <input id="mcp-add-icon" type="text" placeholder="🔌" style="width:56px;text-align:center;" maxlength="4" />
                    <label style="margin:0">아이콘 (이모지)</label>
                </div>
                <div id="mcp-add-error" style="color:#ff6b6b;font-size:12px;display:none;"></div>
                <button class="mcp-submit-btn" onclick="submitCustomMcp()">➕ MCP 추가</button>
            </div>
        `;
    }

    async function submitCustomMcp() {
        const name = document.getElementById('mcp-add-name').value.trim();
        const pkg = document.getElementById('mcp-add-package').value.trim();
        const desc = document.getElementById('mcp-add-desc').value.trim();
        const envsRaw = document.getElementById('mcp-add-envs').value.trim();
        const icon = document.getElementById('mcp-add-icon').value.trim() || '🔌';
        const errEl = document.getElementById('mcp-add-error');
        errEl.style.display = 'none';

        if (!name) { errEl.textContent = '이름을 입력해주세요.'; errEl.style.display='block'; return; }
        if (!pkg)  { errEl.textContent = '패키지를 입력해주세요.'; errEl.style.display='block'; return; }

        const env_vars = envsRaw ? envsRaw.split(',').map(s=>({name:s.trim(),description:''})).filter(e=>e.name) : [];
        try {
            const res = await apiFetch('/mcp/custom', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, package: pkg, description: desc, icon, env_vars }),
            });
            if (res.ok) {
                await renderMcpModal('custom');
            } else {
                const d = await res.json().catch(()=>({}));
                errEl.textContent = d.detail || '추가 실패';
                errEl.style.display = 'block';
            }
        } catch (e) {
            errEl.textContent = e.message || '네트워크 오류';
            errEl.style.display = 'block';
        }
    }

    async function deleteCustomMcp(id) {
        if (!confirm('이 MCP를 삭제하시겠습니까?')) return;
        try {
            const res = await apiFetch('/mcp/custom/' + encodeURIComponent(id), { method: 'DELETE' });
            if (res.ok) {
                await renderMcpModal('custom');
            } else {
                const d = await res.json().catch(()=>({}));
                alert(d.detail || '삭제 실패');
            }
        } catch (e) {
            alert(e.message || '네트워크 오류');
        }
    }

    async function installMcp(id) {
        const btn = document.querySelector(`#mcp-item-${CSS.escape(id)} .mcp-install-btn`);
        if (btn) { btn.disabled = true; btn.textContent = '설치 중...'; }
        try {
            const res = await apiFetch('/mcp/install', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mcp_id: id }),
            });
            if (res.ok) {
                await renderMcpModal('registry');
            } else {
                const d = await res.json().catch(() => ({}));
                if (btn) { btn.disabled = false; btn.textContent = '설치'; }
                alert(d.detail || '설치 실패');
            }
        } catch {
            if (btn) { btn.disabled = false; btn.textContent = '설치'; }
        }
    }

// Register Service Worker for PWA install support
    if ('serviceWorker' in navigator) {
        window.addEventListener('load', () => {
            navigator.serviceWorker.register('/sw.js', { scope: '/' })
                .catch(() => {}); // silent fail — SW is optional enhancement
        });
    }
