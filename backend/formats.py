"""Bounded, self-contained asset validation. No parser accesses the network."""
import io
import json
import math
import struct
import zlib
from pathlib import Path
from PIL import Image

MAX_ASSET=256*1024*1024
MAX_SPLATS=1_000_000
MAX_SPLAT_CENTRE=65504  # Spark PackedSplats stores source centres as float16.
MAX_IMAGE=40*1024*1024
MAX_PIXELS=32_000_000

class InvalidAsset(ValueError):pass

def require(condition,message):
    if not condition:raise InvalidAsset(message)

def image_info(data,allowed=('PNG','JPEG','WEBP')):
    require(len(data)<=MAX_IMAGE,'Images must be at most 40 MiB')
    try:
        with Image.open(io.BytesIO(data)) as im:
            require(im.format in allowed,'Only PNG, JPEG and WebP images are supported')
            require(im.width*im.height<=MAX_PIXELS and max(im.size)<=16384,'Image dimensions exceed 32 megapixels or 16384 pixels per side')
            require(getattr(im,'n_frames',1)==1,'Animated images are not supported')
            result={'width':im.width,'height':im.height,'format':im.format}
            im.verify()
        return result
    except InvalidAsset:raise
    except Exception as e:raise InvalidAsset('Invalid image data') from e

def validate_glb(data):
    """Preflight aggregate work before touching potentially repeated binary arrays."""
    max_scalars=12_000_000
    max_vertices=2_000_000
    max_triangles=1_000_000
    max_primitives=4096
    max_texture_pixels=16_777_216
    max_coordinate=100_000_000
    require(len(data)>=28,'Truncated GLB')
    magic,version,total=struct.unpack_from('<III',data)
    require(magic==0x46546c67 and version==2 and total==len(data),'Expected complete GLB version 2')
    offset=12;chunks=[]
    while offset<len(data):
        require(offset+8<=len(data) and len(chunks)<2,'Invalid GLB chunk count or header')
        length,kind=struct.unpack_from('<II',data,offset);offset+=8
        require(length%4==0 and offset+length<=len(data),'Invalid GLB chunk bounds')
        chunks.append((kind,data[offset:offset+length]));offset+=length
    require(len(chunks)==2 and chunks[0][0]==0x4e4f534a and chunks[1][0]==0x004e4942,'GLB must contain JSON and embedded BIN chunks only')
    require(len(chunks[0][1])<=8*1024*1024,'GLB JSON exceeds 8 MiB')
    doc=json.loads(chunks[0][1]);binary=chunks[1][1]
    require(isinstance(doc,dict) and doc.get('asset',{}).get('version')=='2.0','Expected glTF 2.0')
    pending=[(doc,0)];json_items=0
    while pending:
        value,depth=pending.pop();json_items+=1
        require(depth<=64 and json_items<=200000,'GLB JSON structure exceeds nesting or item limits')
        if isinstance(value,dict):
            require('uri' not in value,'External or data resource URIs are not supported; embed resources in BIN')
            require(not value.get('extensions'),'GLB extensions/compression are not supported')
            pending.extend((x,depth+1) for x in value.values())
        elif isinstance(value,list):pending.extend((x,depth+1) for x in value)
        elif isinstance(value,(int,float)) and not isinstance(value,bool):
            require(math.isfinite(value) and abs(value)<=1e12,'Non-finite or excessive GLB numeric value')
    require(not doc.get('extensionsRequired') and not doc.get('extensionsUsed'),'GLB extensions are not supported')
    require(not doc.get('skins') and not doc.get('animations'),'Skinned and animated GLB files are not supported')

    def objects(key,limit):
        result=doc.get(key,[])
        require(isinstance(result,list) and len(result)<=limit and all(isinstance(x,dict) for x in result),'Invalid or excessive '+key)
        return result
    def index(value,items,label):
        require(type(value) is int and 0<=value<len(items),'Invalid '+label+' index')
        return value
    buffers=objects('buffers',1)
    require(len(buffers)==1 and type(buffers[0].get('byteLength')) is int and 0<=len(binary)-buffers[0]['byteLength']<=3,'GLB needs exactly one embedded buffer')
    views=objects('bufferViews',4096);accessors=objects('accessors',4096)
    meshes=objects('meshes',1024);nodes=objects('nodes',10000);scenes=objects('scenes',16)
    materials=objects('materials',1024);images=objects('images',128);textures=objects('textures',128);samplers=objects('samplers',128)
    require(meshes and nodes and scenes,'GLB needs a mesh and a scene with geometry')
    for v in views:
        require(v.get('buffer')==0 and type(v.get('byteLength')) is int and v['byteLength']>0,'Invalid bufferView')
        start=v.get('byteOffset',0)
        require(type(start) is int and start>=0 and start+v['byteLength']<=buffers[0]['byteLength'],'GLB bufferView out of bounds')
        if 'byteStride' in v:require(type(v['byteStride']) is int and 4<=v['byteStride']<=252 and v['byteStride']%4==0,'Invalid bufferView stride')
    component={5120:('b',1),5121:('B',1),5122:('h',2),5123:('H',2),5125:('I',4),5126:('f',4)}
    widths={'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4}
    specs=[];scalar_work=0
    for a in accessors:
        require('sparse' not in a,'Sparse accessors unsupported')
        v=views[index(a.get('bufferView'),views,'bufferView')]
        require(a.get('componentType') in component and a.get('type') in widths,'Unsupported mesh accessor component/type')
        fmt,size=component[a['componentType']];width=widths[a['type']]
        count=a.get('count');stride=v.get('byteStride',size*width);off=a.get('byteOffset',0)
        require(type(count) is int and 0<count<=3_000_000,'Accessor count exceeds 3 million')
        require(stride>=size*width and stride%size==0,'Invalid accessor stride')
        require(type(off) is int and off>=0 and off%size==0 and off+(count-1)*stride+size*width<=v['byteLength'],'Accessor out of bounds')
        start=v.get('byteOffset',0)+off
        require(start%size==0,'Misaligned accessor')
        require(type(a.get('normalized',False)) is bool and (not a.get('normalized') or a['componentType'] in (5120,5121,5122,5123)),'Invalid normalized accessor')
        for key in ('min','max'):
            if key in a:require(isinstance(a[key],list) and len(a[key])==width and all(type(v) in (int,float) and math.isfinite(v) for v in a[key]),'Invalid accessor bounds')
        scalar_work+=count*width
        require(scalar_work<=max_scalars,'GLB aggregate accessor budget exceeds 12 million scalars')
        specs.append((fmt,width,count,stride,start))

    def texture_info(info):
        require(isinstance(info,dict),'Invalid material texture information')
        index(info.get('index'),textures,'material texture')
        texcoord=info.get('texCoord',0)
        require(type(texcoord) is int and 0<=texcoord<=7,'Texture coordinate set exceeds supported range')
    for material in materials:
        pbr=material.get('pbrMetallicRoughness',{})
        require(isinstance(pbr,dict),'Invalid metallic roughness material')
        for key in ('baseColorTexture','metallicRoughnessTexture'):
            if key in pbr:texture_info(pbr[key])
        for key in ('normalTexture','occlusionTexture','emissiveTexture'):
            if key in material:texture_info(material[key])
    for texture in textures:
        index(texture.get('source'),images,'texture image')
        if 'sampler' in texture:index(texture['sampler'],samplers,'texture sampler')
    for sampler in samplers:
        require(sampler.get('magFilter',9729) in (9728,9729) and sampler.get('minFilter',9987) in (9728,9729,9984,9985,9986,9987),'Invalid texture filter')
        require(sampler.get('wrapS',10497) in (33071,33648,10497) and sampler.get('wrapT',10497) in (33071,33648,10497),'Invalid texture wrapping')

    mesh_costs=[];positions=set();index_limits={};primitive_scalars=0;vertices=0;triangles=0;primitives=0
    for mesh in meshes:
        require(not mesh.get('weights'),'Morph targets unsupported')
        items=mesh.get('primitives')
        require(isinstance(items,list) and 0<len(items)<=max_primitives,'Invalid or excessive mesh primitives')
        mesh_vertices=mesh_triangles=0
        for p in items:
            require(isinstance(p,dict) and p.get('mode',4)==4 and not p.get('targets'),'Only static triangle primitives supported')
            attrs=p.get('attributes',{})
            require(isinstance(attrs,dict) and 'POSITION' in attrs and len(attrs)<=24,'Invalid or excessive vertex attributes')
            pi=index(attrs['POSITION'],accessors,'POSITION');a=accessors[pi];positions.add(pi)
            require(a['componentType']==5126 and a['type']=='VEC3','POSITION must use float VEC3')
            for semantic,ai in attrs.items():
                attr=accessors[index(ai,accessors,'vertex attribute')]
                require(attr['count']==a['count'],'Mismatched vertex attribute count')
                require(not semantic.startswith(('JOINTS_','WEIGHTS_')),'Skinned attributes unsupported')
                if semantic in ('NORMAL','TANGENT'):require(attr['componentType']==5126 and attr['type']==('VEC3' if semantic=='NORMAL' else 'VEC4'),'Invalid normal or tangent attribute')
                elif semantic.startswith(('TEXCOORD_','COLOR_')):
                    allowed=('VEC2',) if semantic.startswith('TEXCOORD_') else ('VEC3','VEC4')
                    require(attr['type'] in allowed and (attr['componentType']==5126 or attr['componentType'] in (5121,5123) and attr.get('normalized')),'Invalid UV or color attribute')
                primitive_scalars+=attr['count']*widths[attr['type']]
            count=a['count']
            if 'indices' in p:
                ii=index(p['indices'],accessors,'triangle accessor');idx=accessors[ii]
                require(idx['componentType'] in (5121,5123,5125) and idx['type']=='SCALAR' and not idx.get('normalized'),'Invalid triangle indices')
                require('byteStride' not in views[idx['bufferView']],'Triangle indices cannot use byteStride')
                count=idx['count'];primitive_scalars+=count
                index_limits[ii]=min(index_limits.get(ii,a['count']),a['count'])
            if 'material' in p:index(p['material'],materials,'primitive material')
            require(count%3==0,'Triangle index/vertex count must be divisible by three')
            primitives+=1;vertices+=a['count'];triangles+=count//3
            mesh_vertices+=a['count'];mesh_triangles+=count//3
            require(primitive_scalars<=max_scalars and vertices<=max_vertices and triangles<=max_triangles and primitives<=max_primitives,'GLB aggregate primitive geometry budget exceeded (12M scalars, 2M vertices, 1M triangles, 4096 primitives)')
        mesh_costs.append((mesh_vertices,mesh_triangles,len(items)))

    identity=(1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.)
    def multiply(a,b):return tuple(sum(a[row*4+k]*b[k*4+col] for k in range(4)) for row in range(4) for col in range(4))
    def transform(n):
        for key,length in [('matrix',16),('translation',3),('rotation',4),('scale',3)]:
            if key in n:require(isinstance(n[key],list) and len(n[key])==length and all(type(x) in (int,float) and math.isfinite(x) for x in n[key]),'Invalid node transform')
        if 'matrix' in n:
            require(not any(k in n for k in ('translation','rotation','scale')),'Node must use matrix or TRS, not both')
            raw=n['matrix'];m=tuple(raw[col*4+row] for row in range(4) for col in range(4))
            require(all(abs(m[12+i])<1e-8 for i in range(3)) and abs(m[15]-1)<1e-8,'Node matrix must be affine')
        else:
            q=n.get('rotation',[0,0,0,1]);norm=sum(x*x for x in q)
            require(.98<=norm<=1.02,'Node quaternion must be normalized')
            x,y,z,w=[v/math.sqrt(norm) for v in q];sx,sy,sz=n.get('scale',[1,1,1]);tx,ty,tz=n.get('translation',[0,0,0])
            m=((1-2*(y*y+z*z))*sx,2*(x*y-z*w)*sy,2*(x*z+y*w)*sz,tx,
               2*(x*y+z*w)*sx,(1-2*(x*x+z*z))*sy,2*(y*z-x*w)*sz,ty,
               2*(x*z-y*w)*sx,2*(y*z+x*w)*sy,(1-2*(x*x+y*y))*sz,tz,0.,0.,0.,1.)
        norms=[math.sqrt(sum(m[r*4+c]**2 for r in range(3))) for c in range(3)]
        determinant=m[0]*(m[5]*m[10]-m[6]*m[9])-m[1]*(m[4]*m[10]-m[6]*m[8])+m[2]*(m[4]*m[9]-m[5]*m[8])
        require(all(1e-8<=v<=1e6 for v in norms) and abs(determinant)>1e-8*math.prod(norms),'Degenerate or excessive node transform scale')
        require(all(abs(m[i])<=max_coordinate for i in (3,7,11)),'Node translation exceeds source-coordinate limits')
        return m

    # A glTF node has at most one parent. Walking the full forest from its actual
    # roots measures depth independently of numeric node order and detects cycles.
    parents=[None]*len(nodes);local=[]
    for i,n in enumerate(nodes):
        require('skin' not in n and 'weights' not in n,'Skinned/morph nodes unsupported')
        if 'mesh' in n:index(n['mesh'],meshes,'node mesh')
        children=n.get('children',[])
        require(isinstance(children,list),'Invalid node children')
        for child in children:
            index(child,nodes,'child node')
            require(parents[child] is None,'Node has multiple parents or repeated child references')
            parents[child]=i
        local.append(transform(n))
    world=[None]*len(nodes);visited=0
    stack=[(i,1,identity) for i,parent in enumerate(parents) if parent is None]
    while stack:
        i,depth,parent_matrix=stack.pop()
        require(depth<=64,'GLB scene graph depth exceeds 64 nodes')
        m=multiply(parent_matrix,local[i])
        require(all(math.isfinite(v) and abs(v)<=max_coordinate for v in m),'Composed node transform exceeds source-coordinate limits')
        # A chain of individually valid scales must not collapse in composition.
        column_lengths=[math.sqrt(sum(m[r*4+c]**2 for r in range(3))) for c in range(3)]
        determinant=m[0]*(m[5]*m[10]-m[6]*m[9])-m[1]*(m[4]*m[10]-m[6]*m[8])+m[2]*(m[4]*m[9]-m[5]*m[8])
        require(all(v>=1e-8 for v in column_lengths) and abs(determinant)>1e-10*math.prod(column_lengths),'Composed node transform is degenerate')
        world[i]=m;visited+=1
        stack.extend((child,depth+1,m) for child in nodes[i].get('children',[]))
    require(visited==len(nodes),'Cyclic scene graph')
    default=index(doc.get('scene',0),scenes,'default scene');scene_nodes=[]
    instance_vertices=instance_triangles=instance_primitives=0
    for si,scene in enumerate(scenes):
        roots=scene.get('nodes',[])
        require(isinstance(roots,list) and len(roots)==len(set(roots)),'Repeated or invalid scene root references')
        for i in roots:
            index(i,nodes,'scene root');require(parents[i] is None,'Scene root must not have a parent')
        pending=list(roots);active_nodes=[];scene_triangles=0
        while pending:
            i=pending.pop();active_nodes.append(i);n=nodes[i]
            if 'mesh' in n:
                nv,nt,np=mesh_costs[n['mesh']]
                instance_vertices+=nv;instance_triangles+=nt;instance_primitives+=np;scene_triangles+=nt
                require(instance_vertices<=max_vertices and instance_triangles<=max_triangles and instance_primitives<=max_primitives,'GLB scene instancing budget exceeded (2M vertices, 1M triangles, 4096 draws across all scenes)')
            pending.extend(n.get('children',[]))
        if si==default:require(scene_triangles>0,'Default scene has no triangle geometry')
        scene_nodes.append(active_nodes)

    # Texture budgets count descriptors even when several share one embedded image.
    image_bytes=0;image_pixels=0;image_sizes=[];image_cache={}
    for im in images:
        require(im.get('mimeType') in ('image/png','image/jpeg'),'Textures must embed PNG/JPEG buffer views')
        vi=index(im.get('bufferView'),views,'image bufferView');v=views[vi]
        image_bytes+=v['byteLength'];require(image_bytes<=64*1024*1024,'GLB aggregate texture encoded bytes exceed 64 MiB')
        key=(v.get('byteOffset',0),v['byteLength'],im['mimeType'])
        if key not in image_cache:
            start=key[0];info=image_info(binary[start:start+v['byteLength']],('PNG','JPEG'))
            require(info['format']==('PNG' if im['mimeType']=='image/png' else 'JPEG'),'Embedded image MIME does not match its bytes')
            image_cache[key]=info
        info=image_cache[key];pixels=info['width']*info['height'];image_sizes.append(pixels);image_pixels+=pixels
        require(image_pixels<=max_texture_pixels,'GLB aggregate decoded texture images exceed 16 megapixels')
    require(sum(image_sizes[t['source']] for t in textures)<=max_texture_pixels,'GLB aggregate texture allocation exceeds 16 megapixels')

    # Each descriptor is scanned exactly once; primitives reuse its computed stats.
    stats={}
    for ai,(fmt,width,count,stride,start) in enumerate(specs):
        minima=[math.inf]*width;maxima=[-math.inf]*width
        decode=struct.Struct('<'+fmt*width).unpack_from
        for offset in range(start,start+count*stride,stride):
            values=decode(binary,offset)
            for j,value in enumerate(values):
                require(math.isfinite(value),'Non-finite GLB accessor data')
                if ai in positions:
                    require(abs(value)<=max_coordinate,'Mesh vertices exceed source-coordinate limits')
                    minima[j]=min(minima[j],value);maxima[j]=max(maxima[j],value)
                if ai in index_limits:require(value<index_limits[ai],'Triangle index exceeds vertex count')
        if ai in positions:
            a=accessors[ai]
            for key,actual in (('min',minima),('max',maxima)):
                if key in a:
                    declared=[struct.unpack('<f',struct.pack('<f',v))[0] for v in a[key]]
                    require(declared==actual,'POSITION accessor bounds disagree with decoded geometry')
            stats[ai]=(minima,maxima)
    mesh_bounds=[]
    for mesh in meshes:
        lows=[math.inf]*3;highs=[-math.inf]*3
        for p in mesh['primitives']:
            lo,hi=stats[p['attributes']['POSITION']]
            lows=[min(a,b) for a,b in zip(lows,lo)];highs=[max(a,b) for a,b in zip(highs,hi)]
        mesh_bounds.append((lows,highs))
    minimum=[math.inf]*3;maximum=[-math.inf]*3
    default_nodes=set(scene_nodes[default])
    for i,n in enumerate(nodes):
        if 'mesh' not in n:continue
        lo,hi=mesh_bounds[n['mesh']];m=world[i]
        for x in (lo[0],hi[0]):
            for y in (lo[1],hi[1]):
                for z in (lo[2],hi[2]):
                    point=[m[r*4]*x+m[r*4+1]*y+m[r*4+2]*z+m[r*4+3] for r in range(3)]
                    require(all(math.isfinite(v) and abs(v)<=max_coordinate for v in point),'Transformed mesh exceeds source-coordinate limits')
                    if i in default_nodes:
                        minimum=[min(a,b) for a,b in zip(minimum,point)];maximum=[max(a,b) for a,b in zip(maximum,point)]
    return {'triangles':sum(mesh_costs[nodes[i]['mesh']][1] for i in scene_nodes[default] if 'mesh' in nodes[i]),'meshes':len(meshes),'bounds':{'min':minimum,'max':maximum},'textures':len(textures)}

def validate_ply(data):
    end=data.find(b'end_header\n',0,65536)
    require(end>=0,'PLY header missing or exceeds 64 KiB')
    header=data[:end].decode('ascii');lines=header.splitlines()
    require(lines[:2]==['ply','format binary_little_endian 1.0'],'Only binary little-endian Gaussian PLY is supported')
    count=None;properties=[]
    for line in lines[2:]:
        parts=line.split()
        if not parts or parts[0] in ('comment','obj_info'):continue
        if parts[0]=='element':
            require(count is None and len(parts)==3 and parts[1]=='vertex','PLY supports vertex elements only');count=int(parts[2])
        elif parts[0]=='property':
            require(count is not None and len(parts)==3 and parts[1] in ('float','float32'),'Gaussian PLY properties must be float32');properties.append(parts[2])
        else:raise InvalidAsset('Unsupported PLY header field')
    required=['x','y','z','f_dc_0','f_dc_1','f_dc_2','opacity','scale_0','scale_1','scale_2','rot_0','rot_1','rot_2','rot_3']
    require(count is not None and 0<count<=MAX_SPLATS,'PLY limit is 1 million splats')
    require(set(required)<=set(properties),'This is a point cloud, not a Gaussian PLY: covariance, opacity and SH color properties are required')
    require(len(properties)==len(set(properties)) and len(properties)<=62,'Invalid Gaussian PLY property count')
    rest=[p for p in properties if p.startswith('f_rest_')]
    require(len(rest) in (0,9,24,45) and set(rest)=={f'f_rest_{i}' for i in range(len(rest))},'Unsupported Gaussian SH coefficients')
    require(set(properties)<=set(required+['nx','ny','nz']+rest),'Unsupported Gaussian PLY properties')
    payload=data[end+11:];require(len(payload)==count*len(properties)*4,'PLY payload length mismatch')
    centre_indices=[properties.index(axis) for axis in ('x','y','z')]
    for values in struct.iter_unpack('<'+'f'*len(properties),payload):
        require(all(math.isfinite(x) and abs(x)<1e9 for x in values),'Invalid non-finite or excessive Gaussian value')
        require(all(abs(values[i])<=MAX_SPLAT_CENTRE for i in centre_indices),'Splat centres exceed the supported range of -65504 to 65504 source units')
    return {'splats':count,'shDegree':{0:0,9:1,24:2,45:3}[len(rest)]}

def validate_spz(data):
    # SPZ v2 legacy gzip layout: https://github.com/nianticlabs/spz
    try:
        decoder=zlib.decompressobj(16+zlib.MAX_WBITS)
        raw=decoder.decompress(data,MAX_ASSET+1)
        require(len(raw)<=MAX_ASSET and decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail,'SPZ decompression exceeds 256 MiB or has trailing/truncated data')
    except zlib.error as e:raise InvalidAsset('Invalid SPZ gzip payload') from e
    require(len(raw)>=16,'Truncated SPZ header')
    magic,version,count,degree,bits,flags,reserved=struct.unpack_from('<IIIBBBB',raw)
    require(magic==0x5053474e and version==2,'Only SPZ version 2 is supported')
    require(0<count<=MAX_SPLATS and degree<=3 and bits<=24 and flags in (0,1) and reserved==0,'Unsupported SPZ schema or splat count')
    require(len(raw)==16+count*(19+3*((degree+1)**2-1)),'SPZ payload length does not match schema')
    # Version 2 positions are signed 24-bit integers followed by other fields.
    # With 8+ fractional bits their complete range already fits finite float16.
    integer_limit=MAX_SPLAT_CENTRE*(1<<bits)
    if integer_limit<1<<23:
        for offset in range(16,16+count*9,3):
            coordinate=int.from_bytes(raw[offset:offset+3],'little',signed=True)
            require(abs(coordinate)<=integer_limit,'Splat centres exceed the supported range of -65504 to 65504 source units')
    return {'splats':count,'shDegree':degree,'version':version,'fractionalBits':bits}

def validate_asset(name,data,role):
    require(0<len(data)<=MAX_ASSET,'Files must be nonempty and at most 256 MiB')
    suffix=Path(name).suffix.lower()
    try:
        if role in ('scan','collision'):
            require(suffix in ('.glb','.ply','.spz'),'Scan formats: static GLB, Gaussian PLY, SPZ v2')
            require(role!='collision' or suffix=='.glb','Collision proxy must be a GLB mesh')
            kind=suffix[1:];meta={'glb':validate_glb,'ply':validate_ply,'spz':validate_spz}[kind](data)
            return kind,{'glb':'model/gltf-binary','ply':'application/octet-stream','spz':'application/octet-stream'}[kind],meta,suffix
        require(role in ('media','thumbnail','capture'),'Unknown asset role')
        if suffix in ('.png','.jpg','.jpeg','.webp'):
            meta=image_info(data);fmt=meta['format'];return 'image',{'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp'}[fmt],meta,{ 'PNG':'.png','JPEG':'.jpg','WEBP':'.webp'}[fmt]
        require(role!='capture','Capture photos must be JPEG, PNG or WebP. Convert HEIC photos before uploading; videos are not supported.')
        require(role!='thumbnail','Thumbnails must be PNG, JPEG or WebP')
        if suffix=='.wav':
            import wave
            require(data[:4]==b'RIFF' and data[8:12]==b'WAVE','Invalid WAV signature')
            with wave.open(io.BytesIO(data)) as wav:
                require(0<wav.getnchannels()<=8 and 0<wav.getframerate()<=384000,'Invalid WAV audio')
                meta={'duration':wav.getnframes()/wav.getframerate()}
            return 'audio','audio/wav',meta,suffix
        if suffix=='.mp3':
            require(data[:3]==b'ID3' or (len(data)>=2 and data[0]==255 and data[1]&224==224),'Invalid MP3 signature')
            return 'audio','audio/mpeg',{},suffix
        if suffix=='.ogg':
            require(data[:4]==b'OggS' and (b'OpusHead' in data[:512] or b'vorbis' in data[:512]),'Only Ogg Opus/Vorbis audio supported')
            return 'audio','audio/ogg',{},suffix
        if suffix in ('.mp4','.mov'):
            require(len(data)>=24 and data[4:8]==b'ftyp' and b'mdat' in data,'Invalid MP4 container')
            return 'video','video/quicktime' if suffix=='.mov' else 'video/mp4',{},suffix
        if suffix=='.webm':
            require(data[:4]==b'\x1aE\xdf\xa3' and b'webm' in data[:128],'Invalid WebM container')
            return 'video','video/webm',{},suffix
        raise InvalidAsset('Media formats: PNG, JPEG, WebP, WAV, MP3, OGG, MP4, MOV, WebM')
    except InvalidAsset:raise
    except Exception as e:raise InvalidAsset('Malformed or unsupported asset: '+type(e).__name__) from e


def validate_reconstruction_provenance(value,assets):
    """Portable allowlist: only bounded settings and verified source identities survive."""
    require(isinstance(value,dict) and value.get('type')=='photogrammetry-gaussian','Invalid reconstruction provenance')
    images=value.get('sourceImages')
    require(isinstance(images,list) and 3<=len(images)<=200,'Invalid reconstruction source count')
    sources=[];seen=set()
    for item in images:
        require(isinstance(item,dict),'Invalid reconstruction source')
        aid=item.get('assetId');digest=item.get('sha256')
        source=assets.get(aid) if isinstance(aid,str) else None
        require(source is not None and source.role=='capture' and source.kind=='image' and source.sha256==digest and aid not in seen,'Reconstruction source identity/hash mismatch')
        sources.append({'assetId':aid,'sha256':digest});seen.add(aid)
    require(value.get('quality') in ('preview','standard','assisted'),'Invalid reconstruction quality')
    versions=value.get('versions',{});require(isinstance(versions,dict),'Invalid reconstruction engine versions')
    safe_versions={}
    for engine in ('pycolmap','brush'):
        version=versions.get(engine)
        require(version is None or (isinstance(version,str) and len(version)<=80 and all(c.isalnum() or c in '.-+_' for c in version)),'Invalid reconstruction engine version')
        safe_versions[engine]=version
    settings=value.get('settings',{});require(isinstance(settings,dict),'Invalid reconstruction settings')
    safe_settings={}
    for key in ('totalSteps','maxResolution','maxSplats','exportEvery','lpipsLossWeight','normalizedMaxResolution','mappingRandomSeed','cleanupVersion','cleanupInputSplats','cleanupRemovedSplats','cleanupSceneRadius'):
        if key in settings:
            v=settings[key]
            require(type(v) in (int,float) and math.isfinite(v) and 0<=v<=10_000_000,'Invalid reconstruction setting')
            safe_settings[key]=v
    if 'cameraCalibration' in settings:
        require(settings['cameraCalibration'] in ('auto','shared-video'),'Invalid reconstruction camera calibration')
        safe_settings['cameraCalibration']=settings['cameraCalibration']
    if 'guidedMatching' in settings:
        require(type(settings['guidedMatching']) is bool,'Invalid reconstruction guided matching setting')
        safe_settings['guidedMatching']=settings['guidedMatching']
    if value.get('quality')=='assisted':
        require(len(images)<=32,'Assisted reconstruction supports at most 32 source photos')
        from .depth_runtime import MODEL_ID, MODEL_REVISION, MODEL_SHA256, SOURCE_REVISION
        for key,expected in {'cameraModel':MODEL_ID,'modelRevision':MODEL_REVISION,'modelSha256':MODEL_SHA256,'modelSourceRevision':SOURCE_REVISION,'modelInputResolution':336,'modelInputFrames':len(images)}.items():
            require(settings.get(key)==expected,'Invalid assisted model provenance')
            safe_settings[key]=expected
    registered=value.get('registeredAssetIds',[])
    require(isinstance(registered,list) and len(registered)<=len(images) and all(isinstance(i,str) and i in seen for i in registered) and len(set(registered))==len(registered),'Invalid registered source identities')
    warnings=value.get('warnings',[])
    require(isinstance(warnings,list) and len(warnings)<=30 and all(isinstance(w,str) and len(w)<=600 for w in warnings),'Invalid reconstruction warnings')
    # Unknown metadata is deliberately dropped: archives cannot introduce file paths or execution settings.
    result={'type':'photogrammetry-gaussian','sourceImages':sources,'quality':value['quality'],'versions':safe_versions,'settings':safe_settings,'registeredAssetIds':registered,'warnings':warnings}
    if 'initialView' in value:
        from .models import Viewpoint,uid
        initial=value['initialView'];require(isinstance(initial,dict),'Invalid reconstruction initial camera pose')
        position,quaternion=initial.get('position'),initial.get('quaternion')
        require(isinstance(position,(list,tuple)) and len(position)==3 and all(type(v) in (int,float) and math.isfinite(v) and abs(v)<=MAX_SPLAT_CENTRE for v in position),'Invalid reconstruction initial camera position')
        require(isinstance(quaternion,(list,tuple)) and len(quaternion)==4 and all(type(v) in (int,float) and math.isfinite(v) for v in quaternion),'Invalid reconstruction initial camera rotation')
        try:view=Viewpoint(id=uid(),name='Reconstructed camera',position=position,quaternion=quaternion)
        except ValueError as exc:raise InvalidAsset('Invalid reconstruction initial camera pose') from exc
        # Pose only: worker/archive IDs, names, targets and other fields have no authority.
        result['initialView']={'position':list(view.position),'quaternion':list(view.quaternion)}
    if 'displayRotation' in value:
        from .models import Transform
        rotation=value['displayRotation']
        require(isinstance(rotation,(list,tuple)) and len(rotation)==3 and all(type(v) in (int,float) and math.isfinite(v) for v in rotation),'Invalid reconstruction display rotation')
        try:transform=Transform(rotation=rotation)
        except ValueError as exc:raise InvalidAsset('Invalid reconstruction display rotation') from exc
        result['displayRotation']=list(transform.rotation)
    return result


def validate_video_frame(value,assets):
    require(isinstance(value,dict),'Invalid video frame provenance')
    source=assets.get(value.get('sourceAssetId')) if isinstance(value.get('sourceAssetId'),str) else None
    require(source is not None and source.kind=='video' and source.role=='media','Video frame source must be a retained video')
    require(value.get('sourceSha256')==source.sha256,'Video frame source checksum differs')
    stamp=value.get('timestampSeconds');score=value.get('sharpness')
    require(type(stamp) in (int,float) and math.isfinite(stamp) and 0<=stamp<=300.1,'Invalid video frame timestamp')
    require(type(score) in (int,float) and math.isfinite(score) and 0<=score<=1e9,'Invalid video frame sharpness')
    return {'sourceAssetId':source.id,'sourceSha256':source.sha256,'timestampSeconds':stamp,'sharpness':score}
