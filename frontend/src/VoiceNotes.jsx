import { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { API_URL } from './config.js';

// Parses the structured note text (TITLE/SUMMARY/KEY POINTS/ACTION ITEMS
// format from the backend) into sections for cleaner display.
function parseStructured(text) {
  if (!text) return null;
  const sections = { title: '', summary: '', keyPoints: [], actionItems: [] };
  const lines = text.split('\n');
  let current = null;
  for (const line of lines) {
    const trimmed = line.trim();
    if (/^TITLE:/i.test(trimmed)) {
      sections.title = trimmed.replace(/^TITLE:/i, '').trim();
      current = null;
    } else if (/^SUMMARY:/i.test(trimmed)) {
      sections.summary = trimmed.replace(/^SUMMARY:/i, '').trim();
      current = null;
    } else if (/^KEY POINTS:/i.test(trimmed)) {
      current = 'keyPoints';
    } else if (/^ACTION ITEMS:/i.test(trimmed)) {
      current = 'actionItems';
    } else if (trimmed.startsWith('-') && current) {
      sections[current].push(trimmed.replace(/^-\s*/, ''));
    }
  }
  return sections;
}

// Common languages for the dropdown — "Other..." reveals a free-text box
// for anything not listed here, so it's never actually limited to these.
const COMMON_LANGUAGES = [
  'English', 'Hindi', 'Spanish', 'French', 'German', 'Mandarin Chinese',
  'Japanese', 'Korean', 'Arabic', 'Portuguese', 'Russian', 'Italian',
  'Tamil', 'Telugu', 'Bengali', 'Marathi', 'Gujarati', 'Punjabi', 'Urdu',
];

export default function VoiceNotes() {
  const [notes, setNotes] = useState([]);
  const [recording, setRecording] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [selectedNote, setSelectedNote] = useState(null);
  const [error, setError] = useState(null);
  const [translateLang, setTranslateLang] = useState('');
  const [showCustomLang, setShowCustomLang] = useState(false);
  const [translated, setTranslated] = useState(null); // { language, text } — summary translation
  const [translating, setTranslating] = useState(false);
  const [translatedTranscript, setTranslatedTranscript] = useState(null); // { language, text }
  const [translatingTranscript, setTranslatingTranscript] = useState(false);
  const [renamingId, setRenamingId] = useState(null);
  const [renameValue, setRenameValue] = useState('');
  const [editing, setEditing] = useState(false);
  const [editStructured, setEditStructured] = useState('');
  const [editTranscript, setEditTranscript] = useState('');
  const [saving, setSaving] = useState(false);

  const mediaRecorderRef = useRef(null);
  const chunksRef = useRef([]);
  const timerRef = useRef(null);

  const loadNotes = () => {
    axios
      .get(`${API_URL}/voice-notes`)
      .then((r) => setNotes(r.data.voice_notes || []))
      .catch(() => setError('Could not load voice notes.'));
  };

  useEffect(() => {
    loadNotes();
    return () => clearInterval(timerRef.current);
  }, []);

  const startRecording = async () => {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (e) => chunksRef.current.push(e.data);
      recorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        uploadRecording();
      };
      recorder.start();
      mediaRecorderRef.current = recorder;
      setRecording(true);
      setSeconds(0);
      timerRef.current = setInterval(() => setSeconds((s) => s + 1), 1000);
    } catch (e) {
      setError('Could not access the microphone. Check your browser permissions.');
    }
  };

  const stopRecording = () => {
    mediaRecorderRef.current?.stop();
    clearInterval(timerRef.current);
    setRecording(false);
  };

  const uploadRecording = async () => {
    const blob = new Blob(chunksRef.current, { type: 'audio/webm' });
    if (blob.size === 0) {
      setError('Recording was empty — try again.');
      return;
    }
    setProcessing(true);
    setError(null);
    const formData = new FormData();
    formData.append('audio', blob, 'note.webm');
    try {
      const res = await axios.post(`${API_URL}/voice-notes`, formData);
      setNotes((prev) => [res.data, ...prev]);
      selectNote(res.data);
    } catch (e) {
      setError('Could not transcribe that recording. Try again.');
    } finally {
      setProcessing(false);
    }
  };

  const deleteNote = (e, id) => {
    e.stopPropagation();
    if (!window.confirm('Delete this voice note? This cannot be undone.')) return;
    axios
      .delete(`${API_URL}/voice-notes/${id}`)
      .then(() => {
        setNotes((prev) => prev.filter((n) => n.id !== id));
        if (selectedNote?.id === id) setSelectedNote(null);
      })
      .catch(() => setError('Could not delete that note.'));
  };

  const startRename = (e, note) => {
    e.stopPropagation();
    setRenamingId(note.id);
    setRenameValue(note.title || '');
  };

  const commitRename = (id) => {
    const title = renameValue.trim();
    setRenamingId(null);
    if (!title) return;
    axios
      .patch(`${API_URL}/voice-notes/${id}`, { title })
      .then(() => {
        setNotes((prev) => prev.map((n) => (n.id === id ? { ...n, title } : n)));
        if (selectedNote?.id === id) setSelectedNote((prev) => ({ ...prev, title }));
      })
      .catch(() => setError('Could not rename that note.'));
  };

  const startEditing = () => {
    if (!selectedNote) return;
    setEditStructured(selectedNote.structured || '');
    setEditTranscript(selectedNote.transcript || '');
    setEditing(true);
  };

  const cancelEditing = () => setEditing(false);

  const saveEdits = () => {
    if (!selectedNote) return;
    setSaving(true);
    setError(null);
    axios
      .patch(`${API_URL}/voice-notes/${selectedNote.id}`, {
        structured: editStructured,
        transcript: editTranscript,
      })
      .then(() => {
        const updated = { ...selectedNote, structured: editStructured, transcript: editTranscript };
        setSelectedNote(updated);
        setNotes((prev) => prev.map((n) => (n.id === updated.id ? updated : n)));
        setEditing(false);
        // Clear any active translations since they'd now be showing stale text
        setTranslated(null);
        setTranslatedTranscript(null);
      })
      .catch(() => setError('Could not save your edits. Try again.'))
      .finally(() => setSaving(false));
  };

  const selectNote = (note) => {
    setSelectedNote(note);
    setTranslated(null);
    setTranslateLang('');
    setShowCustomLang(false);
    setTranslatedTranscript(null);
    setEditing(false);
  };

  const formatTime = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;

  const runTranslate = () => {
    const language = translateLang.trim();
    if (!language || !selectedNote) return;
    setTranslating(true);
    setError(null);
    axios
      .post(`${API_URL}/voice-notes/${selectedNote.id}/translate`, { language, mode: 'structured' })
      .then((r) => setTranslated({ language: r.data.language, text: r.data.translated }))
      .catch(() => setError('Translation failed — try again.'))
      .finally(() => setTranslating(false));
  };

  const runTranslateTranscript = () => {
    const language = translateLang.trim();
    if (!language || !selectedNote) return;
    setTranslatingTranscript(true);
    setError(null);
    axios
      .post(`${API_URL}/voice-notes/${selectedNote.id}/translate`, { language, mode: 'transcript' })
      .then((r) => setTranslatedTranscript({ language: r.data.language, text: r.data.translated }))
      .catch(() => setError('Translation failed — try again.'))
      .finally(() => setTranslatingTranscript(false));
  };

  const sections = selectedNote
    ? parseStructured(translated ? translated.text : selectedNote.structured)
    : null;

  return (
    <div className="voice-notes-view">
      <div className="voice-notes-list-pane">
        <div className="voice-record-area">
          {!recording && !processing && (
            <button className="record-btn" onClick={startRecording}>
              🎙️ Record a voice note
            </button>
          )}
          {recording && (
            <button className="record-btn recording" onClick={stopRecording}>
              ⏹ Stop ({formatTime(seconds)})
            </button>
          )}
          {processing && (
            <div className="record-btn processing" disabled>
              Transcribing…
            </div>
          )}
          <p className="voice-hint">Works in Hindi, English, or a mix of both.</p>
          {error && <div className="error-banner">{error}</div>}
        </div>

        <div className="voice-notes-list">
          {notes.length === 0 && (
            <div className="empty-sidebar">No voice notes yet — record your first one above.</div>
          )}
          {notes.map((n) => (
            <div
              key={n.id}
              className={`conversation-item ${selectedNote?.id === n.id ? 'active' : ''}`}
              onClick={() => selectNote(n)}
            >
              {renamingId === n.id ? (
                <input
                  autoFocus
                  className="rename-input"
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onClick={(e) => e.stopPropagation()}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') commitRename(n.id);
                    if (e.key === 'Escape') setRenamingId(null);
                  }}
                  onBlur={() => commitRename(n.id)}
                />
              ) : (
                <>
                  <span className="conversation-title">{n.title || 'Untitled note'}</span>
                  <span className="conversation-actions">
                    <button className="icon-btn small" title="Rename" onClick={(e) => startRename(e, n)}>
                      ✏️
                    </button>
                    <button className="icon-btn small" title="Delete" onClick={(e) => deleteNote(e, n.id)}>
                      🗑️
                    </button>
                  </span>
                </>
              )}
            </div>
          ))}
        </div>
      </div>

      <div className="voice-notes-detail-pane">
        {!selectedNote && (
          <div className="empty-state">
            Record a voice note, or select one from the list to view its transcript and summary.
          </div>
        )}
        {selectedNote && (
          <div className="voice-note-detail">
            <div className="detail-header-row">
              <h2>{translated ? sections?.title || selectedNote.title : selectedNote.title}</h2>
              {!editing && (
                <button className="translate-btn secondary" onClick={startEditing}>
                  ✏️ Edit
                </button>
              )}
            </div>

            {editing ? (
              <div className="edit-mode">
                <p className="voice-hint">
                  Fix any transcription errors below — useful for numbers or names Whisper
                  misheard. Editing here updates the saved note directly.
                </p>

                <label className="edit-label">Summary note (raw format — keep the TITLE:/SUMMARY:/KEY POINTS:/ACTION ITEMS: labels)</label>
                <textarea
                  className="edit-textarea"
                  value={editStructured}
                  onChange={(e) => setEditStructured(e.target.value)}
                  rows={10}
                />

                <label className="edit-label">Full transcript</label>
                <textarea
                  className="edit-textarea"
                  value={editTranscript}
                  onChange={(e) => setEditTranscript(e.target.value)}
                  rows={8}
                />

                {error && <div className="error-banner">{error}</div>}

                <div className="edit-actions">
                  <button className="translate-btn" onClick={saveEdits} disabled={saving}>
                    {saving ? 'Saving...' : 'Save changes'}
                  </button>
                  <button className="translate-btn secondary" onClick={cancelEditing} disabled={saving}>
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <>
                <div className="translate-row">
                  <select
                    className="translate-select"
                    value={showCustomLang ? '__other__' : translateLang}
                    onChange={(e) => {
                      if (e.target.value === '__other__') {
                        setShowCustomLang(true);
                        setTranslateLang('');
                      } else {
                        setShowCustomLang(false);
                        setTranslateLang(e.target.value);
                      }
                    }}
                  >
                    <option value="" disabled>Translate to...</option>
                    {COMMON_LANGUAGES.map((lang) => (
                      <option key={lang} value={lang}>{lang}</option>
                    ))}
                    <option value="__other__">Other...</option>
                  </select>
                  {showCustomLang && (
                    <input
                      autoFocus
                      className="translate-input"
                      type="text"
                      value={translateLang}
                      onChange={(e) => setTranslateLang(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && runTranslate()}
                      placeholder="Type a language"
                    />
                  )}
                  <button className="translate-btn" onClick={runTranslate} disabled={translating || !translateLang.trim()}>
                    {translating ? '...' : '🌐 Translate summary'}
                  </button>
                  {translated && (
                    <button className="translate-btn secondary" onClick={() => setTranslated(null)}>
                      Show original
                    </button>
                  )}
                </div>
                {translated && (
                  <p className="translate-label">Showing translation to {translated.language}</p>
                )}
                {error && <div className="error-banner">{error}</div>}

                {sections?.summary && <p className="voice-summary">{sections.summary}</p>}

                {sections?.keyPoints?.length > 0 && (
                  <>
                    <h3>Key points</h3>
                    <ul>
                      {sections.keyPoints.map((p, i) => (
                        <li key={i}>{p}</li>
                      ))}
                    </ul>
                  </>
                )}

                {sections?.actionItems?.length > 0 && (
                  <>
                    <h3>Action items</h3>
                    <ul>
                      {sections.actionItems.map((p, i) => (
                        <li key={i}>{p}</li>
                      ))}
                    </ul>
                  </>
                )}

                <details className="transcript-details">
                  <summary>Full transcript</summary>
                  <div className="transcript-translate-row">
                    <button
                      className="translate-btn"
                      onClick={runTranslateTranscript}
                      disabled={translatingTranscript || !translateLang.trim()}
                    >
                      {translatingTranscript ? '...' : `🌐 Translate transcript${translateLang.trim() ? ' to ' + translateLang.trim() : ''}`}
                    </button>
                    {translatedTranscript && (
                      <button className="translate-btn secondary" onClick={() => setTranslatedTranscript(null)}>
                        Show original
                      </button>
                    )}
                  </div>
                  {translatedTranscript && (
                    <p className="translate-label">Showing translation to {translatedTranscript.language}</p>
                  )}
                  <p className="voice-transcript">
                    {translatedTranscript ? translatedTranscript.text : selectedNote.transcript}
                  </p>
                </details>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
