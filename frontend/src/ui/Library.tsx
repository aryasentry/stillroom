import { useEffect, useRef, useState } from 'react';
import { ArrowUpRight, ArchiveRestore, Box, FileUp, FolderOpen, Plus, Trash2, X } from 'lucide-react';
import {api} from '../api';
import {isActiveJob,jobLabel} from './reconstructionModel';
import type {ReconstructionJob, VideoExtractionJob, ProjectSummary } from '../types';

interface Props {
  projects: ProjectSummary[]; loading: boolean; busy: string | null;
  onPhotos(id?:string): void;
  onOpen(id: string): void; onCreate(title: string, file: File | null): void;
  onImport(file: File): void; onDemo(kind: 'mesh' | 'ply' | 'spz'): void;
  onDelete(id: string): void; onRestore(id: string): void;
  assetUrl(projectId: string, assetId: string): string;
}

export function Library({ projects, loading, busy, onOpen, onPhotos, onCreate, onImport, onDemo, onDelete, onRestore, assetUrl }: Props) {
  const [videoJobs,setVideoJobs]=useState<VideoExtractionJob[]>([]);
  const [jobs,setJobs]=useState<ReconstructionJob[]>([]);
  useEffect(()=>{let stopped=false;let timer:ReturnType<typeof setTimeout>;const load=async()=>{try{const next=await api.listReconstructions();if(stopped)return;setJobs(next);if(next.some(isActiveJob))timer=setTimeout(load,2500)}catch{/* Project library remains available when job status cannot be fetched. */}};void load();return()=>{stopped=true;clearTimeout(timer)}},[projects]);
  useEffect(()=>{let stopped=false;let timer:ReturnType<typeof setTimeout>;const load=async()=>{try{const all=await Promise.all(projects.filter(p=>!p.deletedAt).map(p=>api.listVideoExtractions(p.id)));if(stopped)return;const next=all.flatMap(list=>{const latest=list.find(isActiveJob)??list[0];return latest?[latest]:[]});setVideoJobs(next);if(next.some(isActiveJob))timer=setTimeout(load,2500)}catch{/* Saved places remain accessible if progress is temporarily unavailable. */}};void load();return()=>{stopped=true;clearTimeout(timer)}},[projects]);
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState('');
  const [scan, setScan] = useState<File | null>(null);
  const [archive, setArchive] = useState(false);
  const packageInput = useRef<HTMLInputElement>(null);
  const visible = projects.filter(p => archive ? p.deletedAt : !p.deletedAt);
  return <div className="library-shell">
    <header className="library-header"><a className="brand" href="#" onClick={e => e.preventDefault()}><span className="brand-symbol">s.</span> stillroom<span className="brand-dot" /></a><span className="local-label"><span /> Local by design</span></header>
    <main className="library-content">
      <div className="library-intro"><div><span className="eyebrow">YOUR PERSONAL ATLAS</span><h1>Some places stay<br />with you.</h1><p>A quiet place for your rooms, and the memories<br className="desktop-break" /> that belong inside them.</p></div><div className="library-actions"><button className="button primary" onClick={() => onPhotos()} disabled={!!busy}><Plus size={17} /> Create a room from photos</button><span className="helper">Or start with a room video</span><details className="library-advanced"><summary>Advanced: import a 3D scan</summary><button className="text-button" onClick={() => setCreating(true)} disabled={!!busy}>Import GLB, PLY or SPZ</button></details><button className="button subtle" onClick={() => packageInput.current?.click()} disabled={!!busy}><FolderOpen size={16} /> Open a .zip archive</button><input hidden ref={packageInput} type="file" accept=".zip" aria-label="Import portable project" onChange={e => { if (e.target.files?.[0]) onImport(e.target.files[0]); e.target.value = ''; }} /></div></div>
      <div className="collection-heading"><div><h2>{archive ? 'Archived places' : 'Your places'}</h2><span>{visible.length.toString().padStart(2, '0')}</span></div><button className="text-button" onClick={() => setArchive(!archive)}>{archive ? 'Back to your places' : 'View archive'}</button></div>
      {loading ? <div className="empty-state"><span className="spinner" /><p>Opening your library…</p></div> : <div className="project-grid">
        {visible.map(p => <article className="project-card" key={p.id}>
          <button className="project-preview" onClick={() => onOpen(p.id)} disabled={!!p.deletedAt || !!busy} aria-label={`Open ${p.title}`}>
            {p.thumbnailAssetId ? <img src={assetUrl(p.id, p.thumbnailAssetId)} alt={`Saved view of ${p.title}`} /> : <div className="project-placeholder"><Box size={42} strokeWidth={.8} /><span>{p.sceneType === 'splat' ? 'GAUSSIAN SPLAT' : p.sceneType === 'mesh' ? '3D ROOM' : 'READY FOR YOUR PHOTOS'}</span></div>}
            {p.synthetic && <span className="preview-label">Synthetic demo</span>}<span className="project-open"><ArrowUpRight size={19} /></span>
          </button>
          <div className="project-meta"><div><button className="project-title" onClick={() => onOpen(p.id)} disabled={!!p.deletedAt}>{p.title}</button><p>{p.memoryCount} {p.memoryCount === 1 ? 'memory' : 'memories'}<span>·</span>{new Date(p.updatedAt).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</p></div>{p.deletedAt ? <button className="icon-button" aria-label={`Restore ${p.title}`} onClick={() => onRestore(p.id)}><ArchiveRestore size={17} /></button> : <button className="icon-button card-archive" aria-label={`Archive ${p.title}`} onClick={() => onDelete(p.id)}><Trash2 size={16} /></button>}</div>
        {!p.deletedAt&&videoJobs.find(j=>j.projectId===p.id)&&<button className="library-job" onClick={()=>onPhotos(p.id)} disabled={!!busy}>{isActiveJob(videoJobs.find(j=>j.projectId===p.id))?'Extracting video frames':videoJobs.find(j=>j.projectId===p.id)?.status==='ready'?'Review video frames':'Review video extraction'}<ArrowUpRight size={13}/></button>}
        {!p.deletedAt&&jobs.find(j=>j.projectId===p.id)&&jobs.find(j=>j.projectId===p.id)?.status!=='accepted'&&<button className="library-job" onClick={()=>onPhotos(p.id)} disabled={!!busy}>{jobLabel(jobs.find(j=>j.projectId===p.id)!)}<ArrowUpRight size={13}/></button>}
        </article>)}
        {!archive && <button className="new-place-card" onClick={() => onPhotos()} disabled={!!busy}><span><Plus size={24} strokeWidth={1.2} /></span><h3>Make room for a memory</h3><p>Begin with photos or a video of a place you know.</p><small>PHOTOS OR VIDEO → YOUR ROOM</small></button>}
        {archive && visible.length === 0 && <div className="empty-state"><ArchiveRestore size={30} /><p>Your archive is empty.</p><small>Archived places can be restored here.</small></div>}
      </div>}
      {!archive && <section className="demo-banner"><div className="demo-icon"><Box size={28} strokeWidth={1} /></div><div><span className="eyebrow">A PLACE TO BEGIN</span><h3>Step into the sample room.</h3><p>A synthetic room to try navigating and leaving a memory.</p></div><div className="demo-options"><button className="button" onClick={() => onDemo('mesh')} disabled={!!busy}>Explore mesh <ArrowUpRight size={16} /></button><div><button className="text-button" onClick={() => onDemo('ply')} disabled={!!busy}>Try PLY</button><span> / </span><button className="text-button" onClick={() => onDemo('spz')} disabled={!!busy}>Try SPZ</button></div></div></section>}
      <footer className="library-footer"><span>Made for remembering.</span><span>Photos and video are processed on this device. Your originals stay yours.</span></footer>
    </main>
    {creating && <div className="modal-backdrop" onClick={() => setCreating(false)}><section className="dialog" role="dialog" aria-modal="true" aria-labelledby="new-place-title" onClick={e => e.stopPropagation()}><button className="icon-button dialog-close" aria-label="Close new place" onClick={() => setCreating(false)}><X size={20} /></button><span className="eyebrow">A NEW CHAPTER</span><h2 id="new-place-title">Give this place a name.</h2><form onSubmit={e => { e.preventDefault(); onCreate(title.trim() || 'Untitled place', scan); setCreating(false); setTitle(''); setScan(null); }}><label>Place name<input autoFocus value={title} onChange={e => setTitle(e.target.value)} placeholder="Grandma’s living room" maxLength={160} /></label><label className="file-drop"><FileUp size={27} strokeWidth={1.3} /><strong>{scan?.name || 'Choose a room scan'}</strong><span>Self-contained GLB, Gaussian PLY, or SPZ</span><input type="file" accept=".glb,.ply,.spz" onChange={e => setScan(e.target.files?.[0] || null)} /></label><p className="helper">Scans are copied into your local library. You can also add one later.</p><button className="button primary wide" type="submit">Create place <ArrowUpRight size={17} /></button></form></section></div>}
  </div>;
}
