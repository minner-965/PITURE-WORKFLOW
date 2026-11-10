# 商品像素锁定工作流

实拍商品不经过模型重画。模型只生成落影、反光、边缘过渡和食物。背景和原因见 `docs/工作流审查与一致性优化.md`。

状态：已离线校验节点连接和蒙版逻辑，**尚未在 ComfyUI 里实际出图**。先用一张已通过的图（建议 B05）对照验证，再批量使用。

## 准备素材

| 素材 | 要求 |
|---|---|
| 已审背景 | 正方形 PNG，台面上留出放商品的位置，光向和俯角与实拍一致 |
| 商品抠图 | 每件一张透明底 PNG，从摄影师原片抠出，保留原始像素，不要调色或缩小 |
| 食物许可区（可选） | 与抠图同尺寸的黑白 PNG，白色是可以画食物的范围。上沿就是液面线，离碗沿留 3–6 px |

抠图可以用 ComfyUI 的 Mask Editor，也可以用任何能导出透明底 PNG 的工具。文件放进 ComfyUI 的 `input/`。

## 写配置

复制 `example/config.json` 修改。

| 字段 | 含义 |
|---|---|
| `placements` | 商品列表，**由后到前**排列。每件写 `cutout`、`width`、`x`、`y`，高度自动按比例算 |
| `width` | 商品在 1024 画布上的宽度。主盘占画宽八成约为 820 |
| `edit_band_px` | 商品外侧允许模型画落影的宽度，默认 96。设 0 表示整张背景都交给模型围着商品重画 |
| `edge_protect_px` | 商品最外几像素交给模型做过渡，默认 3 |
| `prompt` | 只写落影、光线和食物。不要描述商品器型 |
| `cfg` | 默认 1.0。大于 1 时需要再写 `negative` |
| `final_crop_xywh` | 成图取景，须为正方形 |

配置里的 `size` 在读不到图片文件时使用；文件存在时以文件为准，两者不一致会报错。

## 生成和核对

```sh
python3 build_locked_workflow.py example/config.json --out example/
```

得到 `.ui.json`（拖进画布查看）、`.api.json`（提交用）和 `.identity.json`（本次参数记录）。

出图后工作流会另存布局图、保护区和模型原始输出。核对保护区是否零改动：

```sh
python3 verify_lock.py --layout B05_layout.png --result B05_result.png --protect B05_protect.png
```

`protected_changed_pixels` 为 0 即商品像素与实拍缩放图完全一致。这只证明商品没变，落影、食物和整体融合仍要看图验收。

## 接入现有程序

生成器只用标准库，输出的 API 图可以直接交给 `scripts/comfy_client.py` 提交，缓存键和回执沿用 `fission` 现有做法。两个压缩包没有改动。
