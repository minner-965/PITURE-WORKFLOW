#!/usr/bin/env python3
"""按 docs/提示词标准.md 检查一条提示词。

用法：
    python3 prompt_lint.py --mode lock "Keep the photograph exactly as it is. Add ..."
    python3 prompt_lint.py --mode video --file prompt.txt
    python3 prompt_lint.py --mode multiref --refs 2 --file prompt.txt

模式：scene 生成空景；lock 像素锁定后的局部生成；multiref 原生多参考；video 图生视频。
有「错误」时退出码为 1；「提醒」不影响退出码。规则改动时同步更新标准文件第 8 节。
"""
import argparse
import re
import sys

# (最少词数, 最多词数)
LENGTH = {"scene": (30, 140), "lock": (15, 90), "multiref": (30, 150), "video": (12, 70)}

NEGATION = r"\b(no|not|without|never|avoid|don't|doesn't|isn't|free of|absence of)\b"
# 堆砌的质量词不描述画面里任何可见的东西。
FILLER = ["masterpiece", "best quality", "8k", "4k", "ultra detailed", "ultra-detailed", "hyperrealistic",
          "hyper-realistic", "award winning", "award-winning", "trending", "highly detailed", "ultra realistic"]
# 商品已有实拍时，用文字形容器型会和照片抢话语权。
SHAPE = ["bowed", "flared", "raised corner", "raised corners", "tapered", "elongated", "curled", "upturned",
         "petal", "winged", "deep bowl", "shallow bowl", "tall wall", "thick wall", "thin wall"]
# 视频里要求商品自身运动，模型必须重建没拍到的面。
PRODUCT_MOTION = ["rotate", "rotates", "rotating", "spin", "spins", "spinning", "flip", "flips", "turns over",
                  "turn around", "360", "orbit", "orbits", "orbiting", "morph", "transforms into"]
CAMERA = ["push-in", "push in", "pull-back", "pull back", "dolly", "pan", "tilt", "static camera", "locked-off",
          "locked off", "handheld", "zoom", "rack focus", "tracking"]
LIGHT = ["light", "daylight", "sunlight", "shadow", "backlit", "lamp", "window", "glow", "highlight"]


def found(words, text):
    return [w for w in words if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", text)]


def lint(prompt, mode, refs=0):
    text = prompt.lower()
    words = len(re.findall(r"[A-Za-z0-9#'-]+", prompt))
    errors, warnings = [], []
    low, high = LENGTH[mode]
    if words > high:
        errors.append(f"{words} 词，超过 {mode} 模式上限 {high}。删掉不指向画面内容的句子")
    elif words < low:
        warnings.append(f"只有 {words} 词，低于建议下限 {low}。本地没有提示词扩写，过短的提示词会让模型自由发挥")
    if re.search(r"[一-鿿]", prompt):
        errors.append("含中文。提示词正文用英文写，中文只写在配置的备注字段里")
    if re.search(r"[,，]\s*\w+(\s\w+)?\s*[,，]\s*\w+(\s\w+)?\s*[,，]", prompt) and prompt.count(".") < 2:
        warnings.append("像关键词列表。改写成完整句子，模型是按整段场景描述训练的")
    hits = re.findall(NEGATION, text)
    if hits:
        errors.append(f"含否定写法 {sorted(set(hits))}。改成正面描述，例如 no text → clean unmarked surfaces")
    hits = found(FILLER, text)
    if hits:
        warnings.append(f"含空泛质量词 {hits}。换成相机、镜头、光线这类具体信息")
    if mode in ("lock", "multiref", "video"):
        hits = found(SHAPE, text)
        if hits:
            (errors if mode == "lock" else warnings).append(f"在形容商品器型 {hits}。器型交给实拍，文字只写位置、数量和光线")
    if mode in ("scene", "lock", "multiref") and not found(LIGHT, text):
        warnings.append("没有写光线。至少写清光从哪边来、软还是硬")
    if mode == "lock" and not re.search(r"\b(keep|preserve)\b", text):
        warnings.append("没有写保持句。以 Keep the photograph exactly as it is. 这类句子开头")
    if mode == "multiref":
        mentioned = {int(n) for n in re.findall(r"\b(?:image|reference)\s+(\d+)", text)}
        if refs and mentioned - set(range(1, refs + 1)):
            errors.append(f"提到的图号 {sorted(mentioned)} 超出实际参考数量 {refs}")
        if refs and not mentioned:
            errors.append("没有给参考图分配角色。写明 image 1 是商品、image 2 是场景")
    if mode == "video":
        hits = found(PRODUCT_MOTION, text)
        if hits:
            errors.append(f"要求商品或镜头环绕、翻转 {hits}。商品保持静止，只让镜头小幅移动、环境变化")
        cameras = found(CAMERA, text)
        if not cameras:
            warnings.append("没有写镜头。写一种镜头运动，或写 static camera")
        if len(set(cameras) - {"static camera", "locked-off", "locked off"}) > 1:
            warnings.append(f"写了多种镜头运动 {cameras}。一段 5 秒左右的片段只放一种")
    return words, errors, warnings


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", required=True, choices=sorted(LENGTH))
    parser.add_argument("--refs", type=int, default=0, help="multiref 模式下实际接入的参考图数量")
    parser.add_argument("--file")
    parser.add_argument("prompt", nargs="?")
    arguments = parser.parse_args()
    if bool(arguments.file) == bool(arguments.prompt):
        parser.error("提供提示词文本或 --file 之一")
    prompt = open(arguments.file, encoding="utf-8").read() if arguments.file else arguments.prompt
    words, errors, warnings = lint(prompt.strip(), arguments.mode, arguments.refs)
    print(f"{arguments.mode} · {words} 词")
    for item in errors:
        print("错误：" + item)
    for item in warnings:
        print("提醒：" + item)
    if not errors and not warnings:
        print("通过")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
