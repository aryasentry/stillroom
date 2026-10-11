// Reproducible synthetic content; MediaRecorder container timing can vary.
import {chromium} from '../frontend/node_modules/playwright/index.mjs';
import fs from 'node:fs/promises';
const browser=await chromium.launch({headless:true,...(process.env.STILLROOM_BROWSER_CHANNEL?{channel:process.env.STILLROOM_BROWSER_CHANNEL}:{})});
try {
  const page=await browser.newPage();
  const bytes=await page.evaluate(async()=>{
    const canvas=document.createElement('canvas');canvas.width=320;canvas.height=180;
    const context=canvas.getContext('2d');if(!context)throw new Error('Canvas unavailable');
    const stream=canvas.captureStream(12);const parts=[];
    const recorder=new MediaRecorder(stream,{mimeType:'video/webm;codecs=vp8',videoBitsPerSecond:160000});
    const done=new Promise(resolve=>{recorder.onstop=resolve});recorder.ondataavailable=event=>parts.push(event.data);recorder.start();
    for(let frame=0;frame<24;frame++){
      context.fillStyle='#e9e4d4';context.fillRect(0,0,320,180);context.fillStyle='#4d624b';context.font='18px sans-serif';context.fillText('STILLROOM · SYNTHETIC',34,57);context.font='13px sans-serif';context.fillText('Local video playback fixture',65,83);context.fillStyle='#b77555';context.fillRect(35,117,250*frame/23,7);await new Promise(resolve=>setTimeout(resolve,1000/12));
    }
    recorder.stop();await done;stream.getTracks().forEach(track=>track.stop());return Array.from(new Uint8Array(await new Blob(parts,{type:recorder.mimeType}).arrayBuffer()));
  });
  const target=new URL('../fixtures/memory-video.webm',import.meta.url);await fs.writeFile(target,Buffer.from(bytes));console.log(`Wrote synthetic VP8 WebM (${bytes.length} bytes)`);
} finally {await browser.close()}
