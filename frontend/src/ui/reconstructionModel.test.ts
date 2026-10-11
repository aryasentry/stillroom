import {describe,expect,it} from 'vitest';
import {captureAssets,checkPhotoSelection,checkVideoSelection,videoFrameLabel,makeReconstructionPreview,jobLabel,reconstructionCoverage,reconstructionCaptureViews,reconstructionSourceVideos} from './reconstructionModel';
import type {Asset,Project,ReconstructionJob} from '../types';
const photo=(id:string,size=100):Asset=>({id,role:'capture',kind:'image',size} as Asset);
describe('photo collection and isolated reconstruction preview',()=>{
  it('uses the active collection, deduplicates IDs and ignores removed originals',()=>{
    const project={assets:[photo('a'),photo('b')],captureAssetIds:['a','a','missing']} as Project;
    expect(captureAssets(project).map(a=>a.id)).toEqual(['a']);
    expect(captureAssets({...project,captureAssetIds:undefined})).toEqual([]);
  });
  it('rejects unsupported formats and aggregate capacity before upload',()=>{
    expect(checkPhotoSelection([{name:'phone.HEIC',size:200}])).toMatch(/HEIC/);
    expect(checkPhotoSelection([{name:'image.jpg',size:41*1024**2}])).toMatch(/40 MiB/);
    expect(checkPhotoSelection([{name:'image.jpg',size:20}],200,0)).toMatch(/200/);
    expect(checkPhotoSelection([{name:'image.png',size:20}],1,256*1024**2)).toMatch(/256 MiB/);
    expect(checkPhotoSelection([{name:'image.webp',size:20}],1,30)).toBeNull();
  });
  it('previews only managed ready scan output and never changes saved setup or memories',()=>{
    const result={id:'new',sha256:'hash',role:'scan',kind:'ply'} as Asset;
    const source={id:'room',assets:[result],room:{scanAssetId:'old',transform:{scale:3},calibration:{knownDistance:4}},memories:[{id:'memory',anchor:{assetId:'old'}}],viewpoints:[{id:'old-view'}]} as Project;
    const original=structuredClone(source);
    const job={status:'ready',resultAssetId:'new',projectId:'room'} as ReconstructionJob;
    const preview=makeReconstructionPreview(source,job);
    expect(preview.room.scanAssetId).toBe('new');expect(preview.room.calibration).toBeNull();expect(preview.room.transform.scale).toBe(1);expect(preview.memories).toEqual([]);expect(source).toEqual(original);
    expect(()=>makeReconstructionPreview(source,{...job,status:'running'})).toThrow();
    expect(()=>makeReconstructionPreview(source,{...job,resultAssetId:'absent'})).toThrow();
  });
  it('starts from a recovered photo camera in the estimated upright frame without changing the saved room',()=>{
    const pose={position:[2,1,-3],quaternion:[1,0,0,0]};
    const result:Asset={id:'new',sha256:'hash',role:'scan',kind:'ply',originalName:'room.ply',mimeType:'application/octet-stream',size:1,path:'new.ply',createdAt:'2026-10-11T00:00:00Z',metadata:{reconstruction:{initialView:pose,displayRotation:[Math.PI,0,0]}}};
    const source={id:'room',assets:[result],room:{scanAssetId:'old',entrance:null,transform:{scale:3}},memories:[],viewpoints:[]} as unknown as Project;
    const before=structuredClone(source);const job={status:'ready',resultAssetId:'new',projectId:'room'} as ReconstructionJob;
    const preview=makeReconstructionPreview(source,job);
    expect(preview.room.entrance?.position).toEqual(pose.position);expect(preview.room.entrance?.quaternion).toEqual(pose.quaternion);
    expect(preview.room.transform.rotation).toEqual([Math.PI,0,0]);expect(preview.room.sourceUnits).toBe('unknown');expect(preview.room.floorY).toBeNull();expect(source).toEqual(before);
  });
});

describe('video capture inputs and provenance',()=>{
  it('accepts supported video containers and enforces nonempty 256 MiB source limits',()=>{
    for(const name of ['room.mp4','room.MOV','room.webm'])expect(checkVideoSelection({name,size:256*1024**2})).toBeNull();
    expect(checkVideoSelection({name:'room.avi',size:10})).toMatch(/MP4, MOV or WebM/);
    expect(checkVideoSelection({name:'room.mp4',size:0})).toMatch(/nonempty/);
    expect(checkVideoSelection({name:'room.mp4',size:256*1024**2+1})).toMatch(/256 MiB/);
  });
  it('shows a frame timestamp only for valid video provenance',()=>{
    expect(videoFrameLabel({...photo('frame'),metadata:{videoFrame:{timestampSeconds:65.25}}})).toBe('Video · 1:05.3');
    expect(videoFrameLabel({...photo('frame'),metadata:{videoFrame:{timestampSeconds:-1}}})).toBeNull();
    expect(videoFrameLabel(photo('photo'))).toBeNull();
  });
});

describe('honest reconstruction review',()=>{
  const attempt={status:'ready',sourceAssetIds:['a','b','c'],totalPhotos:3,registeredPhotos:2,unregisteredAssetIds:['b']} as ReconstructionJob;
  it('labels a ready partial result without claiming a complete room',()=>{
    expect(jobLabel(attempt)).toBe('Partial reconstruction ready');
    expect(jobLabel({...attempt,status:'accepted'})).toBe('Partial room accepted');
    expect(jobLabel({...attempt,registeredPhotos:3,unregisteredAssetIds:[]})).toBe('Ready to preview');
  });
  it('reports matched capture fraction and refuses unknown or inconsistent counts',()=>{
    expect(reconstructionCoverage(attempt)).toEqual({matched:2,total:3,percent:66.7,partial:true});
    expect(reconstructionCoverage({...attempt,totalPhotos:200,registeredPhotos:199,sourceAssetIds:Array.from({length:200},(_,i)=>String(i)),unregisteredAssetIds:['199']})?.percent).toBe(99.5);
    for(const job of [{status:'ready'}, {...attempt,totalPhotos:0}, {...attempt,registeredPhotos:4}, {...attempt,registeredPhotos:NaN}])expect(reconstructionCoverage(job as ReconstructionJob)).toBeNull();
  });
  it('does not present initialized counters as measured coverage when stopped before matching',()=>{
    const initial={...attempt,stage:'preparing' as const,registeredPhotos:0,unregisteredAssetIds:[]};
    expect(reconstructionCoverage({...initial,status:'cancelled'})).toBeNull();
    expect(reconstructionCoverage({...initial,status:'failed'})).toBeNull();
    expect(reconstructionCoverage({...initial,status:'interrupted'})).toBeNull();
  });
  it('retains measured coverage after a later failure, including measured zero matches',()=>{
    expect(reconstructionCoverage({...attempt,status:'failed',stage:'training'})).toEqual({matched:2,total:3,percent:66.7,partial:true});
    expect(reconstructionCoverage({...attempt,status:'failed',stage:'matching',registeredPhotos:0,unregisteredAssetIds:['a','b','c']})).toEqual({matched:0,total:3,percent:0,partial:true});
    expect(reconstructionCoverage({...attempt,status:'failed',stage:'training',unregisteredAssetIds:['foreign']})).toBeNull();
  });
  it('keeps per-view matching tied to the selected attempt, including removed captures',()=>{
    const project={assets:[photo('c'),photo('a'),photo('b'),photo('new')],captureAssetIds:['a','new']} as Project;
    expect(reconstructionCaptureViews(project,attempt).map(v=>[v.asset.id,v.status])).toEqual([['a','matched'],['b','unmatched'],['c','matched']]);
    expect(reconstructionCaptureViews(project,{...attempt,unregisteredAssetIds:[]} as ReconstructionJob).map(v=>v.status)).toEqual(['unknown','unknown','unknown']);
  });
  it('retrieves only originals used by this attempt, even when a newer video exists',()=>{
    const source={id:'original',role:'media',kind:'video'} as Asset;
    const project={assets:[source,{...source,id:'unrelated'},{...photo('a'),metadata:{videoFrame:{sourceAssetId:'original',timestampSeconds:5}}},photo('b'),photo('c')]} as Project;
    expect(reconstructionSourceVideos(project,attempt).map(a=>a.id)).toEqual(['original']);
    expect(reconstructionSourceVideos(project,{...attempt,status:'failed',registeredPhotos:0,unregisteredAssetIds:[]}).map(a=>a.id)).toEqual(['original']);
    expect(reconstructionSourceVideos(project,{status:'ready'} as ReconstructionJob)).toEqual([]);
  });
});
