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
            language: 'en',
            maxIterations: 5,
            instructions: '',
        },

        // App settings from server
        appSettings: {
            languages: [],
            proModel: '',
            flashModel: '',
        },

        // Optimization state
        optimization: {
            running: false,
            id: null,
            abortController: null,
            statusMessage: '',
            iterations: [],
            finalResult: null,
            error: null,
            cancelled: false,
        },

        // History
        history: [],

        // Expanded iteration indices
        expandedIterations: {},

        async init() {
            this._restoreFromStorage();
            await Promise.all([
                this.loadSettings(),
                this.loadCachedResumes(),
                this.loadHistory(),
                this.checkActiveOptimization(),
            ]);

            // Watch for changes and persist
            this.$watch('job.text', () => this._saveToStorage());
            this.$watch('job.loaded', () => this._saveToStorage());
            this.$watch('job.preview', () => this._saveToStorage());
            this.$watch('settings', () => this._saveToStorage());
        },

        _storageKey: 'hr-breaker-state',

        _saveToStorage() {
            const state = {
                job: { loaded: this.job.loaded, text: this.job.text, preview: this.job.preview },
                settings: { ...this.settings },
            };
            try { localStorage.setItem(this._storageKey, JSON.stringify(state)); } catch {}
        },

        _restoredFromStorage: false,

        _restoreFromStorage() {
            try {
                const raw = localStorage.getItem(this._storageKey);
                if (!raw) return;
                const state = JSON.parse(raw);
                if (state.job && state.job.loaded) {
                    this.job.loaded = true;
                    this.job.text = state.job.text;
                    this.job.preview = state.job.preview;
                }
                if (state.settings) {
                    Object.assign(this.settings, state.settings);
                    this._restoredFromStorage = true;
                }
            } catch {}
        },

        async loadSettings() {
            try {
                const resp = await fetch('/api/settings');
                const data = await resp.json();
                this.appSettings.languages = data.languages;
                this.appSettings.proModel = data.pro_model;
                this.appSettings.flashModel = data.flash_model;
                // Only apply server defaults if not restored from localStorage
                if (!this._restoredFromStorage) {
                    this.settings.language = data.default_language;
                    this.settings.maxIterations = data.max_iterations;
                }
            } catch (e) {
                console.error('Failed to load settings:', e);
            }
        },

        async loadCachedResumes() {
            try {
                const resp = await fetch('/api/resume/cached');
                const resumes = await resp.json();
                if (resumes.length > 0) {
                    const latest = resumes[resumes.length - 1];
                    this.resume.loaded = true;
                    this.resume.checksum = latest.checksum;
                    this.resume.firstName = latest.first_name;
                    this.resume.lastName = latest.last_name;
                    this.resume.contentPreview = latest.content_preview;
                    if (latest.instructions) {
                        this.settings.instructions = latest.instructions;
                    }
                }
            } catch (e) {
                console.error('Failed to load cached resumes:', e);
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
                const data = await resp.json();

                if (data.error) throw new Error(data.error);

                this.resume.loaded = true;
                this.resume.checksum = data.checksum;
                this.resume.firstName = data.first_name;
                this.resume.lastName = data.last_name;
                this.resume.contentPreview = data.content_preview;
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
                const data = await resp.json();

                if (data.error) throw new Error(data.error);

                this.resume.loaded = true;
                this.resume.checksum = data.checksum;
                this.resume.firstName = data.first_name;
                this.resume.lastName = data.last_name;
                this.resume.contentPreview = data.content_preview;
                this.resume.pasteText = '';
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
            } catch (e) {
                this.job.error = 'Scrape failed: ' + e.message;
            } finally {
                this.job.loading = false;
            }
        },

        submitPasteJob() {
            if (!this.job.pasteText.trim()) return;
            this.job.loaded = true;
            this.job.text = this.job.pasteText;
            this.job.preview = this.job.pasteText.substring(0, 200).replace(/\n/g, ' ');
            this.job.pasteText = '';
        },

        clearJob() {
            this.job.loaded = false;
            this.job.text = '';
            this.job.preview = '';
            this.job.url = '';
            this.job.error = null;
            this.job.showPreview = false;
            this._saveToStorage();
            this.clearResult();
        },

        clearResult() {
            this.optimization.finalResult = null;
            this.optimization.iterations = [];
            this.optimization.error = null;
            this.optimization.cancelled = false;
            this.optimization.statusMessage = '';
            this.optimization.id = null;
            this.expandedIterations = {};
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
            this.optimization.finalResult = null;
            this.optimization.error = null;
            this.optimization.cancelled = false;
            this.optimization.statusMessage = 'Starting...';
            this.expandedIterations = {};

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
                case 'error':
                    this.optimization.error = data.message;
                    this.optimization.running = false;
                    break;
            }
        },

        toggleIteration(idx) {
            this.expandedIterations[idx] = !this.expandedIterations[idx];
        },

        // --- History ---

        async openFolder() {
            await fetch('/api/open-folder', { method: 'POST' });
        },

        formatTimestamp(iso) {
            const d = new Date(iso);
            return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        },
    }));
});
