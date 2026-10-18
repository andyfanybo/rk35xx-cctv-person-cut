# 一键安装与网络选择

`install.sh` 同时支持从 GitHub 下载后运行，以及在克隆的源码目录里运行。源码目录内执行会直接使用本地源码，不重复下载。

## 安装过程

1. 确认 SSD 工作路径，原片、缓存和成品目录互不包含。
2. 下载公开源码或使用本地源码；拒绝覆盖已有安装目录。
3. 新建 Python 虚拟环境、配置和输出目录，按需安装 Python 包。
4. 启动 Web 面板，输出访问端口及 `start.sh`、`stop.sh` 路径。
5. 在 Web 新建任务中选择设备目录；配置合法、匹配的模型及运行库并做短诊断，再开始识别。

默认安装到 `~/.local/share/rk35xx-cctv-person-cut`。必须通过 `--work-dir` 明确指定 SSD 专用工作目录。脚本不会清空已有工作目录，也不会修改原片、系统驱动或生产服务。

安装目录已存在或工作目录非空时退出，避免误覆盖安装或自动加载另一套服务的任务。

## 常用参数

| 参数 | 用途 |
|---|---|
| `--work-dir PATH` | 必填，SSD 工作缓存目录 |
| `--prefix PATH` | 独立安装目录；已存在时退出，不覆盖 |
| `--input-dir PATH` / `--output-dir PATH` | 初始输入/输出目录，后续可在 Web 为不同任务选择 |
| `--model PATH` / `--rknn-library PATH` | 用户已准备的模型与 Runtime |
| `--ffmpeg PATH` / `--ffprobe PATH` | 用户的兼容媒体工具 |
| `--port 8424` / `--bind 127.0.0.1` | 修改端口或监听地址，避免与现有服务冲突 |
| `--china` | 公共 GitHub 加速前缀和清华 PyPI 镜像；不写全局 pip 配置 |
| `--github-proxy URL` | 自定义 HTTPS GitHub 下载加速前缀 |
| `--pip-index-url URL` | 自定义 pip 下载源 |
| `--ref TAG_OR_SHA` | 选择源码版本，正式使用建议固定发布 tag 或 commit |
| `--system-site-packages --no-deps` | 复用板厂已工作的 NumPy/OpenCV，兼容旧 Python 3.7 环境 |
| `--no-start` | 安装后暂不启动 |
| `--dry-run` | 显示计划，不创建目录、安装包或启动服务 |

例如在已经有生产服务的设备上，使用不同目录和端口安装开发版：

```bash
bash install.sh --prefix /mnt/ssd/cctv-dev-app \
  --work-dir /mnt/ssd/cctv-dev-work --port 8424 \
  --system-site-packages --no-deps --no-start
```

不要让两个安装同时推理；如需测试，共用 `npu_lock`，先确认硬件空闲。此示例不会自动迁移生产 job.json。

## 网络

独立下载脚本会按配置尝试加速地址和 GitHub 原地址。下载到 HTML 错误页时不当作源码使用；解包拒绝绝对路径、父级跳转和链接成员。`--china` 对 pip 使用 HTTPS 清华源，不关闭 TLS 校验。

GitHub 加速采用给原公开 URL 加前缀的形式，可自行部署 [gh-proxy](https://github.com/hunshcn/gh-proxy)。公共加速示例为 `https://ghfast.top/`，不承诺其长期可用。清华镜像用法参考 [官方 PyPI 帮助](https://mirrors.tuna.tsinghua.edu.cn/help/pypi/)。

加速站只用于公开文件下载。不要将 GitHub token 写入安装 URL，也不要通过第三方下载代理创建仓库或上传代码。仓库发布是维护者的独立操作，安装器完全不需要 GitHub token。

## 启动、停止和开机启动

安装后脚本会打印实际路径。`start.sh` 在前台运行应用；需要后台运行时自行使用 shell 或 systemd。`stop.sh` 检查 PID 对应的是本安装的 server.py 后才发送停止信号。

```bash
bash ~/.local/share/rk35xx-cctv-person-cut/start.sh
bash ~/.local/share/rk35xx-cctv-person-cut/stop.sh
```

安装器首次自动启动会保存日志到安装目录的 `var/server.log`。开机启动参考 `deploy/` 中的 systemd 示例；其可写目录需要包含 Web 允许选择的输出盘。读取源码无需任何 GitHub 写入权限。
