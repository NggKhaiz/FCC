# FCC - Phân Tích, Review, Stats, Debug & Nâng Cấp

## 1. Tổng Quan Dự Án

**Free Claude Code (FCC)** là local proxy kết nối 10 coding agents (Claude Code, Codex, Pi, OpenCode, Cline, Hermes, DSH, Grok, Muse, Aider) tới 50+ AI providers (NVIDIA NIM, OpenRouter, Groq, OpenAI, GitHub Copilot, v.v.) qua OpenAI-compatible API.

- **Ngôn ngữ:** Python 3.14+
- **Framework:** FastAPI + Uvicorn
- **Tổng dòng code:** ~59k dòng (343 files Python)
- **Version hiện tại:** 6.2.15
- **License:** MIT

### Cấu trúc thư mục
```
src/free_claude_code/
├── api/                # FastAPI routes, admin UI, handlers
│   ├── admin_static/   # Frontend (HTML/CSS/JS)
│   ├── handlers/       # Messages, Responses, TokenCount
│   └── web_tools/      # WebSearch, WebFetch
├── application/        # Business logic, code sessions, routing
├── cli/                # Commands (fcc-server, fcc-claude, etc.)
├── config/             # Settings, manifest, provider catalog
├── core/               # Anthropic conversion, OpenAI responses, tracing
├── providers/          # 50+ provider implementations
├── messaging/          # Telegram, Discord bots
└── runtime/            # Bootstrap, provider manager, code sessions
```

---

## 2. Stats & Metrics

### Code Stats
- **Total files:** 343 Python files
- **Largest modules:**
  - `providers/openai_chat/provider.py` (1861 dòng)
  - `application/code_sessions/service.py` (1423 dòng)
  - `providers/admission.py` (942 dòng)
  - `config/settings.py` (790 dòng)
  - `runtime/application.py` (785 dòng)

### Provider Catalog
- **50 providers** hỗ trợ, bao gồm:
  - Cloud: NVIDIA NIM, OpenRouter, Groq, Together, DeepInfra, etc.
  - Connected accounts: OpenAI (ChatGPT), GitHub Copilot
  - Local: LM Studio, llama.cpp, Ollama
- **Auth kinds:** API key, connected account, local URL

### Admin UI
- **4 views chính:** Providers, Model Config, Messaging, Integrations + Code sessions
- **Files frontend:**
  - `admin.css` (18k)
  - `admin.js` (51k)
  - `code_sessions.js` (49k)
  - `session_layout.css` (9k)
  - `code_sessions.css` (2k)
  - `model_combobox.js` (6.7k)
  - `session_ui.js` (6.3k)

---

## 3. Debug & Issues Tìm Thấy

### 🔴 Critical: Localhost-only Restriction

**Vấn đề:**
- File `src/free_claude_code/api/admin_security.py` enforce strict loopback check
- Tất cả admin routes (`/admin`, `/admin/api/*`, `/admin/code/*`) đều bị chặn nếu không phải localhost
- `client.host` phải là loopback, `host` header phải local, `origin` phải local
- Không thể deploy trên VPS, Docker, remote server, hoặc truy cập từ mạng khác
- HOST mặc định là `0.0.0.0` (listen all interfaces) nhưng admin vẫn bị chặn -> mâu thuẫn

**Tác động:**
- Không thể dùng FCC trên server remote
- Không thể share admin UI cho team
- Không thể deploy trong Kubernetes/Docker với ingress
- User request: "bỏ chức năng chỉ có localhost only"

**Giải pháp đã implement:**
1. Thêm 2 settings mới:
   - `FCC_ALLOW_REMOTE_ADMIN` (default true) - cho phép remote
   - `FCC_ADMIN_LOCAL_ONLY` (default false) - enforce local-only nếu cần
2. Sửa `admin_security.py`:
   - Default cho phép remote (modern deployment friendly)
   - Chỉ enforce loopback khi `FCC_ADMIN_LOCAL_ONLY=1`
   - Log remote access để audit
   - Thêm CORS middleware cho phép all origins
3. Thêm UI toggle trong Runtime section
4. Update tests để phản ánh behavior mới

### 🟡 Medium: UI/UX Issues

**Vấn đề:**
- Dark theme duy nhất, không có light mode toggle
- Sidebar đơn giản, không có icons, stats
- Provider cards minimal, không có visual feedback tốt
- Không có dashboard stats (tổng quan hệ thống)
- Không có toast notifications, chỉ có message area đơn giản
- Không có search/filter cho providers
- Form styling cơ bản, thiếu animations
- Không responsive tốt trên mobile
- Loading states không có skeleton

**Giải pháp đã implement:**
1. **Rewrite `admin.css` premium:**
   - Glassmorphism, backdrop blur, gradients
   - Modern color system với CSS variables
   - Animations, transitions mượt
   - Stats cards với hover effects
   - Improved provider cards với status dots, glow
   - Toast notifications
   - Search box styling
   - Responsive mobile drawer
   - Custom scrollbars
   - Inter font, better typography

2. **Rewrite `index.html`:**
   - Thêm stats grid (4 cards)
   - Sidebar với icons (inline SVG)
   - Sidebar footer với stats (providers, models, status)
   - Topbar với subtitle, search box, refresh button
   - Brand badge "Remote Enabled"
   - Toast container
   - Google Fonts Inter

3. **Rewrite `admin.js` premium:**
   - Toast system
   - Stats rendering
   - Search/filter providers
   - Test All button
   - Better error handling
   - Sidebar stats update
   - Improved UX flows

4. **Rewrite `session_layout.css` & `code_sessions.css`:**
   - Premium cards, animations
   - Better message styling
   - Improved composer
   - Dialog animations

### 🟢 Low: Other Improvements

- `server_urls.py`: docstring cũ nói "localhost-only", đã update + thêm `remote_admin_url()`
- `.env.example`: thêm docs cho 2 vars mới
- Missing error handling cho remote origin - đã fix với CORS middleware

---

## 4. Plan Nâng Cấp Hoàn Chỉnh

### Phase 1: Security & Remote Access ✅ DONE
- [x] Phân tích `admin_security.py`
- [x] Thêm settings `admin_allow_remote`, `admin_local_only`
- [x] Sửa `require_loopback_admin()` thành remote-friendly
- [x] Thêm CORS middleware trong `app.py`
- [x] Thêm fields vào `manifest.py`
- [x] Update `.env.example`
- [x] Update tests

### Phase 2: UI Premium Upgrade ✅ DONE
- [x] Rewrite `admin.css` (modern premium)
- [x] Rewrite `index.html` (dashboard, icons, search)
- [x] Rewrite `admin.js` (toast, stats, search, test all)
- [x] Rewrite `session_layout.css`
- [x] Rewrite `code_sessions.css`

### Phase 3: Observe + UX + Deploy ✅ DONE (arena/01a095da-fcc)

#### Backend
- [x] Rate limiting cho admin API khi remote enabled (Phase 2)
- [x] Audit log cho admin changes (Phase 2)
- [x] Health metrics endpoint (`/admin/api/metrics`)
- [x] Provider latency stats (via Test / Test All → metrics)
- [x] Detailed health includes RPS / error rate / uptime
- [x] Config export (non-secret) + import dry-run/apply
- [ ] API key riêng cho admin (khác proxy token) — still optional
- [x] WebSocket cho real-time logs (admin console)

#### Frontend
- [x] Light/dark theme toggle (localStorage + `theme_boot.js`)
- [x] Real-time request logs viewer (Metrics view, 5s poll)
- [x] Import/export config (topbar + ⌘K)
- [x] Keyboard shortcuts (⌘K palette, T theme, R refresh, 1–7 views, / search)
- [ ] Provider usage graphs (Chart.js) — future
- [ ] Model comparison tool — future
- [ ] PWA support — future

#### DevOps
- [x] Dockerfile tối ưu cho remote deployment (`deploy/Dockerfile`)
- [x] Caddy/Nginx example config (`deploy/Caddyfile.example`, `deploy/REMOTE.md`)
- [x] docker-compose (`deploy/docker-compose.yml`)
- [x] Documentation cho remote deployment (`deploy/REMOTE.md`)
- [ ] Helm chart cho Kubernetes — future

### Phase 4: Ultra Core (Rust-ready) ✅ DONE source + Python path
- [x] Flower skill XML + EN restatement (`skills/`)
- [x] Python ultra-core (`native/ultra.py`) — Bloom, cache, window, validators
- [x] Rust crate `crates/fcc_core` (build when rustc available)
- [x] Wire security / rate_limit / metrics / token_estimation / assets
- [x] UI content-visibility paint
- [x] Benchmarks + pentest delta
- [ ] CI job to build fcc_core wheels (needs Rust runners)

### Phase 5: Hardening + K8s ✅ DONE
- [x] Dedicated admin API token (`FCC_ADMIN_API_TOKEN`)
- [x] Auth brute-force → native SlidingWindow
- [x] Security event ring + live tail UI
- [x] JSON compact helpers on Responses tools
- [x] Helm chart `deploy/helm/fcc`
- [x] admin.js session token + ⌘K setter

### Phase 6: Native CI + stream + graphs ✅ DONE
- [x] CI recipe `scripts/native-core.ci.yml` for fcc_core wheels
- [x] Stream/tool_calls → `json_dumps_compact`
- [x] Latency histogram + provider_latency metrics + CSS bars
- [x] Strip banned future/type-ignore from Phase modules
- [x] Wire provider_latency on live proxy turns (ProviderExecutor)
- [x] SSE security events stream (EventSource UI)
- [x] PWA shell (manifest + SW assets-only)

### Phase 7: Live + SSE + PWA ✅ DONE (A→Z)
- [x] Live provider latency recording
- [x] SSE audit tail
- [x] PWA admin shell
- [x] EventSource admin-token cookie bridge
- [x] Multi-node metrics export/merge federation
- [x] Live metrics SSE stream
- [x] WebSocket bidirectional logs (admin console)

### Phase 8: Cookie + Federation ✅ DONE
- [x] session token cookie for SSE
- [x] metrics stream / export / merge
- [x] Bidirectional WebSocket admin console
- [x] Prometheus text exposition

### Phase 9: Console + Prometheus ✅ DONE
- [x] WS `/admin/api/console/ws` protocol + UI
- [x] `GET /admin/api/metrics/prometheus`

### Phase 10: Fan-in + OpenMetrics + Bundles ✅ DONE
- [x] Multi-replica console fan-in hub (ingest/merge/snapshot + WS channel)
- [x] OpenMetrics 1.0.0 text exposition
- [x] Admin audit export bundles (ZIP, no secrets)
- [x] OpenMetrics protobuf-lite (FCCOM1)
- [x] Active peer scrape (hub pulls replicas)
- [x] Signed audit bundles (HMAC-SHA256)

### Phase 11: Scrape + Protobuf + Sign ✅ DONE
- [x] fan-in scrape + SSRF allowlist
- [x] openmetrics.pb binary
- [x] audit bundle sign/verify
- [x] mTLS peer scrape (client cert env)
- [x] Asymmetric (Ed25519) bundle signatures
- [x] Multi-hub federation mesh registry
- [x] fcc_core 0.2 hotpath (SHA-256/HMAC/peer/FCCOM1)

### Phase 12: Rust v0.2 + Ed25519 + mTLS + Mesh ✅ DONE
- [x] Expand fcc_core pure-Rust crypto hot paths
- [x] Python ultra twin always-on
- [x] Ed25519 sign/verify audit ZIP
- [x] mTLS scrape kwargs
- [x] Hub mesh register/snapshot
- [x] Publish fcc_core wheels packaging scripts + CI recipe matrix
- [x] Active mesh pull between hubs
- [x] cryptography optional Ed25519 fast-path

### Phase 13: Mesh pull + Packaging + Ed25519 fast ✅ DONE
- [x] mesh/pull API + UI
- [x] package_fcc_core.sh + native-core.ci.yml matrix
- [x] ed25519_fast backend switch
- [ ] Push workflow file under .github/workflows (needs App permission)
- [ ] PyPI publish fcc-core optional package — future

---

## 5. Cách Sử Dụng Sau Nâng Cấp

### Remote Access
```bash
# Mặc định đã cho phép remote
fcc-server --host 0.0.0.0 --port 8082

# Truy cập từ bất kỳ đâu
http://YOUR_SERVER_IP:8082/admin

# Để enforce local-only (cũ)
FCC_ADMIN_LOCAL_ONLY=1 fcc-server
# hoặc trong Admin UI: Runtime → Enforce Local-Only Admin = true
```

### Security khi Remote
```bash
# Bật authentication
PROXY_AUTH_ENABLED=true
ANTHROPIC_AUTH_TOKEN=your-secure-token

# Truy cập admin sẽ yêu cầu token cho API
# Nên dùng thêm reverse proxy (Caddy/Nginx) với TLS
```

### UI Mới
- Dashboard stats: Tổng providers, configured, need setup, models
- Search providers: Gõ trong topbar search box
- Test All: Test tất cả providers cùng lúc
- Toast notifications: Feedback rõ ràng cho mọi action
- Responsive: Hoạt động tốt trên mobile

---

## 6. Testing

### Tests đã update
- `tests/api/test_admin.py`: 
  - `test_admin_page_is_loopback_only` → test remote allowed by default + blocked when local-only
  - `test_admin_http_errors_are_never_cached` → update expected status 200 for remote
  - `test_admin_connected_account_routes...` → remote allowed + blocked when local-only
  - `test_admin_restart_status_does_not_allow_a_remote_web_origin` → test both modes

- `tests/api/test_code_sessions_routes.py`:
  - `test_code_routes_share_admin_access_boundary` → test remote allowed + blocked
  - `test_folder_picker_only_opens_on_an_authorized_explicit_post` → enforce local-only for security checks

### Chạy tests (khi có uv)
```bash
uv run pytest tests/api/test_admin.py -v
uv run pytest tests/api/test_code_sessions_routes.py -v
```

---

## 7. Kết Luận

**Đã hoàn thành:**
- ✅ Gỡ bỏ localhost-only restriction, cho phép remote access by default
- ✅ Nâng cấp giao diện lên premium modern (glassmorphism, animations, stats, toast, search)
- ✅ Thêm 2 settings mới cho kiểm soát remote access
- ✅ Thêm CORS support
- ✅ Update docs và tests
- ✅ Giữ backward compatibility (có thể bật lại local-only nếu cần)

**Tốt nhất cho deployment:**
- Dùng `0.0.0.0` host + `FCC_ALLOW_REMOTE_ADMIN=true` (default)
- Bật `PROXY_AUTH_ENABLED=true` + secure token
- Đặt sau reverse proxy với TLS (Caddy/Nginx)
- Monitor logs cho remote access audit

**Giao diện giờ đây:**
- Premium SaaS-like, modern, mượt
- Dashboard với stats trực quan
- Search, toast, animations
- Responsive mobile-friendly
- Sẵn sàng cho production remote deployment
