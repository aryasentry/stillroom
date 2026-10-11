import {describe,it,expect} from 'vitest';
import {sourceToWorld,worldToSource,calibratedScale,distanceDiscrepancy,replaceScan,isSupportedScale,isSupportedCoordinate} from './coordinates';
import type {Project,Transform,Asset,Viewpoint} from './types';
describe('stable source frame',()=>{
  const transform:Transform={position:[4,2,-7],rotation:[Math.PI/2,0,Math.PI/4],scale:2.7};
  it('round trips anchors under orientation, translation and scale',()=>{const p:[number,number,number]=[1,2,3];worldToSource(sourceToWorld(p,transform),transform).forEach((x,i)=>expect(x).toBeCloseTo(p[i],10));});
  it('calibrates source distance, not already transformed distance',()=>expect(calibratedScale([0,0,0],[3,4,0],10)).toBe(2));
  it('rejects coincident and nonpositive measurements',()=>{expect(()=>calibratedScale([1,0,0],[1,0,0],2)).toThrow();expect(()=>calibratedScale([0,0,0],[1,0,0],0)).toThrow()});
  it('keeps manual room and proxy scales inside the coordinate conversion range',()=>{
    for(const value of [1e-300,1e-7,0,-1,1_000_001,Infinity,NaN])expect(isSupportedScale(value)).toBe(false);
    for(const value of [1e-6,1,1e6])expect(isSupportedScale(value)).toBe(true);
  });
  it('rejects coordinates that cannot be saved while accepting supported floor boundaries',()=>{
    for(const value of [1e308,-1e308,1_000_000_001,Infinity,NaN])expect(isSupportedCoordinate(value)).toBe(false);
    for(const value of [-1e9,0,1e9])expect(isSupportedCoordinate(value)).toBe(true);
  });
  it('reports absolute and relative independent discrepancy',()=>{expect(distanceDiscrepancy([0,0,0],[3,4,0],2,9)).toEqual({measured:10,absoluteError:1,relativeError:1/9})});
  it('scan replacement flags old anchors without moving or remapping them',()=>{const p={room:{},memories:[{anchor:{position:[1,2,3],assetId:'old',scanVersion:'old',needsReview:false}}]} as unknown as Project;replaceScan(p,{id:'new',sha256:'newhash',kind:'glb'} as Asset);expect(p.memories[0].anchor.position).toEqual([1,2,3]);expect(p.memories[0].anchor.needsReview).toBe(true);expect(p.memories[0].anchor.assetId).toBe('old')});
  it('removes saved camera coordinates from a replaced scan without removing its memory anchors',()=>{
    const view:Viewpoint={id:'view',name:'Old desk',position:[2,1,-4],quaternion:[0,0,0,1],target:[2,1,-7]};
    const p={room:{entrance:view},viewpoints:[view],memories:[
      {anchor:{position:[1,2,3],assetId:'old',scanVersion:'oldhash',needsReview:false},viewpoint:view},
      {anchor:{position:[4,5,6],assetId:'new',scanVersion:'newhash',needsReview:false},viewpoint:view},
      {anchor:{position:[7,8,9],assetId:'new',scanVersion:'oldhash',needsReview:false},viewpoint:view},
    ]} as unknown as Project;
    replaceScan(p,{id:'new',sha256:'newhash',kind:'glb'} as Asset);
    expect(p.viewpoints).toEqual([]);expect(p.room.entrance).toBeNull();
    expect(p.memories[0].viewpoint).toBeNull();expect(p.memories[0].anchor).toEqual({position:[1,2,3],assetId:'old',scanVersion:'oldhash',needsReview:true});
    expect(p.memories[1].viewpoint).toEqual(view);expect(p.memories[1].anchor.needsReview).toBe(false);
    expect(p.memories[2].viewpoint).toBeNull();expect(p.memories[2].anchor.needsReview).toBe(true);
  });
});
