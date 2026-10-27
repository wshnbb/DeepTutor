import { binding } from './binding.js';

const profilePath = 'services.llm.profiles.0';
const profileId = 'llm-magpie-desktop';
const modelId = 'llm-magpie-selected';

export const agent = {
  id: 'deeptutor',
  name: 'DeepTutor Desktop',
  config: binding.config,
  dir: binding.dir,
  format: 'json',
  model: profilePath + '.models.0.model',
  ua: ['deeptutor', 'deeptutor-desktop'],
  icon: 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"%3E%3Crect width="64" height="64" rx="14" fill="%23526a42"/%3E%3Cpath d="M18 15h13c13 0 20 6 20 17s-7 17-20 17H18zm9 8v18h4c8 0 12-3 12-9s-4-9-12-9z" fill="%23f4f7ed"/%3E%3C/svg%3E',
  notice: '模型已写入。请右键 DeepTutor 托盘图标选择「退出 DeepTutor」，再重新启动；只关闭窗口不会重启服务。',
};

export function connect({ gateway, model }) {
  if (!model || typeof model.id !== 'string' || !model.id) {
    throw new Error('Please select a model from Magpie.');
  }
  if (!gateway || !gateway.v1 || !gateway.key) {
    throw new Error('Magpie did not provide its gateway address and key.');
  }
  const endpoints = model.native_endpoints || [];
  const chat = endpoints.includes('/v1/chat/completions') && !endpoints.includes('/v1/responses');
  const selected = {
    id: modelId,
    name: model.name || model.id,
    model: model.id,
    capabilities: { vision: Boolean(model.images) },
  };
  if (model.context > 0) selected.context_window = model.context;
  const profile = {
    id: profileId,
    name: 'Magpie',
    binding: 'openai',
    connection_id: '',
    base_url: gateway.v1,
    api_key: gateway.key,
    api_format: chat ? 'openai_chat' : 'openai_responses',
    wire_api: chat ? 'chat_completions' : 'responses',
    proxy: '',
    extra_headers: { 'User-Agent': 'DeepTutor-Desktop/0.1.2' },
    models: [selected],
  };
  return {
    'services.llm.active_profile_id': profileId,
    'services.llm.active_model_id': modelId,
    // Keep this typed parent in Magpie's stash so disconnect also restores
    // arrays and any pre-existing first profile, including its other models.
    [profilePath]: profile,
    // A scalar key lets Magpie recognize its own connection and usage agent.
    [profilePath + '.api_key']: gateway.key,
  };
}
