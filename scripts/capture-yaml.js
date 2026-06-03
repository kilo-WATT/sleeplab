const { chromium } = require('playwright');
const yaml = require('js-yaml');
const fs = require('fs');
const path = require('path');

const configPath = path.resolve(__dirname, '../snapshots.yml');
const outputDir = path.resolve(__dirname, '../docs/public/ui-snapshots');

async function capture() {
  if (!fs.existsSync(outputDir)) {
    fs.mkdirSync(outputDir, { recursive: true });
  }

  const fileContents = fs.readFileSync(configPath, 'utf8');
  const data = yaml.load(fileContents);

  const browser = await chromium.launch();
  const page = await browser.newPage();
  
  await page.setViewportSize({ width: 1280, height: 720 });

  for (const snapshot of data.snapshots) {
    const url = `http://127.0.0.1:4173${snapshot.url}`;
    console.log(`Capturing ${snapshot.name} from ${url}`);
    
    await page.goto(url, { waitUntil: 'networkidle' });
    const filename = `${snapshot.name}.png`;
    await page.screenshot({ path: path.join(outputDir, filename) });
  }

  await browser.close();
}

capture().catch(err => {
  console.error(err);
  process.exit(1);
});
