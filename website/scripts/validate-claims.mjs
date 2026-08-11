import fs from "node:fs";
import path from "node:path";

const root = process.cwd();
const failures = [];
const extensions = new Set([".js", ".jsx", ".ts", ".tsx", ".md", ".mdx", ".json"]);

function walk(relative) {
  const absolute = path.join(root, relative);
  if (!fs.existsSync(absolute)) return [];
  const output = [];
  for (const entry of fs.readdirSync(absolute, { withFileTypes: true })) {
    if (["node_modules", ".next", "out", "coverage"].includes(entry.name)) continue;
    const child = path.join(relative, entry.name);
    if (entry.isDirectory()) output.push(...walk(child));
    else if (extensions.has(path.extname(entry.name).toLowerCase())) output.push(child);
  }
  return output;
}

const files = ["app", "components", "content"].flatMap(walk);
const entries = files.map((file) => ({
  file,
  text: fs.readFileSync(path.join(root, file), "utf8"),
}));
const joined = entries.map(({ file, text }) => `\n/* ${file} */\n${text}`).join("\n");
const normalized = joined.toLocaleLowerCase("de-DE");

const forbiddenClaims = [
  [/\bwir speichern keine telefonnummern\b/i, "Absolute phone-number storage claim"],
  [/\btelefonnummern werden nicht gespeichert\b/i, "Absolute phone-number storage claim"],
  [/\bkeine daten werden gespeichert\b/i, "Absolute no-data-storage claim"],
  [/\b100\s*%\s*(korrekt|richtig|sicher|erfolgreich)\b/i, "Unsupported 100% claim"],
  [/\bgarantiert(?:e|er|es|en)?\s+(?:erfolg|rückzahlung|rechtssicherheit|fristwahrung)\b/i, "Unsupported guarantee"],
  [/\bfehlerfrei(?:e|er|es|en)?\b/i, "Unsupported error-free claim"],
  [/\brechtssicher(?:e|er|es|en)?\s+(?:beratung|prüfung|entscheidung)\b/i, "Unsupported legal-safety claim"],
  [/\bersetzt\s+(?:einen\s+)?anwalt\b/i, "Claim that Sam replaces a lawyer"],
  [/\boffizielle\s+(?:deutsche\s+)?behörde\b/i, "Government-affiliation claim"],
  [/§\s*5\s*TMG/i, "Superseded TMG citation"],
  [/ec\.europa\.eu\/consumers\/odr/i, "Discontinued EU ODR-platform link"],
];

for (const [pattern, label] of forbiddenClaims) {
  for (const { file, text } of entries) {
    if (pattern.test(text)) failures.push(`${label}: ${file}`);
  }
}

const requiredBoundaries = [
  [/§\s*5\s*DDG/i, "§ 5 DDG reference"],
  [/(datenschutz-grundverordnung|DSGVO)/i, "DSGVO reference"],
  [/(TDDDG|§\s*25)/i, "TDDDG/cookie-storage boundary"],
  [/(künstliche intelligenz|KI-System)/i, "AI-system transparency"],
  [/(keine rechtsberatung|ersetzt keine rechtsberatung|keine rechtliche vertretung)/i, "legal-service limitation"],
  [/(kann fehler machen|können fehler enthalten|keine garantie)/i, "AI/error limitation"],
  [/(5\s*(plätze|plaetze|places|slots))/i, "Wave 1 capacity"],
];
for (const [pattern, label] of requiredBoundaries) {
  if (!pattern.test(joined)) failures.push(`Missing required boundary: ${label}`);
}

if (!normalized.includes("roh-telefonnummer") && !normalized.includes("rohen telefonnummer") && !normalized.includes("telefonnummern aus logs")) {
  failures.push("Missing precise raw-phone/logging explanation");
}
if (!normalized.includes("freiwillig") || !normalized.includes("closed beta")) {
  failures.push("Missing voluntary Closed Beta wording");
}

if (failures.length) {
  console.error("Legal/marketing claims validation failed:");
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log(`Legal/marketing claims validation passed (${files.length} scoped files).`);
