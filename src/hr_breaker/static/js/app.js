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
            inputMode: 'upload', // 'upload' | 'paste' | 'profile'
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
            showIntermediateLogs: false,
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
            usageEntries: [],
            usageTotals: { requests: 0, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0 },
            finalResult: null,
            error: null,
            cancelled: false,
        },

        // Profile state
        profile: {
            list: [],
            selected: null,
            documents: [],
            loading: false,
            uploading: false,
            synthesizing: false,
            activatingId: null,
            creating: false,
            newName: '',
            newInstructions: '',
            extractionLogs: [],
            logsOpen: false,
            error: null,
            uploadProgress: null,
            showNoteForm: false,
            noteTitle: '',
            noteContent: '',
            addingNote: false,
            _pollTimer: null,

        },

        // Provider state
        provider: {
            activeScope: 'pro',
            shared: { selected: 'gemini', baseUrl: '' },
            scopes: {
                pro: { selected: null, baseUrl: null, status: 'unknown', message: '', detail: '', chatModels: [], embeddingModels: [], checking: false, _debounceTimer: null, _requestId: 0 },
                flash: { selected: null, baseUrl: null, status: 'unknown', message: '', detail: '', chatModels: [], embeddingModels: [], checking: false, _debounceTimer: null, _requestId: 0 },
                embedding: { selected: null, baseUrl: null, status: 'unknown', message: '', detail: '', chatModels: [], embeddingModels: [], checking: false, _debounceTimer: null, _requestId: 0 },
            },
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

        get resumePickerEntries() {
            const profileNames = new Set(
                this.profile.list
                    .map((p) => (p.display_name || p.id).trim())
                    .filter(Boolean)
            );
            const resumeEntries = this.cachedResumes.map((r) => {
                const resumeName = [r.first_name, r.last_name].filter(Boolean).join(' ') || 'Unknown';
                const inferredProfileName = !r.source_type && (r.filename || 'pasted') === 'pasted' && profileNames.has(resumeName)
                    ? resumeName
                    : null;
                return {
                    key: `resume:${r.checksum}`,
                    kind: 'resume',
                    name: resumeName,
                    meta: r.source_type === 'profile' || inferredProfileName
                        ? `from profile: ${r.source_profile_name || inferredProfileName || resumeName || 'unknown'}`
                        : (r.filename || 'pasted'),
                    timestamp: r.timestamp,
                    resume: r,
                };
            });
            const hiddenProfileIds = new Set(
                this.cachedResumes
                    .filter((r) => r.source_type === 'profile')
                    .map((r) => r.source_profile_id)
                    .filter(Boolean)
            );
            const hiddenProfileNames = new Set(
                this.cachedResumes
                    .map((r) => {
                        const resumeName = [r.first_name, r.last_name].filter(Boolean).join(' ').trim();
                        if (r.source_type === 'profile') {
                            return (r.source_profile_name || resumeName).trim();
                        }
                        return (r.filename || 'pasted') === 'pasted' && profileNames.has(resumeName) ? resumeName : null;
                    })
                    .filter(Boolean)
            );
            const profileEntries = this.profile.list
                .filter((p) => (p.document_count || 0) > 0)
                .filter((p) => !hiddenProfileIds.has(p.id))
                .filter((p) => !hiddenProfileNames.has((p.display_name || p.id).trim()))
                .map((p) => ({
                    key: `profile:${p.id}`,
                    kind: 'profile',
                    name: p.display_name || p.id,
                    meta: `profile · ${p.document_count || 0} document(s)`,
                    timestamp: null,
                    profile: p,
                }));
            return [...resumeEntries, ...profileEntries];
        },

        async init() {
            this._restoreFromStorage();
            await Promise.all([
                this.loadSettings(),
                this.loadCachedResumes(),
                this.loadProfiles(),
                this.loadCachedJobs(),
                this.loadHistory(),
                this.checkActiveOptimization(),
            ]);
            // Auto-select newest (by mtime) without touching — mtime is the source of truth
            if (this.cachedResumes.length > 0) this._loadCachedResume(this.cachedResumes[0]);
            if (this.cachedJobs.length > 0) await this._loadCachedJob(this.cachedJobs[0]);

            // Watch for changes and persist
            this.$watch('settings', () => this._saveToStorage());

            // Load provider catalogs after settings, so env keys are known
            await this.checkAllProviders();
        },

        _storageKey: 'hr-breaker-state',

        _saveToStorage() {
            const state = {
                settings: this.settings,
                drawerSections: this.drawerSections,
                provider: this._providerPreferences(),
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
                    Object.assign(this.settings, state.settings);
                    this._restoredFromStorage = true;
                }
                if (state.drawerSections) {
                    Object.assign(this.drawerSections, state.drawerSections);
                }
                if (state.provider && typeof state.provider === 'object') {
                    this._applyProviderPreferences(state.provider);
                } else {
                    this._applyProviderPreferences({
                        shared: {
                            selected: state.providerSelected,
                            customBaseUrl: state.providerBaseUrl,
                        },
                    });
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

        async selectResumeSource(entry) {
            if (entry.kind === 'profile') {
                await this.selectProfile(entry.profile);
                return;
            }
            await this.selectCachedResume(entry.resume);
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

        _resumeRunOverrides() {
            return {
                flash_model: this.settings.flashModel || null,
                reasoning_effort: this.settings.reasoningEffort || null,
                api_keys: this._nonEmptyApiKeys() || null,
                providers: this._providerOverridesForRequest(['flash']),
            };
        },

        _appendRunOverridesToFormData(formData, overrides) {
            if (overrides.flash_model) formData.append('flash_model', overrides.flash_model);
            if (overrides.reasoning_effort) formData.append('reasoning_effort', overrides.reasoning_effort);
            if (overrides.api_keys) formData.append('api_keys_json', JSON.stringify(overrides.api_keys));
            if (overrides.providers) formData.append('providers_json', JSON.stringify(overrides.providers));
        },

        async handleFileUpload(event) {
            const file = event.target.files[0];
            if (!file) return;

            this.resume.loading = true;
            this.resume.error = null;
            try {
                const formData = new FormData();
                formData.append('file', file);
                this._appendRunOverridesToFormData(formData, this._resumeRunOverrides());
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
                    body: JSON.stringify({
                        content: this.resume.pasteText,
                        ...this._resumeRunOverrides(),
                    }),
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
            this.optimization.usageEntries = [];
            this.optimization.usageTotals = { requests: 0, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0 };
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
            this.optimization.usageEntries = [];
            this.optimization.usageTotals = { requests: 0, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0 };
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
                providers: this._providerOverridesForRequest(),
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
                    this.optimization.logs.push({ ...data, kind: 'log' });
                    if (this.optimization.logs.length > 200) {
                        this.optimization.logs.splice(0, this.optimization.logs.length - 200);
                    }
                    this._scrollLogPanel();
                    break;
                case 'usage':
                    this.optimization.usageTotals = data.totals || this.optimization.usageTotals;
                    this.optimization.usageEntries.push(data.entry);
                    if (this.optimization.usageEntries.length > 200) {
                        this.optimization.usageEntries.splice(0, this.optimization.usageEntries.length - 200);
                    }
                    this.optimization.logs.push({
                        kind: 'usage',
                        level: data.entry.usage_available === false ? 'WARNING' : 'INFO',
                        message: this.formatUsageEntry(data.entry),
                        usage_available: data.entry.usage_available,
                    });
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

        formatCount(value) {
            return Number(value || 0).toLocaleString();
        },

        visibleOptimizationLogs() {
            return this.optimization.logs.filter(log => log.kind !== 'usage' || this.settings.showIntermediateLogs || log.usage_available === false);
        },

        usageStatusLines() {
            const summaries = this.usageSummariesByModel();
            if (summaries.length === 0) {
                if (this.optimization.usageTotals.requests === 0
                    && this.optimization.usageTotals.input_tokens === 0
                    && this.optimization.usageTotals.output_tokens === 0
                    && this.optimization.usageTotals.cache_read_tokens === 0
                    && this.optimization.usageTotals.cache_write_tokens === 0) {
                    return [];
                }
                return [this.formatUsageSummary({ label: 'Total', ...this.optimization.usageTotals })];
            }
            if (summaries.length === 1) {
                return [this.formatUsageSummary(summaries[0])];
            }
            return [
                this.formatUsageSummary({ label: 'Total', ...this.optimization.usageTotals }),
                ...summaries.map(summary => this.formatUsageSummary(summary)),
            ];
        },

        usageSummariesByModel() {
            const grouped = new Map();
            for (const entry of this.optimization.usageEntries) {
                if (entry.usage_available === false) continue;
                const key = `${entry.model}|||${entry.provider}`;
                if (!grouped.has(key)) {
                    grouped.set(key, {
                        label: `${entry.model} / ${entry.provider}`,
                        requests: 0,
                        input_tokens: 0,
                        output_tokens: 0,
                        cache_read_tokens: 0,
                        cache_write_tokens: 0,
                    });
                }
                const summary = grouped.get(key);
                summary.requests += Number(entry.requests || 0);
                summary.input_tokens += Number(entry.input_tokens || 0);
                summary.output_tokens += Number(entry.output_tokens || 0);
                summary.cache_read_tokens += Number(entry.cache_read_tokens || 0);
                summary.cache_write_tokens += Number(entry.cache_write_tokens || 0);
            }
            return [...grouped.values()];
        },

        formatUsageSummary(summary) {
            return `${summary.label}: Requests ${this.formatCount(summary.requests)} | Cache ${this.formatCount(summary.cache_read_tokens)}r / ${this.formatCount(summary.cache_write_tokens)}w | Input ${this.formatCount(summary.input_tokens)} | Output ${this.formatCount(summary.output_tokens)}`;
        },

        formatUsageEntry(entry) {
            if (entry.usage_available === false) {
                return `${entry.timestamp} ${entry.component}: ${entry.model} / ${entry.provider}, usage unavailable`;
            }
            return `${entry.timestamp} ${entry.component}: ${entry.model} / ${entry.provider}, requests: ${this.formatCount(entry.requests)}, cache: ${this.formatCount(entry.cache_read_tokens)}r / ${this.formatCount(entry.cache_write_tokens)}w, input: ${this.formatCount(entry.input_tokens)}, output: ${this.formatCount(entry.output_tokens)}`;
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

        _profileRunOverrides() {
            const overrides = {
                flash_model: this.settings.flashModel || null,
                embedding_model: this.settings.embeddingModel || null,
                reasoning_effort: this.settings.reasoningEffort || null,
                api_keys: this._nonEmptyApiKeys() || null,
                providers: this._providerOverridesForRequest(['flash', 'embedding']),
            };
            return overrides;
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

        // --- Provider actions ---

        providerScopes() {
            return ['pro', 'flash', 'embedding'];
        },

        providerTabLabel(scope) {
            return { pro: 'Pro', flash: 'Flash', embedding: 'Embedding' }[scope] || scope;
        },

        providerRuntime(scope = this.provider.activeScope) {
            return this.provider.scopes[scope];
        },

        providerSelected(scope = this.provider.activeScope) {
            return this._providerEffectivePreferences(scope).selected;
        },

        providerBaseUrl(scope = this.provider.activeScope) {
            return this._providerEffectivePreferences(scope).customBaseUrl;
        },

        providerUsesShared(scope = this.provider.activeScope) {
            return scope !== 'pro' && this.providerRuntime(scope).selected == null && this.providerRuntime(scope).baseUrl == null;
        },

        providerScopeSummary(scope = this.provider.activeScope) {
            if (scope === 'pro') {
                return 'Pro is the shared provider default for Flash and Embedding unless overridden.';
            }
            return this.providerUsesShared(scope)
                ? `Using Pro provider by default for ${this.providerTabLabel(scope)}.`
                : `Using a separate provider for ${this.providerTabLabel(scope)}.`;
        },

        canResetProviderOverride(scope = this.provider.activeScope) {
            return scope !== 'pro' && !this.providerUsesShared(scope);
        },

        providerChatModels(scope) {
            return this.providerRuntime(scope).chatModels || [];
        },

        providerEmbeddingModels(scope) {
            return this.providerRuntime(scope).embeddingModels || [];
        },

        _providerPreferences() {
            return {
                activeScope: this.provider.activeScope || 'pro',
                shared: {
                    selected: this.provider.shared.selected || 'gemini',
                    customBaseUrl: (this.provider.shared.baseUrl || '').trim(),
                },
                overrides: {
                    flash: this._providerOverridePreferences('flash'),
                    embedding: this._providerOverridePreferences('embedding'),
                },
            };
        },

        _providerOverridePreferences(scope) {
            if (scope === 'pro') return null;
            const state = this.providerRuntime(scope);
            if (state.selected == null && state.baseUrl == null) return null;
            return {
                selected: state.selected || null,
                customBaseUrl: (state.baseUrl || '').trim(),
            };
        },

        _applyProviderPreferences(preferences) {
            if (!preferences || typeof preferences !== 'object') return;
            const shared = preferences.shared && typeof preferences.shared === 'object' ? preferences.shared : preferences;
            this.provider.activeScope = preferences.activeScope || 'pro';
            this.provider.shared.selected = shared.selected || 'gemini';
            this.provider.shared.baseUrl = String(shared.customBaseUrl || '');

            for (const scope of ['flash', 'embedding']) {
                const state = this.providerRuntime(scope);
                state.selected = null;
                state.baseUrl = null;
                const override = preferences.overrides && typeof preferences.overrides === 'object' ? preferences.overrides[scope] : null;
                if (override && typeof override === 'object') {
                    state.selected = override.selected || null;
                    state.baseUrl = override.customBaseUrl != null ? String(override.customBaseUrl) : null;
                    this._collapseProviderOverride(scope);
                }
            }
        },

        _providerEffectivePreferences(scope = this.provider.activeScope) {
            if (scope === 'pro') {
                return {
                    selected: this.provider.shared.selected || 'gemini',
                    customBaseUrl: (this.provider.shared.baseUrl || '').trim(),
                };
            }
            const state = this.providerRuntime(scope);
            return {
                selected: state.selected || this.provider.shared.selected || 'gemini',
                customBaseUrl: ((state.baseUrl != null ? state.baseUrl : this.provider.shared.baseUrl) || '').trim(),
            };
        },

        _matchingProviderScopes(scope = this.provider.activeScope) {
            const prefs = this._providerEffectivePreferences(scope);
            return this.providerScopes().filter(candidate => {
                const other = this._providerEffectivePreferences(candidate);
                return other.selected === prefs.selected && other.customBaseUrl === prefs.customBaseUrl;
            });
        },

        _collapseProviderOverride(scope) {
            if (scope === 'pro') return;
            const state = this.providerRuntime(scope);
            const sharedSelected = this.provider.shared.selected || 'gemini';
            const sharedBaseUrl = (this.provider.shared.baseUrl || '').trim();
            const selected = state.selected || sharedSelected;
            const baseUrl = (state.baseUrl || '').trim();
            if (selected === sharedSelected && baseUrl === sharedBaseUrl) {
                state.selected = null;
                state.baseUrl = null;
            }
        },

        _resetProviderStatus(scope = this.provider.activeScope, message = '') {
            for (const candidate of this._matchingProviderScopes(scope)) {
                const state = this.providerRuntime(candidate);
                state._requestId = (state._requestId || 0) + 1;
                state.status = 'unknown';
                state.message = message;
                state.detail = '';
                state.chatModels = [];
                state.embeddingModels = [];
                state.checking = false;
            }
        },

        _providerKeyName(scope = this.provider.activeScope) {
            const providerName = this.providerSelected(scope);
            return providerName === 'custom' ? 'openai' : (providerName || 'gemini');
        },

        _providerApiKey(scope = this.provider.activeScope) {
            return this.settings.apiKeys[this._providerKeyName(scope)] || '';
        },

        _providerHasEnvKey(scope = this.provider.activeScope) {
            return this.appSettings.apiKeysSet[this._providerKeyName(scope)] || false;
        },

        _providerCheckRequest(scope = this.provider.activeScope) {
            const preferences = this._providerEffectivePreferences(scope);
            const body = {
                provider: preferences.selected,
                api_key: this._providerApiKey(scope) || null,
            };
            if (preferences.selected === 'custom' && preferences.customBaseUrl) {
                body.base_url = preferences.customBaseUrl;
            }
            return body;
        },

        _providerOverridesForRequest(scopes = this.providerScopes()) {
            const providers = {};
            for (const scope of scopes) {
                const preferences = this._providerEffectivePreferences(scope);
                providers[scope] = {
                    provider: preferences.selected || null,
                    base_url: preferences.selected === 'custom' ? (preferences.customBaseUrl || null) : null,
                };
            }
            return providers;
        },

        setProviderScope(scope) {
            this.provider.activeScope = scope;
            this._saveToStorage();
            const state = this.providerRuntime(scope);
            if (!state.chatModels.length && !state.embeddingModels.length) {
                this.checkProvider(scope);
            }
        },

        resetProviderOverride(scope = this.provider.activeScope) {
            if (scope === 'pro') return;
            const state = this.providerRuntime(scope);
            state.selected = null;
            state.baseUrl = null;
            this._saveToStorage();
            this._resetProviderStatus(scope);
            this.checkProvider(scope);
        },

        handleProviderSelectionChange(value, scope = this.provider.activeScope) {
            if (scope === 'pro') {
                this.provider.shared.selected = value || 'gemini';
                if (this.provider.shared.selected !== 'custom') {
                    this.provider.shared.baseUrl = '';
                }
            } else {
                const state = this.providerRuntime(scope);
                state.selected = value || 'gemini';
                if (state.selected !== 'custom') {
                    state.baseUrl = '';
                }
                this._collapseProviderOverride(scope);
            }
            this._saveToStorage();
            this._resetProviderStatus(scope);
            this.checkProvider(scope);
        },

        handleProviderBaseUrlInput(value, scope = this.provider.activeScope) {
            if (scope === 'pro') {
                this.provider.shared.baseUrl = String(value || '');
            } else {
                const state = this.providerRuntime(scope);
                state.selected = state.selected || this.provider.shared.selected || 'gemini';
                state.baseUrl = String(value || '');
                this._collapseProviderOverride(scope);
            }
            this._saveToStorage();
            this._resetProviderStatus(scope);
            this._debounceCheckProvider(scope);
        },

        _debounceCheckProvider(scope = this.provider.activeScope) {
            const state = this.providerRuntime(scope);
            if (state._debounceTimer) clearTimeout(state._debounceTimer);
            state._debounceTimer = setTimeout(() => this.checkProvider(scope), 500);
        },

        async checkAllProviders() {
            for (const scope of this.providerScopes()) {
                await this.checkProvider(scope);
            }
        },

        async checkProvider(scope = this.provider.activeScope) {
            const requestBody = this._providerCheckRequest(scope);
            const hasEnvKey = this._providerHasEnvKey(scope);
            const matchingScopes = this._matchingProviderScopes(scope);

            if (!requestBody.api_key && !hasEnvKey) {
                this._resetProviderStatus(scope, 'Enter API key to load models');
                return;
            }

            const requestId = (this.providerRuntime(scope)._requestId || 0) + 1;
            for (const candidate of matchingScopes) {
                const state = this.providerRuntime(candidate);
                state._requestId = requestId;
                state.checking = true;
            }

            try {
                const resp = await fetch('/api/providers/check', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(requestBody),
                });
                const data = await resp.json();
                if (!resp.ok) {
                    throw new Error(data.detail || data.error || data.message || 'Request failed');
                }
                for (const candidate of matchingScopes) {
                    const state = this.providerRuntime(candidate);
                    if (state._requestId !== requestId) continue;
                    state.status = data.status.state;
                    state.message = data.status.message;
                    state.detail = data.status.detail || '';
                    state.chatModels = data.chat_models || [];
                    state.embeddingModels = data.embedding_models || [];
                }
            } catch (e) {
                for (const candidate of matchingScopes) {
                    const state = this.providerRuntime(candidate);
                    if (state._requestId !== requestId) continue;
                    state.status = 'warning';
                    state.message = 'Check failed';
                    state.detail = e instanceof Error ? e.message : String(e);
                    state.chatModels = [];
                    state.embeddingModels = [];
                }
            } finally {
                for (const candidate of matchingScopes) {
                    const state = this.providerRuntime(candidate);
                    if (state._requestId === requestId) state.checking = false;
                }
            }
        },

        // --- Profile actions ---

        async loadProfiles() {
            this.profile.loading = true;
            try {
                const resp = await fetch('/api/profile/');
                this.profile.list = await resp.json();
            } catch (e) {
                console.error('Failed to load profiles:', e);
            } finally {
                this.profile.loading = false;
            }
        },

        async createProfile() {
            if (!this.profile.newName.trim()) return;
            this.profile.loading = true;
            try {
                const resp = await fetch('/api/profile/', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name: this.profile.newName.trim(),
                        instructions: this.profile.newInstructions.trim() || null,
                    }),
                });
                if (!resp.ok) throw new Error(await resp.text());
                const p = await resp.json();
                this.profile.list.unshift({ ...p, document_count: 0, created_at: null });
                this.profile.creating = false;
                this.profile.newName = '';
                this.profile.newInstructions = '';
            } catch (e) {
                console.error('Failed to create profile:', e);
            } finally {
                this.profile.loading = false;
            }
        },

        async deleteProfile(pid) {
            if (!confirm('Delete this profile and all its documents?')) return;
            await fetch('/api/profile/' + pid, { method: 'DELETE' });
            this.profile.list = this.profile.list.filter(p => p.id !== pid);
            if (this.profile.selected && this.profile.selected.id === pid) {
                this.closeProfileEditor();
            }
        },

        async selectProfile(p) {
            if ((p.document_count || 0) === 0) {
                await this.openProfileEditor(p);
                return;
            }
            this.profile.activatingId = p.id;
            this.resume.error = null;
            this.profile.error = null;
            try {
                await this._synthesizeProfileToResume(p.id);
            } catch (e) {
                this.resume.error = 'Profile selection failed: ' + e.message;
            } finally {
                this.profile.activatingId = null;
            }
        },

        async openProfileEditor(p) {
            this.profile.selected = p;
            this.profile.documents = [];
            this.profile.extractionLogs = [];
            this.profile.error = null;
            await this._refreshProfileDocs(p.id);
            this._startExtractionPoll(p.id);
        },

        closeProfileEditor() {
            this._stopExtractionPoll();
            this.profile.selected = null;
            this.profile.documents = [];
            this.profile.extractionLogs = [];
            this.profile.showNoteForm = false;
            this.profile.error = null;
        },

        _updateProfileSummary(pid, updates) {
            this.profile.list = this.profile.list.map((profile) => profile.id === pid ? { ...profile, ...updates } : profile);
            if (this.profile.selected && this.profile.selected.id === pid) {
                this.profile.selected = { ...this.profile.selected, ...updates };
            }
        },

        async _refreshProfileDocs(pid) {
            try {
                const resp = await fetch('/api/profile/' + pid);
                if (!resp.ok) return;
                const data = await resp.json();
                this.profile.documents = data.documents || [];
                this._updateProfileSummary(pid, {
                    display_name: data.name || this.profile.selected?.display_name,
                    document_count: (data.documents || []).length,
                });
            } catch (e) {
                console.error('Failed to load profile docs:', e);
            }
        },

        _startExtractionPoll(pid) {
            this._stopExtractionPoll();
            const poll = async () => {
                try {
                    const resp = await fetch('/api/profile/' + pid + '/extraction-status');
                    if (!resp.ok) return;
                    const data = await resp.json();
                    // Append new logs
                    if (data.logs && data.logs.length > 0) {
                        this.profile.extractionLogs.push(...data.logs);
                        if (this.profile.extractionLogs.length > 200) {
                            this.profile.extractionLogs.splice(0, this.profile.extractionLogs.length - 200);
                        }
                    }
                    // Refresh docs to show updated extraction_status
                    if (data.active) {
                        await this._refreshProfileDocs(pid);
                        this.profile._pollTimer = setTimeout(poll, 1500);
                    } else {
                        await this._refreshProfileDocs(pid);
                    }
                } catch (e) {
                    // Stop polling on error
                }
            };
            this.profile._pollTimer = setTimeout(poll, 800);
        },

        _stopExtractionPoll() {
            if (this.profile._pollTimer) {
                clearTimeout(this.profile._pollTimer);
                this.profile._pollTimer = null;
            }
        },

        async uploadProfileDoc(event) {
            const fileList = event.target.files;
            if (!fileList || !fileList.length || !this.profile.selected) return;
            // Snapshot files before any DOM changes
            const files = Array.from(fileList);
            this.profile.uploading = true;
            this.profile.error = null;
            this.profile.uploadProgress = files.length > 1 ? `Uploading 1/${files.length}...` : 'Uploading...';
            try {
                const pid = this.profile.selected.id;
                for (let i = 0; i < files.length; i++) {
                    this.profile.uploadProgress = files.length > 1 ? `Uploading ${i + 1}/${files.length}...` : 'Uploading...';
                    const formData = new FormData();
                    formData.append('file', files[i]);
                    this._appendRunOverridesToFormData(formData, this._profileRunOverrides());
                    const resp = await fetch('/api/profile/' + pid + '/document', {
                        method: 'POST',
                        body: formData,
                    });
                    if (!resp.ok) {
                        const text = await resp.text();
                        try { const j = JSON.parse(text); throw new Error(j.error || j.detail || text); }
                        catch (pe) { if (pe instanceof SyntaxError) throw new Error(text); throw pe; }
                    }
                }
                await this._refreshProfileDocs(pid);
                this._startExtractionPoll(pid);
            } catch (e) {
                this.profile.error = 'Upload failed: ' + e.message;
            } finally {
                this.profile.uploading = false;
                this.profile.uploadProgress = null;
                // Reset file input (if it's a real input element)
                if (event.target && event.target.value !== undefined) {
                    event.target.value = '';
                }
            }
        },

        async deleteProfileDoc(docId) {
            if (!this.profile.selected) return;
            await fetch('/api/profile/' + this.profile.selected.id + '/document/' + docId, { method: 'DELETE' });
            this.profile.documents = this.profile.documents.filter(d => d.id !== docId);
            this._updateProfileSummary(this.profile.selected.id, { document_count: this.profile.documents.length });
        },

        async reExtractProfile() {
            if (!this.profile.selected) return;
            this.profile.error = null;
            try {
                const pid = this.profile.selected.id;
                const resp = await fetch('/api/profile/' + pid + '/extract', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this._profileRunOverrides()),
                });
                const data = await resp.json();
                if (!resp.ok) { this.profile.error = data.error || 'Re-extract failed'; return; }
                await this._refreshProfileDocs(pid);
                this._startExtractionPoll(pid);
            } catch (e) {
                this.profile.error = 'Re-extract failed: ' + e.message;
            }
        },

        async addProfileNote() {
            if (!this.profile.selected || !this.profile.noteTitle.trim()) return;
            this.profile.addingNote = true;
            this.profile.error = null;
            try {
                const resp = await fetch('/api/profile/' + this.profile.selected.id + '/note', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title: this.profile.noteTitle.trim(), content: this.profile.noteContent.trim() }),
                });
                if (!resp.ok) {
                    const text = await resp.text();
                    try { const j = JSON.parse(text); throw new Error(j.detail || j.error || text); }
                    catch (pe) { if (pe instanceof SyntaxError) throw new Error(text); throw pe; }
                }
                this.profile.noteTitle = '';
                this.profile.noteContent = '';
                this.profile.addingNote = false;
                this.profile.showNoteForm = false;
                await this._refreshProfileDocs(this.profile.selected.id);
                this._startExtractionPoll(this.profile.selected.id);
            } catch (e) {
                this.profile.error = 'Failed to add note: ' + e.message;
                this.profile.addingNote = false;
            }
        },

        async _synthesizeProfileToResume(profileId) {
            const resp = await fetch('/api/profile/' + profileId + '/synthesize', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    job_text: this.job.text || null,
                    ...this._profileRunOverrides(),
                }),
            });
            if (!resp.ok) {
                const text = await resp.text();
                try { const j = JSON.parse(text); throw new Error(j.detail || j.error || text); }
                catch (pe) { if (pe instanceof SyntaxError) throw new Error(text); throw pe; }
            }
            const data = await resp.json();
            this.resume.loaded = true;
            this.resume.checksum = data.checksum;
            this.resume.firstName = data.first_name || null;
            this.resume.lastName = data.last_name || null;
            this.resume.contentPreview = '';
            this.resume.error = null;
            await this.loadCachedResumes();
        },

        async synthesizeProfile() {
            if (!this.profile.selected) return;
            this.profile.synthesizing = true;
            this.profile.error = null;
            try {
                await this._synthesizeProfileToResume(this.profile.selected.id);
            } catch (e) {
                this.profile.error = 'Synthesis failed: ' + e.message;
            } finally {
                this.profile.synthesizing = false;
            }
        },
    }));
});
