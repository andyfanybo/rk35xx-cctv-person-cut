# 配置与路径

`PERSON_CONFIG` 指向 JSON 配置文件。配置里的相对路径相对于配置文件，环境变量里的相对路径也按此规则解析。JSON 不支持注释；未知字段会报错，避免拼错配置后悄悄使用默认值。

| 配置字段 | 环境变量覆盖 | 含义 |
|---|---|---|
| `work_dir` | `PERSON_DATA` | 用户指定的 SSD 任务、分段、裁切和日志目录 |
| `publish_dir` | `PERSON_PUBLISH_DIR` | 专用最终成品目录 |
| `sources` | 无 | 名称到原录像根目录的映射，名称显示在界面中 |
| `browse_roots` | 无 | Web 文件夹浏览范围，例如挂载盘的 `/mnt`、`/media`、`/srv`；不允许直接使用 `/` |
| `model` | `PERSON_MODEL` | 目标芯片的兼容 RKNN 模型 |
| `model_manifest` | 无 | 可选的模型转换元数据文件 |
| `rknn_library` | `PERSON_RKNN_LIBRARY` | `librknnrt.so` 路径；空值使用系统动态库查找 |
| `ffmpeg` / `ffprobe` | `PERSON_FFMPEG` / `PERSON_FFPROBE` | 可执行文件路径或 PATH 中的命令 |
| `decoder` | `PERSON_DECODER` | `auto`、`rkmpp`、`software` |
| `allow_software_decode` | 无 | `auto` 模式是否允许转为软件解码，默认 false |
| `npu_lock` | `PERSON_NPU_LOCK` | 默认工作目录的 npu.lock，多实例需共用同一个锁文件 |
| `bind` / `port` | `PERSON_BIND` / `PERSON_PORT` | HTTP 监听，默认 127.0.0.1:8423 |
| `storage` | 无 | 发布盘保护策略 |

工作目录、发布目录和各原片根目录不得互相包含，也不得使用文件系统根目录。模型应放在缓存和发布目录之外。路径字符串本身无法可靠证明它是 SSD，部署者需要确认介质；程序保护配置目录边界。

## Web 选择输入和输出目录

新建任务面板分别提供输入、输出的“浏览”按钮，浏览的是服务器文件系统。可以进入子目录、返回上一级，或输入浏览范围内的路径；不读取浏览器所在电脑的目录、不上传视频。

浏览范围由 `browse_roots`、已配置的输入目录及默认输出目录组成。隐藏目录和越界符号链接不显示，SSD 工作目录不能作为输入或输出。请选择已经存在且服务账户有权限的文件夹。

每个任务保存 `input_directory`、`publish_dir` 以及输出存储策略。修改默认配置或给下一任务选择另一个目录，不会改变已有任务的发布、下载、恢复和删除位置。浏览器会记住上次的选项，供下次新建任务使用。

自选外部输出目录按选择时实际所在挂载点和文件系统保存保护策略；已配置的默认输出目录保留其完整 `storage` 设置。更改默认路径后，已有任务继续使用自己的记录。

`decoder=auto` 先检查解码器存在，再读一帧验证实际解码链。硬解失败时，仅当 `allow_software_decode=true` 才尝试软件解码。`decoder=rkmpp` 要求硬解，`decoder=software` 明确采用软件解码。所有模式保留 NPU 推理。

极速关键帧路径对 SSD 分段测试 `-discard nokey` 与首关键帧 PTS 选择；不支持时改为连续解码稀疏采样，通常更慢。后续坏段仍采用最多三次重试、整段保留。该短探测不能保证所有后续关键帧都正常。

## 发布存储

本地系统盘目录可以使用：

```json
{"storage": {"policy": "local"}}
```

外接盘和 NAS 应使用 mount 策略。例如：

```json
{"storage": {"policy": "mount", "mount_path": "/mnt/archive", "fstype": "nfs"}}
```

`mount_path` 必须是包含成品目录的实际挂载点；`fstype` 可省略，填写时需与 `/proc/self/mountinfo` 一致。挂载缺失或被其他嵌套挂载替代时停止发布和清缓存。虚拟 NAS 若另有真实后端盘，可增加 `backing_mount`，要求它已挂载且容量与虚拟发布盘匹配。

文件系统必须支持同目录硬链接以及同名创建失败的语义。`doctor --storage-test` 只在唯一临时子目录测试。SMB、FAT、部分 FUSE 等不能笼统声明支持；不支持时保留 SSD 成品并报告失败，不使用可能覆盖同名文件的普通 rename 作为替代。

## 系统服务

systemd 不是应用启动的必要条件。先用当前用户完成诊断和短样本，再根据部署示例设置服务用户、只读原片目录和可写目录。

不同板厂设备节点不同。检查所需 MPP、DRM、DMA heap、NPU 权限，按驱动和 udev 的实际设备组授权；不要假定存在 `/dev/rknpu`，也不要一律给所有 `/dev` 写权限。子进程跟随 `sys.executable`，会使用启动服务的同一 Python 虚拟环境。

Docker 不能提供宿主机缺失的硬件驱动。当前仓库优先提供原生安装，避免容器掩盖驱动、设备权限和 ABI 问题。
