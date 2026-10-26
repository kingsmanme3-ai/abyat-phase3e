#!/usr/bin/env bash
# يحمّل خط Tajawal (OFL) إلى web/fonts مرة واحدة ليُخدَّم من منظومتك مباشرة على كل الأجهزة.
# التشغيل من جذر المشروع:  bash scripts/get-fonts.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p web/fonts
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"   # لطلب woff2
CSS=$(curl -fsSL -A "$UA" "https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700&display=swap")
python3 - "$CSS" <<'PY'
import re, sys, urllib.request, pathlib
css = sys.argv[1]
names = {"400": "Regular", "500": "Medium", "700": "Bold"}
for block in re.findall(r"/\*\s*(\w[\w-]*)\s*\*/\s*@font-face\s*\{(.*?)\}", css, re.S):
    subset, body = block
    if subset != "arabic" and subset != "latin": continue
    w = re.search(r"font-weight:\s*(\d+)", body).group(1)
    url = re.search(r"url\((https://[^)]+\.woff2)\)", body).group(1)
    out = pathlib.Path("web/fonts") / (f"Tajawal-{names[w]}.woff2" if subset == "arabic" else f"Tajawal-{names[w]}-latin.woff2")
    urllib.request.urlretrieve(url, out); print("saved", out)
PY
echo "تم. أعد تحميل الصفحة (Ctrl+F5)."
