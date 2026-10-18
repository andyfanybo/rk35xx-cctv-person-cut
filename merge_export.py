"""Stream-copy export entry point for the portable quick engine."""
import argparse
from pathlib import Path
from configuration import settings
from quick_pipeline import merge


def run(job_path, export_id, publish=False):
    path = Path(job_path).resolve(strict=True)
    if settings.work_dir not in path.parents:
        raise ValueError('Export job must belong to the configured SSD work directory')
    return merge(path, export_id, publish)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--job', required=True)
    ap.add_argument('--export-id', required=True)
    ap.add_argument('--publish', action='store_true')
    args = ap.parse_args()
    run(args.job, args.export_id, args.publish)
