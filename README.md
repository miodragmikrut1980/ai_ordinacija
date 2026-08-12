# AI asistent za ordinacije

Verzija: sadržaj fajla [`VERSION`](VERSION) je jedini izvor istine (koristi ga i `pyproject.toml`, i sama aplikacija u `/api/health`). Istorija promena: [`CHANGELOG.md`](CHANGELOG.md). Svaka izmena koja se isporučuje treba da podigne `VERSION` i doda unos u `CHANGELOG.md`, prateći [Semantic Versioning](https://semver.org/).

Lokalni sistem za privatne ordinacije, sa srpskim interfejsom na latinici, višekorisničkim pristupom, kartonima pacijenata, AI pripremom pregleda, medicinskim piscem i epidemiološkim radarom.

**Pre upotrebe sa pravim pacijentima**, pogledajte [`COMPLIANCE_NOTES.md`](COMPLIANCE_NOTES.md) -- tehnički checklist (ne pravni savet) za pravni/regulatorni pregled.

## Novo: bezbednost i pouzdanost podataka

- **SQLite baza sa transakcijama** umesto JSON fajla koji se u celosti prepisivao pri svakom upisu; `UNIQUE` ograničenje na nivou baze sprečava dupliranje korisničkog imena čak i pri paralelnim zahtevima.
- **Enkripcija u mirovanju**: svi klinički podaci (kartoni, dokumenti, nalazi, transkripti, izveštaji) i sadržaj upload-ovanih fajlova enkriptovani su simetričnim ključem (Fernet) pre upisa na disk. Ključ se čita iz `CLINIC_ENCRYPTION_KEY` ili se automatski generiše u `data/secret.key`.
- **Rotacija ključa enkripcije**: `scripts/rotate_key.py` re-enkriptuje celu bazu i sve upload-ovane fajlove novim ključem bez gubitka podataka -- pokrenuti dok je server zaustavljen.
- **Integracija sa pravim KMS/secrets managerom**: `CLINIC_ENCRYPTION_KEY_COMMAND` pokreće spoljnu komandu (npr. `vault kv get ...`, `aws secretsmanager get-secret-value ...`) čiji izlaz je ključ, umesto da ključ stoji direktno u environment varijabli. Neuspeh komande je odmah greška pri pokretanju, nikad tih fallback. Isto važi i za `scripts/rotate_key.py` (`CLINIC_ENCRYPTION_KEY_OLD_COMMAND`).
- **HTTPS**: `CLINIC_TLS=1 ./start.sh` pokreće server na `https://127.0.0.1:8443`. Ako su postavljeni `CLINIC_TLS_CERT_FILE`/`CLINIC_TLS_KEY_FILE`, koristi se pravi sertifikat (npr. Let's Encrypt); u suprotnom se automatski generiše self-signed (`data/tls/`) za lokalnu probu. Za javno dostupan deployment i dalje je najbolje terminirati TLS na reverse proxy-ju ispred aplikacije.
- **Perzistentne sesije**: sesije se čuvaju u bazi, tako da restart servera više ne izloguje sve korisnike.
- **Zaštita od brute-force napada na prijavu**: nalog se privremeno zaključava na 15 minuta nakon 5 uzastopnih neuspešnih pokušaja (HTTP 429).
- **Opšti rate-limiting**: podrazumevano 240 zahteva/minut po IP adresi na celom API-ju (podesivo preko `CLINIC_RATE_LIMIT_PER_MINUTE`). Podrazumevano se koristi stvarna TCP adresa klijenta; iza reverse proxy-ja postavite `CLINIC_TRUST_PROXY_HEADERS=1` da se koristi `X-Forwarded-For` -- **samo** ako je aplikacija zaista dostupna isključivo preko tog proxy-ja (u suprotnom klijent može sam da postavi taj header i dobije svež limit na svaki zahtev).
- **Politika jačine lozinke**: nova lozinka mora sadržati bar jedno slovo i jednu cifru, ne sme biti na listi uobičajenih lozinki i ne sme biti identična korisničkom imenu.
- **Višefaktorska prijava (TOTP)**: svaki korisnik može uključiti šestocifreni kod iz standardne autentikator aplikacije. Tajna se generiše lokalno, enkriptovana je u bazi i potvrđuje se pre uključivanja; prijava zatim koristi kratkotrajni, jednokratni izazov sa ograničenim brojem pokušaja. Ovo značajno smanjuje rizik ako lozinka procuri, ali ne zamenjuje pravilnu kontrolu fizičkog pristupa računaru.
- **Tamper-evident evidencija aktivnosti**: svaki unos u audit log povezan je heš-lancem sa prethodnim; `GET /api/audit/verify` (samo administrator) proverava da lanac nije izmenjen.
- **Sigurnosni HTTP header-i**: `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Content-Security-Policy`, i `HSTS` kada se pristupa preko HTTPS-a.
- Automatska, jednokratna migracija postojećih podataka iz starog `clinic-store.json` formata u novu enkriptovanu bazu pri prvom pokretanju.

**Napomena o ograničenjima**: ključ enkripcije se podrazumevano čuva pored same baze (`data/secret.key`), što je praktičan kompromis za lokalni/single-tenant demo, ali nije zamena za pravi KMS/secrets manager -- za produkciju sa stvarnim pacijentima, prosledite `CLINIC_ENCRYPTION_KEY` iz spoljnog secrets managera i ne dozvolite da se `secret.key` uopšte kreira. Self-signed sertifikat rešava enkripciju transporta lokalno, ali browseri će prijaviti da mu ne veruju -- za javno dostupan deployment i dalje je potreban reverse proxy sa sertifikatom prave CA.

## Novo u ovoj verziji

### Bezbedan početak rada i operativna spremnost (1.12.0)

- **Bezbedan podrazumevani start**: demo se pokreće samo sa eksplicitnim `CLINIC_ENV=demo`. Svaki drugi režim zahteva `CLINIC_ENCRYPTION_KEY` (ili `CLINIC_ENCRYPTION_KEY_COMMAND`) i bootstrap administratora, pa slučajni `./start.sh` ne pravi poznate demo lozinke uz podatke pacijenata.
- **Obavezna promena privremene lozinke**: administrator koji doda korisnika i bootstrap administrator dobijaju privremenu lozinku. API blokira svaki pristup kartonima, terminima i izvozima dok korisnik ne promeni lozinku; browser ovo samo jasno prikazuje.
- **Oporavak izgubljenog MFA uređaja**: drugi administrator kroz `POST /api/users/{user_id}/mfa-reset` može resetovati faktor uz opis razloga. Sesije i MFA izazovi tog korisnika se odmah poništavaju, a događaj ostaje u revizionom dnevniku. Pre toga ordinacija mora nezavisno proveriti identitet korisnika.
- **Kontrola isporuke**: `requirements.lock` je pregledani snapshot zavisnosti, `SBOM.spdx.json` je mašinski čitljiv popis ključnih komponenti, a `SECURITY.md` opisuje bezbedno prijavljivanje propusta bez slanja podataka pacijenta.

### OCR, laboratorija i sigurnost terapije (1.10.0)

- **Skenirani nalazi**: upload prihvata PDF, DOCX, TXT/MD, PNG, JPG/JPEG, TIFF i BMP. Tekst sa slike i skeniranog PDF-a obrađuje se lokalnim Tesseract OCR-om; nikakav dokument se ne šalje cloud OCR servisu. Skenirani PDF je ograničen na prvih 5 strana i OCR ima vremensko ograničenje. Za lokalni macOS/Linux start instalirajte Tesseract i Poppler pre rada sa skenovima (Docker image ih već sadrži): `brew install tesseract poppler` ili odgovarajući paket menadžer operativnog sistema.
- **Laboratorijski rezultati**: aplikacija prepoznaje ograničen skup jasnih laboratorijskih redova iz dokumenta i prikazuje ih kao *nacrte*. Nacrt nije medicinski zapis sve dok ga lekar ne potvrdi prema originalnom nalazu. U tabeli se čuva izvorni red, jedinica, opseg kada je prepoznat i status potvrde.
- **Provera terapije**: dugme „Proveri terapiju” prikazuje potencijalne alergijske/interakcione rizike iz malog, verzionisanog skupa pravila. Ovo nije kompletna baza lekova i odsustvo upozorenja ne znači da je terapija bezbedna. Svaku odluku lekar proverava prema pacijentu, dozama, laboratoriji i važećem SmPC/ALIMS izvoru.
- **Produkcioni onboarding**: za prvi start sa `CLINIC_ENV=production` obavezno postavite `CLINIC_BOOTSTRAP_ADMIN_USERNAME` i `CLINIC_BOOTSTRAP_ADMIN_PASSWORD` (najmanje 12 karaktera, slova i cifre). Demo nalozi se u tom režimu ne kreiraju.

### Višefaktorska prijava (1.11.0)

- U gornjem desnom uglu kliknite na dugme za višefaktorsku prijavu, unesite lokalno prikazan tajni ključ u autentikator aplikaciju i potvrdite aktuelni šestocifreni kod. Tajni ključ je namenjen samo za ručni unos; ne čuvati ga u porukama, beleškama niti delu kartona pacijenta.
- Pri sledećoj prijavi aplikacija prvo proverava lozinku, pa traži kod. Kod ne stvara novu sesiju sam po sebi i izazov ističe za pet minuta.

- AI epidemiološki radar za zbirne podatke jedne ordinacije
- trendovi respiratornih, gastrointestinalnih, febrilnih i kožnih simptoma
- poređenje sa prethodnim periodom od 7, 14 ili 30 dana
- prikaz laboratorijski potvrđenih patogena
- upozorenja na moguće klastere
- minimalni prag uzorka i jasno upozorenje kada nema dovoljno podataka
- izolacija svih rezultata po ordinaciji
- pristup radaru samo lekaru i administratoru
- kompletan interfejs na srpskom jeziku, latinicom

## Novo: AI diferencijalna analiza

- rangira stanja po stepenu podudaranja, ne po izmišljenoj verovatnoći
- prikazuje dokaze koji podržavaju rezultat
- navodi podatke koje lekar još treba da proveri
- posebno označava stanja koja ne treba propustiti
- koristi epidemiološki radar samo kao dodatni kontekst
- dostupna je isključivo lekaru i administratoru

## Važno

Radar predstavlja interni signal ordinacije, a ne potvrdu epidemije. Simptomi ne potvrđuju uzročnika. Naziv virusa ili bakterije prikazuje se samo kada tekst dokumentacije sadrži evidentiran pozitivan ili potvrđen nalaz. Rezultate mora pregledati lekar.

## Pokretanje

```bash
unzip clinic-ai-assistant-1.13.0.zip
cd clinic-ai-assistant
CLINIC_ENV=demo ./start.sh
```

Otvorite `http://127.0.0.1:8080`. Za lokalni HTTPS sa automatski generisanim self-signed sertifikatom: `CLINIC_TLS=1 ./start.sh` (`https://127.0.0.1:8443`).

Ako je port zauzet (npr. `ERROR: [Errno 48] Address already in use`), `start.sh` to sada prijavljuje jasno pre nego što uopšte pokuša pokretanje, i predlaže rešenje. Za pokretanje na drugom portu: `CLINIC_PORT=8081 ./start.sh`.

Pri prvom pokretanju postojeći `data/clinic-store.json` se automatski migrira u enkriptovanu SQLite bazu (`data/clinic.db`) i zatim briše.

Demo nalozi postoje **samo** uz eksplicitni `CLINIC_ENV=demo` i samo za lokalnu, potrošnu probu:

- lekar: `doctor` / `doctor123`
- recepcija: `reception` / `reception123`
- administrator: `admin` / `admin123`

## Testovi

```bash
PYTHONPATH=backend pytest -q
```

## Backup i oporavak

Backup se kreira bez ključa za enkripciju, koji mora ostati odvojen i čuvan
u odobrenom secrets manageru. Uz svaku `.tar.gz` arhivu nastaje odgovarajući
`.manifest.json`. Manifest je sada obavezan pri restore-u: bez njega, sa
pogrešnim SHA-256 ili neočekivanim sadržajem restore se prekida **pre** bilo
kakve izmene postojećih podataka. Restore prvo koristi privremenu lokaciju,
proverava SQLite integritet i tek zatim zamenjuje bazu i upload-e; pri grešci
prethodni podaci se vraćaju.

```bash
python scripts/backup.py data --out /bezbedna/lokacija/backups
python scripts/restore.py /bezbedna/lokacija/backups/clinic-backup-....tar.gz data --force
```

Dokumenti se ne brišu iz kartona. Lekar ili administrator može da ih
arhivira uz obavezan razlog; original ostaje dostupan ovlašćenom lekaru,
razlog i akcija ostaju u revizionom dnevniku, a arhivirani dokumenti se ne
koriste u aktivnim AI sažecima i analizama.

## Docker

```bash
CLINIC_ENV=production \
CLINIC_ENCRYPTION_KEY='ključ-iz-vašeg-secrets-managera' \
CLINIC_BOOTSTRAP_ADMIN_USERNAME='admin' \
CLINIC_BOOTSTRAP_ADMIN_PASSWORD='jedinstvena-lozinka-sa-najmanje-12-karaktera' \
docker compose up --build
```

Podaci se čuvaju u named Docker volume-u (`clinic_data`), ne u plaintext folderu na host mašini. Docker Compose sada zahteva eksplicitno `CLINIC_ENV`; za realne podatke koristite samo `production`. Kontejner ima read-only root filesystem, nema Linux capability-je i ne može da podigne nova prava; `clinic_data` ostaje jedina radna lokacija za podatke.

**Napomena**: `Dockerfile` je pažljivo napisan da odgovara već potvrđenoj `pip install -e .` / `uvicorn --app-dir backend` konfiguraciji, ali sam build nije mogao da se izvrši i testira u ovom okruženju (nema pristupa Docker daemon-u ovde) -- ovo je jedini deo isporuke koji nije end-to-end proveren pokretanjem.

## Deployment bez Docker-a (systemd)

`deploy/clinic-ai-assistant.service` je template za pokretanje kao pravi Linux servis (bez Docker-a), sa `deploy/env.production.example` za `CLINIC_ENCRYPTION_KEY`. Sadrži osnovne systemd hardening opcije (`ProtectSystem=strict`, `NoNewPrivileges=true`, itd.) -- ovo nije zamena za firewall/reverse-proxy podešavanje, samo razuman minimum.

## Logovanje

Podrazumevano, čitljiv tekst na stdout. Za strukturisane JSON logove (za slanje ka Loki/CloudWatch/ELK i sl.): `CLINIC_LOG_FORMAT=json`. Svaki zahtev dobija kratak `request_id`, vraćen i kroz `X-Request-ID` response header, tako da se konkretan API poziv iz izveštaja o grešci može naći u logovima.

## CI

`.github/workflows/tests.yml` pokreće pytest na Python 3.11 i 3.12 pri svakom push/PR-u, plus build i healthcheck Docker image-a.
