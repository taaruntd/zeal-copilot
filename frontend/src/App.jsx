import { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { API_URL } from './config.js';
import './App.css';

export default function App() {
  const [conversationId, setConversationId] = useState(null);
  const [conversations, setConversations] = useState([]);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [theme, setTheme] = useState(() => localStorage.getItem('theme') || 'dark');
  const bottomRef = useRef(null);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
  }, [theme]);

  const toggleTheme = () => {
    setTheme((prev) => (prev === 'dark' ? 'light' : 'dark'));
  };

  const loadConversationList = () => {
    axios
      .get(`${API_URL}/conversations`)
      .then((r) => setConversations(r.data.conversations || []))
      .catch(() => {});
  };

  // On first load: restore the last active conversation, or create a new one
  useEffect(() => {
    loadConversationList();
    const stored = localStorage.getItem('conversation_id');
    if (stored) {
      setConversationId(stored);
      loadHistory(stored);
    } else {
      createNewConversation();
    }
  }, []);

  const loadHistory = (id) => {
    axios
      .get(`${API_URL}/conversations/${id}/messages`)
      .then((r) => setMessages(r.data.messages || []))
      .catch(() => setError('Could not load that conversation.'));
  };

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

  const createNewConversation = () => {
    axios
      .post(`${API_URL}/conversations`)
      .then((r) => {
        setConversationId(r.data.conversation_id);
        localStorage.setItem('conversation_id', r.data.conversation_id);
        setMessages([]);
        loadConversationList();
      })
      .catch(() => setError('Could not reach backend. Check API_URL in config.js.'));
  };

  const switchConversation = (id) => {
    setConversationId(id);
    localStorage.setItem('conversation_id', id);
    setMessages([]);
    setError(null);
    loadHistory(id);
    setSidebarOpen(false);
  };

  const send = async () => {
    if (!input.trim() || !conversationId || loading) return;
    const userMsg = { role: 'user', content: input };
    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    setLoading(true);
    setError(null);
    try {
      const res = await axios.post(`${API_URL}/chat`, {
        conversation_id: conversationId,
        message: userMsg.content,
      });
      setMessages((prev) => [...prev, { role: 'assistant', content: res.data.reply }]);
      loadConversationList(); // refresh sidebar so title/order updates
    } catch (e) {
      setError('Something went wrong reaching the backend. Try again.');
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <div className="app-shell">
      <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`}>
        <div className="sidebar-header">
          <span>History</span>
          <button className="icon-btn" onClick={() => setSidebarOpen(false)}>✕</button>
        </div>
        <button className="new-chat-btn full-width" onClick={createNewConversation}>
          + New Chat
        </button>
        <div className="conversation-list">
          {conversations.length === 0 && (
            <div className="empty-sidebar">No conversations yet</div>
          )}
          {conversations.map((c) => (
            <div
              key={c.id}
              className={`conversation-item ${c.id === conversationId ? 'active' : ''}`}
              onClick={() => switchConversation(c.id)}
            >
              {c.title || 'New chat'}
            </div>
          ))}
        </div>

        <div className="sidebar-footer">
          <button className="theme-toggle" onClick={toggleTheme}>
            <span>{theme === 'dark' ? '🌙 Dark mode' : '☀️ Light mode'}</span>
            <span>Switch</span>
          </button>
        </div>
      </aside>

      <div className="app">
        <header className="header">
          <div className="header-brand">
            <button className="icon-btn menu-btn" onClick={() => setSidebarOpen(true)}>☰</button>
            <img src="/zeal_logo_transparent.png" alt="Zeal" className="header-logo" />
            <h1>Zeal Co-Pilot</h1>
          </div>
          <button className="new-chat-btn" onClick={createNewConversation}>New Chat</button>
        </header>

        <div className="chat-window">
          {messages.length === 0 && !loading && (
            <div className="empty-state">
              Ask about hydrogen economics, IPO strategy, electrolyzer tech, project
              structuring — or anything else, general questions welcome too.
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`bubble-row ${m.role}`}>
              <div className={`bubble ${m.role}`}>{m.content}</div>
            </div>
          ))}
          {loading && (
            <div className="bubble-row assistant">
              <div className="bubble assistant typing">Thinking…</div>
            </div>
          )}
          {error && <div className="error-banner">{error}</div>}
          <div ref={bottomRef} />
        </div>

        <div className="input-row">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type your question... (Enter to send, Shift+Enter for new line)"
            rows={2}
          />
          <button onClick={send} disabled={loading || !input.trim()}>
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
