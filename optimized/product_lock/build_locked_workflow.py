#!/usr/bin/env python3
"""商品像素锁定工作流生成器。

思路：商品不再由模型重画。把实拍抠图（透明底 PNG，保留原始像素）按配置贴到已审背景上，
模型只在「许可区」里生成落影、反光、边缘过渡和食物，最后把保护区的实拍像素原样留在成图里。

只依赖标准库；能读到图片尺寸时用 Pillow 做校验（可选）。
生成的节点类型全部来自本项目已在 ComfyUI 里执行过的工作流，外加同属核心蒙版模块的
SolidMask / InvertMask / ConditioningZeroOut。

用法：
    python3 build_locked_workflow.py example/config.json --out example/
"""
import argparse
import json
import sys
import uuid
from pathlib import Path

# ---- 节点登记表：UI 画布所需的输入口、控件顺序和输出口 -------------------------------------
# (连接输入[(名,类型)], 控件输入[名], 输出[(名,类型)])
SCHEMA = {
    "LoadImage": ([], ["image"], [("IMAGE", "IMAGE"), ("MASK", "MASK")]),
    "LoadImageMask": ([], ["image", "channel"], [("MASK", "MASK")]),
    "UnetLoaderGGUF": ([], ["unet_name"], [("MODEL", "MODEL")]),
    "CLIPLoaderGGUF": ([], ["clip_name", "type"], [("CLIP", "CLIP")]),
    "VAELoader": ([], ["vae_name"], [("VAE", "VAE")]),
    "ImageScale": ([("image", "IMAGE")], ["upscale_method", "width", "height", "crop"], [("IMAGE", "IMAGE")]),
    "ImageCrop": ([("image", "IMAGE")], ["width", "height", "x", "y"], [("IMAGE", "IMAGE")]),
    "CLIPTextEncode": ([("clip", "CLIP")], ["text"], [("CONDITIONING", "CONDITIONING")]),
    "ConditioningZeroOut": ([("conditioning", "CONDITIONING")], [], [("CONDITIONING", "CONDITIONING")]),
    "VAEEncode": ([("pixels", "IMAGE"), ("vae", "VAE")], [], [("LATENT", "LATENT")]),
    "VAEDecode": ([("samples", "LATENT"), ("vae", "VAE")], [], [("IMAGE", "IMAGE")]),
    "ReferenceLatent": ([("conditioning", "CONDITIONING"), ("latent", "LATENT")], [], [("CONDITIONING", "CONDITIONING")]),
    "CFGGuider": ([("model", "MODEL"), ("positive", "CONDITIONING"), ("negative", "CONDITIONING")], ["cfg"], [("GUIDER", "GUIDER")]),
    "Flux2Scheduler": ([], ["steps", "width", "height"], [("SIGMAS", "SIGMAS")]),
    "RandomNoise": ([], ["noise_seed"], [("NOISE", "NOISE")]),
    "KSamplerSelect": ([], ["sampler_name"], [("SAMPLER", "SAMPLER")]),
    "SamplerCustomAdvanced": ([("noise", "NOISE"), ("guider", "GUIDER"), ("sampler", "SAMPLER"), ("sigmas", "SIGMAS"), ("latent_image", "LATENT")], [],
                              [("output", "LATENT"), ("denoised_output", "LATENT")]),
    "SetLatentNoiseMask": ([("samples", "LATENT"), ("mask", "MASK")], [], [("LATENT", "LATENT")]),
    "ImageCompositeMasked": ([("destination", "IMAGE"), ("source", "IMAGE"), ("mask", "MASK")], ["x", "y", "resize_source"], [("IMAGE", "IMAGE")]),
    "SolidMask": ([], ["value", "width", "height"], [("MASK", "MASK")]),
    "InvertMask": ([("mask", "MASK")], [], [("MASK", "MASK")]),
    "GrowMask": ([("mask", "MASK")], ["expand", "tapered_corners"], [("MASK", "MASK")]),
    "ThresholdMask": ([("mask", "MASK")], ["value"], [("MASK", "MASK")]),
    "MaskComposite": ([("destination", "MASK"), ("source", "MASK")], ["x", "y", "operation"], [("MASK", "MASK")]),
    "MaskToImage": ([("mask", "MASK")], [], [("IMAGE", "IMAGE")]),
    "ImageToMask": ([("image", "IMAGE")], ["channel"], [("MASK", "MASK")]),
    "ImageBlur": ([("image", "IMAGE")], ["blur_radius", "sigma"], [("IMAGE", "IMAGE")]),
    "SaveImage": ([("images", "IMAGE")], ["filename_prefix"], [("images", "IMAGE")]),
}

DEFAULT_MODELS = {
    "unet": "flux-2-klein-4b-Q5_K_M.gguf",
    "clip": "Qwen3-4B-Q4_K_M.gguf",
    "vae": "flux2-vae.safetensors",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def node(kind, title, **inputs):
    require(kind in SCHEMA, f"未登记的节点类型 {kind}")
    return {"class_type": kind, "_meta": {"title": title}, "inputs": inputs}


def image_size(path, declared):
    """优先读真实文件；读不到时用配置里声明的 size。两者都有则必须一致。"""
    actual = None
    try:
        from PIL import Image
        if Path(path).is_file():
            with Image.open(path) as image:
                actual = list(image.size)
                if Path(path).suffix.lower() == ".png" and "cutout" in str(declared.get("_kind", "")):
                    require(image.mode == "RGBA", f"抠图必须是带透明通道的 PNG：{path}")
    except ImportError:
        pass
    stated = declared.get("size")
    if actual and stated:
        require(actual == list(stated), f"{path} 实际尺寸 {actual} 与配置 size {stated} 不一致")
    size = actual or stated
    require(size and len(size) == 2 and all(type(v) is int and v > 0 for v in size),
            f"无法确定 {path} 的尺寸：放好文件，或在配置中写 size")
    return list(size)


def build(config, base_dir=Path(".")):
    canvas = config.get("canvas", 1024)
    require(type(canvas) is int and canvas % 16 == 0 and 512 <= canvas <= 2048, "canvas 须为 16 的倍数，512–2048")
    output = config.get("output", 800)
    band = config.get("edit_band_px", 96)
    edge = config.get("edge_protect_px", 3)
    feather = config.get("feather_px", 8)
    require(type(band) is int and 0 <= band <= 400, "edit_band_px 取 0–400；0 表示整张背景都交给模型重绘")
    require(type(edge) is int and 0 <= edge <= 12, "edge_protect_px 取 0–12")
    require(type(feather) is int and 1 <= feather <= 31, "feather_px 取 1–31")
    cfg = float(config.get("cfg", 1.0))
    steps = config.get("steps", 4)
    seed = config["seed"]
    prefix = config.get("filename_prefix", "product_lock/example")
    models = {**DEFAULT_MODELS, **config.get("models", {})}
    prompt = config["prompt"].strip()
    require(len(prompt.split()) <= 220, "正向提示词超过 220 词：商品已被像素锁定，只写落影、光线和食物即可")
    placements = config["placements"]
    require(isinstance(placements, list) and 1 <= len(placements) <= 6, "placements 需要 1–6 件，按由后到前的顺序")

    background = config["background"]
    bg_size = image_size(base_dir / background["path"], background)
    require(bg_size[0] == bg_size[1], "背景须为正方形；先在取景阶段裁成正方形，避免被拉伸")

    g = {}
    g["1"] = node("LoadImage", "输入 · 已审背景", image=background["path"])
    g["3"] = node("UnetLoaderGGUF", "模型 · Klein", unet_name=models["unet"])
    g["4"] = node("CLIPLoaderGGUF", "模型 · 文本编码器", clip_name=models["clip"], type="flux2")
    g["5"] = node("VAELoader", "模型 · VAE", vae_name=models["vae"])
    g["6"] = node("ImageScale", "背景 · 等比到画布", image=["1", 0], upscale_method="lanczos",
                  width=canvas, height=canvas, crop="disabled")
    g["30"] = node("SolidMask", "空蒙版 · 商品累计", value=0.0, width=canvas, height=canvas)
    g["31"] = node("SolidMask", "空蒙版 · 食物累计", value=0.0, width=canvas, height=canvas)

    layout, product_all, food_all = ["6", 0], ["30", 0], ["31", 0]
    record = []
    for index, item in enumerate(placements):
        b = 100 + index * 20
        n = lambda offset: str(b + offset)
        size = image_size(base_dir / item["cutout"], {**item, "_kind": "cutout"})
        width, x, y = item["width"], item["x"], item["y"]
        require(all(type(v) is int for v in (width, x, y)) and width > 0, f"第 {index + 1} 件的 width/x/y 须为整数")
        # 高度永远由原图比例推出，杜绝把商品拉高或压扁。
        height = round(size[1] * width / size[0])
        require(x >= 0 and y >= 0 and x + width <= canvas and y + height <= canvas,
                f"第 {index + 1} 件超出画布：{width}×{height} 放在 ({x},{y})")
        require(width <= size[0] * 1.15, f"第 {index + 1} 件被放大超过原抠图 15%，会发虚；请提供更大的实拍裁切")
        tag = f"商品 {index + 1}"
        g[n(0)] = node("LoadImage", f"输入 · {tag} 实拍抠图（透明底）", image=item["cutout"])
        g[n(1)] = node("InvertMask", f"{tag} · 透明通道转商品蒙版", mask=[n(0), 1])
        g[n(2)] = node("ImageScale", f"{tag} · 等比缩放像素", image=[n(0), 0], upscale_method="lanczos",
                       width=width, height=height, crop="disabled")
        g[n(3)] = node("MaskToImage", f"{tag} · 蒙版转图", mask=[n(1), 0])
        g[n(4)] = node("ImageScale", f"{tag} · 等比缩放蒙版", image=[n(3), 0], upscale_method="lanczos",
                       width=width, height=height, crop="disabled")
        g[n(5)] = node("ImageToMask", f"{tag} · 缩放后蒙版", image=[n(4), 0], channel="red")
        g[n(6)] = node("ImageCompositeMasked", f"{tag} · 实拍像素贴入布局", destination=layout, source=[n(2), 0],
                       mask=[n(5), 0], x=x, y=y, resize_source=False)
        g[n(7)] = node("MaskComposite", f"{tag} · 计入商品蒙版", destination=product_all, source=[n(5), 0],
                       x=x, y=y, operation="add")
        # 前面的商品会遮住后面商品里的食物许可区。
        g[n(8)] = node("MaskComposite", f"{tag} · 遮挡后方食物区", destination=food_all, source=[n(5), 0],
                       x=x, y=y, operation="subtract")
        layout, product_all, food_all = [n(6), 0], [n(7), 0], [n(8), 0]
        entry = dict(index=index + 1, cutout=item["cutout"], source_size=size, placed=[x, y, width, height],
                     width_ratio=round(width / canvas, 3), food=False)
        if item.get("food_mask"):
            food_size = image_size(base_dir / item["food_mask"], {"size": item.get("food_mask_size")})
            require(food_size == size, f"第 {index + 1} 件的食物蒙版须与抠图同尺寸 {size}")
            g[n(9)] = node("LoadImageMask", f"输入 · {tag} 食物许可区（白=可画食物）", image=item["food_mask"], channel="red")
            g[n(10)] = node("MaskToImage", f"{tag} · 食物区转图", mask=[n(9), 0])
            g[n(11)] = node("ImageScale", f"{tag} · 食物区等比缩放", image=[n(10), 0], upscale_method="lanczos",
                            width=width, height=height, crop="disabled")
            g[n(12)] = node("ImageToMask", f"{tag} · 缩放后食物区", image=[n(11), 0], channel="red")
            g[n(13)] = node("MaskComposite", f"{tag} · 计入食物许可区", destination=food_all, source=[n(12), 0],
                            x=x, y=y, operation="add")
            food_all = [n(13), 0]
            entry["food"] = True
        record.append(entry)

    # ---- 蒙版代数：保护区 = 商品内缩 edge 像素，再扣掉食物许可区 --------------------------------
    g["40"] = node("ThresholdMask", "商品蒙版 · 二值", mask=product_all, value=0.5)
    g["41"] = node("GrowMask", f"商品核心 · 内缩 {edge}px", mask=["40", 0], expand=-edge, tapered_corners=True)
    g["42"] = node("MaskComposite", "食物许可区 · 限制在商品核心内", destination=food_all, source=["41", 0],
                   x=0, y=0, operation="multiply")
    g["43"] = node("MaskComposite", "保护区 · 实拍像素不许改", destination=["41", 0], source=["42", 0],
                   x=0, y=0, operation="subtract")
    if band:
        g["44"] = node("GrowMask", f"落影环带 · 商品外扩 {band}px", mask=["40", 0], expand=band, tapered_corners=True)
    else:
        g["44"] = node("SolidMask", "整张背景都允许重绘", value=1.0, width=canvas, height=canvas)
    g["45"] = node("MaskComposite", "许可区 · 硬边", destination=["44", 0], source=["43", 0], x=0, y=0, operation="subtract")
    g["46"] = node("MaskToImage", "许可区转图", mask=["45", 0])
    g["47"] = node("ImageBlur", f"许可区羽化 {feather}px", image=["46", 0], blur_radius=feather, sigma=round(feather / 2, 1))
    g["48"] = node("ImageToMask", "羽化后许可区", image=["47", 0], channel="red")
    g["49"] = node("MaskComposite", "最终许可区 · 羽化后再扣保护区", destination=["48", 0], source=["43", 0],
                   x=0, y=0, operation="subtract")

    # ---- 采样：布局图既是起点 latent 又是唯一参考，输出与参考同尺寸 ------------------------------
    g["8"] = node("CLIPTextEncode", "正向提示词 · 只写落影、光线、食物", clip=["4", 0], text=prompt)
    if cfg == 1.0:
        g["9"] = node("ConditioningZeroOut", "负向 · CFG=1 时不参与计算", conditioning=["8", 0])
    else:
        negative = config.get("negative", "").strip()
        require(len(negative) >= 10, "cfg≠1 时需要提供 negative")
        g["9"] = node("CLIPTextEncode", "负向提示词", clip=["4", 0], text=negative)
    g["10"] = node("VAEEncode", "布局图编码", pixels=layout, vae=["5", 0])
    g["12"] = node("ReferenceLatent", "正向参考 · 布局图", conditioning=["8", 0], latent=["10", 0])
    g["13"] = node("ReferenceLatent", "负向参考 · 布局图", conditioning=["9", 0], latent=["10", 0])
    g["16"] = node("CFGGuider", "引导", model=["3", 0], positive=["12", 0], negative=["13", 0], cfg=cfg)
    g["18"] = node("Flux2Scheduler", "调度", steps=steps, width=canvas, height=canvas)
    g["19"] = node("RandomNoise", "种子", noise_seed=seed)
    g["20"] = node("KSamplerSelect", "采样器", sampler_name="euler")
    g["50"] = node("SetLatentNoiseMask", "只在许可区采样", samples=["10", 0], mask=["49", 0])
    g["21"] = node("SamplerCustomAdvanced", "采样", noise=["19", 0], guider=["16", 0], sampler=["20", 0],
                   sigmas=["18", 0], latent_image=["50", 0])
    g["22"] = node("VAEDecode", "解码", samples=["21", 0], vae=["5", 0])
    g["51"] = node("ImageCompositeMasked", "像素级回贴 · 保护区保持实拍原样", destination=layout, source=["22", 0],
                   mask=["49", 0], x=0, y=0, resize_source=False)

    # ---- 输出与留档 --------------------------------------------------------------------------
    crop = config.get("final_crop_xywh", [0, 0, canvas, canvas])
    require(len(crop) == 4 and all(type(v) is int for v in crop) and crop[2] == crop[3]
            and crop[0] >= 0 and crop[1] >= 0 and crop[0] + crop[2] <= canvas and crop[1] + crop[3] <= canvas,
            "final_crop_xywh 须为画布内的正方形")
    g["23"] = node("SaveImage", "保存 · 成图母图", images=["51", 0], filename_prefix=f"{prefix}_result")
    g["24"] = node("ImageCrop", "成图取景 · 正方形", image=["51", 0], width=crop[2], height=crop[3], x=crop[0], y=crop[1])
    g["25"] = node("ImageScale", f"等比缩放 · {output}", image=["24", 0], upscale_method="lanczos",
                   width=output, height=output, crop="disabled")
    g["26"] = node("SaveImage", f"保存 · {output} 无字主图", images=["25", 0], filename_prefix=f"{prefix}_{output}")
    g["60"] = node("SaveImage", "留档 · 布局图（采样前）", images=layout, filename_prefix=f"{prefix}_layout")
    g["61"] = node("SaveImage", "留档 · 模型原始输出", images=["22", 0], filename_prefix=f"{prefix}_raw")
    g["62"] = node("MaskToImage", "保护区转图", mask=["43", 0])
    g["63"] = node("SaveImage", "留档 · 保护区", images=["62", 0], filename_prefix=f"{prefix}_protect")
    g["64"] = node("MaskToImage", "最终许可区转图", mask=["49", 0])
    g["65"] = node("SaveImage", "留档 · 最终许可区", images=["64", 0], filename_prefix=f"{prefix}_edit")

    check(g)
    identity = dict(method="product_pixel_lock", canvas=canvas, output=output, edit_band_px=band,
                    edge_protect_px=edge, feather_px=feather, cfg=cfg, steps=steps, seed=seed,
                    models=models, placements=record, final_crop_xywh=crop,
                    outputs={"23": "result", "26": f"{output}", "60": "layout", "61": "raw", "63": "protect", "65": "edit"})
    return g, identity


def check(graph):
    """连接两端类型、必填输入和悬空引用检查（等价于项目 check_graph 的离线版）。"""
    for identifier, item in graph.items():
        links, widgets, _ = SCHEMA[item["class_type"]]
        expected = {name for name, _ in links} | set(widgets)
        require(set(item["inputs"]) == expected,
                f"节点 {identifier} {item['class_type']} 输入不符：{sorted(set(item['inputs']) ^ expected)}")
        for name, kind in links:
            value = item["inputs"][name]
            require(isinstance(value, list) and len(value) == 2 and value[0] in graph, f"节点 {identifier}.{name} 未连接")
            outputs = SCHEMA[graph[value[0]]["class_type"]][2]
            require(value[1] < len(outputs) and outputs[value[1]][1] == kind,
                    f"节点 {identifier}.{name} 需要 {kind}，实际接到 {graph[value[0]]['class_type']} 输出 {value[1]}")
        for name in widgets:
            require(not isinstance(item["inputs"][name], list), f"节点 {identifier}.{name} 应为数值或文本")


def depth_of(graph):
    cache = {}

    def depth(identifier):
        if identifier not in cache:
            sources = [v[0] for v in graph[identifier]["inputs"].values() if isinstance(v, list)]
            cache[identifier] = 1 + max((depth(s) for s in sources), default=-1)
        return cache[identifier]
    return {identifier: depth(identifier) for identifier in graph}


def to_ui(graph, title, note):
    depths = depth_of(graph)
    rows, nodes, links, by_id = {}, [], [], {}
    for order, (identifier, item) in enumerate(sorted(graph.items(), key=lambda pair: (depths[pair[0]], int(pair[0])))):
        link_inputs, widgets, outputs = SCHEMA[item["class_type"]]
        values = [item["inputs"][name] for name in widgets]
        if item["class_type"] == "RandomNoise":
            values.append("fixed")
        column = depths[identifier]
        row = rows.get(column, 0)
        rows[column] = row + 1
        size = [420, 260] if item["class_type"] == "CLIPTextEncode" else (
            [340, 320] if item["class_type"] in ("LoadImage", "LoadImageMask", "SaveImage") else [320, 140])
        entry = dict(id=int(identifier), type=item["class_type"], pos=[column * 460, row * 380], size=size, flags={},
                     order=order, mode=0, inputs=[dict(name=name, type=kind, link=None) for name, kind in link_inputs],
                     outputs=[dict(name=name, type=kind, links=[]) for name, kind in outputs],
                     properties={"Node name for S&R": item["class_type"]}, widgets_values=values,
                     title=item["_meta"]["title"])
        if item["class_type"] in ("LoadImage", "LoadImageMask"):
            entry.update(color="#354a62", bgcolor="#243242")
        if item["_meta"]["title"].startswith(("保护区", "像素级回贴")):
            entry.update(color="#4a6235", bgcolor="#2f4224")
        by_id[identifier] = entry
        nodes.append(entry)
    for identifier, item in graph.items():
        for slot, (name, kind) in enumerate(SCHEMA[item["class_type"]][0]):
            source, output = item["inputs"][name]
            link_id = len(links) + 1
            links.append([link_id, int(source), output, int(identifier), slot, kind])
            by_id[identifier]["inputs"][slot]["link"] = link_id
            by_id[source]["outputs"][output]["links"].append(link_id)
    note_id = max(int(i) for i in graph) + 1
    nodes.append(dict(id=note_id, type="Note", pos=[0, -420], size=[900, 360], flags={}, order=len(nodes), mode=0,
                      inputs=[], outputs=[], properties={}, widgets_values=[note], title=title,
                      color="#554330", bgcolor="#332b22"))
    return dict(id=str(uuid.uuid4()), revision=0, last_node_id=note_id, last_link_id=len(links), nodes=nodes,
                links=links, groups=[], config={}, extra={"ds": {"scale": 0.35, "offset": [150, 600]}}, version=0.4)


NOTE = """商品像素锁定 · 实拍商品不经过模型重画

1. 蓝色节点是输入：已审背景（正方形）、每件商品的透明底实拍抠图、可选的食物许可区蒙版。
2. 商品只做等比缩放后贴入布局，高度由原图比例推出，不存在被拉高或压扁的路径。
3. 模型只在「最终许可区」采样：商品外一圈落影环带、商品最外几像素的过渡边，以及食物许可区。
4. 绿色节点「像素级回贴」把保护区恢复为布局图像素，保护区内成图与实拍缩放图逐像素相同。
5. 留档输出：布局图、模型原始输出、保护区、最终许可区。用 verify_lock.py 复核保护区零改动。
6. 位置、大小、环带宽度都在 config.json 里改，再用 build_locked_workflow.py 重新生成本图。"""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config")
    parser.add_argument("--out", default=".")
    arguments = parser.parse_args()
    config_path = Path(arguments.config)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    try:
        graph, identity = build(config, config_path.parent)
    except (ValueError, KeyError) as error:
        sys.exit(f"配置未通过：{error}")
    out = Path(arguments.out)
    out.mkdir(parents=True, exist_ok=True)
    name = config.get("name", "商品像素锁定")
    for suffix, value in (("api", graph), ("ui", to_ui(graph, name, NOTE)), ("identity", identity)):
        (out / f"{name}.{suffix}.json").write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    hero = max(identity["placements"], key=lambda entry: entry["placed"][2])
    print(f"已生成 {name}：{len(graph)} 个节点，{len(identity['placements'])} 件商品，"
          f"最大一件占画宽 {hero['width_ratio']:.0%}")


if __name__ == "__main__":
    main()
