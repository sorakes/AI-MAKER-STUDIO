# Contributing to AI Maker Studio

Thanks for helping. Every contribution counts, from a typo fix to a new node.

## Good first contributions

- **A new interface language.** Copy `static/js/i18n-es.js`, translate the values, register the code in `LANGS` inside `static/js/i18n.js` and add the `<script>` tag to the three HTML pages. Run the app with `localStorage.i18n_debug = '1'` and call `I18N.missing()` in the console to find untranslated text.
- **Model compatibility reports.** Tried a model from the OpenRouter catalog? Open a [model compatibility report](https://github.com/sorakes/AI-MAKER-STUDIO/issues/new?template=model_report.yml) with what worked, what didn't and the cost per clip.
- **Docs and examples.** Guides, example flows and short clips made with the studio.

Issues labeled [`good first issue`](https://github.com/sorakes/AI-MAKER-STUDIO/labels/good%20first%20issue) are a good place to look.

## Development setup

Requires Python 3.12+ and `ffmpeg`/`ffprobe` on the PATH.

```bash
git clone https://github.com/sorakes/AI-MAKER-STUDIO.git && cd AI-MAKER-STUDIO
pip install -r requirements-dev.txt
export SECRET_KEY=dev ADMIN_EMAIL=admin@example.com ADMIN_PASSWORD=dev
uvicorn app.main:app --port 3000 --reload
```

Run the tests before opening a pull request:

```bash
python -m pytest -q   # ~10 s, no network, no ffmpeg needed
```

Media, providers and Microsoft sign-in are mocked in the tests, so you don't need an OpenRouter key to run them.

## Pull requests

- Keep each pull request focused on one change.
- Add or update tests when you change behavior in `app/`.
- For UI or generation changes, include a screenshot or a short clip.
- Never commit API keys, `.env` files or anything from `data/`.

## Reporting security issues

Please don't open a public issue for vulnerabilities. See [SECURITY.md](SECURITY.md).
