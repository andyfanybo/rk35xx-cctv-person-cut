#!/usr/bin/env bash
set -euo pipefail
# Works both from a cloned source tree and as a standalone downloaded bootstrap.
installer_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$installer_dir/tools/install.py" && -f "$installer_dir/server.py" ]]; then
  exec python3 "$installer_dir/tools/install.py" "$@"
fi
download_proxy="${CCTV_GITHUB_PROXY:-}"
download_ref=main
installer_args=("$@")
while (($#)); do
  case "$1" in
    --china) download_proxy="${download_proxy:-https://ghfast.top/}"; shift ;;
    --github-proxy) download_proxy="$2"; shift 2 ;;
    --ref) download_ref="$2"; shift 2 ;;
    *) shift ;;
  esac
done
[[ "$download_ref" =~ ^[a-zA-Z0-9_.-]+$ ]] || { echo 'Invalid source ref' >&2; exit 1; }
if [[ -n "$download_proxy" ]]; then
  [[ "$download_proxy" == https://* && "$download_proxy" != *'@'* && "$download_proxy" != *'?'* && "$download_proxy" != *'#'* ]] || { echo 'Use an HTTPS public download proxy without credentials' >&2; exit 1; }
fi
download_tmp="$(mktemp -d)"
trap 'rm -rf -- "$download_tmp"' EXIT
archive_url="https://github.com/andyfanybo/rk35xx-cctv-person-cut/archive/${download_ref}.tar.gz"
download_urls=("$archive_url")
[[ -z "$download_proxy" ]] || download_urls=("${download_proxy%/}/$archive_url" "$archive_url")
download_ok=false
for url in "${download_urls[@]}"; do
  if curl --proto '=https' --proto-redir '=https' --fail --location --silent --show-error --retry 2 --connect-timeout 10 --max-time 180 "$url" -o "$download_tmp/source.tar.gz" && python3 -c 'import sys,tarfile; t=tarfile.open(sys.argv[1],"r:gz"); t.close()' "$download_tmp/source.tar.gz"; then
    download_ok=true; break
  fi
done
[[ "$download_ok" == true ]] || { echo 'Source download failed. Retry with --china or --github-proxy URL.' >&2; exit 1; }
python3 - "$download_tmp" <<'PY'
import pathlib,sys,tarfile
root=pathlib.Path(sys.argv[1]);destination=root/'source';destination.mkdir()
with tarfile.open(str(root/'source.tar.gz'),'r:gz') as archive:
    members=archive.getmembers()
    for member in members:
        path=pathlib.PurePosixPath(member.name)
        if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
            raise SystemExit('Unsafe source archive member')
    archive.extractall(str(destination),members=members)
trees=[p for p in destination.iterdir() if p.is_dir() and (p/'tools/install.py').is_file() and (p/'server.py').is_file()]
if len(trees)!=1:raise SystemExit('Source archive has an unexpected layout')
(root/'source-path').write_text(str(trees[0]))
PY
source_dir="$(cat "$download_tmp/source-path")"
python3 "$source_dir/tools/install.py" "${installer_args[@]}"
