import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from model_contract import validate_manifest


class ModelContractTests(unittest.TestCase):
    def test_wrong_chip_and_changed_model_are_rejected_before_native_loading(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);model=root/'person.rknn';model.write_bytes(b'model')
            data={'target_platform':'rk3568','model_family':'rockchip-yolov8-9-output-coco80',
                  'input_size':[640,640],'input_color':'RGB','person_class':0,'mean':[0,0,0],
                  'std':[255,255,255],'rknn_sha256':hashlib.sha256(b'model').hexdigest()}
            manifest=root/'model.json';manifest.write_text(json.dumps(data))
            validate_manifest(model,manifest,'rk3566')
            with self.assertRaisesRegex(ValueError,'does not match'):
                validate_manifest(model,manifest,'rk3588')
            model.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'contents'):
                validate_manifest(model,manifest,'rk3568')
