"""Optional model manifest checks before entering a native runtime."""
import hashlib
import json
from pathlib import Path


def detected_soc():
    try:
        value=Path('/proc/device-tree/compatible').read_bytes()
    except OSError:
        return None
    return next((soc for soc in ('rk3566','rk3568','rk3576','rk3588') if soc.encode() in value),None)


def validate_manifest(model, manifest, soc=None):
    data=json.loads(Path(manifest).read_text())
    if not isinstance(data,dict):
        raise ValueError('Model manifest must be an object')
    soc=soc or detected_soc()
    target=data.get('target_platform')
    if target not in ('rk3566','rk3568','rk3576','rk3588'):
        raise ValueError('Unknown model target platform')
    compatible=target==soc or {target,soc}=={'rk3566','rk3568'}
    if soc and not compatible:
        raise ValueError('Model target {} does not match {}'.format(target,soc))
    expected={'model_family':'rockchip-yolov8-9-output-coco80','input_size':[640,640],
              'input_color':'RGB','person_class':0,'mean':[0,0,0],'std':[255,255,255]}
    for key,value in expected.items():
        if data.get(key)!=value:
            raise ValueError('Unsupported model contract: '+key)
    wanted=data.get('rknn_sha256')
    if not isinstance(wanted,str) or len(wanted)!=64:
        raise ValueError('Model manifest needs rknn_sha256')
    digest=hashlib.sha256()
    with Path(model).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            digest.update(block)
    if digest.hexdigest()!=wanted:
        raise ValueError('Model contents do not match the manifest')
    return data
