// @ts-check
const path = require('path');
const fs = require('fs');
const os = require('os');
const { test, expect } = require('@playwright/test');
const { login, uniqueName } = require('./helpers');

/** A minimal, valid plain-text file -- deliberately not a PDF, since the
 * point here is exercising the upload/list/original/archive UI flow, not
 * re-testing PDF text extraction (already covered by backend/tests). */
function makeTestFile() {
  const filePath = path.join(os.tmpdir(), `e2e-nalaz-${Date.now()}.txt`);
  fs.writeFileSync(filePath, 'CRP: 12 mg/L, u granicama referentnog opsega.\nPacijent bez akutnih tegoba.');
  return filePath;
}

async function createAndOpenPatient(page) {
  await page.locator('button.nav-item[data-view="workspace"]').click();
  await page.locator('#newPatientBtn').click();
  await expect(page.locator('#patientDialog')).toBeVisible();
  const name = uniqueName('E2E Dokument Pacijent');
  await page.locator('#patientForm [name=full_name]').fill(name);
  await page.locator('#patientForm button:not(.close)').click();
  await expect(page.locator('#patientDialog')).toBeHidden();
  await expect(page.locator('#patientName')).toHaveText(name, { timeout: 10_000 });
  return name;
}

test.describe('Dokumenti pacijenta', () => {
  test('upload, pregled u listi, otvaranje originala, i arhiviranje', async ({ page }) => {
    await login(page);
    await createAndOpenPatient(page);

    // Upload.
    const filePath = makeTestFile();
    await page.locator('#fileInput').setInputFiles(filePath);
    await expect(page.locator('#toast')).toContainText('obrađen', { timeout: 15_000 });

    // The document list lives under the "Dokumenti" chart tab.
    await page.locator('.chart-tab[data-tab="dokumenti"]').click();
    const filename = path.basename(filePath);
    const row = page.locator('#documents .row', { hasText: filename });
    await expect(row).toBeVisible({ timeout: 10_000 });

    // Opening the original document opens a new tab/window with the raw
    // file content -- this exercises the exact endpoint the AI-citation
    // "Otvori dokument" buttons (differential analysis, lab results) also
    // use, so a regression there would show up here too.
    const [popup] = await Promise.all([
      page.waitForEvent('popup'),
      row.locator('.open-original').click(),
    ]);
    await popup.waitForLoadState();
    expect(popup.url()).not.toBe('about:blank');
    await popup.close();

    // Archiving prompts for a reason via a native browser dialog.
    page.once('dialog', (dialog) => dialog.accept('E2E test arhiviranje'));
    await row.locator('.archive-doc').click();
    await expect(page.locator('#documents .row.archived-row', { hasText: filename })).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('#documents .row', { hasText: filename })).toContainText('arhiviran');

    fs.unlinkSync(filePath);
  });

  test('otkazivanje arhiviranja (prazan razlog) ne menja status dokumenta', async ({ page }) => {
    await login(page);
    await createAndOpenPatient(page);
    const filePath = makeTestFile();
    await page.locator('#fileInput').setInputFiles(filePath);
    await expect(page.locator('#toast')).toContainText('obrađen', { timeout: 15_000 });
    await page.locator('.chart-tab[data-tab="dokumenti"]').click();
    const filename = path.basename(filePath);
    const row = page.locator('#documents .row', { hasText: filename });
    await expect(row).toBeVisible({ timeout: 10_000 });

    page.once('dialog', (dialog) => dialog.dismiss());
    await row.locator('.archive-doc').click();
    // Give the (non-)action a moment, then confirm the document is still active.
    await page.waitForTimeout(500);
    await expect(page.locator('#documents .row.archived-row', { hasText: filename })).toHaveCount(0);

    fs.unlinkSync(filePath);
  });
});
