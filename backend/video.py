"""Durable, cancellable local video extraction with atomic frame acceptance."""
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel,ConfigDict,Field
from .models import now,uid
from .reconstruction import ROOT,safe_message

ACTIVE=('queued','running')
MAX_SECONDS=600

class StartVideo(BaseModel):
    model_config=ConfigDict(extra='forbid')
    assetId:str=Field(min_length=36,max_length=36)
    targetFrames:int=Field(default=80,ge=30,le=160,strict=True)


def worker_command(directory):return [sys.executable,'-u','-m','backend.video_worker',str(directory)]

class VideoManager:
    # Construct only after ReconstructionManager acquired the data-root ownership lock.
    def __init__(self,store):
        self.store=store;self.lock=threading.RLock();self.closed=False;self.processes={};self.threads={}
        self.directory=store.root/'video-extractions';self.directory.mkdir(exist_ok=True)
        with store.connect() as db:
            db.executescript("CREATE TABLE IF NOT EXISTS video_jobs(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,status TEXT NOT NULL,data TEXT NOT NULL); CREATE UNIQUE INDEX IF NOT EXISTS one_active_video ON video_jobs((1)) WHERE status IN ('queued','running');")
            for row in db.execute("SELECT data FROM video_jobs WHERE status IN ('queued','running')").fetchall():
                j=json.loads(row['data']);j.update(status='interrupted',message='Frame extraction was interrupted. The original video is kept; retry when ready.',updatedAt=now());self.write(db,j)
        # Root ownership excludes another live supervisor. Remove both unfinished and
        # terminal staging left by a crash between commit and normal cleanup.
        for directory in self.directory.iterdir():
            if directory.is_dir() and not directory.is_symlink():
                shutil.rmtree(directory/'frames',ignore_errors=True)
    def write(self,db,j):db.execute('INSERT OR REPLACE INTO video_jobs VALUES(?,?,?,?)',(j['id'],j['projectId'],j['status'],json.dumps(j)))
    def get(self,pid,jid):
        self.store.get(pid)
        with self.store.connect() as db:row=db.execute('SELECT data FROM video_jobs WHERE id=? AND project_id=?',(jid,pid)).fetchone()
        if not row:raise HTTPException(404,'Video extraction not found')
        return json.loads(row['data'])
    def list(self,pid):
        self.store.get(pid)
        with self.store.connect() as db:return [json.loads(r['data']) for r in db.execute("SELECT data FROM video_jobs WHERE project_id=? ORDER BY json_extract(data,'$.createdAt') DESC",(pid,))]
    def update(self,j,**values):
        with self.lock,self.store.connect() as db:
            current=self.get(j['projectId'],j['id'])
            if current['status'] not in ACTIVE:return current
            current.update(values,updatedAt=now());self.write(db,current);return current
    def start(self,pid,body):
        with self.lock:
            if self.closed:raise HTTPException(503,'Video service is shutting down')
            p=self.store.get(pid)
            if p.deletedAt:raise HTTPException(409,'Restore this project before extracting frames')
            a=self.store.asset(pid,body.assetId)
            if a.kind!='video' or a.role!='media':raise HTTPException(422,'Choose a retained video from this project')
            path=self.store.asset_path(pid,a)
            if not path.is_file() or path.stat().st_size!=a.size or self.store.digest(path)!=a.sha256:raise HTTPException(409,'Retained video is missing or changed; relink the original')
            if len([x for x in p.assets if x.role=='capture'])>=200:raise HTTPException(422,'This place already contains 200 retained photos. Start another place for the video.')
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute("SELECT 1 FROM video_jobs WHERE status IN ('queued','running')").fetchone():raise HTTPException(409,'Another video is being processed. Wait or cancel it first.')
                stamp=now();j={'id':uid(),'projectId':pid,'sourceAssetId':a.id,'status':'queued','message':'Preparing local video.','createdAt':stamp,'updatedAt':stamp,'targetFrames':body.targetFrames,'frameAssetIds':[],'frameCount':0,'warnings':[],'error':None}
                directory=self.directory/j['id'];directory.mkdir()
                self.store.atomic_file(directory/'input.json',json.dumps({'source':str(path),'sha256':a.sha256,'targetFrames':body.targetFrames}).encode());self.write(db,j)
            thread=threading.Thread(target=self.run,args=(j,a,directory),daemon=True,name='stillroom-video');self.threads[j['id']]=thread;thread.start();return j
    def run(self,j,source,directory):
        process=None
        try:
            with self.lock:
                if self.closed or self.get(j['projectId'],j['id'])['status']!='queued':return
                process=subprocess.Popen(worker_command(directory),cwd=ROOT,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                self.processes[j['id']]=process;self.update(j,status='running')
            deadline=time.monotonic()+MAX_SECONDS;previous=None
            while process.poll() is None:
                if self.closed or self.get(j['projectId'],j['id'])['status'] not in ACTIVE:return
                if time.monotonic()>deadline:raise ValueError('Video extraction exceeded ten minutes. Use a shorter clip.')
                progress=directory/'progress.json'
                if progress.is_file() and progress.stat().st_size<4096:
                    try:
                        message=safe_message(json.loads(progress.read_text()).get('message'))
                        if message!=previous:self.update(j,message=message);previous=message
                    except (OSError,ValueError):pass
                time.sleep(.15)
            result=directory/'result.json'
            if not result.is_file() or result.stat().st_size>128*1024:raise ValueError('The video decoder stopped without a valid result. Try exporting a standard MP4.')
            value=json.loads(result.read_text())
            if process.returncode!=0 or value.get('error'):raise ValueError(safe_message(value.get('error'),'The video decoder could not read this clip.'))
            items=value.get('frames')
            if not isinstance(items,list) or not 1<=len(items)<=j['targetFrames']:raise ValueError('The video decoder returned an invalid frame collection.')
            frames=[];total=0
            for item in items:
                name=item.get('file');path=(directory/'frames'/str(name)).resolve()
                if path.parent!=(directory/'frames').resolve() or not path.is_file():raise ValueError('A derived video frame is missing.')
                total+=path.stat().st_size
                if total>200*1024*1024:raise ValueError('Extracted frames exceed the local size limit.')
                stamp=item.get('timestampSeconds');score=item.get('sharpness')
                frames.append((f'video-{source.id[:8]}-{stamp:08.3f}s.jpg',path.read_bytes(),{'sourceAssetId':source.id,'sourceSha256':source.sha256,'timestampSeconds':stamp,'sharpness':score}))
            with self.lock:
                if self.closed or self.get(j['projectId'],j['id'])['status'] not in ACTIVE:return
                ids=self.store.add_video_frames(j['projectId'],source.id,frames)
                warnings=value.get('warnings',[])
                self.update(j,status='ready',message=f'{len(ids)} frames added. Review them, then create your room.',frameAssetIds=ids,frameCount=len(ids),warnings=[safe_message(v) for v in warnings[:10]])
        except Exception as exc:
            message=exc.detail if isinstance(exc,HTTPException) else str(exc) if isinstance(exc,ValueError) else 'Frame extraction failed. Your original video is retained; retry or export a standard MP4.'
            self.update(j,status='failed',message='Frame extraction needs attention.',error=safe_message(message))
        finally:
            if process and process.poll() is None:process.kill();process.wait(timeout=5)
            with self.lock:self.processes.pop(j['id'],None);self.threads.pop(j['id'],None)
            shutil.rmtree(directory/'frames',ignore_errors=True)
    def cancel(self,pid,jid):
        with self.lock:
            j=self.get(pid,jid)
            return self.update(j,status='cancelled',message='Extraction cancelled. The original video is retained.')
    def close(self):
        with self.lock:
            self.closed=True
            for jid in list(self.processes):
                with self.store.connect() as db:row=db.execute('SELECT data FROM video_jobs WHERE id=?',(jid,)).fetchone()
                if row:self.update(json.loads(row['data']),status='interrupted',message='Extraction interrupted. The original video is retained.')
            threads=list(self.threads.values())
        for thread in threads:thread.join(timeout=6)


def install_video(app,store):
    manager=VideoManager(store);app.state.video=manager
    @app.post('/api/projects/{pid}/video-extractions')
    def start(pid:str,body:StartVideo):return manager.start(pid,body)
    @app.get('/api/projects/{pid}/video-extractions')
    def jobs(pid:str):return manager.list(pid)
    @app.get('/api/projects/{pid}/video-extractions/{jid}')
    def get(pid:str,jid:str):return manager.get(pid,jid)
    @app.post('/api/projects/{pid}/video-extractions/{jid}/cancel')
    def cancel(pid:str,jid:str):return manager.cancel(pid,jid)
    return manager
