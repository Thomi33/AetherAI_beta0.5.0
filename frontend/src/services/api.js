import axios from 'axios';
import { API_BASE_URL, API_ENDPOINTS } from '../utils/constants';

// Create axios instance
const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Response interceptor for error handling
api.interceptors.response.use(
  (response) => response,
  (error) => {
    console.error('API Error:', error);
    return Promise.reject(error);
  }
);

// Chat Service
export const chatService = {
  sendMessage: async (message) => {
    try {
      const response = await api.post(API_ENDPOINTS.CHAT, { message });
      return response.data;
    } catch (error) {
      throw error;
    }
  },

  getConversations: async () => {
    try {
      const response = await api.get(API_ENDPOINTS.CONVERSATIONS);
      return response.data;
    } catch (error) {
      throw error;
    }
  },

  getConversation: async (id) => {
    try {
      const response = await api.get(API_ENDPOINTS.CONVERSATION(id));
      return response.data;
    } catch (error) {
      throw error;
    }
  },

  deleteConversation: async (id) => {
    try {
      const response = await api.delete(API_ENDPOINTS.CONVERSATION(id));
      return response.data;
    } catch (error) {
      throw error;
    }
  },
};

export default api;

api.interceptors.request.use((config) => {
  console.log(
    `[API] ${config.method?.toUpperCase()} ${config.baseURL}${config.url}`
  );
  return config;
});

api.interceptors.response.use(
  (response) => {
    console.log('[API] Response:', response.data);
    return response;
  },
  (error) => {
    console.error('[API] Error:', error);
    return Promise.reject(error);
  }
);
