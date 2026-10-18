# 模型准备

仓库不附带现有 NAS 应用的权重。原模型的 n/s 规格、训练来源及跨芯片转换材料尚未确认，不能将它作为可再分发通用模型。

推荐在独立的转换环境中安装与你选择的软件栈匹配的 RKNN-Toolkit2，使用来源及许可明确的 **Rockchip 优化 YOLOv8 ONNX** 和有权使用的校准图像。参考 [官方模型结构与转换说明](https://github.com/airockchip/rknn_model_zoo/tree/main/examples/yolov8)。官方示例仓库的代码许可不能代替原始模型权重的许可。

当前检测器要求：

- 单输入，640×640 RGB；模型转换时 mean=0、std=255，应用输入 uint8。
- COCO 80 类，person 为类别 0。
- 三尺度九输出，分类分支索引 1、4、7，NCHW 布局。
- INT8 分类输出布局符合检查时使用原生人员分数；否则在相同模型结构下取浮点输出。任意 YOLO、端到端 NMS 模型和任意输出顺序不自动兼容。

转换示例（参数路径由你提供）：

```bash
python tools/convert_model.py \
  --onnx /path/to/rockchip-yolov8n.onnx \
  --dataset /path/to/calibration-list.txt \
  --target rk3588 \
  --output models/person-rk3588.rknn \
  --source 'upstream-model-url-and-revision' \
  --model-license 'actual-upstream-license'
```

分别为目标 SoC 转换，不能将 RK3568 模型改名为 RK3588 模型。官方示例通常合并 RK3566/RK3568，但也需要在实际 runtime/driver 上验证。

脚本会记录转换版本、输入设置、模型文件哈希、来源和许可到 `.manifest.json`。哈希仅用于模型身份，不对视频作逐包或全量哈希。转换结果尚未在目标机验证，元数据的 `runtime_validated` 为 false。

配置 `model` 指向模型，必要时配置 `model_manifest` 检查目标、结构与模型身份。`doctor --hardware` 会检查实际输入输出属性并推理一张零图；零图检查用于运行链路，不代表检测准确率验证。之后使用有人的短样本观察业务行为。

运行服务时只需要 RKNN Runtime。完整 Toolkit2 和校准数据不应安装到生产服务环境。用户自备模型不会被提交 Git；分发模型附件前需要独立完成授权和实机验证。
