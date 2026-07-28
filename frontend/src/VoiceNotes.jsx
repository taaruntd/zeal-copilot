import { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { API_URL } from './config.js';

// Parses the structured note text (TITLE/SUMMARY/KEY POINTS/ACTION ITEMS
// format from the backend) into sections for cleaner display.
function parseStructured(text) {
  if (!text) return null;
  const sections = { summary: '', keyPoints: [], actionItems: [] };
  const lines = text.split('\n');
  let current = null;
  for (const line of lines) {
    const trimmed = line.trim();
    if (/^SUMMARY:/i.test(trimmed)) {
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

export default function VoiceNotes() {
  const [notes, setNotes] = useState([]);
  const [recording, setRecording] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [selectedNote, setSelectedNote] = useState(null);
  const [error, setError] = useState(null);

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
      setSelectedNote(res.data);
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

  const formatTime = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;

  const sections = selectedNote ? parseStructured(selectedNote.structured) : null;

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
              onClick={() => setSelectedNote(n)}
            >
              <span className="conversation-title">{n.title || 'Untitled note'}</span>
              <span className="conversation-actions">
                <button className="icon-btn small" title="Delete" onClick={(e) => deleteNote(e, n.id)}>
                  🗑️
                </button>
              </span>
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
            <h2>{selectedNote.title}</h2>
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
              <p className="voice-transcript">{selectedNote.transcript}</p>
            </details>
          </div>
        )}
      </div>
    </div>
  );
}
