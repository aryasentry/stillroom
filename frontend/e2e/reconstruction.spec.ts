import {test,expect} from '@playwright/test';
import type {Page} from '@playwright/test';
import path from 'node:path';
import fs from 'node:fs/promises';

const evidence=path.resolve('..','docs','evidence');
const fixture=(name:string)=>path.resolve('..','fixtures',name);
const captureNames=Array.from({length:54},(_,i)=>`view_${String(i+1).padStart(3,'0')}.jpg`);
async function collection(page:Page,name:string){
  const summaries=await(await page.request.get('/api/projects')).json();
  const found=summaries.find((p:any)=>p.title===name);
  return found?(await page.request.get(`/api/projects/${found.id}`)).json():null;
}
async function begin(page:Page,name:string,files:string[]){
  await page.goto('/');await page.getByRole('button',{name:'Create a room from photos',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Bring a place back.',exact:true})).toBeVisible();
  await page.getByLabel('Place name',{exact:true}).fill(name);
  await page.getByLabel('Choose room photos',{exact:true}).setInputFiles(files);
  await page.getByRole('button',{name:`Copy ${files.length} photos to this place`,exact:false}).click();
  await expect(page.locator('.pending-label')).toHaveCount(0);
  await expect(page.locator('.reconstruction-working')).toHaveCount(0);
}
async function blockExternal(page:Page){
  const external:string[]=[],errors:string[]=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('console',message=>{if(message.type()==='error')errors.push(message.text())});
  await page.context().route('**/*',route=>{const u=new URL(route.request().url());if(['http:','https:'].includes(u.protocol)&&!['127.0.0.1','localhost'].includes(u.hostname)){external.push(u.href);return route.abort()}return route.continue()});
  return {external,errors};
}

test('photos are the main flow, with capture guidance, removal, deduplication and mobile layout',async({page})=>{
  const observed=await blockExternal(page);const name='Photo collection browser check';
  await begin(page,name,captureNames.slice(0,3).map(n=>fixture(`capture-room/images/${n}`)));
  await expect(page.getByRole('button',{name:'Capture guide',exact:false})).toHaveAttribute('aria-expanded','true');
  await expect(page.getByText('turning from one spot is not enough.',{exact:false})).toBeVisible();
  const before=await collection(page,name);expect(before.captureAssetIds).toHaveLength(3);expect(before.room.scanAssetId).toBeNull();
  await page.getByRole('button',{name:'Remove view_001.jpg',exact:true}).click();
  await expect(page.locator('.capture-grid figure')).toHaveCount(2);
  const removed=await collection(page,name);expect(removed.captureAssetIds).toHaveLength(2);expect(removed.assets).toHaveLength(before.assets.length);
  await page.getByLabel('Choose room photos',{exact:true}).setInputFiles(fixture('capture-room/images/view_001.jpg'));
  await page.getByRole('button',{name:'Copy 1 photos to this place',exact:false}).click();
  await expect(page.locator('.pending-label')).toHaveCount(0);await expect(page.locator('.reconstruction-working')).toHaveCount(0);
  const restored=await collection(page,name);expect([...restored.captureAssetIds].sort()).toEqual([...before.captureAssetIds].sort());expect(restored.assets).toHaveLength(before.assets.length);
  await fs.mkdir(evidence,{recursive:true});await page.screenshot({path:path.join(evidence,'photos-first.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(evidence,'photos-mobile.png'),fullPage:true});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  expect(observed.external).toEqual([]);expect(observed.errors).toEqual([]);
});

test('native insufficient-overlap result keeps photos and supports cancellation and retry',async({page})=>{
  const runtime=await(await page.request.get('/api/reconstruction/runtime')).json();test.skip(!runtime.available,'Optional native reconstruction runtime is not installed');
  const observed=await blockExternal(page);const name='Insufficient photo browser check';
  await begin(page,name,['dark.png','gray.png','light.png'].map(n=>fixture(`capture-insufficient/${n}`)));
  await page.getByLabel('Reconstruction quality',{exact:true}).selectOption('preview');
  await page.getByRole('button',{name:'Create a room from photos',exact:true}).click();
  await expect(page.getByRole('heading',{name:'More overlap needed',exact:true})).toBeVisible({timeout:60000});
  await expect(page.locator('.unmatched-label')).toHaveCount(3);
  const p=await collection(page,name);expect(p.captureAssetIds).toHaveLength(3);expect(p.room.scanAssetId).toBeNull();
  await page.screenshot({path:path.join(evidence,'photos-needs-overlap.png'),fullPage:true});
  await page.getByRole('button',{name:'Try a new reconstruction',exact:false}).click();
  await page.getByRole('button',{name:'Cancel reconstruction',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Cancelled · photos kept',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Try a new reconstruction',exact:false}).click();
  await expect(page.getByRole('heading',{name:'More overlap needed',exact:true})).toBeVisible({timeout:60000});
  const jobs=await(await page.request.get(`/api/projects/${p.id}/reconstructions`)).json();
  expect(jobs.map((j:any)=>j.status)).toEqual(['needs_photos','cancelled','needs_photos']);
  await page.getByRole('button',{name:'Back to places',exact:true}).click();
  await page.locator('.project-card').filter({hasText:name}).getByRole('button',{name:'More overlap needed',exact:false}).click();
  await expect(page.getByRole('heading',{name:'More overlap needed',exact:true})).toBeVisible();
  expect((await collection(page,name)).captureAssetIds).toEqual(p.captureAssetIds);
  expect(observed.external).toEqual([]);expect(observed.errors).toEqual([]);
});

test('photo wizard cannot undo or autosave edits in the saved room',async({page})=>{
  await page.goto('/');await page.getByRole('button',{name:'Explore mesh',exact:true}).click();
  await expect(page.locator('.room-viewer canvas')).toBeVisible();await expect(page.locator('.room-viewer-message')).toHaveCount(0);await page.getByRole('button',{name:'Edit',exact:true}).click();
  await page.locator('.memory-card').click();await page.getByRole('textbox',{name:'THE STORY',exact:true}).fill('Keep this saved memory while reviewing photographs.');
  await expect(page.locator('.save-state')).toHaveText('Saved locally');
  const before=await collection(page,'The afternoon room');
  await page.getByRole('button',{name:'Place options',exact:true}).click();await page.getByRole('button',{name:'Create a room from photos',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Bring a place back.',exact:true})).toBeVisible();await page.getByRole('heading',{name:'Bring a place back.',exact:true}).click();
  await page.keyboard.press('Control+z');await page.keyboard.press('Meta+z');await page.waitForTimeout(900);
  const after=await(await page.request.get(`/api/projects/${before.id}`)).json();
  expect(after.revision).toBe(before.revision);expect(after.memories).toEqual(before.memories);
});

test('native photos create a real preview, accept a room, and retain a memory',async({page})=>{
  test.skip(process.env.STILLROOM_TEST_NATIVE_RECONSTRUCTION!=='1','Long native GPU reconstruction is explicitly opt-in');
  test.setTimeout(30*60*1000);
  page.setDefaultTimeout(15000);
  const observed=await blockExternal(page);const name='Synthetic photos reconstructed locally';const started=Date.now();
  const resumeProject=process.env.STILLROOM_TEST_RESUME_RECONSTRUCTION;
  let first:any,ready:any;
  if(resumeProject){
    first=await(await page.request.get(`/api/projects/${resumeProject}`)).json();
    expect(first.title).toBe(name);expect(first.captureAssetIds).toHaveLength(54);
    ready=(await(await page.request.get(`/api/projects/${first.id}/reconstructions`)).json())[0];
    expect(ready.status).toBe('accepted');expect(first.room.scanAssetId).toBe(ready.resultAssetId);
    await page.goto('/');await page.getByRole('button',{name:`Open ${name}`,exact:true}).click();
    await expect(page.locator('.room-viewer-message')).toHaveCount(0);
    await page.getByRole('button',{name:'Edit',exact:true}).click();await page.getByRole('button',{name:'Room setup',exact:true}).click();
  }else{
  await begin(page,name,captureNames.map(n=>fixture(`capture-room/images/${n}`)));
  await page.getByLabel('Reconstruction quality',{exact:true}).selectOption('preview');
  await page.getByRole('button',{name:'Create a room from photos',exact:true}).click();
  first=await collection(page,name);expect(first.room.scanAssetId).toBeNull();
  const result=async()=>{const jobs=await(await page.request.get(`/api/projects/${first.id}/reconstructions`)).json();return jobs[0]};
  await expect.poll(async()=>{const job=await result();if(!job)return 'starting';if(['failed','needs_photos','cancelled','interrupted'].includes(job.status))throw new Error(JSON.stringify(job));return job.status},{timeout:25*60*1000,intervals:[3000]}).toBe('ready');
  ready=await result();expect(ready.registeredPhotos).toBeGreaterThanOrEqual(3);expect((await collection(page,name)).room.scanAssetId).toBeNull();
  await page.getByRole('button',{name:'Preview reconstructed room',exact:false}).click();
  await expect(page.locator('.reconstruction-preview canvas')).toBeVisible();await expect(page.getByRole('button',{name:'Use this room',exact:true})).toBeEnabled({timeout:60000});
  await page.getByRole('button',{name:'Fly',exact:true}).click();await page.locator('.reconstruction-preview canvas').focus();await page.keyboard.down('KeyW');await page.waitForTimeout(500);await page.keyboard.up('KeyW');await page.getByRole('button',{name:'Reset view',exact:true}).click();await page.getByRole('button',{name:'Orbit',exact:true}).click();
  await page.screenshot({path:path.join(evidence,'photo-reconstruction-preview.png'),fullPage:true});
  await page.getByRole('button',{name:'Use this room',exact:true}).click();
  }
  await expect(page.locator('.room-topbar h1')).toHaveText(name);await expect(page.locator('.room-viewer-message')).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Walk',exact:true})).toBeDisabled();
  await page.getByRole('button',{name:'Close room setup',exact:true}).click();
  if(resumeProject&&first.memories.some((m:any)=>m.title==='Made from photographs')){
    await page.locator('.memory-card').filter({hasText:'Made from photographs'}).click();
  }else{
  await page.getByRole('button',{name:'Leave a memory',exact:true}).click();
  const box=await page.locator('.room-viewer canvas').boundingBox();if(!box)throw new Error('Missing real reconstructed canvas');
  await page.mouse.click(box.x+box.width*.5,box.y+box.height*.5);
  }
  await expect(page.getByLabel('Memory title',{exact:true})).toBeVisible();await page.getByLabel('Memory title',{exact:true}).fill('Made from photographs');
  await page.getByRole('textbox',{name:'THE STORY',exact:true}).fill('A memory placed in the actual locally reconstructed scene.');await expect(page.locator('.save-state')).toHaveText('Saved locally');
  const accepted=await collection(page,name);expect(accepted.room.sceneType).toBe('splat');expect(accepted.room.sourceUnits).toBe('unknown');expect(accepted.room.scanAssetId).toBe(ready.resultAssetId);expect(accepted.memories[0].anchor.placement).toBe('approximate');
  const scan=accepted.assets.find((a:any)=>a.id===ready.resultAssetId);expect(accepted.room.entrance.position).toEqual(scan.metadata.reconstruction.initialView.position);expect(accepted.room.transform.rotation).toEqual(scan.metadata.reconstruction.displayRotation);expect(accepted.room.floorY).toBeNull();
  await page.getByRole('button',{name:'Back to places',exact:true}).click();await page.getByRole('button',{name:`Open ${name}`,exact:true}).click();
  await expect(page.locator('.room-viewer canvas')).toBeVisible();
  await expect(page.locator('.room-viewer-message')).toHaveCount(0);
  await expect(page.locator('.memory-card')).toHaveCount(1);await page.locator('.memory-card').click();await expect(page.getByText('A memory placed in the actual locally reconstructed scene.',{exact:true}).last()).toBeVisible();
  await expect(page.getByRole('button',{name:'Open memory: Made from photographs',exact:true})).toBeVisible();
  await page.screenshot({path:path.join(evidence,'photo-reconstruction-memory.png')});
  expect(observed.external).toEqual([]);expect(observed.errors).toEqual([]);
  await fs.writeFile(path.join(evidence,'photo-browser-verification.json'),JSON.stringify({interactionCheckSeconds:(Date.now()-started)/1000,jobElapsedSeconds:(Date.parse(ready.updatedAt)-Date.parse(ready.createdAt))/1000,resumedAcceptedNativeResult:!!resumeProject,projectId:first.id,job:ready,scan:accepted.assets.find((a:any)=>a.id===ready.resultAssetId),memoryPersisted:true,browserExternalRequests:observed.external,browserErrors:observed.errors,fixture:'Synthetic 54-photo rendered room; no ground-truth cameras or geometry passed to reconstruction',liveRealRoomTested:false},null,2));
});

test('partial review exposes unmatched capture views and plays the retained original',async({page})=>{
  const observed=await blockExternal(page);
  const headers={'X-Stillroom-Request':'local'};
  const project=await(await page.request.post('/api/projects',{headers,data:{title:`Partial review ${Date.now()}`}})).json();
  const upload=async(name:string,role:string,mimeType:string)=>(await page.request.post(`/api/projects/${project.id}/assets`,{headers,multipart:{role,file:{name:path.basename(name),mimeType,buffer:await fs.readFile(fixture(name))}}})).json();
  const video=await upload('memory-video.webm','media','video/webm');
  const images=[];for(const name of captureNames.slice(0,3))images.push(await upload(`capture-room/images/${name}`,'capture','image/jpeg'));
  const scan=await upload('synthetic-room.ply','scan','application/octet-stream');
  const saved=await(await page.request.get(`/api/projects/${project.id}`)).json();
  for(const [index,photo] of images.entries())saved.assets.find((a:any)=>a.id===photo.id).metadata.videoFrame={sourceAssetId:video.id,timestampSeconds:index};
  const job={id:'controlled-partial',projectId:project.id,status:'ready',stage:'complete',message:'Ready to preview',createdAt:new Date().toISOString(),updatedAt:new Date().toISOString(),quality:'preview',sourceAssetIds:images.map(a=>a.id),totalPhotos:3,registeredPhotos:2,unregisteredAssetIds:[images[1].id],components:[2,1],resultAssetId:scan.id,warnings:[],error:null,iteration:3,totalIterations:3};
  await page.route(`**/api/projects/${project.id}`,route=>route.fulfill({json:saved}));
  await page.route('**/api/reconstructions',route=>route.fulfill({json:[job]}));
  await page.route(`**/api/projects/${project.id}/reconstructions`,route=>route.fulfill({json:[job]}));
  await page.goto('/');await page.locator('.project-card').filter({hasText:project.title}).locator('.library-job').click();
  await expect(page.getByRole('heading',{name:'Partial reconstruction ready',exact:true})).toBeVisible();
  await expect(page.getByLabel('Reconstruction quality',{exact:true})).toHaveValue('standard');
  await expect(page.getByText('2 of 3 capture views matched · 66.7%',{exact:true})).toBeVisible();
  await expect(page.locator('.capture-coverage-strip .unmatched')).toHaveCount(1);
  await page.getByRole('button',{name:'Preview reconstructed room',exact:false}).click();
  await expect(page.locator('.reconstruction-preview .partial-preview-warning')).toContainText('Partial reconstruction');
  await expect(page.locator('.reconstruction-preview .partial-preview-warning')).toContainText('2 of 3');
  const original=page.getByLabel(`Original video: ${video.originalName}`,{exact:true});
  await expect(original).toHaveAttribute('src',`/api/projects/${project.id}/assets/${video.id}`);
  await original.evaluate(async(node:HTMLVideoElement)=>{node.muted=true;await node.play()});
  await expect.poll(()=>original.evaluate((node:HTMLVideoElement)=>node.currentTime)).toBeGreaterThan(0);
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:path.resolve('..','test-results','partial-review-mobile.png'),fullPage:true});
  // Reopening an accepted attempt must retain source playback without requiring a ready preview.
  job.status='accepted';
  saved.room.scanAssetId=scan.id;saved.room.scanVersion=scan.sha256;saved.room.sceneType='splat';
  await page.reload();
  await page.getByRole('button',{name:`Open ${project.title}`,exact:true}).click();
  await page.getByRole('button',{name:'Place options',exact:true}).click();
  await page.getByRole('button',{name:'Create a room from photos',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Partial room accepted',exact:true})).toBeVisible();
  await expect(original).toHaveAttribute('src',`/api/projects/${project.id}/assets/${video.id}`);
  await original.evaluate(async(node:HTMLVideoElement)=>{node.muted=true;await node.play()});
  await expect.poll(()=>original.evaluate((node:HTMLVideoElement)=>node.currentTime)).toBeGreaterThan(0);

  expect(observed.external).toEqual([]);expect(observed.errors).toEqual([]);
});

test('experimental sparse mode requires its own model and sends selected originals',async({page})=>{
  const observed=await blockExternal(page);
  const runtime={available:false,installing:false,supported:true,platform:'test',message:'Optional download: 517 MiB model plus dependencies.',error:null,versions:{}};
  await page.route('**/api/reconstruction/assisted-runtime',route=>route.fulfill({json:runtime}));
  await page.route('**/api/reconstruction/runtime',route=>route.fulfill({json:{...runtime,available:true}}));
  await page.route('**/api/reconstruction/assisted-runtime/install',async route=>{
    runtime.available=true;await route.fulfill({json:runtime});
  });
  await begin(page,`Sparse controls ${Date.now()}`,captureNames.slice(0,3).map(n=>fixture(`capture-room/images/${n}`)));
  await expect(page.getByLabel('Reconstruction quality',{exact:true})).toHaveValue('standard');
  await page.getByLabel('Reconstruction quality',{exact:true}).selectOption('assisted');
  await expect(page.getByRole('heading',{name:'Fewer frames, estimated cameras',exact:true})).toBeVisible();
  const start=page.getByRole('button',{name:'Create a room from photos',exact:true});
  await expect(start).toBeDisabled();
  await page.getByRole('button',{name:'Prepare sparse-frame model',exact:true}).click();
  await expect(start).toBeEnabled();
  let sent:any=null;
  await page.route('**/api/projects/*/reconstructions',async route=>{
    if(route.request().method()!=='POST')return route.continue();
    sent=route.request().postDataJSON();await route.fulfill({status:422,json:{detail:'Controlled test: native reconstruction is checked separately.'}});
  });
  await start.click();await expect(page.getByRole('alert')).toContainText('Controlled test');
  expect(sent.quality).toBe('assisted');expect(sent.imageIds).toHaveLength(3);
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  expect(observed.external).toEqual([]);
  // The deliberate HTTP 422 is expected; no uncaught page errors are permitted.
  expect(observed.errors.filter(x=>!x.includes('422'))).toEqual([]);
});
