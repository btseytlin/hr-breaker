import json
import shutil
import subprocess
from pathlib import Path

import pytest


APP_JS_PATH = Path(__file__).resolve().parents[1] / "src/hr_breaker/static/js/app.js"


def _run_app_probe(script_body: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for frontend catalog tests")

    harness = f"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync({json.dumps(str(APP_JS_PATH))}, 'utf8');
let factory = null;
const document = {{ addEventListener(event, cb) {{ if (event === 'alpine:init') cb(); }} }};
const Alpine = {{ data(name, fn) {{ if (name === 'app') factory = fn; }} }};
const context = {{
  console,
  document,
  Alpine,
  URL,
  setTimeout,
  clearTimeout,
  fetch: async () => {{ throw new Error('fetch not stubbed'); }},
}};
vm.createContext(context);
vm.runInContext(source, context);
if (!factory) throw new Error('Failed to capture Alpine app factory');
const app = factory();
(async () => {{
{script_body}
}})().catch(err => {{
  console.error(err && err.stack ? err.stack : String(err));
  process.exit(1);
}});
"""
    completed = subprocess.run([node, "-e", harness], check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def test_on_model_input_fetches_catalog_for_typed_provider_and_scope_base_url():
    payload = _run_app_probe(
        """
  app.settings.flashModel = 'gemini/gemini-2.5-flash';
  app.customBaseUrlEnabled.flash = true;
  app.customBaseUrls.flash = 'http://127.0.0.1:8317/v1';
  app.appSettings.apiKeysSet.openai = true;
  app.appSettings.apiKeysSet.gemini = true;

  const requests = [];
  context.fetch = async (_url, options) => {
    requests.push(JSON.parse(options.body));
    return {
      ok: true,
      json: async () => ({
        status: { state: 'connected', message: 'Connected' },
        chat_models: [],
        embedding_models: [],
      }),
    };
  };

  app.onModelInput('flash', 'openai/gpt-5.4-mini');
  await new Promise(resolve => setTimeout(resolve, 0));
  process.stdout.write(JSON.stringify(requests[0]));
"""
    )

    assert payload == {
        "provider": "openai",
        "api_key": None,
        "base_url": "http://127.0.0.1:8317/v1",
    }


def test_catalog_message_uses_scope_specific_cache_key_for_same_provider():
    payload = _run_app_probe(
        """
  app.settings.proModel = 'openai/gpt-5.4';
  app.customBaseUrlEnabled.pro = true;
  app.customBaseUrls.pro = 'https://pro.example.test/v1';
  app.settings.flashModel = 'openai/gpt-5.4-mini';
  app.customBaseUrlEnabled.flash = true;
  app.customBaseUrls.flash = 'https://flash.example.test/v1';

  app.modelCatalog[app._catalogCacheKey('openai', app._customBaseUrlForScope('pro'))] = {
    status: 'warning',
    message: 'pro endpoint failed',
    detail: '',
    checking: false,
    chatModels: [],
    embeddingModels: [],
  };
  app.modelCatalog[app._catalogCacheKey('openai', app._customBaseUrlForScope('flash'))] = {
    status: 'warning',
    message: 'flash endpoint failed',
    detail: '',
    checking: false,
    chatModels: [],
    embeddingModels: [],
  };

  const pro = app.catalogMessageForText('openai/gpt-5.4', 'pro');
  const flash = app.catalogMessageForText('openai/gpt-5.4-mini', 'flash');
  process.stdout.write(JSON.stringify({ pro: pro?.text || null, flash: flash?.text || null }));
"""
    )

    assert payload == {
        "pro": "pro endpoint failed",
        "flash": "flash endpoint failed",
    }
