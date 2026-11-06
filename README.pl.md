# MHO900 Dekoder protokołów

[English](README.md) | Polski

Aplikacja Windows dekodująca protokoły z kanałów analogowych i cyfrowych oscyloskopu Rigol MHO98 (seria MHO900)
dekoderami projektu libsigrokdecode (sigrok, PulseView) oraz generująca sygnały UART, I2C, SPI, CAN i CAN FD na
wyjściach AFG dla innych urządzeń. Łączy się po sieci (SCPI na TCP 5555), nie wymaga NI-VISA. Dekoderów może działać
wiele naraz, jeden pod drugim, a każdy można rozbudować w stos.

Domyślnym językiem interfejsu jest angielski. Menu **Language** (po polsku **Język**) przełącza między angielskim a polskim, a wybór jest zapamiętywany.

## Uruchomienie

Program z kodu źródłowego uruchamia [start.bat](start.bat): aktywuje `.venv` i startuje `main.py`. Można go kliknąć dwukrotnie.
Jeśli program skończy się błędem, okno konsoli zostaje otwarte z jego treścią.

## Obsługa

1. Wpisz adres IP i kliknij **Połącz**.
2. Przeciągnij dekoder z drzewa i przypisz sygnały do kanałów CH1–CH4 lub D0–D15. Kolejne dekodery dodaje się
   tak samo, także ten sam dekoder kilka razy na innych kanałach i z innymi ustawieniami. Nowy dekoder bierze tylko kanały w użyciu:
   sygnały ponad ich liczbę zostają puste, dopóki ich nie przypiszesz, i żaden kanał nie jest dokładany.
3. Kliknij etykietę pasu dekodera albo wybierz **Ustawienia dekodera** z menu prawego klawisza: otworzy się okno, w którym ustawisz kanały
   i parametry protokołu. Dekodowanie rusza samo po każdej zmianie przebiegu, kanału lub opcji, więc nie ma przycisku dekodowania.
   Sygnały nowego dekodera zaczynają od kanałów w użyciu: tych, na których był sygnał w ostatnich danych, potem włączonych na oscyloskopie, potem
   wybranych w aplikacji. Najpierw idą kanały, których nie używa inny dekoder.
4. Kliknij **Autoskala** w zakładce **Oscyloskop**, gdzie podgląd na żywo pokazuje wynik, aby uruchomić funkcję Auto oscyloskopu na kanałach analogowych wybranych w aplikacji. Są one wtedy włączane,
   a pozostałe wyłączane. Auto dopasowuje każdy kanał do sygnału na tym kanale i ustawia wyzwalanie. Program odczytuje potem zapis i ustawia
   podstawę czasu tak, by widać było cały pakiet: pakiet to ciąg zboczy z przerwą dłuższą niż 20 typowych odstępów między zboczami po obu stronach.
   Gdy zapis nie zawiera całego pakietu albo wybrany kanał nie ma w nim zbocza, podstawa czasu jest zwiększana, najwyżej do 100 ms/dz, a sygnał bez pakietów zachowuje podstawę czasu
   z Auto. Holdoff wyzwalania jest ustawiany na długość pakietu plus połowę tej przerwy, więc oscyloskop nie wyzwala się ponownie wewnątrz pakietu. Kanały, które Auto zostawi włączone, stają się wybranymi.
5. Na zakładce **Oscyloskop** wybierz głębokość pamięci, podstawę czasu (lista 500 ps/div … 500 s/div
   w krokach 1-2-5) i wyzwalanie, sprawdzając efekt na podglądzie, a potem kliknij **Run**.
   Przycisk **Czytaj pamięć** dekoduje to, co jest już w pamięci oscyloskopu, a pole **Powtarzaj** daje akwizycje
   jedna po drugiej, aż je odznaczysz. Próbki zostają: każda kolejna akwizycja trafia dalej na osi czasu, za przerywaną linią
   z numerem, a dekodery czytają każdą akwizycję osobno. Inne kanały lub inna częstotliwość próbkowania zaczynają zapis od nowa,
   a przy około 2 GB próbek Powtarzaj wyłącza się samo. Ręczny **Run** i **Czytaj pamięć** też zaczynają od nowa.
6. Kliknięcie wiersza tabeli przybliża wykres do tego elementu. Kółko myszy i przeciąganie
   zmieniają zakres wykresu.

Przycisk **Przerwij** pojawia się w miejscu **Run**, gdy coś trwa, i kończy bieżącą operację: odczyt pamięci, oczekiwanie na wyzwolenie,
autoskalę, ładowanie generatora albo dekodowanie. Opcja **Czekaj na wyzwolenie**
wyłącza wymuszanie akwizycji po 1 s, więc czeka na sygnał, aż go przerwiesz.

Wyzwalanie na protokołach (UART, I2C, SPI, CAN, LIN, I2S, FlexRay, 1553B) ustawia się na zakładce **Oscyloskop**:
warunek, adres, ID i dane. Pola danych przyjmują wartości szesnastkowe, a dane I2S i 1553B ciąg bitów 0, 1 i X (w I2S co najwyżej **Bity użyte**). Najstarszy
bit wpisuje się pierwszy. Pola bitowe są tylko wysyłane, więc zmiana na samym oscyloskopie ich nie zaktualizuje. Zadziałanie
wyzwalania sprawdzono na sygnale dla UART, I2C, CAN i SPI. Dla LIN, I2S, FlexRay i 1553B sprawdzono tylko, że przyrząd
przyjmuje i oddaje ustawienia. Źródłem SPI, I2S, FlexRay i 1553B nie może być D15, gdy kanały cyfrowe są wyłączone.

W czasie trwającej operacji podgląd na zakładce **Oscyloskop** czeka na jej koniec.

Opcja **Szczegółowy (verbose)** nad logiem dopisuje znaczniki czasu, każdą komendę SCPI z odpowiedzią,
rozmiar, czas i prędkość odczytów oraz liczbę zboczy na sygnał.

Wyniki eksportuje przycisk **Eksport wyników CSV**. Przebiegi można zapisać i dekodować
później bez oscyloskopu (**Zapisz przebiegi**, **Otwórz przebiegi**).

Program ostrzega o przesterowaniu kanału i o sprzężeniu innym niż DC.

Pusty kanał nie daje fałszywych zboczy. Sam szum przyrządu zmierzono od 10 mV/dz do 5 V/dz: do 0,11 działki między skrajnymi
wartościami, więc przy automatycznym progu sygnał poniżej 0,2 działki liczy się jako stały poziom. Słabszy sygnał, na przykład
3,3 V przy 20 V/dz, wymaga ręcznego progu w ustawieniach dekodera. Plik BIN otwarty z dysku nie ma skali, więc tam obowiązuje
tylko stała granica 20 mV.

## Prędkość odczytu pamięci

Domyślny format to BYTE w jednym bloku, najszybsza droga wyjścia danych z tego przyrządu.
Czasy zmierzone dla 10 M punktów:

| Metoda | Czas |
|---|---|
| BYTE, jeden blok | 5,5 s |
| BYTE, paczki po 1 M | 7,9 s |
| WORD, jeden blok | 11 s |
| plik na oscyloskopie i FTP | 13 s |
| ASCII | około 150 s (policzone z próbki 10 k punktów) |

100 M punktów w BYTE trwa około 55 s. BYTE to górny bajt próbki WORD. WORD (na liście **Format odczytu**)
przydaje się, gdy sygnał zajmuje mały ułamek zakresu. Limitem jest ścieżka SCPI przyrządu, około 1,8 MB/s:
FTP na tym samym łączu daje 11,8 MB/s, a większy bufor gniazda nic nie zmienia.

## Okno programu

Układ jest wzorowany na analizatorach logicznych (PulseView, Saleae Logic, DSView). Panel dekoderów przyjmuje
rozwiązania PulseView: drzewo z wyszukiwarką i opisem, niezależne dekodery jeden pod drugim, gwiazdkę przy wymaganych
sygnałach, dokładanie na stos, ukrywanie dekodera i pojedynczych wierszy oraz pas dekodera widoczny od razu po dodaniu.
Z PulseView nie ma tu edycji nazwy i koloru pasu, przeciągania pasów w inną kolejność, pauzy dekodowania ani menu
kopiowania i eksportu adnotacji z pasu. Ustawienia otwierają się w osobnym oknie, a nie w dymku.

- **Pasek narzędzi u góry:** tylko połączenie, a w wierszu pod nim linia stanu oscyloskopu. Każda zakładka ma własne elementy sterujące.
- **Zakładka Przebiegi:** **Ustawienia oscyloskopu**, **Run** (F5), a w jego miejscu w trakcie pracy **Przerwij** (Esc), pole **Powtarzaj**, osobno odczyt pamięci,
  powiększenie, widok **Logika/Analog** i **Kursory**. Skróty tych elementów działają na tej zakładce.
- **Zakładka Oscyloskop:** ręczne RUN, STOP, SINGLE i FORCE oraz **Autoskala**, a w jej miejscu w trakcie pracy **Przerwij**.
- **Ustawienia oscyloskopu:** menu w zakładce Przebiegi z kanałami CH1–CH4 i D0–D15, głębokością pamięci, podstawą czasu, wyzwalaniem,
  formatem odczytu, wymuszeniem DC i czekaniem na wyzwolenie. Oscyloskop dostaje je dopiero przy **Akwizycji**: zaznaczone kanały
  włącza, a niezaznaczone wyłącza. **Czytaj pamięć** niczego z tego nie wysyła, a tylko zatrzymuje oscyloskop i czyta. „Bez zmian”
  zostawia to, co jest na oscyloskopie, także po zmianie w zakładce Oscyloskop, która działa od razu. Obok kanałów cyfrowych są progi
  grup D0–D7 i D8–D15: zaznaczony próg akwizycja ustawia na grupie w użyciu, a odznaczony zostaje taki, jak na oscyloskopie.
- **Pretrigger:** w grupie Akwizycja wybiera się, ile procent pamięci ma leżeć przed wyzwoleniem. Oscyloskop nie ma na to polecenia, więc
  akwizycja ustawia przesunięcie podstawy czasu (`:TIMebase:MAIN:OFFSet`) wyliczone z głębokości pamięci i częstotliwości próbkowania.
  Oscyloskop ogranicza to przesunięcie do −5 działek, więc gdy pamięć jest dłuższa niż ekran, duży pretrigger jest nieosiągalny i log podaje, ile wyszło.
  Odznaczone: przesunięcie zostaje takie, jak na oscyloskopie.
- **Linia wyzwolenia:** czerwona pionowa linia „Trig” przechodzi przez wszystkie wiersze w chwili wyzwolenia, przy Powtarzaj osobno dla każdej
  akwizycji. W akwizycji wymuszonej to chwila wymuszenia. Pliki WFM i pliki NPZ bez zapisanej chwili wyzwolenia nie mają tej linii.
- **Oznaczenie wyzwalania przy kanale:** etykieta wiersza pokazuje obok numeru kanału „Trig” z symbolem warunku albo z nazwą protokołu, którego kanał jest źródłem, np. `CH1 Trig I2C`.
- **Wyzwalanie w Ustawieniach oscyloskopu:** zakładka **Kanały** to warunki na pojedynczych kanałach. Zakładka **Zaawansowane** to
  wyzwalanie na protokole (UART, I2C, SPI, CAN, LIN, I2S, FlexRay, 1553B) ze źródłami na CH1–CH4 i D0–D15. Źródło na D0–D15 nie ma
  własnego poziomu, bo przełącza się na progu swojej grupy. Protokół i warunki na kanałach zastępują się nawzajem, a akwizycja czeka na
  takie wyzwolenie bez limitu. Ustawienia, które oscyloskop odrzuci, kończą akwizycję błędem. Poziomów we wzorcu na D0–D15 nie da się
  ustawić przez SCPI: `:TRIGger:PATTern:PATTern` z więcej niż 4 wartościami daje -108, a 4 wartości działają na CH1–CH4 (firmware
  00.01.00). Taki wzorzec można ustawić, klikając ekran na podglądzie: akwizycja bez warunków zostawia wyzwalanie bez zmian.
- **Kanały na przebiegach:** każdy zaznaczony kanał ma swój wiersz. Kanał bez zmian poziomu rysuje swój stały poziom: napięcie
  w widoku Analog, niski albo wysoki w widoku Logika. Kanał, o który prosi odczyt, a którego nie ma w pamięci oscyloskopu, pokazuje na
  wierszu błąd. **Ukryj nieużywane** jednym kliknięciem odznacza oba rodzaje, oprócz użytych przez dekoder lub wyzwalanie, a kanał
  zaznaczony z powrotem znów się pokazuje. Otwarcie pliku wybiera kanały z tego pliku.
- **Kliknięcie etykiety kanału:** okno jak dymek kanału w PulseView, z warunkiem wyzwalania (brak, niski, wysoki, zbocza), progiem
  i, w widoku Analog, skalą pionową. Na D0–D15 są tylko zbocza, bo wzorzec oscyloskopu przyjmuje wyłącznie CH1–CH4. CH1–CH4 można
  łączyć we wzorzec z jednym zboczem narastającym lub opadającym, a dowolne zbocze działa tylko samo. Akwizycja z warunkiem czeka na
  wyzwolenie bez limitu. Próg ustawiony na kanale działa w widoku Logika, w dekoderach z progiem automatycznym i w wyzwalaniu.
  Bez progu poziom wyzwalania to środek sygnału z ostatniego odczytu, a bez odczytu zostaje poziom z oscyloskopu.
- **Zakładka Przebiegi:** kółko myszy powiększa, a przeciąganie lub suwak pod przebiegami przesuwa w czasie. Widok **Logika** pokazuje
  stany po progu, a **Analog** napięcia. Przy powiększeniu, w którym próbki da się rozróżnić, **Analog** zaznacza każdą kropką. Wiersze przebiegów są niskie, a miejsce dostają wiersze dekodera. Skala pionowa
  w widoku **Analog** jest dopasowywana raz, do całego odczytu, i nie zmienia się przy zmianie skali czasu. Kliknięcie osi
  napięcia pozwala wpisać górę i dół zakresu albo wrócić do skali automatycznej, a wpisany zakres zostaje też przy kolejnych
  odczytach. Kursory, jak w PulseView, mają nad osią
  czasu flagi A i B i strzałkę z czasem i częstotliwością między nimi, a obszar między nimi jest zacieniowany. Przeciąga się
  linie albo flagi. Tam, gdzie widok **Analog** pokazuje kropki, kursor przeciągnięty blisko próbki przyciąga się do niej, a między próbkami porusza się swobodnie. Położenia A i B podaje pasek stanu. Paski adnotacji mają kolor wiersza dla danych i szary dla pól
  sterujących protokołu (start, stop, ACK, adres). Tak samo wyróżnia je tabela. Tekst, który nie mieści się na pasku w żadnej
  postaci, jest pomijany. Kliknięcie paska adnotacji zaznacza jej wiersz w tabeli, a kliknięcie wiersza pokazuje ją na
  przebiegu.
- **Zakładka Oscyloskop:** ekran oscyloskopu na żywo, ze strumienia H.264 strony WWW oscyloskopu, w którym lewy przycisk myszy działa
  jak dotyk. Strumień działa tylko przy widocznej zakładce i zaznaczonym podglądzie. Strona WWW i program wypychają się nawzajem z tego
  połączenia. Oprócz tego podstawa czasu, głębokość pamięci, tryb akwizycji, kanały
  CH1–CH4, kanały cyfrowe i wyzwalanie. Każda zmiana idzie do oscyloskopu od razu, a wartości są stale odczytywane,
  więc widać też zmiany zrobione na samym oscyloskopie. Odrzucone polecenie pojawia się na pasku stanu. **Holdoff** w ustawieniach wyzwalania
  przyjmuje od 8 ns, co oznacza jego brak, do 10 s i działa tylko przy wyzwalaniu typu Zbocze, bo Programming Guide wyklucza go dla pozostałych typów z listy.
- **Linia stanu oscyloskopu:** we własnym wierszu pod połączeniem, na każdej zakładce, odczytywana co sekundę: stan akwizycji, włączone kanały, podstawa czasu, częstotliwość próbkowania,
  głębokość pamięci i wyzwalanie. **Holdoff włączony** z jego czasem jest tam widoczny, dopóki holdoff działa.
  Lista V/dz ma kroki 1-2-5 pomnożone przez mnożnik sondy kanału, od 1 mV × mnożnik do 10 V × mnożnik przy wejściu 1 MΩ, a wybór z niej wyłącza
  dokładne strojenie kanału (`:CHANnel<n>:VERNier OFF`). Wartość spoza kroków ustawioną na oscyloskopie lista pokazuje, dopóki jest ustawiona.
  Przesunięcie czasu jest pokazywane z przedrostkiem SI i tak można je wpisać (`1,5 ms`, `20 us`).
  Grupy D0–D7 i D8–D15 same włączają i wyłączają kanały cyfrowe, bez osobnego pola.
  Głębokość **Auto** pokazuje w nawiasie liczbę punktów dobraną przez oscyloskop. Odczyt z oscyloskopu podaje liczbę punktów także w Auto, więc
  program rozpoznaje Auto po tym, że sam je ustawił, albo po liczbie punktów, której nie ma żadna stała głębokość. Stała głębokość ustawiona
  na samym oscyloskopie po wybraniu Auto w programie nadal pokaże się jako Auto.
  Na tej zakładce panele dekodera są schowane.
- **Panel boczny:** zakładki **Dekoder** i **Generator**. Dekodery są w drzewie, sortowanym po kategoriach albo protokole
  nadrzędnym. Przeciągnij dekoder na przebiegi (albo kliknij dwukrotnie). Upuszczony na pas dekodera, do którego pasuje,
  trafia na jego stos, dekoder wymagający bazy trafia na stos wybranego dekodera, jeśli pasuje, a każdy inny dostaje nowy pas
  pod poprzednimi. Grupa **Pasuje do stosu** pokazuje, co można dodać do wybranego dekodera.
- **Menu pasu dekodera:** prawy klawisz na pasie daje **Ustawienia dekodera** i **Usuń dekoder**. Usuwany jest cały dekoder
  razem ze stosem.
- **Podpowiedzi:** po najechaniu myszą przycisk lub pole wyboru pokazuje, co robi.
- **Okno ustawień dekodera:** otwiera je kliknięcie etykiety pasu lub menu prawego klawisza. Ma kanały, opcje, pole pokazania
  lub ukrycia, przycisk usunięcia, listę **Wiersze** i **Dodaj na stos**. Prędkość UART, CAN, CAN FD i Miller wybiera się z listy typowych
  wartości, a **Własna…** otwiera pole na dowolną.
  Na przebiegach najpierw są kanały, a pod nimi pasy kolejnych dekoderów. Etykiety pasów drugiego i kolejnych dekoderów
  mają jego numer (`#2`).
- **Wyniki na dole:** wyszukiwarka, filtr wiersza dekodera i **Tylko błędy**.
- **Menu:** **Plik** (otwieranie i zapis przebiegów, eksport CSV), **Widok** (panele,
  **Motyw** jasny lub ciemny), **Zaawansowane** (okno odbioru i porównania danych, **Log** w osobnym oknie, Ctrl+L, i log szczegółowy).

Ostatni komunikat widać na pasku stanu, a błąd jest tam czerwony. Kliknięcie otwiera log.

## Kanały cyfrowe D0–D15

Sygnał dekodera można przypisać do kanału cyfrowego (wymaga sondy logicznej). Program czyta je przez plik BIN zapisany
na oscyloskopie i pobrany przez FTP, bo `:WAVeform:SOURce` nie przyjmuje D0–D15. Zapis i pobranie trwają około 7 s
przy 1 M punktów i 23 s przy 10 M punktów (dwa kanały analogowe i cyfrowe).

- **Próg:** domyślny próg to 0 V, a logika 0–3,3 V wymaga około 1,65 V. Ustawia się go na zakładce Oscyloskop.
- **Gęstość próbek:** kanały cyfrowe mają 1/4 próbek kanału analogowego przy jednym kanale, 1/2 przy dwóch i tyle samo
  przy czterech. Program czyta ten stosunek z pliku.
- **Łączenie z analogowymi:** kanały cyfrowe są opóźnione względem analogowych o kilka próbek (zmierzono na przykład
  8 próbek przy 50 MSa/s i 39 przy 1 GSa/s). Opóźnienie nie jest stałe w czasie, więc program go nie koryguje,
  a przy łączeniu kanałów w jednym dekoderze ostrzega w logu.

## Pliki oscyloskopu

**Plik → Otwórz przebiegi** czyta też pliki `.bin` i `.wfm` z oscyloskopu.

- **BIN:** napięcia jako 32-bitowe liczby zmiennoprzecinkowe i kanały cyfrowe. Pola rozmiaru w nagłówku są zawyżone,
  ale dane są kompletne: kolumny D0–D15 są identyczne z plikiem CSV.
- **WFM:** tylko próbki analogowe 16-bitowe, bez kanałów cyfrowych i bez częstotliwości próbkowania, którą trzeba podać.
  Napięcia CH1 i CH2 zgadzają się z BIN do 0,1 µV. Czas liczony jest od środka zapisu, więc przesunięcie czasu
  nie jest uwzględnione. CH3 i CH4 nie były sprawdzone na sygnale.

## Dekodery sigrok

Wszystkie dekodery programu pochodzą z sigrok. Wyszukiwarka nad drzewem filtruje je po nazwie i opisie, a lista **Sortuj**
układa je według kategorii albo protokołu nadrzędnego: dekoder wymagający innego, na przykład `i2cdemux`, leży wtedy
pod nim (I²C).

Niezmienione dekodery leżą w `mhodecode/srd_decoders` (130 używanych, wersja z repozytorium z 1.10.2024).
Plik `mhodecode/sigrokdecode.py` zastępuje rdzeń napisany w C, więc dekodery nie wymagają instalacji sigrok.

**Pas dekodera.** Dodany dekoder ma pas na osi czasu od razu, także bez danych. Wiersze pojawiają się, gdy mają adnotacje,
a do tego czasu pas jest pusty, z nazwą dekodera i ewentualnym brakiem (na przykład kanału) wypisanym na nim.

**Stos.** Dekoder wyższego poziomu czyta wynik dekodera pod nim, tak jak w PulseView. **Dodaj na stos** na karcie
pokazuje tylko te, które pasują do wyjścia ostatniego dekodera. Dodanie dekodera wymagającego bazy bez pasującej karty
samo buduje najkrótszy stos, na przykład `i2c → eeprom24xx`. Usunięcie dekodera ze stosu usuwa też dekodery nad nim.
Każda karta ma własny stos, kanały i ustawienia.

Każdy wiersz adnotacji ma własny pas na wykresie. Lista **Wiersz** nad tabelą pokazuje jeden wiersz, a domyślnie
wszystkie poza bitami. Porównanie (menu **Zaawansowane**) szuka kopii w wynikach wszystkich dekoderów naraz. Działa
dla dekoderów, które podają dane binarne (między innymi UART, I2C, SPI). Z CAN program czyta bajty danych z tekstu
adnotacji, bo ten dekoder danych binarnych nie podaje.

Ograniczenia:

- Przyrząd ma 4 kanały analogowe. Dekodery wymagające więcej nie dadzą sensownego wyniku: `aud`, `lpc`, `mcs48`,
  `seven_segment`, `z80`.
- `ir_irmp` nie jest dostępny, bo wymaga natywnej biblioteki libirmp.
- SPI bez linii CS liczy bity od pierwszego zbocza zapisu i się nie synchronizuje, więc zapis zaczęty w środku słowa daje
  przesunięte dane.
- Dekodery nie wykrywają prędkości same, więc trzeba ją wpisać (pomaga dekoder `guess_bitrate`). Teksty podają po angielsku.
- CAN czyta jedną linię, w której stan dominujący jest niski. Z pary różnicowej przypisz więc CAN_L, bo pary jako takiej
  nie obsługuje. Program nie ostrzega już o zbyt małej liczbie próbek na bit.
- Prefiks `0x` i cudzysłowy pokazują UART (według opcji **Data format**: hex i ascii), I2C, SPI i I2S, a CAN ma `0x` z dekodera.
  Pozostałe dekodery podają teksty bez zmian. O tym, co jest danymi, a co polem sterującym, program wnioskuje z nazw klas
  adnotacji. Dekoder bez klasy danych nie jest dzielony i ma tylko kolor wiersza.
- Poziom początkowy sygnału (Initial pin w PulseView) jest obsłużony w rdzeniu, ale okno go nie ustawia.

## Generator dla innych urządzeń

Zakładka **Generator** wysyła sygnał, a odbiór i porównanie są w menu **Zaawansowane**, w oknie **Odbiór i porównanie danych**.

1. **Generuj** wysyła dane na wyjścia AFG i zostawia je włączone. Dane podajesz jako hex (`0x48 0x65` albo `48 65`) albo ASCII
   (`"Hello"` albo `Hello`, także `\n \r \t \0 \\ \xNN`). Tekst w cudzysłowach traci je przy wysyłaniu, więc same
   cudzysłowy jako dane zapisz podwójnie (`""a""`). Każdej linii protokołu przypisujesz wyjście AFG1 lub AFG2 albo ją pomijasz.
   Dla każdego wyjścia ustawiasz poziom niski i wysoki oraz obciążenie (High-Z lub 50 Ω). Poziomy trafiają
   na wyjście, zanim się włączy, więc urządzenie nie zobaczy amplitudy z wcześniejszego użycia.
2. **Odbierz** wykonuje akwizycję z kanałami i dekoderem z zakładki **Dekoder** i ustawieniami z zakładki **Oscyloskop**.
   Przycisk **Ustaw dekoder i podstawę czasu wg generatora** kopiuje parametry protokołu i dobiera
   podstawę czasu. Kanały i źródło wyzwalania pozostają bez zmian, bo aplikacja nie wie, do czego
   podłączone są wyjścia.
3. **Porównaj** szuka w zdekodowanych danych kompletnej kopii bez błędów: danych wysłanych albo wpisanych
   jako oczekiwane. Przy niezgodności podaje pierwszy różniący się bajt. Porównanie działa dla każdego
   dekodera podającego dane binarne i dla CAN, także gdy urządzenie odpowiada innym protokołem.

**Pliki przebiegów AFG** w menu **Zaawansowane** mają ten sam formularz i nie wymagają połączenia. Narzędzie zapisuje po jednym pliku CSV na
każdą używaną linię, nazwany podaną nazwą i nazwą linii (`i2c_scl.csv`), i pokazuje, co wpisać w generatorze: obciążenie, plik, częstotliwość,
amplitudę, offset, a przy dwóch liniach także Align Phase.

Generator obsługuje UART, I2C, SPI, CAN i CAN FD. I2S wymaga 3 linii, a przyrząd ma 2 wyjścia.

Ograniczenia wyjść AFG:

- Rekord ma 16384 próbki i gra w pętli, bo przewodnik programisty nie opisuje trybu burst. Komunikat powtarza się,
  dopóki wyjście jest włączone. Program sam obniża liczbę próbek na bit (16, 8, 4), by dane się zmieściły.
- Najwyższa użyta częstotliwość próbkowania rekordu to 80 MSa/s, sprawdzona na oscyloskopie.
- Dłuższe dane CAN idą w kolejnych ramkach po 8 bajtów, a CAN FD po 64 bajty. Ostatnia ramka CAN FD musi mieć
  dozwoloną długość (0–8, 12, 16, 20, 24, 32, 48, 64).
- Program zapisuje pliki `mhodecode_afg1.csv` i `mhodecode_afg2.csv` w folderze `C:/arb` oscyloskopu (anonimowe FTP).

## Rozwój

```powershell
.\build.ps1
```

```powershell
.venv\Scripts\python.exe -m pytest tests -q
```

Poniższe testy wymagają połączenia AFG1 z CH1 i AFG2 z CH2:

```powershell
.venv\Scripts\python.exe tests\live_scope.py 192.0.2.1
```

```powershell
.venv\Scripts\python.exe tests\live_crosscheck.py 192.0.2.1
```

```powershell
.venv\Scripts\python.exe tests\live_sigrok.py 192.0.2.1
```

Zgodność z prawdziwym libsigrokdecode sprawdza się na jego przypadkach testowych, po sklonowaniu repozytoriów
[sigrok-test](https://github.com/sigrokproject/sigrok-test) i [sigrok-dumps](https://github.com/sigrokproject/sigrok-dumps):

```powershell
.venv\Scripts\python.exe tests\sigrok_conformance.py ŚCIEŻKA\sigrok-test ŚCIEŻKA\sigrok-dumps
```

Wynik: 574 z 594 przypadków. Pozostałe to `ir_irmp` (16, brak libirmp) i `avr_isp` (4). W `avr_isp` różnica dotyczy
tylko nazw układów z tabeli `parts.py`, które pliki wzorcowe pomijają.

`main.py --selftest ADRES --output FOLDER` przechodzi przez okno programu wszystkie funkcje,
zapisuje log i zrzuty ekranu oraz kończy się kodem 0 tylko wtedy, gdy wszystkie zadziałały.

## Licencja

Dekodery w `mhodecode/srd_decoders` pochodzą z projektu [libsigrokdecode](https://github.com/sigrokproject/libsigrokdecode)
i mają licencje GPLv2+, GPLv3+, BSD i MIT (pole `license` każdego dekodera, tekst GPLv3 w `COPYING`, autorzy
w `AUTHORS`). `mhodecode/sigrokdecode.py` odwzorowuje działanie ich rdzenia. Program rozprowadzany razem z nimi,
także jako exe, podlega licencji GNU GPL w wersji 3 lub nowszej, a odbiorca musi mieć dostęp do kodu źródłowego.

Układ panelu dekoderów jest wzorowany na programie [PulseView](https://github.com/sigrokproject/pulseview) (GPLv3).
Kod PulseView, napisany w C++, nie jest częścią tego programu.
