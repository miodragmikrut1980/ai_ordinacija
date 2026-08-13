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
    const newPassword = 'MfaTestNovaLoz456';

    // Surfaces the real reason for any future failure here (e.g. a 403
    // from the must_change_password gate in deps.py) directly in the test
    // output instead of only the downstream symptom ("box never appeared"),
    // which took two extra round-trips to diagnose the first time.
    page.on('response', (res) => {
      if (res.url().includes('/api/auth/mfa/') && !res.ok()) {
        console.log(`[diagnostic] ${res.request().method()} ${res.url()} -> ${res.status()}`);
      }
    });

    await login(page, { username: 'admin', password: 'admin123' });
    await page.locator('button.nav-item[data-view="users"]').click();
    await expect(page.locator('#usersView')).toBeVisible();
    await page.locator('#newUserBtn').click();
    await page.locator('#userForm [name=username]').fill(username);
    await page.locator('#userForm [name=full_name]').fill('MFA E2E Test');
    await page.locator('#userForm [name=role]').selectOption('doctor');
    await page.locator('#userForm [name=password]').fill(password);
    await page.locator('#userForm button:not(.close)').click();
    await expect(page.locator('#toast')).toBeVisible();
    await page.locator('#logoutBtn').click();
    await expect(page.locator('#loginOverlay')).toBeVisible();

    // A freshly admin-created account always has must_change_password set
    // server-side (see UserCreate/create_user), and the login form now
    // shows the password dialog immediately in that case rather than
    // proceeding to the dashboard (see app.js's loginForm submit handler)
    // -- so this step deliberately does NOT use the shared login() helper,
    // whose success signal (#loginOverlay hidden) does not apply here: the
    // overlay stays up behind the password dialog until the password is
    // actually changed.
    await page.locator('#loginForm [name=organization]').fill('demo-clinic');
    await page.locator('#loginForm [name=username]').fill(username);
    await page.locator('#loginForm [name=password]').fill(password);
    await page.locator('#loginForm button').click();
    await expect(page.locator('#passwordDialog')).toBeVisible({ timeout: 10_000 });
    await page.locator('#passwordForm [name=current_password]').fill(password);
    await page.locator('#passwordForm [name=new_password]').fill(newPassword);
    await page.locator('#passwordForm button:not(.close)').click();
    // Changing password revokes all sessions server-side and the app logs
    // the user out client-side too (see passwordForm's submit handler) --
    // a fresh login with the new password is required.
    await expect(page.locator('#loginOverlay')).toBeVisible({ timeout: 10_000 });
    await login(page, { username, password: newPassword });

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
    await page.locator('#loginForm [name=password]').fill(newPassword);
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
