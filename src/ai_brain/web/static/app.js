/* ── AI-Brain Dashboard — Client Application ──────────────────────────── */
"use strict";

// ── State ────────────────────────────────────────────────────────────────
const PAGE_SIZE = 20;
let currentOffset = 0;
let currentFilters = {};

// ── API Client ───────────────────────────────────────────────────────────
async function api(path, { method = "GET", body, params } = {}) {
    let url = `/api/v1${path}`;
    if (params) {
        const qs = new URLSearchParams();
        for (const [k, v] of Object.entries(params)) {
            if (v == null || v === "") continue;
            if (Array.isArray(v)) v.forEach(i => qs.append(k, i));
            else qs.append(k, String(v));
        }
        const s = qs.toString();
        if (s) url += `?${s}`;
    }
    const opts = { method };
    if (body) {
        opts.headers = { "Content-Type": "application/json" };
        opts.body = JSON.stringify(body);
    }
    const res = await fetch(url, opts);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    if (res.status === 204) return null;
    return res.json();
}

// ── Toast Notifications ──────────────────────────────────────────────────
function showToast(message, type = "success") {
    let container = document.querySelector(".toast-container");
    if (!container) {
        container = document.createElement("div");
        container.className = "toast-container";
        document.body.appendChild(container);
    }
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 3500);
}

// ── Markdown Rendering ───────────────────────────────────────────────────
function renderMarkdown(text) {
    if (typeof marked !== "undefined") {
        marked.setOptions({
            highlight: (code, lang) => {
                if (typeof hljs !== "undefined" && lang && hljs.getLanguage(lang)) {
                    return hljs.highlight(code, { language: lang }).value;
                }
                return typeof hljs !== "undefined" ? hljs.highlightAuto(code).value : code;
            },
            breaks: true,
            gfm: true,
        });
        return marked.parse(text);
    }
    // Fallback: simple escaping
    return "<pre>" + escapeHtml(text) + "</pre>";
}

function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
}

// ── Utility Helpers ──────────────────────────────────────────────────────
function formatDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return d.toLocaleDateString("ru-RU", { year: "numeric", month: "short", day: "numeric" });
}

function formatDateTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return d.toLocaleString("ru-RU", {
        year: "numeric", month: "short", day: "numeric",
        hour: "2-digit", minute: "2-digit",
    });
}

function truncate(text, len = 120) {
    if (!text) return "";
    const clean = text.replace(/[#*_`>\-\[\]()]/g, "").replace(/\n+/g, " ").trim();
    return clean.length > len ? clean.slice(0, len) + "..." : clean;
}

function tagClass(tag) {
    if (tag.startsWith("project:")) return "project";
    if (tag.startsWith("ext:")) return "ext";
    if (tag === "auto-watch") return "auto";
    return "";
}

// ── Component: Memory Card ───────────────────────────────────────────────
function memoryCardHtml(m) {
    const title = m.title || m.content.split("\n")[0].replace(/^#+\s*/, "").slice(0, 80) || "Untitled";
    const preview = truncate(m.content, 140);
    const typeHtml = `<span class="type-badge ${m.memory_type}">${m.memory_type}</span>`;
    const tagsHtml = (m.tags || []).slice(0, 4)
        .map(t => `<span class="tag-badge ${tagClass(t)}">${escapeHtml(t)}</span>`).join(" ");
    return `
        <a href="#memory/${m.id}" class="memory-card" data-id="${m.id}">
            <div class="memory-card-title">${escapeHtml(title)}</div>
            <div class="memory-card-preview">${escapeHtml(preview)}</div>
            <div class="memory-card-meta">
                ${typeHtml}
                ${tagsHtml}
                <span style="margin-left:auto">${formatDate(m.created_at)}</span>
            </div>
        </a>`;
}

// ── Router ────────────────────────────────────────────────────────────────
function initRouter() {
    window.addEventListener("hashchange", handleRoute);
    handleRoute();
}

function handleRoute() {
    const hash = (location.hash || "#dashboard").slice(1);
    const parts = hash.split("/");
    const page = parts[0] || "dashboard";
    const param = parts.slice(1).join("/");

    // Update nav
    document.querySelectorAll(".nav-item").forEach(el => {
        el.classList.toggle("active", el.dataset.page === page);
    });

    renderPage(page, param);
}

async function renderPage(page, param) {
    const main = document.getElementById("main-content");
    main.innerHTML = '<div class="loading"><div class="spinner"></div></div>';

    try {
        switch (page) {
            case "dashboard": await renderDashboard(main); break;
            case "memories":  await renderMemories(main); break;
            case "memory":    await renderMemoryDetail(main, param); break;
            case "projects":  await renderProjects(main); break;
            case "tags":      await renderTags(main, param); break;
            case "search":    await renderSearch(main, decodeURIComponent(param || "")); break;
            case "watcher":   await renderWatcher(main); break;
            case "add":       renderAddForm(main); break;
            default:          main.innerHTML = '<div class="empty-state"><p>Page not found</p></div>';
        }
    } catch (err) {
        main.innerHTML = `<div class="error-message">Error: ${escapeHtml(err.message)}</div>`;
        console.error(err);
    }
}

// ── Page: Dashboard ──────────────────────────────────────────────────────
async function renderDashboard(el) {
    const [stats, tags, recent] = await Promise.all([
        api("/stats"),
        api("/tags"),
        api("/memories", { params: { limit: 8 } }),
    ]);

    const projectTags = Object.entries(tags).filter(([t]) => t.startsWith("project:"));
    const tagCount = Object.keys(tags).length;

    el.innerHTML = `
        <div class="page-header"><h1>Dashboard</h1></div>
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-value">${stats.total_memories}</div>
                <div class="stat-label">Memories</div>
            </div>
            <div class="stat-card green">
                <div class="stat-value">${stats.total_embeddings}</div>
                <div class="stat-label">Embeddings</div>
            </div>
            <div class="stat-card purple">
                <div class="stat-value">${projectTags.length}</div>
                <div class="stat-label">Projects</div>
            </div>
            <div class="stat-card orange">
                <div class="stat-value">${tagCount}</div>
                <div class="stat-label">Tags</div>
            </div>
        </div>
        <div class="dashboard-grid">
            <div class="panel">
                <h2>Recent Memories</h2>
                <div class="memory-list">${recent.length
                    ? recent.map(m => memoryCardHtml(m)).join("")
                    : '<div class="empty-state"><p>No memories yet</p></div>'
                }</div>
            </div>
            <div class="panel">
                <h2>Top Tags</h2>
                <div class="tag-cloud">${Object.entries(tags).slice(0, 25).map(([tag, count]) =>
                    `<a href="#tags/${encodeURIComponent(tag)}" class="tag-badge ${tagClass(tag)}">${escapeHtml(tag)} <span>${count}</span></a>`
                ).join("")}</div>
            </div>
        </div>`;

    updateHeaderStats(stats, tagCount);
}

function updateHeaderStats(stats, tagCount) {
    const el = document.getElementById("header-stats");
    if (el) {
        el.innerHTML = `<span>${stats.total_memories} memories</span><span>${tagCount} tags</span>`;
    }
}

// ── Page: Memories ───────────────────────────────────────────────────────
async function renderMemories(el) {
    const [tags, memories] = await Promise.all([
        api("/tags"),
        api("/memories", {
            params: {
                limit: PAGE_SIZE,
                offset: currentOffset,
                ...currentFilters,
            },
        }),
    ]);

    const sourceOptions = ["", "manual", "mcp", "file", "web", "cli", "obsidian", "telegram"]
        .map(s => `<option value="${s}" ${currentFilters.source === s ? "selected" : ""}>${s || "All sources"}</option>`)
        .join("");

    const typeOptions = ["", "note", "fact", "snippet", "bookmark", "conversation", "session_context"]
        .map(t => `<option value="${t}" ${currentFilters.memory_type === t ? "selected" : ""}>${t || "All types"}</option>`)
        .join("");

    el.innerHTML = `
        <div class="page-header">
            <h1>Memories</h1>
            <a href="#add" class="btn btn-primary">+ Add Memory</a>
        </div>
        <div class="filters-bar">
            <select class="filter-select" id="filter-source">${sourceOptions}</select>
            <select class="filter-select" id="filter-type">${typeOptions}</select>
            <input class="filter-input" id="filter-tag" placeholder="Filter by tag..." value="${escapeHtml(currentFilters.tag || "")}" style="max-width:200px">
        </div>
        <div class="memory-list" id="memory-list">
            ${memories.length
                ? memories.map(m => memoryCardHtml(m)).join("")
                : '<div class="empty-state"><p>No memories found</p></div>'}
        </div>
        <div class="pagination" id="pagination"></div>`;

    // Filters
    document.getElementById("filter-source").onchange = e => {
        currentFilters.source = e.target.value || undefined;
        currentOffset = 0;
        renderMemories(el);
    };
    document.getElementById("filter-type").onchange = e => {
        currentFilters.memory_type = e.target.value || undefined;
        currentOffset = 0;
        renderMemories(el);
    };
    let tagTimeout;
    document.getElementById("filter-tag").oninput = e => {
        clearTimeout(tagTimeout);
        tagTimeout = setTimeout(() => {
            currentFilters.tag = e.target.value || undefined;
            currentOffset = 0;
            renderMemories(el);
        }, 400);
    };

    // Pagination
    if (memories.length === PAGE_SIZE || currentOffset > 0) {
        const pagEl = document.getElementById("pagination");
        pagEl.innerHTML = `
            <button class="btn" ${currentOffset === 0 ? "disabled" : ""} onclick="prevPage()">Prev</button>
            <span style="color:var(--text-secondary)">offset ${currentOffset}</span>
            <button class="btn" ${memories.length < PAGE_SIZE ? "disabled" : ""} onclick="nextPage()">Next</button>`;
    }
}

function nextPage() {
    currentOffset += PAGE_SIZE;
    location.hash = "#memories";
    renderPage("memories", "");
}
function prevPage() {
    currentOffset = Math.max(0, currentOffset - PAGE_SIZE);
    location.hash = "#memories";
    renderPage("memories", "");
}

// ── Page: Memory Detail ──────────────────────────────────────────────────
async function renderMemoryDetail(el, memoryId) {
    if (!memoryId) { el.innerHTML = '<div class="empty-state"><p>Memory ID required</p></div>'; return; }

    const m = await api(`/memories/${memoryId}`);
    const title = m.title || m.content.split("\n")[0].replace(/^#+\s*/, "").slice(0, 120) || "Untitled";

    el.innerHTML = `
        <div class="page-header">
            <h1>${escapeHtml(title)}</h1>
            <div class="btn-group">
                <button class="btn" onclick="editMemory('${m.id}')">Edit</button>
                <button class="btn btn-danger" onclick="deleteMemory('${m.id}')">Delete</button>
                <a href="#memories" class="btn">Back</a>
            </div>
        </div>
        <div class="memory-detail">
            <div class="memory-content">
                <div class="memory-rendered md">${renderMarkdown(m.content)}</div>
            </div>
            <div class="memory-sidebar">
                <div class="meta-block">
                    <h3>Info</h3>
                    <div class="meta-row"><span class="label">Type</span><span class="value"><span class="type-badge ${m.memory_type}">${m.memory_type}</span></span></div>
                    <div class="meta-row"><span class="label">Source</span><span class="value">${m.source}</span></div>
                    <div class="meta-row"><span class="label">Created</span><span class="value">${formatDateTime(m.created_at)}</span></div>
                    <div class="meta-row"><span class="label">Updated</span><span class="value">${formatDateTime(m.updated_at)}</span></div>
                    <div class="meta-row"><span class="label">ID</span><span class="value" style="font-family:var(--font-mono);font-size:.8rem">${m.id.slice(0, 12)}...</span></div>
                    ${m.source_url ? `<div class="meta-row"><span class="label">URL</span><a href="${escapeHtml(m.source_url)}" target="_blank" class="value">${escapeHtml(m.source_url.slice(0, 40))}...</a></div>` : ""}
                </div>
                <div class="meta-block">
                    <h3>Tags</h3>
                    <div class="tag-cloud">
                        ${(m.tags || []).map(t =>
                            `<a href="#tags/${encodeURIComponent(t)}" class="tag-badge ${tagClass(t)}">${escapeHtml(t)}</a>`
                        ).join(" ")}
                        ${m.tags.length === 0 ? '<span style="color:var(--text-tertiary)">No tags</span>' : ""}
                    </div>
                </div>
                ${m.file_path ? `<div class="meta-block"><h3>File</h3><p style="font-family:var(--font-mono);font-size:.82rem;word-break:break-all;color:var(--text-secondary)">${escapeHtml(m.file_path)}</p></div>` : ""}
            </div>
        </div>`;

    // Highlight code blocks
    if (typeof hljs !== "undefined") {
        el.querySelectorAll("pre code").forEach(block => hljs.highlightElement(block));
    }
}

// ── Page: Projects ───────────────────────────────────────────────────────
async function renderProjects(el) {
    const tags = await api("/tags");
    const projects = Object.entries(tags)
        .filter(([t]) => t.startsWith("project:"))
        .map(([t, count]) => ({ name: t.slice(8), tag: t, count }))
        .sort((a, b) => b.count - a.count);

    el.innerHTML = `
        <div class="page-header"><h1>Projects</h1></div>
        ${projects.length === 0
            ? '<div class="empty-state"><p>No projects found. Projects are auto-detected from tags.</p></div>'
            : `<div class="projects-grid">${projects.map(p => `
                <a href="#memories" class="project-card" onclick="filterByProject('${escapeHtml(p.tag)}')">
                    <div class="project-card-name">${escapeHtml(p.name)}</div>
                    <div class="project-card-count">${p.count} memories</div>
                </a>`).join("")}</div>`
        }`;
}

function filterByProject(tag) {
    currentFilters = { tag };
    currentOffset = 0;
}

// ── Page: Tags ───────────────────────────────────────────────────────────
async function renderTags(el, selectedTag) {
    const tags = await api("/tags");
    const sorted = Object.entries(tags).sort((a, b) => b[1] - a[1]);

    let memoriesHtml = "";
    if (selectedTag) {
        const memories = await api("/memories", { params: { limit: 50, tag: selectedTag } });
        memoriesHtml = `
            <div style="margin-top:24px">
                <h2 style="font-size:1.1rem;margin-bottom:16px">Memories tagged: <span style="color:var(--accent)">${escapeHtml(selectedTag)}</span></h2>
                <div class="memory-list">${memories.map(m => memoryCardHtml(m)).join("") || '<div class="empty-state"><p>No memories</p></div>'}</div>
            </div>`;
    }

    el.innerHTML = `
        <div class="page-header"><h1>Tags</h1></div>
        <div class="tag-cloud" style="margin-bottom:24px">
            ${sorted.map(([tag, count]) => {
                const active = tag === selectedTag ? "background:var(--accent-muted);border-color:var(--accent);" : "";
                return `<a href="#tags/${encodeURIComponent(tag)}" class="tag-badge ${tagClass(tag)}" style="${active}">${escapeHtml(tag)} <span>${count}</span></a>`;
            }).join("")}
        </div>
        ${memoriesHtml}`;
}

// ── Page: Search ─────────────────────────────────────────────────────────
async function renderSearch(el, query) {
    if (!query) {
        el.innerHTML = '<div class="empty-state"><p>Enter a search query</p></div>';
        return;
    }

    const results = await api("/search", { method: "POST", body: { query, limit: 30 } });

    el.innerHTML = `
        <div class="page-header"><h1>Search: "${escapeHtml(query)}"</h1></div>
        <p style="color:var(--text-secondary);margin-bottom:20px">${results.length} results found</p>
        <div class="memory-list">
            ${results.length
                ? results.map(r => {
                    const m = r.memory;
                    const scoreHtml = `<span class="type-badge ${r.search_type}">${r.search_type} ${r.score.toFixed(3)}</span>`;
                    const title = m.title || m.content.split("\n")[0].replace(/^#+\s*/, "").slice(0, 80) || "Untitled";
                    return `
                        <a href="#memory/${m.id}" class="memory-card">
                            <div class="memory-card-title">${escapeHtml(title)}</div>
                            <div class="memory-card-preview">${escapeHtml(truncate(m.content, 200))}</div>
                            <div class="memory-card-meta">
                                ${scoreHtml}
                                <span class="type-badge ${m.memory_type}">${m.memory_type}</span>
                                ${(m.tags || []).slice(0, 3).map(t => `<span class="tag-badge ${tagClass(t)}">${escapeHtml(t)}</span>`).join(" ")}
                                <span style="margin-left:auto">${formatDate(m.created_at)}</span>
                            </div>
                        </a>`;
                }).join("")
                : '<div class="empty-state"><p>No results found</p></div>'
            }
        </div>`;
}

// ── Page: Watcher ────────────────────────────────────────────────────────
async function renderWatcher(el) {
    // Show auto-watch stats
    const tags = await api("/tags");
    const autoCount = tags["auto-watch"] || 0;

    // Get recent auto-watch memories
    const autoMemories = await api("/memories", { params: { limit: 20, tag: "auto-watch" } });

    // Aggregate projects from auto-watch memories
    const projects = {};
    autoMemories.forEach(m => {
        (m.tags || []).filter(t => t.startsWith("project:")).forEach(t => {
            projects[t.slice(8)] = (projects[t.slice(8)] || 0) + 1;
        });
    });

    el.innerHTML = `
        <div class="page-header"><h1>File Watcher</h1></div>
        <div class="stats-grid" style="margin-bottom:24px">
            <div class="stat-card green">
                <div class="stat-value">${autoCount}</div>
                <div class="stat-label">Auto-indexed Files</div>
            </div>
            <div class="stat-card purple">
                <div class="stat-value">${Object.keys(projects).length}</div>
                <div class="stat-label">Watched Projects</div>
            </div>
        </div>

        <div class="info-card">
            <h3>How to Start the Watcher</h3>
            <p>Run the watcher daemon to automatically index files from your workspace directories:</p>
            <pre>brain watch ~/Projects ~/LABs</pre>
            <p style="margin-top:12px">Or with options:</p>
            <pre>brain watch ~/Projects --no-scan    # skip initial scan
brain watch ~/Projects --verbose     # verbose logging</pre>
            <p style="margin-top:12px">The watcher will monitor files for changes and auto-index them into brain memory with
            <code>auto-watch</code> tag. It supports: .md, .py, .js, .ts, .yaml, .json, .toml, .sh, .sql, and many more.</p>
        </div>

        ${autoMemories.length > 0 ? `
        <div class="panel" style="margin-top:16px">
            <h2>Recently Indexed Files</h2>
            <div class="memory-list">
                ${autoMemories.map(m => memoryCardHtml(m)).join("")}
            </div>
        </div>` : ""}`;
}

// ── Page: Add Memory ─────────────────────────────────────────────────────
function renderAddForm(el) {
    el.innerHTML = `
        <div class="page-header"><h1>Add Memory</h1></div>
        <form class="add-form" id="add-form" onsubmit="submitAddForm(event)">
            <div class="form-group">
                <label for="add-title">Title (optional)</label>
                <input type="text" id="add-title" placeholder="Auto-generated if empty">
            </div>
            <div class="form-group">
                <label for="add-content">Content *</label>
                <textarea id="add-content" placeholder="Write your memory content here... Markdown supported." required></textarea>
            </div>
            <div class="form-group">
                <label for="add-tags">Tags (comma-separated)</label>
                <input type="text" id="add-tags" placeholder="e.g. python, architecture, project:myapp">
            </div>
            <div class="form-group">
                <label for="add-type">Type</label>
                <select id="add-type">
                    <option value="note">Note</option>
                    <option value="fact">Fact</option>
                    <option value="snippet">Snippet</option>
                    <option value="bookmark">Bookmark</option>
                </select>
            </div>
            <div class="btn-group" style="margin-top:20px">
                <button type="submit" class="btn btn-primary">Save Memory</button>
                <a href="#memories" class="btn">Cancel</a>
            </div>
        </form>`;
}

async function submitAddForm(e) {
    e.preventDefault();
    const content = document.getElementById("add-content").value.trim();
    if (!content) return;

    const title = document.getElementById("add-title").value.trim() || null;
    const tagsStr = document.getElementById("add-tags").value.trim();
    const tags = tagsStr ? tagsStr.split(",").map(t => t.trim()).filter(Boolean) : [];
    const memoryType = document.getElementById("add-type").value;

    try {
        await api("/memories", {
            method: "POST",
            body: { content, title, tags, source: "manual", memory_type: memoryType },
        });
        showToast("Memory saved!");
        location.hash = "#memories";
    } catch (err) {
        showToast("Failed to save: " + err.message, "error");
    }
}

// ── Actions: Edit & Delete ───────────────────────────────────────────────
async function editMemory(id) {
    const m = await api(`/memories/${id}`);
    const overlay = document.getElementById("modal-overlay");
    const title = document.getElementById("modal-title");
    const body = document.getElementById("modal-body");
    const footer = document.getElementById("modal-footer");

    title.textContent = "Edit Memory";
    body.innerHTML = `
        <div class="form-group">
            <label>Title</label>
            <input type="text" id="edit-title" value="${escapeHtml(m.title || "")}">
        </div>
        <div class="form-group">
            <label>Content</label>
            <textarea id="edit-content" style="min-height:300px">${escapeHtml(m.content)}</textarea>
        </div>
        <div class="form-group">
            <label>Tags (comma-separated)</label>
            <input type="text" id="edit-tags" value="${escapeHtml((m.tags || []).join(", "))}">
        </div>
        <div class="form-group">
            <label>Type</label>
            <select id="edit-type">
                ${["note","fact","snippet","bookmark","conversation","session_context"]
                    .map(t => `<option value="${t}" ${m.memory_type === t ? "selected" : ""}>${t}</option>`)
                    .join("")}
            </select>
        </div>`;
    footer.innerHTML = `
        <button class="btn" onclick="closeModal()">Cancel</button>
        <button class="btn btn-primary" onclick="saveEdit('${id}')">Save Changes</button>`;

    overlay.classList.add("open");
}

async function saveEdit(id) {
    const content = document.getElementById("edit-content").value.trim();
    const title = document.getElementById("edit-title").value.trim() || null;
    const tagsStr = document.getElementById("edit-tags").value.trim();
    const tags = tagsStr ? tagsStr.split(",").map(t => t.trim()).filter(Boolean) : [];
    const memoryType = document.getElementById("edit-type").value;

    try {
        await api(`/memories/${id}`, {
            method: "PUT",
            body: { content, title, tags, memory_type: memoryType },
        });
        closeModal();
        showToast("Memory updated!");
        location.hash = `#memory/${id}`;
        renderPage("memory", id);
    } catch (err) {
        showToast("Failed to update: " + err.message, "error");
    }
}

async function deleteMemory(id) {
    if (!confirm("Delete this memory? This action cannot be undone.")) return;
    try {
        await api(`/memories/${id}`, { method: "DELETE" });
        showToast("Memory deleted");
        location.hash = "#memories";
    } catch (err) {
        showToast("Failed to delete: " + err.message, "error");
    }
}

// ── Modal ────────────────────────────────────────────────────────────────
function closeModal() {
    document.getElementById("modal-overlay").classList.remove("open");
}

document.getElementById("modal-overlay").addEventListener("click", e => {
    if (e.target === e.currentTarget) closeModal();
});

document.addEventListener("keydown", e => {
    if (e.key === "Escape") closeModal();
});

// ── Global Search ────────────────────────────────────────────────────────
const searchInput = document.getElementById("global-search");
let searchTimeout;
searchInput.addEventListener("keydown", e => {
    if (e.key === "Enter") {
        e.preventDefault();
        const q = searchInput.value.trim();
        if (q) {
            location.hash = `#search/${encodeURIComponent(q)}`;
        }
    }
});
searchInput.addEventListener("input", () => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => {
        const q = searchInput.value.trim();
        if (q.length >= 3) {
            location.hash = `#search/${encodeURIComponent(q)}`;
        }
    }, 600);
});

// ── Initialize ───────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", initRouter);
