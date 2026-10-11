import { useEffect, useRef, useState } from 'react';
import { Sparkles } from 'lucide-react';
import type { Memory, Project } from '../types';
import './AiSuggestions.css';

type Suggestion = { tags: string[]; caption: string };
type Status = { configured: boolean; defaultModel: string; models: string[]; keySource: 'session' | 'environment' | null };
interface Props { project: Project; memory: Memory; onApply(suggestion: Suggestion): void }
async function call<T>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api${path}`, { method, signal, headers: { 'X-Stillroom-Request': 'local', 'Content-Type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'The request could not be completed.');
  return data;
}

export function AiSuggestions({ project, memory, onApply }: Props) {
  const [status, setStatus] = useState<Status | null>(null);
  const [apiKey, setApiKey] = useState('');
  const [model, setModel] = useState('inclusionai/ling-3.1-flash');
  const [imageIds, setImageIds] = useState<string[]>([]);
  const [includeNote, setIncludeNote] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<Suggestion | null>(null);
  const [tags, setTags] = useState('');
  const [caption, setCaption] = useState('');
  const [applyTags, setApplyTags] = useState(true);
  const [applyCaption, setApplyCaption] = useState(true);
  const generation = useRef(0);
  const pending = useRef<AbortController | null>(null);
  const images = project.assets.filter(a => a.kind === 'image' && a.role === 'media' && memory.mediaIds.includes(a.id));
  const supportsImages = model !== 'inclusionai/ling-3.1-flash';
  const selectedIds = supportsImages ? imageIds.filter(id => images.some(a => a.id === id)) : [];
  useEffect(() => {
    const abort = new AbortController();
    call<Status>('/ai/status', 'GET', undefined, abort.signal).then(setStatus).catch(e => { if (e.name !== 'AbortError') setError(e.message); });
    return () => abort.abort();
  }, []);
  useEffect(() => {
    generation.current++; pending.current?.abort();
    setImageIds([]); setIncludeNote(false); setResult(null); setError(''); setBusy(false); setApiKey('');
    return () => { generation.current++; pending.current?.abort(); };
  }, [project.id, memory.id]);
  async function configure(forget = false) {
    setBusy(true); setError('');
    const current = generation.current;
    const key = apiKey; setApiKey('');
    try {
      const updated = await call<Status>('/ai/configure', forget ? 'DELETE' : 'POST', forget ? undefined : { apiKey: key });
      if (current === generation.current) setStatus(updated);
    } catch (e) { if (current === generation.current) setError((e as Error).message); }
    finally { if (current === generation.current) setBusy(false); }
  }
  async function suggest() {
    setBusy(true); setError(''); setResult(null);
    const current = generation.current;
    const abort = new AbortController(); pending.current = abort;
    try {
      const suggestion = await call<Suggestion>(`/projects/${project.id}/ai/suggest`, 'POST', { memoryId: memory.id, imageIds: selectedIds, includeNote, expectedNote: includeNote ? memory.note : null, model, consent: true }, abort.signal);
      if (current !== generation.current) return;
      setResult(suggestion); setTags(suggestion.tags.join(', ')); setCaption(suggestion.caption); setApplyTags(true); setApplyCaption(true);
    } catch (e) { if (current === generation.current && (e as Error).name !== 'AbortError') setError((e as Error).message); }
    finally { if (current === generation.current) setBusy(false); }
  }
  return <details className="ai-suggestions"><summary><Sparkles size={15} /> Optional AI suggestions</summary>
    <div className="ai-content"><p>Ask OpenRouter for tags and a photo caption. Selected content is sent to OpenRouter and its model provider. Only free models are enabled: $0 input / $0 output. Free quotas and availability limits apply.</p>
      <p className="helper">Nothing is sent until you choose “Send selected content”. Suggestions can be wrong; review them before applying.</p>
      {status && <div className="ai-key-status">{status.configured ? `Key configured for this ${status.keySource === 'session' ? 'server session' : 'server environment'}.` : 'No OpenRouter key configured.'}</div>}
      {(!status?.configured || status.keySource === 'session') && <div className="ai-key-controls"><label>OpenRouter API key<input aria-label="OpenRouter API key" type="password" autoComplete="off" spellCheck={false} value={apiKey} onChange={e => setApiKey(e.target.value)} maxLength={512} disabled={busy} placeholder={status?.configured ? 'Replace session key' : 'Paste key for this session'} /></label><button className="button small" disabled={busy || !apiKey.trim()} onClick={() => void configure()}>Use key for this session</button>{status?.keySource === 'session' && <button className="text-button" disabled={busy} onClick={() => void configure(true)}>Forget session key</button>}<p className="helper">Kept in server memory only; not saved in your project or browser storage. Cleared when the server stops.</p></div>}
      <label>Model<select aria-label="OpenRouter model" value={model} onChange={e => { setModel(e.target.value); setImageIds([]); setResult(null); }} disabled={busy}>{(status?.models || ['inclusionai/ling-3.1-flash', 'google/gemma-4-31b-it:free', 'openrouter/free']).map(value => <option key={value} value={value}>{value === 'inclusionai/ling-3.1-flash' ? 'Ling 3.1 Flash · Free · Text only' : value === 'google/gemma-4-31b-it:free' ? 'Gemma 4 31B · Free' : value === 'openrouter/free' ? 'Free Models Router · Rotating free models' : value}</option>)}</select></label>
      <fieldset disabled={busy}><legend>Choose exactly what to send</legend><p className="helper">{supportsImages ? "Up to 3 photos. Photos are resized to at most 1024 pixels and metadata is removed. Room scans, video and audio are excluded." : "Ling 3.1 Flash accepts text only. Select the saved note for tag suggestions. For photo captions, explicitly choose a free vision model above."}</p>
        {supportsImages && images.map(a => <label className="ai-photo-choice" key={a.id}><input type="checkbox" checked={selectedIds.includes(a.id)} disabled={!selectedIds.includes(a.id) && selectedIds.length >= 3} onChange={e => setImageIds(old => e.target.checked ? [...old, a.id] : old.filter(id => id !== a.id))} /><img src={`/api/projects/${project.id}/assets/${a.id}`} alt="" /><span>{a.originalName}</span></label>)}
        {supportsImages && !images.length && <p className="helper">No photos attached to this memory.</p>}
        <label className="ai-check"><input type="checkbox" checked={includeNote} onChange={e => setIncludeNote(e.target.checked)} disabled={!memory.note.trim()} /> Include this memory’s note</label>
        {includeNote && <pre className="ai-note-preview" aria-label="Exact note selected for sending">{memory.note}</pre>}
      </fieldset>
      <p className="ai-send-summary">Selected: {selectedIds.length} photo{selectedIds.length === 1 ? '' : 's'}{includeNote ? ' and the note shown above' : '; no note'}.</p>
      <button className="button wide" onClick={() => void suggest()} disabled={busy || !status?.configured || (!selectedIds.length && !(includeNote && memory.note.trim()))}>{busy ? 'Working…' : 'Send selected content to OpenRouter'}</button>
      {error && <p className="ai-error" role="alert">{error}</p>}
      {result && <section className="ai-result" aria-label="Review AI suggestions"><h4>Review suggestions</h4><label className="ai-check"><input type="checkbox" checked={applyTags} onChange={e => setApplyTags(e.target.checked)} /> Add these tags</label><input aria-label="Suggested tags" value={tags} onChange={e => setTags(e.target.value)} maxLength={1200} /><label className="ai-check"><input type="checkbox" checked={applyCaption} onChange={e => setApplyCaption(e.target.checked)} /> Append this caption to the note</label><textarea aria-label="Suggested caption" value={caption} onChange={e => setCaption(e.target.value)} maxLength={1000} rows={4} /><p className="helper">Edit the text above. Applying adds tags and appends the caption to your note.</p><button className="button wide" disabled={!applyTags && !applyCaption} onClick={() => { onApply({ tags: applyTags ? [...new Set(tags.split(',').map(t => t.trim().slice(0, 100)).filter(Boolean))].slice(0, 12) : [], caption: applyCaption ? caption.trim() : '' }); setResult(null); }}>Apply selected tags/caption</button><button className="text-button" onClick={() => setResult(null)}>Discard suggestions</button></section>}
    </div>
  </details>;
}
