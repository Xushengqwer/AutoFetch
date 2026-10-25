import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { loadImage, createNodeTextEngine } from "../recognize/node-io.ts";
import { recognizeReport } from "../../lib/recognize/index.ts";

const CONFIG_PATH = "C:/sanguo_report_collector/config.local.json";
const configRaw = await readFile(CONFIG_PATH, "utf-8");
const config = JSON.parse(configRaw);

const SITE = config.site_url.replace(/\/+$/, "");
const TOKEN = config.import_token;
const REPORTS_DIR = "C:/sanguo_report_collector/战报导出/2026-10-24_全天战报";

console.log("=".repeat(60));
console.log(`山河战录 · 2026-10-24 全量战报正式站上传工具 (契约版本 2)`);
console.log(`目标站点: ${SITE}`);
console.log(`令牌前缀: ${TOKEN.slice(0, 10)}... (本地配置文件保密加载)`);
console.log("=".repeat(60));

const manifestRaw = await readFile(join(REPORTS_DIR, "manifest.json"), "utf-8");
const manifest = JSON.parse(manifestRaw);
console.log(`读取到战报清单: 共 ${manifest.length} 场战报\n`);

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
  return { ownTroopType: ownTroop || "弓", enemyTroopType: enemyTroop || ownTroop || "弓" };
}

const engine = createNodeTextEngine();

async function uploadOne(item) {
  const filePath = join(REPORTS_DIR, item.filename);
  const imageBytes = await readFile(filePath);
  const sha256 = createHash("sha256").update(imageBytes).digest("hex");

  const rgbaImage = await loadImage(filePath);
  const r = await recognizeReport(rgbaImage, engine, { expectedOwnName: "绮罗生" });

  if (r.isLandBattle) {
    console.log(`  ⏭️ [过滤] 土地战/NPC战守军，跳过上传`);
    return { status: "skipped_land", file: item.filename };
  }

  const { ownTroopType, enemyTroopType } = extractTroopTypes(item.ocrLines);
  const battleAt = `${item.battleDate} ${item.battleTime}`;

  // Contract v2 Reading schema (strictly without ocrLines)
  const reading = {
    result: r.result,
    ownName: r.ownName || "绮罗生",
    ownInitial: r.ownInitial,
    ownRemaining: r.ownRemaining,
    ownWounded: r.ownWounded,
    merit: r.merit ?? 0,
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
    ownTroopType: ownTroopType,
    enemyTroopType: enemyTroopType,
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

  for (let attempt = 0; attempt < 5; attempt++) {
    const form = new FormData();
    form.set("image", new Blob([imageBytes]), item.filename);
    form.set("reading", JSON.stringify(envelope));

    let resp;
    try {
      resp = await fetch(`${SITE}/api/import/reports`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${TOKEN}`,
          "User-Agent": "shanhe-capture/0.2.0"
        },
        body: form
      });
    } catch (netErr) {
      console.log(`  ⚠️ 网络波动，等待 5 秒重试: ${netErr.message}`);
      await new Promise(res => setTimeout(res, 5000));
      continue;
    }

    const body = await resp.json().catch(() => null);
    if (resp.ok) {
      return { ...body.data, file: item.filename, battleAt, trusted: r.trusted };
    }
    if (resp.status === 429) {
      const waitSec = Number(resp.headers.get("Retry-After")) || 5;
      console.log(`  ⏳ 触发限速 (429)，等待 ${waitSec + 1} 秒后重试...`);
      await new Promise(res => setTimeout(res, (waitSec + 1) * 1000));
      continue;
    }
    throw new Error(`Upload failed (${resp.status}): ${JSON.stringify(body)}`);
  }
  throw new Error("429 重试次数超限");
}

try {
  let importedCount = 0;
  let duplicateCount = 0;
  let skippedLandCount = 0;
  let failedCount = 0;

  for (let i = 0; i < manifest.length; i++) {
    const item = manifest[i];
    console.log(`\n[${i + 1}/${manifest.length}] 正在识图并上传: ${item.filename}`);
    try {
      const res = await uploadOne(item);
      if (res.status === "imported") importedCount++;
      else if (res.status === "duplicate") duplicateCount++;
      else if (res.status === "skipped_land") skippedLandCount++;
      console.log(`  -> 成功: ${res.status} | reportId: ${res.reportId || "已存"} | 战斗时间: ${res.battleAt || item.battleDate} | 可信: ${res.trusted}`);
    } catch (err) {
      failedCount++;
      console.error(`  -> ❌ 失败: ${err.message}`);
    }
  }

  console.log("\n" + "=".repeat(60));
  console.log(`🎉 76 场 2026-10-24 战报正式站上传完成！`);
  console.log(`📊 统计: 新导入 ${importedCount} 场 | 重复标记 ${duplicateCount} 场 | 土地战过滤 ${skippedLandCount} 场 | 失败 ${failedCount} 场`);
  console.log("=".repeat(60));
} finally {
  await engine.close();
}
