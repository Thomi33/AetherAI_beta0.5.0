// API Configuration
export const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';
export const API_ENDPOINTS = {
  CHAT: '/api/chat',
  CONVERSATIONS: '/api/conversations',
  CONVERSATION: (id) => `/api/conversations/${id}`,
};

// Agent Configuration
export const AGENT_CONFIG = {
  name: 'Aether',
  description: 'Agente Local Inteligente',
  model: 'gemma4:12b',
};

// UI Configuration
export const UI_CONFIG = {
  SIDEBAR_WIDTH: 260,
  MESSAGE_MAX_LENGTH: 2000,
  ANIMATION_DURATION: 0.3,
};

// Message Roles
export const MESSAGE_ROLES = {
  USER: 'user',
  ASSISTANT: 'assistant',
  SYSTEM: 'system',
};
