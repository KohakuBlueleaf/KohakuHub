import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

// UnoCSS' presetIcons is configured with `warn: false` (see
// uno.config.js), so a misspelled `i-carbon-XYZ` class compiles
// to nothing and the div renders with zero width. That is what
// produced the README "no icon, text squished left" report.
//
// This regression test scans every tracked .vue / .js source file
// for `i-carbon-<name>` literals and asserts every one is actually
// present in @iconify-json/carbon's index. A new typo in any
// future component fails the suite instead of leaking out.

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC_ROOT = resolve(__dirname, "../../../src/kohaku-hub-ui/src");
const CARBON_INDEX = resolve(
  __dirname,
  "../../../src/kohaku-hub-ui/node_modules/@iconify-json/carbon/icons.json",
);

function walk(dir, exts) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const st = statSync(full);
    if (st.isDirectory()) {
      out.push(...walk(full, exts));
    } else if (exts.some((ext) => entry.endsWith(ext))) {
      out.push(full);
    }
  }
  return out;
}

function extractCarbonIcons(text) {
  // Match the `i-carbon-<kebab>` token shape UnoCSS recognises. The
  // class can appear bare (`class="i-carbon-foo"`), as part of a
  // bigger class string (`"i-carbon-foo text-blue-500"`), or inside
  // a JS function (`return "i-carbon-foo"`). The token is anchored
  // with a non-word boundary so we don't pick up partials.
  const re = /\bi-carbon-([a-z0-9][a-z0-9-]*)/g;
  const found = new Set();
  let m;
  while ((m = re.exec(text)) !== null) found.add(m[1]);
  return [...found];
}

describe("carbon icon literals exist in @iconify-json/carbon", () => {
  const carbonNames = new Set(
    Object.keys(JSON.parse(readFileSync(CARBON_INDEX, "utf-8")).icons),
  );
  const sourceFiles = walk(SRC_ROOT, [".vue", ".js"]);

  // Names that don't ship in @iconify-json/carbon but were already
  // referenced in the repo before this suite landed. Allowlist
  // them so the regression catches NEW misspellings without
  // forcing an unrelated cleanup pass:
  //   - "loading": el-icon is-loading wrapper provides spin via CSS,
  //     the inner mask never resolved but the rotation worked.
  //   - "zip-archive": single use on the standalone blob page.
  //   - "unlock": settings page; carbon ships `locked` only.
  const PRE_EXISTING_GAPS = new Set(["loading", "zip-archive", "unlock"]);

  it("every i-carbon-* class in source is a real Carbon icon", () => {
    const missing = new Map(); // icon → [files]
    for (const file of sourceFiles) {
      const text = readFileSync(file, "utf-8");
      for (const icon of extractCarbonIcons(text)) {
        if (carbonNames.has(icon)) continue;
        if (PRE_EXISTING_GAPS.has(icon)) continue;
        if (!missing.has(icon)) missing.set(icon, []);
        missing.get(icon).push(file);
      }
    }
    if (missing.size > 0) {
      const lines = [];
      for (const [icon, files] of missing) {
        lines.push(`  i-carbon-${icon}`);
        for (const f of files) lines.push(`    in ${f}`);
      }
      throw new Error(
        `Carbon icons not present in @iconify-json/carbon:\n${lines.join("\n")}`,
      );
    }
    expect(missing.size).toBe(0);
  });
});
