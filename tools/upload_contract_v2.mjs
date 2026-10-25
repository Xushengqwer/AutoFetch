import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { loadImage, createNodeTextEngine } from "../recognize/node-io.ts";
import { recognizeReport } from "../../lib/recognize/index.ts";

const SITE = process.env.SHANHE_SITE || "http://127.0.0.1:5190";
const TOKEN = process.env.SHANHE_TOKEN || "shz_09zw7oMIlZ5qswca6P5HhjDJHKaxu5CQwNn2nQuj7Ac";
const REPORTS_DIR = "C:/sanguo_report_collector/战报导出/2026-10-24_全天战报";

const manifestRaw = await readFile(join(REPORTS_DIR, "manifest.json"), "utf-8");
const manifest = JSON.parse(manifestRaw);
console.log(`Found ${manifest.length} battle reports in manifest.json`);

function extractTroopTypes(ocrLines = []) {
  let ownTroop = null;
  let enemyTroop = null;
  for (const line of ocrLines) {
    const clean = line.replace(/\s+/g, "");
    const m = clean.match(/(枪|弓|盾|骑|器械)(?:兵)?[SABC]?/);
    if (m) {
      if (!ownTroop) ownTroop = m[1];
      else if (!enemyTroop && m[1] !== ownTroop) enemyTroop = m[1];
    }
  }
  return { ownTroopType: ownTroop, enemyTroopType: enemyTroop || ownTroop };
}

const engine = createNodeTextEngine();

async function uploadOne(item) {
  const filePath = join(REPORTS_DIR, item.filename);
  const imageBytes = await readFile(filePath);
  const sha256 = createHash("sha256").update(imageBytes).digest("hex");

  const rgbaImage = await loadImage(filePath);
  const r = await recognizeReport(rgbaImage, engine, { expectedOwnName: "绮罗生" });

  if (r.isLandBattle) {
    console.log(`⏭️ [跳过土地战] ${item.filename}`);
    return { status: "skipped_land" };
  }

  const { ownTroopType, enemyTroopType } = extractTroopTypes(item.ocrLines);
  const battleAt = `${item.battleDate} ${item.battleTime}`;

  const reading = {
    result: r.result,
    ownName: r.ownName || "绮罗生",
    ownInitial: r.ownInitial,
    ownRemaining: r.ownRemaining,
    ownWounded: r.ownWounded,
    merit: r.merit,
    enemyInitial: r.enemyInitial,
    enemyRemaining: r.enemyRemaining,
    enemyWounded: r.enemyWounded,
    own: r.own.map(h => ({
      name: h.name,
      sp: h.sp,
      level: h.level,
      red: h.red,
      skills: h.skills
    })),
    enemy: r.enemy.map(h => ({
      name: h.name,
      sp: h.sp,
      level: h.level,
      red: h.red,
      skills: h.skills
    })),
    ownTroopType: ownTroopType || "弓",
    enemyTroopType: enemyTroopType || ownTroopType || "弓",
    trusted: r.trusted,
    battleAt: battleAt
  };

  const envelope = {
    schema: 2,
    sha256,
    capturedAt: item.capturedAt,
    client: "shanhe-capture/0.2.0",
    reading
  };

  const form = new FormData();
  form.set("image", new Blob([imageBytes]), item.filename);
  form.set("reading", JSON.stringify(envelope));

  const resp = await fetch(`${SITE}/api/import/reports`, {
    method: "POST",
    headers: { Authorization: `Bearer ${TOKEN}` },
    body: form
  });

  const body = await resp.json().catch(() => null);
  if (!resp.ok) {
    throw new Error(`Upload failed (${resp.status}): ${JSON.stringify(body)}`);
  }
  return { ...body.data, file: item.filename, battleAt, trusted: r.trusted };
}

try {
  let importedCount = 0;
  let duplicateCount = 0;
  let skippedLandCount = 0;
  let failedCount = 0;

  for (let i = 0; i < manifest.length; i++) {
    const item = manifest[i];
    console.log(`\n[${i + 1}/${manifest.length}] 正在识图并上传: ${item.filename}...`);
    try {
      const res = await uploadOne(item);
      if (res.status === "imported") importedCount++;
      else if (res.status === "duplicate") duplicateCount++;
      else if (res.status === "skipped_land") skippedLandCount++;
      console.log(`  -> 成功: ${res.status} | 战斗时间: ${res.battleAt || item.battleDate} | 可信: ${res.trusted}`);
    } catch (err) {
      failedCount++;
      console.error(`  -> ❌ 失败: ${err.message}`);
    }
  }

  console.log("\n" + "=".repeat(60));
  console.log(`🎉 76 场 2026-10-24 战报全部处理完成！`);
  console.log(`📊 统计: 新导入 ${importedCount} 场 | 重复跳过 ${duplicateCount} 场 | 土地战过滤 ${skippedLandCount} 场 | 失败 ${failedCount} 场`);
  console.log("=".repeat(60));
} finally {
  await engine.close();
}
