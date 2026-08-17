
// ===== CONFIGURATION =====
const DOCS_BASE_URL = new URL('../', window.location.href);
const INDEX_URL = new URL('./_file_index.json', window.location.href);
let DOCS = [];

// Group labels
let GROUP_LABELS = {
  docs: '概观层',
  tech: '技术层',
  ref: '参考 · 研究 · 归档'
};

// ===== STATE =====
let currentDoc = null;
let searchResults = [];
let currentTab = 'docs';
let collapsedGroups = {};
let indexMeta = null;

function normalizeDocEntry(doc) {
  const keywords = [
    doc.label || '',
    doc.title || '',
    doc.desc || '',
    doc.path || '',
    doc.version || '',
    doc.date || '',
  ].join(' ').trim();

  return {
    path: doc.path,
    group: doc.group || 'docs',
    label: doc.label || doc.title || doc.path,
    title: doc.title || doc.label || doc.path,
    size: doc.size || 0,
    star: !!doc.star,
    desc: doc.desc || '',
    version: doc.version || '',
    date: doc.date || '',
    keywords,
  };
}

async function loadDocIndex() {
  const resp = await fetch(INDEX_URL, { cache: 'no-cache' });
  if (!resp.ok) throw new Error(`索引加载失败: HTTP ${resp.status}`);

  const data = await resp.json();
  if (!data || !Array.isArray(data.files)) {
    throw new Error('索引格式无效');
  }

  DOCS = data.files.map(normalizeDocEntry);
  indexMeta = data;
  GROUP_LABELS = data.groups || GROUP_LABELS;
  updateIndexMeta();
}

function updateIndexMeta() {
  if (!indexMeta) {
    document.getElementById('index-meta').textContent = '索引未加载';
    return;
  }

  const generatedAt = (indexMeta.generated_at || '').replace('T', ' ');
  const total = indexMeta.total_files ?? DOCS.length;
  document.getElementById('index-meta').textContent = `${total} 篇 · ${generatedAt || '时间未知'}`;
  document.getElementById('index-meta').title = `索引: ${INDEX_URL.href}\n文档根: ${DOCS_BASE_URL.href}`;
}

// ===== DOM REFS =====
const $sidebarNav = document.getElementById('sidebar-nav');
const $docContent = document.getElementById('doc-content');
const $emptyState = document.getElementById('empty-state');
const $breadcrumb = document.getElementById('doc-breadcrumb');
const $docSizeInfo = document.getElementById('doc-size-info');
const $searchInput = document.getElementById('search-input');
const $searchCount = document.getElementById('search-count');
const $tocPanel = document.getElementById('toc-panel');
const $tocList = document.getElementById('toc-list');
const $toast = document.getElementById('toast');
const $loading = document.getElementById('loading-overlay');

// ===== MARKDOWN CONFIG =====
marked.setOptions({
  highlight: function(code, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try { return hljs.highlight(code, { language: lang }).value; } catch(e) {}
    }
    return hljs.highlightAuto(code).value;
  },
  breaks: true,
  gfm: true,
  headerIds: true,
  mangle: false,
});

// ===== RENDER NAV =====
function renderNav(filter) {
  const groups = {};
  DOCS.forEach(doc => {
    if (!filter || matchesSearch(doc, filter)) {
      if (!groups[doc.group]) groups[doc.group] = [];
      groups[doc.group].push(doc);
    }
  });

  let html = '';
  const groupOrder = Object.keys(GROUP_LABELS);
  Object.keys(groups).forEach(grp => {
    if (!groupOrder.includes(grp)) groupOrder.push(grp);
  });
  for (const grp of groupOrder) {
    const label = GROUP_LABELS[grp] || grp;
    const items = groups[grp];
    if (!items || !items.length) continue;

    const isCollapsed = collapsedGroups[grp];

    // Section header
    html += `<div class="nav-section">
      <div class="nav-section-header${isCollapsed ? ' collapsed' : ''}" data-group="${grp}">
        <span class="chevron">&#9660;</span>
        ${label} <span style="opacity:0.5;font-size:10px;">(${items.length})</span>
      </div>`;

    if (!isCollapsed) {
      html += `<div class="nav-items">`;
      items.forEach(doc => {
        const isActive = currentDoc && currentDoc.path === doc.path;
        const star = doc.star ? '<span class="star">&#9733;</span>' : '';
        const icon = getDocIcon(doc.path);
        html += `<a class="nav-item${isActive ? ' active' : ''}" data-path="${doc.path}">
          <span class="doc-icon">${icon}</span>
          <span class="doc-name">${star}${doc.label}</span>
          <span class="doc-size">${formatSize(doc.size)}</span>
        </a>`;
      });
      html += `</div>`;
    }

    html += `</div>`;
  }

  $sidebarNav.innerHTML = html || '<div style="padding:20px;color:var(--text-3);font-size:13px;">未找到匹配的文档</div>';

  // Bind events
  document.querySelectorAll('.nav-section-header').forEach(el => {
    el.addEventListener('click', () => {
      const g = el.dataset.group;
      collapsedGroups[g] = !collapsedGroups[g];
      renderNav($searchInput.value.trim());
    });
  });

  document.querySelectorAll('.nav-item').forEach(el => {
    el.addEventListener('click', e => {
      e.preventDefault();
      openDoc(el.dataset.path);
    });
  });
}

function getDocIcon(path) {
  if (path === 'README.md') return '&#127968;'; // home
  if (path.includes('OperationalSemantics')) return '&#9889;'; // star
  if (path.includes('DNF')) return '&#9005;'; // tree
  if (path.includes('架构') || path.includes('架构')) return '&#128295;'; // building
  if (path.includes('接口')) return '&#128279;'; // link
  if (path.includes('CDCL')) return '&#128300;'; // gear
  if (path.includes('Benchmark') || path.includes('SAT Competition')) return '&#128202;'; // chart
  if (path.includes('Kissat')) return '&#129302;'; // robot
  if (path.includes('文献') || path.includes('论文') || path.includes('综述')) return '&#128214;'; // book
  if (path.includes('review') || path.includes('评审')) return '&#128172;'; // speech
  if (path.includes('分裂') || path.includes('化简')) return '&#129529;'; // magic
  if (path.includes('README')) return '&#128196;'; // doc
  return '&#128196;'; // default doc
}

function matchesSearch(doc, query) {
  if (!query) return true;
  const q = query.toLowerCase();
  return doc.label.toLowerCase().includes(q) ||
         doc.path.toLowerCase().includes(q) ||
         (doc.keywords && doc.keywords.toLowerCase().includes(q));
}

function formatSize(bytes) {
  if (bytes < 1024) return bytes + 'B';
  if (bytes < 10240) return (bytes/1024).toFixed(1) + 'KB';
  return Math.round(bytes/1024) + 'KB';
}

// ===== OPEN DOC =====
async function openDoc(path) {
  if (currentDoc && currentDoc.path === path) return;

  showLoading(true);
  try {
    const url = new URL(path, DOCS_BASE_URL).href;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const text = await resp.text();

    currentDoc = DOCS.find(d => d.path === path);

    renderMarkdown(text, path);
    updateBreadcrumb(path);
    $docSizeInfo.textContent = currentDoc ? formatSize(currentDoc.size) : '';
    $emptyState.style.display = 'none';
    $docContent.style.display = 'block';

    // Mark active nav item
    document.querySelectorAll('.nav-item').forEach(el => {
      el.classList.toggle('active', el.dataset.path === path);
    });

    // Update URL hash
    history.pushState({path}, '', '#' + encodeURIComponent(path));

    // Close mobile sidebar
    if (window.innerWidth < 600) {
      // sidebar hidden by CSS
    }

    // Show nav buttons
    updateNavButtons(path);

    showToast(`已加载: ${currentDoc ? currentDoc.label : path}`, 'success');
  } catch(err) {
    const url = new URL(path, DOCS_BASE_URL).href;
    showToast(`加载失败: ${err.message}`, 'error');
    $docContent.innerHTML = `
      <div style="padding:24px;color:var(--text);">
        <div style="font-size:18px;font-weight:700;margin-bottom:8px;">加载文档失败</div>
        <div style="color:var(--red);margin-bottom:8px;">${err.message}</div>
        <div style="color:var(--text-3);font-family:var(--font-mono);font-size:12px;word-break:break-all;">${url}</div>
      </div>
    `;
    $emptyState.style.display = 'none';
    $docContent.style.display = 'block';
    console.error(err);
  } finally {
    showLoading(false);
  }
}

function renderMarkdown(md, path) {
  let html = marked.parse(md);

  // Inject copy buttons into pre blocks
  html = html.replace(/<pre><code([^>]*)>([\s\S]*?)<\/code><\/pre>/g, (match, attrs, code) => {
    const escaped = code.replace(/`/g, '&#96;');
    return `<pre><code${attrs}>${code}</code><button class="copy-btn" data-code="${escaped}">复制</button></pre>`;
  });

  // Extract meta info from first blockquote
  let metaHtml = '';
  html = html.replace(/<blockquote>([\s\S]*?)<\/blockquote>/, (m, content) => {
    metaHtml = `<div class="doc-meta">${content}</div>`;
    return '';
  });

  // Add meta at top
  if (metaHtml) {
    html = metaHtml + html;
  }

  $docContent.innerHTML = html;

  // Bind copy buttons
  $docContent.querySelectorAll('.copy-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const code = btn.dataset.code.replace(/&#96;/g, '`').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
      navigator.clipboard.writeText(code).then(() => {
        btn.textContent = '已复制!';
        btn.classList.add('copied');
        setTimeout(() => { btn.textContent = '复制'; btn.classList.remove('copied'); }, 1500);
      });
    });
  });

  // Build TOC
  buildTOC();

  // Scroll to top
  document.getElementById('content').scrollTop = 0;
}

function buildTOC() {
  const headings = $docContent.querySelectorAll('h1, h2, h3');
  if (!headings.length) {
    $tocList.innerHTML = '<div style="padding:12px 16px;font-size:12px;color:var(--text-3);">本文档无目录结构</div>';
    return;
  }

  let html = '';
  headings.forEach((h, i) => {
    // Give each heading a stable id
    if (!h.id) {
      h.id = 'heading-' + i;
    }
    const level = h.tagName.toLowerCase().replace('h', '');
    html += `<div class="toc-item level-${level}" data-target="${h.id}">${h.textContent}</div>`;
  });

  $tocList.innerHTML = html;
  $tocList.querySelectorAll('.toc-item').forEach(el => {
    el.addEventListener('click', () => {
      const target = document.getElementById(el.dataset.target);
      if (target) {
        document.getElementById('content').scrollTo({ top: target.offsetTop - 16, behavior: 'smooth' });
      }
      // Highlight active
      $tocList.querySelectorAll('.toc-item').forEach(e => e.classList.remove('active'));
      el.classList.add('active');
    });
  });
}

function updateBreadcrumb(path) {
  const parts = path.split('/');
  const crumbs = parts.map((p, i) => {
    const name = p.replace('.md', '');
    const isLast = i === parts.length - 1;
    return `<span class="crumb" style="${isLast ? 'color:var(--accent)' : ''}">${name}</span>`;
  });
  $breadcrumb.innerHTML = crumbs.map((c, i) => i < crumbs.length - 1 ? c + '<span class="sep"> / </span>' : c).join('');
}

function updateNavButtons(path) {
  // Doc nav prev/next
  const idx = DOCS.findIndex(d => d.path === path);
  const prev = idx > 0 ? DOCS[idx - 1] : null;
  const next = idx < DOCS.length - 1 ? DOCS[idx + 1] : null;

  // Append to content
  if (prev || next) {
    const navHtml = `<div class="doc-nav">
      ${prev ? `<a class="doc-nav-btn prev" href="#" data-path="${prev.path}"><span>&#8592;</span><div><span class="nav-label">&#8592; 上一篇</span><span class="nav-title">${prev.label}</span></div></a>` : '<div>'}
      ${next ? `<a class="doc-nav-btn next" href="#" data-path="${next.path}"><div><span class="nav-label">下一篇 &#8594;</span><span class="nav-title">${next.label}</span></div><span>&#8594;</span></a>` : '<div>'}
    </div>`;
    $docContent.insertAdjacentHTML('beforeend', navHtml);
    $docContent.querySelectorAll('.doc-nav-btn').forEach(btn => {
      btn.addEventListener('click', e => {
        e.preventDefault();
        openDoc(btn.dataset.path);
      });
    });
  }
}

// ===== SEARCH =====
let searchDebounceTimer = null;

$searchInput.addEventListener('input', () => {
  clearTimeout(searchDebounceTimer);
  searchDebounceTimer = setTimeout(() => {
    const q = $searchInput.value.trim();
    renderNav(q);
    if (q) {
      $searchCount.textContent = `~`;
    } else {
      $searchCount.textContent = '';
    }
  }, 150);
});

$searchInput.addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    const visible = [...document.querySelectorAll('.nav-item:not(.hidden)')];
    if (visible.length > 0) {
      openDoc(visible[0].dataset.path);
      $searchInput.blur();
    }
  }
  if (e.key === 'Escape') {
    $searchInput.value = '';
    renderNav('');
    $searchCount.textContent = '';
    $searchInput.blur();
  }
});

// ===== TABS =====
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentTab = btn.dataset.tab;

    // Filter nav
    const q = $searchInput.value.trim();
    renderNav(q);
  });
});

// ===== ALL TOGGLE =====
document.getElementById('btn-all').addEventListener('click', () => {
  const allOpen = !collapsedGroups[currentTab]; // if current tab is NOT collapsed, we collapse
  const tabGroupMap = { docs: 'docs', tech: 'tech', ref: 'ref' };
  Object.keys(collapsedGroups).forEach(k => collapsedGroups[k] = !allOpen);
  // But only affect current tab
  Object.keys(tabGroupMap).forEach(k => {
    if (k !== currentTab) delete collapsedGroups[k];
  });
  collapsedGroups[currentTab] = allOpen;
  renderNav($searchInput.value.trim());
});

// ===== TOC =====
document.getElementById('btn-toc').addEventListener('click', () => {
  $tocPanel.classList.toggle('open');
});

document.getElementById('toc-close').addEventListener('click', () => {
  $tocPanel.classList.remove('open');
});

// ===== TOOLBAR =====
document.getElementById('btn-copy-link').addEventListener('click', () => {
  if (!currentDoc) return;
  const url = location.origin + location.pathname + '#' + encodeURIComponent(currentDoc.path);
  navigator.clipboard.writeText(url).then(() => {
    showToast('链接已复制到剪贴板', 'success');
  });
});

document.getElementById('btn-top').addEventListener('click', () => {
  document.getElementById('content').scrollTo({ top: 0, behavior: 'smooth' });
});

document.getElementById('btn-refresh').addEventListener('click', async () => {
  try {
    showLoading(true);
    await loadDocIndex();
    renderNav($searchInput.value.trim());

    if (currentDoc) {
      const exists = DOCS.find(d => d.path === currentDoc.path);
      if (exists) {
        currentDoc = exists;
      } else {
        currentDoc = null;
        $docContent.style.display = 'none';
        $emptyState.style.display = 'block';
        $breadcrumb.innerHTML = '<span class="crumb">当前文档已不存在，请重新选择</span>';
      }
    }

    showToast('索引已刷新', 'success');
  } catch (err) {
    console.error(err);
    showToast(`刷新失败: ${err.message}`, 'error');
  } finally {
    showLoading(false);
  }
});

// ===== TOC SCROLL TRACKING =====
document.getElementById('content').addEventListener('scroll', () => {
  const headings = [...$docContent.querySelectorAll('h1, h2, h3')];
  const scrollTop = document.getElementById('content').scrollTop;

  let active = null;
  for (const h of headings) {
    if (h.offsetTop - 80 <= scrollTop) {
      active = h.id;
    }
  }

  if (active) {
    $tocList.querySelectorAll('.toc-item').forEach(el => {
      el.classList.toggle('active', el.dataset.target === active);
    });
  }
});

// ===== TOAST =====
function showToast(msg, type = 'info') {
  $toast.textContent = msg;
  $toast.className = type + ' visible';
  setTimeout(() => { $toast.classList.remove('visible'); }, 2500);
}

// ===== LOADING =====
function showLoading(show) {
  $loading.classList.toggle('visible', show);
}

// ===== INIT =====
async function initApp() {
  try {
    await loadDocIndex();
    renderNav('');

    const hash = location.hash.slice(1);
    if (hash) {
      const path = decodeURIComponent(hash);
      const exists = DOCS.find(d => d.path === path);
      if (exists) {
        await openDoc(path);
      }
    } else {
      const defaultDoc = DOCS.find(d => d.path === 'README.md') || DOCS[0];
      if (defaultDoc) {
        await openDoc(defaultDoc.path);
      }
    }
  } catch (err) {
    console.error(err);
    $sidebarNav.innerHTML = '<div style="padding:20px;color:var(--red);font-size:13px;">无法加载文档索引，请先运行 build_index.py 生成 _file_index.json。</div>';
    showToast(`初始化失败: ${err.message}`, 'error');
  }
}

initApp();

// Keyboard shortcut: / to focus search
document.addEventListener('keydown', e => {
  if (e.key === '/' && document.activeElement !== $searchInput) {
    e.preventDefault();
    $searchInput.focus();
  }
  if (e.key === 'Escape') {
    $tocPanel.classList.remove('open');
  }
});
