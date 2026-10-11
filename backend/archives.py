"""Portable archives are validated entirely before committing a new project."""
import hashlib
import io
import json
import shutil
import stat
import zipfile
from pathlib import PurePosixPath
from .formats import MAX_ASSET,InvalidAsset,require,validate_asset,validate_reconstruction_provenance,validate_video_frame
from .models import Project,now,uid

MAX_ARCHIVE=512*1024*1024
MAX_EXPANDED=1024*1024*1024

def export_project(store,p):
    manifest=p.model_dump_json().encode('utf-8')
    require(len(manifest)<=4*1024*1024,'Export manifest exceeds the 4 MiB import limit')
    entries=[('manifest.json',len(manifest))]+[('assets/'+a.path,a.size) for a in p.assets]
    # ZIP_STORED, ASCII names, no ZIP64: local header 30 + central header 46,
    # the filename in both headers, and the final 22-byte central-directory tail.
    archive_size=22+sum(size+76+2*len(name.encode('utf-8')) for name,size in entries)
    require(archive_size<=MAX_ARCHIVE,'Export exceeds the 512 MiB portable archive limit, including manifest and ZIP headers')
    problems=store.integrity(p)
    require(not problems['missing'] and not problems['corrupt'],'Relink missing or corrupt assets before export')
    require(sum(a.size for a in p.assets)<=MAX_EXPANDED,'Export exceeds the 1 GiB portable project limit')
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_STORED) as z:
        z.writestr('manifest.json',manifest)
        for asset in p.assets:z.write(store.asset_path(p.id,asset),'assets/'+asset.path)
    stream.seek(0);return stream

def import_project(store,data):
    require(len(data)<=MAX_ARCHIVE,'ZIP upload limit is 512 MiB')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries=archive.infolist();require(0<len(entries)<=1001,'ZIP supports at most 1000 assets and one manifest')
            names=set();expanded=0
            for info in entries:
                name=info.filename;path=PurePosixPath(name)
                require(not info.is_dir() and not path.is_absolute() and '\\' not in name and '\x00' not in name and ':' not in name and all(x not in ('..','.') for x in name.split('/')),'Unsafe ZIP member path')
                require(name not in names,'Duplicate ZIP member');names.add(name)
                mode=(info.external_attr>>16)&0xffff
                require(not stat.S_ISLNK(mode) and (not stat.S_IFMT(mode) or stat.S_ISREG(mode)),'ZIP links and special files are forbidden')
                require(not info.flag_bits&1 and info.compress_type in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED),'Unsupported encrypted/compressed ZIP entry')
                expanded+=info.file_size
                require(info.file_size<=MAX_ASSET and expanded<=MAX_EXPANDED,'ZIP expanded size limit exceeded')
                require(info.file_size<=max(1,info.compress_size)*200,'ZIP compression ratio exceeds 200:1')
            require('manifest.json' in names,'ZIP manifest.json missing')
            require(archive.getinfo('manifest.json').file_size<=4*1024*1024,'Manifest exceeds 4 MiB')
            p=Project.model_validate_json(archive.read('manifest.json'))
            expected={'manifest.json'}|{'assets/'+a.path for a in p.assets}
            require(names==expected,'ZIP entries must exactly match the manifest assets')
            validated=[]
            assets_by_id={a.id:a for a in p.assets}
            for a in p.assets:
                payload=archive.read('assets/'+a.path)
                require(len(payload)==a.size and hashlib.sha256(payload).hexdigest()==a.sha256,'Asset checksum or size mismatch')
                kind,mime,metadata,suffix=validate_asset(a.path,payload,a.role)
                require(kind==a.kind and mime==a.mimeType and a.path.endswith(suffix),'Asset content disagrees with manifest format')
                if 'derivedFrom' in a.metadata:
                    source_id=a.metadata['derivedFrom']
                    source=assets_by_id.get(source_id) if isinstance(source_id,str) else None
                    require(a.role=='thumbnail' and a.kind=='image' and source is not None and source.role in ('media','capture') and source.kind=='image' and source.thumbnailId==a.id,'Invalid thumbnail source relationship')
                    metadata['derivedFrom']=source_id
                if 'videoFrame' in a.metadata:
                    require(a.role=='capture' and a.kind=='image','Video frame provenance requires a capture image')
                    metadata['videoFrame']=validate_video_frame(a.metadata['videoFrame'],assets_by_id)
                if 'reconstruction' in a.metadata:
                    require(a.role=='scan' and a.kind=='ply','Reconstruction provenance requires a Gaussian scan')
                    metadata['reconstruction']=validate_reconstruction_provenance(a.metadata['reconstruction'],assets_by_id)
                a.metadata=metadata;validated.append((a,payload))
            p.id=uid();p.createdAt=p.updatedAt=now();p.revision=0;p.deletedAt=None
            try:
                for a,payload in validated:store.atomic_file(store.asset_path(p.id,a),payload)
                store.create(p)
            except Exception:
                shutil.rmtree(store.root/'assets'/p.id,ignore_errors=True);raise
            return p
    except InvalidAsset:raise
    except Exception as e:raise InvalidAsset('Invalid project archive: '+str(e)[:300]) from e
