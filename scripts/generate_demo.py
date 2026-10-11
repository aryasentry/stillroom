"""Deterministic CC0 fixtures. Modelled geometry, NOT a captured room."""
from pathlib import Path
import gzip, io, json, math, random, struct, wave
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'fixtures'
OUT.mkdir(exist_ok=True)
rng = random.Random(417)

def image_bytes(image):
    buf = io.BytesIO(); image.save(buf, format='PNG'); return buf.getvalue()

def texture(kind):
    im = Image.new('RGB', (256,256)); px=im.load()
    for y in range(256):
        for x in range(256):
            n = rng.randint(-5,5)
            if kind=='wood':
                seam = x%64<2 or (y+(x//64%2)*128)%256<2
                v = -22 if seam else int(5*math.sin(y*.36 + x*.024)) + n
                px[x,y] = (max(0,151+v),max(0,111+v),max(0,76+v))
            elif kind=='rug':
                border=x<12 or x>244 or y<12 or y>244
                v=10 if (x+y)%4==0 else n
                px[x,y]=(155+v,105+v,80+v) if border else (201+v,174+v,137+v)
            else: px[x,y]=(225+n,219+n,207+n)
    return image_bytes(im)

blob = bytearray(); views=[]; accessors=[]; materials=[]; images=[]; textures=[]; meshes=[]; nodes=[]
def add_bytes(data, target=None):
    while len(blob)%4:blob.append(0)
    offset=len(blob);blob.extend(data);v={'buffer':0,'byteOffset':offset,'byteLength':len(data)}
    if target:v['target']=target
    views.append(v);return len(views)-1
def accessor(data, typ, n, component=5126, minimum=None, maximum=None):
    values=[v for row in data for v in row] if isinstance(data[0],(tuple,list)) else data
    raw=struct.pack('<'+('f' if component==5126 else 'H')*len(values),*values)
    a={'bufferView':add_bytes(raw,34963 if component!=5126 else 34962),'componentType':component,'count':n,'type':typ}
    if minimum is not None:a.update(min=minimum,max=maximum)
    accessors.append(a);return len(accessors)-1
def material(name,color,tex=None,metal=0,rough=0.82,emission=None):
    pbr={'baseColorFactor':[*color,1],'metallicFactor':metal,'roughnessFactor':rough}
    if tex:
        images.append({'bufferView':add_bytes(tex),'mimeType':'image/png'});textures.append({'source':len(images)-1,'sampler':0});pbr['baseColorTexture']={'index':len(textures)-1}
    mat={'name':name,'pbrMetallicRoughness':pbr,'doubleSided':False}
    if emission:mat['emissiveFactor']=emission
    materials.append(mat);return len(materials)-1

wood=material('Oak · embedded texture',(1,1,1),texture('wood'))
wall=material('Warm plaster · embedded texture',(1,1,1),texture('wall'))
rug=material('Woven linen · embedded texture',(1,1,1),texture('rug'))
walnut=material('Walnut',(.28,.17,.105)); cream=material('Cream',(.79,.75,.65)); dark=material('Charcoal',(.07,.09,.085)); green=material('Olive',(.26,.32,.20)); clay=material('Terracotta',(.58,.27,.16)); brass=material('Brass',(.55,.39,.17),metal=.65,rough=.35); sky=material('Synthetic pale sky',(.64,.79,.82),emission=[.15,.22,.24]); paper=material('Paper',(.91,.87,.75)); screen=material('Screen',(.19,.28,.27),emission=[.02,.04,.035]); leaf=material('Leaves',(.15,.26,.12)); leaf2=material('Light leaves',(.29,.38,.17))

faces=[([1,0,0],[(.5,-.5,-.5),(.5,.5,-.5),(.5,.5,.5),(.5,-.5,.5)]),([-1,0,0],[(-.5,-.5,.5),(-.5,.5,.5),(-.5,.5,-.5),(-.5,-.5,-.5)]),([0,1,0],[(-.5,.5,.5),(.5,.5,.5),(.5,.5,-.5),(-.5,.5,-.5)]),([0,-1,0],[(-.5,-.5,-.5),(.5,-.5,-.5),(.5,-.5,.5),(-.5,-.5,.5)]),([0,0,1],[(.5,-.5,.5),(.5,.5,.5),(-.5,.5,.5),(-.5,-.5,.5)]),([0,0,-1],[(-.5,-.5,-.5),(-.5,.5,-.5),(.5,.5,-.5),(.5,-.5,-.5)])]
pos=[];normal=[];uv=[];ind=[]
for norm,verts in faces:
    start=len(pos);pos+=verts;normal += [norm]*4;uv += [(1,1),(1,0),(0,0),(0,1)];ind += [start,start+1,start+2,start,start+2,start+3]
cube={'POSITION':accessor(pos,'VEC3',24,minimum=[-.5]*3,maximum=[.5]*3),'NORMAL':accessor(normal,'VEC3',24),'TEXCOORD_0':accessor(uv,'VEC2',24)};cube_indices=accessor(ind,'SCALAR',36,5123)
mesh_cache={}
boxes=[]
def box(name,center,size,mat,rotate=None):
    if mat not in mesh_cache:
        meshes.append({'name':materials[mat]['name'],'primitives':[{'attributes':cube,'indices':cube_indices,'material':mat,'mode':4}]});mesh_cache[mat]=len(meshes)-1
    n={'name':name,'mesh':mesh_cache[mat],'translation':center,'scale':size}
    if rotate:n['rotation']=rotate
    nodes.append(n);boxes.append((center,size,mat));return len(nodes)-1

box('Floor · 6 × 5 metres',[0,-.10,0],[6,.2,5],wood)
box('Back wall',[0,1.5,-2.55],[6,3,.1],wall)
box('Left wall',[-3.05,1.5,0],[.1,3,5],wall)
box('Right window wall lower',[3.05,.55,0],[.1,1.1,5],wall)
box('Right wall upper',[3.05,2.85,0],[.1,.3,5],wall)
box('Right wall rear',[3.05,1.9,-2.2],[.1,1.6,.6],wall)
box('Right wall front',[3.05,1.9,1.45],[.1,1.6,2.1],wall)
box('Window view · synthetic flat colour',[3.1,1.9,-.75],[.025,1.6,2.3],sky)
for z in [-1.91,-.75,.41]:box('Window frame',[3,1.9,z],[.16,1.65,.045],paper)
for y in [1.12,1.9,2.68]:box('Window frame',[3,y,-.75],[.16,.045,2.37],paper)
box('Windowsill',[2.9,1.1,-.75],[.42,.08,2.5],wood)
box('Linen rug',[.2,.016,.4],[3.6,.022,2.6],rug)
box('Desk · 1.8m width',[-1.7,.78,-1.5],[1.8,.12,.85],wood)
for x in [-2.45,-.95]:
    for z in [-1.8,-1.2]:box('Desk leg',[x,.35,z],[.075,.7,.075],walnut)
box('Notebook',[-1.45,.852,-1.48],[.39,.025,.3],paper)
box('Notebook binding',[-1.65,.86,-1.48],[.02,.032,.3],dark)
box('Laptop base',[-2,.855,-1.6],[.5,.025,.32],dark)
box('Laptop screen',[-2,1.04,-1.75],[.5,.35,.025],dark)
box('Laptop display',[-2,1.04,-1.732],[.445,.295,.008],screen)
box('Chair seat',[-1.7,.46,-.6],[.5,.1,.5],green)
box('Chair back',[-1.7,.78,-.32],[.5,.6,.07],green)
for x in [-1.9,-1.5]:
    for z in [-.8,-.4]:box('Chair leg',[x,.22,z],[.045,.44,.045],walnut)

box('Sofa base',[.85,.29,-1.72],[2.1,.45,.75],cream)
box('Sofa back',[.85,.78,-2.08],[2.1,.8,.18],cream)
for x in [-.15,1.85]:box('Sofa arm',[x,.62,-1.68],[.18,.58,.85],cream)
for x in [.36,1.32]:box('Linen seat cushion',[x,.56,-1.63],[.88,.17,.63],paper)
box('Olive cushion',[.1,.8,-1.84],[.42,.43,.17],green, [0,0,.07,.99755])
box('Clay cushion',[1.48,.83,-1.84],[.44,.46,.17],clay,[0,0,-.10,.994987])
box('Coffee table',[.45,.39,.23],[1.25,.09,.7],walnut)
for x in [-.06,.96]:
    for z in [-.02,.49]:box('Coffee table leg',[x,.18,z],[.055,.36,.055],walnut)
box('Book on table',[.35,.454,.18],[.31,.038,.39],green)
box('Book pages',[.35,.469,.18],[.285,.018,.37],paper)

box('Bookshelf left',[-2.83,1.13,.95],[.09,2.2,1.08],walnut)
for y in [.15,.68,1.21,1.74,2.24]:box('Bookshelf board',[-2.72,y,.95],[.42,.065,1.08],wood)
for z in [.41,1.49]:box('Bookshelf upright',[-2.72,1.2,z],[.42,2.15,.06],wood)
for tier,y in enumerate([.70,1.23,1.76]):
    for j in range(7):
        z=.53+j*.12;h=rng.uniform(.25,.39);mat=[green,clay,cream,paper,dark][(j+tier)%5]
        box('Book spine %s-%s'%(tier,j),[-2.55,y+h/2,z],[.18,h,.075],mat)

def label(text,name,center,size):
    im=Image.new('RGB',(512,256),(230,223,207));d=ImageDraw.Draw(im)
    font=ImageFont.load_default(size=36)
    d.text((28,85),text,fill=(58,67,56),font=font)
    mat=material(name,(1,1,1),image_bytes(im));box(name,center,size,mat)

box('Frame above desk',[-1.7,1.92,-2.47],[1.12,.72,.06],walnut)
label('A place to begin.','Labelled desk landmark',[-1.7,1.92,-2.43],[1.03,.63,.01])
box('Frame above sofa',[.92,1.98,-2.47],[1.15,.95,.06],walnut)
art=Image.new('RGB',(400,320),(209,204,178));d=ImageDraw.Draw(art);d.ellipse((170,40,260,130),fill=(194,128,83));d.polygon([(0,320),(0,245),(130,140),(300,320)],fill=(111,128,92));d.polygon([(120,320),(340,130),(400,230),(400,320)],fill=(68,91,77));artmat=material('Synthetic landscape artwork',(1,1,1),image_bytes(art));box('Landscape print',[.92,1.98,-2.43],[1.04,.84,.01],artmat)

# Low-poly ellipsoids make the plant clearly modelled, with analytic bounds.
def ellipsoid(name,center,size,mat):
    ps=[];ns=[];us=[];indices=[];rings=10;segments=12
    for i in range(rings+1):
        v=i/rings;phi=v*math.pi
        for j in range(segments+1):
            u=j/segments;t=u*math.pi*2;p=(math.sin(phi)*math.cos(t)*.5,math.cos(phi)*.5,math.sin(phi)*math.sin(t)*.5);ps.append(p);ns.append(tuple(n*2 for n in p));us.append((u,v))
    for i in range(rings):
        for j in range(segments):
            a=i*(segments+1)+j;b=a+segments+1;indices.extend([a,b,a+1,b,b+1,a+1])
    attrs={'POSITION':accessor(ps,'VEC3',len(ps),minimum=[-.5]*3,maximum=[.5]*3),'NORMAL':accessor(ns,'VEC3',len(ns)),'TEXCOORD_0':accessor(us,'VEC2',len(us))}
    meshes.append({'name':name,'primitives':[{'attributes':attrs,'indices':accessor(indices,'SCALAR',len(indices),5123),'material':mat}]});nodes.append({'name':name,'mesh':len(meshes)-1,'translation':center,'scale':size})
ellipsoid('Clay plant pot',[2.3,.25,1.48],[.5,.5,.5],clay)
box('Plant stem',[2.3,.94,1.48],[.035,1.4,.035],walnut)
for j in range(11):
    theta=j*2.4;y=.65+j*.08;ellipsoid('Plant leaf',[2.3+.22*math.cos(theta),y,1.48+.22*math.sin(theta)],[.45,.18,.3],leaf if j%2 else leaf2)
box('Standing lamp base',[2.45,.035,-1.85],[.36,.07,.36],dark)
box('Standing lamp pole',[2.45,.9,-1.85],[.04,1.8,.04],brass)
box('Linen lampshade',[2.45,1.72,-1.85],[.47,.42,.47],paper)

gltf={'asset':{'version':'2.0','generator':'Stillroom deterministic synthetic fixture; CC0'},'scene':0,'scenes':[{'name':'Synthetic afternoon room — 6m × 5m','nodes':list(range(len(nodes)))}],'nodes':nodes,'meshes':meshes,'materials':materials,'textures':textures,'images':images,'samplers':[{'magFilter':9729,'minFilter':9987,'wrapS':10497,'wrapT':10497}],'accessors':accessors,'bufferViews':views,'buffers':[{'byteLength':len(blob)}]}
js=json.dumps(gltf,separators=(',',':')).encode();js+=b' '*((-len(js))%4);blob+=b'\0'*((-len(blob))%4)
glb=struct.pack('<4sII',b'glTF',2,12+8+len(js)+8+len(blob))+struct.pack('<I4s',len(js),b'JSON')+js+struct.pack('<I4s',len(blob),b'BIN\0')+blob
(OUT/'synthetic-room.glb').write_bytes(glb)

# Surface-sampled Gaussian fixture. It tests splat rendering, never measured solidity.
splats=[]
for center,size,mat in boxes:
    color=materials[mat]['pbrMetallicRoughness']['baseColorFactor'][:3]
    if mat==wood:color=[.59,.43,.29]
    elif mat==wall:color=[.78,.75,.68]
    elif mat==rug:color=[.65,.49,.33]
    for axis in range(3):
        others=[i for i in range(3) if i!=axis];a,b=others
        na=max(1,math.ceil(size[a]/.105));nb=max(1,math.ceil(size[b]/.105))
        for sign in [-1,1]:
            for ia in range(na):
                for ib in range(nb):
                    p=list(center);p[axis]+=sign*size[axis]/2;p[a]+=(ia+.5)/na*size[a]-size[a]/2;p[b]+=(ib+.5)/nb*size[b]-size[b]/2
                    splats.append((p,color))
fields=['x','y','z','f_dc_0','f_dc_1','f_dc_2','opacity','scale_0','scale_1','scale_2','rot_0','rot_1','rot_2','rot_3']
header=('ply\nformat binary_little_endian 1.0\ncomment Stillroom synthetic Gaussian fixture CC0\nelement vertex '+str(len(splats))+'\n'+''.join('property float '+f+'\n' for f in fields)+'end_header\n').encode()
ply=bytearray(header)
for p,color in splats:ply.extend(struct.pack('<14f',*p,*[(c-.5)/.2820947918 for c in color],4.0,*([math.log(.065)]*3),1,0,0,0))
(OUT/'synthetic-room.ply').write_bytes(ply)
n=len(splats);payload=bytearray(struct.pack('<4sIIBBBB',b'NGSP',2,n,0,12,0,0))
for p,_ in splats:
    for v in p:payload.extend((round(v*4096)&0xffffff).to_bytes(3,'little'))
payload.extend(bytes([250])*n)
for _,color in splats:payload.extend(bytes(max(0,min(255,round(127.5+((c-.5)/.2820947918)*.15*255))) for c in color))
payload.extend(bytes([round((math.log(.065)+10)*16)])*(3*n));payload.extend(bytes([128,128,128])*n)
(OUT/'synthetic-room.spz').write_bytes(gzip.compress(bytes(payload),mtime=0))
(OUT/'unsupported-pointcloud.ply').write_text('ply\nformat ascii 1.0\nelement vertex 1\nproperty float x\nproperty float y\nproperty float z\nend_header\n0 0 0\n')

photo=Image.new('RGB',(1000,700),(224,215,193));d=ImageDraw.Draw(photo)
d.rectangle((0,430,1000,700),fill=(155,113,75));d.rectangle((150,240,660,440),fill=(57,71,65));d.rectangle((175,262,635,414),fill=(126,151,133));d.rectangle((520,485,795,625),fill=(237,226,197));d.line((560,520,755,520),fill=(109,107,83),width=4);d.line((560,550,710,550),fill=(109,107,83),width=4)
font=ImageFont.load_default(size=38)
d.text((55,50),'An afternoon, held here.',fill=(67,72,57),font=font);d.text((55,640),'SYNTHETIC DEMO IMAGE',fill=(245,235,215),font=ImageFont.load_default(size=20))
photo.save(OUT/'memory-photo.png')
with wave.open(str(OUT/'memory-audio.wav'),'wb') as w:
    rate=22050;w.setnchannels(1);w.setsampwidth(2);w.setframerate(rate)
    samples=[]
    for i in range(rate*3):
        t=i/rate;envelope=max(0,1-t/3)**2;v=.12*math.sin(2*math.pi*440*t)*envelope+.07*math.sin(2*math.pi*660*t)*envelope;samples.append(round(v*32767))
    w.writeframes(struct.pack('<'+'h'*len(samples),*samples))
(OUT/'measurements.json').write_text(json.dumps({'synthetic':True,'licence':'CC0-1.0','source':'Procedurally authored by scripts/generate_demo.py; not a real scan','dimensionsMetres':{'width':6,'depth':5,'height':3},'floorY':0,'entrance':{'position':[0,1.65,2],'quaternion':[0,0,0,1]},'referenceDistances':[{'a':[-3,0,-2.5],'b':[3,0,-2.5],'metres':6},{'a':[-3,0,-2.5],'b':[-3,0,2.5],'metres':5}],'anchors':{'desk':[-1.7,.845,-1.5],'window':[2.9,1.15,-.75],'shelf':[-2.51,1.5,.95]},'splats':n},indent=2)+'\n')
print(json.dumps({'glbBytes':len(glb),'objects':len(nodes),'splats':n,'plyBytes':len(ply),'spzBytes':(OUT/'synthetic-room.spz').stat().st_size},indent=2))
