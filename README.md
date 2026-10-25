# Fiat Vox

Fiat Vox turns books into audiobooks on your Mac. Give it an EPUB, a PDF (scanned books are fine), or an RTF,
Word, ODT or text file, and it makes a chaptered `.m4b` audiobook read by the Kokoro voice model, then adds it to
Apple Books. Everything is spoken on your own Mac; nothing is uploaded except, if you choose, short passages sent
to an AI model to decide chapters and fix scanning errors.

It needs a Mac with Apple Silicon (M1 or later) and macOS 13 or later.

## Install

Open Terminal and paste:

```bash
curl -fsSL https://raw.githubusercontent.com/mstrudle/Fiatvox/main/install.sh | bash
```

The installer asks one question, then runs by itself for 10 to 20 minutes. It puts everything in `~/.fiatvox`
(about 3 GB): Fiat Vox, its own Python, the Kokoro voice model and its 28 English voices, ffmpeg, and its own
copies of Node.js and Pi. It never uses Homebrew and never touches any Python, Node or Pi you already have. Every
download is pinned to an exact version and checked against its SHA-256 checksum. The only change outside that
folder is one marked line in `~/.zprofile` and `~/.zshrc` so that a new Terminal window finds the `fiatvox`
command. Running the installer again repairs or updates the install.

## First run

Open a new Terminal window and type:

```bash
fiatvox
```

The first time, Fiat Vox asks how it should reach an AI model (see the next section). You can choose "No AI" and
change your mind later in Settings.

Then press **A** to add books. You can use the Mac's file picker, type a path, or drag files from Finder into the
window. For each book you choose a voice, where to save the audiobook, and whether to use the review stop. The
queue starts by itself.

| Key | What it does |
|---|---|
| A | Add books |
| S | Start (or resume) the queue |
| C | Cancel the current book; everything narrated so far is kept, and it resumes from there |
| Return | Review a book that has paused, or show a finished one in Finder |
| R | Remove a book from the queue |
| U, D | Move a book up or down |
| V | Voices: Return plays a five-second sample; D makes a voice the default |
| P | Fix a pronunciation |
| T | Settings |
| E | Error reports |
| Q | Quit (work in progress resumes next time) |

The queue is saved, so you can quit at any time; the Mac stays awake while Fiat Vox is working.

**Review stop.** If you turn it on for a book, Fiat Vox narrates the first chapter and pauses. You can play it,
continue with the rest, switch voice and redo it, add a pronunciation fix and redo it, send the AI a note about
what to fix ("chapter titles are read twice"), or cancel.

When a book finishes, Fiat Vox adds it to Apple Books, shows a notification, and writes a QC report (a short
markdown file) next to the audiobook: the chapter list with times, what was cut and why, what the AI changed, and
anything worth checking.

### From the command line

```bash
fiatvox make "~/Books/On War.epub" --voice bm_george --out ~/Audiobooks
fiatvox voices --play george
fiatvox doctor
```

`fiatvox --help` lists every command.

## AI options and what they cost

Fiat Vox uses an AI model for judgment calls: which pages or sections to cut (covers, contents, notes, index),
where chapters start and what they are called, and fixing words damaged by scanning. The AI only returns
decisions; Fiat Vox checks every one against fixed limits before using it, and undoes any round of changes that
makes the book worse. A typical book takes 10 to 50 short requests. Without AI, Fiat Vox uses simpler local rules.

| Option | Cost | Notes |
|---|---|---|
| **Claude Code** | Counts against your Claude Pro or Max plan | Fiat Vox runs your own installed and signed-in [Claude Code](https://claude.com/claude-code). Install it and sign in to it first; Fiat Vox does not install it. |
| **ChatGPT Plus or Pro, or GitHub Copilot, through Pi** | Included in that subscription | Sign in from Fiat Vox's setup screen; your browser opens to sign in. |
| **Claude Pro or Max through Pi** | Billed by Anthropic as extra usage, per token | Not part of your plan's allowance. To use a Claude plan without extra charges, choose Claude Code instead. |
| **An API key** (Anthropic, OpenAI, Google Gemini, OpenRouter, xAI) | Per token, billed by that provider | From a few cents for a clean EPUB to about a dollar for a long scanned book with a mid-range model. Keys are stored in your Mac's Keychain, never in files. |
| **No AI** | Free | Local rules only. Works well for clean EPUBs. |

Fiat Vox never signs in to Claude itself. Claude subscriptions are reached only through Claude Code or Pi.

## Voices

All 28 of Kokoro's English voices are available: 20 American and 8 British, female and male. Press **V** to hear
a five-second sample of each. The first letter of a voice's name sets its accent (`a` American, `b` British).

## Pronunciation

Fiat Vox reads numbers, Roman numerals, units and abbreviations the way a person would: "World War II" as "World
War Two", "Napoleon III" as "Napoleon the Third", "XVIII Airborne Corps" as "Eighteenth Airborne Corps", "500 lb"
as "500 pounds", "Lt. Col. Smith" as "Lieutenant Colonel Smith", and "U.S. Army" without a pause in the middle.
Chapter names in Apple Books stay exactly as printed.

When a word is still read wrongly, press **P**: type the word and how it should sound ("quasi", "kwah-zee"). The AI
proposes the sounds, Fiat Vox checks them, you hear the word before and after, and it is saved only if you
approve. Fixes are kept per accent, because American and British voices need different ones:

| File | What it holds |
|---|---|
| `pronunciation/american.tsv`, `pronunciation/british.tsv` (shipped) | Tested fixes, for example "combatant", "lieutenant" and "quasi" for British voices |
| `pronunciation/spelling.tsv` (shipped) | Spelling substitutions for every voice, for example `HIMARS` read as "High-mars" |
| `~/Library/Application Support/Fiat Vox/pronunciation/user-*.tsv` | Your own fixes; they win over the shipped ones |

Each line is `word<TAB>/phonemes/` (or `find<TAB>replace` in the spelling list), and lines starting with `#` are
comments.

## When something goes wrong

If a step fails, Fiat Vox asks the AI (when one is set up) what went wrong and tries one fix from a fixed list:
recognise a scanned PDF again, decide the chapters again, skip a step, cut a section, and so on. It tries at most
three times. The AI never changes Fiat Vox's code or files; it only picks from that list.

Every failure, including ones that were fixed, is written as a report in
`~/Library/Application Support/Fiat Vox/errors/`. Press **E** in the app or run `fiatvox errors` to see them.

`fiatvox doctor` checks every part of the install and repairs what it can.

## Update and uninstall

```bash
fiatvox update
fiatvox uninstall
```

`update` installs the latest release. `uninstall` removes `~/.fiatvox` (including Fiat Vox's own Node and Pi),
moves the data folder to the Trash so your pronunciation lists can still be recovered, removes the shell profile
lines, and deletes any API keys Fiat Vox stored. Your audiobooks are not touched.

## Where things are

| Folder | Contents |
|---|---|
| `~/.fiatvox` | The installed program and everything it needs |
| `~/Library/Application Support/Fiat Vox` | Settings, the queue, the narration cache, voice samples, your pronunciation lists, error reports, and each book's working files |
| `~/Audiobooks` | Finished audiobooks and their QC reports (you can choose another folder) |

Narration is cached piece by piece, so a book that was cancelled or interrupted resumes where it stopped, and
changing a pronunciation fix only re-records the paragraphs it affects.

## Licence and credits

Fiat Vox is free software under the [GNU Affero General Public License 3.0](LICENSE), because it uses PyMuPDF,
which is AGPL-licensed.

It is built on:

- [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M), the voice model, by hexgrad (Apache-2.0)
- [misaki](https://github.com/hexgrad/misaki), Kokoro's pronunciation engine (Apache-2.0)
- [espeak-ng](https://github.com/espeak-ng/espeak-ng), used by misaki for unfamiliar words (GPL-3.0)
- [PyMuPDF](https://pymupdf.readthedocs.io/) for PDFs (AGPL-3.0)
- [FFmpeg](https://ffmpeg.org/), in the static macOS build by [Martin Riedl](https://ffmpeg.martin-riedl.de/) (GPL)
- [Pi](https://pi.dev/) for AI sign-in (MIT), [Textual](https://textual.textualize.io/) for the interface (MIT),
  [uv](https://docs.astral.sh/uv/) for installing Python, and PyTorch, spaCy and NumPy
- Apple's Vision framework for reading scanned pages
