# Changelog

## 1.19.0

Poslednja dva stavke sa originalne liste prioriteta: verzionisana baza
lekova (bezbednosno prošireno, ne fabrikovano) i prvi specijalistički
modul (Pedijatrija, namerno bez WHO percentila i vakcinalnog kalendara
koje sistem ne može pouzdano da garantuje).

### Added
- **Upozorenja za bubrežnu/jetrenu funkciju** u proveri bezbednosti
  terapije: sistem prepoznaje pominjanje bubrežne/jetrene insuficijencije
  u dijagnozama ili anamnezi pacijenta (pretraga korena reči, otporna na
  srpske padeže) i unakrsno proverava sa poznatim lekovima iz terapije
  (npr. metformin + bubrežna insuficijencija → kritično upozorenje o
  laktatnoj acidozi).
- **`rule_id` i `source_note`** na svakom nalazu provere terapije —
  svako upozorenje sada vodi do konkretnog, verzionisanog pravila i
  jasne napomene da je reč o opšte poznatoj farmakološkoj činjenici,
  ne o licenciranoj spoljnoj bazi. Novi endpoint
  `/api/medication-safety/rules` — pun katalog svih pravila za reviziju.
- **Pedijatrija — prvi specijalistički modul**: podaci o staratelju
  (ime, srodstvo, telefon), evidentiranje merenja rasta kroz vreme
  (visina/težina/obim glave) sa trend grafikonom, i dnevnik datih
  vakcina (naziv, datum, serija, ko je dao). Namerno bez izračunavanja
  WHO percentila rasta i bez zvaničnog rasporeda vakcinacije — oba
  zahtevaju autoritativne, ažurne izvore koje ovaj sistem ne može
  pouzdano da proveri; pogrešan medicinski kalendar u alatu je opasniji
  nego da ga uopšte nema.
- Nov tab „Pedijatrija" u kartonu pacijenta.

### Fixed
- Pretraga dijagnoza/anamneze za bubrežnu/jetrenu funkciju je prvobitno
  koristila fraze u nominativu ("ciroza", "bubrežna insuficijencija"),
  pa padežni oblici ("cirozu", "bubrežnu insuficijenciju") nisu
  prepoznavani — opasan tihi propust za bezbednosnu proveru. Ispravljeno
  pretragom korena reči, uz regresioni test.

### Novi testovi
- 7 testova za verzionisanje/organsku funkciju u proveri terapije
  (`test_laboratory_and_safety.py`), uključujući regresioni test za
  padežni bug.
- 8 testova za pedijatrijski modul (`test_pediatrics.py`), uključujući
  eksplicitnu proveru da odgovor nikad ne pominje „percentil".

142/142 testova prolazi.

## 1.18.0

Pacijentski portal — najveći dodatak do sada. Potpuno odvojena
autentifikacija od osoblja (poseban token prostor, poseban brojač
zaključavanja), sa jasno ograničenim obimom podataka koje pacijent vidi.

### Added
- **Nalog pacijenta i prijava**: recepcija/admin kreira portal nalog
  (username + privremena lozinka, isti obrazac kao kreiranje naloga
  osoblja), pacijent je menja pri prvoj prijavi. Novi endpointi pod
  `/api/portal/auth/*` i `POST /api/patients/{id}/portal-account`.
  Bezbednosna granica je stroga i testirana: token osoblja se ne
  prihvata ni na jednom `/api/portal/*` endpointu, i obrnuto — dva
  potpuno odvojena skladišta sesija (`portal_sessions` naspram
  `sessions`) i odvojen brojač neuspešnih prijava.
- **Online zakazivanje**: pacijent bira lekara i datum, sistem računa
  slobodne termine u radnom vremenu ordinacije (`CLINIC_WORKING_HOURS_*`)
  koristeći isti mehanizam sprečavanja sudara koji već štiti kalendar
  osoblja. Otkazivanje sopstvenog termina je takođe podržano; pokušaj
  otkazivanja tuđeg termina vraća 404 (ne 403), da se ne otkrije
  postojanje tuđeg zakazanog termina.
- **Pristanak za obradu podataka**: verzionisan tekst pristanka,
  vremenski žigosan prilikom prihvatanja (`/api/portal/consent`).
- **Bezbedne poruke**: dvosmerna nit između pacijenta i ordinacije,
  vidljiva sa obe strane (`/api/portal/messages` i novo
  `/api/patients/{id}/messages` za osoblje).
- **Digitalni upitnik pre pregleda**: glavna tegoba, opis simptoma,
  potvrda tačnosti alergija/terapije — vidljivo lekaru u kartonu
  (`/api/patients/{id}/questionnaire-responses`, samo doctor/admin).
- **Nalazi**: pacijent vidi isključivo laboratorijske rezultate sa
  statusom „potvrđeno" — nikad nacrt (neproveren OCR/AI nalaz) niti
  odbačen rezultat.
- Nova frontend aplikacija na `/portal` (`portal.html`/`.js`/`.css`),
  vizuelno usklađena sa glavnom aplikacijom ali sa sopstvenim,
  odvojenim čuvanjem tokena u pregledaču (različit localStorage ključ
  od aplikacije za osoblje, da se izbegne mešanje kad su oba otvorena u
  istom pregledaču).
- `backend/tests/test_portal.py`: 12 novih testova, uključujući
  eksplicitan test da token osoblja i portal token nisu međusobno
  zamenljivi ni u jednom smeru.

## 1.17.0

Document-viewer sa citatima + laboratorijski standardi. Namerno mala,
tačna verzija LOINC/MKB-10 podrške (seed skup za analize/dijagnoze koje
sistem već prepoznaje), ne pokušaj pune terminološke baze — isti princip
pouzdanosti kao kod SMS/Viber podsetnika i fiskalizacije u ranijim
izdanjima.

### Added
- **Citat do tačne stranice originala**: PDF ekstrakcija sada prati sa
  koje je stranice svaki prepoznati laboratorijski nalaz izvučen
  (`page_offsets` u `extractors.py`). U tabu Laboratorija, svaki nalaz sa
  poznatom stranicom dobija dugme „📄 Str. N u originalu" koje otvara PDF
  direktno na toj strani (`#page=N`, radi nativno u browseru, bez
  dodatnih biblioteka). Izvorni red iz dokumenta je već prikazan kao
  tekst pored dugmeta.
- **LOINC seed kodovi** (`standards.py`) za svih 8 analiza koje sistem
  prepoznaje (CRP, Glukoza, Hemoglobin, Leukociti, Trombociti,
  Kreatinin, TSH, HbA1c), sa opštim referentnim opsezima iz literature i
  jasnom napomenom da laboratorija ordinacije može imati drugačiji
  opseg. Novi endpoint `/api/lab-standards`.
- **MKB-10/ICD-10 seed kodovi** za svih 6 dijagnoza koje AI
  diferencijalna analiza prepoznaje (npr. Infekcija urinarnog trakta →
  N39.0). Prikazuje se uz svaki AI predlog u kartonu. Novi endpoint
  `/api/icd10-codes`.
- **Trend grafikon laboratorijskih nalaza**: dugme „📈 Trend" pored
  analiza sa više od jednog merenja, sa ručno iscrtanim SVG grafikonom
  (bez eksternih biblioteka, isti pristup kao epidemiološki radar).
  Novi endpoint `/api/patients/{id}/lab-results/trend`.
- Novo polje `source_page` na laboratorijskim rezultatima.
- Novi test fajl `test_standards_and_citations.py` (6 testova,
  uključujući end-to-end test sa pravim dvostranim PDF-om) i 2 nova
  jedinična testa za mapiranje reda u stranicu.

## 1.16.0

UX runda iz ugla svakodnevnog rada ordinacije, po povratnim informacijama
sa terena: predug karton, previše AI dugmadi na jednom mestu, nejasne
ikonice, raspored kao gola lista i nepostojeći pregled za recepciju.

### Added
- **Karton pacijenta podeljen na tabove**: Pregled, Dokumenti,
  Laboratorija, Terapija, AI alati, Istorija — umesto jedne duge
  stranice gde se sažetak, diferencijal, pisar, laboratorija, dokumenti
  i istorija bore za pažnju. Tab „Laboratorija" dobija tačku upozorenja
  kad postoji nacrt nalaza koji čeka potvrdu.
- **Nov tab „Terapija"**: trenutna terapija, alergije i dijagnoze na
  jednom mestu uz dugme za proveru interakcija — izdvojeno iz taba
  Pregled da se smanji broj dugmadi vidljivih odjednom.
- **Tab „AI alati"** okuplja diferencijalnu analizu, pisara, pripremu
  pregleda i „Pitajte karton" na jednom mestu, sa kratkim uputstvom o
  predloženom redosledu koraka — umesto da se svih pet AI akcija
  takmiče za pažnju na glavnom pregledu kartona.
- **Statusna traka inbox-a dokumenata**: „Spremno", „Potrebna
  provera", „OCR neuspešan", „Čeka potvrdu laboratorije" (novo —
  računa se prema tome da li dokument ima nacrt laboratorijskog nalaza
  koji još nije potvrđen ili odbačen).
- **Komandni centar za recepciju** na kontrolnoj tabli (vidljiv
  recepciji i adminu): koliko je pacijenata stiglo, koliko kasni,
  koliko termina je preostalo danas, koliko dokumenata čeka proveru, i
  ko je sledeći pacijent na redu.

### Changed
- Nejasne ikonice u gornjoj traci zamenjene prepoznatljivim simbolima
  sa tooltipovima: 🔑 za promenu lozinke, 🛡 za MFA, 🔍 za brzu
  pretragu pacijenta, 🌓 za temu. Dugme za odjavu sada ima vidljiv
  tekst „Odjava" umesto simbola bez ijednog opisa. Ispravljen je i
  pravi bug: dugme za MFA i dugme za brzu pretragu su ranije delila
  identičnu ⌘ ikonicu.

## 1.15.0

- Dodat je finansijsko-administrativni modul: cenovnik usluga (samo admin
  kreira/menja cene), izdavanje računa sa više stavki i popustom po stavci
  i na ceo račun, evidentiranje uplata (gotovina/kartica/prenos) sa
  delimičnim plaćanjem, dnevni promet po načinu plaćanja, i pregled
  dugovanja sortiran po broju dana neplaćeno.
- Brojevi računa su sekvencijalni i bez rupa po ordinaciji i godini
  (`GGGG-000001`), generisani u istoj zaključanoj transakciji kao i sam
  račun — to je preduslov za buduću integraciju sa fiskalizacijom, koju
  ovaj modul namerno ne pokušava da simulira (izdavanje pravog fiskalnog
  računa zahteva sertifikovan ESIR ili licenciranu API integraciju, isti
  princip pouzdanosti kao kod SMS/Viber podsetnika: ne lažira se ono što
  nije zaista urađeno).
- Novčani iznosi se čuvaju kao celi dinari (int), ne float, da se izbegnu
  greške zaokruživanja u ukupnim iznosima.
- RBAC: cenovnik menja samo admin; račun mogu izdati lekar, recepcija i
  admin (uobičajeno u maloj ordinaciji); uplate evidentiraju recepcija i
  admin; otkazivanje računa (sa obaveznim razlogom) samo admin; dnevni
  promet i dugovanja vidljivi samo recepciji i adminu.
- Novi frontend prikaz „Finansije": kasa ordinacije, dugovanja, poslednji
  računi i cenovnik, plus dugme „Izdaj račun" direktno iz kartona
  pacijenta.
- Novi test fajl `test_finance.py` (11 testova): obračun popusta,
  sekvencijalna numeracija, delimično plaćanje, prekoračenje uplate,
  otkazivanje, dnevni promet, dugovanja, RBAC, tenant izolacija.

## 1.14.0

- Dodat je pravi kalendar ordinacije: nedeljni i mesečni prikaz termina
  (raspored po satima za nedelju, pregled po danima za mesec), pored
  postojeće liste. Termin sada ima dodeljenog lekara, sobu, tip usluge i
  trajanje.
- Sprečeno je dupliranje termina: novi ili pomereni termin se odbija sa
  409 statusom ako se preklapa sa postojećim aktivnim terminom istog
  lekara ili iste sobe (soba je fizičko ograničenje bez obzira na to koji
  je lekar slobodan).
- Dodato je pomeranje termina (`PATCH /api/appointments/{id}`) sa istom
  proverom sudara, i status „nije se pojavio" odvojen od „otkazano",
  oba sa opcionim razlogom koji ostaje u revizionom dnevniku.
- Dodata je lista čekanja: pacijent se dodaje kada nema slobodnog
  termina, a promocija u pravi termin prolazi kroz istu proveru sudara
  kao i direktno zakazivanje.
- Dodati su podsetnici za termine: novi termin automatski zakazuje
  podsetnik 24h unapred. E-mail kanal stvarno šalje poštu preko SMTP
  naloga ordinacije (`CLINIC_SMTP_*`); SMS i Viber su namerno označeni
  kao „nije podešeno" dok se ne poveže pravi gateway, umesto lažnog
  prikazivanja da je poruka poslata. `scripts/send_reminders.py` je nova
  periodična skripta (cron/systemd timer, vidi
  `deploy/clinic-ai-assistant-reminders.timer`) koja šalje dospele
  podsetnike; prati isti obrazac direktnog pristupa bazi kao
  `scripts/backup.py`.
- Novi endpoint `/api/clinicians` za listu lekara u formama zakazivanja,
  bez izlaganja pune liste korisnika (koja je i dalje samo za admina).
- Novi test fajl `test_scheduling.py` (18 testova): sudari po lekaru i
  po sobi, oslobađanje termina pri otkazivanju, pomeranje, lista
  čekanja, i podsetnici uključujući skriptu za slanje.

## 1.13.0

- Restore sada zahteva odgovarajući backup manifest, proverava SHA-256 i
  kompletan inventar arhive, testira SQLite integritet u privremenoj lokaciji
  i atomically zamenjuje podatke tek po uspešnoj proveri. Backup bez manifesta
  se odbija bez izmene postojeće ordinacijske baze.
- Dodati su pregled izvornog dokumenta uz autorizaciju i audit, kao i
  arhiviranje dokumenata uz obavezan razlog umesto nepovratnog brisanja.
  Arhivirani dokument ostaje sačuvan i ulazi u kompletan izvoz kartona, ali
  nije ulaz aktivnim AI analizama.
- Dodat je automatizovan sintetički scenario jednog dana ordinacije:
  termin, pregled, upload nalaza, pregled originala, arhiviranje, audit i
  provera ograničenja za recepciju.

## 1.12.0

- Safe-by-default startup: known demo accounts exist only with explicit `CLINIC_ENV=demo`. Any other mode requires a production bootstrap administrator and an externally supplied encryption key.
- Removed all pre-filled credentials from the login screen; newly created and bootstrap accounts are blocked from clinic data until their temporary password is changed, enforced by the API rather than the browser.
- Added audited, two-person MFA lost-device recovery: a different administrator can reset a user's factor only with a documented reason; all of the target user's sessions and pending MFA challenges are revoked.
- Hardened the Docker deployment with a mandatory environment choice, read-only root filesystem, dropped Linux capabilities, `no-new-privileges`, a limited non-executable `/tmp`, and the correct 1.12.0 image/version metadata.
- Added a reviewed dependency lock snapshot, SPDX SBOM, security-reporting guidance, and regression coverage for forced password change and MFA recovery.

## 1.11.0

- Added locally generated TOTP multi-factor authentication: enrollment is confirmed with a real code, the TOTP secret is encrypted at rest, and sign-in uses a short-lived, single-use challenge with a five-attempt limit.
- Added a Serbian UI flow for authenticator-app enrollment, replacement, disabling, and MFA login; removed pre-filled demo credentials from the login screen.
- Extended encryption-key rotation to include active and pending MFA secrets, so a planned key rotation does not lock users out.
- Added MFA API regression coverage and bumped the SQLite schema migration to version 4.

This project follows [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`.

- **MAJOR** -- breaking changes: API/response shape changes, config env vars
  renamed or removed, anything that would break an existing deployment or
  integration without changes on their end.
- **MINOR** -- new functionality that's backward compatible (new endpoints,
  new optional config, new scripts).
- **PATCH** -- bug fixes and internal improvements with no behavior change
  from the outside.

**The version lives in exactly one place: the `VERSION` file at the repo
root.** `pyproject.toml` reads it dynamically (`[tool.setuptools.dynamic]`),
and the app reads it at startup (`backend/app/state.py`) for the FastAPI
title and the `/api/health` response -- so there is nothing else to keep in
sync by hand. **Every change that ships bumps `VERSION` and adds an entry
here**, in the same commit/PR as the change itself.

## [Unreleased]

Nothing yet.

## [1.10.0] - 2026-08-11

### Added
- **Lokalni OCR za skeniranu dokumentaciju**: PNG/JPG/TIFF/BMP i PDF bez tekstualnog sloja sada se čitaju lokalnim Tesseract OCR-om; sistem ne šalje dokumente pacijenta spolja. PDF OCR je ograničen na prvih pet stranica i svaki OCR proces ima vremensko ograničenje, kako loš ili namerno problematičan upload ne bi blokirao ordinaciju.
- **Strukturisana laboratorija sa obaveznom potvrdom lekara**: konzervativno se prepoznaju česti testovi (CRP, glukoza, hemoglobin, leukociti, trombociti, kreatinin, TSH, HbA1c) i referentni opsezi. Rezultati iz dokumenta nastaju samo kao nacrti; lekar ih eksplicitno potvrđuje ili odbacuje. Ručni unos se odmah vodi kao potvrđen i svaka akcija ostaje u auditu.
- **Provera potencijalnih rizika terapije i alergija**: lekar može proveriti postojeću i predloženu terapiju kroz transparentan, verzionisan mali skup pravila. Rezultat nikada ne tvrdi da je terapija bezbedna, izričito prikazuje neprepoznate lekove i zahteva proveru u važećem SmPC/ALIMS izvoru i kliničkom kontekstu.
- **Produkcioni prvi pristup bez demo naloga**: kada je `CLINIC_ENV=production`, aplikacija neće napraviti `doctor123` / `admin123` naloge. Prvo pokretanje zahteva eksplicitnog bootstrap administratora kroz environment promenljive.

### Changed
- Docker image sada uključuje lokalne OCR zavisnosti (`tesseract-ocr` i `poppler-utils`) i koristi oznaku `1.10.0`.

## [1.9.0] - 2026-08-08

Izdanje fokusirano na tri konkurentske prednosti proizvoda: izvoz podataka
kao odgovor na inspekciju i prava pacijenata, uvođenje ordinacije u rad bez
IT stručnjaka, i epidemiološki radar vidljiv u svakodnevnom radu umesto
skriven u posebnom tabu.

### Added
- **Izvoz kartona pacijenta** (dugme u radnom prostoru, doctor/admin):
  ZIP sa `karton.json` (mašinski čitljiv kompletan zapis), `pregledi.csv`
  i tekstom svih dokumenata — odgovor na zahtev pacijenta za kopiju
  svojih podataka (pravo na prenosivost). Svaki izvoz se beleži u
  revizionom dnevniku.
- **Izvoz podataka ordinacije** (dugme u evidenciji aktivnosti, samo
  admin): ZIP sa CSV fajlovima (pacijenti, pregledi, termini, kompletan
  revizioni dnevnik) i README na srpskom — format koji inspektor ili
  knjigovođa otvara direktno u Excel-u. CSV se izvozi kao UTF-8 sa BOM
  oznakom da Excel ispravno prikaže srpske dijakritike.
- **Onboarding checklist** (baner na kontrolnoj tabli, samo admin):
  proverava da li demo lozinke (doctor123, admin123, reception123) i
  dalje važe i glasno upozorava, da li je naziv ordinacije još uvek
  „Demo Clinic", i nudi promenu naziva na klik. Novi endpointi
  `/api/setup/checklist` i `PATCH /api/organization`.
- **Epidemiološki signal na kontrolnoj tabli** (doctor/admin): klasteri
  i laboratorijski potvrđeni patogeni iz radara sada se prikazuju kao
  baner na početnom ekranu sa prečicom na radar — radar postaje deo
  radnog dana umesto tab koji se ne otvara.
- Novi testovi za sve četiri funkcionalnosti, uključujući RBAC (izvoz
  ordinacije samo admin, izvoz kartona ne za recepciju), tenant
  izolaciju izvoza i BOM u CSV fajlovima.

## [1.8.0] - 2026-08-08

Nastavak v1.7.0 UX rundе: bezbednosna runda po novom interfejsu, PDF
izveštaj sa istim tretmanom kao ekran, tablet/mobilni prikaz, vizuelne
eskalacije za crvenu zastavicu i kašnjenje termina, i proširenje kliničke
logike.

### Added
- **Eskalacije na kontrolnoj tabli**: crveni baner kad AI diferencijalna
  analiza ima crvenu zastavicu koja čeka odluku lekara (potvrdi/odbaci),
  sa direktnim skokom na karton pacijenta. Vidljivo samo za doctor/admin.
- **Kašnjenje termina**: zakazan termin čije je vreme prošlo bez prijave
  pacijenta sada dobija „Kasni X min" oznaku na kontrolnoj tabli i u
  rasporedu.
- **Tablet/mobilni prikaz**: sidebar navigacija je ranije na širinama
  ispod 900px potpuno nestajala i postajala nedostupna. Zamenjena je
  hamburger meni dugmetom i off-canvas panelom; brza pretraga pacijenta
  (Ctrl+K) sada je dostupna i iz gornje trake na svakom ekranu, ne samo
  iz radnog prostora.
- **Novi klinički sindrom „urinarni"** u epidemiološkom radaru (dizurija,
  bol/pečenje pri mokrenju, učestalo mokrenje) i novo pravilo „Infekcija
  urinarnog trakta" u AI diferencijalnoj analizi.
- Novi testovi: adversarial bezbednosna runda za sve UI površine dodate u
  v1.7.0 (RBAC na safety strip/PDF/diferencijalnu analizu preko API-ja,
  XSS otpornost, tenant izolacija, regresija login lockout-a i
  enumeracije korisničkih imena), PDF UX testovi, testovi za eskalacije
  i za novu kliničku logiku.

### Changed
- **PDF medicinski izveštaj u potpunosti prerađen** iz ugla lekara: sada
  na srpskom (prethodno na engleskom), sa crvenim alergijskim
  upozorenjem na vrhu dokumenta (isti princip kao ekranski „safety
  strip"), godinama pacijenta, vitalnim znacima istaknutim po istim
  pragovima odstupanja kao na ekranu, i footerom sa vremenom generisanja
  i brojem strana.
- Negation detection (heuristika za „bez X" / „X negativan") premeštena
  iz `differential.py` u deljeni `clinical_keywords.py`, po istom
  principu kao i ranije centralizovanje liste ključnih reči — sprečava
  da se logika neopaženo razmimoiđe između modula.

### Fixed
- **Epidemiološki radar nije imao detekciju negacije**: „bez kašlja" se
  računalo kao pozitivan slučaj respiratornog sindroma u trend grafikonu
  i klaster signalima — ista klasa greške koja je ranije ispravljena u
  diferencijalnoj analizi, ovog puta pronađena u drugom modulu jer
  logika nije bila deljena.
- **Izveštaj u PDF-u sa srpskim dijakritičkim imenom pacijenta** (č/ć/ž/
  š/đ) mogao je izazvati grešku pri slanju HTTP zaglavlja
  (Content-Disposition je ograničen na Latin-1); sada se šalje
  transliterisano ASCII ime fajla uz UTF-8 varijantu (RFC 5987) za
  pregledače koji je podržavaju.
- **Test svita je delila pravu bazu podataka ordinacije** (`data/`)
  između uzastopnih pokretanja umesto izolovanog direktorijuma, pa su se
  neuspeli pokušaji prijave gomilali kroz vreme i mogli slučajno
  zaključati pravi `doctor` demo nalog. Testovi sada rade nad
  privremenim direktorijumom (`CLINIC_DATA_DIR` env promenljiva, nova).

## [1.7.0] - 2026-08-08

UX izdanje iz ugla lekara: manje klikova do kartona, kritične bezbednosne
informacije uvek na ekranu, i brže prepoznavanje odstupanja.

### Added
- Brzi izbor pacijenta (Ctrl+K / Cmd+K): paleta za pretragu pacijenata sa
  navigacijom tastaturom (strelice + Enter) i listom nedavno otvaranih
  kartona (čuva se lokalno u pregledaču, bez ličnih podataka na serveru
  van postojećeg API-ja).
- Bezbednosna traka pacijenta u zaglavlju kartona: alergije (crveno,
  uvek vidljive), broj aktivnih terapija, vodeće dijagnoze i krvna grupa —
  bez otvaranja panela kliničkog profila. Prikaz izračunatih godina
  pacijenta pored datuma rođenja.
- Skok „Karton" direktno iz reda termina (kontrolna tabla i raspored) —
  jedan klik od rasporeda do otvorenog kartona pacijenta.
- Vitalni znaci u listi pregleda kao bedževi sa automatskim isticanjem
  odstupanja (TA ≥140/90 ili <90 sist., puls >100 ili <50, T ≥37,5 °C ili
  <35 °C, SpO₂ <94 %). Podržan decimalni zarez ("38,2").
- Jedinice i orijentacione vrednosti u formi pregleda (mmHg, /min, °C, %).
- Relativni datumi („danas u 10:30", „juče u 14:05") za dokumente i
  preglede.
- Zaglavlje aktivnog pacijenta ostaje zakačeno pri skrolovanju dugog
  kartona (sticky), da lekar nikad ne izgubi kontekst čiji karton gleda.
- Vidljiv fokus za navigaciju tastaturom (focus-visible) i poštovanje
  prefers-reduced-motion podešavanja.

### Changed
- Statusi termina se prikazuju na srpskom („Zakazano", „Pacijent stigao",
  „Završeno", „Otkazano") umesto sirovih engleskih vrednosti iz API-ja;
  otkazani termini su precrtani, stigli pacijenti označeni bočnom linijom.
- Razgovor „Pitajte karton" se automatski pomera na najnoviju poruku.
- Prazna stanja sada predlažu sledeći korak umesto samo konstatacije.

### Fixed
- Dva zaostala engleska teksta u interfejsu: „Proveraing record…" pri
  postavljanju pitanja i „Izaberite pacijenta first" pri dodavanju
  dokumenta bez izabranog pacijenta.

## [1.6.1] -- 2026-07-31

### Fixed
- Main content area was capped at `max-width:1500px`, which left a
  noticeable empty margin on either side on common 1920px+ screens (and a
  large one on ultrawide monitors), making the dashboard look small
  relative to the browser window. Widened the cap to 1800px and gave the
  dashboard's metric cards a bit more padding/font size so the extra room
  is used deliberately rather than left as blank space. Verified visually
  by rendering the dashboard at 1920px and 2560px before and after.

## [1.6.0] -- 2026-07-31

### Added
- Epidemiological radar now shows a real trend chart -- a hand-rolled SVG
  multi-line graph of daily case counts per syndrome over the selected
  window (7/14/30 days), not just a two-period before/after comparison.
  No external charting library or CDN dependency: the existing strict CSP
  (`script-src 'self'`) would have blocked a CDN-hosted script anyway, and
  a self-hosted dependency would have added a build step this project
  deliberately doesn't have. `build_radar()` in `epidemiology.py` now also
  returns `daily_counts` (a list of `{date, counts}` per calendar day in
  the window); the existing period-over-period fields are unchanged.

## [1.5.0] -- 2026-07-29

Second adversarial round: SQL injection, XSS, and a decompression-bomb
attack against file uploads. One critical finding (server crash), one
real correctness bug found via the same review.

### Fixed
- **Critical: a small crafted `.docx` upload could crash the server
  outright.** A `.docx` is a zip archive; a ~1.5MB file with an extreme
  compression ratio expanded to ~445MB when handed to python-docx, which
  exhausted memory and killed the process after ~112 seconds of
  processing -- confirmed by hand, not a theoretical concern. The existing
  15MB upload-size limit only checked the *compressed* size and did
  nothing to stop this. Fixed by reading each zip member's declared
  uncompressed size from the archive's central directory (cheap, no
  decompression) and rejecting anything over 50MB before ever calling
  `Document()`. Also added a hard cap on extracted text length regardless
  of source format as a second line of defense. Re-tested the exact file
  that crashed the server before: now rejected instantly (415), server
  stays healthy.
- **PDF export crashed (500) on ordinary clinical text.** `chief_complaint`
  and the clinic's name were passed unescaped into reportlab's `Paragraph`
  (which interprets a small markup subset), unlike anamnesis/examination/
  assessment/plan, which already had the correct `&`/`<`/`>` escaping. Any
  chief complaint containing a bare `<` -- e.g. the entirely ordinary
  "Bol < 3/10" -- broke report generation. Fixed by routing every value
  that reaches a `Paragraph()` through one escaping helper.

### Verified, not changed
SQL injection through every text field tried (patient name, login
username/organization) -- parameterized queries hold, payloads are stored/
compared as literal data, never executed. An f-string-built SQL statement
in `scripts/rotate_key.py` looked risky at a glance but isn't: the
interpolated value is always one of a fixed, hardcoded table-name list,
never user input (comment added for future readers). Stored XSS: reviewed
every `innerHTML` assignment in the frontend (16+ call sites) -- all
consistently route user-controlled text through the existing `esc()`
helper.

### Added
- `backend/tests/test_extractors.py`: unit tests for the decompression-
  bomb guard, plus normal docx/pdf/txt/md extraction and a corrupt-archive
  case.
- Two new `backend/tests/test_api.py` integration tests: the exact
  zip-bomb payload rejected through the real upload endpoint with the
  server verified alive afterward, and PDF generation with realistic
  angle-bracket clinical shorthand.

## [1.4.0] -- 2026-07-29

First adversarial security pass: actively trying to break the app (IDOR,
path traversal, mass assignment, rate-limiter bypass, timing attacks)
instead of only verifying it behaves as intended. Most of what was tried
held up; one real, serious finding below.

### Fixed
- **Username-enumeration timing side channel in login.** `authenticate()`
  used `row and _verify_password(...)`, so the ~260k-iteration PBKDF2
  password check was skipped entirely via short-circuit whenever the
  organization or username didn't match anything -- measured locally at
  ~190ms for a real account with the wrong password vs. ~3.5ms for a
  nonexistent account/org, a 50x+ difference trivially usable to enumerate
  valid usernames purely by timing, even though the HTTP response body is
  identical (401) either way. Fixed by always running the full password
  hash comparison against a fixed dummy hash when no matching row exists,
  closing the gap to within normal jitter (~185-200ms either way, measured).
- **Rate limiter becomes a single shared global limiter behind a reverse
  proxy.** It keyed on `request.client.host`, which is the proxy's own
  address once one is in front of the app -- meaning all real clients would
  share one rate budget, so one heavy or malicious client could trigger 429s
  for everyone. Added opt-in `CLINIC_TRUST_PROXY_HEADERS=1` to key on
  `X-Forwarded-For` instead, defaulting to off since blindly trusting that
  header without an actual proxy in front would let any direct client spoof
  a fresh IP on every request and bypass the limiter entirely.

### Verified, not changed
Tried and found to already hold up correctly: cross-organization IDOR
across every patient-scoped endpoint including compound-ID cases (a
patient's own valid ID substituted for another patient's scribe-draft/
differential-analysis ID within the same org); path traversal via a crafted
upload filename (`Path(...).name` strips it); mass assignment of
server-controlled fields (`id`, `organization_id`, `role`,
`must_change_password`) on patient and user creation; direct
`X-Forwarded-For` header spoofing without the new trust-proxy opt-in.

### Added
- `test_authenticate_pays_the_same_verification_cost_whether_or_not_the_account_exists`,
  `test_cross_organization_idor_across_scribe_and_differential_endpoints`,
  `test_mass_assignment_is_rejected_on_patient_and_user_creation`,
  `test_client_ip_ignores_x_forwarded_for_unless_trust_proxy_enabled` in
  `backend/tests/test_api.py`.

## [1.3.0] -- 2026-07-29

First pass at reviewing the parts of this project that hadn't been touched
since the initial hardening work: the actual clinical-suggestion logic, and
the frontend.

### Fixed
- `differential.py`'s keyword matching had no negation handling: a negated
  finding ("bez kašlja" / "test negativan") counted as positive supporting
  evidence for a diagnosis candidate the same as an actual, unnegated
  finding would. This is a real false-positive risk, not a cosmetic one.
  Added a heuristic negation check (still substring-based, not linguistic
  parsing -- documented as such) that suppresses a match when a common
  Serbian/English negation cue appears immediately before the matched term.
- `differential.py`'s epidemiology context never read the radar's `clusters`
  output (its own highest-confidence signal) at all, and separately failed
  to surface a syndrome newly emerging from zero prior cases (its
  `change_percent` is `None` in that case, which a bare truthiness check
  silently treated as "not rising"). Both are now included.
- `ai.py`'s pre-visit-briefing "abnormal finding" scan had the same
  English-only keyword bug already fixed once in `store.py`'s document
  attention flag -- an independent copy of the same list, with the same bug,
  that the earlier fix never touched. Both now import one shared list
  (`clinical_keywords.py`) specifically so this class of bug can't recur a
  third time from a silently-drifting duplicate.
- The frontend had no UI at all for the admin session-management endpoints
  added in an earlier release (`GET/DELETE /api/sessions`) -- they were
  only reachable by calling the API directly. Added an "Aktivne sesije"
  panel to the admin Users view (list active sessions, revoke one).

### Added
- `backend/tests/test_differential.py`: direct unit tests for the
  differential-diagnosis rule engine (negation handling, both directions;
  epidemiology-context assembly; score bounds).
- `COMPLIANCE_NOTES.md`: an honest, explicitly-not-legal-advice technical
  checklist of what a real legal/regulatory review (Serbian data protection
  law, medical records retention requirements) would need to check before
  this is used with real patients. Several concrete gaps are called out
  deliberately unimplemented (e.g. an automated retention/deletion policy)
  because the correct behavior depends on a legal determination this
  project cannot make on its own.

## [1.2.0] -- 2026-07-29

### Added
- `CLINIC_PORT`: run `./start.sh` on a port other than the default
  8080/8443 without editing the script.
- `start.sh` now checks whether the port is already in use *before* handing
  off to uvicorn, and prints a clear explanation (likely cause, how to find
  and stop the process holding it, how to use a different port) instead of
  uvicorn's bare `[Errno 48] Address already in use`.
- `backend/tests/test_scripts.py`: automated tests for `scripts/backup.py`,
  `scripts/restore.py`, and `scripts/rotate_key.py` (backup/restore round
  trip, tampered-archive rejection, key exclusion from backups, key
  rotation preserving data and audit-chain integrity, rotation correctly
  refusing to touch data it can't decrypt with a wrong old key). These were
  previously verified only by hand.

## [1.1.0] -- 2026-07-29

### Added
- `CLINIC_ENCRYPTION_KEY_COMMAND` (and `CLINIC_ENCRYPTION_KEY_OLD_COMMAND`
  for `scripts/rotate_key.py`): fetch the encryption key by running an
  external command instead of putting it directly in an environment
  variable -- the integration point for a real secrets manager (Vault, AWS
  Secrets Manager, etc.) without adding a vendor SDK dependency. A failing
  or empty command is a hard startup failure, never a silent fallback.
  `CLINIC_ENV=production` now accepts either this or `CLINIC_ENCRYPTION_KEY`.
- `CLINIC_TLS_CERT_FILE` / `CLINIC_TLS_KEY_FILE`: use a real certificate
  (e.g. from Let's Encrypt or an internal CA) with `CLINIC_TLS=1 ./start.sh`
  instead of the auto-generated self-signed one.

## [1.0.0] -- 2026-07-29

First hardened, "ready to run for real" release. Everything before this was
a working but demo-grade MVP (JSON-file storage, in-memory sessions, no
encryption at rest, no rate limiting); this release replaces the storage and
security foundations without changing the product's feature set.

### Added
- SQLite-backed storage (`backend/app/store.py`) with real transactions and
  a `UNIQUE` constraint that closes a username-race condition, replacing the
  JSON file that was rewritten whole on every write.
- Field-level encryption at rest (Fernet) for all patient-health data,
  uploaded files, and audit log detail text. See `backend/app/crypto.py`.
- `scripts/rotate_key.py` and `scripts/backup.py` / `scripts/restore.py`.
- Persistent, database-backed sessions (a restart no longer logs everyone
  out) with admin visibility and revocation (`GET/DELETE /api/sessions`).
- Login lockout after repeated failures, general per-IP rate limiting,
  minimum password strength rules.
- Tamper-evident audit log (hash chain) with `GET /api/audit/verify`.
- Security response headers (CSP, X-Frame-Options, HSTS over HTTPS, etc.).
- Optional local HTTPS via a self-signed cert (`CLINIC_TLS=1 ./start.sh`).
- `CLINIC_ENV=production` refuses to start without an explicit
  `CLINIC_ENCRYPTION_KEY` instead of silently auto-generating one.
- Router split (`backend/app/routers/`) instead of one large `main.py`.
- Structured logging (`CLINIC_LOG_FORMAT=json`) with a per-request ID
  echoed in `X-Request-ID` and in every access-log line for that request.
- `Dockerfile`, `docker-compose.yml`, `deploy/clinic-ai-assistant.service`
  (systemd), `.github/workflows/tests.yml` (CI).
- One-time, automatic migration from the old `clinic-store.json` format
  into the new encrypted database on first startup, including migrating a
  database that already ran an earlier build of this SQLite backend before
  the audit-encryption/session-id changes below existed (`schema_version`
  table + `_migrate_schema()`).

### Fixed
- `pip install -e .` failed on a fresh checkout (setuptools couldn't infer
  the package layout).
- The "needs attention" flag on uploaded documents only matched English
  keywords, so it essentially never fired for the Serbian-language
  documents this app is built around.
- A receptionist could read a patient's full clinical profile
  (diagnoses, medications) despite every other clinical endpoint being
  doctor/admin-only.
- Several endpoints returned a stale, pre-update copy of a record instead
  of the freshly written one (an artifact of the old in-memory store, where
  mutating an object in place happened to work by accident).
- A password change did not invalidate other active sessions for that
  user, so a stolen token kept working after the password was changed.
- `UPDATE ... SET x=NULL` does not remove the old bytes from a SQLite file
  on disk; migrating previously-plaintext audit details to encrypted
  storage now also drops the old column and runs `VACUUM`, and this was
  verified by reading the raw file bytes before/after.
- An intermittent (not 100%-reproducible) audit-hash-chain corruption
  caused by database reads not being serialized through the same lock as
  writes, safe only by accident depending on the SQLite build's threading
  guarantees.

## [0.9.3] and earlier

Pre-hardening MVP. Not tracked in this changelog; see git history / the
original project handoff if you need the detail.
