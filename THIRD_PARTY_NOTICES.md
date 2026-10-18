# 第三方依赖与模型

本项目的非商业许可适用于有权授权的项目原创代码，不改变第三方软件、模型或数据的许可。GitHub 能公开下载某个文件，不代表本项目可以重新授权或再分发它。

| 依赖 | 使用方式 | 上游许可入口 |
|---|---|---|
| NumPy | Python 数组及后处理；用户安装 | [NumPy LICENSE](https://github.com/numpy/numpy/blob/main/LICENSE.txt) |
| OpenCV | Python 图像缩放与旋转；用户安装 | [OpenCV LICENSE](https://github.com/opencv/opencv/blob/4.x/LICENSE) |
| FFmpeg / FFprobe | 外部子进程；使用用户系统版本 | [FFmpeg 许可说明](https://ffmpeg.org/legal.html) |
| Rockchip MPP | 由所选 FFmpeg 调用 | [官方 MPP](https://github.com/rockchip-linux/mpp) |
| RKNN Runtime / Toolkit2 | 用户另行配置，仓库不附带二进制 | [官方 SDK 及 LICENSE](https://github.com/airockchip/rknn-toolkit2) |
| YOLOv8 模型与转换工具链 | 用户提供授权明确的权重和校准图像 | [Rockchip 模型示例](https://github.com/airockchip/rknn_model_zoo/tree/main/examples/yolov8) |

以实际安装版本的许可证、构建参数和随附声明为准，依赖包还可能包含其他组件。不能将第三方许可统一替换为本项目的 LICENSE。

不附带开发设备原有应用中的模型、运行库或 FFmpeg。若将来发布二进制、容器或模型附件，应逐项确认来源、再分发条件并随附件保留相应声明。特别是带 `--enable-nonfree` 的 FFmpeg 构建不能直接作为本项目的通用发布附件。

模型转换不会改变原权重的许可。转换工具记录用户声明的来源及许可，记录本身不构成对其真实性或再分发权利的证明。权重的使用及分发必须同时满足原模型许可和本项目代码的非商业使用要求。
