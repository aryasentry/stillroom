import {test,expect} from '@playwright/test';
import path from 'node:path';
import fs from 'node:fs/promises';

const fixture=(name:string)=>path.resolve('..','fixtures',name);

test('a local room video becomes reviewable frames without accepting a room',async({page})=>{
  const name=`Video frame review ${Date.now()}`;
  await page.goto('/');await page.getByRole('button',{name:'Create a room from photos',exact:true}).click();
  await page.getByLabel('Place name',{exact:true}).fill(name);
  await expect(page.getByLabel('Target video frames')).toHaveValue('120');
  await page.getByLabel('Choose room video',{exact:true}).setInputFiles(fixture('memory-video.webm'));
  await page.getByRole('button',{name:'Extract frames from video',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Video frames ready',exact:true})).toBeVisible({timeout:60000});
  await expect(page.locator('.video-timestamp').first()).toBeVisible();
  const summaries=await(await page.request.get('/api/projects')).json();
  const project=await(await page.request.get(`/api/projects/${summaries.find((p:any)=>p.title===name).id}`)).json();
  const jobs=await(await page.request.get(`/api/projects/${project.id}/video-extractions`)).json();
  expect(project.room.scanAssetId).toBeNull();expect(jobs[0].frameCount).toBeGreaterThan(0);
  expect(project.assets.some((a:any)=>a.id===jobs[0].sourceAssetId&&a.kind==='video'&&a.role==='media')).toBe(true);
  expect(project.captureAssetIds).toEqual(jobs[0].frameAssetIds);
  await fs.mkdir(path.resolve('..','docs','evidence'),{recursive:true});
  await page.screenshot({path:path.resolve('..','docs','evidence','video-capture.png'),fullPage:true});
  await page.getByRole('button',{name:'Back to places',exact:true}).click();await page.reload();
  await page.locator('.project-card').filter({hasText:name}).getByRole('button',{name:'Review video frames',exact:false}).click();
  await expect(page.getByRole('heading',{name:'Video frames ready',exact:true})).toBeVisible();
  await expect(page.locator('.capture-grid figure')).toHaveCount(jobs[0].frameCount);
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:path.resolve('..','docs','evidence','video-capture-mobile.png'),fullPage:true});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('saved extraction resumes, cancels, retries retained source, and keeps pending photos',async({page})=>{
  // Real project/upload; job timing is controlled so cancellation and reopening are deterministic.
  const project=await(await page.request.post('/api/projects',{headers:{'X-Stillroom-Request':'local'},data:{title:`Video recovery ${Date.now()}`}})).json();
  const fs=await import('node:fs/promises');
  const source=await(await page.request.post(`/api/projects/${project.id}/assets`,{headers:{'X-Stillroom-Request':'local'},multipart:{role:'media',file:{name:'room.webm',mimeType:'video/webm',buffer:await fs.readFile(fixture('memory-video.webm'))}}})).json();
  let job={id:'controlled-extraction',projectId:project.id,sourceAssetId:source.id,status:'running',message:'Selecting sharp frames locally…',createdAt:new Date().toISOString(),updatedAt:new Date().toISOString(),frameAssetIds:[],frameCount:0,warnings:[],error:null};
  let starts=0;
  await page.route(`**/api/projects/${project.id}/video-extractions**`,async route=>{
    const request=route.request();
    if(request.url().endsWith('/cancel'))job={...job,status:'cancelled',message:'Extraction cancelled. Source video kept.'};
    else if(request.method()==='POST'){expect(request.postDataJSON()).toEqual({assetId:source.id,targetFrames:40});starts++;job={...job,status:'running'};}
    await route.fulfill({json:request.method()==='GET'&&request.url().endsWith('/video-extractions')?[job]:job});
  });
  await page.goto('/');await page.locator('.project-card').filter({hasText:project.title}).getByRole('button',{name:'Extracting video frames',exact:false}).click();
  await expect(page.getByRole('button',{name:'Cancel frame extraction',exact:true})).toBeVisible();
  await expect(page.getByLabel('Choose room photos',{exact:true})).toBeDisabled();
  await page.getByRole('button',{name:'Back to places',exact:true}).click();
  await page.locator('.project-card').filter({hasText:project.title}).getByRole('button',{name:'Extracting video frames',exact:false}).click();
  await page.getByRole('button',{name:'Cancel frame extraction',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Video extraction cancelled',exact:true})).toBeVisible();
  await page.getByLabel('Choose room photos',{exact:true}).setInputFiles(fixture('capture-insufficient/gray.png'));
  await page.getByLabel('Target video frames').selectOption('40');
  await page.getByRole('button',{name:'Retry frame extraction',exact:true}).click();
  await expect(page.locator('.pending-label')).toHaveCount(1);expect(starts).toBe(1);
  await expect(page.getByRole('button',{name:'Copy 1 photos to this place',exact:false})).toBeDisabled();
  const saved=await(await page.request.get(`/api/projects/${project.id}`)).json();
  expect(saved.assets.some((a:any)=>a.id===source.id)).toBe(true);expect(saved.room.scanAssetId).toBeNull();
});

test('stopping a video upload never claims frames were copied or starts extraction',async({page})=>{
  let extractions=0;
  await page.route('**/api/projects/*/assets',async route=>{await new Promise(resolve=>setTimeout(resolve,1200));await route.abort().catch(()=>{});});
  await page.route('**/api/projects/*/video-extractions',async route=>{if(route.request().method()==='POST')extractions++;await route.continue();});
  await page.goto('/');await page.getByRole('button',{name:'Create a room from photos',exact:true}).click();
  await page.getByLabel('Choose room video',{exact:true}).setInputFiles(fixture('memory-video.webm'));
  await page.getByRole('button',{name:'Extract frames from video',exact:true}).click();
  await page.getByRole('button',{name:'Cancel video upload',exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('Video upload stopped. No frame extraction was started.');
  expect(extractions).toBe(0);await expect(page.locator('.capture-grid figure')).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Extract frames from video',exact:true})).toBeEnabled();
});

test('unreadable video keeps the source and exposes an extraction retry',async({page})=>{
  const name=`Unreadable video ${Date.now()}`;
  await page.goto('/');await page.getByRole('button',{name:'Create a room from photos',exact:true}).click();
  await page.getByLabel('Place name',{exact:true}).fill(name);
  await page.getByLabel('Choose room video',{exact:true}).setInputFiles({name:'broken.mp4',mimeType:'video/mp4',buffer:Buffer.from('0000ftypisom000000000000mdatbroken')});
  await page.getByRole('button',{name:'Extract frames from video',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Video extraction needs attention',exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Retry frame extraction',exact:true})).toBeEnabled();
  const summaries=await(await page.request.get('/api/projects')).json();
  const project=await(await page.request.get(`/api/projects/${summaries.find((p:any)=>p.title===name).id}`)).json();
  expect(project.assets.some((a:any)=>a.originalName==='broken.mp4'&&a.role==='media')).toBe(true);
  expect(project.captureAssetIds).toEqual([]);expect(project.room.scanAssetId).toBeNull();
});
