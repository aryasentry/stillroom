"""Small isolated model entry point; only camera arrays cross into reconstruction."""
import json
import os
from pathlib import Path
import sys
import threading
import time


def validate_prediction(prediction, count):
    import numpy as np
    if type(count) is not int or not 3<=count<=32: raise ValueError('Invalid model input count.')
    try:
        ex=np.asarray(prediction['extrinsics'],dtype=float);k=np.asarray(prediction['intrinsics'],dtype=float);size=np.asarray(prediction['imageSize'],dtype=float)
    except (KeyError,ValueError,TypeError) as exc: raise ValueError('Invalid model prediction.') from exc
    if ex.shape!=(count,3,4) or k.shape!=(count,3,3) or size.shape!=(2,): raise ValueError('Invalid camera array dimensions.')
    if not all(np.isfinite(v).all() for v in (ex,k,size)) or np.any(size<14) or np.any(size>336) or np.any(size!=np.round(size)): raise ValueError('Invalid model prediction dimensions or values.')
    r=ex[:,:,:3]
    if not np.allclose(r.transpose(0,2,1)@r,np.eye(3),atol=.002) or not np.allclose(np.linalg.det(r),1,atol=.002) or np.max(np.abs(ex[:,:,3]))>1e5: raise ValueError('Invalid estimated camera pose.')
    if np.any(k[:,[0,1],[0,1]]<1) or np.any(k[:,[0,1],[0,1]]>1e5) or not np.allclose(k[:,2,:],[0,0,1]) or not np.allclose(k[:,0,1],0) or not np.allclose(k[:,1,0],0): raise ValueError('Invalid estimated camera intrinsics.')
    if np.any(k[:,0,2]<0) or np.any(k[:,0,2]>size[0]) or np.any(k[:,1,2]<0) or np.any(k[:,1,2]>size[1]): raise ValueError('Invalid camera principal point.')
    return {'extrinsics':ex,'intrinsics':k,'imageSize':size.astype(int)}


def infer(paths):
    import numpy as np
    import torch
    from safetensors.torch import load_file
    from safetensors import safe_open
    from depth_anything_3.cfg import create_object,load_config
    from depth_anything_3.utils.io.input_processor import InputProcessor
    from depth_anything_3.utils.io.output_processor import OutputProcessor
    from .depth_runtime import TOOL_ROOT
    if not 3<=len(paths)<=32: raise ValueError('Choose 3–32 photos.')
    model=create_object(load_config(str(TOOL_ROOT/'source/src/depth_anything_3/configs/da3-base.yaml')))
    weights=load_file(str(TOOL_ROOT/'model.safetensors'))
    with safe_open(str(TOOL_ROOT/'model.safetensors'),framework='pt') as checkpoint:
        for alias,original in (checkpoint.metadata() or {}).items():
            if original in weights: weights[alias]=weights[original]
    model.load_state_dict({k.removeprefix('model.'):v for k,v in weights.items()},strict=True);del weights
    device='mps' if torch.backends.mps.is_available() else 'cuda' if torch.cuda.is_available() else 'cpu'
    torch.set_num_threads(min(8,max(1,(os.cpu_count() or 2)-1)))
    model.eval().to(device)
    x,_,_=InputProcessor()(paths,process_res=336,num_workers=1)
    x=x[None].to(device)
    from contextlib import nullcontext
    with torch.inference_mode(), (torch.autocast(device_type=device,dtype=torch.float16) if device!='cpu' else nullcontext()):
        raw=model(x,ref_view_strategy='saddle_balanced')
    pred=OutputProcessor()(raw)
    return validate_prediction({'extrinsics':pred.extrinsics,'intrinsics':pred.intrinsics,'imageSize':np.array([x.shape[-1],x.shape[-2]])},len(paths))


def main():
    # A killed reconstruction must not leave this expensive child running on Windows.
    from .reconstruction_worker import _watch_parent
    threading.Thread(target=_watch_parent,args=(os.getppid(),),daemon=True).start()
    request=json.loads(Path(sys.argv[1]).read_text())
    import numpy as np
    np.savez(Path(request['output']),**infer(request['images']))


if __name__=='__main__': main()
