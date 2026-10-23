# Workout Tracker

Single-file PWA for a 4-session Upper/Lower dumbbell program with inline form videos, one-tap exercise logging, rest timer and a session log. Lives at https://siddharthjaswal.github.io/workout-tracker/ and installs from Chrome on Android.

- `index.html` — the whole app (program data in `DAYS`, logging, sync).
- `sw.js`, `manifest.webmanifest`, icons — offline shell, install, Android share target.
- The log syncs to a secret GitHub gist (`workout.json`) with a gist-scoped token pasted once in the Log tab.
- `health.json` in the same gist holds watch data (sleep, steps, resting HR, workouts) pulled from the Google Health API by `.github/workflows/health.yml` every morning.
- `reviews.json` in the same gist holds the weekly coach notes (`{reviews:[{week, range, title, written, body[], focus[]}]}`, `**bold**` allowed). The Progress tab shows the latest one next to its own computed "What to work on" list, KPI tiles, steps/sleep/heart charts and the weekly and exercise tables.

## Connecting Google Health (one-time, ~10 minutes)

1. **Google Cloud project** at https://console.cloud.google.com → new project (any name).
2. **Enable the API**: APIs & Services → Library → search *Google Health API* → Enable.
3. **OAuth consent screen** (Google Auth Platform → Branding/Audience): External; app name *Workout tracker*; your email as support and developer contact. Under **Audience → Test users** add the Google account that owns the Google Health / Fitbit data.
4. **Data access → Add scopes** — paste these three:
   ```
   https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly
   https://www.googleapis.com/auth/googlehealth.sleep.readonly
   https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly
   ```
5. **Credentials → Create credentials → OAuth client ID → Desktop app**. Download the JSON.
6. **Publish** (Audience → *Publish app*). Unverified is fine for personal use; it only removes the 7-day refresh-token expiry that Testing mode imposes. If Google refuses, leave it in Testing and re-run step 7 whenever the workflow reports `invalid_grant`.
7. On the Mac, in this repo:
   ```bash
   python3 tools/health_auth.py ~/Downloads/client_secret_*.json
   ```
   It opens the consent page, catches the redirect, and stores `GHEALTH_CLIENT_ID`, `GHEALTH_CLIENT_SECRET`, `GHEALTH_REFRESH_TOKEN` as repository secrets.
8. Store the gist token too (same token the app uses, or a new one with only the `gist` scope):
   ```bash
   gh secret set GIST_TOKEN
   ```
9. Run it once and check: `gh workflow run health.yml`, then `gh run watch`. From then on it runs daily at 09:00 IST; the app shows the data on each session card and under *From your watch* in the Log.

Secrets never touch the repo contents; the data lands only in your secret gist.
