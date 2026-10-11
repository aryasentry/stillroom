import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, Bookmark, Camera, Check, ChevronDown, Download, Eye, EyeOff, Footprints, HelpCircle, Home, LoaderCircle, MapPin, MoreHorizontal, MousePointer2, PanelLeft, Pencil, Plus, Redo2, RotateCcw, Settings2, Undo2, Upload, X } from 'lucide-react';
import { api } from './api';
import {replaceScan} from './coordinates';
import { useProjectStore } from './store';
import type { Asset, Memory, NavigationMode, PickResult, Project, ProjectSummary, ViewerHandle, ViewerStatus } from './types';
import { ReconstructionFlow } from './ui/ReconstructionFlow';
import { Library } from './ui/Library';
import { MemoryRail } from './ui/MemoryRail';
import { MemoryInspector } from './ui/MemoryInspector';
import SetupPanel from './ui/SetupPanel';
import { RoomViewer } from './viewer/RoomViewer';
import './styles.css';

export default function App() {
  const store = useProjectStore();
  const project = store.project;
  const [reconstruction,setReconstruction]=useState<{project:Project|null}|null>(null);
  const [inRoom, setInRoom] = useState(false);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [loadingLibrary, setLoadingLibrary] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [rail, setRail] = useState(true);
  const [setup, setSetup] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [placing, setPlacing] = useState(false);
  const [movingId, setMovingId] = useState<string | null>(null);
  const [mode, setMode] = useState<NavigationMode>('orbit');
  const [viewerStatus, setViewerStatus] = useState<ViewerStatus>({ loading: false, progress: null, error: null, canWalk: false, message: '' });
  const [query, setQuery] = useState('');
  const [searchIds, setSearchIds] = useState<string[] | null>(null);
  const [tag, setTag] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [filters, setFilters] = useState(false);
  const [deleted, setDeleted] = useState(false);
  const [missing, setMissing] = useState<string[]>([]);
  const [more, setMore] = useState(false);
  const [views, setViews] = useState(false);
  const [help, setHelp] = useState(false);
  const [viewName, setViewName] = useState('');
  const viewerRef = useRef<ViewerHandle | null>(null);
  const scanInput = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const setupPick = useRef<((pick: PickResult) => void) | null>(null);
  const activeId = useRef<string | null>(null);
  activeId.current = project?.id || null;

  const refreshLibrary = useCallback(async () => { setLoadingLibrary(true); try { setProjects(await api.listProjects()); } catch (e) { setError(message(e)); } finally { setLoadingLibrary(false); } }, []);
  useEffect(() => { void refreshLibrary(); }, [refreshLibrary]);
  useEffect(() => { if (!toast) return; const timer = setTimeout(() => setToast(null), 4500); return () => clearTimeout(timer); }, [toast]);
  useEffect(() => { if (!project || !inRoom) return; let cancelled = false; void api.integrity(project.id).then(r => { if (!cancelled) setMissing([...r.missing, ...r.corrupt]); }).catch(e => { if (!cancelled) setError(message(e)); }); return () => { cancelled = true; }; }, [project?.id, inRoom]);
  useEffect(() => {
    if (!project || !query.trim()) { setSearchIds(null); return; }
    let cancelled = false;
    const timer = setTimeout(async () => { try { await store.flush(); const result = await api.search(project.id, query.trim()); if (!cancelled) setSearchIds(result.map(m => m.id)); } catch (e) { if (!cancelled) setError(message(e)); } }, 220);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [project?.id, project?.revision, query]);
  useEffect(() => { const handler = (e: KeyboardEvent) => {
    if (e.key === 'Escape') { setPlacing(false); setMovingId(null); setupPick.current = null; setMore(false); setViews(false); setHelp(false); }
    if (!editing || !inRoom || reconstruction || ['INPUT', 'TEXTAREA', 'SELECT'].includes((e.target as HTMLElement)?.tagName)) return;
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? store.redo() : store.undo(); }
  }; window.addEventListener('keydown', handler); return () => window.removeEventListener('keydown', handler); }, [editing, inRoom, reconstruction, store.undo, store.redo]);

  const perform = async (label: string, action: () => Promise<void>) => { setBusy(label); setError(null); try { await action(); } catch (e) { if ((e as Error).name === 'AbortError') setToast('Import cancelled. Your original file is unchanged.'); else setError(message(e)); } finally { setBusy(null); setProgress(null); abortRef.current = null; } };
  const enter = (p: Project) => { store.open(p); setInRoom(true); setSelectedId(null); setSetup(false); setEditing(false); setMode('orbit'); setPlacing(false); setMovingId(null); setQuery(''); setSearchIds(null); setTag(''); setFrom(''); setTo(''); setDeleted(false); setMissing([]); setMore(false); setViews(false); setRail(window.innerWidth > 760); };
  const open = (id: string) => void perform('Opening this place…', async () => { await store.flush(); const next=await api.getProject(id); if(!next.room.scanAssetId&&(next.captureAssetIds?.length??0)>0){setReconstruction({project:next});setInRoom(false)}else enter(next); });
  const photos = (id?:string) => void perform('Opening your photo collection…',async()=>{await store.flush();setReconstruction({project:id?await api.getProject(id):null});setInRoom(false)});
  const associateScan = (asset: Asset) => store.update(p => replaceScan(p, asset));
  const uploadScan = (file: File) => void perform('Copying your room scan…', async () => { abortRef.current = new AbortController(); const a = await store.upload(file, 'scan', setProgress, abortRef.current.signal); associateScan(a); await store.flush(); setSetup(true); setSelectedId(null); setEditing(true); });
  const create = (title: string, file: File | null) => void perform(file ? 'Importing your place…' : 'Creating your place…', async () => { await store.flush(); const p = await api.createProject(title); enter(p); setEditing(true); if (file) { abortRef.current = new AbortController(); const a = await store.upload(file, 'scan', setProgress, abortRef.current.signal); associateScan(a); await store.flush(); setSetup(true); } });
  const back = () => void perform('Saving your place…', async () => { await store.flush(); setInRoom(false); setPlacing(false); setupPick.current = null; await refreshLibrary(); });
  const updateMemory = (patch: Partial<Memory>) => { if (!selectedId) return; store.update(p => { const m = p.memories.find(item => item.id === selectedId); if (m) Object.assign(m, patch, { updatedAt: new Date().toISOString() }); }); };
  const addMemory = () => { if (!project?.room.scanAssetId) return; setEditing(true); setPlacing(true); setMovingId(null); setSetup(false); setSelectedId(null); setupPick.current = null; setToast('Click a surface in the room to leave a memory.'); };
  const onPick = (pick: PickResult) => {
    if (setupPick.current) { const handler = setupPick.current; setupPick.current = null; setPlacing(false); handler(pick); return; }
    if (!placing) return;
    if (movingId) { store.update(p => { const m = p.memories.find(item => item.id === movingId); if (m) { m.anchor = pick.anchor; m.updatedAt = new Date().toISOString(); } }); setSelectedId(movingId); setMovingId(null); setToast('Memory moved.'); }
    else { const id = crypto.randomUUID(); const now = new Date().toISOString(); store.update(p => { p.memories.push({ id, title: 'Untitled memory', note: '', date: null, tags: [], anchor: pick.anchor, mediaIds: [], viewpoint: pick.viewpoint, createdAt: now, updatedAt: now, deletedAt: null }); }); setSelectedId(id); setToast('A memory has a place. Make it yours.'); }
    setPlacing(false);
  };
  const attach = (files: File[]) => { const memoryId = selectedId; if (!memoryId) return; void perform('Keeping your attachments…', async () => { for (const file of files) { const a = await store.upload(file, 'media', setProgress); store.update(p => { const m = p.memories.find(item => item.id === memoryId); if (m) { m.mediaIds.push(a.id); m.updatedAt = new Date().toISOString(); } }); } await store.flush(); }); };
  const saveView = () => { if (!viewerRef.current) return; const view = viewerRef.current.captureView(); view.name = viewName.trim() || `View ${(project?.viewpoints.length || 0) + 1}`; store.update(p => { p.viewpoints.push(view); }); setViewName(''); setToast('View saved.'); };
  const cover = () => void perform('Saving a cover for this place…', async () => { const blob = await viewerRef.current?.captureThumbnail(); if (!blob) throw new Error('A cover is not available yet. Let the room finish loading.'); const a = await store.upload(new File([blob], blob.type === 'image/jpeg' ? 'room-cover.jpg' : 'room-cover.png', { type: blob.type || 'image/png' }), 'thumbnail'); store.update(p => { p.thumbnailAssetId = a.id; }); await store.flush(); setToast('Library cover updated.'); });
  const recoverConflict = () => void perform('Keeping your draft and reopening the saved room…', async () => {
    const draft = store.project;
    if (!draft) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(draft, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = `stillroom-unsaved-draft-${draft.id}.json`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    enter(await api.getProject(draft.id));
    setToast('Unsaved metadata downloaded. Reopened the latest committed room.');
  });
  const selected = project?.memories.find(m => m.id === selectedId);
  const visibleMemories = project?.memories.filter(m => (!!m.deletedAt === deleted) && (!searchIds || searchIds.includes(m.id)) && (!tag || m.tags.includes(tag)) && (!from || (!!m.date && m.date >= from)) && (!to || (!!m.date && m.date <= to))) || [];

  return <>{reconstruction?<ReconstructionFlow initialProject={reconstruction.project} onClose={()=>{setReconstruction(null);void refreshLibrary()}} onAccepted={accepted=>{setReconstruction(null);enter(accepted);setSetup(true);setEditing(true);setToast('Room accepted. Review scale and orientation in Room setup.')}}/>:!inRoom || !project ? <Library projects={projects} loading={loadingLibrary} busy={busy} onPhotos={photos} onOpen={open} onCreate={create} onImport={file => void perform('Opening your archive…', async () => { await store.flush(); abortRef.current = new AbortController(); enter(await api.importPackage(file, abortRef.current.signal)); })} onDemo={kind => void perform('Preparing the synthetic sample room…', async () => { await store.flush(); enter(await api.demo(kind)); })} onDelete={id => void perform('Archiving place…', async () => { await api.deleteProject(id); await refreshLibrary(); setToast('Place archived. You can restore it from the archive.'); })} onRestore={id => void perform('Restoring place…', async () => { await api.restoreProject(id); await refreshLibrary(); })} assetUrl={api.assetUrl} /> : <div className={`room-shell ${rail ? 'rail-is-open' : ''} ${selected || setup ? 'inspector-is-open' : ''} ${placing ? 'is-placing' : ''}`}>
    <header className="room-topbar"><div className="room-title-group"><button className="icon-button" onClick={back} aria-label="Back to places"><ArrowLeft size={18} /></button><span className="topbar-divider" /><div><h1>{project.title}</h1>{project.room.synthetic && <span className="synthetic-label">SYNTHETIC DEMO</span>}</div></div><div className="topbar-actions"><span className={`save-state ${store.saveStatus}`} aria-live="polite">{store.saveStatus === 'saving' ? <LoaderCircle size={12} className="spin" /> : store.saveStatus === 'saved' ? <Check size={12} /> : null}{store.saveStatus === 'saved' ? 'Saved locally' : store.saveStatus === 'saving' ? 'Saving…' : store.saveStatus === 'error' ? 'Save failed' : 'Unsaved changes'}</span><div className="mode-switch" aria-label="View or edit"><button className={!editing ? 'active' : ''} onClick={() => { setEditing(false); setPlacing(false); setSetup(false); setupPick.current = null; }}><Eye size={14} /> View</button><button className={editing ? 'active' : ''} onClick={() => setEditing(true)}><Pencil size={13} /> Edit</button></div>{editing && <div className="undo-controls"><button className="icon-button" aria-label="Undo" disabled={!store.canUndo} onClick={store.undo}><Undo2 size={16} /></button><button className="icon-button" aria-label="Redo" disabled={!store.canRedo} onClick={store.redo}><Redo2 size={16} /></button></div>}<div className="popover-wrap"><button className={`icon-button ${more ? 'active' : ''}`} aria-label="Place options" aria-expanded={more} onClick={() => setMore(!more)}><MoreHorizontal size={21} /></button>{more && <div className="popover place-menu"><label>Place name<input value={project.title} onChange={e => store.update(p => { p.title = e.target.value; })} /></label><button onClick={() => { setMore(false); setSetup(true); setSelectedId(null); setEditing(true); }}><Settings2 size={15} /> Room setup</button><button onClick={() => { setMore(false); photos(project.id); }}><Camera size={15} /> Create a room from photos</button><button onClick={() => { setMore(false); scanInput.current?.click(); }}><Upload size={15} />{project.room.scanAssetId ? 'Replace room scan' : 'Import room scan'}</button><button onClick={() => { setMore(false); cover(); }} disabled={!project.room.scanAssetId}><Camera size={15} /> Save library cover</button><button onClick={() => void perform('Preparing your archive…', async () => { await store.flush(); const a = document.createElement('a'); a.href = api.exportUrl(project.id); a.download = ''; a.click(); setMore(false); })}><Download size={15} /> Export portable .zip</button><button onClick={() => void perform('Recovering previous save…', async () => { await store.flush(); enter(await api.recoverProject(project.id)); setToast('Previous committed version restored.'); })}><RotateCcw size={15} /> Recover previous save</button></div>}</div></div></header>
    <div className="room-body">{rail && <MemoryRail project={project} memories={visibleMemories} selectedId={selectedId} query={query} onQuery={setQuery} tag={tag} onTag={setTag} from={from} onFrom={setFrom} to={to} onTo={setTo} showFilters={filters} onToggleFilters={() => setFilters(!filters)} showDeleted={deleted} onToggleDeleted={() => setDeleted(!deleted)} onSelect={id => { setSelectedId(id); setSetup(false); }} onAdd={addMemory} onClose={() => setRail(false)} editing={editing} assetUrl={api.assetUrl} />}
      <main className="room-stage" aria-label="Immersive room"><RoomViewer ref={viewerRef} project={project} mode={mode} editing={editing} placing={placing} showMarkers={project.settings.showMarkers} selectedMemoryId={selectedId} speed={project.settings.navigationSpeed} onPick={onPick} onMoveAnchor={(id, position) => store.update(p => { const m = p.memories.find(memory => memory.id === id); if (m) { m.anchor.position = position; m.anchor.placement = 'approximate'; m.updatedAt = new Date().toISOString(); } })} onSelectMemory={id => { setSelectedId(id); setSetup(false); }} onStatus={setViewerStatus} />
        <div className="stage-top"><button className={`stage-button ${rail ? 'active' : ''}`} onClick={() => setRail(!rail)} aria-label="Toggle memory panel"><PanelLeft size={17} /><span>Memories</span><span className="count-badge">{project.memories.filter(m => !m.deletedAt).length}</span></button><div className="stage-top-right"><button className="stage-button icon-only" aria-label={project.settings.showMarkers ? 'Hide memory markers' : 'Show memory markers'} onClick={() => store.update(p => { p.settings.showMarkers = !p.settings.showMarkers; })}>{project.settings.showMarkers ? <MapPin size={16} /> : <EyeOff size={16} />}</button>{editing && <button className="stage-button" aria-label="Room setup" onClick={() => { setSetup(!setup); setSelectedId(null); }}><Settings2 size={16} /><span>Room setup</span></button>}</div></div>
        {!project.room.scanAssetId && <div className="stage-empty"><span className="empty-memory-symbol">⌑</span><h2>A place for your memories.</h2><p>Bring your room into Stillroom with overlapping photos.</p><button className="button primary" onClick={() => photos(project.id)}><Camera size={16} /> Create a room from photos</button><button className="text-button capture-empty-secondary" onClick={() => scanInput.current?.click()}><Upload size={14} /> Advanced: import a room scan</button><small>JPEG · PNG · WebP</small></div>}
        {placing && <div className="placement-banner"><MapPin size={16} /><span>{setupPick.current ? 'Select a point in the room' : movingId ? 'Choose a new place for this memory' : 'Click a surface to leave your memory'}</span><button className="icon-button" aria-label="Cancel placement" onClick={() => { setPlacing(false); setMovingId(null); setupPick.current = null; }}><X size={16} /></button></div>}
        {project.room.scanAssetId && !placing && <div className="stage-caption">{project.room.sceneType === 'splat' && <small className="scene-limitation">Gaussian splat · approximate anchors · markers visible through surfaces</small>}<span>{project.room.synthetic ? 'THE SAMPLE ROOM' : 'YOU ARE HERE'}</span><h2>{project.title}</h2></div>}
        <button className="stage-button help-button icon-only" aria-label="Navigation help" onClick={() => setHelp(!help)}><HelpCircle size={17} /></button>
        {help && <div className="help-card"><div className="section-heading"><h3>A little orientation</h3><button className="icon-button" aria-label="Close help" onClick={() => setHelp(false)}><X size={16} /></button></div><p><strong>Orbit</strong> Drag to look around. Scroll to zoom. Right-drag to pan.</p><p><strong>Fly / walk</strong> Drag to look. Use W A S D to move. Q / E moves vertically in fly mode.</p><p><strong>Memories</strong> Click a pin to open its story. In Edit, choose “Leave a memory” and click a surface.</p><p>Walk is available only after a floor and entrance are validated in room setup. Escape cancels placement.</p></div>}
      </main>
      {setup ? <SetupPanel project={project} onChange={store.update} viewerRef={viewerRef} onRequestPick={handler => { setupPick.current = handler; setPlacing(true); setMovingId(null); }} onClose={() => { setSetup(false); setPlacing(false); setupPick.current = null; }} /> : selected ? <MemoryInspector key={selected.id} project={project} memory={selected} editing={editing} busy={!!busy} missing={missing} onClose={() => setSelectedId(null)} onUpdate={updateMemory} onDelete={() => { updateMemory({ deletedAt: new Date().toISOString() }); setSelectedId(null); setToast('Memory removed. Find it under Recently removed.'); }} onRestore={() => updateMemory({ deletedAt: null })} onMove={() => { setMovingId(selected.id); setPlacing(true); setupPick.current = null; }} onView={() => { if (selected.viewpoint) viewerRef.current?.goToView(selected.viewpoint); }} onSaveView={() => { const view = viewerRef.current?.captureView(); if (view) updateMemory({ viewpoint: view }); }} onAttach={attach} onRelink={(a, file) => void perform('Checking original attachment…', async () => { await store.flush(); await api.relink(project.id, a.id, file); await store.reload(); setMissing(old => old.filter(id => id !== a.id)); setToast('Original attachment relinked.'); })} assetUrl={api.assetUrl} /> : null}
    </div>
    <footer className="room-bottom"><div className="navigation-modes" aria-label="Navigation mode"><button className={mode === 'orbit' ? 'active' : ''} onClick={() => setMode('orbit')}><MousePointer2 size={14} /> Orbit</button><button className={mode === 'fly' ? 'active' : ''} onClick={() => setMode('fly')}><Eye size={14} /> Fly</button><button className={mode === 'walk' ? 'active' : ''} disabled={!viewerStatus.canWalk} title={viewerStatus.canWalk ? 'Walk through this room' : 'Set a validated floor and entrance in room setup first'} onClick={() => setMode('walk')}><Footprints size={14} /> Walk</button></div><span className="navigation-hint">{placing ? 'Click to place · Esc to cancel' : mode === 'orbit' ? 'Drag to look · Scroll to explore' : 'W A S D to move · Drag to look'}</span><div className="bottom-actions"><label className="speed-control">Speed<input type="range" min="0.1" max="5" step="0.1" aria-label="Navigation speed" value={project.settings.navigationSpeed} onChange={e => store.update(p => { p.settings.navigationSpeed = Number(e.target.value); })} /></label><button className="text-button" onClick={() => viewerRef.current?.returnToEntrance()}><Home size={14} /><span>Entrance</span></button><div className="popover-wrap"><button className="text-button" onClick={() => setViews(!views)} aria-expanded={views}><Bookmark size={14} /><span>Saved views</span><ChevronDown size={12} /></button>{views && <div className="popover views-menu"><span className="eyebrow">A FAMILIAR PERSPECTIVE</span>{project.viewpoints.length === 0 && <p className="helper">Keep a view to return to later.</p>}{project.viewpoints.map(v => <div className="saved-view-row" key={v.id}><button onClick={() => { viewerRef.current?.goToView(v); setViews(false); }}><Camera size={14} />{v.name}</button><button className="icon-button" aria-label={`Remove saved view ${v.name}`} onClick={() => store.update(p => { p.viewpoints = p.viewpoints.filter(item => item.id !== v.id); })}><X size={13} /></button></div>)}<form onSubmit={e => { e.preventDefault(); saveView(); }}><input value={viewName} onChange={e => setViewName(e.target.value)} placeholder="Name this view…" aria-label="Saved view name" /><button className="button small" type="submit" disabled={!project.room.scanAssetId}><Plus size={14} /> Save current view</button></form></div>}</div></div></footer>
    <input ref={scanInput} hidden type="file" accept=".glb,.ply,.spz" aria-label="Import or replace room scan" onChange={e => { if (e.target.files?.[0]) uploadScan(e.target.files[0]); e.target.value = ''; }} />
  </div>}
  {(error || (inRoom && store.error)) && <div className="error-banner" role="alert"><span>{error || store.error}</span>{store.error && <button className="text-button" onClick={() => void perform('Retrying local save…', async () => { await store.flush(); })}>Retry save</button>}{store.error && <button className="text-button" onClick={recoverConflict}>Save draft &amp; reload saved</button>}<button className="icon-button" onClick={() => setError(null)} aria-label="Dismiss error"><X size={17} /></button></div>}
  {toast && <div className="toast" role="status"><Check size={15} /><span>{toast}</span><button className="icon-button" aria-label="Dismiss notification" onClick={() => setToast(null)}><X size={15} /></button></div>}
  {busy && <div className="busy-backdrop"><div className="busy-card" role="status"><span className="spinner" /><h3>{busy}</h3>{progress !== null && <><progress value={progress} max={100} /><small>{Math.round(progress)}% copied</small></>}{abortRef.current && <button className="button small" onClick={() => abortRef.current?.abort()}>Cancel import</button>}</div></div>}
  </>;
}

function message(error: unknown) { return error instanceof Error ? error.message : String(error); }
