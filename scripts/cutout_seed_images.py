"""一次性抠图脚本：把演示商品图抠白底、居中放正，覆盖回 seed 目录。

仅处理传入的文件名列表，避免误伤已是干净白底的图。
"""
import sys
from pathlib import Path
from PIL import Image
from rembg import remove

SEED_DIR = Path(__file__).resolve().parent.parent / "app/static/product-images/seed"
CANVAS = 800  # 正方形白底画布


def process(name: str) -> str:
    src = SEED_DIR / name
    if not src.exists():
        return f"跳过(不存在): {name}"

    img = Image.open(src).convert("RGBA")
    cut = remove(img)  # 抠掉背景，得到透明前景

    # 裁到前景实际边界
    bbox = cut.getbbox()
    if bbox:
        cut = cut.crop(bbox)

    # 等比缩放到画布的 ~82%，居中贴到白底
    target = int(CANVAS * 0.82)
    w, h = cut.size
    scale = min(target / w, target / h)
    cut = cut.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)

    canvas = Image.new("RGBA", (CANVAS, CANVAS), (255, 255, 255, 255))
    x = (CANVAS - cut.width) // 2
    y = (CANVAS - cut.height) // 2
    canvas.paste(cut, (x, y), cut)

    out = canvas.convert("RGB")
    out.save(src, "PNG")
    return f"OK: {name} ({w}x{h} -> {CANVAS}x{CANVAS})"


if __name__ == "__main__":
    names = sys.argv[1:]
    for n in names:
        print(process(n))
