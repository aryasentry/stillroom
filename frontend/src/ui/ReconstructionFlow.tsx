import {useEffect,useMemo,useRef,useState} from 'react';
import {ArrowLeft,ArrowRight,Camera,Check,ImagePlus,LoaderCircle,RefreshCw,Video,X} from 'lucide-react';
import {api,ApiError} from '../api';
import type {Asset,Project,ReconstructionJob,ReconstructionRuntime,ViewerHandle,ViewerStatus,VideoExtractionJob} from '../types';
import {RoomViewer} from '../viewer/RoomViewer';
import {selectAssistedPhotos,captureAssets,checkPhotoSelection,checkVideoSelection,videoFrameLabel,isActiveJob,jobLabel,makeReconstructionPreview,reconstructionCoverage,reconstructionCaptureViews,reconstructionSourceVideos} from './reconstructionModel';
import './ReconstructionFlow.css';

interface Props{initialProject:Project|null;onClose():void;onAccepted(project:Project):void}
interface PendingPhoto{id:string;file:File}
const explain=(error:unknown)=>error instanceof Error?error.message:String(error);
const size=(bytes:number)=>`${(bytes/1024**2).toFixed(1)} MiB`;
function LocalPhoto({file}:{file:File}){
  const [url,setUrl]=useState('');
  useEffect(()=>{const next=URL.createObjectURL(file);setUrl(next);return()=>URL.revokeObjectURL(next)},[file]);
  return url?<img src={url} alt={file.name} loading="lazy"/>:null;
}
function SourceVideoSection({project,sources}:{project:Project;sources:Asset[]}){
  if(!sources.length)return null;
  return <section className="source-video-review" aria-label="Original capture videos"><h2>People and motion, as recorded.</h2><p>A static 3D reconstruction can smear moving people and lose face detail. Play the retained original to see the faces and motion actually recorded by your camera.</p>{sources.map(source=><figure key={source.id}><video controls playsInline preload="metadata" aria-label={`Original video: ${source.originalName}`} src={api.assetUrl(project.id,source.id)}/><figcaption>{source.originalName} · original capture, stored on this device</figcaption></figure>)}</section>;
}
function CaptureCoverage({project,job}:{project:Project;job:ReconstructionJob}){
  const coverage=reconstructionCoverage(job);
  if(!coverage||isActiveJob(job))return null;
  const views=reconstructionCaptureViews(project,job);
  return <section className="capture-coverage" aria-label="Matched capture views">
    <strong>{coverage.matched} of {coverage.total} capture views matched · {coverage.percent}%</strong>
    <p>{coverage.total-coverage.matched} {coverage.total-coverage.matched===1?'view was':'views were'} not included in this reconstruction. This percentage describes matched capture views, not how much of the physical room is complete.</p>
    {views.length>0&&<><div className="capture-coverage-strip" aria-label="Capture sequence for this attempt">{views.map(({asset,status},i)=><figure key={asset.id} className={status} title={`${asset.originalName} · ${status}`}><img src={api.assetUrl(project.id,asset.thumbnailId||asset.id)} alt={asset.originalName} loading="lazy"/><figcaption>{videoFrameLabel(asset)??`View ${i+1}`}<span>{status==='matched'?'Matched':status==='unmatched'?'Not matched':'Match unknown'}</span></figcaption></figure>)}</div><p className="helper">Capture order for this attempt · amber frames did not match. Look for gaps and add overlapping views between them.</p></>}
  </section>;
}
export function ReconstructionFlow({initialProject,onClose,onAccepted}:Props){
  const [project,setProject]=useState(initialProject);
  const [title,setTitle]=useState(initialProject?.title??'');
  const [pending,setPending]=useState<PendingPhoto[]>([]);
  const [runtime,setRuntime]=useState<ReconstructionRuntime|null>(null);
  const [assistedRuntime,setAssistedRuntime]=useState<ReconstructionRuntime|null>(null);
  const [jobs,setJobs]=useState<ReconstructionJob[]>([]);
  const [job,setJob]=useState<ReconstructionJob|null>(null);
  const [videoJob,setVideoJob]=useState<VideoExtractionJob|null>(null);
  const [videoFile,setVideoFile]=useState<File|null>(null);
  const [videoSourceId,setVideoSourceId]=useState('');
  const [targetFrames,setTargetFrames]=useState(120);
  const [videoUploadProgress,setVideoUploadProgress]=useState<number|null>(null);
  const [loading,setLoading]=useState(true);
  const [refreshVersion,setRefreshVersion]=useState(0);
  const [error,setError]=useState<string|null>(null);
  const [busy,setBusy]=useState<string|null>(null);
  const [uploadProgress,setUploadProgress]=useState<{done:number;total:number;percent:number}|null>(null);
  const [quality,setQuality]=useState<'preview'|'standard'|'assisted'>('standard');
  const [guide,setGuide]=useState(true);
  const [preview,setPreview]=useState<Project|null>(null);
  const [previewStatus,setPreviewStatus]=useState<ViewerStatus>({loading:true,progress:null,error:null,canWalk:false,message:''});
  const [mode,setMode]=useState<'orbit'|'fly'>('orbit');
  const controller=useRef<AbortController|null>(null);
  const previewViewer=useRef<ViewerHandle|null>(null);
  const mounted=useRef(true);
  const photos=useMemo(()=>captureAssets(project),[project]);
  const sourceBytes=photos.reduce((n,p)=>n+p.size,0);
  const pendingBytes=pending.reduce((n,p)=>n+p.file.size,0);
  const active=isActiveJob(job);
  const extracting=isActiveJob(videoJob);
  const locked=loading||!!busy||active||extracting;
  const coverage=reconstructionCoverage(job);
  const reviewVideos=project&&job?reconstructionSourceVideos(project,job):[];
  const videoSources=project?.assets.filter(a=>a.kind==='video'&&a.role==='media')??[];
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;controller.current?.abort()}},[]);
  useEffect(()=>{
    let disposed=false;
    const load=async()=>{
      try{
        const [status,attempts,fresh,extractions]=await Promise.all([api.reconstructionRuntime(),initialProject?api.listReconstructions(initialProject.id):Promise.resolve([]),initialProject?api.getProject(initialProject.id):Promise.resolve(null),initialProject?api.listVideoExtractions(initialProject.id):Promise.resolve([])]);
        if(disposed)return;setRuntime(status);setJobs(attempts);setJob(attempts.find(isActiveJob)??attempts[0]??null);const savedVideo=extractions.find(isActiveJob)??extractions[0]??null;setVideoJob(savedVideo);setVideoSourceId(savedVideo?.sourceAssetId??fresh?.assets.find(a=>a.kind==='video'&&a.role==='media')?.id??'');if(fresh)setProject(fresh);
      }catch(e){if(!disposed)setError(explain(e))}finally{if(!disposed)setLoading(false)}
    };void load();return()=>{disposed=true};
  },[initialProject?.id]);
  useEffect(()=>{
    if(!runtime?.installing)return;
    let disposed=false;let timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{try{const status=await api.reconstructionRuntime();if(disposed)return;setRuntime(status);if(status.installing)timer=setTimeout(poll,2000)}catch(e){if(!disposed)setError(explain(e))}};
    timer=setTimeout(poll,2000);return()=>{disposed=true;clearTimeout(timer)};
  },[runtime?.installing,refreshVersion]);
  useEffect(()=>{
    if(!project||!job||!isActiveJob(job))return;
    let disposed=false;let timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{try{
      const next=await api.getReconstruction(project.id,job.id);if(disposed)return;
      setJob(next);setJobs(old=>old.map(item=>item.id===next.id?next:item));
      if(next.status==='ready'){const fresh=await api.getProject(project.id);if(!disposed)setProject(fresh)}
      if(next.status==='needs_photos')setGuide(true);
      if(isActiveJob(next))timer=setTimeout(poll,1500);
    }catch(e){if(!disposed)setError(explain(e))}};
    timer=setTimeout(poll,700);return()=>{disposed=true;clearTimeout(timer)};
  },[project?.id,job?.id,active,refreshVersion]);
  useEffect(()=>{
    if(!project||!videoJob||!isActiveJob(videoJob))return;
    let disposed=false;let timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{try{
      const next=await api.getVideoExtraction(project.id,videoJob.id);
      if(disposed)return;
      if(!isActiveJob(next)){const fresh=await api.getProject(project.id);if(disposed)return;setProject(fresh)}
      setVideoJob(next);
      if(isActiveJob(next))timer=setTimeout(poll,1500);
    }catch(e){if(!disposed){setError(explain(e));timer=setTimeout(poll,3000)}}};
    timer=setTimeout(poll,700);return()=>{disposed=true;clearTimeout(timer)};
  },[project?.id,videoJob?.id,extracting,refreshVersion]);
  const run=async(label:string,action:()=>Promise<void>)=>{setBusy(label);setError(null);try{await action()}catch(e){if(mounted.current)setError((e as Error).name==='AbortError'?label==='Copying video into your local library…'?'Video upload stopped. No frame extraction was started. Your original is unchanged.':'Copying stopped. Photos already copied are kept; you can retry the remaining photos.':explain(e))}finally{if(mounted.current){setBusy(null);setUploadProgress(null);setVideoUploadProgress(null)}controller.current=null}};
  const addPhotos=(files:File[])=>{
    const issue=checkPhotoSelection(files,photos.length+pending.length,sourceBytes+pendingBytes);if(issue){setError(issue);return}
    setError(null);setPending(old=>[...old,...files.map(file=>({id:crypto.randomUUID(),file}))]);
  };
  const copyPhotos=()=>run('Copying photos into your local library…',async()=>{
    let current=project;
    if(!current){current=await api.createProject(title.trim()||'Untitled place');setProject(current)}
    const projectId=current.id;controller.current=new AbortController();
    let completed=0;
    try{
      for(const item of pending){
        setUploadProgress({done:completed,total:pending.length,percent:0});
        await api.uploadAsset(projectId,item.file,'capture',percent=>setUploadProgress({done:completed,total:pending.length,percent}),controller.current.signal);
        completed++;setPending(old=>old.filter(p=>p.id!==item.id));
      }
    }finally{const fresh=await api.getProject(projectId);if(mounted.current)setProject(fresh)}
  });
  const extractVideo=()=>run(videoFile?'Copying video into your local library…':'Starting frame extraction…',async()=>{
    let current=project;
    if(!current){current=await api.createProject(title.trim()||'Untitled place');setProject(current)}
    let sourceId=videoSourceId;
    if(videoFile){
      controller.current=new AbortController();setVideoUploadProgress(0);
      const source=await api.uploadAsset(current.id,videoFile,'media',setVideoUploadProgress,controller.current.signal);
      sourceId=source.id;setVideoSourceId(sourceId);setVideoFile(null);setVideoUploadProgress(null);
      const fresh=await api.getProject(current.id);setProject(fresh);
    }
    if(!sourceId)throw new Error('Choose a video first.');
    setBusy('Starting frame extraction…');
    const next=await api.startVideoExtraction(current.id,sourceId,targetFrames);setVideoJob(next);
  });
  useEffect(()=>{
    if(quality!=='assisted')return;
    let disposed=false;let timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{try{const status=await api.assistedRuntime();if(disposed)return;setAssistedRuntime(status);if(status.installing)timer=setTimeout(poll,2000)}catch(e){if(!disposed)setError(explain(e))}};
    void poll();return()=>{disposed=true;clearTimeout(timer)};
  },[quality,assistedRuntime?.installing,refreshVersion]);
  const start=()=>run('Starting your room…',async()=>{
    if(!project)return;
    const fresh=await api.getProject(project.id);setProject(fresh);
    const all=captureAssets(fresh);const selected=quality==='assisted'?selectAssistedPhotos(all):all;
    const next=await api.startReconstruction(project.id,selected.map(a=>a.id),quality);
    setJob(next);setJobs(old=>[next,...old]);setPreview(null);
  });
  const refresh=()=>run('Refreshing saved progress…',async()=>{
    const [status,attempts,fresh,extractions]=await Promise.all([api.reconstructionRuntime(),project?api.listReconstructions(project.id):Promise.resolve([]),project?api.getProject(project.id):Promise.resolve(null),project?api.listVideoExtractions(project.id):Promise.resolve([])]);
    setVideoJob(extractions.find(isActiveJob)??extractions[0]??null);setRuntime(status);setJobs(attempts);setJob(attempts.find(isActiveJob)??attempts.find(j=>j.id===job?.id)??attempts[0]??null);if(fresh)setProject(fresh);setRefreshVersion(v=>v+1);
  });
  const openPreview=()=>run('Opening the reconstructed room…',async()=>{
    if(!project||!job)return;const fresh=await api.getProject(project.id);setProject(fresh);
    setPreviewStatus({loading:true,progress:null,error:null,canWalk:false,message:''});setPreview(makeReconstructionPreview(fresh,job));
  });
  const accept=()=>run('Keeping this room…',async()=>{
    if(!project||!job||!preview)return;
    try{onAccepted(await api.acceptReconstruction(project.id,job.id,project.revision))}
    catch(e){if(e instanceof ApiError&&e.status===409){setProject(await api.getProject(project.id));setPreview(null);throw new Error('This place changed while you were reviewing. The saved room was kept. Open the preview again to review against the latest version.')}throw e}
  });
  return <div className="reconstruction-shell">
    <header className="reconstruction-header"><button className="text-button" onClick={onClose} disabled={!!busy}><ArrowLeft size={16}/> Back to places</button><span className="brand">stillroom<span className="brand-dot"/></span><span className="local-label"><span/> Photos and video stay on this device</span></header>
    <main className="reconstruction-content">
      <div className="reconstruction-intro"><span className="eyebrow">FROM A CAPTURE TO A PLACE</span><h1>{preview?(coverage?.partial?'A partial view of your room.':'Meet your room.'):'Bring a place back.'}</h1><p>{preview?'Take a look around before making this your saved room.':'Start with photos or a video, or follow the guide to capture a room.'}</p></div>
      {error&&<div className="reconstruction-error" role="alert"><p>{error}</p><button className="text-button" onClick={()=>void refresh()} disabled={!!busy}><RefreshCw size={13}/> Refresh saved state</button><button className="icon-button" aria-label="Dismiss reconstruction error" onClick={()=>setError(null)}><X size={15}/></button></div>}
      {loading?<div className="empty-state"><span className="spinner"/><p>Opening your photos and saved attempts…</p></div>:preview?<section className="reconstruction-preview-section">
        <div className="reconstruction-preview"><RoomViewer ref={previewViewer} key={preview.room.scanAssetId} project={preview} mode={mode} editing={false} placing={false} showMarkers={false} selectedMemoryId={null} onPick={()=>{}} onSelectMemory={()=>{}} onStatus={setPreviewStatus}/>{coverage?.partial&&<div className="partial-preview-warning" role="note"><strong>Partial reconstruction</strong><span>{coverage.matched} of {coverage.total} capture views matched. Missing views may leave gaps or distorted areas.</span></div>}</div>
        <div className="preview-toolbar"><div className="navigation-modes"><button className={mode==='orbit'?'active':''} onClick={()=>setMode('orbit')}>Orbit</button><button className={mode==='fly'?'active':''} onClick={()=>setMode('fly')}>Fly</button><button onClick={()=>previewViewer.current?.returnToEntrance()}>Reset view</button></div><span>Drag to look · scroll to explore · W A S D in fly mode</span></div>
        {project&&job&&<CaptureCoverage project={project} job={job}/>}
        {project&&<SourceVideoSection project={project} sources={reviewVideos}/>}
        {previewStatus.loading&&<p role="status">Loading the saved result{previewStatus.progress!==null?` · ${Math.round(previewStatus.progress*100)}%`:''}…</p>}
        {previewStatus.error&&<p className="reconstruction-error" role="alert">{previewStatus.error}</p>}
        <div className="preview-decision"><div><h2>A visual memory, with room to refine.</h2><p>This is an approximate visual reconstruction. Scale is unknown and memory placement is approximate. Set scale, floor and entrance in Room setup after accepting. Existing memory anchors will need review.</p></div><div className="reconstruction-buttons"><button className="button" onClick={()=>setPreview(null)} disabled={!!busy}>Back to photos</button><button className="button primary" onClick={()=>void accept()} disabled={!!busy||previewStatus.loading||!!previewStatus.error}><Check size={16}/> Use this room</button></div></div>
      </section>:<div className="reconstruction-columns">
        <section className="capture-collection"><div className="capture-heading"><div><span className="eyebrow">01 / YOUR PHOTOGRAPHS</span><h2>{project?.title||'A room worth keeping.'}</h2></div><Camera size={27} strokeWidth={1}/></div>
          {!project&&<label className="capture-name">Place name<input value={title} onChange={e=>setTitle(e.target.value)} placeholder="Grandma’s living room" maxLength={160} disabled={!!busy}/></label>}
          <label className={`capture-drop ${locked?'disabled':''}`}><ImagePlus size={29} strokeWidth={1.2}/><strong>{photos.length||pending.length?'Add more photographs':'Choose photos of your room'}</strong><span>Select several photos from the same room, taken from different positions.</span><input type="file" multiple accept=".jpg,.jpeg,.png,.webp" aria-label="Choose room photos" disabled={locked} onChange={e=>{addPhotos(Array.from(e.target.files??[]));e.target.value=''}}/></label>
          <p className="capture-limits">JPEG, PNG or WebP · up to 200 photos / 256 MiB combined · 40 MiB and 32 megapixels per image. Convert HEIC to JPEG before adding it. </p>
          <section className="video-capture" aria-label="Room video capture">
            <div className="capture-heading"><h2>Or begin with a video.</h2><Video size={24} strokeWidth={1.2}/></div>
            <p>We’ll select frames locally, then you can review them with your photographs before creating a room.</p>
            <label className={`capture-drop ${locked?'disabled':''}`}><strong>{videoFile?.name??'Choose a video of your room'}</strong><input type="file" accept=".mp4,.mov,.webm" aria-label="Choose room video" disabled={locked} onChange={e=>{const file=e.target.files?.[0];if(file){const issue=checkVideoSelection(file);setError(issue);if(!issue)setVideoFile(file)}e.target.value=''}}/></label>
            <p className="capture-limits">MP4, MOV or WebM · up to 256 MiB, 5 minutes and 4K. A steady 30–90 second walk is a good starting point.</p>
            {videoFile&&<button className="text-button" disabled={locked} onClick={()=>setVideoFile(null)}>Clear selected video</button>}
            {!videoFile&&videoSources.length>0&&<label>Saved source video<select aria-label="Saved source video" value={videoSourceId} disabled={locked} onChange={e=>setVideoSourceId(e.target.value)}><option value="">Choose a retained video</option>{videoSources.map(a=><option key={a.id} value={a.id}>{a.originalName}</option>)}</select></label>}
            <label>Target video frames<select aria-label="Target video frames" value={targetFrames} disabled={locked} onChange={e=>setTargetFrames(Number(e.target.value))}>{[40,80,120,160].map(n=><option key={n} value={n}>{n}{n===120?' · recommended':''}</option>)}</select></label>
            <p className="helper">This is a target, not a guarantee. Short or repetitive videos may yield fewer frames. Existing photos remain in your collection.</p>
            <button className="button wide" disabled={locked||(!videoFile&&!videoSourceId)} onClick={()=>void extractVideo()}>{!videoFile&&videoJob&&['failed','cancelled','interrupted'].includes(videoJob.status)?'Retry frame extraction':'Extract frames from video'} <ArrowRight size={15}/></button>
            {videoUploadProgress!==null&&<div className="copy-progress" role="status"><p>Copying source video · {Math.round(videoUploadProgress)}%</p><progress value={videoUploadProgress} max={100}/><button className="text-button" onClick={()=>controller.current?.abort()}>Cancel video upload</button></div>}
            {videoJob&&<div className="video-job" aria-live="polite"><h3>{extracting?'Extracting video frames':videoJob.status==='ready'?'Video frames ready':videoJob.status==='failed'?'Video extraction needs attention':videoJob.status==='interrupted'?'Video extraction interrupted':'Video extraction cancelled'}</h3><p>{videoJob.message}</p>{videoJob.error&&<p className="job-error">{videoJob.error}</p>}{videoJob.warnings.map((warning,i)=><p key={i} className="job-warning">{warning}</p>)}{videoJob.status==='ready'&&<p>{videoJob.frameCount} frames selected. Review and remove unwanted frames below, then create a room from the collection.</p>}{extracting?<><p>You can return to your library and reopen this extraction. Keep the local server running.</p><button className="button small" disabled={!!busy} onClick={()=>void run('Stopping frame extraction…',async()=>{if(project){const next=await api.cancelVideoExtraction(project.id,videoJob.id);setProject(await api.getProject(project.id));setVideoJob(next)}})}>Cancel frame extraction</button></>:<p>Your source video is retained in this place. Your original file is unchanged.</p>}</div>}
          </section>
          {project&&<SourceVideoSection project={project} sources={reviewVideos}/>}
          {project&&job&&<CaptureCoverage project={project} job={job}/>}
          <div className="capture-count"><strong>{photos.length+pending.length} photos</strong><span>{size(sourceBytes+pendingBytes)} / 256 MiB{pending.length?` · ${pending.length} waiting to copy`:''}</span></div>
          {!!(photos.length+pending.length)&&<div className="capture-grid">{photos.map(a=><figure key={a.id} className={job?.unregisteredAssetIds?.includes(a.id)?'unmatched':''}><img src={api.assetUrl(project!.id,a.thumbnailId||a.id)} alt={a.originalName} loading="lazy"/><figcaption title={a.originalName}>{a.originalName}</figcaption>{videoFrameLabel(a)&&<span className="video-timestamp">{videoFrameLabel(a)}</span>}{job?.unregisteredAssetIds?.includes(a.id)&&<span className="unmatched-label">Not matched</span>}<button className="photo-remove" aria-label={`Remove ${a.originalName}`} disabled={locked} onClick={()=>void run('Updating your photo collection…',async()=>setProject(await api.removeCapture(project!.id,a.id)))}><X size={13}/></button></figure>)}{pending.map(item=><figure key={item.id}><LocalPhoto file={item.file}/><figcaption title={item.file.name}>{item.file.name}</figcaption><span className="pending-label">To copy</span><button className="photo-remove" aria-label={`Remove ${item.file.name}`} disabled={!!busy} onClick={()=>setPending(old=>old.filter(p=>p.id!==item.id))}><X size={13}/></button></figure>)}</div>}
          {pending.length>0&&<button className="button primary wide" onClick={()=>void copyPhotos()} disabled={locked}>Copy {pending.length} photos to this place <ArrowRight size={16}/></button>}
          {uploadProgress&&<div className="copy-progress" role="status"><p>{uploadProgress.done} of {uploadProgress.total} photos copied · current photo {Math.round(uploadProgress.percent)}%</p><progress value={uploadProgress.done+uploadProgress.percent/100} max={uploadProgress.total}/><button className="text-button" onClick={()=>controller.current?.abort()}>Stop copying</button></div>}
          <p className="capture-storage">Your originals stay untouched. Copied photos are retained in the local project, including photos removed from this collection. Portable ZIP archives have a 512 MiB limit.</p>
        </section>
        <aside className="capture-sidebar">
          <section className="capture-guide"><button className="capture-guide-toggle" aria-expanded={guide} onClick={()=>setGuide(!guide)}><span><span className="eyebrow">A LITTLE PREPARATION</span><h2>Capture guide</h2></span><span>{guide?'−':'+'}</span></button>{guide&&<><div className="capture-guide-illustration" aria-hidden="true"><span/><span/><span/><i>Overlap every view</i></div><ol><li><strong>Move around, slowly.</strong> Walk along the room’s edges. Take small steps between photos; turning from one spot is not enough.</li><li><strong>Keep the room connected.</strong> Aim for roughly 70–80% overlap. Keep the same features visible across neighboring photos, including corners.</li><li><strong>Keep light and objects still.</strong> Use bright, even light. Avoid blur, zoom changes, moving people, mirrors and blank walls.</li><li><strong>Close the loop, then fill gaps.</strong> Walk the room’s perimeter and finish by overlapping your starting views. Capture corners and important surfaces from more than one position. Add views between disconnected sections; a full loop still cannot guarantee a complete room.</li></ol><p>No camera access is requested. Use your usual camera, then add the files here. For video, walk slowly sideways along the room’s edges; do not just pan from one spot. Keep the camera steady and avoid zooming.</p></>}</section>
          <section className="reconstruction-start"><span className="eyebrow">02 / CREATE YOUR ROOM</span><h2>Let the details find each other.</h2><p>Three photos is the minimum, but a whole room usually needs many more overlapping views. Sparse collections may need another capture pass.</p>
          {!runtime?.available&&<div className="runtime-setup"><h3>{runtime?.installing?'Preparing local tools…':'One-time local preparation'}</h3><p>{runtime?.message||'Prepare the tools that turn photos into a room on this computer.'}</p>{runtime?.error&&<p role="alert">{runtime.error}</p>}<p>Preparation downloads tools. Your photographs are processed on this device.</p><button className="button small" onClick={()=>void run('Preparing local tools…',async()=>setRuntime(await api.installReconstructionRuntime()))} disabled={!!busy||runtime?.installing||!runtime?.supported}>{runtime?.installing?<LoaderCircle size={13} className="spin"/>:null}{runtime?.supported?'Prepare local tools':'Not available on this computer'}</button></div>}
          <label>Reconstruction quality<select aria-label="Reconstruction quality" value={quality} onChange={e=>setQuality(e.target.value as 'preview'|'standard'|'assisted')} disabled={locked}><option value="standard">Standard · more refinement</option><option value="preview">Preview · a faster first look</option><option value="assisted">Sparse · estimated cameras (experimental)</option></select></label><p className="helper">Standard takes longer to refine the captured detail. Reconstruction cannot recover unseen surfaces or reliably reconstruct moving people.</p>{quality==='assisted'&&<div className="runtime-setup"><h3>Fewer frames, estimated cameras</h3><p>This attempt uses {Math.min(32,photos.length)} of {photos.length} photos, spread through the capture. All originals stay saved. A local model estimates cameras; image features refine and check them. Missing surfaces and moving people can still smear.</p>{!assistedRuntime?.available&&<><p>{assistedRuntime?.message||'Checking optional model…'}</p>{assistedRuntime?.error&&<p role="alert">{assistedRuntime.error}</p>}<button className="button small" disabled={locked||assistedRuntime?.installing||!assistedRuntime?.supported} onClick={()=>void run('Preparing sparse-frame model…',async()=>setAssistedRuntime(await api.installAssistedRuntime()))}>{assistedRuntime?.installing?'Preparing model…':'Prepare sparse-frame model'}</button></>}</div>}<button className="button primary wide" onClick={()=>void start()} disabled={locked||pending.length>0||photos.length<3||!runtime?.available||(quality==='assisted'&&!assistedRuntime?.available)}>{jobs.length?'Try a new reconstruction':'Create a room from photos'} <ArrowRight size={15}/></button>{pending.length>0?<p className="helper">Copy your selected photos first.</p>:photos.length<3?<p className="helper">Add at least {3-photos.length} more photos to begin.</p>:null}
          </section>
          {job&&<section className="reconstruction-job" aria-live="polite"><span className="eyebrow">YOUR SAVED ATTEMPT</span><h2>{active&&<LoaderCircle size={18} className="spin"/>}{jobLabel(job)}</h2><p className="helper">{job.quality==='assisted'?'Sparse · estimated cameras':job.quality==='standard'?'Standard':'Preview'} · {job.totalPhotos} selected photos</p><p>{job.message}</p>{coverage&&!active&&<p>{coverage.partial?'Partial capture: ':''}{coverage.matched} of {coverage.total} views matched. {coverage.partial?'Review missing views before accepting.':'Matched views do not prove whole-room completeness.'}</p>}{job.iteration!==null&&job.totalIterations!==null&&job.totalIterations>0&&<div><progress value={job.iteration} max={job.totalIterations}/><small>{job.iteration.toLocaleString()} / {job.totalIterations.toLocaleString()} refinement steps</small></div>}{job.error&&<p className="job-error">{job.error}</p>}{job.warnings.map((warning,i)=><p key={i} className="job-warning">{warning}</p>)}{job.status==='needs_photos'&&<><p>Review the photos marked “Not matched”. Take overlapping views that bridge gaps, add them above, then try again.</p><button className="text-button" onClick={()=>setGuide(true)}>Open capture guide</button></>}{job.status==='ready'&&<button className="button primary wide" onClick={()=>void openPreview()} disabled={locked}>Preview reconstructed room <ArrowRight size={15}/></button>}{job.status==='accepted'&&project&&<button className="button wide" onClick={()=>onAccepted(project)}>Open saved room</button>}{active&&<><p className="helper">You can return to your library and reopen this attempt. Keep the local server running.</p><button className="button small" onClick={()=>void run('Stopping this attempt…',async()=>{const next=await api.cancelReconstruction(project!.id,job.id);setJob(next);setJobs(old=>old.map(j=>j.id===next.id?next:j))})} disabled={!!busy}>Cancel reconstruction</button></>}{jobs.length>1&&<label>Previous attempts<select value={job.id} disabled={locked} onChange={e=>{setJob(jobs.find(j=>j.id===e.target.value)??null);setPreview(null)}}>{jobs.map(j=><option key={j.id} value={j.id}>{new Date(j.createdAt).toLocaleString()} · {jobLabel(j)}</option>)}</select></label>}</section>}
        </aside>
      </div>}
      {busy&&!uploadProgress&&videoUploadProgress===null&&<div className="reconstruction-working" role="status"><LoaderCircle size={15} className="spin"/>{busy}</div>}
    </main>
  </div>;
}
