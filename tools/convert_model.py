"""Convert a user-supplied Rockchip YOLOv8 ONNX graph for a specified SoC.

Run in a separate RKNN-Toolkit2 conversion environment, not the service venv.
No weights or calibration images are downloaded or redistributed by this script.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1024*1024), b''):
            h.update(data)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--onnx', type=Path, required=True)
    ap.add_argument('--dataset', type=Path, required=True, help='Toolkit2 INT8 calibration image list')
    ap.add_argument('--target', choices=['rk3566', 'rk3568', 'rk3576', 'rk3588'], required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--source', required=True, help='original model provenance / upstream revision')
    ap.add_argument('--model-license', required=True, help='actual upstream model license identifier or URL')
    args = ap.parse_args()
    if not args.onnx.is_file() or not args.dataset.is_file():
        ap.error('ONNX file and calibration list must exist')
    output = args.output.resolve()
    manifest = output.with_suffix('.manifest.json')
    if output.exists() or manifest.exists():
        ap.error('Output or manifest already exists; use a new name')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.stem+'.partial.rknn')
    if temporary.exists():
        ap.error('A previous partial conversion exists; inspect it before retrying')
    from rknn.api import RKNN
    try:
        from importlib.metadata import version
    except ImportError:
        from importlib_metadata import version
    toolkit_version = version('rknn-toolkit2')
    rknn = RKNN(verbose=True)
    try:
        rknn.config(mean_values=[[0, 0, 0]], std_values=[[255, 255, 255]], target_platform=args.target)
        if rknn.load_onnx(model=str(args.onnx)) != 0:
            raise RuntimeError('ONNX import failed')
        if rknn.build(do_quantization=True, dataset=str(args.dataset)) != 0:
            raise RuntimeError('INT8 build failed')
        if rknn.export_rknn(str(temporary)) != 0:
            raise RuntimeError('RKNN export failed')
        # Exclusive publication, including concurrent conversions.
        os.link(str(temporary), str(output))
        temporary.unlink()
        metadata = {'target_platform': args.target, 'toolkit_version': toolkit_version,
                    'model_family': 'rockchip-yolov8-9-output-coco80',
                    'input_size': [640, 640], 'input_color': 'RGB', 'person_class': 0,
                    'mean': [0, 0, 0], 'std': [255, 255, 255],
                    'source': args.source, 'model_license': args.model_license,
                    'onnx_sha256': digest(args.onnx), 'rknn_sha256': digest(output),
                    'runtime_validated': False}
        with manifest.open('x') as stream:
            json.dump(metadata, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        print('Converted {}. Run doctor --hardware on the target board before use.'.format(output.name))
    finally:
        rknn.release()


if __name__ == '__main__':
    main()
