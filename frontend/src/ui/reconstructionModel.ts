import {replaceScan} from '../coordinates';
import type {Asset,Project,Quat,ReconstructionJob,Vec3} from '../types';
export const MAX_PHOTOS=200, MAX_PHOTO_BYTES=256*1024**2;
export function captureAssets(project:Project|null){
  const ids=new Set(project?.captureAssetIds??[]);
  return project?.assets.filter(asset=>ids.has(asset.id)&&asset.role==='capture'&&asset.kind==='image')??[];
}
export function checkPhotoSelection(files:Pick<File,'name'|'size'>[],count=0,bytes=0):string|null{
  for(const file of files){
    if(/\.(heic|heif)$/i.test(file.name))return 'Convert HEIC or HEIF photos to JPEG first. On Mac, use Preview → Export → JPEG; on iPhone, choose Most Compatible for future captures.';
    if(!/\.(jpe?g|png|webp)$/i.test(file.name))return 'Choose JPEG, PNG or WebP photos. Use the video chooser for MP4, MOV or WebM.';
    if(file.size<=0||file.size>40*1024**2)return 'Each photo must be nonempty and at most 40 MiB.';
  }
  if(count+files.length>MAX_PHOTOS)return 'Keep this capture collection to 200 photos or fewer.';
  if(bytes+files.reduce((n,f)=>n+f.size,0)>MAX_PHOTO_BYTES)return 'Keep the combined source photos within 256 MiB.';
  return null;
}
export function isActiveJob(job:{status:string}|null|undefined){return job?.status==='queued'||job?.status==='running';}
export function jobLabel(job:ReconstructionJob){
  if(job.status==='ready')return reconstructionCoverage(job)?.partial?'Partial reconstruction ready':'Ready to preview';
  if(job.status==='needs_photos')return 'More overlap needed';
  if(job.status==='failed')return 'Attempt needs attention';
  if(job.status==='cancelled')return 'Cancelled · photos kept';
  if(job.status==='interrupted')return 'Interrupted · photos kept';
  if(job.status==='accepted')return reconstructionCoverage(job)?.partial?'Partial room accepted':'Room accepted';
  return {preparing:'Preparing your photos',matching:'Finding shared details',training:'Building your room',validating:'Checking the result',complete:'Finishing'}[job.stage];
}
export function makeReconstructionPreview(project:Project,job:ReconstructionJob):Project{
  const result=project.assets.find(a=>a.id===job.resultAssetId&&a.role==='scan'&&a.kind==='ply');
  if(job.status!=='ready'||job.projectId!==project.id||!result)throw new Error('The ready room is not available yet. Refresh to load its saved result.');
  const preview=structuredClone(project);replaceScan(preview,result);preview.memories=[];preview.viewpoints=[];
  const provenance=result.metadata?.reconstruction as {initialView?:{position?:unknown;quaternion?:unknown};displayRotation?:unknown}|undefined;
  const vector=(value:unknown,length:number,bound:number):value is number[]=>Array.isArray(value)&&value.length===length&&value.every(n=>typeof n==='number'&&Number.isFinite(n)&&Math.abs(n)<=bound);
  const pose=provenance?.initialView;
  if(pose&&vector(pose.position,3,1e9)&&vector(pose.quaternion,4,1e9)){
    const norm=pose.quaternion.reduce((sum,n)=>sum+n*n,0);
    if(norm>=.98&&norm<=1.02)preview.room.entrance={id:crypto.randomUUID(),name:'Recovered photo view',position:[...pose.position] as Vec3,quaternion:[...pose.quaternion] as Quat};
  }
  if(vector(provenance?.displayRotation,3,1e6))preview.room.transform.rotation=[...provenance.displayRotation] as Vec3;
  return preview;
}

export function checkVideoSelection(file:Pick<File,'name'|'size'>):string|null{
  if(!/\.(mp4|mov|webm)$/i.test(file.name))return 'Choose an MP4, MOV or WebM video.';
  if(file.size<=0||file.size>256*1024**2)return 'Choose a nonempty video up to 256 MiB.';
  return null;
}
export function videoFrameLabel(asset:Asset):string|null{
  const value=(asset.metadata?.videoFrame as {timestampSeconds?:unknown}|undefined)?.timestampSeconds;
  if(typeof value!=='number'||!Number.isFinite(value)||value<0)return null;
  const tenths=Math.round(value*10);
  return `Video · ${Math.floor(tenths/600)}:${((tenths%600)/10).toFixed(1).padStart(4,'0')}`;
}

/** Capture-view registration is evidence about the input images, not physical room completeness. */
export function reconstructionCoverage(job:ReconstructionJob|null){
  if(!job)return null;
  const total=job.totalPhotos,matched=job.registeredPhotos;
  if(!Number.isInteger(total)||total<=0||!Number.isInteger(matched)||matched<0||matched>total)return null;
  // Jobs initialize counters before matching; only a consistent partition proves measurement.
  if(!Array.isArray(job.sourceAssetIds)||!Array.isArray(job.unregisteredAssetIds))return null;
  const sources=new Set(job.sourceAssetIds),unmatched=new Set(job.unregisteredAssetIds);
  if(sources.size!==total||unmatched.size!==total-matched||[...unmatched].some(id=>!sources.has(id)))return null;
  return {matched,total,percent:Math.round(1000*matched/total)/10,partial:matched<total};
}
export function reconstructionCaptureViews(project:Project,job:ReconstructionJob){
  const ids=[...new Set(job.sourceAssetIds??[])];
  const coverage=reconstructionCoverage(job);
  const unmatched=new Set(job.unregisteredAssetIds??[]);
  const known=!!coverage&&ids.length===coverage.total&&unmatched.size===coverage.total-coverage.matched&&[...unmatched].every(id=>ids.includes(id));
  return ids.flatMap(id=>{
    const asset=project.assets.find(a=>a.id===id&&a.role==='capture'&&a.kind==='image');
    return asset?[{asset,status:known?(unmatched.has(id)?'unmatched':'matched'):'unknown'}]:[];
  });
}
export function reconstructionSourceVideos(project:Project,job:ReconstructionJob){
  const ids=new Set(reconstructionCaptureViews(project,job).map(({asset})=>(asset.metadata?.videoFrame as {sourceAssetId?:string}|undefined)?.sourceAssetId));
  return project.assets.filter(a=>a.role==='media'&&a.kind==='video'&&ids.has(a.id));
}
