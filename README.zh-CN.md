# 视频加字幕 / 去字幕工具包

[English README](README.md)

这是一个**仅包含源码的 Python 工具包**，面向中文视频的两个常见工作流：

1. 生成 SRT / ASS 字幕，并可通过 FFmpeg 将字幕烧录到视频；
2. 检测视频画面中的硬字幕，生成检测缓存和遮罩，再使用 OpenCV 进行去字幕处理。

仓库还包含单字残留候选审计，以及人工确认帧区间后的安全拼接工具。

> 当前仓库公开的是源码、测试、文档和示例配置。视频、模型权重、缓存、日志、报告和生成结果均不在仓库中。

## 功能概览

- 中文字幕文本与中文文件路径友好；
- SRT / ASS 字幕生成；
- 明确的字幕位置控制，例如 ASS 的 `\\an2\\pos(x,y)`；
- 可选 PaddleOCR 检测硬字幕区域；
- 检测缓存绑定源视频大小、帧数、帧率和 SHA-256，避免误用缓存；
- OpenCV Telea 和 Navier–Stokes 两种局部修复方式；
- 单字字幕候选检测，例如“上”“好”“嗯”等方形字形；
- 场景切换和遮罩边界附近的候选帧强化检查；
- 只替换人工确认区间的修复视频拼接；
- 默认拒绝覆盖已有输出，并进行 FFmpeg 完整解码检查。

本项目**不包含也不声称实现** STTN、LaMa、ProPainter、RAFT 或其他时序生成式去字幕后端。具体边界请阅读 [第三方依赖说明](THIRD_PARTY_NOTICES.md)。

## 环境要求

- Python 3.10 或更高版本；
- OpenCV、NumPy；
- 系统中可执行的 `ffmpeg` 和 `ffprobe`；
- 可选：PaddleOCR / PaddlePaddle，用于 OCR 检测；
- 可选：faster-whisper，用于 ASR 字幕生成。

建议使用 UTF-8 环境运行命令和保存 JSON / SRT / ASS 文件。

## 安装

安装测试依赖：

```bash
python -m pip install -e ".[test]"
```

安装 OCR 可选依赖前，请先检查 PaddleOCR、PaddlePaddle 及其模型的版本和许可证：

```bash
python -m pip install -e ".[ocr]"
```

安装 ASR 可选依赖：

```bash
python -m pip install -e ".[asr]"
```

## 生成中文字幕

使用已经审核过的字幕种子：

```bash
subtitle-toolkit caption \
  --input ./input \
  --output ./output \
  --seed-evidence ./seeds \
  --style-config ./configs/caption-style.example.json
```

如果明确需要 ASR，可以显式开启：

```bash
subtitle-toolkit caption \
  --input ./input \
  --output ./output \
  --run-asr \
  --asr-model medium \
  --asr-language zh \
  --cpu-threads 4 \
  --style-config ./configs/caption-style.example.json
```

ASR 不会自动运行。`--asr-language zh` 用于中文，`--asr-language auto` 可请求自动识别语言。模型权重由用户自行下载和审核，不会写入本仓库。

如果需要把 ASS 字幕烧录进视频，添加 `--render`。已有目标文件会触发错误，不会被覆盖。

关于如何避免 ASR 长段和静音造成字幕提前出现、常驻或过早消失，参见
[`docs/caption-timing.md`](docs/caption-timing.md)。

## 去除硬字幕

去字幕分为两个阶段：先检测并检查缓存，再使用缓存执行修复。

第一步，生成检测缓存：

```bash
subtitle-toolkit remove \
  --config ./configs/removal.example.json \
  --input input.mp4 \
  --cache detections.json \
  --detect-only
```

确认缓存中的框选区域无误后，再执行去字幕：

```bash
subtitle-toolkit remove \
  --config ./configs/removal.example.json \
  --input input.mp4 \
  --output cleaned-review.mp4 \
  --cache detections.json \
  --reuse-cache \
  --inpaint-method ns
```

ROI 参数顺序为：

```text
TOP BOTTOM LEFT RIGHT
```

取值是 0 到 1 之间的归一化比例。例如画面下方区域可以使用：

```text
0.55 0.98 0.0 1.0
```

注意：OpenCV 是局部空间修复，不是时序生成式修复。复杂运动背景、人物轮廓和场景切换可能需要人工检查；OCR 误检也可能把正常画面当作字幕擦除。

## 检查“上”“好”“嗯”等单字残留

单字检测只是候选发现工具，不能代替人工确认。人物、衣服、道具、光效和背景纹理都可能产生类似单字的误报。

审核原则：

1. 对比源视频和当前输出的同一帧；
2. 检查候选在时间上是否连续；
3. 检查场景切换前后是否需要拆分；
4. 只有确认是对白字幕后，才建立修复区间；
5. 不要仅凭 OCR 文本或一个候选框自动发布修复结果。

## 只拼接人工确认的修复帧

当外部、独立审核过的流程已经生成完整修复视频后，可以只替换人工批准的帧区间。

区间 JSON 使用**从 0 开始、两端都包含**的帧号：

```json
[
  {"start": 768, "end": 805},
  {"start": 907, "end": 941}
]
```

执行拼接：

```bash
subtitle-toolkit splice \
  --current accepted.mp4 \
  --corrected corrected-staging.mp4 \
  --intervals reviewed-intervals.json \
  --output reviewed-fix.mp4
```

工具会：

- 拒绝覆盖已有输出；
- 检查两个视频的帧数、宽高和 FPS；
- 只从修复视频取批准区间的帧；
- 从当前已接受视频保留音轨；
- 完整解码并检查输出帧数；
- 通过临时文件和原子改名发布结果。

拼接后视频会重新编码，因此区间外的“帧选择”保持不变，但 H.264 编码可能使像素产生轻微变化。正式替换前应保留备份，并检查区间前后边界画面。

## 中文路径和文件名

建议：

- 使用 UTF-8 编码读写配置、字幕和报告；
- Windows 用户可把视频、缓存和输出放在中文目录，但应避免把凭据写入路径或日志；
- 如果第三方 OCR 组件对中文路径兼容性不好，可把运行缓存放到 ASCII 路径；
- 不要把视频、模型、缓存、日志或生成结果复制进 Git 仓库。

## 测试

测试只使用生成的 NumPy 图像和临时合成视频，不需要真实视频或模型：

```bash
python -m pytest -q
```

## 许可证与第三方依赖

本项目代码采用 Apache-2.0。PaddleOCR、PaddlePaddle、faster-whisper、FFmpeg、模型权重和视频素材各自有独立许可证、来源和使用限制。

- [Apache-2.0 许可证](LICENSE)
- [第三方依赖说明](THIRD_PARTY_NOTICES.md)
- [安全策略](SECURITY.md)
