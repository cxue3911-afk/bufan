from pathlib import Path
from PIL import Image
import re
import shutil

src = Path('data/repair_12739b')
dst = Path('data/repair_12739_png')
if dst.exists():
    shutil.rmtree(dst)
shutil.copytree(src, dst)
assets = dst / 'article_assets'
md = dst / 'article.md'
text = md.read_text(encoding='utf-8')
converted = 0
for path in list(assets.glob('*.gif')):
    image = Image.open(path)
    if getattr(image, 'n_frames', 1) > 1:
        image.seek(0)
    output = path.with_suffix('.png')
    converted_image = image.convert('RGB')
    converted_image.save(output, 'PNG', optimize=True)
    converted_image.close()
    image.close()
    text = text.replace(path.name, output.name)
    path.unlink()
    converted += 1
md.write_text(text, encoding='utf-8')
print(f'converted={converted} refs={len(re.findall(r"!\\[.*?\\]\\((.*?)\\)", text))}')
