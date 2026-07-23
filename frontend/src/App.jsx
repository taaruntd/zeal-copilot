import { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import ReactMarkdown from 'react-markdown';
import { API_URL } from './config.js';
import './App.css';

// Some models (especially routed free ones) emit their chain-of-thought
// wrapped in <think>...</think> before the real answer. Split it out so it
// renders as a collapsible "Thinking" section instead of leaking into the
// visible reply.
function splitThinking(raw) {
  if (!raw) return { thinking: null, answer: raw };
  const match = raw.match(/<think>([\s\S]*?)<\/think>/i);
  if (!match) return { thinking: null, answer: raw };
  const thinking = match[1].trim();
  let answer = (raw.slice(0, match.index) + raw.slice(match.index + match[0].length)).trim();
  // Some models wrap the whole remaining reply in stray braces — strip a
  // single matching leading/trailing brace if present.
  if (answer.startsWith('{') && answer.endsWith('}')) {
    answer = answer.slice(1, -1).trim();
  }
  return { thinking, answer };
}

function AssistantMessage({ content }) {
  const { thinking, answer } = splitThinking(content);
  const [open, setOpen] = useState(false);
  return (
    <>
      {thinking && (
        <div className="thinking-block">
          <button className="thinking-toggle" onClick={() => setOpen((o) => !o)}>
            💭 {open ? 'Hide thinking' : 'Show thinking'}
          </button>
          {open && <div className="thinking-content">{thinking}</div>}
        </div>
      )}
      <ReactMarkdown>{answer}</ReactMarkdown>
    </>
  );
}

export default function App() {
  const [conversationId, setConversationId] = useState(null);
  const [conversations, setConversations] = useState([]);
  const [sidebarOpen, setSidebarOpen] = useState(false); // mobile off-canvas
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => localStorage.getItem('sidebarCollapsed') === 'true'
  ); // desktop collapse
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [theme, setTheme] = useState(() => localStorage.getItem('theme') || 'dark');
  const [renamingId, setRenamingId] = useState(null);
  const [renameValue, setRenameValue] = useState('');
  const [providerList, setProviderList] = useState([
    { id: 'groq', label: 'Groq (Llama)', available: true },
  ]);
  const [provider, setProvider] = useState(() => localStorage.getItem('provider') || 'groq');
  const [imagePreview, setImagePreview] = useState(null); // base64 data URL
  const bottomRef = useRef(null);
  const fileInputRef = useRef(null);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
  }, [theme]);

  useEffect(() => {
    localStorage.setItem('provider', provider);
  }, [provider]);

  useEffect(() => {
    localStorage.setItem('sidebarCollapsed', String(sidebarCollapsed));
  }, [sidebarCollapsed]);

  useEffect(() => {
    axios
      .get(`${API_URL}/providers`)
      .then((r) => setProviderList(r.data.providers || []))
      .catch(() => {});
  }, []);

  const toggleTheme = () => {
    setTheme((prev) => (prev === 'dark' ? 'light' : 'dark'));
  };

  const loadConversationList = () => {
    axios
      .get(`${API_URL}/conversations`)
      .then((r) => setConversations(r.data.conversations || []))
      .catch(() => {});
  };

  // Every time the app is opened/reloaded, start with a blank, unsaved chat.
  // Nothing is written to the database until the user actually sends a
  // message — this avoids filling the sidebar with empty "New chat" entries.
  useEffect(() => {
    loadConversationList();
    setConversationId(null);
    setMessages([]);
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

  // Clears the current view back to a blank, unsaved chat. Does NOT create a
  // database row — that only happens when a message is actually sent.
  const startNewChat = () => {
    setConversationId(null);
    setMessages([]);
    setError(null);
    setSidebarOpen(false);
  };

  const switchConversation = (id) => {
    setConversationId(id);
    setMessages([]);
    setError(null);
    loadHistory(id);
    setSidebarOpen(false);
  };

  const startRename = (e, c) => {
    e.stopPropagation();
    setRenamingId(c.id);
    setRenameValue(c.title || '');
  };

  const commitRename = (id) => {
    const title = renameValue.trim();
    setRenamingId(null);
    if (!title) return;
    axios
      .patch(`${API_URL}/conversations/${id}`, { title })
      .then(() => loadConversationList())
      .catch(() => setError('Could not rename that conversation.'));
  };

  const deleteConversation = (e, id) => {
    e.stopPropagation();
    if (!window.confirm('Delete this conversation? This cannot be undone.')) return;
    axios
      .delete(`${API_URL}/conversations/${id}`)
      .then(() => {
        loadConversationList();
        if (id === conversationId) {
          startNewChat();
        }
      })
      .catch(() => setError('Could not delete that conversation.'));
  };

  const MAX_IMAGE_MB = 4;

  const handleImageSelect = (e) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      setError('Please choose an image file.');
      return;
    }
    if (file.size > MAX_IMAGE_MB * 1024 * 1024) {
      setError(`Image is too large — please use one under ${MAX_IMAGE_MB}MB.`);
      return;
    }
    const reader = new FileReader();
    reader.onload = () => setImagePreview(reader.result);
    reader.readAsDataURL(file);
  };

  const removeImage = () => setImagePreview(null);

  const send = async () => {
    if ((!input.trim() && !imagePreview) || loading) return;
    const userMsg = { role: 'user', content: input, image: imagePreview };
    setMessages((prev) => [...prev, userMsg]);
    const sentImage = imagePreview;
    const sentText = input;
    setInput('');
    setImagePreview(null);
    setLoading(true);
    setError(null);
    try {
      let activeId = conversationId;
      if (!activeId) {
        const created = await axios.post(`${API_URL}/conversations`);
        activeId = created.data.conversation_id;
        setConversationId(activeId);
      }

      const res = await axios.post(`${API_URL}/chat`, {
        conversation_id: activeId,
        message: sentText,
        provider,
        image: sentImage,
      });
      setMessages((prev) => [...prev, { role: 'assistant', content: res.data.reply }]);
      loadConversationList();
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
      <aside className={`sidebar ${sidebarOpen ? 'open' : ''} ${sidebarCollapsed ? 'collapsed' : ''}`}>
        <div className="sidebar-header">
          <span>History</span>
          <button className="icon-btn mobile-only" onClick={() => setSidebarOpen(false)}>✕</button>
        </div>
        <button className="new-chat-btn full-width" onClick={startNewChat}>
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
              {renamingId === c.id ? (
                <input
                  autoFocus
                  className="rename-input"
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onClick={(e) => e.stopPropagation()}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') commitRename(c.id);
                    if (e.key === 'Escape') setRenamingId(null);
                  }}
                  onBlur={() => commitRename(c.id)}
                />
              ) : (
                <>
                  <span className="conversation-title">{c.title || 'New chat'}</span>
                  <span className="conversation-actions">
                    <button
                      className="icon-btn small"
                      title="Rename"
                      onClick={(e) => startRename(e, c)}
                    >
                      ✏️
                    </button>
                    <button
                      className="icon-btn small"
                      title="Delete"
                      onClick={(e) => deleteConversation(e, c.id)}
                    >
                      🗑️
                    </button>
                  </span>
                </>
              )}
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
            <button
              className="icon-btn sidebar-toggle-btn mobile-only"
              title="Open history"
              onClick={() => setSidebarOpen(true)}
            >
              ☰
            </button>
            <button
              className="icon-btn sidebar-toggle-btn desktop-only"
              title={sidebarCollapsed ? 'Show history' : 'Hide history'}
              onClick={() => setSidebarCollapsed((c) => !c)}
            >
              {sidebarCollapsed ? '☰' : '⟨'}
            </button>
            <img src="/zeal_logo_transparent.png" alt="Zeal" className="header-logo" />
            <h1>Zeal Co-Pilot</h1>
          </div>
          <button className="new-chat-btn" onClick={startNewChat}>New Chat</button>
        </header>

        <div className="chat-window">
          {messages.length === 0 && !loading && (
            <div className="empty-state">
              Ask me anything — everyday questions, business strategy for any
              industry, planning, writing, or general knowledge. Nothing here is
              limited to one topic.
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`bubble-row ${m.role}`}>
              <div className={`bubble ${m.role}`}>
                {m.image && <img src={m.image} alt="attached" className="bubble-image" />}
                {m.role === 'assistant' ? (
                  <AssistantMessage content={m.content} />
                ) : (
                  m.content
                )}
              </div>
            </div>
          ))}
          {loading && (
            <div className="bubble-row assistant">
              <div className="bubble assistant typing">
                <span className="typing-dot" />
                <span className="typing-dot" />
                <span className="typing-dot" />
              </div>
            </div>
          )}
          {error && <div className="error-banner">{error}</div>}
          <div ref={bottomRef} />
        </div>

        {imagePreview && (
          <div className="image-preview-row">
            <img src={imagePreview} alt="preview" className="image-preview-thumb" />
            <button className="icon-btn small" onClick={removeImage} title="Remove image">✕</button>
          </div>
        )}

        <div className="input-toolbar">
          <select
            id="provider-select"
            className="provider-select toolbar-provider-select"
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            title="Choose AI model"
          >
            {providerList.map((p) => (
              <option key={p.id} value={p.id} disabled={!p.available}>
                {p.label}{!p.available ? ' (not configured)' : ''}
              </option>
            ))}
          </select>
        </div>

        <div className="input-row">
          <input
            type="file"
            accept="image/*"
            ref={fileInputRef}
            onChange={handleImageSelect}
            style={{ display: 'none' }}
          />
          <button
            className="attach-btn"
            title="Attach an image"
            onClick={() => fileInputRef.current?.click()}
          >
            📎
          </button>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type your question... (Enter to send, Shift+Enter for new line)"
            rows={2}
          />
          <button onClick={send} disabled={loading || (!input.trim() && !imagePreview)}>
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
