import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

if (!process.env.VIZ_PKG || !process.env.SHARP_PKG) {
  throw new Error("Set VIZ_PKG and SHARP_PKG to the temporary package entry points.");
}

const { instance } = await import(pathToFileURL(process.env.VIZ_PKG));
const { default: sharp } = await import(pathToFileURL(process.env.SHARP_PKG));
const viz = await instance();
const directory = path.dirname(fileURLToPath(import.meta.url));

for (const name of fs.readdirSync(directory).filter((file) => file.endsWith(".dot"))) {
  const sourcePath = path.join(directory, name);
  const svg = viz.renderString(fs.readFileSync(sourcePath, "utf8"), {
    format: "svg",
    engine: "dot",
  });
  const svgPath = sourcePath.replace(/\.dot$/, ".svg");
  const pngPath = sourcePath.replace(/\.dot$/, ".png");
  fs.writeFileSync(svgPath, svg);
  await sharp(Buffer.from(svg)).png().toFile(pngPath);
  console.log(`Rendered ${path.basename(svgPath)} and ${path.basename(pngPath)}`);
}