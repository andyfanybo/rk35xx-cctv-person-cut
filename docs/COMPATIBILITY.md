# 兼容范围与验证记录

初始整理日期：2026-10-18。应用版本：2.0.0-dev；业务基线：v1.9.2。

| 芯片 | 上游 RKNN 支持 | 模型 | 本项目状态 |
|---|---|---|---|
| RK3568 | 支持 | 针对 RK3566/RK3568 的匹配模型 | 原生产基线在一台厂商 Linux 4.19 ARM64 NAS 使用；适配版检查见 VALIDATION.md |
| RK3566 | 支持 | 官方示例与 RK3568 同组 | 待实机短测 |
| RK3576 | 支持 | rk3576 目标转换 | 待实机短测 |
| RK3588 | 支持 | rk3588 目标转换 | 待实机短测 |

“上游支持”不等于本项目已通过对应板卡验收。新的 CPU、系统镜像、FFmpeg 分支和 RKNN 组合都需要短测；不能仅凭内核版本号判断。当前 C API ctypes 绑定使用传统公开张量结构，需要与运行库 ABI 配套。

## 分层兼容

```text
业务层：队列 → SSD分段 → 人员采样 → 原编码裁切 → 合并 → 发布 → 清缓存
                    ↓                    ↓
NPU：模型 → RKNN C API → 用户态库 → RKNPU驱动
VPU：FFmpeg解码/滤镜 → MPP/DRM → 内核驱动与设备树
存储：目录边界 → 挂载身份 → 硬链接不覆盖发布
```

支持厂商 BSP 软件栈的可验证组合。普通主线内核即使识别出芯片或有 NPU 设备，也不自动等于兼容厂商 RKNN/MPP ABI；另一个用户态驱动栈需独立后端。

当前基础路径为 VPU 解码、CPU 缩放/旋转、NPU 推理；RGA 缩放尚未作为默认后端。兼容模式允许 CPU 解码，速度单独观察。保留单 worker，不按芯片算力增加并发。

## 添加实机记录

提交以下不含隐私的内容：板卡、SoC、系统、架构、内核、Python/NumPy/OpenCV、FFmpeg 版本与构建、RKNN runtime/driver、模型 target/转换版本、解码路径、存储类型、短样本是否完成。

短样本至少覆盖识别、一次非零起点裁切、合并发布、同名不覆盖和缓存清理。无硬件 CI 只覆盖业务及故障模拟，不能证明 VPU/NPU 可用。不提交真实录像、任务 JSON、相机标识、人物图像或完整私有挂载路径。

## 上游依据

- [RKNN-Toolkit2 支持平台](https://github.com/airockchip/rknn-toolkit2)
- [官方模型分组选取示例](https://github.com/airockchip/rknn-toolkit2/blob/master/rknn-toolkit-lite2/examples/resnet18/test.py)
- [RKNN C API 与 SDK/driver 查询](https://github.com/airockchip/rknn-toolkit2/blob/master/rknpu2/runtime/Linux/librknn_api/include/rknn_api.h)
- [官方 MPP 驱动及平台要求](https://github.com/rockchip-linux/mpp/blob/develop/doc/Rockchip_Developer_Guide_MPP_CN.md)
- [可选 ffmpeg-rockchip 后端要求](https://github.com/nyanmisaka/ffmpeg-rockchip)

上游版本会变化，部署记录应保存具体版本或 commit。安装器不自动升级板厂驱动。
