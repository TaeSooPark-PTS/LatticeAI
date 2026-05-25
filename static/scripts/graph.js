/* Lattice AI — graph.html scripts */

const API_BASE = window.location.protocol === 'file:' ? 'http://localhost:4825' : '';

    const G18N = {
      ko: {
        nav_home: '홈', nav_graph: '지식 그래프', nav_chat: '대화', nav_files: '파일', nav_code: '코드', nav_settings: '설정',
        project: '프로젝트', search_title: '그래프 탐색', search_sub: '주제, 파일, 대화, 결정, 작업을 검색하세요.',
        ready: '준비됨', search_ph: '주제, 파일, 대화로 검색...', clear_search: '검색 지우기',
        search_results: '{n}개 결과',
        search_empty: '검색 결과는 여기에 표시됩니다. 키워드를 입력하면 서버 검색 결과를 불러오고, 항목을 누르면 해당 노드로 바로 이동합니다.',
        search_no_results: '일치하는 노드를 찾지 못했습니다. 더 구체적인 주제어, 파일명, 대화 제목으로 다시 시도해 보세요.',
        searching: '검색 중...', search_loading: '그래프 인덱스를 검색하는 중...',
        sidebar_eyebrow: '지식 그래프', sidebar_title: '지식 토폴로지',
        sidebar_sub: '주제의 크기는 중요도 기반으로, 선의 굵기와 색은 관계 종류와 강도를 반영합니다.',
        nodes: '노드', edges: '연결', relationship_legend: '관계 범례', node_types: '노드 유형',
        local_sources: '지식 소스', local_notice: 'Lattice AI는 사용자가 선택한 폴더만 AI 지식으로 변환합니다.',
        local_path_ph: '폴더 경로 입력...', local_roots: '드라이브 선택', local_tree: '폴더 구조 확인',
        local_audit: '안전 검사', local_index: '지식 그래프 만들기', local_ocr: '이미지 글자 인식',
        local_watch: '자동 감지 켜기', local_permission: '권한 승인', local_sources_empty: '아직 추가된 지식 소스가 없습니다.',
        local_indexed: '지식 그래프 생성 완료', local_watch_unavailable: '자동 감지는 watchdog 설치 후 작동합니다.',
        detail_empty: '노드를 클릭하면 요약, 중요도, 연결 강도, 메타데이터를 볼 수 있습니다. 검색 패널에서는 서버 검색 결과를 기준으로 더 정확하게 이동할 수 있습니다.',
        detail_empty_short: '노드를 클릭하면 요약, 중요도, 메타데이터를 볼 수 있습니다.',
        refresh: '새로고침', error: '오류', graph_load_fail: '그래프를 불러오지 못했습니다.', graph_refresh_fail: '그래프를 새로고침하지 못했습니다.',
        no_node_types: '아직 노드 유형이 없습니다.', no_relationships: '아직 관계가 없습니다.',
        open_in_chat: '채팅에서 열기', today: '오늘', day_ago: '1일 전', days_ago: '{n}일 전', months_ago: '{n}개월 전', years_ago: '{n}년 전',
      },
      en: {
        nav_home: 'Home', nav_graph: 'Knowledge Graph', nav_chat: 'Chat', nav_files: 'Files', nav_code: 'Code', nav_settings: 'Settings',
        project: 'Project', search_title: 'Explore the graph', search_sub: 'Search topics, files, conversations, decisions, and tasks.',
        ready: 'Ready', search_ph: 'Search by topic, file, or conversation...', clear_search: 'Clear search',
        search_results: '{n} result(s)',
        search_empty: 'Search results appear here. Enter a keyword to load server results and jump directly to a node.',
        search_no_results: 'No matching nodes found. Try a more specific topic, filename, or conversation title.',
        searching: 'Searching...', search_loading: 'Searching graph index...',
        sidebar_eyebrow: 'Knowledge Graph', sidebar_title: 'Knowledge topology',
        sidebar_sub: 'Topic size follows importance; line width and color reflect relationship type and strength.',
        nodes: 'Nodes', edges: 'Edges', relationship_legend: 'Relationship legend', node_types: 'Node types',
        local_sources: 'Knowledge sources', local_notice: 'Lattice AI only turns folders you choose into AI knowledge.',
        local_path_ph: 'Enter a folder path...', local_roots: 'Drive picker', local_tree: 'Check folders',
        local_audit: 'Safety check', local_index: 'Build graph', local_ocr: 'Image text recognition',
        local_watch: 'Auto watch', local_permission: 'Approve access', local_sources_empty: 'No knowledge sources yet.',
        local_indexed: 'Knowledge graph built', local_watch_unavailable: 'Auto watch works after watchdog is installed.',
        detail_empty: 'Click a node to see its summary, importance, connection strength, and metadata. Search results can jump to more precise nodes.',
        detail_empty_short: 'Click a node to see its summary, importance, and metadata.',
        refresh: 'Refresh', error: 'Error', graph_load_fail: 'Could not load the graph.', graph_refresh_fail: 'Could not refresh the graph.',
        no_node_types: 'No node types yet.', no_relationships: 'No relationships yet.',
        open_in_chat: 'Open in chat', today: 'today', day_ago: '1 day ago', days_ago: '{n} days ago', months_ago: '{n} mo ago', years_ago: '{n} yr ago',
      }
    };

    let currentLang = localStorage.getItem('ltcai_lang') || 'ko';
    function t(key) { return (G18N[currentLang] || G18N.ko)[key] || key; }

    function applyI18n() {
      document.documentElement.lang = currentLang;
      const navLabels = ['nav_home', 'nav_graph', 'nav_chat', 'nav_files', 'nav_code', 'nav_settings'];
      document.querySelectorAll('.graph-rail nav a').forEach((link, index) => {
        const icon = link.querySelector('i')?.outerHTML || '';
        link.innerHTML = `${icon} ${t(navLabels[index])}`;
      });
      const projectLabel = document.querySelector('.rail-project span');
      if (projectLabel) projectLabel.textContent = t('project');
      document.querySelector('.search-title strong').textContent = t('search_title');
      document.querySelector('.search-title span').textContent = t('search_sub');
      searchInput.placeholder = t('search_ph');
      document.getElementById('clear-search-btn').title = t('clear_search');
      document.querySelector('.eyebrow').textContent = t('sidebar_eyebrow');
      document.querySelector('.sidebar-head h1').textContent = t('sidebar_title');
      document.querySelector('.sidebar-sub').textContent = t('sidebar_sub');
      document.querySelectorAll('.stat span')[0].textContent = t('nodes');
      document.querySelectorAll('.stat span')[1].textContent = t('edges');
      document.getElementById('local-source-label').textContent = t('local_sources');
      document.getElementById('edge-label').textContent = t('relationship_legend');
      document.getElementById('type-label').textContent = t('node_types');
      document.getElementById('refresh-btn').textContent = `↺ ${t('refresh')}`;
      const langBtn = document.getElementById('graph-lang-btn');
      if (langBtn) langBtn.textContent = `Language: ${currentLang === 'ko' ? '한국어' : 'English'}`;
      ['ko', 'en'].forEach(lang => {
        const el = document.getElementById(`graph-lang-${lang}`);
        if (el) el.classList.toggle('active', lang === currentLang);
      });
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
      setSearchIdleState(searchInput.value.trim() ? searchCountEl.textContent : t('ready'));
      renderSearchResults();
      renderTypeFilters(buildTypeCounts());
      renderEdgeLegend(buildEdgeCounts());
      renderLocalSources();
      showDetail(selected);
    }
    window.toggleLangMenu = toggleLangMenu;
    window.setLang = setLang;

    const TYPE_CONFIG = {
      Computer:     { color: '#14b8a6', label: 'Computer' },
      Drive:        { color: '#38bdf8', label: 'Drive' },
      Folder:       { color: '#f0a500', label: 'Folder' },
      Conversation: { color: '#9b8af0', label: 'Conversation' },
      Message:      { color: '#b8a9f5', label: 'Message' },
      AIResponse:   { color: '#6f42e8', label: 'AI Response' },
      File:         { color: '#5b9cf6', label: 'File' },
      Document:     { color: '#5b9cf6', label: 'Document' },
      CodeFile:     { color: '#22c55e', label: 'Code File' },
      Spreadsheet:  { color: '#059669', label: 'Spreadsheet' },
      SlideDeck:    { color: '#818cf8', label: 'Slide Deck' },
      Topic:        { color: '#7c3aed', label: 'Topic' },
      Concept:      { color: '#7c3aed', label: 'Concept' },
      Person:       { color: '#0d9488', label: 'Person' },
      Page:         { color: '#a78bfa', label: 'Page' },
      Slide:        { color: '#818cf8', label: 'Slide' },
      Sheet:        { color: '#059669', label: 'Sheet' },
      Image:        { color: '#d97706', label: 'Image' },
      ImageText:    { color: '#f97316', label: 'Image Text' },
      Decision:     { color: '#f59e0b', label: 'Decision' },
      Task:         { color: '#ec4899', label: 'Task' },
      ClearEvent:   { color: '#6366f1', label: 'Clear Event' },
      Event:        { color: '#8b5cf6', label: 'Event' },
    };

    const EDGE_CONFIG = {
      contains:        { color: '#7186c8', label: 'Contains', width: 1.3 },
      authored:        { color: '#20b8aa', label: 'Authored', width: 1.5 },
      uploaded:        { color: '#7db7ff', label: 'Uploaded', width: 1.5 },
      has_event:       { color: '#7a6ba8', label: 'Event', width: 1.2 },
      triggered:       { color: '#a77cff', label: 'Triggered', width: 1.2, dash: [5, 4] },
      mentions:        { color: '#aebcff', label: 'Mentions', width: 1.55 },
      discusses:       { color: '#c9b7ff', label: 'Discusses', width: 1.75 },
      implies:         { color: '#ff7db3', label: 'Implies', width: 1.55 },
      based_on:        { color: '#a77cff', label: 'Based on', width: 1.4, dash: [8, 4] },
      contains_signal: { color: '#f1c86d', label: 'Signal', width: 1.6 },
      has_page:        { color: '#7186c8', label: 'Page', width: 1.25 },
      has_slide:       { color: '#8fa3ff', label: 'Slide', width: 1.3 },
      has_sheet:       { color: '#20b8aa', label: 'Sheet', width: 1.3 },
      contains_image:  { color: '#f1c86d', label: 'Image', width: 1.35 },
      has_chunk:       { color: '#4e566f', label: 'Chunk', width: 0.9, dash: [2, 5] },
      '포함함':          { color: '#7186c8', label: 'Contains', width: 1.35 },
      '언급함':          { color: '#aebcff', label: 'Mentions', width: 1.45 },
      '관련됨':          { color: '#7f8f9d', label: 'Related', width: 1.3 },
    };

    const canvas = document.getElementById('graph');
    const ctx = canvas.getContext('2d');
    const detail = document.getElementById('detail');
    const tooltip = document.getElementById('tooltip');
    const searchInput = document.getElementById('search');
    const searchResultsEl = document.getElementById('search-results');
    const searchCountEl = document.getElementById('search-count');
    const localSourcePanel = document.getElementById('local-source-panel');

    let rawGraph = { nodes: [], edges: [] };
    let graph = { nodes: [], edges: [] };
    let hiddenTypes = new Set();
    let selected = null;
    let hovered = null;
    let dragging = null;
    let panning = null;
    let cam = { scale: 1, tx: 0, ty: 0 };
    let animFrameId = null;
    let width = 0;
    let height = 0;
    let searchResults = [];
    let searchResultIds = new Set();
    let searchAbortController = null;
    let searchDebounceId = null;
    let localState = {
      roots: [],
      sources: [],
      watch: null,
      selectedPath: '',
      tree: null,
      audit: null,
      includeOcr: false,
      watchEnabled: false,
      busy: false,
      status: '',
      error: '',
      pendingPermission: null,
    };

    function apiFetch(path, opts = {}) {
      return fetch(`${API_BASE}${path}`, {
        credentials: 'include',
        ...opts,
        headers: { ...(opts.headers || {}) },
      });
    }

    function clamp(value, min, max) {
      return Math.max(min, Math.min(max, value));
    }

    function escapeHtml(text) {
      return String(text || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
    }

    function formatCount(value) {
      return Number(value || 0).toLocaleString();
    }

    async function apiJson(path, payload) {
      return apiFetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
      });
    }

    async function loadLocalSources() {
      try {
        const [rootsRes, sourcesRes] = await Promise.all([
          apiFetch('/knowledge-graph/local/roots'),
          apiFetch('/knowledge-graph/local/sources'),
        ]);
        if (rootsRes.status === 401 || sourcesRes.status === 401) {
          window.location.href = '/account';
          return;
        }
        const rootsData = rootsRes.ok ? await rootsRes.json() : {};
        const sourcesData = sourcesRes.ok ? await sourcesRes.json() : {};
        localState.roots = Array.isArray(rootsData.roots) ? rootsData.roots : [];
        localState.sources = Array.isArray(sourcesData.sources) ? sourcesData.sources : [];
        localState.watch = sourcesData.watch || null;
        if (!localState.selectedPath && localState.roots[0]) {
          localState.selectedPath = localState.roots[0].path;
        }
        renderLocalSources();
      } catch (error) {
        localState.error = error.message;
        renderLocalSources();
      }
    }

    function renderLocalSources() {
      if (!localSourcePanel) return;
      const rootRows = localState.roots.slice(0, 8).map(root => {
        const active = root.path === localState.selectedPath ? 'active' : '';
        return `
          <button class="local-root-btn ${active}" onclick="selectLocalPath(decodeURIComponent('${encodeURIComponent(root.path)}'))" title="${escapeHtml(root.path)}">
            <i class="ti ${root.kind === 'drive' || root.kind === 'volume' ? 'ti-device-desktop' : 'ti-folder'}"></i>
            <span class="local-source-main">
              <strong>${escapeHtml(root.label || root.path)}</strong>
              <span>${escapeHtml(root.path)}</span>
            </span>
            ${root.warning ? '<i class="ti ti-alert-triangle"></i>' : ''}
          </button>
        `;
      }).join('');

      const treeRows = (localState.tree?.items || []).slice(0, 8).map(item => `
        <div class="local-tree-row" title="${escapeHtml(item.path)}">
          <i class="ti ${item.type === 'directory' ? 'ti-folder' : 'ti-file'}"></i>
          <span class="local-tree-main">
            <strong>${escapeHtml(item.name)}</strong>
            <span>${escapeHtml(item.excluded_reason || item.extension || item.type)}</span>
          </span>
          ${item.accessible === false ? '<i class="ti ti-lock"></i>' : ''}
        </div>
      `).join('');

      const summary = localState.audit?.summary || null;
      const auditHtml = summary ? `
        <div class="local-audit-grid">
          <div class="local-audit-stat"><strong>${formatCount(summary.readable_files)}</strong><span>읽을 파일</span></div>
          <div class="local-audit-stat"><strong>${formatCount(summary.sensitive_files)}</strong><span>민감 제외</span></div>
          <div class="local-audit-stat"><strong>${formatCount(summary.unsupported_files)}</strong><span>미지원</span></div>
          <div class="local-audit-stat"><strong>${formatCount(summary.too_large_files)}</strong><span>너무 큼</span></div>
          <div class="local-audit-stat"><strong>${formatCount(summary.image_ocr_candidates)}</strong><span>이미지</span></div>
          <div class="local-audit-stat"><strong>${formatCount(summary.estimated_seconds)}</strong><span>예상 초</span></div>
        </div>
      ` : '';

      const permissionHtml = localState.pendingPermission ? `
        <div class="local-permission">
          <div class="local-status-line">${escapeHtml(localState.pendingPermission.message || '')}</div>
          <button class="local-source-btn primary" onclick="approveLocalPermission()">
            <i class="ti ti-shield-check"></i>${t('local_permission')}
          </button>
        </div>
      ` : '';

      const sourceRows = localState.sources.slice(0, 4).map(source => {
        const status = source.watch_active ? '자동 감지 중' : (source.watch_enabled ? '자동 감지 대기' : '수동 반영');
        return `
          <div class="local-source-row" title="${escapeHtml(source.root_path)}">
            <i class="ti ti-database"></i>
            <span class="local-source-main">
              <strong>${escapeHtml(source.label || source.root_path)}</strong>
              <span>${escapeHtml(status)} · ${escapeHtml(source.root_path)}</span>
            </span>
            <span>${formatCount((source.file_status || {}).indexed)}</span>
          </div>
        `;
      }).join('');

      const watchWarning = localState.watch && localState.watch.available === false
        ? `<div class="local-status-line">${t('local_watch_unavailable')}</div>`
        : '';
      const statusClass = localState.error ? ' error' : '';
      const statusText = localState.error || localState.status || '';

      localSourcePanel.innerHTML = `
        <div class="local-source-notice">${t('local_notice')}</div>
        <div class="local-source-input">
          <input id="local-path-input" value="${escapeHtml(localState.selectedPath)}" placeholder="${t('local_path_ph')}" oninput="updateLocalPath(this.value)">
        </div>
        ${rootRows ? `<div class="local-root-list">${rootRows}</div>` : ''}
        <div class="local-option-row">
          <label><input type="checkbox" ${localState.includeOcr ? 'checked' : ''} onchange="setLocalOption('includeOcr', this.checked)"> ${t('local_ocr')}</label>
          <label><input type="checkbox" ${localState.watchEnabled ? 'checked' : ''} onchange="setLocalOption('watchEnabled', this.checked)"> ${t('local_watch')}</label>
        </div>
        <div class="local-source-actions">
          <button class="local-source-btn" ${localState.busy ? 'disabled' : ''} onclick="runLocalTree()" title="${t('local_tree')}"><i class="ti ti-folders"></i>${t('local_tree')}</button>
          <button class="local-source-btn" ${localState.busy ? 'disabled' : ''} onclick="runLocalAudit()" title="${t('local_audit')}"><i class="ti ti-shield-search"></i>${t('local_audit')}</button>
          <button class="local-source-btn primary" ${localState.busy ? 'disabled' : ''} onclick="runLocalIndex()" title="${t('local_index')}"><i class="ti ti-chart-dots-3"></i>${t('local_index')}</button>
        </div>
        ${permissionHtml}
        ${statusText ? `<div class="local-status-line${statusClass}">${escapeHtml(statusText)}</div>` : ''}
        ${watchWarning}
        ${auditHtml}
        ${treeRows ? `<div class="local-tree-list">${treeRows}</div>` : ''}
        <div class="local-source-list">
          ${sourceRows || `<div class="local-status-line">${t('local_sources_empty')}</div>`}
        </div>
      `;
    }

    function selectLocalPath(path) {
      localState.selectedPath = path;
      localState.tree = null;
      localState.audit = null;
      localState.error = '';
      localState.status = '';
      renderLocalSources();
    }

    function updateLocalPath(path) {
      localState.selectedPath = path;
    }

    function setLocalOption(key, value) {
      localState[key] = Boolean(value);
      renderLocalSources();
    }

    async function runLocalRequest(endpoint, payload, onSuccess) {
      if (!localState.selectedPath) return;
      localState.busy = true;
      localState.error = '';
      localState.status = '';
      localState.pendingPermission = null;
      renderLocalSources();
      try {
        const res = await apiJson(endpoint, payload);
        if (res.status === 401) {
          window.location.href = '/account';
          return;
        }
        const data = await res.json();
        if (data.permission_required) {
          localState.pendingPermission = { endpoint, payload, ...data };
          localState.busy = false;
          renderLocalSources();
          return;
        }
        if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
        await onSuccess(data);
      } catch (error) {
        localState.error = error.message;
      } finally {
        localState.busy = false;
        renderLocalSources();
      }
    }

    async function approveLocalPermission() {
      const pending = localState.pendingPermission;
      if (!pending) return;
      localState.busy = true;
      renderLocalSources();
      try {
        const approveRes = await apiFetch(`/permissions/approve/${encodeURIComponent(pending.approval_token)}`, { method: 'POST' });
        const approveData = await approveRes.json().catch(() => ({}));
        if (!approveRes.ok) throw new Error(approveData.detail || `Approval failed (${approveRes.status})`);
        const payload = { ...pending.payload, approved: true, approval_token: pending.approval_token };
        const res = await apiJson(pending.endpoint, payload);
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
        localState.pendingPermission = null;
        if (pending.endpoint.endsWith('/tree')) {
          localState.tree = data;
          localState.status = data.privacy_notice || '';
        } else if (pending.endpoint.endsWith('/audit')) {
          localState.audit = data;
          localState.status = data.privacy_notice || '';
        } else if (pending.endpoint.endsWith('/index')) {
          localState.status = `${t('local_indexed')} · ${formatCount((data.counts || {}).indexed)} files`;
          await Promise.all([loadGraph(), loadLocalSources()]);
          return;
        }
      } catch (error) {
        localState.error = error.message;
      } finally {
        localState.busy = false;
        renderLocalSources();
      }
    }

    function runLocalTree() {
      runLocalRequest('/knowledge-graph/local/tree', {
        path: localState.selectedPath,
        max_items: 120,
      }, data => {
        localState.tree = data;
        localState.status = data.privacy_notice || '';
      });
    }

    function runLocalAudit() {
      runLocalRequest('/knowledge-graph/local/audit', {
        path: localState.selectedPath,
        include_ocr: localState.includeOcr,
        max_files: 50000,
      }, data => {
        localState.audit = data;
        localState.status = data.privacy_notice || '';
      });
    }

    function runLocalIndex() {
      runLocalRequest('/knowledge-graph/local/index', {
        path: localState.selectedPath,
        include_ocr: localState.includeOcr,
        watch_enabled: localState.watchEnabled,
        max_files: 5000,
        consent: {
          ui: 'graph',
          knowledge_source: true,
          image_ocr: localState.includeOcr,
          watch_enabled: localState.watchEnabled,
          sensitive_files_default_excluded: true,
        },
      }, async data => {
        localState.status = `${t('local_indexed')} · ${formatCount((data.counts || {}).indexed)} files`;
        await Promise.all([loadGraph(), loadLocalSources()]);
      });
    }

    window.selectLocalPath = selectLocalPath;
    window.updateLocalPath = updateLocalPath;
    window.setLocalOption = setLocalOption;
    window.runLocalTree = runLocalTree;
    window.runLocalAudit = runLocalAudit;
    window.runLocalIndex = runLocalIndex;
    window.approveLocalPermission = approveLocalPermission;

    function nodeColor(type) {
      return (TYPE_CONFIG[type] || {}).color || '#8fa8bb';
    }

    function edgeStyle(type) {
      return EDGE_CONFIG[type] || { color: '#7f8f9d', label: type, width: 1.3 };
    }

    function typeLabel(type) {
      return (TYPE_CONFIG[type] || {}).label || type;
    }

    function formatMetric(value, digits = 2) {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return '-';
      const num = Number(value);
      if (Math.abs(num) >= 1000) return num.toLocaleString();
      return Number.isInteger(num) ? String(num) : num.toFixed(digits);
    }

    function formatUpdatedAt(updatedAt) {
      if (!updatedAt) return '';
      const stamp = new Date(updatedAt);
      if (Number.isNaN(stamp.getTime())) return '';
      const diffMs = Date.now() - stamp.getTime();
      const diffDays = Math.floor(diffMs / 86400000);
      if (diffDays <= 0) return t('today');
      if (diffDays === 1) return t('day_ago');
      if (diffDays < 30) return t('days_ago').replace('{n}', diffDays);
      const diffMonths = Math.floor(diffDays / 30);
      if (diffMonths < 12) return t('months_ago').replace('{n}', diffMonths);
      const diffYears = Math.floor(diffMonths / 12);
      return t('years_ago').replace('{n}', diffYears);
    }

    function updateStats() {
      document.getElementById('node-count').textContent = rawGraph.nodes.length.toLocaleString();
      document.getElementById('edge-count').textContent = rawGraph.edges.length.toLocaleString();
    }

    function computeVisuals() {
      const degreeMap = {};
      rawGraph.edges.forEach(edge => {
        degreeMap[edge.from] = (degreeMap[edge.from] || 0) + 1;
        degreeMap[edge.to] = (degreeMap[edge.to] || 0) + 1;
      });

      rawGraph.nodes.forEach(node => {
        const metrics = ((node.metadata || {}).graph_metrics) || {};
        const importanceNorm = clamp(
          Number.isFinite(Number(node.importance_norm))
            ? Number(node.importance_norm)
            : Number(metrics.importance_norm || 0),
          0,
          1
        );
        node.degree = degreeMap[node.id] || Number(metrics.degree || 0) || 0;
        node.importance_norm = importanceNorm;
        node.importance = Number.isFinite(Number(node.importance))
          ? Number(node.importance)
          : Number(metrics.importance_raw || 0);

        let radius = 10;
        if (node.type === 'Topic') {
          radius = 20 + importanceNorm * 24 + Math.sqrt(node.degree) * 1.2;
        } else if (node.type === 'Conversation') {
          radius = 16 + importanceNorm * 14 + Math.sqrt(node.degree) * 0.8;
        } else if (node.type === 'File') {
          radius = 15 + importanceNorm * 12 + Math.sqrt(node.degree) * 0.7;
        } else if (node.type === 'Decision' || node.type === 'Task') {
          radius = 14 + importanceNorm * 11 + Math.sqrt(node.degree) * 0.65;
        } else {
          radius = 13 + importanceNorm * 9 + Math.sqrt(node.degree) * 0.5;
        }
        const maxRadius = node.type === 'Topic' ? 52 : 38;
        node.r = clamp(radius, node.type === 'Topic' ? 18 : 12, maxRadius);
      });
    }

    function buildTypeCounts() {
      const counts = {};
      rawGraph.nodes.forEach(node => {
        counts[node.type] = (counts[node.type] || 0) + 1;
      });
      return counts;
    }

    function buildEdgeCounts() {
      const counts = {};
      rawGraph.edges.forEach(edge => {
        counts[edge.type] = (counts[edge.type] || 0) + 1;
      });
      return counts;
    }

    function applyFilter() {
      graph.nodes = rawGraph.nodes.filter(node => !hiddenTypes.has(node.type));
      const nodeSet = new Set(graph.nodes.map(node => node.id));
      const byId = Object.fromEntries(rawGraph.nodes.map(node => [node.id, node]));
      graph.edges = rawGraph.edges
        .filter(edge => nodeSet.has(edge.from) && nodeSet.has(edge.to))
        .map(edge => ({ ...edge, source: byId[edge.from], target: byId[edge.to] }));
    }

    function seedLayout() {
      rawGraph.nodes.forEach((node, index) => {
        if (node.x === undefined || node.y === undefined) {
          const angle = (index / Math.max(1, rawGraph.nodes.length)) * Math.PI * 2;
          const ring = Math.min(width, height) * (node.type === 'Topic' ? 0.22 : 0.32);
          node.x = width / 2 + Math.cos(angle) * ring;
          node.y = height / 2 + Math.sin(angle) * ring;
        }
        node.vx = node.vx || 0;
        node.vy = node.vy || 0;
        node._pinned = false;
      });
    }

    /* 방사형(허브-스포크) 레이아웃 — 최고 연결 노드를 중심에 고정 */
    function radialLayout() {
      const nodes = rawGraph.nodes;
      if (!nodes.length) return;

      nodes.forEach(n => { n._pinned = false; });

      // 가장 연결이 많은 노드 찾기
      const deg = {};
      rawGraph.edges.forEach(e => {
        deg[e.from] = (deg[e.from] || 0) + 1;
        deg[e.to]   = (deg[e.to]   || 0) + 1;
      });
      const sorted = [...nodes].sort((a, b) =>
        ((deg[b.id] || 0) + (b.importance_norm || 0) * 5) -
        ((deg[a.id] || 0) + (a.importance_norm || 0) * 5)
      );

      const hub = sorted[0];
      const others = sorted.slice(1);

      const cx = width / 2;
      const cy = height / 2;

      // 허브 노드 중앙 고정
      hub.x = cx; hub.y = cy;
      hub.vx = 0; hub.vy = 0;
      hub._pinned = true;

      // 나머지를 1~2개 링에 배치
      const INNER_MAX = Math.min(others.length, 10);
      const innerNodes = others.slice(0, INNER_MAX);
      const outerNodes = others.slice(INNER_MAX);

      const shortSide = Math.min(width, height);
      const innerR = shortSide * 0.27;
      const outerR  = shortSide * 0.46;

      innerNodes.forEach((node, i) => {
        const angle = (i / innerNodes.length) * Math.PI * 2 - Math.PI / 2;
        node.x = cx + Math.cos(angle) * innerR;
        node.y = cy + Math.sin(angle) * innerR;
        node.vx = 0; node.vy = 0;
      });

      outerNodes.forEach((node, i) => {
        const angle = (i / outerNodes.length) * Math.PI * 2 - Math.PI / 2;
        node.x = cx + Math.cos(angle) * outerR;
        node.y = cy + Math.sin(angle) * outerR;
        node.vx = 0; node.vy = 0;
      });
    }

    function mergeGraphData(extraNodes, extraEdges) {
      const nodeMap = new Map(rawGraph.nodes.map(node => [node.id, node]));
      extraNodes.forEach(node => {
        const prev = nodeMap.get(node.id) || {};
        nodeMap.set(node.id, {
          ...prev,
          ...node,
          metadata: { ...(prev.metadata || {}), ...(node.metadata || {}) },
        });
      });
      rawGraph.nodes = [...nodeMap.values()];

      const edgeMap = new Map(rawGraph.edges.map(edge => [edge.id || `${edge.from}|${edge.type}|${edge.to}`, edge]));
      extraEdges.forEach(edge => {
        const key = edge.id || `${edge.from}|${edge.type}|${edge.to}`;
        edgeMap.set(key, edge);
      });
      rawGraph.edges = [...edgeMap.values()];

      computeVisuals();
      seedLayout();
      applyFilter();
      updateStats();
      renderTypeFilters(buildTypeCounts());
      renderEdgeLegend(buildEdgeCounts());
    }

    async function loadGraph() {
      updateStats();
      const [graphRes, statsRes] = await Promise.all([
        apiFetch('/knowledge-graph/graph?limit=600'),
        apiFetch('/knowledge-graph/stats'),
      ]);
      if (graphRes.status === 401) {
        window.location.href = '/account';
        return;
      }
      if (!graphRes.ok) throw new Error(`Graph API failed (${graphRes.status})`);

      const graphData = await graphRes.json();
      const stats = statsRes.ok ? await statsRes.json() : {};
      rawGraph = {
        nodes: Array.isArray(graphData.nodes) ? graphData.nodes : [],
        edges: Array.isArray(graphData.edges) ? graphData.edges : [],
      };
      computeVisuals();
      seedLayout();
      radialLayout();
      applyFilter();
      updateStats();
      renderTypeFilters(stats.nodes || buildTypeCounts());
      renderEdgeLegend(stats.edges || {});
      showDetail(selected && rawGraph.nodes.find(node => node.id === selected.id) || graph.nodes[0] || null);
      cam = { scale: 1, tx: 0, ty: 0 };
      wakeUp();
    }

    function renderTypeFilters(typeCounts) {
      const presentTypes = [...new Set(rawGraph.nodes.map(node => node.type))];
      const ordered = [...Object.keys(TYPE_CONFIG), ...presentTypes.filter(type => !TYPE_CONFIG[type])]
        .filter(type => presentTypes.includes(type));
      const container = document.getElementById('type-filters');
      if (!ordered.length) {
        container.innerHTML = `<div class="empty-hint">${t('no_node_types')}</div>`;
        return;
      }
      container.innerHTML = ordered.map(type => {
        const checked = hiddenTypes.has(type) ? '' : 'checked';
        return `
          <label class="filter-item">
            <input type="checkbox" ${checked} onchange="toggleType('${type}', this.checked)">
            <span class="dot" style="background:${nodeColor(type)}"></span>
            <span class="filter-name">${escapeHtml(typeLabel(type))}</span>
            <span class="filter-count">${typeCounts[type] || 0}</span>
          </label>
        `;
      }).join('');
    }

    function renderEdgeLegend(edgeCounts) {
      const presentEdgeTypes = [...new Set(rawGraph.edges.map(edge => edge.type))];
      const ordered = [...Object.keys(EDGE_CONFIG), ...presentEdgeTypes.filter(type => !EDGE_CONFIG[type])]
        .filter(type => presentEdgeTypes.includes(type));
      const container = document.getElementById('edge-legend');
      if (!ordered.length) {
        container.innerHTML = `<div class="empty-hint">${t('no_relationships')}</div>`;
        return;
      }
      container.innerHTML = ordered.map(type => {
        const style = edgeStyle(type);
        return `
          <div class="legend-item">
            <span class="legend-line" style="border-top-color:${style.color}; border-top-width:${Math.max(2, style.width)}px;"></span>
            <span class="legend-name">${escapeHtml(style.label || type)}</span>
            <span class="legend-meta">${edgeCounts[type] || 0}</span>
          </div>
        `;
      }).join('');
    }

    function toggleType(type, visible) {
      if (visible) hiddenTypes.delete(type);
      else hiddenTypes.add(type);
      applyFilter();
      if (selected && hiddenTypes.has(selected.type)) showDetail(null);
      wakeUp();
    }
    window.toggleType = toggleType;

    function step() {
      const nodes = graph.nodes;
      const edges = graph.edges;
      const centerPull = selected ? 0.00035 : 0.00055;

      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i];
          const b = nodes[j];
          const dx = a.x - b.x;
          const dy = a.y - b.y;
          const d2 = Math.max(120, dx * dx + dy * dy);
          const strength = (a.type === 'Topic' || b.type === 'Topic') ? 2900 : 2100;
          const force = strength / d2;
          a.vx += dx * force;
          a.vy += dy * force;
          b.vx -= dx * force;
          b.vy -= dy * force;
        }
      }

      edges.forEach(edge => {
        if (!edge.source || !edge.target) return;
        const dx = edge.target.x - edge.source.x;
        const dy = edge.target.y - edge.source.y;
        const dist = Math.max(1, Math.hypot(dx, dy));
        const targetDistance = edge.type === 'mentions' || edge.type === 'discusses'
          ? 118
          : edge.type === 'contains'
            ? 138
            : 132;
        const force = (dist - targetDistance) * (0.0038 + Math.min(0.003, (edge.weight || 1) * 0.0015));
        edge.source.vx += (dx / dist) * force;
        edge.source.vy += (dy / dist) * force;
        edge.target.vx -= (dx / dist) * force;
        edge.target.vy -= (dy / dist) * force;
      });

      let kineticEnergy = 0;
      nodes.forEach(node => {
        if (node === dragging) return;
        const pull = node._pinned ? 0.55 : centerPull;
        node.vx += (width / 2 - node.x) * pull;
        node.vy += (height / 2 - node.y) * pull;
        node.vx *= 0.84;
        node.vy *= 0.84;
        node.x += node.vx;
        node.y += node.vy;
        kineticEnergy += node.vx * node.vx + node.vy * node.vy;
      });
      return kineticEnergy;
    }

    function wakeUp() {
      if (!animFrameId) animFrameId = requestAnimationFrame(draw);
    }

    const nbCache = new Map();
    function neighborIds(node) {
      if (nbCache.has(node.id)) return nbCache.get(node.id);
      const ids = new Set([node.id]);
      graph.edges.forEach(edge => {
        if (edge.from === node.id) ids.add(edge.to);
        if (edge.to === node.id) ids.add(edge.from);
      });
      nbCache.set(node.id, ids);
      return ids;
    }

    function draw() {
      animFrameId = null;
      const kineticEnergy = step();
      nbCache.clear();

      ctx.clearRect(0, 0, width, height);
      ctx.save();
      ctx.translate(cam.tx, cam.ty);
      ctx.scale(cam.scale, cam.scale);

      const active = hovered || selected;
      const neighborSet = active ? neighborIds(active) : null;

      graph.edges.forEach(edge => {
        if (!edge.source || !edge.target) return;
        const style = edgeStyle(edge.type);
        const isNeighborEdge = neighborSet && neighborSet.has(edge.from) && neighborSet.has(edge.to);
        const baseAlpha = neighborSet ? (isNeighborEdge ? 0.88 : 0.07) : 0.34;
        const widthBoost = isNeighborEdge ? 0.5 : 0;
        ctx.save();
        ctx.globalAlpha = baseAlpha;
        ctx.strokeStyle = style.color;
        ctx.lineWidth = (style.width + Math.min(3.4, (edge.weight || 1) * 1.1) + widthBoost) / cam.scale;
        ctx.setLineDash(style.dash || []);
        ctx.beginPath();
        ctx.moveTo(edge.source.x, edge.source.y);
        ctx.lineTo(edge.target.x, edge.target.y);
        ctx.stroke();
        ctx.restore();
      });

      graph.nodes.forEach(node => {
        const isNeighbor = neighborSet ? neighborSet.has(node.id) : true;
        const isSearchHit = searchResultIds.has(node.id);
        const isSelected = node === selected;
        const isHovered = node === hovered;
        const alpha = neighborSet ? (isNeighbor ? 1 : 0.12) : 1;
        const radius = node.r + (isSelected ? 4 : isHovered ? 2 : isSearchHit ? 2.6 : 0);

        ctx.globalAlpha = alpha;

        if (node.type === 'Topic') {
          const haloRadius = radius + 6 + node.importance_norm * 8;
          const halo = ctx.createRadialGradient(node.x, node.y, radius * 0.4, node.x, node.y, haloRadius);
          halo.addColorStop(0, `${nodeColor(node.type)}30`);
          halo.addColorStop(1, `${nodeColor(node.type)}00`);
          ctx.fillStyle = halo;
          ctx.beginPath();
          ctx.arc(node.x, node.y, haloRadius, 0, Math.PI * 2);
          ctx.fill();
        }

        // 노드 원 그리기 (흰 테두리 + 색상 채우기)
        ctx.fillStyle = nodeColor(node.type);
        ctx.beginPath();
        ctx.arc(node.x, node.y, radius, 0, Math.PI * 2);
        ctx.fill();

        // 흰 테두리
        ctx.strokeStyle = isSelected ? '#6f42e8' : 'rgba(255,255,255,0.85)';
        ctx.lineWidth = (isSelected ? 3.2 : isHovered ? 2.4 : 2.0) / cam.scale;
        ctx.beginPath();
        ctx.arc(node.x, node.y, radius, 0, Math.PI * 2);
        ctx.stroke();

        // 선택/호버 외곽 링
        if (isSelected || isHovered || isSearchHit) {
          ctx.strokeStyle = isSelected ? '#6f42e8' : nodeColor(node.type);
          ctx.lineWidth = (isSelected ? 2.8 : 1.8) / cam.scale;
          ctx.globalAlpha = alpha * 0.55;
          ctx.beginPath();
          ctx.arc(node.x, node.y, radius + 5 / cam.scale, 0, Math.PI * 2);
          ctx.stroke();
          ctx.globalAlpha = alpha;
        }

        // 레이블 항상 노드 아래에 표시
        {
          const label = node.title.slice(0, 24);
          const fs = Math.max(9.5, 12 / cam.scale);
          ctx.font = `600 ${fs}px "SF Pro Display","Inter",system-ui`;
          const lw = ctx.measureText(label).width;
          const gap = (radius + 8) / cam.scale;
          const lx = node.x - lw / 2;
          const ly = node.y + gap + fs;
          const pad = 4 / cam.scale;
          const br  = 5 / cam.scale;
          // 흰 배경 pill
          ctx.fillStyle = alpha > 0.5 ? 'rgba(255,255,255,0.88)' : 'rgba(255,255,255,0.22)';
          ctx.beginPath();
          if (ctx.roundRect) {
            ctx.roundRect(lx - pad, ly - fs, lw + pad * 2, fs + pad * 1.6, br);
          } else {
            ctx.rect(lx - pad, ly - fs, lw + pad * 2, fs + pad * 1.6);
          }
          ctx.fill();
          ctx.fillStyle = alpha > 0.5 ? '#14162c' : 'rgba(20,22,44,0.3)';
          ctx.fillText(label, lx, ly);
        }

        ctx.globalAlpha = 1;
      });

      ctx.restore();
      if (kineticEnergy > 0.04 || dragging) animFrameId = requestAnimationFrame(draw);
    }

    function toWorld(canvasX, canvasY) {
      return { x: (canvasX - cam.tx) / cam.scale, y: (canvasY - cam.ty) / cam.scale };
    }

    function nodeAt(canvasX, canvasY) {
      const { x, y } = toWorld(canvasX, canvasY);
      let best = null;
      let bestDistance = Infinity;
      graph.nodes.forEach(node => {
        const distance = Math.hypot(node.x - x, node.y - y);
        if (distance < (node.r + 10) / cam.scale && distance < bestDistance) {
          best = node;
          bestDistance = distance;
        }
      });
      return best;
    }

    function fitToScreen() {
      if (!graph.nodes.length) return;
      let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
      graph.nodes.forEach(node => {
        x0 = Math.min(x0, node.x - node.r);
        x1 = Math.max(x1, node.x + node.r);
        y0 = Math.min(y0, node.y - node.r);
        y1 = Math.max(y1, node.y + node.r);
      });
      const margin = 56;
      const scale = Math.min(
        2.8,
        Math.min(
          (width - margin * 2) / Math.max(1, x1 - x0),
          (height - margin * 2) / Math.max(1, y1 - y0)
        )
      );
      cam.scale = scale;
      cam.tx = (width - (x0 + x1) * scale) / 2;
      cam.ty = (height - (y0 + y1) * scale) / 2;
      wakeUp();
    }

    function centerOnNode(node, targetScale = cam.scale) {
      cam.scale = clamp(targetScale, 0.12, 4.5);
      cam.tx = width / 2 - node.x * cam.scale;
      cam.ty = height / 2 - node.y * cam.scale;
      wakeUp();
    }

    function metricCards(node) {
      const metrics = ((node.metadata || {}).graph_metrics) || {};
      const cards = [
        { value: formatMetric(metrics.importance_norm ? metrics.importance_norm * 100 : 0, 0), label: 'Importance %' },
        { value: formatMetric(metrics.degree || node.degree || 0, 0), label: 'Connections' },
      ];
      if (node.type === 'Topic') {
        cards.push({ value: formatMetric(metrics.mention_count || 0, 0), label: 'Mentions' });
        cards.push({ value: formatMetric(metrics.conversation_count || 0, 0), label: 'Conversations' });
      } else {
        cards.push({ value: formatMetric(metrics.recency_score || 0), label: 'Recency' });
        cards.push({ value: formatMetric(node.importance || metrics.importance_raw || 0), label: 'Raw score' });
      }
      return `<div class="metric-grid">${cards.map(card => `
        <div class="metric-card">
          <strong>${escapeHtml(card.value)}</strong>
          <span>${escapeHtml(card.label)}</span>
        </div>
      `).join('')}</div>`;
    }

    function showDetail(node) {
      if (!node) {
        selected = null;
        detail.innerHTML = `<p class="empty-hint">${t('detail_empty_short')}</p>`;
        wakeUp();
        return;
      }
      selected = node;
      const meta = node.metadata || {};
      const convId = meta.conversation_id;
      const jumpHtml = convId
        ? `<a class="jump-btn" href="${API_BASE}/chat?open_conversation=${encodeURIComponent(convId)}">${t('open_in_chat')}</a>`
        : '';
      const metrics = metricCards(node);
      const updatedAt = formatUpdatedAt(node.updated_at);
      const source = meta.relative_path || meta.filename || meta.conversation_id || meta.source || '';
      const metadataStr = Object.keys(meta).length ? JSON.stringify(meta, null, 2) : '';
      detail.innerHTML = `
        <div class="type-badge" style="background:${nodeColor(node.type)}">${escapeHtml(typeLabel(node.type))}</div>
        <div class="detail-title">${escapeHtml(node.title || node.id)}</div>
        ${node.summary ? `<div class="detail-summary">${escapeHtml(node.summary)}</div>` : ''}
        ${jumpHtml}
        ${metrics}
        <div class="detail-summary">
          ${source ? `<strong>source:</strong> ${escapeHtml(source)}<br>` : ''}
          ${updatedAt ? `<strong>updated:</strong> ${escapeHtml(updatedAt)}` : ''}
        </div>
        ${metadataStr ? `<div class="meta-block">${escapeHtml(metadataStr)}</div>` : ''}
      `;
      wakeUp();
    }

    function resize() {
      const rect = canvas.getBoundingClientRect();
      width = rect.width;
      height = rect.height;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function setSearchIdleState(message = t('ready')) {
      searchCountEl.textContent = message;
    }

    function renderSearchResults() {
      if (!searchInput.value.trim()) {
        searchResultsEl.innerHTML = `<p class="search-empty">${t('search_empty')}</p>`;
        return;
      }
      if (!searchResults.length) {
        searchResultsEl.innerHTML = `<p class="search-empty">${t('search_no_results')}</p>`;
        return;
      }
      searchResultsEl.innerHTML = `
        <div class="search-list">
          ${searchResults.map(match => {
            const active = selected && selected.id === match.id ? 'active' : '';
            const source = (match.metadata || {}).relative_path || (match.metadata || {}).filename || (match.metadata || {}).conversation_id || '';
            return `
              <button class="search-item ${active}" data-node-id="${escapeHtml(match.id)}">
                <div class="search-item-top">
                  <span class="search-type" style="background:${nodeColor(match.type)}">${escapeHtml(typeLabel(match.type))}</span>
                  <span class="search-item-title">${escapeHtml(match.title || match.id)}</span>
                </div>
                ${match.summary ? `<p class="search-item-summary">${escapeHtml(match.summary)}</p>` : ''}
                <div class="search-item-meta">
                  ${source ? `<span>${escapeHtml(source)}</span>` : ''}
                  ${match.updated_at ? `<span>${escapeHtml(formatUpdatedAt(match.updated_at))}</span>` : ''}
                </div>
              </button>
            `;
          }).join('')}
        </div>
      `;
    }

    async function runSearch(query) {
      const trimmed = String(query || '').trim();
      if (!trimmed) {
        searchResults = [];
        searchResultIds = new Set();
        setSearchIdleState(t('ready'));
        renderSearchResults();
        wakeUp();
        return;
      }

      if (searchAbortController) searchAbortController.abort();
      searchAbortController = new AbortController();
      searchCountEl.textContent = t('searching');
      searchResultsEl.innerHTML = `<p class="search-loading">${t('search_loading')}</p>`;

      try {
        const res = await apiFetch(`/knowledge-graph/search?q=${encodeURIComponent(trimmed)}&limit=12`, {
          signal: searchAbortController.signal,
        });
        if (!res.ok) throw new Error(`Search failed (${res.status})`);
        const data = await res.json();
        searchResults = Array.isArray(data.matches) ? data.matches : [];
        searchResultIds = new Set(searchResults.map(match => match.id));
        searchCountEl.textContent = t('search_results').replace('{n}', searchResults.length);
        renderSearchResults();
        wakeUp();
      } catch (error) {
        if (error.name === 'AbortError') return;
        searchResults = [];
        searchResultIds = new Set();
        searchCountEl.textContent = t('error');
        searchResultsEl.innerHTML = `<p class="search-empty">${escapeHtml(error.message)}</p>`;
        wakeUp();
      }
    }

    function scheduleSearch() {
      clearTimeout(searchDebounceId);
      searchDebounceId = setTimeout(() => runSearch(searchInput.value), 160);
    }

    function clearSearch() {
      searchInput.value = '';
      searchResults = [];
      searchResultIds = new Set();
      setSearchIdleState(t('ready'));
      renderSearchResults();
      wakeUp();
    }

    async function focusSearchResult(match) {
      let node = rawGraph.nodes.find(item => item.id === match.id);
      if (!node) {
        const res = await apiFetch(`/knowledge-graph/neighbors/${encodeURIComponent(match.id)}`);
        if (res.ok) {
          const payload = await res.json();
          mergeGraphData([
            {
              id: match.id,
              type: match.type,
              title: match.title,
              summary: match.summary,
              metadata: match.metadata,
              updated_at: match.updated_at,
            },
            ...((payload.neighbors || []).map(nodeItem => ({
              ...nodeItem,
              updated_at: nodeItem.updated_at,
            }))),
          ], payload.edges || []);
          node = rawGraph.nodes.find(item => item.id === match.id);
        }
      }
      if (!node) return;
      showDetail(node);
      centerOnNode(node, Math.max(cam.scale, node.type === 'Topic' ? 1.15 : 0.95));
      renderSearchResults();
    }

    canvas.addEventListener('mousedown', event => {
      const rect = canvas.getBoundingClientRect();
      const canvasX = event.clientX - rect.left;
      const canvasY = event.clientY - rect.top;
      const node = nodeAt(canvasX, canvasY);
      if (node) {
        dragging = node;
        showDetail(node);
      } else {
        panning = { sx: event.clientX, sy: event.clientY, tx0: cam.tx, ty0: cam.ty };
        canvas.classList.add('panning');
      }
      wakeUp();
    });

    canvas.addEventListener('mousemove', event => {
      const rect = canvas.getBoundingClientRect();
      const node = nodeAt(event.clientX - rect.left, event.clientY - rect.top);
      if (node !== hovered) {
        hovered = node;
        wakeUp();
      }
      canvas.style.cursor = panning ? 'grabbing' : (node ? 'pointer' : 'grab');
      if (node) {
        const metrics = ((node.metadata || {}).graph_metrics) || {};
        tooltip.style.display = 'block';
        tooltip.style.left = `${event.clientX + 14}px`;
        tooltip.style.top = `${event.clientY - 8}px`;
        tooltip.innerHTML = `
          <strong>${escapeHtml(node.title)}</strong><br>
          ${escapeHtml(typeLabel(node.type))} · importance ${escapeHtml(formatMetric((node.importance_norm || 0) * 100, 0))}%<br>
          ${node.type === 'Topic'
            ? `mentions ${escapeHtml(formatMetric(metrics.mention_count || 0, 0))} · conversations ${escapeHtml(formatMetric(metrics.conversation_count || 0, 0))}`
            : `connections ${escapeHtml(formatMetric(metrics.degree || node.degree || 0, 0))}`
          }
        `;
      } else {
        tooltip.style.display = 'none';
      }
    });

    canvas.addEventListener('mouseleave', () => {
      hovered = null;
      tooltip.style.display = 'none';
      wakeUp();
    });

    window.addEventListener('mousemove', event => {
      if (dragging) {
        const rect = canvas.getBoundingClientRect();
        const world = toWorld(event.clientX - rect.left, event.clientY - rect.top);
        dragging.x = world.x;
        dragging.y = world.y;
        dragging.vx = 0;
        dragging.vy = 0;
        wakeUp();
      } else if (panning) {
        cam.tx = panning.tx0 + (event.clientX - panning.sx);
        cam.ty = panning.ty0 + (event.clientY - panning.sy);
        wakeUp();
      }
    });

    window.addEventListener('mouseup', () => {
      dragging = null;
      panning = null;
      canvas.classList.remove('panning');
    });

    canvas.addEventListener('wheel', event => {
      event.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const canvasX = event.clientX - rect.left;
      const canvasY = event.clientY - rect.top;
      const zoomFactor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
      const nextScale = clamp(cam.scale * zoomFactor, 0.07, 6);
      cam.tx = canvasX - (canvasX - cam.tx) * (nextScale / cam.scale);
      cam.ty = canvasY - (canvasY - cam.ty) * (nextScale / cam.scale);
      cam.scale = nextScale;
      wakeUp();
    }, { passive: false });

    let lastTouchDistance = null;
    canvas.addEventListener('touchstart', event => {
      event.preventDefault();
      if (event.touches.length === 2) {
        lastTouchDistance = Math.hypot(
          event.touches[0].clientX - event.touches[1].clientX,
          event.touches[0].clientY - event.touches[1].clientY
        );
        dragging = null;
        return;
      }
      const touch = event.touches[0];
      const rect = canvas.getBoundingClientRect();
      const node = nodeAt(touch.clientX - rect.left, touch.clientY - rect.top);
      if (node) {
        dragging = node;
        showDetail(node);
      } else {
        panning = { sx: touch.clientX, sy: touch.clientY, tx0: cam.tx, ty0: cam.ty };
      }
      wakeUp();
    }, { passive: false });

    canvas.addEventListener('touchmove', event => {
      event.preventDefault();
      if (event.touches.length === 2) {
        const distance = Math.hypot(
          event.touches[0].clientX - event.touches[1].clientX,
          event.touches[0].clientY - event.touches[1].clientY
        );
        if (lastTouchDistance) {
          const factor = distance / lastTouchDistance;
          const centerX = (event.touches[0].clientX + event.touches[1].clientX) / 2;
          const centerY = (event.touches[0].clientY + event.touches[1].clientY) / 2;
          const rect = canvas.getBoundingClientRect();
          const px = centerX - rect.left;
          const py = centerY - rect.top;
          const nextScale = clamp(cam.scale * factor, 0.07, 6);
          cam.tx = px - (px - cam.tx) * (nextScale / cam.scale);
          cam.ty = py - (py - cam.ty) * (nextScale / cam.scale);
          cam.scale = nextScale;
          wakeUp();
        }
        lastTouchDistance = distance;
        return;
      }

      const touch = event.touches[0];
      if (dragging) {
        const rect = canvas.getBoundingClientRect();
        const world = toWorld(touch.clientX - rect.left, touch.clientY - rect.top);
        dragging.x = world.x;
        dragging.y = world.y;
        dragging.vx = 0;
        dragging.vy = 0;
      } else if (panning) {
        cam.tx = panning.tx0 + (touch.clientX - panning.sx);
        cam.ty = panning.ty0 + (touch.clientY - panning.sy);
      }
      wakeUp();
    }, { passive: false });

    canvas.addEventListener('touchend', () => {
      dragging = null;
      panning = null;
      lastTouchDistance = null;
    });

    searchInput.addEventListener('input', scheduleSearch);
    searchInput.addEventListener('keydown', event => {
      if (event.key === 'Enter' && searchResults.length) {
        event.preventDefault();
        focusSearchResult(searchResults[0]).catch(error => {
          searchCountEl.textContent = t('error');
          searchResultsEl.innerHTML = `<p class="search-empty">${escapeHtml(error.message)}</p>`;
        });
      }
    });

    document.getElementById('clear-search-btn').addEventListener('click', clearSearch);
    document.addEventListener('click', event => {
      if (!event.target.closest('.lang-picker')) {
        document.querySelectorAll('.lang-picker-menu').forEach(menu => menu.classList.remove('open'));
      }
    });
    document.getElementById('refresh-btn').addEventListener('click', () => {
      rawGraph = { nodes: [], edges: [] };
      graph = { nodes: [], edges: [] };
      selected = null;
      loadGraph().catch(error => {
        detail.innerHTML = `<div class="type-badge" style="background:${nodeColor('ClearEvent')}; color:#091019">${t('error')}</div><div class="detail-title">${t('graph_refresh_fail')}</div><div class="detail-summary">${escapeHtml(error.message)}</div>`;
      });
    });

    searchResultsEl.addEventListener('click', event => {
      const target = event.target.closest('[data-node-id]');
      if (!target) return;
      const match = searchResults.find(item => item.id === target.dataset.nodeId);
      if (!match) return;
      focusSearchResult(match).catch(error => {
        searchCountEl.textContent = t('error');
        searchResultsEl.innerHTML = `<p class="search-empty">${escapeHtml(error.message)}</p>`;
      });
    });

    window.addEventListener('resize', () => {
      resize();
      wakeUp();
    });

    resize();
    applyI18n();
    renderSearchResults();
    renderLocalSources();
    loadLocalSources();
    loadGraph().catch(error => {
      detail.innerHTML = `
        <div class="type-badge" style="background:${nodeColor('ClearEvent')}">${t('error')}</div>
        <div class="detail-title">${t('graph_load_fail')}</div>
        <div class="detail-summary">${escapeHtml(error.message)}</div>
      `;
    });
