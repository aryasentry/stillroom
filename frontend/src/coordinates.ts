import {Euler,Matrix4,Quaternion,Vector3} from 'three';
import type {Asset,Project,Transform,Vec3} from './types';
export const MIN_SCALE=1e-6,MAX_SCALE=1e6,MAX_COORDINATE=1e9,MAX_ROTATION=1e6;
export function isSupportedScale(value:number){return Number.isFinite(value)&&value>=MIN_SCALE&&value<=MAX_SCALE;}
export function isSupportedCoordinate(value:number){return Number.isFinite(value)&&Math.abs(value)<=MAX_COORDINATE;}
export function roomMatrix(t:Transform):Matrix4 { return new Matrix4().compose(new Vector3(...t.position),new Quaternion().setFromEuler(new Euler(...t.rotation,'XYZ')),new Vector3(t.scale,t.scale,t.scale)); }
export function sourceToWorld(p:Vec3,t:Transform):Vec3 {return new Vector3(...p).applyMatrix4(roomMatrix(t)).toArray() as Vec3;}
export function worldToSource(p:Vec3,t:Transform):Vec3 {return new Vector3(...p).applyMatrix4(roomMatrix(t).invert()).toArray() as Vec3;}
export function calibratedScale(a:Vec3,b:Vec3,known:number):number {
  const distance=new Vector3(...a).distanceTo(new Vector3(...b));
  if(!Number.isFinite(known)||known<=0||!Number.isFinite(distance)||distance<1e-8)throw new Error('Select two distinct points and enter a positive measured distance.');
  const scale=known/distance;if(!isSupportedScale(scale))throw new Error('Calibration scale exceeds the supported range.');return scale;
}
export function distanceDiscrepancy(a:Vec3,b:Vec3,scale:number,known:number){
  if(!(scale>0)||!(known>0))throw new Error('Distances and scale must be positive.');
  const measured=new Vector3(...a).distanceTo(new Vector3(...b))*scale,absoluteError=Math.abs(measured-known);
  return {measured,absoluteError,relativeError:absoluteError/known};
}
export function replaceScan(project:Project,asset:Asset){
  for(const memory of project.memories)if(memory.anchor.assetId!==asset.id||memory.anchor.scanVersion!==asset.sha256){memory.anchor.needsReview=true;memory.viewpoint=null;}
  project.viewpoints=[];
  Object.assign(project.room,{scanAssetId:asset.id,scanVersion:asset.sha256,sceneType:asset.kind==='glb'?'mesh':'splat',entrance:null,floorY:null,sourceUnits:'unknown',calibration:null,distanceChecks:[],collisionAssetId:null,collisionAligned:false,synthetic:false,transform:{position:[0,0,0],rotation:[0,0,0],scale:1}});
}
