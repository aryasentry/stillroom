import type { Asset, Memory, Project, ProjectSummary, ReconstructionJob, ReconstructionRuntime, VideoExtractionJob } from './types';

export class ApiError extends Error { constructor(message:string, public status:number) {super(message);} }
async function request<T>(path:string, init:RequestInit={}):Promise<T> {
  const response = await fetch(`/api${path}`, {...init, headers:{'X-Stillroom-Request':'local',...(init.body instanceof FormData?{}:{'Content-Type':'application/json'}),...init.headers}});
  if (!response.ok) { const data=await response.json().catch(()=>({detail:response.statusText})); throw new ApiError(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail),response.status); }
  if(response.status===204)return undefined as T;
  const value=await response.json();
  if(value && (value.schemaVersion===1 || value.schemaVersion===2) && Array.isArray(value.assets))value.captureAssetIds ??= [];
  return value as T;
}
function upload<T>(path:string,file:File,fields:Record<string,string>={},onProgress?:(n:number)=>void,signal?:AbortSignal):Promise<T>{
  return new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest(); const form=new FormData(); form.append('file',file); Object.entries(fields).forEach(([k,v])=>form.append(k,v));
    xhr.open('POST',`/api${path}`);xhr.setRequestHeader('X-Stillroom-Request','local');
    xhr.upload.onprogress=e=>{if(e.lengthComputable)onProgress?.(100*e.loaded/e.total)};
    const abort=()=>xhr.abort();signal?.addEventListener('abort',abort,{once:true});
    const clean=()=>signal?.removeEventListener('abort',abort);
    xhr.onload=()=>{clean();let value;try{value=JSON.parse(xhr.responseText)}catch{reject(new Error('The local server returned an unreadable response.'));return}if(xhr.status<200||xhr.status>=300)reject(new ApiError(typeof value.detail==='string'?value.detail:JSON.stringify(value.detail),xhr.status));else resolve(value)};
    xhr.onerror=()=>{clean();reject(new Error('Cannot reach the local server. Your source file was not changed.'))};
    xhr.onabort=()=>{clean();reject(new DOMException('Import cancelled','AbortError'))};
    if(signal?.aborted){reject(new DOMException('Import cancelled','AbortError'));return}xhr.send(form);
  });
}
export const api={
  listVideoExtractions:(id:string)=>request<VideoExtractionJob[]>(`/projects/${id}/video-extractions`),
  getVideoExtraction:(id:string,jobId:string)=>request<VideoExtractionJob>(`/projects/${id}/video-extractions/${jobId}`),
  startVideoExtraction:(id:string,assetId:string,targetFrames:number)=>request<VideoExtractionJob>(`/projects/${id}/video-extractions`,{method:'POST',body:JSON.stringify({assetId,targetFrames})}),
  cancelVideoExtraction:(id:string,jobId:string)=>request<VideoExtractionJob>(`/projects/${id}/video-extractions/${jobId}/cancel`,{method:'POST'}),
  reconstructionRuntime:()=>request<ReconstructionRuntime>('/reconstruction/runtime'),
  installReconstructionRuntime:()=>request<ReconstructionRuntime>('/reconstruction/runtime/install',{method:'POST'}),
  listReconstructions:(id?:string)=>request<ReconstructionJob[]>(id?`/projects/${id}/reconstructions`:'/reconstructions'),
  getReconstruction:(id:string,jobId:string)=>request<ReconstructionJob>(`/projects/${id}/reconstructions/${jobId}`),
  startReconstruction:(id:string,imageIds:string[],quality:'preview'|'standard')=>request<ReconstructionJob>(`/projects/${id}/reconstructions`,{method:'POST',body:JSON.stringify({imageIds,quality})}),
  cancelReconstruction:(id:string,jobId:string)=>request<ReconstructionJob>(`/projects/${id}/reconstructions/${jobId}/cancel`,{method:'POST'}),
  acceptReconstruction:(id:string,jobId:string,expectedRevision:number)=>request<Project>(`/projects/${id}/reconstructions/${jobId}/accept`,{method:'POST',body:JSON.stringify({expectedRevision})}),
  removeCapture:(id:string,assetId:string)=>request<Project>(`/projects/${id}/captures/${assetId}`,{method:'DELETE'}),
  listProjects:()=>request<ProjectSummary[]>('/projects'),
  createProject:(title:string)=>request<Project>('/projects',{method:'POST',body:JSON.stringify({title})}),
  getProject:(id:string)=>request<Project>(`/projects/${id}`),
  saveProject:(project:Project,expectedRevision:number)=>request<Project>(`/projects/${project.id}`,{method:'PUT',body:JSON.stringify({project,expectedRevision})}),
  uploadAsset:(id:string,file:File,role:Asset['role'],onProgress?:(n:number)=>void,signal?:AbortSignal)=>upload<Asset>(`/projects/${id}/assets`,file,{role},onProgress,signal),
  demo:(kind:'mesh'|'ply'|'spz'='mesh')=>request<Project>(`/demo?kind=${kind}`,{method:'POST'}),
  importPackage:(file:File,signal?:AbortSignal)=>upload<Project>('/import',file,{},undefined,signal),
  search:(id:string,q:string)=>request<Memory[]>(`/projects/${id}/search?q=${encodeURIComponent(q)}`),
  integrity:(id:string)=>request<{missing:string[];corrupt:string[]}>(`/projects/${id}/integrity`),
  relink:(id:string,assetId:string,file:File)=>upload<Asset>(`/projects/${id}/assets/${assetId}/relink`,file),
  deleteProject:(id:string)=>request<void>(`/projects/${id}`,{method:'DELETE'}),
  restoreProject:(id:string)=>request<Project>(`/projects/${id}/restore`,{method:'POST'}),
  recoverProject:(id:string)=>request<Project>(`/projects/${id}/recover`,{method:'POST'}),
  assetUrl:(id:string,assetId:string)=>`/api/projects/${id}/assets/${assetId}`,
  exportUrl:(id:string)=>`/api/projects/${id}/export`,
};
