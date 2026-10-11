"""SQLite transactions pair project state, history and the full-text index."""
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from fastapi import HTTPException
from .models import Asset,Project,now,uid
from .formats import validate_asset
from .thumbnails import image_thumbnail

MAX_CAPTURE_COUNT=200
MAX_CAPTURE_BYTES=256*1024*1024

class Store:
    def __init__(self,root):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        (self.root/'assets').mkdir(exist_ok=True)
        self.database=self.root/'stillroom.sqlite3'
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, revision INTEGER NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS history(project_id TEXT NOT NULL, revision INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY(project_id, revision));
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(project_id UNINDEXED, memory_id UNINDEXED, title, note, tags);
            ''')
    def connect(self):
        db=sqlite3.connect(self.database,timeout=15);db.row_factory=sqlite3.Row;db.execute('PRAGMA synchronous=FULL');return db
    def get(self,id,db=None):
        if db is None:
            with self.connect() as db:return self.get(id,db)
        row=db.execute('SELECT data FROM projects WHERE id=?',(id,)).fetchone()
        if row is None:raise HTTPException(404,'Project not found')
        return Project.model_validate_json(row['data'])
    def all(self):
        with self.connect() as db:return [Project.model_validate_json(r['data']) for r in db.execute('SELECT data FROM projects ORDER BY json_extract(data,\'$.updatedAt\') DESC')]
    def write(self,db,p):
        data=p.model_dump_json()
        db.execute('INSERT OR REPLACE INTO projects VALUES(?,?,?)',(p.id,p.revision,data))
        db.execute('INSERT OR REPLACE INTO history VALUES(?,?,?)',(p.id,p.revision,data))
        db.execute('DELETE FROM memories_fts WHERE project_id=?',(p.id,))
        db.executemany('INSERT INTO memories_fts VALUES(?,?,?,?,?)',[(p.id,m.id,m.title,m.note,' '.join(m.tags)) for m in p.memories if m.deletedAt is None])
        # Keep bounded committed recovery snapshots; newest 100 survive crashes.
        db.execute('DELETE FROM history WHERE project_id=? AND revision NOT IN (SELECT revision FROM history WHERE project_id=? ORDER BY revision DESC LIMIT 100)',(p.id,p.id))
    def create(self,p):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');self.write(db,p)
        return p
    def update(self,id,expected,incoming):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');old=self.get(id,db)
            if old.revision!=expected:raise HTTPException(409,'Project changed in another window. Reload before saving.')
            for field in ('id','createdAt','updatedAt','revision','assets','deletedAt','captureAssetIds'):
                if getattr(incoming,field)!=getattr(old,field):raise HTTPException(422,f'Server-managed {field} cannot be changed through PUT')
            if incoming.room.scanAssetId!=old.room.scanAssetId or incoming.room.scanVersion!=old.room.scanVersion:
                for memory in incoming.memories:
                    if memory.anchor.assetId!=incoming.room.scanAssetId or memory.anchor.scanVersion!=incoming.room.scanVersion:memory.anchor.needsReview=True
            stamp=now();old_mem={m.id:m for m in old.memories}
            for memory in incoming.memories:
                prior=old_mem.get(memory.id)
                if prior:
                    memory.createdAt=prior.createdAt
                    memory.updatedAt=stamp if memory!=prior else prior.updatedAt
                else:memory.createdAt=memory.updatedAt=stamp
            incoming.revision=old.revision+1;incoming.updatedAt=stamp;self.write(db,incoming)
        return incoming
    def mutate(self,id,fn):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');p=self.get(id,db);fn(p);p.revision+=1;p.updatedAt=now();self.write(db,p)
        return p
    def asset_path(self,pid,asset):
        base=(self.root/'assets'/pid).resolve();path=(base/asset.path).resolve()
        if path.parent!=base or base.parent!=(self.root/'assets').resolve():raise HTTPException(422,'Unsafe managed asset path')
        return path
    def atomic_file(self,path,data):
        path.parent.mkdir(parents=True,exist_ok=True)
        temp=path.with_name('.'+uid()+'.tmp')
        try:
            with temp.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
            os.replace(temp,path)
            if os.name!='nt':
                fd=os.open(path.parent,os.O_RDONLY)
                try:os.fsync(fd)
                finally:os.close(fd)
        finally:temp.unlink(missing_ok=True)
    def add_asset(self,pid,name,data,role,metadata=None):
        self.get(pid)
        kind,mime,meta,suffix=validate_asset(name,data,role);id=uid()
        name=Path(name.replace('\\','/')).name[:255]
        asset=Asset(id=id,originalName=name,role=role,kind=kind,mimeType=mime,size=len(data),sha256=hashlib.sha256(data).hexdigest(),path=id+suffix,createdAt=now(),metadata=meta)
        if metadata:asset.metadata.update(metadata)
        additions=[(asset,data)]
        if role in ('media','capture') and kind=='image':
            thumb_data,thumb_mime,thumb_suffix,thumb_meta=image_thumbnail(data)
            thumb_id=uid();thumb_meta['derivedFrom']=asset.id
            thumbnail=Asset(
                id=thumb_id,originalName=Path(name).stem[:230]+'.thumbnail'+thumb_suffix,
                role='thumbnail',kind='image',mimeType=thumb_mime,size=len(thumb_data),
                sha256=hashlib.sha256(thumb_data).hexdigest(),path=thumb_id+thumb_suffix,
                createdAt=asset.createdAt,metadata=thumb_meta,
            )
            asset.thumbnailId=thumbnail.id
            additions.append((thumbnail,thumb_data))
        paths=[]
        try:
            # Serialize deduplication, retained-original budgets and association.
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE');project=self.get(pid,db)
                if role=='capture':
                    captures=[a for a in project.assets if a.role=='capture']
                    existing=next((a for a in captures if a.sha256==asset.sha256),None)
                    if existing:
                        if existing.id not in project.captureAssetIds:
                            project.captureAssetIds.append(existing.id);project.revision+=1;project.updatedAt=now();self.write(db,project)
                        return existing
                    if len(captures)>=MAX_CAPTURE_COUNT or sum(a.size for a in captures)+asset.size>MAX_CAPTURE_BYTES:
                        raise HTTPException(422,'Capture limit is 200 retained source photos and 256 MiB combined originals; use another project for a new collection.')
                if len(project.assets)+len(additions)>1000:raise HTTPException(422,'Project limit is 1000 assets, including thumbnails')
                for item,payload in additions:
                    path=self.asset_path(pid,item);paths.append(path);self.atomic_file(path,payload)
                project.assets.extend(item for item,_ in additions)
                if role=='capture':project.captureAssetIds.append(asset.id)
                project.revision+=1;project.updatedAt=now();self.write(db,project)
        except Exception:
            for path in paths:path.unlink(missing_ok=True)
            raise
        return asset
    def remove_capture(self,pid,aid):
        def remove(project):
            if aid not in project.captureAssetIds:raise HTTPException(404,'Capture photo not found in this collection')
            project.captureAssetIds.remove(aid)
        return self.mutate(pid,remove)
    def asset(self,pid,aid):
        p=self.get(pid)
        asset=next((a for a in p.assets if a.id==aid),None)
        if asset is None:raise HTTPException(404,'Asset not found')
        return asset
    def integrity(self,p):
        missing=[];corrupt=[]
        for a in p.assets:
            path=self.asset_path(p.id,a)
            if not path.is_file():missing.append(a.id)
            elif path.stat().st_size!=a.size or self.digest(path)!=a.sha256:corrupt.append(a.id)
        return {'missing':missing,'corrupt':corrupt}
    @staticmethod
    def digest(path):
        h=hashlib.sha256()
        with path.open('rb') as f:
            for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
        return h.hexdigest()
    def recover(self,id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');old=self.get(id,db)
            row=db.execute('SELECT data FROM history WHERE project_id=? AND revision<? ORDER BY revision DESC LIMIT 1',(id,old.revision)).fetchone()
            if row is None:raise HTTPException(404,'No earlier committed revision is available')
            p=Project.model_validate_json(row['data']);p.revision=old.revision+1;p.updatedAt=now();self.write(db,p)
        return p
    def search(self,id,query):
        p=self.get(id)
        terms=query.split()[:30]
        if not terms:return [m for m in p.memories if m.deletedAt is None]
        expression=' AND '.join('"'+t.replace('"','""')+'"' for t in terms)
        with self.connect() as db:
            ids={r[0] for r in db.execute('SELECT memory_id FROM memories_fts WHERE project_id=? AND memories_fts MATCH ? LIMIT 1000',(id,expression))}
        return [m for m in p.memories if m.id in ids]

    def add_video_frames(self,pid,source_id,frames):
        """Commit the entire derived capture set or leave the collection untouched."""
        from .formats import validate_video_frame
        additions=[];paths=[];capture_ids=[]
        try:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE');p=self.get(pid,db)
                if p.deletedAt:raise HTTPException(409,'Restore this project before extracting frames')
                assets={a.id:a for a in p.assets};source=assets.get(source_id)
                if not source or source.kind!='video':raise HTTPException(422,'Retained video missing')
                captures=[a for a in p.assets if a.role=='capture'];by_hash={a.sha256:a for a in captures}
                for name,data,provenance in frames:
                    kind,mime,meta,suffix=validate_asset(name,data,'capture')
                    digest=hashlib.sha256(data).hexdigest();existing=by_hash.get(digest)
                    if existing:
                        if existing.id not in capture_ids:capture_ids.append(existing.id)
                        continue
                    meta['videoFrame']=validate_video_frame(provenance,assets)
                    aid=uid();stamp=now();tid=uid()
                    a=Asset(id=aid,originalName=name,role='capture',kind=kind,mimeType=mime,size=len(data),sha256=digest,path=aid+suffix,createdAt=stamp,metadata=meta,thumbnailId=tid)
                    thumb,tm,ts,tmeta=image_thumbnail(data);tmeta['derivedFrom']=aid
                    t=Asset(id=tid,originalName=name+'.thumbnail'+ts,role='thumbnail',kind='image',mimeType=tm,size=len(thumb),sha256=hashlib.sha256(thumb).hexdigest(),path=tid+ts,createdAt=stamp,metadata=tmeta)
                    additions.extend(((a,data),(t,thumb)));captures.append(a);by_hash[digest]=a;capture_ids.append(aid)
                if len(captures)>MAX_CAPTURE_COUNT or sum(a.size for a in captures)>MAX_CAPTURE_BYTES:raise HTTPException(422,'These frames exceed the 200 retained photos and 256 MiB limit. Choose fewer frames or start another place.')
                if len(p.assets)+len(additions)>1000:raise HTTPException(422,'Project asset limit reached. Start another place.')
                for a,data in additions:
                    path=self.asset_path(pid,a);paths.append(path);self.atomic_file(path,data)
                p.assets.extend(a for a,_ in additions)
                p.captureAssetIds=list(dict.fromkeys(p.captureAssetIds+capture_ids));p.revision+=1;p.updatedAt=now();self.write(db,p)
            return capture_ids
        except Exception:
            for path in paths:path.unlink(missing_ok=True)
            raise
