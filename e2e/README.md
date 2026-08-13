# E2E testovi (Playwright)

Ovi testovi pokreću pravi browser (Chromium) protiv prave aplikacije
(uvicorn + demo baza u privremenom direktorijumu) i pokrivaju tokove koje
API test svita (backend/tests, pytest) ne može: prijavu, MFA, upload
dokumenta, arhiviranje, otvaranje originala, i osnovnu navigaciju.

## Pokretanje lokalno

```bash
npm install
npx playwright install --with-deps chromium
npm run test:e2e
```

## Pokretanje protiv već pokrenutog servera

```bash
E2E_BASE_URL=http://127.0.0.1:8899 npm run test:e2e
```

## Napomena o poreklu ovih testova

Ovi testovi su napisani i pregledani unakrsnom proverom sa stvarnim
HTML/JS aplikacije (tačni id-jevi elemenata, imena polja formi, struktura
dijaloga) — ali NISU mogli biti pokrenuti u okruženju u kom su napisani,
jer taj sandbox nema izlazni pristup ka `cdn.playwright.dev` (odakle
Playwright preuzima Chromium binarni fajl). Očekuje se da rade čisto u
CI-ju (vidi `.github/workflows/tests.yml`, `e2e` job) ili na razvojnoj
mašini sa uobičajenim internet pristupom, ali prvo stvarno pokretanje
treba pažljivo pratiti, ne pretpostaviti da su ispravni samo na osnovu
pregleda koda.

## Deljeno stanje

Testovi u ovom paketu dele istu demo ordinaciju (`demo-clinic`) i rade
sekvencijalno (`fullyParallel: false`, `workers: 1` u `playwright.config.js`)
namerno — svaki test kreira svoje pacijente sa nasumičnim imenima da
izbegne koliziju, ali deljena stanja poput broja korisnika ili MFA statusa
demo naloga bi mogla da izazovu nestabilnost ako bi se testovi paralelno
izvršavali.
