import fs from "node:fs";
import path from "node:path";

const rootLockPath = path.resolve("package-lock.json");
const nestedFrontendLockPath = path.resolve("frontend/package-lock.json");

if (fs.existsSync(nestedFrontendLockPath)) {
  console.error("frontend/package-lock.json must not be tracked in this npm workspace repo.");
  process.exit(1);
}

if (!fs.existsSync(rootLockPath)) {
  console.error("package-lock.json is missing at the workspace root.");
  process.exit(1);
}

const lock = JSON.parse(fs.readFileSync(rootLockPath, "utf8"));
const packages = lock.packages ?? {};
const rolldown = packages["node_modules/rolldown"] ?? packages["frontend/node_modules/rolldown"];
const lightningcss =
  packages["node_modules/lightningcss"] ?? packages["frontend/node_modules/lightningcss"];

if (!rolldown) {
  console.error("rolldown is missing from the root workspace lockfile.");
  process.exit(1);
}

if (!lightningcss) {
  console.error("lightningcss is missing from the root workspace lockfile.");
  process.exit(1);
}

const requiredBindings = [
  {
    name: "@rolldown/binding-linux-x64-gnu",
    owner: "rolldown",
    dependencies: rolldown.optionalDependencies ?? {},
  },
  {
    name: "lightningcss-linux-x64-gnu",
    owner: "lightningcss",
    dependencies: lightningcss.optionalDependencies ?? {},
  },
];

for (const binding of requiredBindings) {
  if (!(binding.name in binding.dependencies)) {
    console.error(`${binding.owner} is missing the ${binding.name} optional binding in the root workspace lockfile.`);
    process.exit(1);
  }
}

for (const binding of requiredBindings) {
  const bindingEntries = [
    `node_modules/${binding.name}`,
    `frontend/node_modules/${binding.name}`,
  ];

  if (!bindingEntries.some((entry) => packages[entry])) {
    console.error(`Root workspace lockfile is missing the ${binding.name} entry.`);
    process.exit(1);
  }
}

if (!("@rolldown/binding-linux-x64-gnu" in (rolldown.optionalDependencies ?? {}))) {
  console.error("rolldown is missing the linux-x64-gnu optional binding in the root workspace lockfile.");
  process.exit(1);
}

console.log("Workspace lockfile structure looks valid.");
