# MHO900 Protocol Decoder

English | [Polski](README.pl.md)

A Windows application that decodes protocols from the analog and digital channels of the Rigol MHO98 oscilloscope (MHO900
series) with the decoders of the libsigrokdecode project (sigrok, PulseView), and generates UART, I2C, SPI, CAN and CAN FD
signals on the AFG outputs for other devices. It connects over the network (SCPI on TCP 5555) and does not need NI-VISA.
Many decoders can run at once, one under another, and each can be extended into a stack.

The interface is in English by default. The **Language** menu switches it between English and Polish, and the choice is remembered.

## Starting

[start.bat](start.bat) runs the program from source: it activates `.venv` and starts `main.py`. It can be double-clicked.
If the program ends with an error, the console window stays open and shows it.

## Use

1. Enter the IP address and click **Connect**.
2. Drag a decoder from the tree and assign its signals to channels CH1–CH4 or D0–D15. Further decoders are added the same
   way, including the same decoder several times on other channels with other settings. A new decoder takes only the channels in use:
   signals beyond them stay empty until you assign them, and no further channel is turned on.
3. Click the label of a decoder lane or choose **Decoder settings** from the right-click menu: a window opens where you set
   the channels and the protocol parameters. Decoding starts by itself after every change of a waveform, channel or option,
   so there is no decode button. A new decoder's signals start on the channels in use: those that showed a signal in the last data, then those
   on on the scope, then those chosen in the app. Channels no other decoder uses come first.
4. Click **Autoscale** on the **Oscilloscope** tab, where the live view shows the result, to run the scope's own Auto on the analog channels chosen in the app, which are turned on for it while the others
   are turned off. It fits each channel to the signal on that channel and sets the trigger. The program then reads a record and sets the time
   base so that one whole packet shows: a packet is a run of edges with a pause of over 20 typical edge spacings on both sides. If a record holds
   no whole packet, or a chosen channel has no edge in it, the time base is widened, up to 100 ms/div, and a signal without packets keeps the time base Auto gave it.
   The trigger holdoff is set to the packet plus half of that pause, so the scope does not trigger again inside a packet. The channels Auto
   leaves on become the chosen ones.
5. On the **Oscilloscope** tab choose the memory depth, the time base (a list from 500 ps/div to 500 s/div in 1-2-5 steps)
   and the trigger, watching the effect in the live view, then click **Run**.
   The **Read memory** button decodes what is already in the scope's memory, and the **Repeat** box runs acquisitions one
   after another until you untick it. The samples stay: each further acquisition goes further along the time axis, behind a
   dashed numbered line, and decoders read each acquisition on its own. Other channels or another sample rate start the
   record anew, and at about 2 GB of samples Repeat turns itself off. A manual **Run** and **Read memory** start anew as well.
6. Clicking a row of the table zooms the plot to that item. The mouse wheel and dragging change the range of the plot.

The **Abort** button appears in place of **Run** while something is in progress, and ends the current operation: a memory
read, waiting for a trigger, Autoscale, loading the generator or decoding. The **Wait for trigger** option turns off forcing
the acquisition after 1 s, so it waits for a signal until you abort it.

Triggers on protocols (UART, I2C, SPI, CAN, LIN, I2S, FlexRay, 1553B) are set on the **Oscilloscope** tab: condition, address,
ID and data. Data fields take hexadecimal values, and I2S and 1553B data take a string of bits 0, 1 and X (in I2S at most
**Bits used**). The most significant bit is entered first. Bit fields are only sent, so a change on the scope itself does not
update them. Triggering was checked on a signal for UART, I2C, CAN and SPI. For LIN, I2S, FlexRay and 1553B it was only checked
that the instrument accepts and returns the settings. The source of SPI, I2S, FlexRay and 1553B cannot be D15 while the digital
channels are off.

While an operation is in progress, the view on the **Oscilloscope** tab waits for it to end.

The **Detailed (verbose)** option above the log adds timestamps, every SCPI command with its reply, the size, time and speed of
reads, and the number of edges per signal.

The **Export results to CSV** button exports the results. Waveforms can be saved and decoded later without the scope
(**Save waveforms**, **Open waveforms**).

The program warns about a clipped channel and about coupling other than DC.

An empty channel does not give false edges. The instrument's own noise was measured from 10 mV/div to 5 V/div: up to 0.11
division between the extreme values, so with the automatic threshold a signal below 0.2 division counts as a constant level. A
weaker signal, for example 3.3 V at 20 V/div, needs a manual threshold in the decoder settings. A BIN file opened from disk has
no scale, so only a fixed limit of 20 mV applies there.

## Memory read speed

The default format is BYTE in one block, the fastest way to get data out of this instrument. Times measured for 10 M points:

| Method | Time |
|---|---|
| BYTE, one block | 5.5 s |
| BYTE, 1 M chunks | 7.9 s |
| WORD, one block | 11 s |
| file on the scope and FTP | 13 s |
| ASCII | about 150 s (calculated from a 10 k point sample) |

100 M points in BYTE take about 55 s. BYTE is the upper byte of a WORD sample. WORD (on the **Read format** list) is useful
when a signal takes a small fraction of the range. The limit is the instrument's SCPI path, about 1.8 MB/s: FTP on the same link
gives 11.8 MB/s, and a larger socket buffer changes nothing.

## Program window

The layout follows logic analyzers (PulseView, Saleae Logic, DSView). The decoder panel takes these solutions from PulseView: a
tree with search and a description, independent decoders one under another, an asterisk at required signals, stacking, hiding
a decoder and single rows, and a decoder lane visible right after adding it. Not taken from PulseView: editing a lane's name and
color, dragging lanes into another order, pausing decoding, and the menu for copying and exporting annotations from a lane.
Settings open in a separate window rather than a popup.

- **Toolbar at the top:** the connection only, with the scope's status line in the row below it. Each tab has its own controls.
- **Waveforms tab:** **Scope settings**, **Run** (F5), and in its place while working **Abort** (Esc), the **Repeat** box, memory read
  set apart, zoom, the **Logic/Analog** view and **Cursors**. The shortcuts of these controls work on this tab.
- **Oscilloscope tab:** manual RUN, STOP, SINGLE and FORCE, and **Autoscale**, with **Abort** in its place while working.
- **Scope settings:** a menu on the Waveforms tab with channels CH1–CH4 and D0–D15, memory depth, time base, trigger, read format, forcing DC
  and waiting for a trigger. The scope receives them only at an **Acquisition**: it turns ticked channels on and unticked ones
  off. **Read memory** sends none of this, it only stops the scope and reads. "No change" leaves what is on the scope, also after
  a change on the Oscilloscope tab, which works at once. Next to the digital channels are the thresholds of groups D0–D7 and
  D8–D15: an acquisition sets a ticked threshold on a group in use, and an unticked one stays as it is on the scope.
- **Pretrigger:** in the Acquisition group you choose what percentage of the memory lies before the trigger. The scope has no
  command for this, so an acquisition sets the time base offset (`:TIMebase:MAIN:OFFSet`) calculated from the memory depth and the
  sample rate. The scope limits this offset to −5 divisions, so when the memory is longer than the screen a large pretrigger
  cannot be reached and the log says what came out. Unticked: the offset stays as it is on the scope.
- **Trigger line:** a red vertical "Trig" line runs through all rows at the moment of the trigger, separately for each acquisition
  with Repeat. In a forced acquisition it is the moment of forcing. WFM files and NPZ files with no saved trigger moment do not
  have this line.
- **Trigger mark on a channel:** next to the channel number, the row label shows "Trig" with the symbol of the condition or the
  name of the protocol the channel is a source of, for example `CH1 Trig I2C`.
- **Trigger in Scope settings:** the **Channels** tab holds conditions on single channels. The **Advanced** tab holds the trigger
  on a protocol (UART, I2C, SPI, CAN, LIN, I2S, FlexRay, 1553B) with sources on CH1–CH4 and D0–D15. A source on D0–D15 has no level
  of its own, as it switches at the threshold of its group. A protocol and conditions on channels replace each other, and an
  acquisition waits for such a trigger with no limit. Settings the scope refuses end the acquisition with an error. Levels in a
  pattern on D0–D15 cannot be set through SCPI: `:TRIGger:PATTern:PATTern` with more than 4 values gives -108, and 4 values act on
  CH1–CH4 (firmware 00.01.00). Such a pattern can be set by clicking the screen in the live view: an acquisition with no conditions
  leaves the trigger as it is.
- **Channels on the waveforms:** each ticked channel has its row. A channel with no level changes draws its constant level: its
  voltage in the Analog view, low or high in the Logic view. A channel asked for in a read with nothing in the scope's memory shows an
  error on its row. **Hide unused** unticks both kinds with one click, except those used by a decoder or the trigger, and a channel
  ticked again shows again. Opening a file selects the channels of that file.
- **Clicking a channel label:** a window like PulseView's channel popup, with the trigger condition (none, low, high, edges), the
  threshold and, in the Analog view, the vertical scale. D0–D15 have edges only, as the scope's pattern takes CH1–CH4 alone. CH1–CH4
  can be combined into a pattern with one rising or falling edge, and either edge works only on its own. An acquisition with a
  condition waits for the trigger with no limit. A threshold set on a channel works in the Logic view, in decoders with an automatic
  threshold and in the trigger. With no threshold, the trigger level is the middle of the signal from the last read, and with no read
  the level on the scope stays.
- **Waveforms tab:** the mouse wheel zooms, and dragging or the scroll bar under the waveforms moves along the time axis. The **Logic** view shows states after the threshold,
  and **Analog** shows voltages. Zoomed in far enough to tell the samples apart, **Analog** marks each one with a dot. Waveform rows are low, and decoder rows get the room. The vertical scale in the **Analog** view is fitted
  once, to the whole read, and does not change when the time scale does. Clicking the voltage axis lets you enter the top and bottom of
  the range or go back to the automatic scale, and an entered range stays for further reads. Cursors, as in PulseView, have flags A
  and B above the time axis and an arrow with the time and frequency between them, and the area between them is shaded. You drag the
  lines or the flags. Where the **Analog** view shows dots, a cursor dragged close to a sample is drawn to it, and between samples it moves freely. The status bar gives the positions of A and B. Annotation bars have the row's color for data and gray for the
  control fields of a protocol (start, stop, ACK, address). The table marks them the same way. Text that does not fit on a bar in any
  form is left out. Clicking an annotation bar selects its row in the table, and clicking a row shows it on the waveform.
- **Oscilloscope tab:** the scope's screen live, from the H.264 stream of the scope's web page, where the left mouse button acts like
  touch. The stream runs only while the tab is visible and the live view is ticked. The web page and this program push each other off
  this connection. Besides this: time base, memory depth, acquisition mode, channels CH1–CH4, digital channels and the trigger. Every
  change goes to the scope at once, and values are read continuously, so changes made on the scope itself show as well. A refused
  command appears on the status bar. **Holdoff** in the trigger settings takes 8 ns, which means none, to 10 s, and works only with the Slope
  trigger type, as the Programming Guide rules it out for the other types on the list.
- **Scope status line:** in its own row under the connection, on every tab, and read once a second: the state of the acquisition, the channels on, time base, sample rate,
  memory depth and the trigger. **Holdoff enabled** with its time appears there as long as a holdoff is in effect.
  The V/div list has 1-2-5 steps times the channel's probe ratio, from 1 mV × ratio to 10 V × ratio at the 1 MΩ input, and choosing from
  it turns off the channel's fine adjustment (`:CHANnel<n>:VERNier OFF`). The list shows a value between the steps set on the scope for
  as long as it is set. The time offset is shown with an SI prefix and can be entered that way (`1.5 ms`,
  `20 us`). Groups D0–D7 and D8–D15 turn the digital channels on and off themselves, with no separate box.
  The **Auto** depth shows in brackets the number of points the scope chose. The scope's reply gives a number of points in Auto too, so
  the program recognizes Auto by having set it itself, or by a number of points no fixed depth has. A fixed depth set on the scope
  itself after choosing Auto in the program still shows as Auto.
  The decoder panels are hidden on this tab.
- **Side panel:** the **Decoder** and **Generator** tabs. Decoders are in a tree, sorted by category or parent protocol. Drag a decoder
  onto the waveforms (or double-click it). Dropped on a decoder lane it fits, it goes onto that lane's stack, a decoder that needs a
  base goes onto the stack of the selected decoder if it fits, and any other gets a new lane under the previous ones. The **Fits the
  stack** group shows what can be added to the selected decoder.
- **Decoder lane menu:** the right mouse button on a lane gives **Decoder settings** and **Delete decoder**. The whole decoder is
  deleted, together with its stack.
- **Tooltips:** hovering over a button or a selection box shows what it does.
- **Decoder settings window:** opened by clicking the lane label or from the right-click menu. It has channels, options, a box to show
  or hide, a delete button, the **Rows** list and **Stack a decoder**. The rate of UART, CAN, CAN FD and Miller is chosen from a list of
  typical values, and **Custom…** opens a field for any other.
  The waveforms show channels first, with the lanes of further decoders under them. The lane labels of the second and later decoders
  have its number (`#2`).
- **Results at the bottom:** search, a filter by decoder row, and **Errors only**.
- **Menu:** **File** (opening and saving waveforms, CSV export), **View** (panels, light or dark **Theme**), **Advanced**
  (the window for receiving and comparing data, the **Log** in a separate window, Ctrl+L, and the detailed log).

The last message shows on the status bar, in red if it is an error. Clicking it opens the log.

## Digital channels D0–D15

A decoder signal can be assigned to a digital channel (this needs the logic probe). The program reads them through a BIN file saved on
the scope and downloaded over FTP, as `:WAVeform:SOURce` does not take D0–D15. Saving and downloading take about 7 s at 1 M points and
23 s at 10 M points (two analog channels and digital).

- **Threshold:** the default threshold is 0 V, and 0–3.3 V logic needs about 1.65 V. It is set on the Oscilloscope tab.
- **Sample density:** digital channels have 1/4 of the samples of an analog channel with one channel on, 1/2 with two, and the same
  number with four. The program reads this ratio from the file.
- **Combining with analog:** digital channels are delayed against analog ones by a few samples (measured for example 8 samples at
  50 MSa/s and 39 at 1 GSa/s). The delay is not constant over time, so the program does not correct it, and it warns in the log when a
  decoder combines both kinds of channel.

## Scope files

**File → Open waveforms** also reads `.bin` and `.wfm` files from the scope.

- **BIN:** voltages as 32-bit floating-point numbers, and digital channels. The size fields in the header are too large, but the data
  is complete: columns D0–D15 are identical to the CSV file.
- **WFM:** 16-bit analog samples only, with no digital channels and no sample rate, which has to be given. Voltages of CH1 and CH2 agree
  with BIN to 0.1 µV. Time counts from the middle of the record, so the time offset is not taken into account. CH3 and CH4 were not
  checked on a signal.

## Sigrok decoders

All decoders of the program come from sigrok. The search above the tree filters them by name and description, and the **Sort** list
arranges them by category or parent protocol: a decoder that needs another, for example `i2cdemux`, then lies under it (I²C).

The unchanged decoders are in `mhodecode/srd_decoders` (130 in use, the version from the repository of 2024-10-01).
The file `mhodecode/sigrokdecode.py` replaces the core written in C, so the decoders do not need sigrok to be installed.

**Decoder lane.** An added decoder has a lane on the time axis at once, also with no data. Rows appear when they have annotations, and
until then the lane is empty, with the decoder's name and anything missing (for example a channel) written on it.

**Stack.** A higher-level decoder reads the result of the decoder under it, as in PulseView. **Stack a decoder** on the card shows only
those that fit the output of the last decoder. Adding a decoder that needs a base with no fitting card builds the shortest stack by
itself, for example `i2c → eeprom24xx`. Removing a decoder from a stack also removes the decoders above it. Each card has its own stack,
channels and settings.

Each annotation row has its own lane on the plot. The **Row** list above the table shows one row, and by default all except bits.
Comparing (the **Advanced** menu) looks for a copy in the results of all decoders at once. It works for decoders that give binary data
(among others UART, I2C, SPI). From CAN the program reads data bytes from the annotation text, as this decoder gives no binary data.

Limits:

- The instrument has 4 analog channels. Decoders that need more do not give a meaningful result: `aud`, `lpc`, `mcs48`,
  `seven_segment`, `z80`.
- `ir_irmp` is not available, as it needs the native libirmp library.
- SPI with no CS line counts bits from the first edge of the record and does not synchronize, so a record started in the middle of a word
  gives shifted data.
- Decoders do not detect the rate by themselves, so it has to be entered (the `guess_bitrate` decoder helps). Their texts are in English.
- CAN reads one line where the dominant state is low. From a differential pair assign CAN_L, as the pair as such is not supported. The
  program no longer warns about too few samples per bit.
- The `0x` prefix and quotes are shown by UART (according to its **Data format** option: hex and ascii), I2C, SPI and I2S, and CAN has `0x`
  from the decoder. Other decoders give their texts unchanged. The program infers what is data and what is a control field from the names
  of annotation classes. A decoder with no data class is not split and has only the row's color.
- The initial level of a signal (Initial pin in PulseView) is handled in the core, but the window does not set it.

## Generator for other devices

The **Generator** tab sends a signal, and receiving and comparing are in the **Advanced** menu, in the **Receiving and comparing data** window.

1. **Generate** sends the data to the AFG outputs and leaves them on. You give the data as hex (`0x48 0x65` or `48 65`) or ASCII (`"Hello"`
   or `Hello`, also `\n \r \t \0 \\ \xNN`). Quoted text loses the quotes when sent, so write quotes as data double (`""a""`). You assign each
   protocol line to output AFG1 or AFG2 or skip it. For each output you set the low and high level and the load (High-Z or 50 Ω). The levels
   reach the output before it turns on, so a device does not see the amplitude from an earlier use.
2. **Receive** runs an acquisition with the channels and decoder from the **Decoder** tab and the settings from the **Oscilloscope** tab.
   The **Set the decoder and time base from the generator** button copies the protocol parameters and picks a time base. The channels and the
   trigger source stay as they are, because the app does not know what the outputs are connected to.
3. **Compare** looks in the decoded data for a complete copy with no errors: of the sent data or of what was entered as expected. On a mismatch
   it gives the first byte that differs. Comparing works for every decoder that gives binary data and for CAN, also when a device replies with
   another protocol.

**AFG waveform files** in the **Advanced** menu has the same form and needs no connection. It writes one CSV file per line in use, named
after the name you give and the line (`i2c_scl.csv`), and shows what to enter on the generator: load, file, frequency, amplitude, offset and,
for two lines, Align Phase.

The generator handles UART, I2C, SPI, CAN and CAN FD. I2S needs 3 lines, and the instrument has 2 outputs.

Limits of the AFG outputs:

- A record has 16384 samples and plays in a loop, as the programming guide does not describe a burst mode. The message repeats while the
  output is on. The program lowers the samples per bit by itself (16, 8, 4) so that the data fits.
- The highest sample rate of a record used is 80 MSa/s, checked on the scope.
- Longer CAN data goes in further frames of 8 bytes, and CAN FD of 64 bytes. The last CAN FD frame must have an allowed length (0–8, 12, 16,
  20, 24, 32, 48, 64).
- The program writes the files `mhodecode_afg1.csv` and `mhodecode_afg2.csv` in the scope's `C:/arb` folder (anonymous FTP).

## Development

```powershell
.\build.ps1
```

```powershell
.venv\Scripts\python.exe -m pytest tests -q
```

The tests below need AFG1 connected to CH1 and AFG2 to CH2:

```powershell
.venv\Scripts\python.exe tests\live_scope.py 192.0.2.1
```

```powershell
.venv\Scripts\python.exe tests\live_crosscheck.py 192.0.2.1
```

```powershell
.venv\Scripts\python.exe tests\live_sigrok.py 192.0.2.1
```

Agreement with the real libsigrokdecode is checked on its test cases, after cloning the repositories
[sigrok-test](https://github.com/sigrokproject/sigrok-test) and [sigrok-dumps](https://github.com/sigrokproject/sigrok-dumps):

```powershell
.venv\Scripts\python.exe tests\sigrok_conformance.py PATH\sigrok-test PATH\sigrok-dumps
```

Result: 574 of 594 cases. The others are `ir_irmp` (16, no libirmp) and `avr_isp` (4). In `avr_isp` the difference concerns only the part
names from the `parts.py` table, which the reference files leave out.

`main.py --selftest ADDRESS --output FOLDER` goes through all functions in the program's window, saves a log and screenshots, and ends with
code 0 only when all of them worked.

## Translations

The source strings in the code are in English. Polish is the catalogue `mhodecode/i18n_pl.py` (English source string -> Polish); a test
fails if a visible text has no entry there.

## License

The decoders in `mhodecode/srd_decoders` come from the [libsigrokdecode](https://github.com/sigrokproject/libsigrokdecode) project and have the
licenses GPLv2+, GPLv3+, BSD and MIT (the `license` field of each decoder, the GPLv3 text in `COPYING`, the authors in `AUTHORS`).
`mhodecode/sigrokdecode.py` reproduces the behavior of their core. The program distributed together with them, also as an exe, is subject to the
GNU GPL version 3 or later, and a recipient must have access to the source code.

The layout of the decoder panel follows the [PulseView](https://github.com/sigrokproject/pulseview) program (GPLv3). The PulseView code, written in
C++, is not part of this program.
