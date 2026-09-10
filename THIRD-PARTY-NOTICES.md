# Third-Party Notices

ODBM is distributed under the GNU Affero General Public License v3.0 or later
(see `LICENSE`). It bundles and redistributes the third-party components below,
each under its own licence. All are compatible with the AGPL-3.0.

The vendored browser libraries in `static/lib/` are minified CDN builds whose
upstream copyright banners were stripped by the build process. Their notices are
reproduced here to satisfy the attribution requirement.

---

## Bundled JavaScript (`static/lib/`)

### Alpine.js 3.13.3 — MIT
Copyright (c) 2019-2025 Caleb Porzio and contributors
<https://github.com/alpinejs/alpine>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

### Tailwind CSS 3.4.17 — MIT
Copyright (c) Tailwind Labs, Inc.
<https://github.com/tailwindlabs/tailwindcss>

Bundled sub-components, each MIT:
- fill-range, is-number, to-regex-range — Copyright (c) 2014-present, Jon Schlinkert
- cssesc — Copyright (c) Mathias Bynens <https://mths.be/cssesc>

---

### ApexCharts 5.3.6 — MIT
Copyright (c) 2018-2025 ApexCharts
<https://github.com/apexcharts/apexcharts.js>

Bundled sub-components, each MIT:
- @svgdotjs/svg.js — Copyright (c) 2018 Wout Fierens
- @svgdotjs/svg.select.js 4.0.1 — Copyright (c) Ulrich-Matthias Schäfer
- @svgdotjs/svg.resize.js 2.0.4 — Copyright (c) Ulrich-Matthias Schäfer

  (The upstream banner in svg.resize.js reads `@copyright [object Object]`, a
  build defect; the holder is recorded here from the project's own repository.)

---

## Python runtime (bundled into the released executable)

### python-oracledb — Apache-2.0 OR UPL-1.0

Required NOTICE, reproduced per Apache-2.0 section 4(d):

    Copyright (c) 2016, 2025, Oracle and/or its affiliates.

python-oracledb additionally bundles Cython 0.24.1 under Apache-2.0.

### Other bundled dependencies

| Component | Licence |
| --- | --- |
| FastAPI | MIT |
| Starlette | BSD-3-Clause |
| Pydantic, pydantic-core | MIT |
| Uvicorn | BSD-3-Clause |
| cryptography | Apache-2.0 OR BSD-3-Clause |
| anyio, h11, annotated-types, typing-inspection, cffi | MIT |
| click, pycparser, idna | BSD-3-Clause |
| typing_extensions | PSF-2.0 |
| packaging | Apache-2.0 OR BSD-2-Clause |
| certifi | MPL-2.0 |

`certifi` is under the Mozilla Public License 2.0, which is file-level copyleft.
Its unmodified source is available at <https://github.com/certifi/python-certifi>.

---

## Build tooling (not linked into the product)

### PyInstaller — GPL-2.0-or-later WITH Bootloader Exception
Copyright (c) 2010-2023, PyInstaller Development Team
Copyright (c) 2005-2009, Giovanni Bajo
Based on previous work under copyright (c) 2002 McMillan Enterprises, Inc.

The bootloader embedded in the released executable carries an explicit
exception permitting distribution in combination with programs under any
licence, so it imposes no additional conditions on ODBM.
