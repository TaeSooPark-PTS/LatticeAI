/* Lattice AI — account.html scripts */

const API_BASE = window.location.protocol === 'file:' ? 'http://localhost:4825' : '';
        function apiFetch(path, opts = {}) {
            const headers = { ...(opts.headers || {}) };
            return fetch(API_BASE + path, { credentials: 'include', ...opts, headers });
        }

        // ── i18n ──────────────────────────────────────────────
        const I18N = {
            ko: {
                login_title: 'Lattice AI', login_sub: '내 PC에서 시작하는<br>개인 AI 워크스페이스',
                ph_email: '이메일 주소', ph_pw: '비밀번호', ph_new_pw: '비밀번호 (4자 이상)',
                ph_pw_confirm: '비밀번호 확인', ph_name: '이름', ph_nick: '닉네임',
                btn_login: '로그인', btn_register: '가입하기',
                no_account: '계정이 없으신가요?', go_register: '회원가입',
                have_account: '이미 계정이 있나요?', go_login: '로그인',
                reg_title: '계정 만들기', reg_sub: 'Lattice AI 워크스페이스에 참여하세요',
                err_pw_mismatch: '비밀번호가 일치하지 않습니다.',
                err_fill: '모든 항목을 입력해주세요.',
                err_login_fail: '이메일 또는 비밀번호가 틀렸습니다.',
                err_server: '서버 연결 실패',
                sso_divider: '조직 계정으로 로그인', sso_btn: '로 로그인',
                ms_sso: 'Microsoft Entra ID로 계속하기', okta_sso: 'Okta SSO로 계속하기',
                local_start: '로컬 계정으로 시작', help: '도움말', privacy: '개인정보 처리방침',
                language_btn: '🌐 한국어',
                sso_unavailable: 'SSO가 아직 설정되지 않았습니다. 로컬 계정으로 시작하거나 관리자에게 문의하세요.',
            },
            en: {
                login_title: 'Lattice AI', login_sub: 'Your personal AI workspace<br>starts on this PC',
                ph_email: 'Email address', ph_pw: 'Password', ph_new_pw: 'Password (min. 4 chars)',
                ph_pw_confirm: 'Confirm password', ph_name: 'Full name', ph_nick: 'Nickname',
                btn_login: 'Log in', btn_register: 'Sign up',
                no_account: "Don't have an account?", go_register: 'Sign up',
                have_account: 'Already have an account?', go_login: 'Log in',
                reg_title: 'Create Account', reg_sub: 'Join the Lattice AI workspace',
                err_pw_mismatch: 'Passwords do not match.',
                err_fill: 'Please fill in all fields.',
                err_login_fail: 'Invalid email or password.',
                err_server: 'Server connection failed',
                sso_divider: 'Sign in with organization account', sso_btn: 'Sign in with',
                ms_sso: 'Continue with Microsoft Entra ID', okta_sso: 'Continue with Okta SSO',
                local_start: 'Start with a local account', help: 'Help', privacy: 'Privacy Policy',
                language_btn: '🌐 English',
                sso_unavailable: 'SSO is not configured yet. Start with a local account or contact your administrator.',
            }
        };

        let lang = localStorage.getItem('ltcai_lang') || 'ko';
        function t(k) { return (I18N[lang] || I18N.ko)[k] || k; }

        function applyI18n() {
            document.documentElement.lang = lang;
            document.getElementById('lang-btn').textContent = t('language_btn');
            document.getElementById('login-title').textContent = t('login_title');
            document.getElementById('login-sub').innerHTML = t('login_sub');
            document.getElementById('reg-title').textContent = t('reg_title');
            document.getElementById('reg-sub').textContent = t('reg_sub');
            document.getElementById('login-btn').textContent = t('btn_login');
            document.getElementById('reg-btn').textContent = t('btn_register');
            document.getElementById('go-register-link').textContent = t('go_register');
            document.getElementById('have-account-text').textContent = t('have_account');
            document.getElementById('go-login-link').textContent = t('go_login');
            document.getElementById('login-email').placeholder = t('ph_email');
            document.getElementById('login-pw').placeholder = t('ph_pw');
            document.getElementById('reg-email').placeholder = t('ph_email');
            document.getElementById('reg-pw').placeholder = t('ph_new_pw');
            document.getElementById('reg-pw2').placeholder = t('ph_pw_confirm');
            document.getElementById('reg-name').placeholder = t('ph_name');
            document.getElementById('reg-nick').placeholder = t('ph_nick');
            document.getElementById('sso-divider-text').textContent = t('sso_divider');
            document.getElementById('sso-ms-label').textContent = t('ms_sso');
            document.getElementById('sso-okta-label').textContent = t('okta_sso');
            document.getElementById('local-start-label').textContent = t('local_start');
            document.getElementById('help-link').textContent = t('help');
            document.getElementById('privacy-link').textContent = t('privacy');
            ['ko', 'en'].forEach(l => {
                const el = document.getElementById(`opt-${l}`);
                if (el) el.classList.toggle('active', l === lang);
            });
        }

        async function initSSO() {
            try {
                const res = await apiFetch('/auth/sso/config');
                if (!res.ok) return;
                const cfg = await res.json();
                if (cfg.enabled) {
                    window._ssoEnabled = true;
                    window._ssoProviderName = cfg.provider_name;
                    applyI18n();
                }
            } catch {}
        }

        function doSSOLogin(provider) {
            if (!window._ssoEnabled) {
                setMsg('login-msg', t('sso_unavailable'));
                return;
            }
            if (provider) sessionStorage.setItem('ltcai_sso_provider_hint', provider);
            window.location.href = '/auth/sso/login';
        }

        function togglePasswordVisibility() {
            const input = document.getElementById('login-pw');
            input.type = input.type === 'password' ? 'text' : 'password';
        }

        function toggleLang() {
            const m = document.getElementById('lang-menu');
            m.classList.toggle('open');
        }

        function setLang(l) {
            lang = l;
            localStorage.setItem('ltcai_lang', l);
            document.getElementById('lang-menu').classList.remove('open');
            applyI18n();
        }

        document.addEventListener('click', e => {
            if (!e.target.closest('.lang-wrap'))
                document.getElementById('lang-menu').classList.remove('open');
        });

        function showSection(name) {
            document.getElementById('login-section').style.display = name === 'login' ? '' : 'none';
            document.getElementById('register-section').style.display = name === 'register' ? '' : 'none';
            document.getElementById('login-msg').textContent = '';
            document.getElementById('reg-msg').textContent = '';
        }

        function setMsg(id, text, ok = false) {
            const el = document.getElementById(id);
            el.textContent = text;
            el.className = 'msg' + (ok ? ' ok' : '');
        }

        function requestSetupAfterLogin() {
            try {
                sessionStorage.setItem('ltcai_force_setup_after_login', 'true');
            } catch (_) {}
        }

        async function doLogin() {
            const email = document.getElementById('login-email').value.trim();
            const password = document.getElementById('login-pw').value;
            if (!email || !password) { setMsg('login-msg', t('err_fill')); return; }
            const btn = document.getElementById('login-btn');
            btn.disabled = true;
            btn.textContent = '...';
            try {
                const res = await apiFetch('/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email, password })
                });
                if (res.ok) {
                    const data = await res.json();
                    localStorage.setItem('ltcai_user_email', data.email);
                    localStorage.setItem('ltcai_user_nickname', data.nickname || data.name || data.email);
                    localStorage.setItem('ltcai_is_admin', data.is_admin ? 'true' : 'false');
                    requestSetupAfterLogin();
                    window.location.href = '/chat?setup=1';
                } else {
                    const data = await res.json().catch(() => ({}));
                    setMsg('login-msg', data.detail || t('err_login_fail'));
                    btn.disabled = false;
                    btn.textContent = t('btn_login');
                }
            } catch {
                setMsg('login-msg', t('err_server'));
                btn.disabled = false;
                btn.textContent = t('btn_login');
            }
        }

        async function doRegister() {
            const email = document.getElementById('reg-email').value.trim();
            const pw = document.getElementById('reg-pw').value;
            const pw2 = document.getElementById('reg-pw2').value;
            const name = document.getElementById('reg-name').value.trim();
            const nickname = document.getElementById('reg-nick').value.trim();
            if (!email || !pw || !name || !nickname) { setMsg('reg-msg', t('err_fill')); return; }
            if (pw !== pw2) { setMsg('reg-msg', t('err_pw_mismatch')); return; }
            const btn = document.getElementById('reg-btn');
            btn.disabled = true;
            btn.textContent = '...';
            try {
                const res = await apiFetch('/register', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email, password: pw, name, nickname })
                });
                if (res.ok) {
                    setMsg('reg-msg', lang === 'ko' ? '가입 완료! 로그인 중...' : 'Registered! Logging in...', true);
                    await apiFetch('/login', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ email, password: pw })
                    }).then(r => r.ok ? r.json() : null).then(data => {
                        if (data) {
                            localStorage.setItem('ltcai_user_email', data.email);
                            localStorage.setItem('ltcai_user_nickname', data.nickname || data.name || data.email);
                            localStorage.setItem('ltcai_is_admin', data.is_admin ? 'true' : 'false');
                            requestSetupAfterLogin();
                            window.location.href = '/chat?setup=1';
                        }
                    });
                } else {
                    const data = await res.json().catch(() => ({}));
                    setMsg('reg-msg', data.detail || '가입 실패');
                    btn.disabled = false;
                    btn.textContent = t('btn_register');
                }
            } catch {
                setMsg('reg-msg', t('err_server'));
                btn.disabled = false;
                btn.textContent = t('btn_register');
            }
        }

        // If already logged in, skip to chat
        apiFetch('/account/profile').then(r => {
            if (r.ok) window.location.href = '/chat';
        }).catch(() => {});

        initSSO();

        // Handle invite code in URL
        const urlCode = new URLSearchParams(window.location.search).get('code');
        if (urlCode) {
            document.getElementById('reg-email').focus();
            showSection('register');
        }

        applyI18n();
