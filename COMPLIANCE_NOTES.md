# Napomene o usklađenosti (nije pravni savet)

Ovo **nije pravni savet** i ne predstavlja tvrdnju da je aplikacija usklađena
sa bilo kojim zakonom. Ovo je tehnički checklist -- šta aplikacija tehnički
radi, i šta bi neko sa pravnom/regulatornom ekspertizom (advokat, DPO,
komplajns službenik ordinacije) trebalo da proveri pre upotrebe sa pravim
pacijentima u Srbiji. AI asistent koji je pisao kod ove aplikacije nije
kvalifikovan da potvrdi pravnu usklađenost, i ovaj dokument to ne pokušava.

## Šta aplikacija tehnički radi (proveriti da li je dovoljno za vaš slučaj)

| Oblast | Trenutno stanje | Šta proveriti |
|---|---|---|
| Enkripcija podataka o ličnosti | Fernet enkripcija u mirovanju za sve kliničke podatke i upload-ovane fajlove | Da li je ovaj nivo enkripcije dovoljan za kategoriju podataka (posebno osetljivi podaci o zdravlju prema Zakonu o zaštiti podataka o ličnosti) |
| Kontrola pristupa | Uloge (lekar/recepcija/administrator), audit log svake aktivnosti, ograničenje pokušaja prijave, obavezna promena privremene lozinke i opciona TOTP višefaktorska prijava po korisniku | Da li raspodela uloga odgovara stvarnoj podeli odgovornosti u ordinaciji; za stvarne naloge uključiti MFA |
| Čuvanje medicinske dokumentacije | Podaci se čuvaju neograničeno dok se ručno ne obrišu | **Zakon o zdravstvenoj dokumentaciji i evidencijama propisuje minimalne/maksimalne rokove čuvanja po tipu dokumentacije** -- aplikacija trenutno nema automatsko brisanje/arhiviranje po isteku roka. Ovo treba proveriti i po potrebi implementirati. |
| Pravo pacijenta na uvid u sopstvene podatke | Lekar/administrator može izvesti kompletan karton jednog pacijenta kao auditovan ZIP; administrator može izvesti podatke cele ordinacije | Proveriti proceduru predaje izvoza pacijentu, identifikaciju podnosioca zahteva i šta se po zakonu sme/obavezno mora zadržati; aplikacija i dalje nema automatizovano brisanje |
| Obrada od strane trećih lica | Ako se koristi Ollama lokalno, podaci ne napuštaju mašinu; ako bi se koristio eksterni AI provajder, to bi bio prenos podataka trećoj strani | Proveriti da li se AI provider menja u budućnosti, i da li to zahteva ugovor o obradi podataka (DPA) |
| Pristanak pacijenta | Aplikacija ne modeluje eksplicitni pristanak pacijenta na obradu | Proveriti da li je i kako pribavljen pristanak pacijenta za AI-potpomognutu obradu njegovih podataka |
| Prijava incidenta (data breach) | Postoji tamper-evident audit log koji pomaže u forenzici, ali nema automatizovanog mehanizma prijave nadležnom organu | Zakon propisuje rokove za prijavu povrede podataka -- ovo mora biti organizacioni proces, ne samo tehnička mera |
| Lokacija podataka | Podaci ostaju lokalno na mašini gde je aplikacija pokrenuta (nema cloud storage po defaultu) | Proveriti da li to zadovoljava zahteve za lokalizaciju zdravstvenih podataka, ako postoje |
| Enkripcioni ključ | Van eksplicitnog demo režima aplikacija odbija start bez ključa iz environment promenljive ili komande za secrets manager | Ko ima fizički/administratorski pristup mašini efektivno ima pristup ključu -- ovo je organizaciono, ne samo tehničko pitanje |

## Šta bi bilo razumno implementirati NAKON pravnog pregleda

Namerno nije implementirano u ovom krugu jer zahteva odluku sa pravne strane
pre nego što se kodira (npr. tačan rok čuvanja se razlikuje po tipu
dokumentacije i to mora da odredi neko ko poznaje propis, ne ja):

- Automatizovana politika čuvanja/arhiviranja podataka (retention policy) po isteku zakonskog roka
- Endpoint/proces za brisanje podataka pacijenta na zahtev, uz zadržavanje minimuma potrebnog za zakonsku obavezu čuvanja evidencije
- Formalizovan proces pristanka pacijenta, evidentiran u sistemu
- Formalna procedura provere identiteta pre MFA oporavka. Tehnički administratorski reset, razlog u auditu i trenutna revokacija aktivnih sesija postoje, ali aplikacija ne može sama da potvrdi identitet osobe koja traži reset.

## Preporuka

Pre upotrebe sa pravim pacijentima: konsultovati advokata specijalizovanog za
zdravstveno pravo i zaštitu podataka o ličnosti u Srbiji, sa ovim dokumentom
kao polaznom tačkom za tehnički deo razgovora.
