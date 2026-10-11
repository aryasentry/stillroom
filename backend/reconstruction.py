"""Durable local reconstruction supervision. A worker exit is never proof of a scan."""
import json
import os
import platform
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from .formats import MAX_ASSET, InvalidAsset, require, validate_asset, validate_reconstruction_provenance, validate_video_frame
from .models import Room, Transform, Viewpoint, now, uid

ROOT=Path(__file__).resolve().parent.parent
ACTIVE=('queued','running')
MAX_LINE=65536  # Includes JSON and newline; accommodates 200-photo completion metadata.
MAX_LOG=65536
MAX_JOB_SECONDS=2*60*60
_install_lock=threading.Lock()
_installing=False
_install_error=None


def runtime_status():
    try:
        from .reconstruction_runtime import runtime_status as status
        return status()
    except ImportError:
        return {'available':False,'installing':False,'supported':False,'platform':platform.system(),'message':'Local reconstruction runtime is not installed. Run the repository setup.','error':None,'versions':{'pycolmap':None,'brush':None}}


def status():
    result=runtime_status()
    if _installing:result={**result,'installing':True,'message':'Installing the local reconstruction tools…'}
    if _install_error:result={**result,'error':_install_error}
    return result


def begin_install():
    global _installing,_install_error
    with _install_lock:
        current=runtime_status()
        if _installing or current['available']:return status()
        if not current['supported']:raise HTTPException(422,current['message'])
        _installing=True;_install_error=None
        def run():
            global _installing,_install_error
            try:
                from .reconstruction_runtime import install_runtime
                install_runtime()
            except Exception:
                _install_error='Local runtime setup failed. Check the setup log and retry the repository setup command.'
            finally:_installing=False
        threading.Thread(target=run,name='stillroom-runtime-install',daemon=True).start()
    return status()


def worker_command(directory):
    return [sys.executable,'-u','-m','backend.reconstruction_worker',str(directory)]


def safe_message(value,default='Processing the local capture.'):
    if not isinstance(value,str):return default
    value=' '.join(value.split())[:600]
    # Protocol text is explanatory UI copy; keep native paths and URLs out of it.
    if re.search(r'(?:https?://|(?:^|\s)/|[A-Za-z]:[\\/])',value):return default
    return value or default


class ReconstructionManager:
    def __init__(self,store):
        self.store=store;self.lock=threading.RLock();self.processes={};self.threads={};self.closed=False
        # Recovery is permitted only to the exclusive live owner of this root.
        # Keep the lock inode permanently; unlinking it could create two owners.
        self._owner=(store.root/'reconstruction.lock').open('a+b')
        try:
            if os.name=='nt':
                import msvcrt
                if self._owner.seek(0,os.SEEK_END)==0:self._owner.write(b'0');self._owner.flush()
                self._owner.seek(0);msvcrt.locking(self._owner.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self._owner.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as exc:
            self._owner.close();self._owner=None
            raise RuntimeError('Another Stillroom instance already owns this data folder. Close it before starting another server.') from exc
        try:
            self.directory=store.root/'reconstructions';self.directory.mkdir(exist_ok=True)
            with store.connect() as db:
                db.executescript('''CREATE TABLE IF NOT EXISTS reconstruction_jobs(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,status TEXT NOT NULL,data TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_reconstruction ON reconstruction_jobs((1)) WHERE status IN ('queued','running');''')
                for row in db.execute("SELECT data FROM reconstruction_jobs WHERE status IN ('queued','running')").fetchall():
                    job=json.loads(row['data']);job.update(status='interrupted',message='Reconstruction was interrupted. Your original photos are retained; start a new attempt.',error=None,updatedAt=now());self._write(db,job)
        except BaseException:
            self._release_ownership();raise

    def _release_ownership(self):
        owner,self._owner=self._owner,None
        if owner is None:return
        try:
            if os.name=='nt':
                import msvcrt
                owner.seek(0);msvcrt.locking(owner.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(owner.fileno(),fcntl.LOCK_UN)
        finally:owner.close()

    @staticmethod
    def _write(db,job):
        db.execute('INSERT OR REPLACE INTO reconstruction_jobs VALUES(?,?,?,?)',(job['id'],job['projectId'],job['status'],json.dumps(job)))

    def get(self,pid,jid,db=None):
        if db is None:
            with self.store.connect() as db:return self.get(pid,jid,db)
        self.store.get(pid,db)
        row=db.execute('SELECT data FROM reconstruction_jobs WHERE id=? AND project_id=?',(jid,pid)).fetchone()
        if not row:raise HTTPException(404,'Reconstruction not found')
        return json.loads(row['data'])

    def list(self,pid=None):
        if pid:self.store.get(pid)
        with self.store.connect() as db:
            if pid:rows=db.execute('SELECT data FROM reconstruction_jobs WHERE project_id=? ORDER BY json_extract(data,\'$.createdAt\') DESC',(pid,))
            else:rows=db.execute("SELECT j.data FROM reconstruction_jobs j JOIN projects p ON p.id=j.project_id WHERE json_extract(p.data,'$.deletedAt') IS NULL ORDER BY json_extract(j.data,'$.createdAt') DESC")
            return [json.loads(row['data']) for row in rows]

    def _update(self,pid,jid,**values):
        with self.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');job=self.get(pid,jid,db)
            if job['status'] not in ACTIVE:return job
            job.update(values,updatedAt=now());self._write(db,job);return job

    def start(self,pid,image_ids,quality):
        with self.lock:
            if self.closed:raise HTTPException(503,'Reconstruction service is shutting down')
            current=runtime_status()
            if not current['available']:raise HTTPException(503,'Install the local reconstruction runtime before starting a capture.')
            if not 3<=len(image_ids)<=200 or len(set(image_ids))!=len(image_ids):raise HTTPException(422,'Choose 3–200 unique capture photos')
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE');project=self.store.get(pid,db)
                if project.deletedAt:raise HTTPException(409,'Restore this project before reconstructing it')
                assets={a.id:a for a in project.assets}
                if any(i not in project.captureAssetIds or i not in assets or assets[i].role!='capture' for i in image_ids):raise HTTPException(422,'Choose active capture photos owned by this project')
                if db.execute("SELECT 1 FROM reconstruction_jobs WHERE status IN ('queued','running')").fetchone():raise HTTPException(409,'Another reconstruction is already running. Wait or cancel it first.')
                images=[]
                for aid in image_ids:
                    a=assets[aid];path=self.store.asset_path(pid,a)
                    if not path.is_file() or path.stat().st_size!=a.size or self.store.digest(path)!=a.sha256:raise HTTPException(409,'A capture original is missing or changed. Relink the exact original before reconstruction.')
                    item={'id':a.id,'path':a.path,'sha256':a.sha256,'originalName':a.originalName}
                    if 'videoFrame' in a.metadata:item['videoFrame']=validate_video_frame(a.metadata['videoFrame'],assets)
                    images.append(item)
                stamp=now();job={'id':uid(),'projectId':pid,'status':'queued','stage':'preparing','message':'Preparing local photos.','createdAt':stamp,'updatedAt':stamp,'quality':quality,'sourceAssetIds':list(image_ids),'totalPhotos':len(image_ids),'registeredPhotos':0,'unregisteredAssetIds':[],'components':[],'resultAssetId':None,'warnings':[],'error':None,'iteration':None,'totalIterations':None}
                directory=self.directory/job['id'];directory.mkdir()
                payload={'projectId':pid,'jobId':job['id'],'assetRoot':str((self.store.root/'assets'/pid).resolve()),'images':images,'quality':quality}
                self.store.atomic_file(directory/'input.json',json.dumps(payload).encode());self._write(db,job)
            thread=threading.Thread(target=self._run,args=(job,payload,current['versions'],directory),daemon=True,name='stillroom-reconstruction')
            self.threads[job['id']]=thread;thread.start();return job

    def _progress(self,job,event):
        values={};stage=event.get('stage')
        if stage in ('preparing','matching','training','validating','complete'):values['stage']=stage
        if 'message' in event:values['message']=safe_message(event['message'])
        n=job['totalPhotos'];ids=set(job['sourceAssetIds'])
        if type(event.get('registeredPhotos')) is int and 0<=event['registeredPhotos']<=n:values['registeredPhotos']=event['registeredPhotos']
        unregistered=event.get('unregisteredAssetIds')
        if isinstance(unregistered,list) and len(unregistered)<=n and all(isinstance(i,str) and i in ids for i in unregistered):values['unregisteredAssetIds']=list(dict.fromkeys(unregistered))
        components=event.get('components')
        # COLMAP candidate models may reuse images, so these counts need not sum
        # to the capture count. Bound candidates independently (COLMAP max_models).
        if isinstance(components,list) and len(components)<=50 and all(type(v) is int and 0<v<=n for v in components):values['components']=components
        warnings=event.get('warnings')
        if isinstance(warnings,list):values['warnings']=[safe_message(w,'Capture quality needs review.') for w in warnings[:30]]
        iteration,total=event.get('iteration'),event.get('totalIterations')
        if type(iteration) is int and type(total) is int and 0<=iteration<=total<=10_000_000 and total>0:values.update(iteration=iteration,totalIterations=total)
        return values

    def _run(self,job,payload,versions,directory):
        pid,jid=job['projectId'],job['id'];process=None;events=queue.Queue(maxsize=128);finished=threading.Event()
        try:
            with self.lock:
                if self.closed or self.get(pid,jid)['status']!='queued':return
                options={'cwd':ROOT,'stdout':subprocess.PIPE,'stderr':subprocess.STDOUT,'stdin':subprocess.DEVNULL}
                if os.name=='nt':options['creationflags']=subprocess.CREATE_NEW_PROCESS_GROUP
                else:options['start_new_session']=True
                process=subprocess.Popen(worker_command(directory),**options);self.processes[jid]=process
                self._update(pid,jid,status='running')
            def read():
                try:
                    while not finished.is_set():
                        line=process.stdout.readline(MAX_LINE+1)
                        if not line:break
                        while not finished.is_set():
                            try:events.put(line,timeout=.1);break
                            except queue.Full:pass
                        if len(line)>MAX_LINE:break
                finally:
                    while not finished.is_set():
                        try:events.put(None,timeout=.1);break
                        except queue.Full:pass
            threading.Thread(target=read,daemon=True,name='stillroom-job-output').start()
            completion=None;deadline=time.monotonic()+MAX_JOB_SECONDS;log_size=0
            with (directory/'supervisor.log').open('wb') as log:
                while True:
                    if self.closed or self.get(pid,jid)['status'] not in ACTIVE:return
                    if time.monotonic()>deadline:raise InvalidAsset('Reconstruction exceeded the two-hour limit. Try preview quality or fewer photos.')
                    try:line=events.get(timeout=.2)
                    except queue.Empty:continue
                    if line is None:break
                    if len(line)>MAX_LINE:raise InvalidAsset('The reconstruction worker exceeded its progress message limit.')
                    if log_size<MAX_LOG:
                        block=line[:MAX_LOG-log_size];log.write(block);log.flush();log_size+=len(block)
                    try:event=json.loads(line)
                    except (ValueError,UnicodeError):continue
                    if not isinstance(event,dict):continue
                    if event.get('status') in ('ready','needs_photos'):completion=event
                    progress=self._progress(job,event)
                    if event.get('status')=='ready':progress.update(stage='validating',message='Waiting for the worker to finish, then checking the Gaussian scene.')
                    self._update(pid,jid,**progress)
            remaining=max(.1,deadline-time.monotonic());code=process.wait(timeout=remaining)
            if code!=0:raise InvalidAsset('Local reconstruction failed. Check photo overlap and runtime setup, then try again. Originals are retained.')
            if not completion:raise InvalidAsset('The worker finished without a validated reconstruction. Try again or review the local setup.')
            if completion['status']=='needs_photos':
                self._update(pid,jid,status='needs_photos',stage='matching',message=safe_message(completion.get('message'),'More overlapping photos are needed.'));return
            with self.lock:
                if self.closed or self.get(pid,jid)['status'] not in ACTIVE:return
                self._update(pid,jid,stage='validating',message='Checking the reconstructed Gaussian scene.')
                result=Path(completion.get('resultPath',''))
                if not result.is_absolute():result=directory/result
                result=result.resolve()
                require(result.is_relative_to(directory.resolve()) and result.suffix.lower()=='.ply' and result.is_file(),'Worker result is missing or outside its managed job directory.')
                require(0<result.stat().st_size<=MAX_ASSET,'Reconstructed scan exceeds the 256 MiB scene limit.')
                data=result.read_bytes();validate_asset('reconstructed-room.ply',data,'scan')
                meta=completion.get('metadata');meta=meta if isinstance(meta,dict) else {}
                provenance={'type':'photogrammetry-gaussian','sourceImages':[{'assetId':x['id'],'sha256':x['sha256']} for x in payload['images']],'quality':job['quality'],'versions':versions,'settings':meta.get('settings',{}),'registeredAssetIds':meta.get('registeredAssetIds',[]),'warnings':self.get(pid,jid)['warnings']}
                for field in ('initialView','displayRotation'):
                    if field in meta:provenance[field]=meta[field]
                provenance=validate_reconstruction_provenance(provenance,{a.id:a for a in self.store.get(pid).assets})
                asset=self.store.add_asset(pid,'reconstructed-room.ply',data,'scan',metadata={'reconstruction':provenance})
                self._update(pid,jid,status='ready',stage='complete',resultAssetId=asset.id,message='Reconstruction ready. Preview it before replacing the room.')
        except Exception as exc:
            message=safe_message(str(exc),'Local reconstruction could not complete. Check photo overlap and runtime setup, then retry.') if isinstance(exc,InvalidAsset) else 'Local reconstruction could not complete. Check photo overlap and runtime setup, then retry.'
            if not self.closed:self._update(pid,jid,status='failed',message=message,error=message)
        finally:
            finished.set()
            if process:
                self._kill(process)
                if process.stdout:process.stdout.close()
            with self.lock:self.processes.pop(jid,None);self.threads.pop(jid,None)

    @staticmethod
    def _kill(process):
        if os.name=='nt':
            if process.poll() is None:
                subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10,check=False)
        else:
            try:os.killpg(process.pid,signal.SIGTERM)
            except (ProcessLookupError,PermissionError):pass
            try:process.wait(timeout=1)
            except subprocess.TimeoutExpired:pass
            try:os.killpg(process.pid,signal.SIGKILL)
            except (ProcessLookupError,PermissionError):pass
        try:process.wait(timeout=2)
        except subprocess.TimeoutExpired:process.kill()

    def cancel(self,pid,jid):
        with self.lock:
            job=self.get(pid,jid)
            if job['status'] not in ACTIVE:return job
            process=self.processes.get(jid)
            if process:self._kill(process)
            return self._update(pid,jid,status='cancelled',message='Reconstruction cancelled. Original photos are retained.',error=None)

    def close(self):
        with self.lock:
            if self.closed:return
            self.closed=True
            for process in list(self.processes.values()):self._kill(process)
            threads=list(self.threads.values())
        for thread in threads:thread.join(timeout=3)
        try:
            with self.store.connect() as db:
                for row in db.execute("SELECT data FROM reconstruction_jobs WHERE status IN ('queued','running')").fetchall():
                    job=json.loads(row['data']);job.update(status='interrupted',updatedAt=now(),message='Reconstruction interrupted. Original photos are retained.');self._write(db,job)
        finally:self._release_ownership()

    def accept(self,pid,jid,expected):
        with self.lock,self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');project=self.store.get(pid,db);job=self.get(pid,jid,db)
            if project.revision!=expected:raise HTTPException(409,'Project changed. Reload and review the result before accepting.')
            if job['status']!='ready':raise HTTPException(409,'Only a ready reconstruction can be accepted')
            asset=next((a for a in project.assets if a.id==job['resultAssetId']),None)
            if not asset or asset.role!='scan' or asset.kind!='ply':raise HTTPException(409,'Reconstruction result is unavailable')
            path=self.store.asset_path(pid,asset)
            if not path.is_file() or path.stat().st_size!=asset.size or self.store.digest(path)!=asset.sha256:raise HTTPException(409,'Reconstruction result is missing or changed. Start a new attempt.')
            validate_asset(asset.path,path.read_bytes(),'scan')
            entrance=None;transform=Transform();alignment_notes=''
            provenance=asset.metadata.get('reconstruction',{})
            if 'initialView' in provenance or 'displayRotation' in provenance:
                provenance=validate_reconstruction_provenance(provenance,{a.id:a for a in project.assets})
                if 'initialView' in provenance:entrance=Viewpoint(id=uid(),name='Reconstructed camera',**provenance['initialView'])
                if 'displayRotation' in provenance:
                    transform=Transform(rotation=provenance['displayRotation'])
                    alignment_notes='Estimated visual upright from reconstructed camera orientations; review alignment. This does not establish physical floor orientation or real-world scale.'
            project.room=Room(scanAssetId=asset.id,scanVersion=asset.sha256,sceneType='splat',sourceUnits='unknown',entrance=entrance,transform=transform,alignmentNotes=alignment_notes,visualQuality='Reconstructed from local photographs. Scale is unknown; inspect holes, floaters and alignment before calibration. Anchors are approximate.')
            project.viewpoints=[]
            for memory in project.memories:
                memory.anchor.needsReview=True;memory.viewpoint=None;memory.updatedAt=now()
            project.revision+=1;project.updatedAt=now();self.store.write(db,project)
            job.update(status='accepted',stage='complete',updatedAt=now(),message='Reconstruction accepted. Set the floor, entrance and scale; review existing memory anchors.');self._write(db,job)
            return project


class StartBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    imageIds:list[str]=Field(min_length=3,max_length=200)
    quality:Literal['preview','standard']

class AcceptBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expectedRevision:int=Field(ge=0)


def install_reconstruction(app,store):
    manager=ReconstructionManager(store);app.state.reconstruction=manager
    from .video import install_video
    video=install_video(app,store)
    @asynccontextmanager
    async def lifespan(app):
        try:yield
        finally:
            video.close();manager.close()
    app.router.lifespan_context=lifespan
    @app.get('/api/reconstruction/runtime')
    def runtime():return status()
    @app.post('/api/reconstruction/runtime/install')
    def install():return begin_install()
    @app.get('/api/reconstructions')
    def all_jobs():return manager.list()
    @app.get('/api/projects/{pid}/reconstructions')
    def jobs(pid:str):return manager.list(pid)
    @app.post('/api/projects/{pid}/reconstructions')
    def start(pid:str,body:StartBody):return manager.start(pid,body.imageIds,body.quality)
    @app.get('/api/projects/{pid}/reconstructions/{jid}')
    def job(pid:str,jid:str):return manager.get(pid,jid)
    @app.post('/api/projects/{pid}/reconstructions/{jid}/cancel')
    def cancel(pid:str,jid:str):return manager.cancel(pid,jid)
    @app.post('/api/projects/{pid}/reconstructions/{jid}/accept')
    def accept(pid:str,jid:str,body:AcceptBody):return manager.accept(pid,jid,body.expectedRevision)
