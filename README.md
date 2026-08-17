# Kids Phonics Engine

This project creates animated, narrated A-to-Z phonics lessons from the real
PNG, teacher voice, student voice, background, intro, music, thumbnail, and
reviewed object-video libraries already in the project. FFmpeg renders the
timeline without loading the whole video into Python memory.

## Production behavior

- Object PNGs and real teacher/student recordings are matched by exact
  normalized filename. Apple cannot absorb Airplane or Pineapple assets.
- Exactly one randomly balanced background is used throughout one lesson.
- Intros, thumbnails, two to four music tracks, objects, matching voice takes,
  and footage sources use weighted balanced selection. Less-used choices are
  preferred while every valid choice remains selectable.
- The lesson repeats A-to-Z only as needed to exceed eight minutes. An object
  is not reused for a letter until that letter's available objects are used.
- Each scene varies its font, contrasting text/frame colors, eyes, teaching
  badge, object placement, overlay shape/color, rain layout, and motion path.
- Three or four faint duplicates of the selected object fall from different
  upper lanes. Lightweight colored lines, circles, crosses, chevrons, or
  dashes move behind the main object and over the full-screen example clip.
- The foreground object has a soft black shadow; the alphabet has only a very
  light shadow. Teacher echo is applied to a small selected subset only.
- Music is crossfaded and ducked during speech. The final mix is raised by the
  configured master volume.
- Scenes render at stable 1920x1080. A single final pass exports H.264/AAC
  3840x2160 YouTube landscape 4K. Intel Quick Sync is attempted on supported
  machines, with an automatic CPU fallback.

## Exact provider video order

The lookup order is strict:

1. Pixabay child-safe landscape results whose metadata contains the complete
   exact object phrase.
2. Pexels landscape results whose public URL contains the complete exact
   object phrase.
3. A random reviewed clip only from
   `downloaded_videos/<LETTER>/<object>/`.
4. The selected object's own animated PNG.

The production match threshold is 100%. Non-contiguous or partial provider
metadata cannot pass. A provider clip is downloaded only into the active
`render_*` directory, rendered immediately, and deleted. Nothing is added to
`downloaded_videos`, and permanent manual clips are never modified. Duplicate
manual files assigned to different objects are rejected without deletion.

## Originality ledger

All channel labels share mirrored ledgers at:

```text
.phonics_work/content_history.json
.phonics_work/content_history.backup.json
```

The copy containing the most completed videos wins, so a corrupt or interrupted
write never silently resets the history. Paths are stored in a portable format
that works across Windows and Linux GitHub runners.

Every candidate is checked against every previously completed lesson. The
first A-to-Z round must differ by at least:

- 12 object positions;
- 21 object/teacher voice/student voice/teaching-treatment positions; and
- 18 visual-composition positions.

Cosmetic styling cannot replace the first two substantive gates. Exact content
and object routes are always rejected, and the engine tries up to 500
candidates before refusing a weak plan. A intentionally remains Apple-only
with the supplied real recordings.

## Local setup and use

Install Python 3.11 and place `ffmpeg` and `ffprobe` on `PATH`.

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set `PIXABAY_API_KEY` and `PEXELS_API_KEY` in `.env`. Credentials are never
written to logs, plans, manifests, or source code.

```powershell
python generate.py --validate
python generate.py --dry-run --seed 42
python generate.py --channel channel_1
```

Different channel labels still use the same global non-repetition history:

```powershell
python generate.py --channel channel_2
python generate.py --channel channel_3
```

Completed files use `outputs/phonics_A_to_Z_YYYYMMDD_HHMMSS.mp4`. Each run also
writes a log and an auditable plan/manifest under `logs/`. Failed and dry runs
do not consume a history combination.

## GitHub Actions: three exact-time publications every day

The included workflow publishes at **08:17**, **14:47**, and **19:38** in the
`Asia/Kolkata` timezone. Primary jobs start four hours early, at **04:17**,
**10:47**, and **15:38**, so the 4K render, upload, and YouTube processing finish
before the public slot. YouTube's `publishAt` scheduler releases the
already-uploaded private video at the exact target time. Recovery triggers run
one hour after each primary start; the durable slot receipt makes them skip
when the primary upload succeeded, while allowing a failed primary run to be
replaced.

Each production run generates one 4K lesson, commits the shared originality
history, uploads the MP4 and selected thumbnail, records the returned YouTube
video ID and publishing slot, and removes outputs and render cache from the
temporary runner. Network operations use bounded timeouts, resumable 2 MiB
chunks, exponential retries, and secure system certificate validation. Failed
run logs are retained for one day.

First, authorize the production OAuth client once on this computer:

```powershell
python -m phonics_engine.youtube_upload authorize --client-secrets client_secret.json --token-output youtube_token.json
python -m phonics_engine.youtube_upload check-auth --token youtube_token.json
```

The browser sign-in must use the Google account that owns the destination
YouTube channel. `client_secret.json` defines the production OAuth application;
`youtube_token.json` contains the separate refresh token required for
unattended uploads. The token deliberately requests only `youtube.upload`, so
the check verifies upload authorization without reading private channel
metadata. Both files are ignored by Git.

In the GitHub repository, open **Settings → Secrets and variables → Actions**.
Create these repository secrets:

- `PIXABAY_API_KEY` — the Pixabay API key.
- `PEXELS_API_KEY` — the Pexels API key.
- `YOUTUBE_TOKEN_JSON` — the complete contents of `youtube_token.json`.

Create these optional repository variables:

- `PRODUCTION_START_DATE` — optional scheduled-production override; use an India
  calendar date such as `2026-08-18`. This repository defaults to that date,
  and the variable lets you postpone scheduled production without editing code.
- `YOUTUBE_CATEGORY_ID` — `27` for Education (default).
- `YOUTUBE_CHANNEL_KEY` — a stable history label such as
  `phonics_channel_1`.

Also open **Settings → Actions → General → Workflow permissions**, select
**Read and write permissions**, and save. The default branch must allow the
workflow's history-only commits; otherwise the non-repetition ledger cannot be
made durable between temporary runners.

Uploads are resumable, receive the selected thumbnail, and are explicitly
marked made for kids. A mirrored upload receipt prevents a rerun from uploading
the same plan twice.

An OAuth consent screen marked **Production** is not by itself the YouTube API
upload audit. For API projects created after 28 July 2020, YouTube can force API
uploads to `private` until that separate audit is approved. The uploader records
and prints the actual privacy returned by YouTube, even when `public` was
requested.

Use a **private repository** because the project contains your proprietary
voice recordings, reviewed footage library, and generation strategy. Never
commit the OAuth token or API keys. Private repositories consume GitHub-hosted
Actions minutes; three 4K software renders per day will likely exceed the free
monthly allowance, so configure an Actions budget/payment method or attach a
self-hosted runner before production.

The media library exceeds normal Git file limits, so `.gitattributes` sends
images, audio, and video through Git LFS. Before the first push:

```powershell
git init
git branch -M main
git lfs install
git add .
git commit -m "Add phonics generation and upload automation"
git remote add origin https://github.com/YOUR_ACCOUNT/YOUR_PRIVATE_REPOSITORY.git
git push -u origin main
```

The reviewed `downloaded_videos` library and both content-history files must be
included. The unused unreviewed `assets/videos` folder stays ignored. Do not
force-add `.env`, `client_secret.json`, or `youtube_token.json`.

The workflow caches `.git/lfs` by the media-library hash. The first run and any
run after media changes download LFS objects; unchanged daily runs restore
them from the Actions cache before `git lfs pull`. This substantially reduces
repeated Git LFS bandwidth, but the account still needs enough LFS storage for
the initial media upload.

The workflow must exist on the repository's default branch for schedules to
run. Use the Actions page's **Run workflow** button with `private` for the first
controlled test. GitHub's trigger can start late during heavy load, but this
does not move the public YouTube time because rendering begins four hours early
and YouTube stores the exact publication timestamp.

## Cache cleanup

Normal generation removes provider clips and `render_*` after success or
failure unless `--keep-temporary-files` is used. CI additionally removes
outputs, logs, and temporary caches after the upload attempt while preserving
both originality and YouTube upload histories.
