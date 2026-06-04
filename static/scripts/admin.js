/* Lattice AI - admin.html scripts */

const API_BASE = window.location.protocol === 'file:' ? 'http://localhost:4825' : '';

function apiFetch(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    return fetch(`${API_BASE}${path}`, { credentials: 'include', ...options, headers });
}

function currentUserEmail() {
    return localStorage.getItem('ltcai_user_email') || '';
}

function currentUserNickname() {
    return localStorage.getItem('ltcai_user_nickname') || 'Guest';
}

function currentUserIsAdmin() {
    return localStorage.getItem('ltcai_is_admin') === 'true';
}

function restoreSessionFromQuery() {
    const raw = sessionStorage.getItem('ltcai_admin_handoff');
    if (!raw) return;
    sessionStorage.removeItem('ltcai_admin_handoff');
    let data;
    try { data = JSON.parse(raw); } catch { return; }
    const { email, nickname, is_admin } = data;
    if (!email) return;
    localStorage.setItem('ltcai_user_email', email);
    if (nickname) localStorage.setItem('ltcai_user_nickname', nickname);
    if (is_admin === 'true' || is_admin === 'false') localStorage.setItem('ltcai_is_admin', is_admin);
}

function adminHeaders() {
    return {
        'Content-Type': 'application/json',
        'X-Admin-Email': currentUserEmail(),
    };
}

const A18N = {
    ko: {
        admin_sub: '관리자 대시보드',
        btn_back: '채팅으로',
        btn_refresh: '새로고침',
        btn_logout: '로그아웃',
        nav_dashboard: '대시보드',
        nav_users: '사용자 관리',
        nav_permissions: '권한 관리',
        nav_sso: 'SSO 관리',
        nav_enterprise: 'Enterprise',
        nav_security: '보안 모니터링',
        nav_audit: '감사 로그',
        nav_chat: '채팅으로',
        system_admin: '시스템 관리자',
        hero_title: '관리자 대시보드',
        hero_desc: '운영 현황, 세션, 모델, VPC 상태를 요약해서 보여줍니다.',
        current_session: '현재 세션',
        checking_session: '세션 확인 중...',
        card_total_users: '전체 사용자',
        card_messages: '활성 메시지',
        card_model: '현재 모델',
        card_vpc: 'VPC 상태',
        meta_need_admin: '관리자 권한 필요',
        meta_msg_unavailable: '최근 메시지 정보를 불러올 수 없음',
        chart_title: '메시지 활동 (최근 14일)',
        chart_desc: '사용자 메시지와 AI 응답 수를 날짜별로 표시합니다.',
        label_user: '사용자',
        label_email: '이메일',
        label_name: '이름',
        label_nickname: '닉네임',
        label_perm: '권한',
        label_status: '상태',
        label_actions: '관리',
        label_none: '없음',
        vpc_desc: '네트워크 프로필과 운영 상태를 수정합니다.',
        vpc_notes_ph: '운영 메모',
        vpc_save: '저장',
        vpc_loading: '불러오는 중...',
        vpc_saving: '저장 중...',
        vpc_saved: '저장되었습니다.',
        vpc_save_fail: '저장 실패',
        vpc_default_profile: '기본 VPC 프로필을 사용 중입니다.',
        vpc_last_saved: '마지막 저장:',
        vpc_standby: '대기',
        vpc_connected: '연결됨',
        vpc_needs_setup: '설정 필요',
        session_desc: '현재 로그인한 계정과 관리자 API 상태를 확인합니다.',
        session_no_info: '세션 정보가 없습니다',
        session_help_ok: '이메일 헤더가 설정되어 관리자 API를 호출할 수 있습니다.',
        session_help_fail: '채팅 화면에서 로그인한 뒤 이 화면을 열어야 관리자 API를 사용할 수 있습니다.',
        users_title: '사용자 관리',
        users_desc: '등록된 사용자와 활성/비활성 상태를 관리합니다.',
        invite_title: '초대 링크',
        invite_desc: '새 사용자를 초대할 링크를 확인하고 복사합니다.',
        btn_copy: '복사',
        copied: '복사됨',
        invite_gate_active: '초대 게이트 활성화됨',
        invite_gate_inactive: '초대 게이트 비활성 - 링크 없이도 접근 가능합니다.',
        permissions_title: '권한 관리',
        permissions_desc: '사용자별 기본 모드, 고급 모드, 관리자 모드 권한을 확인합니다.',
        permission_default: '기본 모드',
        permission_advanced: '고급 모드',
        permission_admin: '관리자 모드',
        permission_allowed: '허용',
        permission_blocked: '차단',
        permission_granted: '부여됨',
        permission_not_granted: '없음',
        sso_title: 'SSO 관리',
        sso_desc: 'Okta 또는 Microsoft Entra ID OIDC 설정을 저장하고 로그인 플로우에 연결합니다.',
        sso_provider_template: '제공자 템플릿',
        sso_provider_name: '제공자 이름',
        sso_discovery_url: 'OIDC Discovery URL',
        sso_client_id: 'Client ID',
        sso_client_secret: 'Client Secret',
        sso_redirect_uri: 'Redirect URI',
        sso_scopes: 'Scopes',
        sso_secret_ph: '비워두면 기존 값을 유지합니다',
        sso_loading: 'SSO 설정을 불러오는 중...',
        sso_save: 'SSO 설정 저장',
        sso_test: 'SSO 로그인 테스트',
        sso_saved: 'SSO 설정이 저장되었습니다.',
        sso_ready: '연동 준비됨',
        sso_not_ready: '설정 필요',
        sso_secret_saved: '시크릿 저장됨',
        sso_secret_missing: '시크릿 없음',
        sso_okta_help: 'Okta Admin Console에서 OIDC Web App을 만들고 Sign-in redirect URI에 아래 Redirect URI를 등록하세요.',
        sso_entra_help: 'Microsoft Entra ID 앱 등록에서 Web redirect URI를 등록하고 Client secret을 생성하세요.',
        sso_custom_help: '표준 OIDC discovery endpoint, client ID, client secret, redirect URI를 입력하세요.',
        sensitivity_title: '보안 모니터링',
        sensitivity_desc: '민감정보, 위험 필드, 준수 필드를 집중적으로 확인합니다.',
        sensitivity_risk: '위험',
        sensitivity_compliant: '준수',
        sensitivity_risk_rate: '위험률',
        sensitivity_high: '높음',
        risk_fields: '위험 필드',
        compliance_fields: '준수 필드',
        no_risk_fields: '감지된 위험 필드가 없습니다.',
        no_compliance_fields: '준수 항목이 없습니다.',
        security_export_toggle: '보안 모니터링 로그 추출',
        audit_export_toggle: '감사 로그 추출',
        export_txt: 'TXT 추출',
        export_excel: 'Excel 추출',
        export_csv: 'CSV 추출',
        export_no_data: '추출할 데이터가 없습니다.',
        audit_title: '감사 로그',
        audit_desc: 'AI 사용량, 업로드, 민감정보 감지, 삭제/정리 이벤트를 보존합니다.',
        audit_user_risk: '사용자 사용량 및 위험도',
        audit_trail: '감사 이벤트',
        audit_no_data: '감사 데이터가 아직 없습니다.',
        audit_no_events: '최근 감사 이벤트가 없습니다.',
        loading: '불러오는 중...',
        no_users: '사용자 데이터가 없습니다.',
        status_active: '활성',
        status_inactive: '비활성',
        role_admin: '관리자',
        role_user: '사용자',
        btn_grant_admin: '관리자 지정',
        btn_revoke_admin: '권한 해제',
        btn_activate: '활성화',
        btn_deactivate: '비활성화',
        btn_delete: '삭제',
        confirm_delete: '사용자를 삭제할까요?',
        err_no_admin: '관리자 권한이 없습니다. 채팅 화면에서 관리자 계정으로 로그인한 뒤 다시 열어주세요.',
        err_partial: '일부 섹션을 불러오지 못했습니다:',
        err_network: '네트워크 연결을 확인해 주세요.',
        err_load: '대시보드를 불러오지 못했습니다.',
        section_summary: '요약',
        section_users: '사용자 목록',
        section_sensitivity: '보안 모니터링',
        section_audit: '감사 로그',
        section_sso: 'SSO 관리',
        enterprise_title: 'Enterprise 관리자',
        enterprise_desc: '관리자 정책, 감사 추출, SIEM 추출, 조직 설정, 기능 상태를 확인합니다.',
        enterprise_policies: '관리자 정책',
        enterprise_policies_desc: 'Community 유효 정책과 Enterprise 정책 팩 상태입니다.',
        enterprise_org: '조직 설정',
        enterprise_org_desc: '워크스페이스 거버넌스와 조직 기능 상태입니다.',
        enterprise_audit_export: '감사 추출',
        enterprise_audit_export_desc: 'Community에서는 로컬 추출이 가능하며 보존 정책은 Enterprise 확장 지점입니다.',
        enterprise_siem: 'SIEM 추출',
        enterprise_siem_desc: 'Community에서 외부 이벤트를 전송하지 않고 SIEM envelope를 미리 봅니다.',
    },
    en: {
        admin_sub: 'Admin Dashboard',
        btn_back: 'Chat',
        btn_refresh: 'Refresh',
        btn_logout: 'Logout',
        nav_dashboard: 'Dashboard',
        nav_users: 'User Management',
        nav_permissions: 'Permission Management',
        nav_sso: 'SSO Management',
        nav_enterprise: 'Enterprise',
        nav_security: 'Security Monitoring',
        nav_audit: 'Audit Logs',
        nav_chat: 'Back to Chat',
        system_admin: 'System Administrator',
        hero_title: 'Admin Dashboard',
        hero_desc: 'Summarize operations, session, model, and VPC status.',
        current_session: 'Current Session',
        checking_session: 'Checking session...',
        card_total_users: 'Total Users',
        card_messages: 'Active Messages',
        card_model: 'Current Model',
        card_vpc: 'VPC Status',
        meta_need_admin: 'Admin permission required',
        meta_msg_unavailable: 'Could not load recent message info',
        chart_title: 'Message Activity (Last 14 Days)',
        chart_desc: 'User messages and AI responses by day.',
        label_user: 'User',
        label_email: 'Email',
        label_name: 'Name',
        label_nickname: 'Nickname',
        label_perm: 'Role',
        label_status: 'Status',
        label_actions: 'Actions',
        label_none: 'None',
        vpc_desc: 'Edit the network profile and operating state.',
        vpc_notes_ph: 'Operations notes',
        vpc_save: 'Save',
        vpc_loading: 'Loading...',
        vpc_saving: 'Saving...',
        vpc_saved: 'Saved.',
        vpc_save_fail: 'Save failed',
        vpc_default_profile: 'Using the default VPC profile.',
        vpc_last_saved: 'Last saved:',
        vpc_standby: 'Standby',
        vpc_connected: 'Connected',
        vpc_needs_setup: 'Setup required',
        session_desc: 'Check the current login account and admin API status.',
        session_no_info: 'No session info',
        session_help_ok: 'Email header is set, so admin API calls are available.',
        session_help_fail: 'Log in from the chat screen first, then open this screen.',
        users_title: 'User Management',
        users_desc: 'Manage registered users and active/inactive status.',
        invite_title: 'Invite Link',
        invite_desc: 'View and copy the link for inviting new users.',
        btn_copy: 'Copy',
        copied: 'Copied',
        invite_gate_active: 'Invite gate active',
        invite_gate_inactive: 'Invite gate disabled - users can access without a link.',
        permissions_title: 'Permission Management',
        permissions_desc: 'Review Default Mode, Advanced Mode, and Admin Mode permissions by user.',
        permission_default: 'Default Mode',
        permission_advanced: 'Advanced Mode',
        permission_admin: 'Admin Mode',
        permission_allowed: 'Allowed',
        permission_blocked: 'Blocked',
        permission_granted: 'Granted',
        permission_not_granted: 'None',
        sso_title: 'SSO Management',
        sso_desc: 'Save Okta or Microsoft Entra ID OIDC settings and connect them to the login flow.',
        sso_provider_template: 'Provider Template',
        sso_provider_name: 'Provider Name',
        sso_discovery_url: 'OIDC Discovery URL',
        sso_client_id: 'Client ID',
        sso_client_secret: 'Client Secret',
        sso_redirect_uri: 'Redirect URI',
        sso_scopes: 'Scopes',
        sso_secret_ph: 'Leave blank to keep the existing value',
        sso_loading: 'Loading SSO settings...',
        sso_save: 'Save SSO Settings',
        sso_test: 'Test SSO Login',
        sso_saved: 'SSO settings saved.',
        sso_ready: 'Ready',
        sso_not_ready: 'Needs setup',
        sso_secret_saved: 'Secret saved',
        sso_secret_missing: 'No secret',
        sso_okta_help: 'Create an OIDC Web App in Okta Admin Console and add the Redirect URI below as the sign-in redirect URI.',
        sso_entra_help: 'Register a web app in Microsoft Entra ID, add the Redirect URI, and create a client secret.',
        sso_custom_help: 'Enter a standard OIDC discovery endpoint, client ID, client secret, and redirect URI.',
        sensitivity_title: 'Security Monitoring',
        sensitivity_desc: 'Focus on sensitive data, risk fields, and compliance fields.',
        sensitivity_risk: 'Risk',
        sensitivity_compliant: 'Compliant',
        sensitivity_risk_rate: 'Risk rate',
        sensitivity_high: 'High',
        risk_fields: 'Risk Fields',
        compliance_fields: 'Compliance Fields',
        no_risk_fields: 'No risk fields detected.',
        no_compliance_fields: 'No compliance items.',
        security_export_toggle: 'Export Security Logs',
        audit_export_toggle: 'Export Audit Logs',
        export_txt: 'Export TXT',
        export_excel: 'Export Excel',
        export_csv: 'Export CSV',
        export_no_data: 'No data to export.',
        audit_title: 'Audit Logs',
        audit_desc: 'Preserve AI usage, uploads, sensitive detections, and delete/cleanup events.',
        audit_user_risk: 'User Usage & Risk',
        audit_trail: 'Audit Trail',
        audit_no_data: 'No audit data yet.',
        audit_no_events: 'No recent audit events.',
        loading: 'Loading...',
        no_users: 'No user data.',
        status_active: 'Active',
        status_inactive: 'Inactive',
        role_admin: 'Admin',
        role_user: 'User',
        btn_grant_admin: 'Make Admin',
        btn_revoke_admin: 'Remove Admin',
        btn_activate: 'Activate',
        btn_deactivate: 'Deactivate',
        btn_delete: 'Delete',
        confirm_delete: 'Delete this user?',
        err_no_admin: 'No admin permission. Log in as an admin from the chat screen.',
        err_partial: 'Failed to load some sections:',
        err_network: 'Please check your network connection.',
        err_load: 'Could not load dashboard.',
        section_summary: 'Summary',
        section_users: 'User list',
        section_sensitivity: 'Security monitoring',
        section_audit: 'Audit logs',
        section_sso: 'SSO management',
        enterprise_title: 'Enterprise Admin',
        enterprise_desc: 'Review admin policies, audit export, SIEM export, organization settings, and capability status.',
        enterprise_policies: 'Admin Policies',
        enterprise_policies_desc: 'Effective Community policy and Enterprise policy-pack status.',
        enterprise_org: 'Organization Settings',
        enterprise_org_desc: 'Workspace governance and organization capability status.',
        enterprise_audit_export: 'Audit Export',
        enterprise_audit_export_desc: 'Community local export is available; retention is an Enterprise extension point.',
        enterprise_siem: 'SIEM Export',
        enterprise_siem_desc: 'Preview the SIEM envelope without streaming external events in Community.',
    }
};

let currentLang = localStorage.getItem('ltcai_lang') || 'ko';
let activityChartInstance = null;
let latestUsers = [];
let latestSso = null;
let latestSensitivity = null;
let latestAudit = null;
let latestEnterprise = null;

function t(key) {
    return (A18N[currentLang] || A18N.ko)[key] || key;
}

function applyI18n() {
    document.documentElement.lang = currentLang;
    document.querySelectorAll('[data-i18n]').forEach(el => {
        if (el.id === 'session-help') return;
        const val = t(el.dataset.i18n);
        if (val) el.textContent = val;
    });
    document.querySelectorAll('[data-i18n-ph]').forEach(el => {
        const val = t(el.dataset.i18nPh);
        if (val) el.placeholder = val;
    });
    ['ko', 'en'].forEach(lang => {
        const el = document.getElementById(`admin-lang-${lang}`);
        if (el) el.classList.toggle('active', lang === currentLang);
    });
    const langBtn = document.getElementById('admin-lang-btn');
    if (langBtn) langBtn.textContent = `Language: ${currentLang === 'ko' ? '한국어' : 'English'}`;
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
    renderUsers(latestUsers);
    renderPermissions(latestUsers);
    renderSso(latestSso);
    renderSensitivity(latestSensitivity);
    renderAudit(latestAudit);
    renderEnterpriseAdmin(latestEnterprise);
    loadDashboard();
}

window.toggleLangMenu = toggleLangMenu;
window.setLang = setLang;

document.addEventListener('click', e => {
    if (!e.target.closest('.lang-picker')) {
        document.querySelectorAll('.lang-picker-menu').forEach(m => m.classList.remove('open'));
    }
});

function switchAdminView(view) {
    const target = view || 'dashboard';
    document.querySelectorAll('[data-admin-view]').forEach(section => {
        section.classList.toggle('active', section.dataset.adminView === target);
    });
    document.querySelectorAll('[data-admin-nav]').forEach(link => {
        link.classList.toggle('active', link.dataset.adminNav === target);
    });
    if (location.hash.slice(1) !== target) history.replaceState(null, '', `#${target}`);
}

function initAdminNav() {
    document.querySelectorAll('[data-admin-nav]').forEach(link => {
        link.addEventListener('click', event => {
            event.preventDefault();
            switchAdminView(link.dataset.adminNav);
        });
    });
    const initial = location.hash.slice(1) || 'dashboard';
    switchAdminView(document.getElementById(`admin-view-${initial}`) ? initial : 'dashboard');
}

function esc(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function compactModelName(modelId, maxLength = 28) {
    if (!modelId) return 'None';
    const clean = String(modelId).replaceAll('mlx-community/', '');
    if (clean.length <= maxLength) return clean;
    const head = Math.max(8, maxLength - 10);
    return `${clean.slice(0, head)}...${clean.slice(-6)}`;
}

function sumStatValue(value) {
    if (value === null || value === undefined) return 0;
    if (typeof value === 'number') return value;
    if (typeof value === 'string') {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : 0;
    }
    if (Array.isArray(value)) return value.reduce((total, item) => total + sumStatValue(item), 0);
    if (typeof value === 'object') return Object.values(value).reduce((total, item) => total + sumStatValue(item), 0);
    return 0;
}

function formatNumber(value) {
    const num = Number(value || 0);
    return Number.isFinite(num) ? num.toLocaleString(currentLang === 'ko' ? 'ko-KR' : 'en-US') : '0';
}

function roleLabel(role) {
    return role === 'admin' ? t('role_admin') : t('role_user');
}

function statusLabel(user) {
    return user.disabled ? t('status_inactive') : t('status_active');
}

function permissionTag(text, tone = 'low') {
    return `<span class="tag ${tone}">${esc(text)}</span>`;
}

function vpcHealthText(config) {
    if (!config) return t('vpc_standby');
    if (config.vpn_status === 'connected' || config.peering_status === 'active') return t('vpc_connected');
    if (config.vpn_status === 'standby') return t('vpc_standby');
    return config.vpn_status || config.peering_status || t('vpc_needs_setup');
}

function formatTime(value) {
    if (!value) return '-';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString(currentLang === 'ko' ? 'ko-KR' : 'en-US');
}

function renderActivityChart(daily = []) {
    if (!window.Chart) return;
    const labels = daily.map(d => d.date);
    const userData = daily.map(d => d.user);
    const aiData = daily.map(d => d.assistant);
    const canvas = document.getElementById('activity-chart');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (activityChartInstance) activityChartInstance.destroy();
    activityChartInstance = new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [
                { label: t('label_user'), data: userData, backgroundColor: 'rgba(99,102,241,0.7)', borderRadius: 4 },
                { label: 'AI', data: aiData, backgroundColor: 'rgba(34,196,160,0.55)', borderRadius: 4 }
            ]
        },
        options: {
            responsive: true,
            plugins: { legend: { labels: { color: '#4a4668', font: { size: 12 } } } },
            scales: {
                x: { ticks: { color: '#7a74a0' }, grid: { color: 'rgba(111,66,232,0.08)' } },
                y: { ticks: { color: '#7a74a0', stepSize: 1 }, grid: { color: 'rgba(111,66,232,0.08)' }, beginAtZero: true }
            }
        }
    });
}

async function copyInviteLink() {
    const input = document.getElementById('invite-link-input');
    const btn = document.getElementById('copy-invite-btn');
    try {
        await navigator.clipboard.writeText(input.value);
        btn.querySelector('span').textContent = t('copied');
        setTimeout(() => btn.querySelector('span').textContent = t('btn_copy'), 1800);
    } catch {
        input.select();
    }
}

function setSessionInfo() {
    const email = currentUserEmail();
    const nick = currentUserNickname();
    const isAdmin = currentUserIsAdmin();
    document.getElementById('session-value').textContent = email ? `${nick} <${email}>` : t('session_no_info');
    const tags = [
        [t('label_user'), nick, 'low'],
        [t('label_email'), email || t('label_none'), 'medium'],
        [t('label_perm'), isAdmin ? t('role_admin') : t('role_user'), isAdmin ? 'low' : 'medium']
    ];
    document.getElementById('session-tags').innerHTML = tags.map(([label, value, tone]) => `
        <span class="tag ${tone}"><span>${esc(label)}</span> ${esc(value)}</span>
    `).join('');
    document.getElementById('admin-pill').innerHTML = isAdmin
        ? '<i class="ti ti-shield-check"></i> Admin'
        : '<i class="ti ti-lock"></i> Read only';
    document.getElementById('session-help').textContent = email
        ? t('session_help_ok')
        : t('session_help_fail');
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
        ? `${t('vpc_last_saved')} ${formatTime(config.updated_at)}`
        : t('vpc_default_profile');
}

function renderSummary(health, summary, vpc) {
    document.getElementById('total-users').textContent = summary ? summary.total_users : '-';
    document.getElementById('total-users-meta').textContent = summary
        ? `${summary.active_users} ${t('status_active')} - ${summary.admin_users} ${t('role_admin')}`
        : t('meta_need_admin');
    document.getElementById('total-messages').textContent = summary ? formatNumber(summary.total_messages) : '-';
    document.getElementById('total-messages-meta').textContent = summary
        ? `user ${summary.user_messages} - assistant ${summary.assistant_messages}`
        : t('meta_msg_unavailable');
    const modelValue = compactModelName(health?.current_model, 22);
    document.getElementById('current-model').textContent = modelValue;
    document.getElementById('current-model').title = health?.current_model || modelValue;
    document.getElementById('current-model-meta').textContent = `${health?.loaded_models?.length || 0} loaded - ${health?.device || 'local runtime'}`;
    document.getElementById('vpc-status').textContent = vpc?.provider || '-';
    document.getElementById('vpc-status').title = [vpc?.provider, vpc?.region].filter(Boolean).join(' ');
    document.getElementById('vpc-status-meta').textContent = `${vpc?.region || '-'} - ${vpc?.cidr_block || '-'} - ${vpcHealthText(vpc)}`;
}

function renderUsers(users) {
    latestUsers = Array.isArray(users) ? users : [];
    const wrap = document.getElementById('user-table-wrap');
    if (!latestUsers.length) {
        wrap.innerHTML = `<div class="preview" style="padding:14px">${t('no_users')}</div>`;
        return;
    }
    wrap.innerHTML = `
        <table>
            <thead>
                <tr>
                    <th>${t('label_email')}</th>
                    <th>${t('label_name')}</th>
                    <th>${t('label_nickname')}</th>
                    <th>${t('label_perm')}</th>
                    <th>${t('label_status')}</th>
                    <th>${t('label_actions')}</th>
                </tr>
            </thead>
            <tbody>
                ${latestUsers.map(user => `
                    <tr>
                        <td data-label="${t('label_email')}">${esc(user.email)}</td>
                        <td data-label="${t('label_name')}">${esc(user.name || '-')}</td>
                        <td data-label="${t('label_nickname')}">${esc(user.nickname || '-')}</td>
                        <td data-label="${t('label_perm')}"><span class="role">${esc(roleLabel(user.role))}</span></td>
                        <td data-label="${t('label_status')}">${permissionTag(statusLabel(user), user.disabled ? 'medium' : 'low')}</td>
                        <td data-label="${t('label_actions')}">
                            <div class="actions">
                                <button class="table-btn" data-action="role" data-email="${esc(user.email)}" data-next-role="${user.role === 'admin' ? 'user' : 'admin'}">
                                    ${user.role === 'admin' ? t('btn_revoke_admin') : t('btn_grant_admin')}
                                </button>
                                <button class="table-btn" data-action="disable" data-email="${esc(user.email)}" data-disabled="${user.disabled ? 'false' : 'true'}">
                                    ${user.disabled ? t('btn_activate') : t('btn_deactivate')}
                                </button>
                                <button class="table-btn danger" data-action="delete" data-email="${esc(user.email)}">${t('btn_delete')}</button>
                            </div>
                        </td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

function renderPermissions(users) {
    latestUsers = Array.isArray(users) ? users : latestUsers;
    const wrap = document.getElementById('permission-table-wrap');
    if (!latestUsers.length) {
        wrap.innerHTML = `<div class="preview" style="padding:14px">${t('no_users')}</div>`;
        return;
    }
    wrap.innerHTML = `
        <table>
            <thead>
                <tr>
                    <th>${t('label_user')}</th>
                    <th>${t('label_status')}</th>
                    <th>${t('permission_default')}</th>
                    <th>${t('permission_advanced')}</th>
                    <th>${t('permission_admin')}</th>
                    <th>${t('label_actions')}</th>
                </tr>
            </thead>
            <tbody>
                ${latestUsers.map(user => {
                    const active = !user.disabled;
                    const isAdmin = user.role === 'admin';
                    return `
                        <tr>
                            <td data-label="${t('label_user')}">
                                <strong>${esc(user.nickname || user.name || user.email)}</strong>
                                <div class="preview">${esc(user.email)}</div>
                            </td>
                            <td data-label="${t('label_status')}">${permissionTag(statusLabel(user), active ? 'low' : 'medium')}</td>
                            <td data-label="${t('permission_default')}">${permissionTag(active ? t('permission_allowed') : t('permission_blocked'), active ? 'low' : 'medium')}</td>
                            <td data-label="${t('permission_advanced')}">${permissionTag(active ? t('permission_allowed') : t('permission_blocked'), active ? 'low' : 'medium')}</td>
                            <td data-label="${t('permission_admin')}">${permissionTag(isAdmin && active ? t('permission_granted') : t('permission_not_granted'), isAdmin && active ? 'low' : 'medium')}</td>
                            <td data-label="${t('label_actions')}">
                                <div class="actions">
                                    <button class="table-btn" data-action="role" data-email="${esc(user.email)}" data-next-role="${isAdmin ? 'user' : 'admin'}">
                                        ${isAdmin ? t('btn_revoke_admin') : t('btn_grant_admin')}
                                    </button>
                                    <button class="table-btn" data-action="disable" data-email="${esc(user.email)}" data-disabled="${user.disabled ? 'false' : 'true'}">
                                        ${user.disabled ? t('btn_activate') : t('btn_deactivate')}
                                    </button>
                                </div>
                            </td>
                        </tr>
                    `;
                }).join('')}
            </tbody>
        </table>
    `;
}

function renderSensitivity(report) {
    latestSensitivity = report || null;
    const summary = report?.summary || {};
    const severity = summary.severity_counts || {};
    const fieldCounts = summary.field_counts || {};
    const userCounts = summary.user_counts || {};
    const tags = [
        ['high', `${t('sensitivity_risk')} ${summary.risky_messages || 0}`],
        ['low', `${t('sensitivity_compliant')} ${summary.compliant_messages || 0}`],
        ['medium', `${t('sensitivity_risk_rate')} ${summary.risk_rate || 0}%`],
        ['high', `${t('sensitivity_high')} ${severity.high || 0}`]
    ];
    const fieldTags = Object.entries(fieldCounts).map(([label, count]) => ['medium', `${label} ${count}`]);
    const userTags = Object.entries(userCounts).map(([label, count]) => ['high', `${label} ${count}`]);
    document.getElementById('sensitivity-summary').innerHTML = [...tags, ...fieldTags, ...userTags]
        .map(([tone, label]) => `<span class="tag ${tone}">${esc(label)}</span>`).join('');

    const riskList = report?.risk_fields || [];
    const complianceList = report?.compliance_fields || [];
    document.getElementById('risk-fields').innerHTML = riskList.length
        ? riskList.slice().reverse().map(item => sensitivityItemHtml(item, true)).join('')
        : `<div class="preview">${t('no_risk_fields')}</div>`;
    document.getElementById('compliance-fields').innerHTML = complianceList.length
        ? complianceList.slice().reverse().map(item => sensitivityItemHtml(item, false)).join('')
        : `<div class="preview">${t('no_compliance_fields')}</div>`;
}

function sensitivityItemHtml(item, risky) {
    const labels = risky ? item.labels : item.compliance_fields;
    return `
        <div class="item">
            <div class="item-meta">
                <span class="tag">${esc(item.user_nickname || 'Unknown')}</span>
                <span class="tag">${esc(item.user_email || 'unknown')}</span>
                <span class="tag ${item.sensitivity || 'low'}">${esc(item.sensitivity || 'none')}</span>
                ${(labels || []).map(label => `<span class="tag ${risky ? 'medium' : 'low'}">${esc(label)}</span>`).join('')}
            </div>
            <div class="preview">${esc(item.preview || '')}</div>
        </div>
    `;
}

function auditEventLabel(event) {
    const labels = {
        chat_message: event?.role === 'assistant' ? 'AI response' : 'User message',
        document_upload: 'Document upload',
        clear_command: 'Chat clear',
        conversation_delete: 'Conversation delete',
        history_delete: 'History delete',
        user_delete: 'User delete',
        user_update: 'User update',
        sso_config_update: 'SSO config update',
    };
    return labels[event?.event_type] || event?.event_type || '-';
}

function auditTarget(event) {
    if (!event) return '-';
    if (event.filename) return event.filename;
    if (event.target_email) return `target: ${event.target_email}`;
    if (event.provider_name || event.discovery_url) return [event.provider_name, event.discovery_url].filter(Boolean).join(' - ');
    if (event.command) return `${event.command} - ${event.scope || '-'} - removed ${event.removed || 0}`;
    if (event.event_type === 'history_delete') return `history - removed ${event.removed || 0} - kept ${event.kept || 0}`;
    if (event.conversation_id) return `conversation ${String(event.conversation_id).slice(0, 18)}`;
    return event.content_preview || '-';
}

function renderAudit(audit) {
    latestAudit = audit || null;
    const summary = audit?.summary || {};
    const graph = audit?.graph || {};
    const graphNodes = sumStatValue(graph.nodes);
    const graphEdges = sumStatValue(graph.edges);
    const metrics = [
        ['Total Events', summary.total_events || 0, `${summary.chat_events || 0} chat events`],
        ['AI Usage', `${summary.user_messages || 0}/${summary.assistant_messages || 0}`, 'user / assistant'],
        ['Uploads', summary.document_uploads || 0, `${formatNumber(graphNodes)} graph nodes`],
        ['Clear Events', summary.clear_events || 0, 'screen cleanup only'],
        ['Sensitive', summary.sensitive_events || 0, `${summary.high_sensitive_events || 0} high risk`],
    ];
    document.getElementById('audit-metrics').innerHTML = metrics.map(([label, value, meta]) => `
        <div class="audit-metric">
            <div class="label">${esc(label)}</div>
            <div class="value">${esc(value)}</div>
            <div class="meta">${esc(meta)}</div>
        </div>
    `).join('');

    const tags = [
        ['low', `Graph nodes ${formatNumber(graphNodes)}`],
        ['low', `Edges ${formatNumber(graphEdges)}`],
        ['medium', `Deletes ${summary.delete_events || 0}`],
        [summary.high_sensitive_events ? 'high' : 'low', `High risk ${summary.high_sensitive_events || 0}`]
    ];
    document.getElementById('audit-summary-tags').innerHTML = tags.map(([tone, label]) => `<span class="tag ${tone}">${esc(label)}</span>`).join('');

    const users = audit?.per_user || [];
    document.getElementById('audit-user-table').innerHTML = users.length ? `
        <table>
            <thead>
                <tr>
                    <th>${t('label_user')}</th>
                    <th>AI Use</th>
                    <th>Uploads</th>
                    <th>Sensitive</th>
                    <th>Clear/Delete</th>
                    <th>Last Active</th>
                </tr>
            </thead>
            <tbody>
                ${users.map(user => `
                    <tr>
                        <td data-label="${t('label_user')}"><strong>${esc(user.nickname || user.email || 'Unknown')}</strong><div class="preview">${esc(user.email || '')}</div></td>
                        <td data-label="AI Use">${esc(user.user_messages || 0)} / ${esc(user.assistant_messages || 0)}</td>
                        <td data-label="Uploads">${esc(user.document_uploads || 0)}</td>
                        <td data-label="Sensitive">${permissionTag(user.sensitive_events || 0, (user.high_sensitive_events || 0) ? 'high' : ((user.sensitive_events || 0) ? 'medium' : 'low'))}</td>
                        <td data-label="Clear/Delete">${esc(user.clear_events || 0)} / ${esc(user.delete_events || 0)}</td>
                        <td data-label="Last Active">${esc(formatTime(user.last_activity_at))}</td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    ` : `<div class="preview" style="padding:14px">${t('audit_no_data')}</div>`;

    const events = audit?.recent_events || [];
    document.getElementById('audit-event-table').innerHTML = events.length ? `
        <table>
            <thead>
                <tr>
                    <th>Time</th>
                    <th>Event</th>
                    <th>${t('label_user')}</th>
                    <th>Target/Data</th>
                    <th>Risk</th>
                </tr>
            </thead>
            <tbody>
                ${events.map(event => `
                    <tr>
                        <td data-label="Time">${esc(formatTime(event.timestamp))}</td>
                        <td data-label="Event">${esc(auditEventLabel(event))}</td>
                        <td data-label="${t('label_user')}">${esc(event.user_nickname || event.user_email || 'Unknown')}</td>
                        <td data-label="Target/Data">${esc(auditTarget(event))}</td>
                        <td data-label="Risk">${permissionTag(event.sensitivity || 'none', event.sensitivity === 'high' ? 'high' : (event.sensitivity && event.sensitivity !== 'none' ? 'medium' : 'low'))}</td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    ` : `<div class="preview" style="padding:14px">${t('audit_no_events')}</div>`;
}

function enterpriseStatusTag(label, enabled) {
    return `<span class="tag ${enabled ? 'low' : 'medium'}">${esc(label)}: ${enabled ? 'enabled' : 'disabled'}</span>`;
}

function renderKeyValues(targetId, rows) {
    const target = document.getElementById(targetId);
    if (!target) return;
    target.innerHTML = `
        <div class="enterprise-kv">
            ${rows.map(([label, value]) => `
                <div>
                    <span>${esc(label)}</span>
                    <strong>${esc(value)}</strong>
                </div>
            `).join('')}
        </div>
    `;
}

function renderEnterpriseAdmin(payload) {
    latestEnterprise = payload || null;
    const enterprise = payload || {};
    const edition = enterprise.edition || {};
    const caps = edition.capabilities || {};
    const tags = document.getElementById('enterprise-status-tags');
    if (tags) {
        tags.innerHTML = [
            enterpriseStatusTag('edition', Boolean(edition.is_enterprise)),
            enterpriseStatusTag('policy packs', Boolean(enterprise.admin_policies?.enabled)),
            enterpriseStatusTag('siem', Boolean(enterprise.siem_export?.enabled)),
        ].join('');
    }

    const grid = document.getElementById('enterprise-capability-status');
    if (grid) {
        const entries = Object.keys(caps).length ? Object.entries(caps) : [];
        grid.innerHTML = entries.length ? entries.map(([name, enabled]) => `
            <div class="enterprise-cap-card ${enabled ? 'on' : 'off'}">
                <i class="ti ${enabled ? 'ti-circle-check' : 'ti-lock'}"></i>
                <span>${esc(name.replaceAll('_', ' '))}</span>
                <strong>${enabled ? 'enabled' : 'disabled'}</strong>
            </div>
        `).join('') : `<div class="preview" style="padding:14px">Capability status unavailable.</div>`;
    }

    const policies = enterprise.admin_policies || {};
    renderKeyValues('enterprise-admin-policies', [
        ['Capability', policies.capability || 'admin_policy_packs'],
        ['Enabled', Boolean(policies.enabled)],
        ['Enforced', Boolean(policies.enforced)],
        ['Base roles', (policies.effective_policy?.base_roles || []).join(', ')],
        ['Local file access', policies.effective_policy?.local_file_access || 'approval-token gated'],
        ['Package install', policies.effective_policy?.package_install || 'admin-only'],
        ['Note', policies.note || 'Community features remain available.'],
    ]);

    const org = enterprise.organization_settings || {};
    renderKeyValues('enterprise-org-settings', [
        ['Workspaces', (org.community_baseline?.workspaces || []).join(', ')],
        ['Roles', (org.community_baseline?.roles || []).join(', ')],
        ['Data isolation', org.community_baseline?.data_isolation || 'single-tenant local storage'],
        ['Governance enabled', Object.values(org.governance_capabilities || {}).filter(Boolean).length],
        ['Note', org.note || 'Enterprise governance is an extension point.'],
    ]);

    const audit = enterprise.audit_export || {};
    renderKeyValues('enterprise-audit-export', [
        ['Local export', audit.local_export?.available ? 'available' : 'unavailable'],
        ['Endpoint', audit.local_export?.endpoint || '/admin/security/export'],
        ['Formats', (audit.local_export?.formats || []).join(', ')],
        ['SIEM streaming', audit.siem_streaming?.enabled ? 'enabled' : 'disabled'],
        ['Retention', audit.compliance_retention?.enabled ? 'enabled' : 'disabled'],
    ]);

    const siem = enterprise.siem_export || {};
    renderKeyValues('enterprise-siem-export', [
        ['Capability', siem.capability || 'siem_export'],
        ['Enabled', Boolean(siem.enabled)],
        ['Streamed', Boolean(siem.streamed)],
        ['Destination', siem.destination || 'not configured'],
    ]);
    const preview = document.getElementById('enterprise-siem-preview');
    if (preview) preview.textContent = JSON.stringify(siem.preview_envelope || {}, null, 2);
}

async function refreshSiemPreview() {
    const res = await apiFetch('/admin/enterprise/siem-export', { headers: adminHeaders() });
    const data = res.ok ? await res.json() : {};
    renderEnterpriseAdmin({ ...(latestEnterprise || {}), siem_export: data });
}

function cellValue(value) {
    if (value === null || value === undefined) return '';
    if (Array.isArray(value)) return value.map(cellValue).filter(Boolean).join('; ');
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
}

function csvCell(value) {
    const text = cellValue(value);
    return `"${text.replace(/"/g, '""')}"`;
}

function tableToCsv(headers, rows) {
    return [
        headers.map(csvCell).join(','),
        ...rows.map(row => headers.map(header => csvCell(row[header])).join(','))
    ].join('\r\n');
}

function tableToTxt(headers, rows) {
    return [
        headers.join('\t'),
        ...rows.map(row => headers.map(header => cellValue(row[header])).join('\t'))
    ].join('\r\n');
}

function htmlCell(value, tag = 'td') {
    return `<${tag}>${esc(cellValue(value))}</${tag}>`;
}

function tableToExcelHtml(title, sections) {
    const tables = sections.map(section => `
        <h2>${esc(section.title)}</h2>
        <table border="1">
            <thead><tr>${section.headers.map(header => htmlCell(header, 'th')).join('')}</tr></thead>
            <tbody>
                ${section.rows.map(row => `<tr>${section.headers.map(header => htmlCell(row[header])).join('')}</tr>`).join('')}
            </tbody>
        </table>
    `).join('<br>');
    return `<!doctype html>
<html>
<head>
<meta charset="UTF-8">
<style>
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
table { border-collapse: collapse; }
th, td { padding: 6px 8px; mso-number-format:"\\@"; }
th { background: #efe8ff; font-weight: 700; }
</style>
</head>
<body>
<h1>${esc(title)}</h1>
${tables}
</body>
</html>`;
}

function downloadUtf8File(filename, content, mimeType) {
    const blob = new Blob([`\ufeff${content}`], { type: `${mimeType};charset=utf-8` });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function exportDateStamp() {
    const d = new Date();
    const pad = value => String(value).padStart(2, '0');
    return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}`;
}

function securityExportSections() {
    const riskHeaders = ['type', 'time', 'user', 'email', 'sensitivity', 'labels', 'preview'];
    const complianceHeaders = ['type', 'time', 'user', 'email', 'sensitivity', 'fields', 'preview'];
    const riskRows = (latestSensitivity?.risk_fields || []).map(item => ({
        type: 'risk',
        time: formatTime(item.timestamp || item.created_at || item.time),
        user: item.user_nickname || 'Unknown',
        email: item.user_email || '',
        sensitivity: item.sensitivity || 'none',
        labels: item.labels || [],
        preview: item.preview || ''
    }));
    const complianceRows = (latestSensitivity?.compliance_fields || []).map(item => ({
        type: 'compliance',
        time: formatTime(item.timestamp || item.created_at || item.time),
        user: item.user_nickname || 'Unknown',
        email: item.user_email || '',
        sensitivity: item.sensitivity || 'none',
        fields: item.compliance_fields || [],
        preview: item.preview || ''
    }));
    return [
        { title: currentLang === 'ko' ? '위험 필드' : 'Risk Fields', headers: riskHeaders, rows: riskRows },
        { title: currentLang === 'ko' ? '준수 필드' : 'Compliance Fields', headers: complianceHeaders, rows: complianceRows }
    ];
}

function auditExportSections() {
    const userHeaders = ['user', 'email', 'user_messages', 'assistant_messages', 'uploads', 'sensitive', 'high_risk', 'clear_events', 'delete_events', 'last_active'];
    const eventHeaders = ['time', 'event', 'event_type', 'user', 'email', 'target_data', 'sensitivity', 'labels', 'preview'];
    const userRows = (latestAudit?.per_user || []).map(user => ({
        user: user.nickname || user.email || 'Unknown',
        email: user.email || '',
        user_messages: user.user_messages || 0,
        assistant_messages: user.assistant_messages || 0,
        uploads: user.document_uploads || 0,
        sensitive: user.sensitive_events || 0,
        high_risk: user.high_sensitive_events || 0,
        clear_events: user.clear_events || 0,
        delete_events: user.delete_events || 0,
        last_active: formatTime(user.last_activity_at)
    }));
    const eventRows = (latestAudit?.recent_events || []).map(event => ({
        time: formatTime(event.timestamp),
        event: auditEventLabel(event),
        event_type: event.event_type || '',
        user: event.user_nickname || event.user_email || 'Unknown',
        email: event.user_email || '',
        target_data: auditTarget(event),
        sensitivity: event.sensitivity || 'none',
        labels: event.sensitive_labels || event.labels || event.compliance_fields || [],
        preview: event.content_preview || event.preview || ''
    }));
    return [
        { title: currentLang === 'ko' ? '사용자 사용량 및 위험도' : 'User Usage & Risk', headers: userHeaders, rows: userRows },
        { title: currentLang === 'ko' ? '감사 이벤트' : 'Audit Trail', headers: eventHeaders, rows: eventRows }
    ];
}

function flattenSections(sections) {
    const headers = Array.from(new Set(sections.flatMap(section => ['section', ...section.headers])));
    const rows = sections.flatMap(section => section.rows.map(row => ({ section: section.title, ...row })));
    return { headers, rows };
}

function toggleExportOptions(scope) {
    const options = document.getElementById(`${scope}-export-options`);
    if (!options) return;
    options.classList.toggle('open');
}

function exportAdminLogs(scope, format) {
    const isSecurity = scope === 'security';
    const title = isSecurity ? t('sensitivity_title') : t('audit_title');
    const sections = isSecurity ? securityExportSections() : auditExportSections();
    if (!sections.some(section => section.rows.length)) {
        alert(t('export_no_data'));
        return;
    }
    const stamp = exportDateStamp();
    const prefix = isSecurity ? 'lattice-security-monitoring' : 'lattice-audit-log';
    if (format === 'excel') {
        downloadUtf8File(`${prefix}-${stamp}.xls`, tableToExcelHtml(title, sections), 'application/vnd.ms-excel');
    } else if (format === 'csv') {
        const flat = flattenSections(sections);
        downloadUtf8File(`${prefix}-${stamp}.csv`, tableToCsv(flat.headers, flat.rows), 'text/csv');
    } else {
        const content = sections.map(section => [
            `[${section.title}]`,
            tableToTxt(section.headers, section.rows)
        ].join('\r\n')).join('\r\n\r\n');
        downloadUtf8File(`${prefix}-${stamp}.txt`, content, 'text/plain');
    }
    document.getElementById(`${scope}-export-options`)?.classList.remove('open');
}

function detectSsoTemplate(discoveryUrl = '') {
    const url = discoveryUrl.toLowerCase();
    if (url.includes('okta.com')) return 'okta';
    if (url.includes('login.microsoftonline.com')) return 'entra';
    return 'custom';
}

function updateSsoTemplateHelp() {
    const template = document.getElementById('sso-provider-template')?.value || 'custom';
    const help = document.getElementById('sso-template-help');
    if (!help) return;
    const key = template === 'okta' ? 'sso_okta_help' : template === 'entra' ? 'sso_entra_help' : 'sso_custom_help';
    help.textContent = t(key);
}

function applySsoTemplate() {
    const template = document.getElementById('sso-provider-template').value;
    const provider = document.getElementById('sso-provider-name');
    const discovery = document.getElementById('sso-discovery-url');
    const redirect = document.getElementById('sso-redirect-uri');
    if (template === 'okta') {
        if (!provider.value || provider.value === 'Microsoft Entra ID') provider.value = 'Okta';
        if (!discovery.value) discovery.value = 'https://your-domain.okta.com/oauth2/default/.well-known/openid-configuration';
    } else if (template === 'entra') {
        if (!provider.value || provider.value === 'Okta') provider.value = 'Microsoft Entra ID';
        if (!discovery.value) discovery.value = 'https://login.microsoftonline.com/{tenant-id}/v2.0/.well-known/openid-configuration';
    }
    if (!redirect.value) redirect.value = `${location.origin}/auth/sso/callback`;
    updateSsoTemplateHelp();
}

function renderSso(config) {
    latestSso = config || latestSso;
    if (!latestSso) return;
    document.getElementById('sso-provider-template').value = detectSsoTemplate(latestSso.discovery_url || '');
    document.getElementById('sso-provider-name').value = latestSso.provider_name || '';
    document.getElementById('sso-discovery-url').value = latestSso.discovery_url || '';
    document.getElementById('sso-client-id').value = latestSso.client_id || '';
    document.getElementById('sso-client-secret').value = '';
    document.getElementById('sso-redirect-uri').value = latestSso.redirect_uri || `${location.origin}/auth/sso/callback`;
    document.getElementById('sso-scopes').value = latestSso.scopes || 'openid email profile';
    document.getElementById('sso-save-status').textContent = latestSso.enabled ? t('sso_ready') : t('sso_not_ready');
    document.getElementById('sso-status-tags').innerHTML = [
        [latestSso.enabled ? 'low' : 'medium', latestSso.enabled ? t('sso_ready') : t('sso_not_ready')],
        [latestSso.secret_configured ? 'low' : 'medium', latestSso.secret_configured ? t('sso_secret_saved') : t('sso_secret_missing')],
        ['medium', latestSso.provider_name || 'OIDC']
    ].map(([tone, label]) => `<span class="tag ${tone}">${esc(label)}</span>`).join('');
    updateSsoTemplateHelp();
}

async function saveSso() {
    const status = document.getElementById('sso-save-status');
    status.textContent = t('vpc_saving');
    const payload = {
        enabled: true,
        provider_name: document.getElementById('sso-provider-name').value.trim(),
        discovery_url: document.getElementById('sso-discovery-url').value.trim(),
        client_id: document.getElementById('sso-client-id').value.trim(),
        client_secret: document.getElementById('sso-client-secret').value,
        redirect_uri: document.getElementById('sso-redirect-uri').value.trim(),
        scopes: document.getElementById('sso-scopes').value.trim() || 'openid email profile',
    };
    try {
        const res = await apiFetch('/admin/sso', {
            method: 'PATCH',
            headers: adminHeaders(),
            body: JSON.stringify(payload)
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || t('vpc_save_fail'));
        renderSso(data);
        status.textContent = t('sso_saved');
    } catch (e) {
        status.textContent = e.message || t('vpc_save_fail');
    }
}

async function saveVpc() {
    const payload = {
        provider: document.getElementById('vpc-provider').value.trim(),
        region: document.getElementById('vpc-region').value.trim(),
        cidr_block: document.getElementById('vpc-cidr').value.trim(),
        endpoint: document.getElementById('vpc-endpoint').value.trim(),
        vpn_status: document.getElementById('vpc-vpn').value.trim(),
        peering_status: document.getElementById('vpc-peering').value.trim(),
        private_subnets: document.getElementById('vpc-subnets').value.split(',').map(v => v.trim()).filter(Boolean),
        notes: document.getElementById('vpc-notes').value.trim()
    };
    const status = document.getElementById('vpc-save-status');
    status.textContent = t('vpc_saving');
    try {
        const res = await apiFetch('/admin/vpc', {
            method: 'PATCH',
            headers: adminHeaders(),
            body: JSON.stringify(payload)
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || t('vpc_save_fail'));
        fillVpcForm(data);
        status.textContent = t('vpc_saved');
        await loadDashboard();
    } catch (e) {
        status.textContent = e.message || t('vpc_save_fail');
    }
}

async function loadDashboard() {
    applyI18n();
    setSessionInfo();

    const access = document.getElementById('access-notice');
    access.style.display = 'none';

    try {
        const [healthRes, vpcRes, summaryRes, usersRes, sensitivityRes, inviteRes, statsRes, auditRes, ssoRes, enterpriseRes] = await Promise.all([
            apiFetch('/health'),
            apiFetch('/vpc/status'),
            apiFetch('/admin/summary', { headers: adminHeaders() }),
            apiFetch('/admin/users', { headers: adminHeaders() }),
            apiFetch('/admin/sensitivity', { headers: adminHeaders() }),
            apiFetch('/admin/invite-link', { headers: adminHeaders() }),
            apiFetch('/admin/stats', { headers: adminHeaders() }),
            apiFetch('/admin/audit', { headers: adminHeaders() }),
            apiFetch('/admin/sso', { headers: adminHeaders() }),
            apiFetch('/admin/enterprise', { headers: adminHeaders() }),
        ]);

        const health = healthRes.ok ? await healthRes.json() : null;
        const vpc = vpcRes.ok ? await vpcRes.json() : null;
        const summary = summaryRes.ok ? await summaryRes.json() : null;
        const users = usersRes.ok ? await usersRes.json() : [];
        const sensitivity = sensitivityRes.ok ? await sensitivityRes.json() : null;
        const invite = inviteRes.ok ? await inviteRes.json() : null;
        const stats = statsRes.ok ? await statsRes.json() : null;
        const audit = auditRes.ok ? await auditRes.json() : null;
        const sso = ssoRes.ok ? await ssoRes.json() : null;
        const enterprise = enterpriseRes.ok ? await enterpriseRes.json() : null;

        renderSummary(health, summary, vpc);
        fillVpcForm(vpc);
        renderUsers(users);
        renderPermissions(users);
        renderSensitivity(sensitivity);
        renderAudit(audit);
        renderSso(sso);
        renderEnterpriseAdmin(enterprise);

        if (invite) {
            document.getElementById('invite-link-input').value = invite.invite_url;
            document.getElementById('invite-gate-info').textContent = invite.gate_enabled
                ? `${t('invite_gate_active')} - ${invite.invite_code}`
                : t('invite_gate_inactive');
        }
        if (stats) renderActivityChart(stats.daily);

        const failedSections = [];
        if (!summaryRes.ok) failedSections.push(t('section_summary'));
        if (!usersRes.ok) failedSections.push(t('section_users'));
        if (!sensitivityRes.ok) failedSections.push(t('section_sensitivity'));
        if (!auditRes.ok) failedSections.push(t('section_audit'));
        if (!ssoRes.ok) failedSections.push(t('section_sso'));
        if (!enterpriseRes.ok) failedSections.push('Enterprise');

        if (failedSections.length) {
            access.style.display = 'block';
            access.textContent = summaryRes.status === 403
                ? t('err_no_admin')
                : `${t('err_partial')} ${failedSections.join(', ')}`;
        }
    } catch (e) {
        access.style.display = 'block';
        access.textContent = !navigator.onLine
            ? t('err_network')
            : (e.message || t('err_load'));
    }
}

async function handleUserAction(event) {
    const btn = event.target.closest('button[data-action]');
    if (!btn) return;
    const action = btn.dataset.action;
    const email = btn.dataset.email;
    if (!email) return;
    const encodedEmail = encodeURIComponent(email);
    if (action === 'role') {
        await apiFetch(`/admin/users/${encodedEmail}`, {
            method: 'PATCH',
            headers: adminHeaders(),
            body: JSON.stringify({ role: btn.dataset.nextRole })
        });
        await loadDashboard();
    } else if (action === 'disable') {
        await apiFetch(`/admin/users/${encodedEmail}`, {
            method: 'PATCH',
            headers: adminHeaders(),
            body: JSON.stringify({ disabled: btn.dataset.disabled === 'true' })
        });
        await loadDashboard();
    } else if (action === 'delete') {
        if (!confirm(`'${email}' ${t('confirm_delete')}`)) return;
        await apiFetch(`/admin/users/${encodedEmail}`, {
            method: 'DELETE',
            headers: adminHeaders()
        });
        await loadDashboard();
    }
}

async function logout() {
    try {
        await apiFetch('/logout', { method: 'POST' });
    } catch (e) {}
    localStorage.removeItem('ltcai_user_email');
    localStorage.removeItem('ltcai_user_nickname');
    localStorage.removeItem('ltcai_is_admin');
    window.location.href = '/';
}

restoreSessionFromQuery();
applyI18n();
initAdminNav();

document.getElementById('refresh-btn').addEventListener('click', loadDashboard);
document.getElementById('save-vpc-btn').addEventListener('click', saveVpc);
document.getElementById('logout-btn').addEventListener('click', logout);
document.getElementById('copy-invite-btn').addEventListener('click', copyInviteLink);
document.getElementById('user-table-wrap').addEventListener('click', handleUserAction);
document.getElementById('permission-table-wrap').addEventListener('click', handleUserAction);
document.getElementById('sso-provider-template').addEventListener('change', applySsoTemplate);
document.getElementById('save-sso-btn').addEventListener('click', saveSso);
document.getElementById('test-sso-btn').addEventListener('click', () => {
    window.location.href = `${API_BASE}/auth/sso/login`;
});
document.getElementById('refresh-siem-btn')?.addEventListener('click', () => refreshSiemPreview().catch(e => alert(String(e))));
document.getElementById('security-export-toggle')?.addEventListener('click', () => toggleExportOptions('security'));
document.getElementById('audit-export-toggle')?.addEventListener('click', () => toggleExportOptions('audit'));
document.querySelectorAll('[data-export-scope][data-export-format]').forEach(btn => {
    btn.addEventListener('click', () => exportAdminLogs(btn.dataset.exportScope, btn.dataset.exportFormat));
});

// ── Security & Audit Command Center (피드백 #5) ─────────────────────────────

function ccEscape(value) {
    if (value === null || value === undefined) return '';
    const str = String(value);
    return str
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

const CC_CARD_LABELS = {
    events_today: '오늘 이벤트',
    high_risk_events: 'High Risk',
    risky_chats: '위험 채팅',
    risky_files: '위험 파일',
    secret_blocks: 'Secret 차단',
    external_blocks: '외부 전송 차단',
    admin_raw_views: '관리자 원문 조회',
    review_required: '검토 필요',
};

let ccUserChart = null;
let ccFieldChart = null;

async function ccFetchJson(path) {
    try {
        const res = await apiFetch(path, { headers: adminHeaders() });
        if (!res.ok) {
            console.warn('Security CC fetch failed', path, res.status);
            return null;
        }
        return await res.json();
    } catch (e) {
        console.warn('Security CC fetch error', path, e);
        return null;
    }
}

function renderCcCards(overview) {
    const root = document.getElementById('security-cc-cards');
    if (!root || !overview || !overview.cards) return;
    const html = Object.entries(overview.cards).map(([key, value]) => `
        <div class="audit-card">
            <div class="audit-card-label">${ccEscape(CC_CARD_LABELS[key] || key)}</div>
            <div class="audit-card-value">${ccEscape(value)}</div>
        </div>
    `).join('');
    root.innerHTML = html;
}

function renderCcUsersTable(users) {
    const wrap = document.getElementById('security-cc-users');
    if (!wrap) return;
    if (!users || users.length === 0) {
        wrap.innerHTML = '<div class="preview" style="padding:14px">표시할 사용자가 없습니다.</div>';
        return;
    }
    const rows = users.slice(0, 25).map(u => `
        <tr data-cc-user="${ccEscape(u.email)}" style="cursor:pointer">
            <td data-label="사용자">${ccEscape(u.user)}</td>
            <td data-label="총 채팅">${ccEscape(u.total_chats)}</td>
            <td data-label="준수 채팅" style="color:#2c8a3f">${ccEscape(u.compliant_chats)}</td>
            <td data-label="위험 채팅" style="color:#b13030">${ccEscape(u.risky_chats)}</td>
            <td data-label="총 파일">${ccEscape(u.uploaded_files)}</td>
            <td data-label="준수 파일" style="color:#2c8a3f">${ccEscape(u.compliant_files)}</td>
            <td data-label="위험 파일" style="color:#b13030">${ccEscape(u.risky_files)}</td>
            <td data-label="High">${ccEscape(u.high_risk_events)}</td>
            <td data-label="위험률">${ccEscape(u.risk_rate)}%</td>
            <td data-label="마지막 활동">${ccEscape((u.last_activity_at || '').slice(0, 19).replace('T', ' '))}</td>
        </tr>
    `).join('');
    wrap.innerHTML = `
        <table class="data-table">
            <thead><tr>
                <th>사용자</th><th>총 채팅</th><th>준수 채팅</th><th>위험 채팅</th>
                <th>총 파일</th><th>준수 파일</th><th>위험 파일</th>
                <th>High</th><th>위험률</th><th>마지막 활동</th>
            </tr></thead>
            <tbody>${rows}</tbody>
        </table>`;
    wrap.querySelectorAll('tr[data-cc-user]').forEach(tr => {
        tr.addEventListener('click', () => ccShowUserDrillDown(tr.dataset.ccUser));
    });
}

function renderCcUserChart(users) {
    const canvas = document.getElementById('security-cc-user-chart');
    if (!canvas || typeof Chart === 'undefined') return;
    const top = users.slice(0, 8);
    const labels = top.map(u => u.user);
    if (ccUserChart) { ccUserChart.destroy(); ccUserChart = null; }
    ccUserChart = new Chart(canvas, {
        type: 'bar',
        data: {
            labels,
            datasets: [
                { label: '준수 채팅', backgroundColor: '#5cb874', data: top.map(u => u.compliant_chats) },
                { label: '위험 채팅', backgroundColor: '#e8636e', data: top.map(u => u.risky_chats) },
                { label: '준수 파일', backgroundColor: '#7fb5e6', data: top.map(u => u.compliant_files) },
                { label: '위험 파일', backgroundColor: '#d94c4c', data: top.map(u => u.risky_files) },
            ]
        },
        options: {
            responsive: true,
            scales: { x: { stacked: true }, y: { stacked: true } },
            plugins: { legend: { position: 'bottom' } },
        }
    });
}

function renderCcFieldChart(overview) {
    const canvas = document.getElementById('security-cc-field-chart');
    const legend = document.getElementById('security-cc-field-legend');
    if (!canvas || typeof Chart === 'undefined') return;
    const counts = overview?.field_counts || {};
    const labels = Object.keys(counts);
    const data = labels.map(l => counts[l]);
    if (ccFieldChart) { ccFieldChart.destroy(); ccFieldChart = null; }
    if (labels.length === 0) {
        if (legend) legend.textContent = '감지된 민감정보 유형이 없습니다.';
        return;
    }
    ccFieldChart = new Chart(canvas, {
        type: 'doughnut',
        data: { labels, datasets: [{ data, backgroundColor: ['#e8636e','#7fb5e6','#f0b14a','#5cb874','#9b6cd0','#3da9b6','#d18cd4','#a3a3a3'] }] },
        options: { plugins: { legend: { position: 'bottom' } } }
    });
    if (legend) {
        legend.innerHTML = labels.map((l, i) => `${ccEscape(l)}: ${ccEscape(data[i])}`).join(' · ');
    }
}

async function ccShowUserDrillDown(email) {
    const data = await ccFetchJson(`/admin/security/events?user=${encodeURIComponent(email)}`);
    const wrap = document.getElementById('security-cc-timeline');
    if (!wrap) return;
    const events = (data && data.events) || [];
    if (!events.length) {
        wrap.innerHTML = `<div class="preview" style="padding:14px">${ccEscape(email)} 사용자에 대한 이벤트가 없습니다.</div>`;
        return;
    }
    const rows = events.slice(0, 40).map(e => `
        <tr>
            <td>${ccEscape((e.timestamp || '').slice(0, 19).replace('T', ' '))}</td>
            <td>${ccEscape(e.event_type || '')}</td>
            <td>${ccEscape(e.sensitivity || 'none')}</td>
            <td>${ccEscape((e.sensitive_labels || []).join(', '))}</td>
            <td>${ccEscape((e.content_preview || '').slice(0, 80))}</td>
        </tr>
    `).join('');
    wrap.innerHTML = `
        <div style="margin-bottom:8px;color:var(--muted-text);font-size:12px">${ccEscape(email)} 사용자의 보안 이벤트 ${events.length}건</div>
        <table class="data-table">
            <thead><tr><th>시각</th><th>유형</th><th>민감도</th><th>라벨</th><th>마스킹 preview</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>`;
}

async function loadSecurityCommandCenter() {
    const [overview, usersResp, eventsResp, filesResp] = await Promise.all([
        ccFetchJson('/admin/security/overview'),
        ccFetchJson('/admin/security/users'),
        ccFetchJson('/admin/security/events?limit=50'),
        ccFetchJson('/admin/security/files'),
    ]);

    if (overview) {
        renderCcCards(overview);
        renderCcFieldChart(overview);
    }
    if (usersResp && Array.isArray(usersResp.users)) {
        renderCcUsersTable(usersResp.users);
        renderCcUserChart(usersResp.users);
    }
    if (eventsResp && Array.isArray(eventsResp.events)) {
        const chats = eventsResp.events.filter(e => (e.sensitivity || 'none') !== 'none' && e.event_type === 'chat_message').slice(0, 20);
        const chatWrap = document.getElementById('security-cc-chats');
        if (chatWrap) {
            chatWrap.innerHTML = chats.length ? `
                <table class="data-table">
                    <thead><tr><th>시각</th><th>사용자</th><th>민감도</th><th>라벨</th><th>마스킹 preview</th></tr></thead>
                    <tbody>${chats.map(e => `
                        <tr>
                            <td>${ccEscape((e.timestamp || '').slice(0, 19).replace('T', ' '))}</td>
                            <td>${ccEscape(e.user_nickname || e.user_email || 'Unknown')}</td>
                            <td>${ccEscape(e.sensitivity)}</td>
                            <td>${ccEscape((e.sensitive_labels || []).join(', '))}</td>
                            <td>${ccEscape((e.content_preview || '').slice(0, 100))}</td>
                        </tr>`).join('')}
                    </tbody>
                </table>` : '<div class="preview" style="padding:14px">감지된 민감 채팅이 없습니다.</div>';
        }
        const timelineWrap = document.getElementById('security-cc-timeline');
        if (timelineWrap && !timelineWrap.querySelector('table')) {
            const rows = eventsResp.events.slice(0, 30).map(e => `
                <tr>
                    <td>${ccEscape((e.timestamp || '').slice(0, 19).replace('T', ' '))}</td>
                    <td>${ccEscape(e.event_type || '')}</td>
                    <td>${ccEscape(e.user_nickname || e.user_email || 'Unknown')}</td>
                    <td>${ccEscape(e.sensitivity || 'none')}</td>
                </tr>
            `).join('');
            timelineWrap.innerHTML = rows ? `
                <table class="data-table">
                    <thead><tr><th>시각</th><th>유형</th><th>사용자</th><th>민감도</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table>` : '<div class="preview" style="padding:14px">감사 이벤트가 없습니다.</div>';
        }
    }
    if (filesResp && Array.isArray(filesResp.files)) {
        const files = filesResp.files.filter(f => (f.sensitivity || 'none') !== 'none' || (f.sensitive_labels || []).length > 0).slice(0, 20);
        const fileWrap = document.getElementById('security-cc-files');
        if (fileWrap) {
            fileWrap.innerHTML = files.length ? `
                <table class="data-table">
                    <thead><tr><th>파일</th><th>업로드 사용자</th><th>민감도</th><th>라벨</th><th>크기</th></tr></thead>
                    <tbody>${files.map(f => `
                        <tr>
                            <td>${ccEscape(f.filename || f.file_id)}</td>
                            <td>${ccEscape(f.user_nickname || f.user_email || 'Unknown')}</td>
                            <td>${ccEscape(f.sensitivity || 'none')}</td>
                            <td>${ccEscape((f.sensitive_labels || []).join(', '))}</td>
                            <td>${ccEscape(f.bytes || '')}</td>
                        </tr>`).join('')}
                    </tbody>
                </table>` : '<div class="preview" style="padding:14px">위험 등급 파일이 없습니다.</div>';
        }
    }
}

async function ccLoadRaw(scope) {
    const pre = document.getElementById('security-cc-raw');
    if (!pre) return;
    pre.textContent = '불러오는 중...';
    try {
        const res = await apiFetch(`/admin/security/raw?scope=${encodeURIComponent(scope)}`, { headers: adminHeaders() });
        if (!res.ok) { pre.textContent = `요청 실패 (HTTP ${res.status})`; return; }
        const text = await res.text();
        try {
            pre.textContent = JSON.stringify(JSON.parse(text), null, 2);
        } catch (_) {
            pre.textContent = text;
        }
    } catch (e) {
        pre.textContent = String(e);
    }
}

async function ccExport(scope, format) {
    try {
        const res = await apiFetch('/admin/security/export', {
            method: 'POST',
            headers: { ...adminHeaders(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ scope, format }),
        });
        if (!res.ok) {
            alert('보안 리포트 추출 실패 (HTTP ' + res.status + ')');
            return;
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `security_${scope}.${format === 'xlsx' ? 'xlsx' : format}`;
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (e) {
        alert(String(e));
    }
}

document.getElementById('security-cc-export-toggle')?.addEventListener('click', () => {
    const opts = document.getElementById('security-cc-export-options');
    if (opts) opts.classList.toggle('open');
});
document.querySelectorAll('[data-cc-scope][data-cc-format]').forEach(btn => {
    btn.addEventListener('click', () => ccExport(btn.dataset.ccScope, btn.dataset.ccFormat));
});
document.querySelectorAll('[data-cc-raw]').forEach(btn => {
    btn.addEventListener('click', () => ccLoadRaw(btn.dataset.ccRaw));
});

// 보안 탭 진입 시 자동 로드
document.querySelectorAll('[data-admin-nav="security"]').forEach(el => {
    el.addEventListener('click', () => { setTimeout(loadSecurityCommandCenter, 50); });
});
// 메뉴 셀렉터를 모를 수도 있으니 hash 변경 시에도 시도
window.addEventListener('hashchange', () => {
    if (location.hash.indexOf('security') >= 0) loadSecurityCommandCenter();
});

loadDashboard();
// 보안 콘솔도 첫 진입 시 로드 시도 (실패해도 무해)
setTimeout(loadSecurityCommandCenter, 600);
