// @ts-check
const { test, expect } = require('@playwright/test');
const { authenticator } = require('otplib');
const { login, uniqueName } = require('./helpers');

test.describe('Prijava', () => {
  test('uspešna prijava vodi na kontrolnu tablu', async ({ page }) => {
    await login(page);
    await expect(page.locator('#dashboardView')).toBeVisible();
    await expect(page.locator('#currentUser')).toHaveText('Dr. Demo');
  });

  test('pogrešna lozinka prikazuje grešku i ne otvara radni prostor', async ({ page }) => {
    await page.goto('/');
    await page.locator('#loginForm [name=organization]').fill('demo-clinic');
    await page.locator('#loginForm [name=username]').fill('doctor');
    await page.locator('#loginForm [name=password]').fill('pogresna-lozinka');
    await page.locator('#loginForm button').click();
    await expect(page.locator('#toast')).toContainText(/lozinka|username|password/i, { timeout: 5_000 });
    await expect(page.locator('#loginOverlay')).toBeVisible();
  });

  test('sesija preživljava osvežavanje stranice (HttpOnly kolačić)', async ({ page }) => {
    await login(page);
    await page.reload();
    // No localStorage token to restore -- this only works if the session
    // cookie set by the server on login is actually being sent back and
    // accepted, which is the real thing worth checking after the
    // localStorage->cookie migration (v1.20.0).
    await expect(page.locator('#dashboardView')).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('#loginOverlay')).toBeHidden();
  });
});

test.describe('Višefaktorska prijava (MFA)', () => {
  // Uses a disposable admin-created user for the whole MFA lifecycle so
  // this never touches the shared 'doctor' demo account's MFA state --
  // other tests in this suite log in as plain 'doctor' and would break if
  // that account suddenly required a TOTP code.
  test('podešavanje, prijava sa kodom, i isključivanje', async ({ page }) => {
    const username = `mfa-e2e-${Date.now()}`;
    const password = 'MfaTestLoz123';

    await login(page, { username: 'admin', password: 'admin123' });
    await page.locator('#newUserBtn').click();
    await page.locator('#userForm [name=username]').fill(username);
    await page.locator('#userForm [name=full_name]').fill('MFA E2E Test');
    await page.locator('#userForm [name=role]').selectOption('doctor');
    await page.locator('#userForm [name=password]').fill(password);
    await page.locator('#userForm button[type=submit], #userForm button:not([type])').first().click();
    await expect(page.locator('#toast')).toBeVisible();
    await page.locator('#logoutBtn').click();
    await expect(page.locator('#loginOverlay')).toBeVisible();

    // New user must set a real password before anything else (see
    // must_change_password in deps.py) -- log in once to trigger that flow.
    await login(page, { username, password });
    if (await page.locator('#passwordDialog').isVisible().catch(() => false)) {
      const newPassword = 'MfaTestNovaLoz456';
      await page.locator('#passwordForm [name=current_password]').fill(password);
      await page.locator('#passwordForm [name=new_password]').fill(newPassword);
      await page.locator('#passwordForm button').click();
      await login(page, { username, password: newPassword });
    }

    // Set up MFA.
    await page.locator('#mfaBtn').click();
    await expect(page.locator('#mfaDialog')).toBeVisible();
    await page.locator('#mfaForm button#mfaPrimaryBtn').click();
    await expect(page.locator('#mfaSecretBox')).toBeVisible();
    const secret = await page.locator('#mfaSecret').inputValue();
    expect(secret.length).toBeGreaterThan(10);
    const code = authenticator.generate(secret);
    await page.locator('#mfaForm [name=code]').fill(code);
    await page.locator('#mfaForm button#mfaPrimaryBtn').click();
    await expect(page.locator('#toast')).toContainText('uključena');

    // Log out and log back in -- this time MFA must be required.
    await page.locator('#logoutBtn').click();
    await page.goto('/');
    await page.locator('#loginForm [name=organization]').fill('demo-clinic');
    await page.locator('#loginForm [name=username]').fill(username);
    await page.locator('#loginForm [name=password]').fill('MfaTestNovaLoz456');
    await page.locator('#loginForm button').click();
    await expect(page.locator('#mfaLoginField')).toBeVisible({ timeout: 5_000 });
    const loginCode = authenticator.generate(secret);
    await page.locator('#loginForm [name=mfa_code]').fill(loginCode);
    await page.locator('#loginForm button').click();
    await expect(page.locator('#dashboardView')).toBeVisible({ timeout: 10_000 });

    // Disable MFA again so this account (and this test) is self-cleaning.
    await page.locator('#mfaBtn').click();
    await page.locator('#mfaForm [name=code]').fill(authenticator.generate(secret));
    await page.locator('#mfaDisableBtn').click();
    await expect(page.locator('#toast')).toContainText('isključena');
  });
});
