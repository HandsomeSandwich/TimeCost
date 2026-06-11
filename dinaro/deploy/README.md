# Splitting Dinaro into its own repo & deploy

Phases A–D made `dinaro/` a self-contained, standalone-capable Flask app living
inside the TimeCost monorepo. This is the **Phase E checklist** to lift it out
into its own repository and deploy it independently. Nothing here runs
automatically — it's the manual cutover.

## Dependency surface (what Dinaro needs)

- **Python packages:** Flask, gunicorn, SQLAlchemy, psycopg2-binary (Postgres),
  pywebpush. See `requirements.txt` in this folder.
- **Vendored, no external coupling:** `dinaro/kernel.py` holds Dinaro's own copy
  of `safe_float`, PIN hashing, and `utc_now_iso` — it does **not** import the
  TimeCost `core` package.
- **Shared static assets it references via `url_for('static', ...)`:**
  `favicon.svg`, `manifest.json`, `sw.js`, `dinaro-push.js` (currently in the
  monorepo's top-level `static/`). These must travel with Dinaro — see step 3.

## Target repo layout

```
dinaro-app/                 ← new repo root
├── Dockerfile              ← from dinaro/deploy/
├── fly.toml                ← from dinaro/deploy/
├── requirements.txt        ← from dinaro/deploy/
├── .dockerignore           ← from dinaro/deploy/
├── README.md
├── static/                 ← the 4 shared assets (step 3)
│   ├── favicon.svg
│   ├── manifest.json
│   ├── sw.js
│   └── dinaro-push.js
└── dinaro/                 ← the package, copied as-is (keep the folder name!)
    ├── __init__.py
    ├── wsgi.py             ← entrypoint: gunicorn dinaro.wsgi:app
    ├── routes.py
    ├── db.py
    ├── kernel.py
    ├── push.py
    ├── static/             ← dinaro.css (served at /assets)
    └── templates/
```

> Keep the `dinaro/` package folder — the entrypoint is `dinaro.wsgi:app` and
> all imports are `from dinaro.x import ...`. `wsgi.py` points the app's
> `static/` at `../static` (the repo root), which is why the 4 shared assets go
> in a top-level `static/`.

## Steps

1. **Create the repo** and copy the `dinaro/` package into it unchanged.
2. **Move** `dinaro/deploy/{Dockerfile, fly.toml, requirements.txt, .dockerignore}`
   to the new repo root. (Delete the now-empty `dinaro/deploy/` in the new repo.)
3. **Copy the shared static assets** into a top-level `static/`:
   `favicon.svg`, `manifest.json`, `sw.js`, `dinaro-push.js`.
   (Consider trimming `sw.js`/`manifest.json` to Dinaro-only entries.)
4. **Set environment variables** (Fly secrets / local `.env`):
   - `DINARO_DATABASE_URL` — Dinaro's own database (Postgres in prod). Required
     standalone; without it, `dinaro/db.py` tries to import the monorepo's
     `database.py`, which won't exist here.
   - `FLASK_SECRET_KEY` — stable session signing key.
   - `TIMECOST_URL` — optional external link back to TimeCost (e.g.
     `https://thetimecost.com`); leave unset to hide the link.
   - `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_CLAIM_EMAIL` — for web push
     (`dinaro/push.py`).
5. **Migrate data (optional).** `init_dinaro_db()` creates an empty schema on
   first boot. To carry existing families over, export the `dinaro_*` tables +
   `push_subscriptions` from the monorepo DB and import them into the new one.
   For a fresh start, skip this.
6. **Run locally:** `pip install -r requirements.txt` then
   `DINARO_DATABASE_URL=sqlite:///dinaro.db gunicorn dinaro.wsgi:app --bind 0.0.0.0:8080`
   → Dinaro at `http://localhost:8080/`.
7. **Deploy:** `fly launch --no-deploy` (or reuse `fly.toml`), create a Postgres
   DB (`fly postgres create` + `fly postgres attach`), set the secrets above,
   then `fly deploy`.

## Decommission from the monorepo (after the new app is live)

- Remove `dinaro/` and its `/dinaro` mount + `init_dinaro_db()` call from
  `app.py`, and drop `pywebpush` from the monorepo `requirements.txt`.
- Optionally redirect `/dinaro` → the new domain.
