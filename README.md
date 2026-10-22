# MimetiCraft

**Turn Minecraft building videos into approximate Minecraft Java Data Packs.**

MimetiCraft samples a construction video, filters redundant frames locally, asks a
vision model to describe the building as a voxel scene, and compiles it into a
single ZIP. No task-specific training or local GPU is required. Use **Sign in
with ChatGPT** with saved account sessions, or an optional OpenAI API key.

> This is a reconstruction prototype, not a block-perfect converter. Real-account
> inference and ZIP generation have been tested locally. In-game behavior and
> reconstruction accuracy have not been verified in a Minecraft client.

## Features

- Windows desktop interface built with Tkinter.
- Optimized ChatGPT account-session persistence, account switching and model discovery using OS credential storage.
- Streamlined desktop controls with saved-account selection and fewer status-only UI elements.
- Improved recognition and validated placement for glass, stained glass, water, Light blocks and Barriers.
- Optional API-key mode; no automatic fallback to separately billed API usage.
- **500 target frames by default**, adjustable from **2 to 1200**.
- Local CPU filtering: sample `ceil(target * 1.1)` candidates, then remove the
  least-novel neighboring images until the target count remains.
- First/last decoded frames and chronological order are preserved. Short videos
  use available readable samples instead of duplicating frames.
- One artifact per run: **the Data Pack ZIP**. Intermediate scene data stays in memory.
- Copyable Minecraft commands, output paths and model-error diagnostics.
- At most one automatic retry for transient errors before any model output arrives.
- Offline compilation and an included demo scene for testing without AI access.

## Quick start on Windows

1. Install Python 3.13 or another supported version from
   [python.org](https://www.python.org/downloads/windows/), including the Windows
   `py` launcher and Tkinter support.
2. Clone this repository or download and extract its source ZIP:

   ```powershell
   git clone https://github.com/silhovette/MimetiCraft.git
   cd MimetiCraft
   ```

3. Double-click **START_WINDOWS.bat**. It creates a project-local `.venv`, installs
   dependencies there, and opens the app. Administrator access is not needed.
4. Keep **Use ChatGPT plan** selected. Click **Sign in with ChatGPT**, choose your
   account in the browser, and approve the requested permissions.
5. The authorization is saved locally: the access/refresh credentials stay in the
   Windows credential vault and account metadata stays in your user profile. After
   the first authorization, select any previously saved account from **Saved
   accounts** to switch without opening the browser again. Use **Add account** only
   when adding a new account, then select it from the saved-account
   list later.
6. Click **Refresh current account models** when using a saved session. Select an image-capable
   model listed for your account.
7. Select a video with **Browse**, choose an output ZIP with **Save as**, and set
   **Sample frames**. Start with 500; reduce the count if requests fail.
8. Click **Generate Minecraft Data Pack ZIP** and wait for the success window.

Use a different ZIP filename for each video to keep previous results. Commands in
both the main and success windows support text selection and **Copy** buttons.

### Manual installation

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

To preselect a video and destination:

```powershell
.\.venv\Scripts\python.exe app.py --video "C:\Videos\house.mp4" --out outputs\house.zip
```

The launcher prefers Python 3.13/3.12 and also checks 3.11, 3.14 and 3.10.
Windows Python 3.11 has been tested locally; the other versions have not been
validated in this environment. Dependencies are installed only inside `.venv`.

## Use the Data Pack

The compiler targets **Minecraft Java 26.3**, using data pack format `[121, 0]`.

1. Back up the world. Use an empty creative/superflat test world first.
2. Put the generated ZIP in `<game directory>/saves/<world>/datapacks/`. Keep it
   zipped, with `pack.mcmeta` at its root. Do not install the repository's source
   ZIP as a Data Pack.
3. Enable commands and run:

   ```mcfunction
   /reload
   /function vidbuild:check
   /function vidbuild:build
   ```

4. To remove the tracked building, stay near it so its chunks are loaded and run:

   ```mcfunction
   /function vidbuild:undo
   ```

**Undo removes matching generated blocks; it does not restore overwritten terrain.**
The origin and original dimension are recorded so moving the player does not
change the undo target. Only the three vanilla dimensions are supported.

All generated packs currently use the **vidbuild** namespace. Only one should be
enabled in a world at a time, and only one pending building is tracked. Undo the
old building before replacing its ZIP, then reload the world and build again.
The namespace remains unchanged for compatibility with earlier project versions.

## Command-line usage

After authorizing the app through the GUI:

```powershell
.\.venv\Scripts\python.exe video2pack.py --video "C:\Videos\house.mp4" --out outputs\house.zip --auth chatgpt --model gpt-6.1-sol --frames 500
```

Use a model listed for your account. Without `--model`, ChatGPT mode uses the
first model returned by that account's catalog.

Optional API-key mode uses separately billed API credits:

```powershell
$env:OPENAI_API_KEY="your-api-key"
.\.venv\Scripts\python.exe video2pack.py --video "C:\Videos\house.mp4" --out outputs\house.zip --auth api --model gpt-6.1-sol
```

The GUI does not persist an API key entered in its key field. Never commit keys.

### Offline demo

```powershell
.\.venv\Scripts\python.exe video2pack.py --scene examples/demo_house.json --out outputs\demo_house.zip
```

This needs no sign-in, video or model request. Existing scene JSON files remain
valid offline inputs; compilation writes only the ZIP.

## How reconstruction works

1. **Sample:** select distinct timestamps with slightly more coverage toward later
   construction stages. Sample 10% extra candidates, rounded up and capped by the
   video's frame count.
2. **Filter locally:** compare small color thumbnails using overall and local
   differences, with sharpness as a tie-breaker. Delete the least-novel interior
   frames while preserving endpoints and order.
3. **Infer:** send retained JPEGs and instructions to the selected vision model.
   Frames are resized to a maximum dimension of 1280 pixels. The raw video is not
   uploaded.
4. **Validate:** check dimensions, block names, operation bounds and command budgets.
   Validation does not measure resemblance to the video.
5. **Compile:** create build/check/undo functions in one vanilla Data Pack ZIP.

A target of 500 samples 550 candidates and keeps 500; 1200 samples 1320 and keeps
1200. This CPU-only appearance heuristic makes no AI calls. It cannot establish
semantic novelty or compensate for camera motion. If all candidates differ, the
least-different ones are still removed. Short or partially unreadable videos may
produce fewer retained frames, reported in the console.

## Limits and troubleshooting

### Special blocks and Building Hints

The palette includes Air, Light, Barrier, Structure Void, Glass, Glass Pane,
Water, and all 16 colors of stained glass/panes. State validation is block-specific:
Light accepts `level=0..15` and `waterlogged`; Water accepts `level=0..15`;
panes accept cardinal connection booleans and `waterlogged`. Invalid properties
such as `glass[waterlogged=true]` or `stone[level=8]` are rejected, not dropped.

Video inference considers placement changes, held items/hotbar icons, text,
edges/thickness, multiple views, water motion/immersion and visible collisions.
Brightness or transparency alone is insufficient evidence for invisible blocks.
Each inferred operation records `action`, `confidence`, `evidence` and `replace`:

- `place`: a confirmed block placement, including a real `structure_void` block.
- `clear`: deliberate `minecraft:air` placement; unknown space is not air.
- `preserve`: leave the world unchanged, not a Structure Void placement.
- `unknown` or `confidence=uncertain`: leave the region untouched.

The model schema separates placement/clear operations from nullable unknown/
preserve operations. If an older or non-conforming result still requests placement
with `block=null`, that region is downgraded to unknown and excluded from all
placements and undo. It is never substituted with Air. The success window reports
the count of unknown/preserved regions left untouched. Missing block fields and
non-string/non-null values are rejected with the operation index.

Preserved/uncertain regions are excluded from all broad overlapping fills and undo
commands. A conflicting later overwrite of a special block requires `replace=true`.
The compiler resolves final occupancy before sorting construction phases, so a
later fluid pass cannot accidentally overwrite a Light or pane in the same cell.
Different state strings are never merged into one uniform fill. Existing explicit
offline block operations remain supported as manually specified placements/clears.

Optional building hints can be passed with `--building-hints "Place a level-8 Light
at the indicated location"` in the CLI. Hints stay separate from video evidence. Inferred frame references must
refer to supplied frame IDs; hint quotations must match supplied hints. This checks
provenance consistency, not whether a model's interpretation is actually correct.

Placement uses the target version's `strict` command mode to avoid intermediate
neighbor/shape updates. Missing pane connections are computed from the final
planned geometry; explicit connection states are retained. Existing neighbors
outside the plan cannot be inferred statically. Walls/floors are built before
water and waterlogged hosts. Known open water boundaries are rejected. Boundaries
that rely on the existing world get a runtime check for loaded chunks and a
conservative tag of full solid seals before any placement or undo record is
created. If that check fails, build stops without changing blocks. Unlisted but
potentially watertight existing blocks can conservatively fail this check.

Undo removes water/waterlogged hosts before container walls and uses strict,
block-type-filtered removal at the saved origin/dimension. It excludes preserved
regions and clears the history after a loaded-chunk undo. It does not restore
terrain. Strict placement does not disable later game updates: modifying a
container can still cause normal fluid flow and pane/state updates.

Offline special-block example (no model request):

```powershell
.\.venv\Scripts\python.exe video2pack.py --scene examples/special_blocks.json --out outputs\special_blocks.zip
```

Air, Light, Barrier and Structure Void cannot be reliably distinguished from final
appearance alone. The new rules reduce unsupported guesses; they do not provide
block-perfect recognition. Minecraft-client lighting, collision and subsequent
fluid ticks still require in-game acceptance testing.

### General limits

- Scenes are limited to **64 x 48 x 64**, with at most **500 operations**. The model
  prompt currently favors approximately 20-250 operations where useful.
- Blocks come from a conservative vanilla palette. Hidden geometry, shader
  lighting, textures, materials and dimensions can be misinterpreted.
- More frames do not guarantee a better result. All retained frames are sent in a
  single request; model context, image-input and request-size limits still apply.
- A local 500-frame request completed successfully with 91 build commands. A
  1000-frame request for the same video/model returned `server_error` after output
  began. This does not establish a universal safe count or a precise failure cause.
- Error details include the model, actual submitted frame count, HTTP status when
  available, error code and request ID. Use **Copy error details** to report an
  error. Do not include credentials or personal videos.
- Transient errors can be retried once before output starts, using the same input.
  Partial streams and ChatGPT allowance errors are not automatically replayed.
  There is no silent frame reduction, model switch or paid-API fallback.
- The interface shows processing stages, not percentage-complete estimates.
- A ChatGPT plan does not guarantee access to every model or feature. Account
  allowances and availability still apply.
- If an environment is broken, recreate only this project's `.venv`. Do not modify
  global Python packages or run the launcher as Administrator.

## Authentication and privacy

Browser login uses OpenAI's documented dynamic-client OAuth flow with a loopback
callback, PKCE, state, nonce, verified ID tokens and granted-scope checks. New
registrations use the name **MimetiCraft**. Existing registrations remain usable.

Access, refresh and ID tokens are stored through the OS credential backend
(`keyring`; Windows Credential Manager on Windows). Large records are split into
small credential entries to respect Windows per-entry limits. Refresh replacements
are committed before older chunks are retired.

For compatibility, this release retains the legacy credential service
`video2datapack_siwc_v03` and metadata location
`~/.video2datapack/registrations.json`. The metadata includes registration IDs and
basic account information, not access/refresh tokens. Neither the metadata nor OS
credentials belong in this repository.

ChatGPT inference uses the public Responses API with `store=false` and
`stream=true`. The app does not read browser cookies or Codex credentials. Scene
data stays in memory. The generation workflow writes no additional scene, summary
or diagnostic files.

## Development

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
```

The **43 automated tests** cover mocked OAuth registration/refresh, large credential
records, failed-write recovery, streamed inference and bounded retries, single-ZIP
output, compiler structure, tracked undo, real MP4 decoding and local frame
selection, special-block semantics/states, preserve masks, intentional overrides,
pane connections and water preflight. Tests make no model requests and need no
account or API key.

Additional local checks exercised Windows credential persistence, GUI copying and
real-account inference. They do not replace in-game acceptance testing.

```text
app.py                        Desktop interface
chatgpt_auth.py                OAuth and OS credential storage
video2pack.py                  Sampling, inference, validation and ZIP compiler
block_rules.py                 Block semantics, state rules and final-occupancy planning
START_WINDOWS.bat             Windows environment setup and launcher
requirements.txt              Python dependencies
examples/demo_house.json      Offline demo scene
examples/special_blocks.json  Special blocks and sealed-water example
tests/                        Automated tests
```

Local environments, generated packs, videos, screenshots and run diagnostics are
excluded by `.gitignore`. Install dependencies locally rather than committing a
virtual environment.

## References

- [ChatGPT registration and sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [ChatGPT models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [OpenAI image input requirements](https://developers.openai.com/api/docs/guides/images-vision)
- [OpenAI API errors](https://developers.openai.com/api/docs/guides/error-codes)
- [Java strict placement command semantics](https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-5)
