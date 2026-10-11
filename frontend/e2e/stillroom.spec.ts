import {test,expect} from '@playwright/test';
import type {Page} from '@playwright/test';
import path from 'node:path';
import fs from 'node:fs/promises';

const fixture=(name:string)=>path.resolve('..','fixtures',name);
const evidence=path.resolve('..','docs','evidence');
async function saved(page:Page){await expect(page.locator('.save-state')).toHaveText('Saved locally')}
async function loaded(page:Page){await expect(page.locator('.room-viewer canvas')).toBeVisible();await expect(page.locator('.room-viewer-message')).toHaveCount(0)}
async function current(page:Page){const title=await page.locator('.room-topbar h1').innerText();const projects=await(await page.request.get('/api/projects')).json();return (await page.request.get(`/api/projects/${projects.find((p:any)=>p.title===title&&!p.deletedAt).id}`)).json()}
async function sampleActivity(page:Page){
  await page.getByRole('button',{name:'Fly',exact:true}).click();await page.locator('.room-viewer canvas').focus();
  await page.keyboard.down('KeyA');await page.waitForTimeout(2000);await page.keyboard.up('KeyA');
  await page.keyboard.down('KeyD');await page.waitForTimeout(2000);await page.keyboard.up('KeyD');
  await page.getByRole('button',{name:'Entrance',exact:true}).click();await page.getByRole('button',{name:'Orbit',exact:true}).click();
}
async function saveReport(page:Page,name:string){
  await page.getByRole('button',{name:'Edit',exact:true}).click();
  await page.getByRole('button',{name:'Room setup',exact:true}).click();
  const download=page.waitForEvent('download');await page.getByRole('button',{name:'Save device performance report'}).click();
  const file=await download;await file.saveAs(path.join(evidence,`${name}-performance.json`));const data=JSON.parse(await fs.readFile(path.join(evidence,`${name}-performance.json`),'utf8'));
  await page.getByRole('button',{name:'Close room setup'}).click();return data;
}

test('mesh memory survives save, media, undo, transform, reopen and portable archive without external network',async({page})=>{
  await fs.mkdir(evidence,{recursive:true});const external:string[]=[];const errors:string[]=[];
  await page.context().route('**/*',route=>{const u=new URL(route.request().url());if(!['127.0.0.1','localhost'].includes(u.hostname)&&['http:','https:'].includes(u.protocol)){external.push(u.href);return route.abort()}return route.continue()});
  page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text())});
  await page.goto('/');await expect(page).toHaveTitle(/Stillroom/);await page.getByRole('button',{name:'Explore mesh'}).click();await loaded(page);await saved(page);
  const demo=await current(page);expect(demo.room.synthetic).toBe(true);expect(demo.assets.find((a:any)=>a.id===demo.room.scanAssetId).metadata.textures).toBe(5);
  await page.screenshot({path:path.join(evidence,'mesh-room.png')});
  await page.getByRole('button',{name:'Edit',exact:true}).click();await page.getByRole('button',{name:'Leave a memory',exact:true}).click();
  const box=await page.locator('.room-viewer canvas').boundingBox();if(!box)throw new Error('No rendered canvas');
  await page.mouse.click(box.x+box.width*.57,box.y+box.height*.43);
  await expect(page.getByLabel('Memory title',{exact:true})).toBeVisible();await page.getByLabel('Memory title',{exact:true}).fill('Acceptance window memory');await page.getByLabel('THE STORY',{exact:true}).fill('A quiet afternoon. Persistence acceptance note.');await page.getByLabel('Tags',{exact:true}).fill('acceptance, quiet');await page.getByLabel('Tags',{exact:true}).press('Tab');
  await page.getByLabel('Choose memory attachments',{exact:true}).setInputFiles([fixture('memory-photo.png'),fixture('memory-audio.wav'),fixture('memory-video.webm')]);await expect(page.locator('.busy-backdrop')).toHaveCount(0);await saved(page);
  let p=await current(page);const memory=p.memories.find((m:any)=>m.title==='Acceptance window memory');expect(memory.anchor.placement).toBe('surface');expect(memory.mediaIds).toHaveLength(3);const original=memory.anchor.position;
  expect(await page.locator('audio').evaluate((a:HTMLAudioElement)=>a.paused&&!a.autoplay)).toBe(true);
  await page.locator('audio').evaluate((a:HTMLAudioElement)=>a.play());await expect.poll(()=>page.locator('audio').evaluate((a:HTMLAudioElement)=>a.currentTime)).toBeGreaterThan(0);await page.locator('audio').evaluate((a:HTMLAudioElement)=>a.pause());
  await expect.poll(()=>page.locator('video').evaluate((v:HTMLVideoElement)=>v.readyState)).toBeGreaterThanOrEqual(1);expect(await page.locator('video').evaluate((v:HTMLVideoElement)=>v.paused&&!v.autoplay&&v.videoWidth===320)).toBe(true);
  await page.locator('video').evaluate((v:HTMLVideoElement)=>v.play());await expect.poll(()=>page.locator('video').evaluate((v:HTMLVideoElement)=>v.currentTime)).toBeGreaterThan(0);await page.locator('video').evaluate((v:HTMLVideoElement)=>v.pause());
  await page.getByRole('button',{name:'Enlarge memory-photo.png'}).click();await page.getByRole('button',{name:'Zoom in'}).click();await expect(page.getByText('125%',{exact:true})).toBeVisible();await page.getByRole('button',{name:'Close image'}).click();
  await page.getByRole('button',{name:'Adjust anchor coordinates',exact:false}).click();await page.getByLabel('Anchor X',{exact:true}).fill(String(original[0]+.1));await saved(page);await page.getByRole('button',{name:'Undo',exact:true}).click();await saved(page);expect((await current(page)).memories.find((m:any)=>m.id===memory.id).anchor.position).toEqual(original);
  await page.getByRole('button',{name:'Room setup',exact:true}).click();await page.getByLabel('Rotation (degrees) Y',{exact:true}).fill('90');await page.getByLabel('Uniform scale',{exact:true}).fill('1.5');await saved(page);p=await current(page);expect(p.memories.find((m:any)=>m.id===memory.id).anchor.position).toEqual(original);expect(p.room.transform.scale).toBe(1.5);
  await page.getByRole('button',{name:'Close room setup'}).click();await page.getByRole('button',{name:'Back to places'}).click();await page.getByRole('button',{name:'Open The afternoon room',exact:true}).first().click();await loaded(page);expect((await current(page)).id).toBe(demo.id);await page.getByLabel('Search memories').fill('Persistence acceptance');await expect(page.locator('.memory-card')).toHaveCount(1);
  await page.locator('.memory-card').click();await expect(page.getByText('A quiet afternoon. Persistence acceptance note.',{exact:true}).last()).toBeVisible();
  p=await current(page);expect(p.memories.find((m:any)=>m.id===memory.id).anchor.position).toEqual(original);
  await page.getByRole('button',{name:'Place options',exact:true}).click();const exportEvent=page.waitForEvent('download');await page.getByRole('button',{name:'Export portable .zip'}).click();const exported=await exportEvent;const zipPath=test.info().outputPath('portable-room.zip');await exported.saveAs(zipPath);
  await page.getByRole('button',{name:'Back to places'}).click();await page.getByLabel('Import portable project').setInputFiles(zipPath);await loaded(page);const imported=await current(page);expect(imported.id).not.toBe(demo.id);expect(imported.memories.find((m:any)=>m.id===memory.id).anchor.position).toEqual(original);
  await sampleActivity(page);const stats=await saveReport(page,'mesh');expect(stats.textures).toBeGreaterThanOrEqual(5);expect(stats.triangles).toBeGreaterThan(0);expect(stats.gpu).toBeTruthy();expect(stats.frameTimeMedianMs).toBeGreaterThan(0);await page.locator('.memory-card').filter({hasText:'Acceptance window memory'}).click();await expect.poll(()=>page.locator('.attachment-image img').evaluate((img:HTMLImageElement)=>img.naturalWidth)).toBeGreaterThan(0);await page.screenshot({path:path.join(evidence,'memory-workflow.png')});
  expect(external).toEqual([]);expect(errors).toEqual([]);
  await fs.writeFile(path.join(evidence,'offline-observation.json'),JSON.stringify({externalRequests:external,consoleErrors:errors,method:'Browser route denies every non-loopback HTTP(S) request; complete mesh/media/save/search/archive workflow executed',os:process.platform},null,2));
});

for(const kind of ['PLY','SPZ'])test(`Gaussian ${kind} renders and picks approximate anchors with external network denied`,async({page})=>{
  const external:string[]=[],errors:string[]=[];await page.context().route('**/*',route=>{const u=new URL(route.request().url());if(['http:','https:'].includes(u.protocol)&&u.hostname!=='127.0.0.1'){external.push(u.href);return route.abort()}return route.continue()});page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text())});
  await page.goto('/');await page.getByRole('button',{name:`Try ${kind}`,exact:true}).click();await loaded(page);await expect(page.getByRole('button',{name:'Walk',exact:true})).toBeDisabled();
  await page.getByRole('button',{name:'Edit',exact:true}).click();await page.getByRole('button',{name:'Leave a memory',exact:true}).click();const b=await page.locator('.room-viewer canvas').boundingBox();if(!b)throw new Error('Missing canvas');await page.mouse.click(b.x+b.width*.6,b.y+b.height*.43);await expect(page.getByLabel('Memory title',{exact:true})).toBeVisible();await page.getByLabel('Memory title',{exact:true}).fill(`${kind} approximate memory`);await saved(page);expect((await current(page)).memories.find((m:any)=>m.title===`${kind} approximate memory`).anchor.placement).toBe('approximate');
  await page.getByRole('button',{name:'Close memory details'}).click();await page.screenshot({path:path.join(evidence,`${kind.toLowerCase()}-room.png`)});await sampleActivity(page);const stats=await saveReport(page,kind.toLowerCase());expect(stats.splats).toBe(25036);expect(external).toEqual([]);expect(errors).toEqual([]);
});

test('real file chooser imports GLB and rejects ordinary point-cloud PLY',async({page})=>{
  await page.goto('/');await page.getByText('Advanced: import a 3D scan',{exact:true}).click();await page.getByRole('button',{name:'Import GLB, PLY or SPZ',exact:true}).click();await page.getByLabel('Place name',{exact:true}).fill('Uploaded synthetic GLB');await page.locator('.file-drop input').setInputFiles(fixture('synthetic-room.glb'));await page.getByRole('button',{name:'Create place',exact:false}).click();await loaded(page);await expect(page.getByRole('heading',{name:'Uploaded synthetic GLB',exact:true}).first()).toBeVisible();await saved(page);const p=await current(page);expect(p.room.sceneType).toBe('mesh');expect(p.room.sourceUnits).toBe('unknown');
  await page.getByRole('button',{name:'Close room setup'}).click();await page.getByLabel('Import or replace room scan').setInputFiles(fixture('unsupported-pointcloud.ply'));await expect(page.getByRole('alert')).toContainText('binary little-endian');expect((await current(page)).room.scanAssetId).toBe(p.room.scanAssetId);
});

test('small screen can open a memory and return to the room',async({page})=>{
  await page.setViewportSize({width:390,height:844});await page.goto('/');await page.getByRole('button',{name:'Explore mesh'}).click();await loaded(page);await page.getByRole('button',{name:'Toggle memory panel'}).click();await page.locator('.memory-card').click();await expect(page.getByRole('heading',{name:'An afternoon at the desk',exact:true}).last()).toBeVisible();await expect.poll(()=>page.locator('.attachment-image img').evaluate((img:HTMLImageElement)=>img.naturalWidth)).toBeGreaterThan(0);await page.screenshot({path:path.join(evidence,'mobile-memory.png')});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);await page.getByRole('button',{name:'Close memory details'}).click();await expect(page.locator('.room-viewer canvas')).toBeVisible();
});

test('splat anchor can be dragged with the visible translation gizmo',async({page})=>{
  await page.goto('/');await page.getByRole('button',{name:'Try PLY',exact:true}).click();await loaded(page);
  await page.getByRole('button',{name:'Edit',exact:true}).click();await page.getByRole('button',{name:'Leave a memory',exact:true}).click();const b=await page.locator('.room-viewer canvas').boundingBox();if(!b)throw new Error('Missing canvas');await page.mouse.click(b.x+b.width*.52,b.y+b.height*.46);await page.getByLabel('Memory title',{exact:true}).fill('Gizmo anchor');await saved(page);
  const before=(await current(page)).memories.find((m:any)=>m.title==='Gizmo anchor');await expect(page.getByText('Drag an axis to adjust position or depth · approximate',{exact:true})).toBeVisible();
  const marker=page.getByRole('button',{name:'Open memory: Gizmo anchor',exact:true});await expect(marker).toBeVisible();
  const point=await marker.evaluate(el=>{const rect=el.parentElement!.getBoundingClientRect();return{x:rect.left+parseFloat((el as HTMLElement).style.left),y:rect.top+parseFloat((el as HTMLElement).style.top)}});
  await page.screenshot({path:path.join(evidence,'splat-gizmo.png')});await page.mouse.move(point.x,point.y-36);await page.mouse.down();await page.mouse.move(point.x,point.y-78,{steps:16});await page.mouse.up();await saved(page);
  const after=(await current(page)).memories.find((m:any)=>m.id===before.id);expect(after.anchor.position).not.toEqual(before.anchor.position);expect(after.anchor.placement).toBe('approximate');await page.getByRole('button',{name:'Undo',exact:true}).click();await saved(page);expect((await current(page)).memories.find((m:any)=>m.id===before.id).anchor.position).toEqual(before.anchor.position);
});

test('walking, context recovery, idle rendering and repeated room switching release resources',async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text())});await page.goto('/');await page.getByRole('button',{name:'Explore mesh'}).click();await loaded(page);await expect(page.getByRole('button',{name:'Walk',exact:true})).toBeEnabled();await page.getByRole('button',{name:'Walk',exact:true}).click();await page.locator('.room-viewer canvas').focus();await page.keyboard.down('KeyW');await page.waitForTimeout(1500);await page.keyboard.up('KeyW');await expect(page.locator('.room-viewer-message')).toHaveCount(0);await page.getByRole('button',{name:'Entrance',exact:true}).click();await page.getByRole('button',{name:'Orbit',exact:true}).click();
  await page.locator('.room-viewer canvas').evaluate((canvas:HTMLCanvasElement)=>canvas.getContext('webgl2')!.getExtension('WEBGL_lose_context')!.loseContext());await expect(page.getByRole('alert')).toContainText('Graphics context lost');await page.getByRole('button',{name:'Restore room',exact:true}).click();await loaded(page);
  const beforeIdle=await saveReport(page,'idle-before');await page.waitForTimeout(1000);const afterIdle=await saveReport(page,'idle-after');expect(afterIdle.frames-beforeIdle.frames).toBeLessThanOrEqual(4);
  const readings=[];for(let i=0;i<4;i++){const gl=await page.locator('.room-viewer canvas').evaluateHandle((canvas:HTMLCanvasElement)=>canvas.getContext('webgl2')!);const report=await saveReport(page,`switch-${i}`);readings.push({geometries:report.geometries,textures:report.textures});await page.getByRole('button',{name:'Back to places'}).click();expect(await gl.evaluate(gl=>gl.isContextLost())).toBe(true);await gl.dispose();await page.getByRole('button',{name:'Open The afternoon room',exact:true}).first().click();await loaded(page)}
  expect(readings.every(r=>r.geometries===readings[0].geometries&&r.textures===readings[0].textures)).toBe(true);expect(errors).toEqual([]);await fs.writeFile(path.join(evidence,'resource-observation.json'),JSON.stringify({switches:4,oldContextsLost:true,rendererCounts:readings,idleFrames:afterIdle.frames-beforeIdle.frames,contextLossRestored:true,consoleErrors:errors},null,2));
});

test('rendering quality immediately changes pixel density on a high-DPI viewport',async({browser})=>{
  const context=await browser.newContext({baseURL:'http://127.0.0.1:8850',viewport:{width:1440,height:900},deviceScaleFactor:2});const page=await context.newPage();page.setDefaultTimeout(12000);try{await page.goto('/');await page.getByRole('button',{name:'Explore mesh'}).click();await loaded(page);await page.getByRole('button',{name:'Edit',exact:true}).click();await page.getByRole('button',{name:'Room setup',exact:true}).click();
  const ratio=()=>page.locator('.room-viewer canvas').evaluate((canvas:HTMLCanvasElement)=>canvas.width/canvas.getBoundingClientRect().width);await expect.poll(ratio).toBeCloseTo(1.5,1);await page.getByLabel('Rendering quality').selectOption('high');await expect.poll(ratio).toBeCloseTo(2,1);await saved(page);
  }finally{await context.close()}
});

test('OpenRouter defaults to text-only Ling and requires explicit selection before suggestions',async({page})=>{
  const requests:any[]=[];
  await page.route('**/api/projects/*/ai/suggest',async route=>{requests.push(route.request().postDataJSON());await route.fulfill({json:{tags:['afternoon'],caption:''}})});
  await page.goto('/');await page.getByRole('button',{name:'Explore mesh'}).click();await loaded(page);await page.getByRole('button',{name:'Edit',exact:true}).click();await page.locator('.memory-card').click();await page.getByText('Optional AI suggestions',{exact:true}).click();
  await expect(page.getByLabel('OpenRouter model',{exact:true})).toHaveValue('inclusionai/ling-3.1-flash');await expect(page.getByText('Ling 3.1 Flash accepts text only.',{exact:false})).toBeVisible();await expect(page.locator('.ai-photo-choice')).toHaveCount(0);
  await page.getByLabel('OpenRouter API key',{exact:true}).fill('local-test-key');await page.getByRole('button',{name:'Use key for this session',exact:true}).click();const send=page.getByRole('button',{name:'Send selected content to OpenRouter',exact:true});await expect(send).toBeDisabled();expect(requests).toHaveLength(0);
  await page.getByLabel('Include this memory’s note',{exact:true}).check();await send.click();await expect(page.getByRole('region',{name:'Review AI suggestions'})).toBeVisible();expect(requests).toHaveLength(1);expect(requests[0].model).toBe('inclusionai/ling-3.1-flash');expect(requests[0].imageIds).toEqual([]);expect(requests[0].includeNote).toBe(true);
  await page.getByRole('button',{name:'Discard suggestions',exact:true}).click();await page.getByLabel('OpenRouter model',{exact:true}).selectOption('google/gemma-4-31b-it:free');await expect(page.locator('.ai-photo-choice')).toHaveCount(1);await page.getByRole('button',{name:'Forget session key',exact:true}).click();await expect(send).toBeDisabled();await page.getByLabel('OpenRouter model',{exact:true}).selectOption('inclusionai/ling-3.1-flash');await page.screenshot({path:path.join(evidence,'openrouter-settings.png')});
});
