// @ts-check
const { expect } = require('@playwright/test');

/**
 * Logs in as the seeded demo doctor (or another role) via the real login
 * form -- not by hitting the API directly -- so this exercises the exact
 * flow a lekar experiences, including the HttpOnly session cookie set by
 * the server (see backend/app/routers/auth.py).
 */
async function login(page, { username = 'doctor', password = 'doctor123', organization = 'demo-clinic' } = {}) {
  await page.goto('/');
  await page.locator('#loginForm [name=organization]').fill(organization);
  await page.locator('#loginForm [name=username]').fill(username);
  await page.locator('#loginForm [name=password]').fill(password);
  await page.locator('#loginForm button').click();
  await expect(page.locator('#loginOverlay')).toBeHidden({ timeout: 10_000 });
}

/** A short, human-readable-enough random suffix so parallel/repeat test
 * runs against the same demo database never collide on patient names. */
function uniqueName(prefix) {
  return `${prefix} ${Date.now()}-${Math.floor(Math.random() * 10_000)}`;
}

module.exports = { login, uniqueName };
