"""Bounded offline video decoding. No camera truth or network input is accepted."""
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import threading
import time

import av
from PIL import Image, ImageChops, ImageFilter, ImageStat

MAX_SECONDS=300
MAX_PIXELS=4096*2160
MAX_FRAMES=36000
MAX_OUTPUT=200*1024*1024


def save_json(path,value):
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(value,allow_nan=False));temporary.replace(path)


def extract(source,output,target,progress=lambda value:None):
    output.mkdir(parents=True,exist_ok=True)
    candidates=[];best=None;bucket=-1;sample_at=-1.;first_time=None;last_time=0.;decoded=0;written=0
    def keep():
        nonlocal written
        if best is None:return
        image,stamp,score,signature=best
        path=output/f'frame-{len(candidates):04d}.jpg';image.save(path,'JPEG',quality=94)
        written+=path.stat().st_size
        if written>MAX_OUTPUT:raise ValueError('Extracted frames exceed the local size limit. Use a shorter video.')
        candidates.append({'file':path.name,'timestampSeconds':round(stamp,6),'sharpness':round(score,4),'signature':signature})
    # A file object and a strict protocol allowlist prevent external references in media.
    with source.open('rb') as handle,av.open(handle,options={'protocol_whitelist':'file','threads':'2'}) as container:
        if not container.streams.video:raise ValueError('This file has no decodable video track.')
        stream=container.streams.video[0];stream.thread_type='SLICE';stream.codec_context.thread_count=2
        duration=float(stream.duration*stream.time_base) if stream.duration is not None and stream.time_base else float(container.duration/av.time_base) if container.duration else None
        if duration is not None and (not math.isfinite(duration) or duration<=0 or duration>MAX_SECONDS+.1):raise ValueError('Use a video no longer than five minutes.')
        width,height=stream.codec_context.width,stream.codec_context.height
        if not 0<width*height<=MAX_PIXELS or max(width,height)>4096:raise ValueError('Use a video at 4K resolution or below.')
        interval=max(.125,duration/target) if duration else 1.
        for frame in container.decode(stream):
            decoded+=1
            if decoded>MAX_FRAMES:raise ValueError('Too many decoded frames. Use a shorter video at 30 fps.')
            if not 0<frame.width*frame.height<=MAX_PIXELS or max(frame.width,frame.height)>4096:raise ValueError('Video resolution changed beyond the 4K limit.')
            if frame.time is None or not math.isfinite(frame.time):raise ValueError('Video timestamps are missing. Export it as a normal MP4 and retry.')
            if first_time is None:first_time=frame.time
            stamp=frame.time-first_time
            if stamp<last_time-.001:raise ValueError('Video timestamps are out of order. Export as MP4 and retry.')
            last_time=stamp
            if stamp>MAX_SECONDS+.1:raise ValueError('Use a video no longer than five minutes.')
            if stamp+1e-6<sample_at:continue
            sample_at=stamp+max(.125,interval/3)
            this_bucket=int(stamp/interval)
            if this_bucket!=bucket:
                keep();best=None;bucket=this_bucket
                progress({'message':f'Choosing clear frames · {stamp:.0f} seconds decoded','decodedSeconds':round(stamp,2)})
            scale=min(1,1600/max(frame.width,frame.height));w=max(1,round(frame.width*scale));h=max(1,round(frame.height*scale))
            im=frame.reformat(width=w,height=h,format='rgb24').to_image()
            # PyAV returns DISPLAYMATRIX's counterclockwise angle; Pillow uses the same convention.
            rotation=frame.rotation
            if rotation:im=im.rotate(rotation,expand=True)
            gray=im.convert('L').resize((160,120),Image.Resampling.BILINEAR)
            edges=gray.filter(ImageFilter.FIND_EDGES).crop((1,1,159,119))
            score=ImageStat.Stat(edges).var[0]
            signature=gray.resize((32,24),Image.Resampling.BILINEAR)
            if best is None or score>best[2]:best=(im,stamp,score,signature)
        keep()
    if not candidates:raise ValueError('No readable frames were found in this video.')
    # Unknown-duration clips are collected at one-second intervals and then spread evenly.
    if len(candidates)>target:
        indices={round(i*(len(candidates)-1)/(target-1)) for i in range(target)}
        candidates=[entry for i,entry in enumerate(candidates) if i in indices]
    selected=[];previous=None;duplicates=0
    for entry in candidates:
        signature=entry.pop('signature')
        if previous is not None and ImageStat.Stat(ImageChops.difference(previous,signature)).mean[0]<1.5:
            duplicates+=1;continue
        previous=signature;selected.append(entry)
    keep_names={x['file'] for x in selected}
    for path in output.glob('frame-*.jpg'):
        if path.name not in keep_names:path.unlink()
    warnings=[]
    if duplicates:warnings.append(f'Skipped {duplicates} almost-identical views. Moving the camera through the room supplies depth information.')
    if len(selected)<20:warnings.append('Few distinct views were found. A room usually needs many overlapping viewpoints; record a slow walk rather than a stationary pan.')
    if sum(x['sharpness']<20 for x in selected)>len(selected)//3:warnings.append('Many frames have little sharp detail. Use brighter light, move slowly, and review the frames before reconstructing.')
    return {'frames':selected,'durationSeconds':round(last_time,6),'decodedFrames':decoded,'warnings':warnings,'decoderVersion':av.__version__}


def watch_parent(parent):
    if parent<=1:os._exit(1)
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.OpenProcess(0x00100000,False,parent)
        if not handle:os._exit(1)
        try:
            while True:
                if kernel.WaitForSingleObject(handle,500)!=258:os._exit(1)
        finally:kernel.CloseHandle(handle)
    while True:
        if os.getppid()!=parent:os._exit(1)
        time.sleep(.5)


def main(directory):
    threading.Thread(target=watch_parent,args=(os.getppid(),),daemon=True).start()
    spec=json.loads((directory/'input.json').read_text());source=Path(spec['source'])
    try:
        if hashlib.sha256(source.read_bytes()).hexdigest()!=spec['sha256']:raise ValueError('The retained source video changed. Relink the original and retry.')
        result=extract(source,directory/'frames',spec['targetFrames'],lambda value:save_json(directory/'progress.json',value))
        save_json(directory/'result.json',result)
    except Exception as exc:
        message=str(exc) if isinstance(exc,ValueError) else 'This video could not be decoded. Export a standard H.264 MP4 from your camera app and retry.'
        save_json(directory/'result.json',{'error':message});return 1
    return 0

if __name__=='__main__':sys.exit(main(Path(sys.argv[1])))
