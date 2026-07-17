import { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { API_URL } from './config.js';
import './App.css';

export default function App() {
  const [conversationId, setConversationId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const bottomRef = useRef(null);

  // Create a new conversation on first load
  useEffect(() => {
    const stored = localStorage.getItem('conversation_id');
    if (stored) {
      setConversationId(stored);
      loadHistory(stored);
    } else {
      axios
        .post(`${API_URL}/conversations`)
        .then((r) => {
          setConversationId(r.data.conversation_id);
          localStorage.setItem('conversation_id', r.data.conversation_id);
        })
        .catch(() => setError('Could not reach backend. Check API_URL in config.js.'));
    }
  }, []);

  const loadHistory = (id) => {
    axios
      .get(`${API_URL}/conversations/${id}/messages`)
      .then((r) => setMessages(r.data.messages || []))
      .catch(() => {});
  };

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

  const startNewChat = () => {
    localStorage.removeItem('conversation_id');
    setMessages([]);
    axios.post(`${API_URL}/conversations`).then((r) => {
      setConversationId(r.data.conversation_id);
      localStorage.setItem('conversation_id', r.data.conversation_id);
    });
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
    <div className="app">
      <header className="header">
        <div className="header-brand">
          <img src="/zeal_logo_transparent.png" alt="Zeal" className="header-logo" />
          <h1>Zeal Co-Pilot</h1>
        </div>
        <button className="new-chat-btn" onClick={startNewChat}>New Chat</button>
      </header>

      <div className="chat-window">
        {messages.length === 0 && !loading && (
          <div className="empty-state">
            Ask about hydrogen economics, IPO strategy, electrolyzer tech, project
            structuring, or anything else in scope.
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
  );
}
