document.addEventListener('alpine:init', () => {
    Alpine.data('app', () => ({
        // Resume state
        resume: {
            loaded: false,
            checksum: null,
            firstName: null,
            lastName: null,
            contentPreview: '',
            instructions: '',
            inputMode: 'upload', // 'upload' | 'paste'
            pasteText: '',
            loading: false,
            showPreview: false,
            error: null,
        },

        // Job state
        job: {
            loaded: false,
            text: '',
            preview: '',
            url: '',
            inputMode: 'url', // 'url' | 'paste'
            pasteText: '',
            loading: false,
            showPreview: false,
            error: null,
        },

        // Settings
        settings: {
            sequential: false,
            debug: true,
            noShame: false,
            language: 'from_job',
            maxIterations: 5,
            instructions: '',
            // Per-run overrides
            proModel: '',
            flashModel: '',
            embeddingModel: '',
            reasoningEffort: '',
            apiKeys: { gemini: '', openrouter: '', openai: '', anthropic: '', moonshot: '' },
            thresholds: { hallucination: 0.9, keyword: 0.25, llm: 0.7, vector: 0.4, ai_generated: 0.4, translation: 0.95 },
        },

        // App settings from server
        appSettings: {
            languageModes: [],
            proModel: '',
            flashModel: '',
            embeddingModel: '',
            reasoningEffort: '',
            apiKeysSet: { gemini: false, openrouter: false, openai: false, anthropic: false, moonshot: false },
            filterThresholds: {},
        },

        // Optimization state
        optimization: {
            running: false,
            id: null,
            abortController: null,
            statusMessage: '',
            iterations: [],
            logs: [],
            logsOpen: false,
            finalResult: null,
            error: null,
            cancelled: false,
        },

        // Cached resumes/jobs for pickers
        cachedResumes: [],
        cachedJobs: [],

        // History
        history: [],

        // Expanded iteration indices
        expandedIterations: {},

        // Drawer state
        drawerOpen: false,
        drawerSections: { options: true, models: true, apiKeys: false, thresholds: false, history: true },
        showPdfPreview: false,

        // Computed getters
        get apiKeyEntries() {
            return [
                ['gemini', 'Gemini API Key'],
                ['openrouter', 'OpenRouter API Key'],
                ['openai', 'OpenAI API Key'],
                ['anthropic', 'Anthropic API Key'],
                ['moonshot', 'Moonshot API Key'],
            ];
        },

        get thresholdEntries() {
            return [
                ['hallucination', 'Hallucination'],
                ['keyword', 'Keyword Match'],
                ['llm', 'LLM Check'],
                ['vector', 'Vector Similarity'],
                ['ai_generated', 'AI Generated'],
                ['translation', 'Translation Quality'],
            ];
        },

        async init() {
            this._restoreFromStorage();
            await Promise.all([
                this.loadSettings(),
                this.loadCachedResumes(),
                this.loadCachedJobs(),
                this.loadHistory(),
                this.checkActiveOptimization(),
            ]);
            // Auto-select newest (by mtime) without touching — mtime is the source of truth
            if (this.cachedResumes.length > 0) this._loadCachedResume(this.cachedResumes[0]);
            if (this.cachedJobs.length > 0) await this._loadCachedJob(this.cachedJobs[0]);

            // Watch for changes and persist
            this.$watch('settings', () => this._saveToStorage());

        },

        _storageKey: 'hr-breaker-state',

        _saveToStorage() {
            const state = {
                settings: { ...this.settings, apiKeys: undefined },
                drawerSections: this.drawerSections,
            };
            try { localStorage.setItem(this._storageKey, JSON.stringify(state)); } catch {}
        },

        _restoredFromStorage: false,

        _restoreFromStorage() {
            try {
                const raw = localStorage.getItem(this._storageKey);
                if (!raw) return;
                const state = JSON.parse(raw);
                if (state.settings) {
                    const { apiKeys, ...rest } = state.settings;
                    Object.assign(this.settings, rest);
                    // Always reset apiKeys to empty (never restore from storage)
                    this.settings.apiKeys = { gemini: '', openrouter: '', openai: '', anthropic: '', moonshot: '' };
                    this._restoredFromStorage = true;
                }
                if (state.drawerSections) {
                    Object.assign(this.drawerSections, state.drawerSections);
                }
            } catch {}
        },

        // Load resume metadata into UI state (no server touch — preserves mtime)
        _loadCachedResume(r) {
            this.resume.loaded = true;
            this.resume.checksum = r.checksum;
            this.resume.firstName = r.first_name;
            this.resume.lastName = r.last_name;
            this.resume.contentPreview = '';
            this.resume.error = null;
            if (r.instructions) {
                this.settings.instructions = r.instructions;
            }
        },

        // Load job content into UI state (no server touch — preserves mtime)
        async _loadCachedJob(j) {
            try {
                const resp = await fetch('/api/job/' + j.checksum);
                const data = await resp.json();
                this.job.loaded = true;
                this.job.text = data.text;
                this.job.preview = data.text.substring(0, 200).replace(/\n/g, ' ');
            } catch (e) {
                console.error('Failed to auto-load job:', e);
            }
        },

        async loadSettings() {
            try {
                const resp = await fetch('/api/settings');
                const data = await resp.json();
                this.appSettings.languageModes = data.language_modes;
                this.appSettings.proModel = data.pro_model;
                this.appSettings.flashModel = data.flash_model;
                this.appSettings.embeddingModel = data.embedding_model || '';
                this.appSettings.reasoningEffort = data.reasoning_effort || '';
                this.appSettings.apiKeysSet = data.api_keys_set || {};
                this.appSettings.filterThresholds = data.filter_thresholds || {};
                // Always prefill models/reasoning from server if not customized
                if (!this.settings.proModel) this.settings.proModel = data.pro_model || '';
                if (!this.settings.flashModel) this.settings.flashModel = data.flash_model || '';
                if (!this.settings.embeddingModel) this.settings.embeddingModel = data.embedding_model || '';
                if (!this.settings.reasoningEffort) this.settings.reasoningEffort = data.reasoning_effort || '';
                if (!this._restoredFromStorage) {
                    this.settings.language = data.default_language;
                    this.settings.maxIterations = data.max_iterations;
                    if (data.filter_thresholds) {
                        Object.assign(this.settings.thresholds, data.filter_thresholds);
                    }
                }
            } catch (e) {
                console.error('Failed to load settings:', e);
            }
        },

        async loadCachedResumes() {
            try {
                const resp = await fetch('/api/resume/cached');
                const resumes = await resp.json();
                this.cachedResumes = resumes.slice().reverse();
            } catch (e) {
                console.error('Failed to load cached resumes:', e);
            }
        },


        async removeCachedResume(checksum) {
            await fetch('/api/resume/cached/' + checksum, { method: 'DELETE' });
            this.cachedResumes = this.cachedResumes.filter(r => r.checksum !== checksum);
            if (this.resume.checksum === checksum) {
                this.clearResume();
            }
        },

        async selectCachedResume(r) {
            this._loadCachedResume(r);
            await fetch('/api/resume/select/' + r.checksum, { method: 'POST' });
        },

        async loadResumeContent() {
            if (this.resume.contentPreview || !this.resume.checksum) return;
            try {
                const resp = await fetch('/api/resume/' + this.resume.checksum);
                const data = await resp.json();
                this.resume.contentPreview = data.content;
            } catch (e) {
                console.error('Failed to load resume content:', e);
            }
        },

        async loadHistory() {
            try {
                const resp = await fetch('/api/history');
                this.history = await resp.json();
            } catch (e) {
                console.error('Failed to load history:', e);
            }
        },

        get resumeName() {
            if (!this.resume.firstName && !this.resume.lastName) return 'Unknown';
            return [this.resume.firstName, this.resume.lastName].filter(Boolean).join(' ');
        },

        get canOptimize() {
            return this.resume.loaded && this.job.loaded && !this.optimization.running;
        },

        get optimizeButtonText() {
            if (!this.resume.loaded) return 'Upload resume first';
            if (!this.job.loaded) return 'Add job posting first';
            return 'Optimize';
        },

        // --- Resume actions ---

        async handleFileUpload(event) {
            const file = event.target.files[0];
            if (!file) return;

            this.resume.loading = true;
            this.resume.error = null;
            try {
                const formData = new FormData();
                formData.append('file', file);
                const resp = await fetch('/api/resume/upload', { method: 'POST', body: formData });
                if (!resp.ok) {
                    const text = await resp.text();
                    try { const j = JSON.parse(text); throw new Error(j.error || j.detail || text); }
                    catch (pe) { if (pe instanceof SyntaxError) throw new Error(text); throw pe; }
                }
                const data = await resp.json();

                if (data.error) throw new Error(data.error);

                this.resume.loaded = true;
                this.resume.checksum = data.checksum;
                this.resume.firstName = data.first_name;
                this.resume.lastName = data.last_name;
                this.resume.contentPreview = '';
                this.loadCachedResumes();
            } catch (e) {
                this.resume.error = 'Upload failed: ' + e.message;
            } finally {
                this.resume.loading = false;
            }
        },

        async submitPasteResume() {
            if (!this.resume.pasteText.trim()) return;

            this.resume.loading = true;
            this.resume.error = null;
            try {
                const resp = await fetch('/api/resume/paste', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content: this.resume.pasteText }),
                });
                if (!resp.ok) {
                    const text = await resp.text();
                    try { const j = JSON.parse(text); throw new Error(j.error || j.detail || text); }
                    catch (pe) { if (pe instanceof SyntaxError) throw new Error(text); throw pe; }
                }
                const data = await resp.json();

                if (data.error) throw new Error(data.error);

                this.resume.loaded = true;
                this.resume.checksum = data.checksum;
                this.resume.firstName = data.first_name;
                this.resume.lastName = data.last_name;
                this.resume.contentPreview = '';
                this.resume.pasteText = '';
                this.loadCachedResumes();
            } catch (e) {
                this.resume.error = 'Failed: ' + e.message;
            } finally {
                this.resume.loading = false;
            }
        },

        clearResume() {
            this.resume.loaded = false;
            this.resume.checksum = null;
            this.resume.firstName = null;
            this.resume.lastName = null;
            this.resume.contentPreview = '';
            this.resume.showPreview = false;
            this.resume.error = null;
            this.clearResult();
        },

        // --- Job actions ---

        async scrapeJobUrl() {
            if (!this.job.url.trim()) return;

            this.job.loading = true;
            this.job.error = null;
            try {
                const resp = await fetch('/api/job/scrape', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: this.job.url }),
                });
                const data = await resp.json();

                if (data.error === 'cloudflare') {
                    this.job.error = 'Bot protection detected. Copy & paste the job description instead.';
                    window.open(this.job.url, '_blank');
                    return;
                }
                if (data.error) {
                    this.job.error = data.message || data.error;
                    return;
                }

                this.job.loaded = true;
                this.job.text = data.text;
                this.job.preview = data.text.substring(0, 200).replace(/\n/g, ' ');
                this.loadCachedJobs();
            } catch (e) {
                this.job.error = 'Scrape failed: ' + e.message;
            } finally {
                this.job.loading = false;
            }
        },

        async submitPasteJob() {
            if (!this.job.pasteText.trim()) return;
            this.job.loaded = true;
            this.job.text = this.job.pasteText;
            this.job.preview = this.job.pasteText.substring(0, 200).replace(/\n/g, ' ');
            this.job.pasteText = '';
            const resp = await fetch('/api/job/paste', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: this.job.text }),
            });
            this.loadCachedJobs();
        },

        clearJob() {
            this.job.loaded = false;
            this.job.text = '';
            this.job.preview = '';
            this.job.url = '';
            this.job.error = null;
            this.job.showPreview = false;
            this.clearResult();
        },

        async loadCachedJobs() {
            try {
                const resp = await fetch('/api/job/cached');
                this.cachedJobs = (await resp.json()).slice().reverse();
            } catch (e) {
                console.error('Failed to load cached jobs:', e);
            }
        },

        async removeCachedJob(checksum) {
            await fetch('/api/job/cached/' + checksum, { method: 'DELETE' });
            this.cachedJobs = this.cachedJobs.filter(j => j.checksum !== checksum);
            // If the removed job was the currently loaded one, clear it
            if (this.job.loaded && !this.job.text) {
                this.clearJob();
            }
        },

        async selectCachedJob(j) {
            this.job.loading = true;
            this.job.error = null;
            try {
                await this._loadCachedJob(j);
                await fetch('/api/job/select/' + j.checksum, { method: 'POST' });
            } catch (e) {
                this.job.error = 'Failed to load job: ' + e.message;
            } finally {
                this.job.loading = false;
            }
        },

        clearResult() {
            this.optimization.finalResult = null;
            this.optimization.iterations = [];
            this.optimization.logs = [];
            this.optimization.logsOpen = false;
            this.optimization.error = null;
            this.optimization.cancelled = false;
            this.optimization.statusMessage = '';
            this.optimization.id = null;
            this.expandedIterations = {};
            this.showPdfPreview = false;
        },

        // --- Optimization ---

        async checkActiveOptimization() {
            try {
                const resp = await fetch('/api/optimize/status');
                const data = await resp.json();
                if (data.active && data.id) {
                    this.optimization.running = !data.done;
                    this.optimization.id = data.id;
                    this.optimization.statusMessage = 'Reconnecting...';
                    await this.connectToStream(data.id);
                }
            } catch (e) {
                console.error('Failed to check active optimization:', e);
            }
        },

        async connectToStream(id) {
            const abortController = new AbortController();
            this.optimization.abortController = abortController;
            this.optimization.running = true;

            try {
                const resp = await fetch('/api/optimize/stream/' + id, {
                    signal: abortController.signal,
                });

                if (!resp.ok) {
                    this.optimization.running = false;
                    return;
                }

                await this._readSSEStream(resp);
            } catch (e) {
                if (e.name !== 'AbortError') {
                    this.optimization.error = 'Connection failed: ' + e.message;
                }
            } finally {
                this.optimization.running = false;
                this.optimization.abortController = null;
            }
        },

        async startOptimization() {
            if (!this.canOptimize) return;

            this.optimization.running = true;
            this.optimization.iterations = [];
            this.optimization.logs = [];
            this.optimization.logsOpen = false;
            this.optimization.finalResult = null;
            this.optimization.error = null;
            this.optimization.cancelled = false;
            this.optimization.statusMessage = 'Starting...';
            this.expandedIterations = {};
            this.showPdfPreview = false;

            const abortController = new AbortController();
            this.optimization.abortController = abortController;

            const body = {
                resume_checksum: this.resume.checksum,
                job_text: this.job.text,
                sequential: this.settings.sequential,
                debug: this.settings.debug,
                no_shame: this.settings.noShame,
                language: this.settings.language,
                max_iterations: this.settings.maxIterations,
                instructions: this.settings.instructions || null,
                pro_model: this.settings.proModel || null,
                flash_model: this.settings.flashModel || null,
                embedding_model: this.settings.embeddingModel || null,
                reasoning_effort: this.settings.reasoningEffort || null,
                api_keys: this._nonEmptyApiKeys() || null,
                filter_thresholds: this.settings.thresholds,
            };

            try {
                const resp = await fetch('/api/optimize', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                    signal: abortController.signal,
                });

                if (resp.status === 409) {
                    const data = await resp.json();
                    this.optimization.id = data.id;
                    this.optimization.statusMessage = 'Reconnecting to running optimization...';
                    this.optimization.iterations = [];
                    await this.connectToStream(data.id);
                    return;
                }

                if (!resp.ok) {
                    const data = await resp.json();
                    this.optimization.error = data.error || 'Failed to start optimization';
                    return;
                }

                await this._readSSEStream(resp);
            } catch (e) {
                if (e.name !== 'AbortError') {
                    this.optimization.error = 'Connection failed: ' + e.message;
                }
            } finally {
                this.optimization.running = false;
                this.optimization.abortController = null;
            }
        },

        async _readSSEStream(resp) {
            const reader = resp.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });

                const lines = buffer.split('\n');
                buffer = lines.pop();

                let eventType = null;
                let eventData = '';

                for (const line of lines) {
                    if (line.startsWith('event: ')) {
                        eventType = line.substring(7).trim();
                    } else if (line.startsWith('data: ')) {
                        eventData = line.substring(6);
                    } else if (line === '' && eventType && eventData) {
                        this.handleSSEEvent(eventType, JSON.parse(eventData));
                        eventType = null;
                        eventData = '';
                    }
                }
            }
        },

        async cancelOptimization() {
            try {
                await fetch('/api/optimize/cancel', { method: 'POST' });
            } catch (e) {
                console.error('Cancel request failed:', e);
            }
            if (this.optimization.abortController) {
                this.optimization.abortController.abort();
            }
            this.optimization.running = false;
            this.optimization.cancelled = true;
            this.optimization.statusMessage = '';
        },

        handleSSEEvent(event, data) {
            switch (event) {
                case 'started':
                    this.optimization.id = data.id;
                    break;
                case 'status':
                    this.optimization.statusMessage = data.message;
                    break;
                case 'iteration':
                    // Avoid duplicates on reconnect replay
                    if (!this.optimization.iterations.find(i => i.iteration === data.iteration)) {
                        this.optimization.iterations.push(data);
                    }
                    this.optimization.statusMessage = `Iteration ${data.iteration}/${data.max_iterations}`;
                    this.expandedIterations[data.iteration] = true;
                    if (data.iteration > 1) {
                        this.expandedIterations[data.iteration - 1] = false;
                    }
                    break;
                case 'complete':
                    this.optimization.finalResult = data;
                    this.optimization.statusMessage = '';
                    this.optimization.running = false;
                    this.loadHistory();
                    break;
                case 'cancelled':
                    this.optimization.cancelled = true;
                    this.optimization.statusMessage = '';
                    this.optimization.running = false;
                    break;
                case 'log':
                    this.optimization.logs.push(data);
                    // Cap at 200 entries
                    if (this.optimization.logs.length > 200) {
                        this.optimization.logs.splice(0, this.optimization.logs.length - 200);
                    }
                    this._scrollLogPanel();
                    break;
                case 'error':
                    this.optimization.error = data.message;
                    this.optimization.running = false;
                    break;
            }
        },

        _scrollLogPanel() {
            this.$nextTick(() => {
                const el = document.getElementById('log-panel');
                if (el) el.scrollTop = el.scrollHeight;
            });
        },

        toggleIteration(idx) {
            this.expandedIterations[idx] = !this.expandedIterations[idx];
        },

        // --- Helpers ---

        _nonEmptyApiKeys() {
            const keys = {};
            let hasAny = false;
            for (const [k, v] of Object.entries(this.settings.apiKeys)) {
                if (v) { keys[k] = v; hasAny = true; }
            }
            return hasAny ? keys : null;
        },

        // --- History ---

        async openFolder() {
            await fetch('/api/open-folder', { method: 'POST' });
        },

        formatTimestamp(iso) {
            const d = new Date(iso);
            return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        },

        formatShortDate(iso) {
            if (!iso) return '';
            const d = new Date(iso);
            return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
        },

        shortenUrl(url) {
            if (!url || url === 'pasted') return url;
            try {
                const u = new URL(url);
                const path = u.pathname.length > 30 ? u.pathname.substring(0, 30) + '...' : u.pathname;
                return u.hostname + path;
            } catch { return url.substring(0, 40); }
        },
    }));
});
