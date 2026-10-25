# Fiat Vox: build plan

Status: DRAFT for approval. Nothing is built until you approve this file.

Fiat Vox turns EPUB and PDF books into chaptered `.m4b` audiobooks with the local Kokoro model, then imports them into Apple Books. macOS on Apple Silicon only. One command (`fiatvox`), a Textual terminal interface, a one-line installer, and AI sign-in through Pi.

The old pipeline in `~/audiobook-tools` is the reference. It is read-only; Fiat Vox ports its logic into a tested package and never imports or edits those files.

---

## 1. Architecture

### 1.1 The big picture

```
            +--------------------------- fiatvox (Textual TUI) ---------------------------+
            | Home | Add books | Voice picker | Queue | Progress | Review stop | Settings |
            +------------------------------------+---------------------------------------+
                                                 | JSON-lines events over a pipe
                                                 v
                                   fiatvox worker (one subprocess)
                                                 |
   source.epub / source.pdf                      v
        |            +------------------------------------------------------------+
        |            |  PREPARE                                                   |
        +----------> |  1 ingest      EPUB parse | PDF layout rebuild (+ OCR)     |
                     |  2 dossier     per section / per page summary             |
                     |  3 decisions   AI backend -> JSON  (or heuristics)        |
                     |  4 build       apply decisions -> Book model              |
                     |  5 clean       markup, URLs, hyphens, flags, replacements |
                     |  6 audit + AI OCR correction (guarded, logged)           |
                     |  7 review rounds (revert if worse)                       |
                     |  8 chapters    uniform names, flat list, stray labels    |
                     +------------------------------------------------------------+
                     |  NARRATE                                                   |
                     |  9 normalise   numerals, units, abbreviations             |
                     | 10 overrides   accent lists -> [word](/phonemes/)         |
                     | 11 Kokoro      per paragraph clip, cached on disk         |
                     | 12 silences    title and paragraph pauses as zero samples |
                     +------------------------------------------------------------+
                     |  FINISH                                                    |
                     | 13 assemble    ffmpeg: AAC, chapters, title, author, cover |
                     | 14 import      open -a Books;  notification;  QC report    |
                     +------------------------------------------------------------+
```

### 1.2 Key design decisions

1. **One internal book model instead of an intermediate EPUB.** The old pipeline passed EPUB files between scripts because the narrator needed an EPUB. Fiat Vox narrates directly, so both EPUB and PDF are read into one `Book` model (sections, each with a title and a list of paragraphs, plus metadata and cover), saved as JSON in the work folder. Every later stage reads and writes that model. Benefits: one cleanup path for both formats; the audit, AI correction and narration all work on the same paragraphs; the chunk cache keys on paragraph text. EPUB-specific markup cleanup (note references, page-break spans, superscripts) runs while parsing the EPUB, exactly as `epub_clean.py` does it now. The PDF layout code (`pdf2epub.py`) is ported to emit sections and paragraphs instead of writing an EPUB.
2. **The engine runs in a worker subprocess** (`fiatvox worker`), not inside the TUI process. PyTorch and Kokoro stay out of the interface, the interface never freezes, and cancel is a clean signal to one process. The worker reports progress as JSON lines. `fiatvox make` uses the same engine in the foreground, so the CLI and the TUI exercise identical code.
3. **The cache makes resume free.** Each paragraph's audio is stored under a key made from the exact text sent to Kokoro, the voice, the language code, the Kokoro version and the model revision. A cancelled or crashed book resumes by skipping everything already in the cache. Changing a voice or a pronunciation fix re-renders only the paragraphs whose key changed.
4. **One audio encode per book.** The worker streams the whole book's PCM (24 kHz mono) into a single ffmpeg process, which avoids AAC priming gaps that come from joining separately encoded files. Chapter start times come from exact sample counts, so markers land on the first sample of each spoken title.
5. **The AI never edits text.** It returns JSON decisions; Python validates and applies them, exactly as now. All safety checks are kept (see 4.3).

### 1.3 Folders

| What | Where | Why |
|---|---|---|
| Runtime (uv, Python 3.12, venv, Kokoro model, voices, ffmpeg, Node, Pi) | `~/.fiatvox/` | See open question Q1: a path without spaces avoids broken script shebangs. |
| Launcher | `~/.fiatvox/bin/fiatvox` | Added to `PATH` in `~/.zprofile` and `~/.zshrc`. |
| Work, cache, queue, settings, voice samples, user pronunciation lists, logs | `~/Library/Application Support/Fiat Vox/` | Required by item 8. Nothing is written beside the source. |
| Pi's own config and sign-in | `~/.fiatvox/pi-agent/` via `PI_CODING_AGENT_DIR` (verified to relocate `auth.json` and model store) | Never touches the user's own `~/.pi`. |
| API keys | macOS Keychain, service `Fiat Vox` | Never in plain files. |

Inside the data folder:

```
Fiat Vox/
  settings.json
  queue.json
  books/<book-id>/          book.json, dossier.json, decisions.json, ai-log.md, build.json, cover.jpg, state.json
  cache/audio/<k[:2]>/<k>.flac   one clip per paragraph (lossless, about a third the size of WAV)
  samples/<voice>.wav       voice samples
  pronunciation/            user-american.tsv, user-british.tsv, user-spelling.tsv
  logs/
```

### 1.4 Module layout

```
fiat-vox/
  install.sh                  one-line installer (also used by `fiatvox update`)
  pyproject.toml              Python 3.12, uv, entry point `fiatvox`
  uv.lock
  LICENSE                     AGPL-3.0
  README.md
  PLAN.md
  manifest/components.toml    pinned versions, URLs and SHA-256 of every downloaded component
  src/fiatvox/
    __init__.py
    cli.py                    `fiatvox` (TUI), make, worker, doctor, update, uninstall, setup, voices
    platform.py               refuse anything but macOS on arm64
    paths.py                  runtime and data folders
    settings.py               typed settings, saved as JSON
    model.py                  Book, Section, Paragraph dataclasses; JSON load/save
    ingest/
      epub.py                 port of epub_clean.Epub, markup rules, nav labels, dossier
      pdf.py                  port of pdf2epub layout rebuild, headers, footnotes, chapter placement
      ocr.py                  OCR triage and the OCR engine (see 2.4)
      cover.py                cover from EPUB manifest or first PDF page
    prepare/
      clean.py                text cleanup rules, flags, replacements (port of clean_text_segment)
      audit.py                OCR damage audit (port of compute_audit, paragraph_issues)
      decisions.py            prompts, dossier compaction, heuristics, validation of AI JSON
      ocrfix.py               AI OCR correction with length and similarity guards and change log
      review.py               self-review rounds, patch validation, revert-if-worse
      chapters.py             uniform names, one sequence, stray labels, flat list (port of narrator_prep)
      pipeline.py             runs prepare stages for one book, writes QC data
    narrate/
      normalise.py            numerals, units, abbreviations (port of spoken_fixes), table-tested
      pronounce.py            spelling list and accent phoneme lists; override insertion
      chunk.py                paragraphs -> Kokoro inputs; never splits inside an override
      kokoro_engine.py        KPipeline per accent, MPS, voice loading, synth one chunk
      cache.py                content-addressed clip cache
      silences.py             title and paragraph pauses as zero samples
      assemble.py             ffmpeg: AAC, FFMETADATA chapters, title, author, cover, .m4b
    ai/
      base.py                 Backend interface: complete_json(prompt, schema) -> dict
      pi_backend.py           Pi, non-interactive
      direct.py               Anthropic, OpenAI, Gemini, OpenRouter over HTTPS (httpx)
      none.py                 heuristics only
      keys.py                 Keychain via keyring
    finish/
      books.py                open -a Books
      notify.py               macOS notification via osascript
      report.py               markdown QC report
    voices.py                 English voice list, samples, afplay
    queue.py                  persistent queue, per-book settings and state
    worker.py                 runs the queue, emits JSON-line events, caffeinate
    doctor.py                 checks and repairs
    tui/
      app.py                  Textual app
      screens/                home, add_books, voices, queue, progress, review_stop, settings, setup, pronounce
      pickers.py              osascript file and folder pickers; drag-and-drop path parsing
  pronunciation/
    american.tsv              shipped phoneme overrides, American
    british.tsv               shipped phoneme overrides, British
    spelling.tsv              shipped spelling substitutions (pronounce.tsv format)
  tests/
    test_normalise.py         table-driven
    test_abbreviations.py     table-driven
    test_overrides.py         misaki on every shipped override; fails if markup survives
    test_chunking.py
    test_chapters.py
    test_metadata.py
    test_clean_audit.py
    test_ai_validation.py     safety checks with a mock backend
    test_integration.py       tiny generated EPUB -> .m4b; checks chapters, names, durations
    fixtures/
  .github/workflows/ci.yml, release.yml
```

---

## 2. Components and how each is pinned

Every downloaded component is pinned in `manifest/components.toml` with a URL and SHA-256. The installer refuses a file whose checksum does not match.

| Component | Version (to confirm at build time) | Source | Notes |
|---|---|---|---|
| uv | 0.13.1 | GitHub release tarball `uv-aarch64-apple-darwin.tar.gz` + `.sha256` | Extracted directly (no uv installer, no profile edits). `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR`, `UV_PYTHON_BIN_DIR`, `UV_TOOL_DIR`, `UV_PROJECT_ENVIRONMENT` all point inside `~/.fiatvox`. |
| Python | 3.12.x | uv-managed standalone build | `kokoro` 0.9.4 requires `>=3.10,<3.13`. |
| Python packages | `uv.lock` with hashes | PyPI | `kokoro==0.9.4`, `misaki[en]==0.9.4`, `torch`, `textual`, `pymupdf`, `pyobjc-framework-Vision`, `httpx`, `keyring`, `soundfile`, `numpy`. |
| spaCy `en_core_web_sm` | 3.8.0 wheel, sha256 `1932429d…0fb85` | GitHub release of spacy-models, pinned in the lock | Must be pre-installed: misaki otherwise tries to download it at run time. |
| espeak-ng | bundled in the `espeakng-loader` 0.2.4 arm64 wheel | PyPI | Verified: misaki's unknown-word fallback works with no system espeak-ng. |
| Kokoro model and voices | `hexgrad/Kokoro-82M` revision `f3ff3571791e39611d31c381e3a41a3af07b4987` | Hugging Face | `kokoro-v1_0.pth` (327 MB, sha256 `496dba11…ad1e4`), `config.json`, 28 English voice files. Loaded from local paths with `KModel(config=..., model=...)`; `HF_HUB_OFFLINE=1` at run time. |
| ffmpeg and ffprobe | 9.0.2 static arm64 (martin-riedl.de), versioned URLs, sha256 pinned | see 2.3 | Developer ID signed. No Homebrew. |
| Node.js | 24.22.0 LTS, `darwin-arm64` `.tar.xz` | nodejs.org; sha256 pinned in the manifest and cross-checked with `SHASUMS256.txt` | Only for Pi (needs Node 22.19 or later). |
| Pi | `@earendil-works/pi-coding-agent` 1.3.0 | npm, `npm ci --omit=dev --ignore-scripts` from a lockfile committed in this repo, run with the bundled Node into `~/.fiatvox/pi` | Never global npm. The older `@mariozechner/pi-coding-agent` is deprecated. |

### 2.1 Voices

Kokoro's own `VOICES.md` lists **28 English voices**: 20 American (`af_*`, `am_*`) and 8 British (`bf_*`, `bm_*`). The Studio's `SUPPORTED_VOICES` has 27: the same list without `af_nova`, and nothing extra. Fiat Vox offers all 28. The picker groups them by accent and gender and shows Kokoro's published quality grade for each (for example `af_heart` A, `bf_emma` B-).

Samples: about 5 seconds each, one fixed sentence per accent, generated at install (and regenerated by `doctor` if missing), cached in `samples/`, played with `afplay`.

### 2.2 Narration details

- `KPipeline(lang_code="a")` for `a*` voices and `"b"` for `b*` voices, one pipeline per accent, sharing one `KModel` loaded from local files. `KPipeline` never picks MPS by itself, so the model is moved to `mps` explicitly (CPU if MPS is unavailable), with `PYTORCH_ENABLE_MPS_FALLBACK=1` as insurance. Measured in research: about 14x real time on MPS, 6 to 9x on CPU, so a 10-hour book takes roughly 45 minutes.
- Input unit: one paragraph. Kokoro's English path splits a long paragraph into chunks under its token limit at punctuation; Fiat Vox only ever gives it single paragraphs with no newlines, so no override is ever split by Kokoro's newline splitter.
- Silences inserted as zero samples at 24 kHz: before a spoken title (default 1.5 s), after it (0.8 s), between paragraphs (0.4 s), at the end of a chapter (1.0 s). All four are settings.
- No reading-speed control (Apple Books has one).

### 2.3 ffmpeg

Chosen: the static arm64 build from martin-riedl.de, release 9.0.2, `ffmpeg.zip` and `ffprobe.zip` from versioned (immutable) URLs, each pinned by sha256, signed with a Developer ID. Its published codec and format lists include `aac_at` (Apple's AudioToolbox AAC encoder, better than ffmpeg's built-in `aac` at low bitrates), the `ipod` muxer and `ffmetadata`. ffprobe is used by the tests and the QC report to read back chapters and durations.

Fallback: the `imageio-ffmpeg` 0.6.0 wheel (ffmpeg 7.1, no ffprobe), installed by uv with a locked hash. Research produced a correct `.m4b` with it (chapters, tags, cover). `doctor` switches to it if the static build is missing or fails its self-test.

Encoding: `-c:a aac_at`, 64 kbps mono by default (a setting), `-f ipod`, `.m4b`, chapters from an FFMETADATA file, cover with `-disposition:v attached_pic`.

### 2.4 OCR for scanned PDFs

The old pipeline used `ocrmypdf` (Tesseract plus Ghostscript, both from Homebrew). Proposed replacement: **Apple's Vision framework** through `pyobjc-framework-Vision`, which is built into macOS and needs no download beyond a Python wheel.

How it fits the existing PDF logic: PyMuPDF renders each page that lacks text at 300 dpi, Vision returns recognised lines with bounding boxes, and Fiat Vox writes them back into a copy of the PDF as an invisible text layer at those positions. The ported `pdf2epub` layout code (headers, footers, footnotes by line height, two columns, chapter matching) then runs unchanged on that copy, just as it ran on `ocrmypdf` output. Rotated pages are detected by trying the four orientations on a low-resolution render when Vision's confidence is low.

Triage rules are kept: OCR when under half the pages have text; re-OCR when the text layer looks garbled (more than 20% of text pages with junk over 0.25); `--force-ocr` exists in `fiatvox make`.

Research check (one synthetic 300 dpi page): Vision 0.54 s per page with 0.948 character similarity; Tesseract 1.8 s with 0.942. Vision also kept accented letters and an en-dash range that Tesseract garbled. Not yet tested on real scans, two columns or skewed pages; that happens in phase 2 on your scanned book.

Fallback, also with no Homebrew: PyMuPDF already contains Tesseract inside MuPDF and needs only the `eng.traineddata` file (15 MB, pinned by commit and sha256, stored in `~/.fiatvox`). A setting chooses the engine; `fiatvox make --ocr tesseract` forces it. Ghostscript is not needed at all: PyMuPDF renders the pages, and Fiat Vox never writes a searchable PDF for the user.

Risk: superscript note markers are found from font size and baseline in the old code. OCR output has no font sizes, so on scanned pages footnotes and markers are found from line height in the bounding boxes (the old code already does this for footnote lines). Acceptance: the scanned PDF reads without page numbers, headers or footnotes at least as well as the old pipeline managed with `ocrmypdf`.

**Result: nothing in Fiat Vox needs Homebrew.**

---

## 3. AI providers

### 3.1 Interface

```python
class Backend(Protocol):
    name: str
    def available(self) -> tuple[bool, str]: ...          # ready?  reason if not
    def complete_json(self, prompt: str, *, purpose: str, timeout: int) -> dict: ...
```

Every call goes through one helper that strips code fences, extracts the JSON object, retries once with "reply with the JSON object only", and raises a clear error otherwise (the old `ask_claude` behaviour). Purposes: `decisions`, `ocr_fix`, `review`, `phonemes`.

### 3.1a Claude Code (added in Phase 2, approved)

Testing showed that Anthropic bills Claude Pro and Max sign-ins through Pi (Fiat Vox's and Feynman's alike) as
extra usage, per token. To run on a Claude plan, Fiat Vox can instead call the user's own installed and signed-in
Claude Code (`claude -p --output-format json --tools "" --strict-mcp-config --no-session-persistence`), as the old
pipeline did. Fiat Vox still implements no Claude login: the user installs Claude Code and signs in with
Anthropic's own login. The installer does not bundle Claude Code; the setup screen offers this option when it finds
`claude` installed, and lists it first. Any API key in the environment is removed for these calls so they count
against the plan.

### 3.2 Pi

Verified against Pi 1.3.0's own docs (`docs/cli.md`, `docs/json.md`) and Feynman's source. I will re-check `pi --help` on the pinned version before coding.

**Calls.** Fiat Vox runs its own copy:

```
~/.fiatvox/node/bin/node ~/.fiatvox/pi/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js \
    --mode json --no-session --no-tools -ne -ns -np -nc --no-mcp \
    --model <provider/model> --thinking off
```

- Environment: `PI_CODING_AGENT_DIR=~/.fiatvox/pi-agent`, `PI_SKIP_VERSION_CHECK=1`, `PI_TELEMETRY=0`.
- The prompt goes in on stdin; the working folder is an empty temporary folder.
- Output is JSONL. Fiat Vox splits on LF only, ignores streaming deltas, and takes the final text from the assistant's `message_end` record (content blocks of type text), then extracts the JSON object. `usage` from the same record feeds the cost line in the QC report.
- Readiness: `pi auth check --provider X --json` (exit 0 ready, 1 not ready, 2 invalid). Model list: `pi --list-models`, which only shows providers that have credentials.

**Sign-in from inside the app.** Pi's CLI only offers sign-in as `/login` inside its own interface; there is no `pi login` command and no login in RPC mode. Feynman solves this by calling Pi's SDK directly, and Fiat Vox does the same: a small Node helper shipped with Fiat Vox (`pi-login.mjs`, about 100 lines) calls `createModelRuntime(authPath)` and `modelRuntime.login(providerId, "oauth", interaction)`, and speaks JSON lines to the Textual setup screen:

- `{"event":"open_url", "url":...}`: Fiat Vox opens the browser with `open` and shows the URL.
- `{"event":"prompt", "message":...}`: Fiat Vox shows an input box (for copy-code or device-code flows) and sends the answer back.
- `{"event":"done"}` or `{"event":"error", ...}`.

Providers shown come from the SDK (`getProviders()` with OAuth support). In Pi 1.3.0 they are: Claude Pro/Max, ChatGPT Plus/Pro, GitHub Copilot, xAI (Grok), OpenRouter, Kimi Code and a few others. Google Gemini has no subscription sign-in in Pi 1.3.0; Gemini users use a direct API key (3.3). Pi API keys and local models (for example Ollama) are configured through the same helper, writing to Fiat Vox's isolated Pi folder.

The interface surface of this helper depends on Pi's SDK, which is newer than its CLI. It is pinned with Pi, and `doctor` runs a no-network self-test of it.

### 3.3 Direct API keys

Anthropic, OpenAI, Google Gemini and OpenRouter, called over HTTPS with `httpx` (no vendor SDKs, fewer dependencies). Keys are entered in the setup screen and stored in the Keychain with `keyring`. Each provider has a default model and a model field in settings.

### 3.4 No AI

The old heuristics: keep PDF pages with readable text; drop EPUB sections whose titles look like front or back matter; chapters from bookmarks or large headings; no OCR correction; no review rounds. Always available, and used automatically if an AI call fails, with a warning in the QC report.

### 3.5 Claude subscriptions

Fiat Vox does not implement claude.ai sign-in. The setup screen and README say plainly: "Claude Pro and Max subscriptions work only through Pi. Anthropic bills that use as extra usage, per token, not as part of your plan. If you want a fixed cost, use a ChatGPT or Copilot subscription through Pi, an API key, or no AI."

### 3.6 Costs shown to the user

The setup screen gives a rough per-book estimate based on the dossier and flagged paragraphs (tokens in and out), so a user on a metered option knows what a book will cost before it starts.

---

## 4. The prepare stages (ported)

### 4.1 What is ported, from where

| Old code | New module | Changes |
|---|---|---|
| `epub_clean.Epub`, `MARKUP_RULES`, `nav_labels`, `cmd_dossier` | `ingest/epub.py` | Produces the Book model. |
| `epub_clean.clean_text_segment`, `build_pron_rules`, `DEFAULT_PRONOUNCE` | `prepare/clean.py` | Same rules; flags as booleans. |
| `epub_clean.compute_audit`, `paragraph_issues`, `known_word` | `prepare/audit.py` | Same; uses `/usr/share/dict/words`. |
| `epub_clean.cmd_ai_fix`, `acceptable`, `word_diff` | `prepare/ocrfix.py` | Same guards: length within 15%, similarity at least 0.88; every change logged; cache by paragraph hash. |
| `pdf2epub.page_lines`, header and footer logic, `locate_chapters`, paragraph rebuild | `ingest/pdf.py` | Emits sections, not an EPUB. Module-level counters become a stats object. |
| `audiobook` prompts, compaction, heuristics, `apply_patch`, `build`, review loop | `prepare/decisions.py`, `review.py`, `pipeline.py` | Same prompts, generalised from "Claude Code" to "the AI". |
| `audiobook.find_debris`, `flag_contexts`, problem score | `prepare/review.py` | Same score: issues + 3 x debris + 5 x unmatched + 5 x tiny + 20 x validation. |
| `audiobook.write_report` | `finish/report.py` | Adds narration facts (voice, duration, chapters with times). |
| `narrator_prep` titles, labels, flat list | `prepare/chapters.py` | Operates on the model; no HTML. |
| `narrator_prep.spoken_fixes` and abbreviations | `narrate/normalise.py` | Rewritten as a tested module (5.1). |
| `narrator_prep.phoneme_fixes` | `narrate/pronounce.py` | Emits `[word](/ph/)` directly to Kokoro; no HTML span. |

### 4.2 Order of operations (lessons 7 and 8)

1. Clean, audit, OCR correction and review rounds work on plain text with no override markup.
2. Chapter names are fixed on the model's `title` fields, which feed the `.m4b` chapter list.
3. Only then, at narration time, a *spoken copy* of each paragraph and title is made: normalisation (numerals, units, abbreviations), the spelling list, then phoneme overrides. The spoken copy is never written back to the model, so the chapter names shown in Books never contain "Napoleon the Third" rewrites or override markup.

### 4.3 Safety checks kept

- OCR correction: length ratio 0.85 to 1.15; similarity at least 0.88; rejected changes logged.
- Review patches: replacements must match text that exists in the book and be at most 120 characters; page changes cannot cut more than 40% of kept pages; a chapter list cannot shrink by half.
- Review rounds: kept only if the problem score falls, at most 7% of words disappear, and validation does not get worse. Otherwise revert and stop. Default 3 rounds; a setting.
- If the AI proposes dropping every section, the drop list is ignored.

### 4.4 "Send back to the AI with a note"

The old `audiobook.py` has no `--note` option that I can find, so I will build it this way: the note is added to the review prompt as "The listener reports: ...", one review round runs with the normal safety checks, the book is rebuilt, and only paragraphs whose text changed are re-narrated (the cache keeps the rest). The QC report records the note and what changed. If you meant something else by the old `--note`, tell me.

---

## 5. Narration text rules

### 5.1 Normalisation (`narrate/normalise.py`)

A straight port of `spoken_fixes` with the same order of rules, rewritten as small named functions, each with a table of cases. Rules:

- `World War II`, `WWII`, `WW2` -> `World War Two`; `WWI` -> `World War One`.
- Regnal: listed names (`Napoleon III` -> `Napoleon the Third`), titled names (`King ... III`), names the book itself uses with a title (`Archbishop Makarios III` makes every later `Makarios III` regnal), and name suffixes (`John Smith III`).
- Things: `Phase I` -> `Phase One`, `Chapter IV` -> `Chapter Four`, `Minuteman III` -> `Minuteman Three`.
- Corps: `XVIII Airborne Corps` -> `Eighteenth Airborne Corps`; `I Corps` unchanged.
- Lone `I`: the pronoun unless clearly a numeral (`"The Road to War I discussed"` unchanged).
- Units after a number: `500 lb` -> `500 pounds`, `1 lb` -> `1 pound`, `10-ft` -> `10-foot`, `lbs` alone -> `pounds`.
- Abbreviations: initialisms lose periods (`U.S.` -> `US`); those that spell a common word are spaced (`H.E.` -> `H E`); ranks and titles expanded before a capitalised word (`Lt. Col. Smith` -> `Lieutenant Colonel Smith`); `Jr.`, `Sr.`, `No.`, `etc.`, `e.g.`, `i.e.`, `vs.`, `ca.`, `approx.`; middle initials lose the period; a period is kept only where the next word starts a sentence.

Tests: every regex in the old file gets positive and negative cases taken from its comments and behaviour, plus edge cases: end of paragraph, quotes and brackets after an abbreviation, `St.` as Saint versus Street at sentence end, `I` before `of/and/in`, mixed `Lt.Col.`, numbers with commas and decimals before units, `L` and `C` never treated as numerals alone.

### 5.2 Pronunciation lists

- `pronunciation/british.tsv`: combatant, combatants, noncombatant, noncombatants, quasi, lieutenant, lieutenants (your tested values).
- `pronunciation/american.tsv`: quasi `/kwˈɑzi/` only.
- `pronunciation/spelling.tsv`: shared spelling substitutions in the `pronounce.tsv` format (ships with the commented examples).
- User files in `Application Support/Fiat Vox/pronunciation/` are merged over the shipped ones (user wins for the same word).
- Override matching: whole words, any capitalisation, longest first, never inside an existing override.

### 5.3 Override integrity (lesson 6)

- The override is built as one string with no whitespace inside the brackets or slashes, and the paragraph has no newlines, so Kokoro's splitter cannot break it.
- `tests/test_overrides.py` runs misaki's G2P for both accents on a sentence containing every shipped override and fails if any of `[ ] ( ) /` survive into the phonemes, or if the expected phonemes are absent.
- The same check runs at the end of prepare on the whole book ("G2P dry run"), replacing the old Studio dry run; problems go in the QC report.

### 5.4 Pronunciation fix helper

1. The user types a word and a respelling ("quasi", "kwah-zee").
2. The AI backend proposes phonemes in misaki's symbol set for the book's accent. Without AI, the user can type phonemes directly; the helper shows misaki's current reading as a starting point.
3. Python checks every symbol is in Kokoro's vocabulary and runs misaki on "Say [word](/ph/) again." to confirm the phonemes pass through intact.
4. Kokoro speaks the sentence in the book's voice; `afplay` plays it; the user can also hear the current (wrong) reading for comparison.
5. Saved to the user list for that accent only on approval.

---

## 6. Interface

Textual. Screens:

1. **Home:** the queue at a glance, big actions: Add books, Start, Settings. A first-run banner if setup is not done.
2. **Add books:** native picker (`osascript` "choose file" with multiple selection, EPUB and PDF), typed path, or a path dragged into the terminal (the terminal pastes the quoted path; the field unquotes it). Output folder via native folder picker or typed path. Per book: voice, review stop on or off, title and author override.
3. **Voice picker:** voices grouped American and British, female and male; Enter plays the sample; shows the accent's pronunciation list.
4. **Queue:** reorder, remove, edit settings per book. Saved to `queue.json` on every change.
5. **Progress:** per book (stage, chapter n of m, percent) and overall, with time remaining from the measured narration speed on this Mac; Cancel (finish the current paragraph, then stop; cache kept).
6. **Review stop:** shown when a book with the toggle on finishes its first chapter: play chapter, continue, switch voice and redo, add a pronunciation fix and redo, send back to the AI with a note, cancel.
7. **Settings:** AI provider and model, reopen setup, silence lengths, review stop default, review rounds, QC report on or off, Books import on or off, bitrate.
8. **Setup:** first launch. Choose: subscription through Pi, API key, local model through Pi, or no AI. Runs the sign-in inside the app. Plain statement about Claude subscriptions and costs.
9. **Pronunciation helper:** as in 5.4.

"First chapter" for the review stop means the first section that is a numbered chapter, or, if none, the first section with at least 300 words, so the listener does not review a title page.

Keeping the Mac awake: the worker runs under `caffeinate -i -w <worker pid>` while there is work.

---

## 7. Installer and maintenance commands

`curl -fsSL https://raw.githubusercontent.com/<owner>/fiatvox/main/install.sh | bash`

1. Refuse anything but macOS on `arm64` with a clear message.
2. Print one summary: what will be installed, where (`~/.fiatvox` and the data folder), about 3 GB of disk, and the shell profile lines it will add. One yes/no prompt (read from `/dev/tty`, since stdin is the pipe).
3. Then unattended, with numbered progress lines:
   1. uv (pinned, checksum-verified);
   2. Python 3.12 through uv; the Fiat Vox package and locked dependencies into `~/.fiatvox/venv`;
   3. ffmpeg (2.3);
   4. Node 24.22.0 (sha256 pinned in the manifest);
   5. Pi 1.3.0: `npm ci --omit=dev --ignore-scripts` with that Node, from the `package-lock.json` committed in this repo, into `~/.fiatvox/pi` (about 150 MB);
   6. Kokoro model and voices from Hugging Face at the pinned revision, checksums verified; spaCy model; Tesseract `eng.traineddata` (fallback OCR);
   7. voice samples;
   8. launcher and `PATH` lines in `~/.zprofile` and `~/.zshrc`, inside marked blocks (`# >>> fiatvox >>>`) so re-runs and uninstall can find them;
   9. `fiatvox doctor --quiet` as a final check.
4. Safe to re-run: each step checks what is present and its checksum, repairs or skips.

`fiatvox doctor`: checks platform, every component and checksum, MPS availability, misaki and spaCy, the model and every voice, samples, ffmpeg capabilities, Pi and its sign-in state, Keychain access, data folder permissions, `PATH`; repairs what it can by re-running the matching installer step.

`fiatvox update`: downloads the installer for the latest tagged release, verifies it against the release's checksum file, and runs it (the same repair path).

`fiatvox uninstall`: one confirmation listing what will go: `~/.fiatvox` (including its Node and Pi), the data folder (with a separate yes/no to keep finished-book work and user pronunciation lists), the profile blocks, and Fiat Vox Keychain items. Finished `.m4b` files and books in Apple Books are never touched.

---

## 8. Phases and acceptance checks

### Phase 1: Plan
- [ ] You approve this file (with any changes).

### Phase 2: Core engine, no interface
- [ ] `uv` project, Python 3.12, locked dependencies; `kokoro==0.9.4` and `misaki==0.9.4` load and narrate on MPS.
- [ ] EPUB and PDF ingest to the Book model, including OCR of a scanned PDF.
- [ ] Prepare stages with Pi, a direct key, and no AI; decisions, OCR correction and review rounds with all safety checks.
- [ ] Normalisation, pronunciation lists, overrides.
- [ ] Narration with silences; chunk cache; resume after `Ctrl+C`.
- [ ] `.m4b` with AAC, chapters, title, author and cover; imported into Books; QC report.
- [ ] `fiatvox make BOOK --voice X --out DIR` works end to end on one EPUB and one scanned PDF.
- Check with you: listen to one chapter of each.

### Phase 3: Tests
- [ ] Unit tests: normalisation, abbreviations, overrides (misaki), chunking, chapter naming, metadata, cleanup and audit, AI validation with a mock backend.
- [ ] Integration test: generate a tiny 3-chapter EPUB, narrate it, check the `.m4b` has 3 chapters with the right names and durations within 50 ms of the expected sample counts.
- [ ] Run them and show you the results.

### Phase 4: Terminal interface
- [ ] All screens in section 6 working against the worker.
- [ ] Queue survives a restart; cancel and resume work from the interface.
- [ ] Review stop with all six actions.
- [ ] Pronunciation helper end to end.

### Phase 5: Installer and documentation
- [ ] `install.sh`, setup screen, `doctor`, `update`, `uninstall`, README (install, first run, AI options and costs, pronunciation lists, licence and credits).
- [ ] Tested on a clean macOS user account with none of the dependencies (see Q6).

### Phase 6: Release
- [ ] Local git repository, a commit at each phase.
- [ ] GitHub Actions on a macOS arm64 runner: tests on every push (CPU, since hosted runners have no MPS); tagged releases publish the package, the installer and a checksum file.
- [ ] Remote added when you give it to me.

### Final acceptance (with you on this Mac)
As in the brief: fresh install on an account without Node, Pi, Python 3.12 or Homebrew; two books from two folders to a third, one American and one British voice, review stop on one; both in Books with correct chapters and correct speech; cancel, restart, resume.

---

## 8a. Deferred: error reports to GitHub (decided, not scheduled)

Every error report (see `troubleshoot.py`) should eventually reach the project's GitHub issues, so failures feed
back into the code. Decided approach, to build later:

- A small relay service holds the only GitHub credential and creates the issues. Fiat Vox never carries a token.
- The relay accepts only a fixed, pre-formatted message and rejects anything else: a JSON schema with closed
  fields (Fiat Vox version, macOS version, file type, size bucket, failed step, error type, a fingerprint, a
  traceback limited to Fiat Vox's own source files, the AI's diagnosis and remedy, the outcome), each with a
  length limit and allowed characters. No free text from the user, no book text, no paths outside `~`.
- One issue per fingerprint (error type plus the failing line in Fiat Vox's code); repeats become comments.
- Issues carry the `error-report` label. Sending is a choice the user makes in setup.
- Needs the GitHub repository (Q7) and a host for the relay.

## 9. Decisions (formerly open questions)

- **Q1 Runtime folder:** `~/.fiatvox` for the runtime; `~/Library/Application Support/Fiat Vox/` for data. Approved.
- **Q2 OCR:** Apple Vision first, PyMuPDF's built-in Tesseract as fallback; no Homebrew anywhere. Approved.
- **Q3 Book model instead of an intermediate EPUB.** Approved.
- **Q4 Voices:** all 28 English voices. Approved.
- **Q5 `--note`:** the review stop's "send to the AI with a note" runs one review round led by the note. Approved.
- **Q6 Clean-account installer test:** installer tested from scratch into a separate folder (Phase 5). The test on
  a fresh macOS user account still needs that account, which needs an administrator password.
- **Q7 Repository:** github.com/mstrudle/Fiatvox. It must be public before release, since the one-line installer
  downloads from it without signing in.
