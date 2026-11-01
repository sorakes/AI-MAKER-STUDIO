# AI Maker Studio

An AI commercial and video production studio: a node canvas (script, cast/product, image, video, transition, frame, approval and edit) where every person has their own workspace, plus an admin panel that controls accounts, groups, token budgets, API keys and models.

Images and videos are generated through the [OpenRouter](https://openrouter.ai) API, so no local GPU is needed.

The interface ships in **English** (default), **Português** and **Español**. Each person picks their language in the profile menu (or on the sign-in page), and the admin sets the default in Settings. The AI writes its notes, titles and suggestions in the language of the interface.

## Features

**Studio** (`/`, after signing in)
- Connected nodes: an image going into a video becomes its 1st frame, and a connected video continues from its last frame.
- Chrome-like tabs inside each area (Video Studio, Characters, Products), plus an **Inventory** to browse, bulk-delete and send files to any node.
- Each image/video/transition node picks a **model** among the ones the admin enabled. **Automatic** chooses the admin default that fits the connection (text→video, image→video, first + last frame, references).
- **Multi-reference video**: recommended models receive the frame, cast, product and camera drawing together with the text (tested model by model). The node warns when a model ignores references or when there are more references than it accepts.
- Camera direction drawn on the 1st frame (purple = camera route, other colors = elements), read by a vision model and written into the prompt.
- The technical prompt is written behind the scenes from the plain-language intent, the drawn camera, the reference images and the brand.
- Final edit with ffmpeg (cuts, editorial transitions, music/voice-over).
- A token gauge next to the profile shows how much of the monthly budget is used.

**Characters and Products**
- Character/product sheet (fixed description + attributes), references with notes and generation of 8 poses/views, spritesheet, and a library used by the **Cast / product** node so the same character or product looks identical in every shot.

**Admin panel** (`/admin`)
- **Users** and **Groups**: base permissions (which areas, generate image/video, AI assistants, upload/delete files, admin) and per-node permissions. Accounts in several groups get the sum of permissions and the most generous limit.
- **Token budget per group**: type a monthly amount in US$ and choose "each person gets this amount" or "split among members". The studio converts it into tokens (Settings: 1 US$ = N tokens). Users only ever see tokens.
- **Microsoft sign-in (Entra ID)** with automatic group assignment by email domain, Entra group, app role or tenant.
- **Keys & Models**: the OpenRouter key is **always stored encrypted** (Fernet, derived from `SECRET_KEY`); the browser only gets a mask (`sk-or…1234`). Models are added from the OpenRouter catalog with type, capabilities, durations and price detected automatically.
- **Branding** (name, logo, colors, pixel or rounded style), **Prompts** (every instruction sent to the AI, editable), **Logs** (with CSV export), **Files** and a **Dashboard**.

## Run with Docker

```bash
cp .env.example .env
# edit .env: SECRET_KEY (required in production), ADMIN_EMAIL, ADMIN_PASSWORD
python -c "import secrets; print(secrets.token_urlsafe(48))"   # generates a SECRET_KEY

docker compose up -d --build
```

Open `http://localhost:3000`, sign in with the admin account and paste your OpenRouter key in **Keys & Models**. One key covers text (prompts), vision (reading images), image and video.

Data (SQLite database + user files) lives in the `studio-data` volume (`/data` inside the container).

**Backup:** copy the volume. The database alone does not expose the keys: without the `SECRET_KEY` they cannot be decrypted. Keep the `SECRET_KEY` somewhere else.

## Run without Docker

Requires Python 3.12+ and `ffmpeg`/`ffprobe` on the PATH.

```bash
pip install -r requirements.txt
export SECRET_KEY=... ADMIN_EMAIL=admin@example.com ADMIN_PASSWORD=...
uvicorn app.main:app --port 3000
```

## Configuration

All settings come from environment variables (see `.env.example`):

| Variable | Default | What it does |
|---|---|---|
| `SECRET_KEY` | generated in the data folder | Encrypts the API keys stored in the database. Set it in production. |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | `admin@local` / random (printed in the log) | First administrator, created only if there is no admin yet. |
| `APP_NAME` | `AI Studio` | Name on the sign-in page and in the studio (can be changed in Branding). |
| `PUBLIC_URL` | `http://localhost:3000` | Public address (OpenRouter referer and Microsoft redirect). |
| `PORT` | `3000` | Port published on the host. |
| `COOKIE_SECURE` | `0` | `1` = session cookie only over HTTPS (use behind a TLS proxy). |
| `DATA_DIR` | `./data` (`/data` in Docker) | Database and user files. |
| `OPENROUTER_API_KEY` | — | Optional: key from the environment instead of the panel. |
| `SESSION_DAYS`, `MAX_UPLOAD_MB` | `14`, `200` | Session length and upload size limit. |

## Adding a language

UI texts are translated by `static/js/i18n.js` using dictionaries keyed by the original text:
- `static/js/i18n-en.js`: English.
- `static/js/i18n-es.js`: Spanish, keyed by the English text. Anything missing falls back to English.

To add a language, copy `i18n-es.js`, translate the values, register the code in `LANGS` inside `i18n.js` and add the `<script>` tag to the three HTML pages. Opening the app with `localStorage.i18n_debug = '1'` and running `I18N.missing()` in the console lists any text still untranslated.

## Microsoft sign-in (Entra ID)

1. In Azure: Entra ID → App registrations → New registration. As Redirect URI (Web) use `<public address>/api/auth/microsoft/callback`. The panel shows the exact address, ready to copy.
2. Create a client secret and fill in Tenant, Client ID and Client secret in the panel (Microsoft tab). The secret is stored encrypted, like the API keys.
3. To assign groups automatically, enable the groups claim in the token or use App roles, then add rules to each group: email domain, Entra group ID, app role or tenant. Automatic groups are recalculated on every sign-in; manually assigned groups stay.

The flow is OpenID Connect with PKCE, and the id_token is validated: RS256 signature against Microsoft's public keys, issuer, audience, expiry and nonce. Existing accounts are only linked by email when the Tenant is a fixed ID. In production use HTTPS and `COOKIE_SECURE=1`.

## Project layout

```
app/
  main.py         page routes (/login, /, /admin), startup, request language
  config.py       environment variables
  db.py           SQLite (users, sessions, providers, models, usage, jobs, canvas)
  security.py     scrypt (passwords), session tokens, Fernet (keys)
  auth.py         sign-in, session, current user, default limits
  groups.py       groups, permissions (base + nodes), automatic rules, tokens
  microsoft.py    Microsoft sign-in (OIDC + PKCE, id_token validation)
  usage.py        usage logs and limit checks
  storage.py      per-user isolated files (/files/<id>/...)
  providers.py    OpenRouter (encrypted keys, HTTP, catalog, pricing)
  registry.py     models: catalog → capabilities, reference limits, request body
  jobs.py         render queue (image/video steps, resumes after restart)
  llm.py          text/vision with the default model
  promptgen.py    technical prompt + camera path analysis
  transitions.py  A→B transition planning
  montage.py      final cut (ffmpeg)
  routers/        auth, studio, admin
static/           login.html, studio.html, admin.html, css, js (i18n*.js = translations)
```

## Security

- Passwords hashed with scrypt. The session is an HttpOnly, SameSite=Lax cookie. For HTTPS, set `COOKIE_SECURE=1`.
- Sign-in attempts are rate limited per IP + email.
- Each file only opens for its owner (or an admin). Paths with `..` and access to another user's folders are refused.
- API keys are never returned by the API; only the mask leaves the server.
- To expose it on the internet, put it behind a TLS proxy (Caddy, Traefik, Nginx).

## Credits

- Icons: [Pixelarticons](https://pixelarticons.com) by Gerrit Halfmann, MIT license (embedded in `static/js/icons.js`).
