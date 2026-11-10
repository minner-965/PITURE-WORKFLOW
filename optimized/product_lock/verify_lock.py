#!/usr/bin/env python3
"""复核商品像素锁定：保护区内，成图必须与布局图逐像素相同。

用法：
    python3 verify_lock.py --layout x_layout.png --result x_result.png --protect x_protect.png

三张图都是工作流的留档输出，尺寸相同。退出码 0 表示保护区零改动。
"""
import argparse
import json
import sys

import numpy as np
from PIL import Image


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("layout", "result", "protect"):
        parser.add_argument(f"--{name}", required=True)
    arguments = parser.parse_args()
    layout = np.asarray(Image.open(arguments.layout).convert("RGB")).astype(np.int16)
    result = np.asarray(Image.open(arguments.result).convert("RGB")).astype(np.int16)
    protect = np.asarray(Image.open(arguments.protect).convert("L"))
    if not (layout.shape == result.shape and layout.shape[:2] == protect.shape):
        sys.exit(f"尺寸不一致：layout {layout.shape} result {result.shape} protect {protect.shape}")
    core = protect == 255
    difference = np.abs(layout - result).max(axis=2)
    report = {
        "protected_pixels": int(core.sum()),
        "protected_share_of_image": round(float(core.mean()), 4),
        "protected_changed_pixels": int((difference[core] > 0).sum()),
        "protected_max_difference": int(difference[core].max()) if core.any() else 0,
        "edited_pixels": int((difference[~core] > 0).sum()),
    }
    report["passed"] = report["protected_pixels"] > 0 and report["protected_changed_pixels"] == 0
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
