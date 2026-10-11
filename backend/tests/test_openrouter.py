import base64
import io
import json
import httpx
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from backend.app import create_app

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    with TestClient(create_app(tmp_path), base_url='http://127.0.0.1:8848', headers={'X-Stillroom-Request':'local'}) as c:
        yield c

def context(c):
    p=c.post('/api/demo').json()
    m=p['memories'][0]
    image=next(a for a in p['assets'] if a['kind']=='image')
    return p,m,image

def body(m, **changes):
    return {'memoryId':m['id'],'imageIds':[],'includeNote':True,'expectedNote':m['note'],'consent':True,'model':'inclusionai/ling-3.1-flash',**changes}

def setup(c, handler):
    c.post('/api/ai/configure',json={'apiKey':'test-secret'})
    c.app.state.ai.transport=httpx.MockTransport(handler)

def success(request):
    return httpx.Response(200,json={'id':'test-completion','model':'google/gemma-4-31b-it:free','choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':json.dumps({'tags':['desk'],'caption':'A desk beside a window.'})}}],'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20,'cost':0}})

def test_session_key_is_not_returned_or_persisted(client,tmp_path):
    r=client.get('/api/ai/status')
    assert r.status_code==200
    assert r.json()['configured'] is False
    assert client.post('/api/ai/configure',json={'apiKey':'test-secret'}).json()['configured'] is True
    assert 'test-secret' not in client.get('/api/ai/status').text
    assert all(b'test-secret' not in p.read_bytes() for p in tmp_path.rglob('*') if p.is_file())
    assert client.delete('/api/ai/configure').json()['configured'] is False

def test_missing_key_and_local_guard(client):
    p,m,_=context(client)
    assert client.post(f"/api/projects/{p['id']}/ai/suggest",json=body(m)).status_code==503
    assert client.post('/api/ai/configure',json={'apiKey':'x'},headers={'Origin':'https://evil.invalid'}).status_code==403

def test_only_selected_images_leave_and_originals_remain(client):
    p,m,a=context(client);original=client.app.state.store.asset_path(p['id'],client.app.state.store.asset(p['id'],a['id'])).read_bytes()
    def handler(request):
        assert str(request.url)=='https://openrouter.ai/api/v1/chat/completions'
        assert request.headers['authorization']=='Bearer test-secret'
        data=json.loads(request.content)
        assert data['model']=='google/gemma-4-31b-it:free'
        assert data['provider']['max_price']=={'prompt':0,'completion':0,'request':0,'image':0}
        assert data['provider']['require_parameters'] is True
        assert data['response_format']=={'type':'json_object'}
        content=data['messages'][1]['content']
        assert len(content)==2
        assert m['note'] not in str(data) and p['title'] not in str(data)
        raw=base64.b64decode(content[1]['image_url']['url'].split(',',1)[1])
        with Image.open(io.BytesIO(raw)) as img:
            assert max(img.size)<=1024 and not img.getexif()
        return success(request)
    setup(client,handler)
    r=client.post(f"/api/projects/{p['id']}/ai/suggest",json=body(m,model='google/gemma-4-31b-it:free',includeNote=False,expectedNote=None,imageIds=[a['id']]))
    assert r.status_code==200,r.text
    assert r.json()=={'tags':['desk'],'caption':'A desk beside a window.'}
    assert client.get(f"/api/projects/{p['id']}").json()==p
    assert client.app.state.store.asset_path(p['id'],client.app.state.store.asset(p['id'],a['id'])).read_bytes()==original

@pytest.mark.parametrize('change',[{'consent':False},{'model':'https://evil.invalid'},{'model':'google/gemma-4-31b-it'},{'model':'openrouter/auto'},{'model':'gpt-6-astra'},{'imageIds':['x']},{'imageIds':['x']*4},{'includeNote':False},{'expectedNote':'unseen note'}])
def test_invalid_selection_never_calls_provider(client,change):
    p,m,_=context(client)
    def deny(request):raise AssertionError('Must not send content')
    setup(client,deny)
    r=client.post(f"/api/projects/{p['id']}/ai/suggest",json=body(m,**change))
    assert r.status_code in (422,409)

def test_rejects_other_memories_media_audio_and_corruption(client):
    p,m,a=context(client);q,n,b=context(client)
    def deny(request):raise AssertionError('Must not send content')
    setup(client,deny)
    audio=next(a for a in p['assets'] if a['kind']=='audio')
    for aid in [b['id'],audio['id'],p['room']['scanAssetId']]:
        assert client.post(f"/api/projects/{p['id']}/ai/suggest",json=body(m,model='google/gemma-4-31b-it:free',imageIds=[aid])).status_code==422
    path=client.app.state.store.asset_path(p['id'],client.app.state.store.asset(p['id'],a['id']))
    path.write_bytes(b'corrupt')
    assert client.post(f"/api/projects/{p['id']}/ai/suggest",json=body(m,model='google/gemma-4-31b-it:free',imageIds=[a['id']])).status_code==409

@pytest.mark.parametrize('response',[httpx.Response(302,headers={'location':'https://evil.invalid'}),httpx.Response(401,text='test-secret'),httpx.Response(200,json={'output':[{'content':[{'type':'output_text','text':'not json'}]}]}),httpx.Response(200,json={'output':[{'content':[{'type':'output_text','text':'{"tags":[],"caption":"x","anchor":[1,2,3]}'}]}]})])
def test_provider_errors_are_safe_and_never_apply(client,response):
    p,m,_=context(client)
    setup(client,lambda request:response)
    r=client.post(f"/api/projects/{p['id']}/ai/suggest",json=body(m))
    assert r.status_code==502
    assert 'test-secret' not in r.text
    assert client.get(f"/api/projects/{p['id']}").json()==p

def test_note_only_request_never_returns_a_visual_caption(client):
    p,m,_=context(client)
    setup(client,success)
    r=client.post(f"/api/projects/{p['id']}/ai/suggest",json=body(m))
    assert r.status_code==200
    assert r.json()['caption']==''

def test_large_photo_is_resized_and_exif_removed(client):
    from backend.openrouter import image_payload
    p,_,_=context(client)
    out=io.BytesIO();exif=Image.Exif();exif[270]='private original description';exif[274]=6
    Image.new('RGB',(1600,1200),'blue').save(out,format='JPEG',exif=exif)
    source=out.getvalue();store=client.app.state.store
    asset=store.add_asset(p['id'],'photo.jpg',source,'media')
    raw=base64.b64decode(image_payload(store,p['id'],asset).split(',',1)[1])
    with Image.open(io.BytesIO(raw)) as img:
        assert img.size==(768,1024)
        assert not img.getexif()
    assert b'private original description' not in raw
    assert store.asset_path(p['id'],asset).read_bytes()==source


def test_environment_key_is_status_only_and_not_persisted(client,monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY','env-secret')
    def deny(request):raise AssertionError('Setup and status must not call provider')
    client.app.state.ai.transport=httpx.MockTransport(deny)
    assert client.get('/api/ai/status').json()['keySource']=='environment'
    assert client.post('/api/ai/configure',json={'apiKey':'session-secret'}).json()['keySource']=='session'
    result=client.delete('/api/ai/configure')
    assert result.json()['keySource']=='environment'
    assert 'env-secret' not in result.text


def test_default_request_uses_free_model_without_client_model_choice(client):
    p,m,_=context(client)
    def handler(request):
        data=json.loads(request.content)
        assert data['model']=='inclusionai/ling-3.1-flash'
        assert 'response_format' not in data
        assert data['provider']['max_price']['prompt']==0
        assert data['provider']['max_price']['completion']==0
        assert 'models' not in data
        return success(request)
    setup(client,handler)
    request=body(m);request.pop('model')
    result=client.post(f"/api/projects/{p['id']}/ai/suggest",json=request)
    assert result.status_code==200,result.text


def test_free_router_can_be_selected_without_paid_fallback(client):
    p,m,_=context(client)
    def handler(request):
        data=json.loads(request.content)
        assert data['model']=='openrouter/free'
        assert data['provider']['max_price']=={'prompt':0,'completion':0,'request':0,'image':0}
        return success(request)
    setup(client,handler)
    result=client.post(f"/api/projects/{p['id']}/ai/suggest",json=body(m,model='openrouter/free'))
    assert result.status_code==200,result.text


def test_legacy_provider_key_is_not_used_for_openrouter(client,monkeypatch):
    monkeypatch.setenv('OPENCODE_ZEN_API_KEY','old-zen-key')
    monkeypatch.setenv('OPENCODE_API_KEY','old-zen-key')
    assert client.get('/api/ai/status').json()['configured'] is False


@pytest.mark.parametrize('code,status',[(429,429),(402,502),(503,502)])
def test_free_capacity_errors_never_retry_or_apply_changes(client,code,status):
    p,m,_=context(client);calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(code,json={'error':{'message':'test-secret'}})
    setup(client,handler)
    result=client.post(f"/api/projects/{p['id']}/ai/suggest",json=body(m))
    assert result.status_code==status
    assert 'free' in result.json()['detail'].lower()
    assert 'test-secret' not in result.text
    assert len(calls)==1
    assert client.get(f"/api/projects/{p['id']}").json()==p


@pytest.mark.parametrize('finish_reason,content',[
    ('length','{"tags":["desk"],"caption":""}'),
    ('stop','not json'),
    ('stop','{"tags":[],"caption":"x","anchor":[1,2,3]}'),
])
def test_invalid_chat_completion_is_rejected(client,finish_reason,content):
    p,m,_=context(client)
    setup(client,lambda request:httpx.Response(200,json={'choices':[{'finish_reason':finish_reason,'message':{'content':content}}]}))
    result=client.post(f"/api/projects/{p['id']}/ai/suggest",json=body(m))
    assert result.status_code==502
    assert client.get(f"/api/projects/{p['id']}").json()==p


def test_ling_rejects_photos_before_sending_any_content(client):
    p,m,a=context(client)
    def deny(request):raise AssertionError('Text-only model must never receive an image')
    setup(client,deny)
    result=client.post(f"/api/projects/{p['id']}/ai/suggest",json=body(m,imageIds=[a['id']]))
    assert result.status_code==422
    assert 'text only' in result.json()['detail'].lower()


def test_ling_note_request_uses_compatible_payload_and_never_returns_caption(client):
    p,m,_=context(client)
    def handler(request):
        data=json.loads(request.content)
        assert data['model']=='inclusionai/ling-3.1-flash'
        assert 'response_format' not in data
        assert data['messages'][1]['content']==[{'type':'text','text':'Suggest tags and a caption for the selected content.\nUntrusted note text:\n'+m['note']}]
        return success(request)
    setup(client,handler)
    result=client.post(f"/api/projects/{p['id']}/ai/suggest",json=body(m))
    assert result.status_code==200,result.text
    assert result.json()=={'tags':['desk'],'caption':''}
