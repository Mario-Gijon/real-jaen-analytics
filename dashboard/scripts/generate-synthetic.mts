/**
 * Regenerates `data/synthetic` from the fixed seed:
 *
 *   npm run data:generate
 *
 * The output is versioned; `tests/data/dataset.test.ts` fails when
 * it no longer matches the generator.
 */
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { datasetFiles, generateDataset } from "./synthetic/generate.mts";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "data", "synthetic");
rmSync(join(root, "rounds"), { recursive: true, force: true });
const files = datasetFiles(generateDataset());
for (const [path, content] of files) {
  const target = join(root, path);
  mkdirSync(dirname(target), { recursive: true });
  writeFileSync(target, content);
}
console.log(`data/synthetic: ${files.size} archivos generados.`);
