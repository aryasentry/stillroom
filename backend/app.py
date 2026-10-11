"""FastAPI entrypoint: all project data remains on this machine."""
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit
from typing import Literal
from fastapi import FastAPI,File,Form,HTTPException,Query,Request,UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse,JSONResponse,StreamingResponse
from pydantic import BaseModel,ConfigDict,Field,ValidationError
from .archives import MAX_ARCHIVE,export_project,import_project
from .formats import MAX_ASSET,InvalidAsset
from .models import Anchor,Memory,Project,Viewpoint,now,uid
from .storage import Store
from .openrouter import install_openrouter
from .reconstruction import install_reconstruction

ROOT=Path(__file__).resolve().parent.parent

class CreateBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    title:str=Field(min_length=1,max_length=200)
class SaveBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expectedRevision:int=Field(ge=0)
    project:Project

class LocalGuard:
    """Check headers before parsing multipart; bound streamed request bodies."""
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        headers={k.lower():v for k,v in scope['headers']}
        host=headers.get(b'host',b'').decode('latin1')
        try:
            parsed=urlsplit('http://'+host)
            safe=bool(re.fullmatch(r'(localhost|127\.0\.0\.1|\[::1\])(?::[0-9]{1,5})?',host))
            if parsed.port is not None:safe=safe and 0<parsed.port<=65535
        except ValueError:safe=False
        async def reject(code,detail):await JSONResponse({'detail':detail},status_code=code)(scope,receive,send)
        if not safe:return await reject(403,'Stillroom accepts loopback Host headers only')
        origin=headers.get(b'origin',b'').decode('latin1')
        allowed={f"{scope.get('scheme','http')}://{host}"}
        if os.environ.get('STILLROOM_DEV')=='1':allowed.update({'http://127.0.0.1:5173','http://localhost:5173'})
        if origin and origin not in allowed:return await reject(403,'Cross-origin requests are forbidden')
        if scope['method'] not in ('GET','HEAD','OPTIONS') and headers.get(b'x-stillroom-request')!=b'local':return await reject(403,'Missing X-Stillroom-Request: local header')
        path=scope.get('path','')
        maximum=MAX_ARCHIVE+1024*1024 if path=='/api/import' else MAX_ASSET+1024*1024 if '/assets' in path else 4*1024*1024
        try:
            if int(headers.get(b'content-length',b'0'))>maximum:return await reject(413,'Request exceeds the size limit')
        except ValueError:return await reject(400,'Invalid Content-Length')
        consumed=0
        async def bounded_receive():
            nonlocal consumed
            message=await receive()
            consumed+=len(message.get('body',b''))
            if consumed>maximum:raise HTTPException(413,'Request exceeds the size limit')
            return message
        async def secured_send(message):
            if message['type']=='http.response.start':
                message.setdefault('headers',[]).extend([(b'x-content-type-options',b'nosniff'),(b'referrer-policy',b'no-referrer'),(b'cross-origin-resource-policy',b'same-origin'),(b'content-security-policy',b"default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self' blob:; worker-src 'self' blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")])
            await send(message)
        return await self.app(scope,bounded_receive,secured_send)

async def uploaded(file,maximum=MAX_ASSET):
    data=bytearray()
    try:
        while True:
            block=await file.read(1024*1024)
            if not block:break
            data.extend(block)
            if len(data)>maximum:raise HTTPException(413,f'File exceeds {maximum//1024//1024} MiB limit')
    finally:await file.close()
    return bytes(data)

def create_app(data_dir=None):
    app=FastAPI(title='Stillroom',docs_url=None,redoc_url=None,openapi_url=None)
    store=Store(data_dir or os.environ.get('STILLROOM_DATA_DIR') or ROOT/'.stillroom')
    app.state.store=store
    app.add_middleware(LocalGuard)
    @app.exception_handler(InvalidAsset)
    async def invalid_asset(request,exc):return JSONResponse({'detail':str(exc)},status_code=422)
    @app.exception_handler(RequestValidationError)
    async def validation_error(request,exc):
        return JSONResponse({'detail':'; '.join('.'.join(str(x) for x in e['loc'])+': '+e['msg'] for e in exc.errors())[:2000]},status_code=422)
    @app.exception_handler(ValidationError)
    async def stored_validation(request,exc):return JSONResponse({'detail':'Invalid project schema: '+str(exc)[:1500]},status_code=422)
    @app.get('/api/health')
    def health():return {'status':'ok','schemaVersion':2}
    @app.get('/api/projects')
    def projects():
        return [{'id':p.id,'title':p.title,'updatedAt':p.updatedAt,'createdAt':p.createdAt,'memoryCount':sum(m.deletedAt is None for m in p.memories),'sceneType':p.room.sceneType,'synthetic':p.room.synthetic,'thumbnailAssetId':p.thumbnailAssetId,'deletedAt':p.deletedAt} for p in store.all()]
    @app.post('/api/projects')
    def create(body:CreateBody):return store.create(Project(title=body.title))
    @app.get('/api/projects/{pid}')
    def get(pid:str):return store.get(pid)
    @app.put('/api/projects/{pid}')
    def save(pid:str,body:SaveBody):return store.update(pid,body.expectedRevision,body.project)
    @app.delete('/api/projects/{pid}')
    def delete(pid:str):return store.mutate(pid,lambda p:setattr(p,'deletedAt',now()))
    @app.post('/api/projects/{pid}/restore')
    def restore(pid:str):return store.mutate(pid,lambda p:setattr(p,'deletedAt',None))
    @app.post('/api/projects/{pid}/assets')
    async def add_asset(pid:str,file:UploadFile=File(...),role:Literal['scan','collision','media','thumbnail','capture']=Form(...)):
        return store.add_asset(pid,file.filename or 'file',await uploaded(file),role)
    @app.delete('/api/projects/{pid}/captures/{aid}')
    def remove_capture(pid:str,aid:str):return store.remove_capture(pid,aid)
    @app.get('/api/projects/{pid}/assets/{aid}')
    def asset_file(pid:str,aid:str):
        asset=store.asset(pid,aid);path=store.asset_path(pid,asset)
        if not path.is_file():raise HTTPException(404,'Managed asset missing. Relink the original file.')
        if path.stat().st_size!=asset.size or store.digest(path)!=asset.sha256:raise HTTPException(409,'Managed asset is corrupt. Relink the original file.')
        return FileResponse(path,media_type=asset.mimeType,headers={'Cache-Control':'private, max-age=31536000, immutable','ETag':'"'+asset.sha256+'"'})
    @app.post('/api/projects/{pid}/assets/{aid}/relink')
    async def relink(pid:str,aid:str,file:UploadFile=File(...)):
        asset=store.asset(pid,aid);payload=await uploaded(file)
        if len(payload)!=asset.size or hashlib.sha256(payload).hexdigest()!=asset.sha256:raise HTTPException(422,'Relink requires the exact original file with the same SHA256')
        store.atomic_file(store.asset_path(pid,asset),payload);store.mutate(pid,lambda p:None)
        return asset
    @app.get('/api/projects/{pid}/search')
    def search(pid:str,q:str=Query(default='',max_length=2000)):return store.search(pid,q)
    @app.get('/api/projects/{pid}/integrity')
    def integrity(pid:str):return store.integrity(store.get(pid))
    @app.post('/api/projects/{pid}/recover')
    def recover(pid:str):return store.recover(pid)
    @app.get('/api/projects/{pid}/export')
    def export(pid:str):
        stream=export_project(store,store.get(pid))
        def chunks():
            while block:=stream.read(1024*1024):yield block
        return StreamingResponse(chunks(),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="stillroom-{pid}.zip"'})
    @app.post('/api/import')
    async def import_zip(file:UploadFile=File(...)):return import_project(store,await uploaded(file,MAX_ARCHIVE))
    @app.post('/api/demo')
    def demo(kind:Literal['mesh','ply','spz']='mesh'):
        scan=ROOT/'fixtures'/('synthetic-room.glb' if kind=='mesh' else 'synthetic-room.'+kind)
        if not scan.is_file():raise HTTPException(503,'Synthetic demo fixtures are not installed')
        p=store.create(Project(title='The afternoon room'))
        a=store.add_asset(p.id,scan.name,scan.read_bytes(),'scan')
        media=[]
        for name in ('memory-photo.png','memory-audio.wav'):
            path=ROOT/'fixtures'/name
            if path.is_file():media.append(store.add_asset(p.id,name,path.read_bytes(),'media'))
        def setup(p):
            p.room.scanAssetId=a.id;p.room.scanVersion=a.sha256;p.room.sceneType='mesh' if kind=='mesh' else 'splat'
            p.room.sourceUnits='metres';p.room.floorY=0;p.room.synthetic=True
            p.room.visualQuality='Synthetic geometry fixture. Demonstrates local saving and anchors; not a captured room.'
            p.room.entrance=Viewpoint(id=uid(),name='Entrance',position=(0,1.65,2),quaternion=(0,0,0,1),target=(0,1.65,-1))
            p.viewpoints=[p.room.entrance.model_copy(deep=True)]
            p.memories=[Memory(id=uid(),title='An afternoon at the desk',note='A sample memory attached to the desk. Replace this text with your own story.',date=None,tags=['sample'],anchor=Anchor(position=(-1.7,.85,-1.5),assetId=a.id,scanVersion=a.sha256,placement='surface' if kind=='mesh' else 'approximate',needsReview=False),mediaIds=[x.id for x in media],viewpoint=p.room.entrance.model_copy(deep=True),createdAt=now(),updatedAt=now(),deletedAt=None)]
            if media:p.thumbnailAssetId=next((x.thumbnailId or x.id for x in media if x.kind=='image'),None)
        return store.mutate(p.id,setup)
    install_openrouter(app,store)
    install_reconstruction(app,store)
    @app.get('/{path:path}')
    def frontend(path:str):
        if path.startswith('api/'):raise HTTPException(404,'API endpoint not found')
        dist=(ROOT/'frontend'/'dist').resolve();target=(dist/path).resolve()
        if target.is_relative_to(dist) and target.is_file():return FileResponse(target)
        index=dist/'index.html'
        if index.is_file():return FileResponse(index)
        raise HTTPException(503,'Frontend is not built. Run the documented setup command.')
    return app

# Uvicorn factory invocation: uvicorn backend.app:create_app --factory --host 127.0.0.1 --port 8848
