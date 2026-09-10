# ODBM - Oracle Database Monitor
# Copyright (C) 2025 Bruchsaal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Re-fetch the vendored browser libraries.

Versions are pinned to match THIRD-PARTY-NOTICES.md. If you bump one here,
update the notices too - these CDN builds ship with their upstream copyright
banners stripped, so that file is the only attribution they have.
"""
import os
import urllib.request

ASSETS_DIR = os.path.join("static", "lib")

FILES = {
    "tailwind.js":   "https://cdn.tailwindcss.com/3.4.17",
    "alpine.js":     "https://cdn.jsdelivr.net/npm/alpinejs@3.13.3/dist/cdn.min.js",
    "apexcharts.js": "https://cdn.jsdelivr.net/npm/apexcharts@5.3.6/dist/apexcharts.min.js",
}

os.makedirs(ASSETS_DIR, exist_ok=True)
print(f"Downloading assets to {ASSETS_DIR}...")

failed = []
for filename, url in FILES.items():
    path = os.path.join(ASSETS_DIR, filename)
    try:
        print(f" - {filename} <- {url}")
        urllib.request.urlretrieve(url, path)
    except Exception as e:
        print(f"   FAILED: {e}")
        failed.append((filename, url))

if failed:
    print("\nSome downloads failed. Fetch these manually into static/lib/:")
    for filename, url in failed:
        print(f"  {filename}  {url}")
else:
    print("\nDone. Remember: versions here must match THIRD-PARTY-NOTICES.md.")
