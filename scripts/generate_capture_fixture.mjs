// CC0 synthetic multiview photographs for an actual reconstruction test.
// The worker receives ONLY the JPEGs. Generator geometry and camera truth are not inputs.
import { chromium } from '../frontend/node_modules/playwright/index.mjs';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const output = path.resolve(process.argv[2] || path.join(root, 'fixtures', 'capture-room'));
const count = 54;
const html = `<!doctype html><html><head><meta charset="utf-8"><title>Synthetic capture fixture</title>
<style>body{margin:0}canvas{display:block}</style>
<script type="importmap">{"imports":{"three":"/three/build/three.module.js","three/addons/":"/three/examples/jsm/"}}</script></head><body>
<script type="module">
import * as THREE from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
const renderer = new THREE.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});
renderer.setSize(960,720); renderer.setPixelRatio(1);
renderer.outputColorSpace=THREE.SRGBColorSpace;
renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.12;
document.body.append(renderer.domElement);
const scene=new THREE.Scene();scene.background=new THREE.Color('#aaa99e');
scene.add(new THREE.HemisphereLight('#fff7e9','#a7a6a0',2.1));
const light=new THREE.DirectionalLight('#fff5df',2.2);light.position.set(-2,5,4);scene.add(light);
const room=(await new GLTFLoader().loadAsync('/room.glb')).scene;scene.add(room);
let seed=1847;
function random(){seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed/4294967296}
let meshIndex=0;
room.traverse(mesh=>{
  if(!mesh.isMesh)return;
  const source=mesh.material;
  if(source.name.includes('artwork')||source.name.includes('landmark'))return;
  const material=source.clone(), canvas=document.createElement('canvas');canvas.width=512;canvas.height=512;
  const ctx=canvas.getContext('2d');
  // Fixed multiscale surface detail supplies trackable features. It stays attached to
  // the surface between views; this intentionally makes a favourable synthetic case.
  const color=source.color.clone().convertLinearToSRGB();
  let rgb=[color.r,color.g,color.b].map(v=>Math.round(v*255));
  if(source.map)rgb=source.name.includes('Oak')?[161,119,81]:source.name.includes('plaster')?[207,201,184]:[174,152,119];
  ctx.fillStyle='rgb('+rgb.join(',')+')';ctx.fillRect(0,0,512,512);
  for(let i=0;i<3600;i++){
    const k=0.55+random()*.8;ctx.fillStyle='rgb('+rgb.map(v=>Math.min(255,Math.round(v*k))).join(',')+')';
    const size=1+random()*5;ctx.fillRect(random()*512,random()*512,size,size);
  }
  for(let i=0;i<65;i++){
    const x=random()*490,y=random()*490,w=3+random()*22,h=3+random()*14;
    ctx.strokeStyle='rgba(25,35,28,'+(.2+random()*.45)+')';ctx.lineWidth=1+random()*2;ctx.strokeRect(x,y,w,h);
  }
  ctx.font='bold 22px sans-serif';ctx.fillStyle='rgba(30,44,35,.6)';
  ctx.fillText('SYNTHETIC '+String(++meshIndex).padStart(2,'0'),20,35);
  const texture=new THREE.CanvasTexture(canvas);texture.colorSpace=THREE.SRGBColorSpace;
  texture.anisotropy=4;material.map=texture;material.color.set('#ffffff');material.metalness=0;material.roughness=.88;mesh.material=material;
});
const camera=new THREE.PerspectiveCamera(69,960/720,.05,100);
window.renderCapture=index=>{
  const row=Math.floor(index/18),t=(index%18)/17,angle=(t-.5)*1.45;
  camera.position.set(3.35*Math.sin(angle),1.1+row*.56,1.0+2.25*Math.cos(angle));
  camera.lookAt(0,1.25,-.9);camera.updateMatrixWorld();
  renderer.render(scene,camera);
  return {jpeg:renderer.domElement.toDataURL('image/jpeg',.94).split(',')[1],position:camera.position.toArray(),quaternion:camera.quaternion.toArray(),fov:camera.fov};
};
window.fixtureReady=true;
</script></body></html>`;

const threeRoot = path.join(root, 'frontend', 'node_modules', 'three');
const server = http.createServer(async (request, response) => {
  try {
    const url = new URL(request.url, 'http://127.0.0.1');
    if (url.pathname === '/') { response.setHeader('Content-Type', 'text/html'); response.end(html); return; }
    if (url.pathname === '/room.glb') { response.setHeader('Content-Type', 'model/gltf-binary'); response.end(await fs.readFile(path.join(root, 'fixtures', 'synthetic-room.glb'))); return; }
    if (url.pathname.startsWith('/three/')) {
      const target = path.resolve(threeRoot, '.' + url.pathname.slice('/three'.length));
      if (!target.startsWith(threeRoot + path.sep)) throw new Error('Invalid path');
      response.setHeader('Content-Type', 'text/javascript'); response.end(await fs.readFile(target)); return;
    }
    response.statusCode = 404; response.end();
  } catch { response.statusCode = 500; response.end('Fixture resource error'); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const browser = await chromium.launch({headless:true,...(process.env.STILLROOM_BROWSER_CHANNEL ? {channel:process.env.STILLROOM_BROWSER_CHANNEL} : {})});
try {
  await fs.mkdir(path.join(output,'images'),{recursive:true});
  const page=await browser.newPage({viewport:{width:960,height:720}});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('http://127.0.0.1:'+server.address().port);
  await page.waitForFunction(()=>window.fixtureReady===true);
  const views=[];
  for(let index=0;index<count;index++){
    const view=await page.evaluate(index=>window.renderCapture(index),index);
    const bytes=Buffer.from(view.jpeg,'base64'),filename='view_'+String(index+1).padStart(3,'0')+'.jpg';
    await fs.writeFile(path.join(output,'images',filename),bytes);
    views.push({filename,sha256:createHash('sha256').update(bytes).digest('hex'),bytes:bytes.length,position:view.position,quaternion:view.quaternion,fov:view.fov});
  }
  if(errors.length)throw new Error(errors.join('\n'));
  await fs.writeFile(path.join(output,'ground-truth.json'),JSON.stringify({synthetic:true,licence:'CC0-1.0',source:'scripts/generate_capture_fixture.mjs',description:'Rendered views of a synthetic textured room fragment. Favourable fixed lighting and deliberately rich surface detail; not evidence of real-room capture accuracy. Camera truth must not be given to the reconstruction pipeline.',imageSize:[960,720],views},null,2)+'\n');
  await fs.writeFile(path.join(output,'README.md'),'# Synthetic multi-view reconstruction fixture\n\nCC0-1.0, generated by `scripts/generate_capture_fixture.mjs`. These are rendered photographs of an open-front synthetic room, with deliberately rich fixed surface detail to make a favourable reconstruction case. They are not images of a real place.\n\nGive only the `images/` files to Stillroom. The reconstruction pipeline must estimate cameras from those images; `ground-truth.json` and the source geometry are verification material and must not be inputs. The original geometry is never a substitute for a reconstructed result.\n\nRegenerate with `STILLROOM_BROWSER_CHANNEL=chrome node scripts/generate_capture_fixture.mjs`. Requires the existing frontend dependencies and installed Chrome. Rendering may vary slightly across browser/GPU versions.\n');
  console.log(JSON.stringify({output,images:count,bytes:views.reduce((sum,v)=>sum+v.bytes,0),consoleErrors:errors}));
} finally {
  await browser.close();await new Promise(resolve=>server.close(resolve));
}
