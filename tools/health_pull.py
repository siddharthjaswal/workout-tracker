#!/usr/bin/env python3
"""Pull the last N days from the Google Health API and merge them into health.json in the workout gist.

Env: GHEALTH_CLIENT_ID, GHEALTH_CLIENT_SECRET, GHEALTH_REFRESH_TOKEN, GIST_TOKEN, GIST_ID, [DAYS=14]
Stdlib only, so it runs anywhere. Every data type is pulled independently: one failing never blocks the rest.
Field names follow the v4 discovery document (health.googleapis.com/$discovery/rest?version=v4).
"""
import json, os, sys, urllib.request, urllib.parse, urllib.error
from datetime import date, datetime, timedelta, timezone

API = 'https://health.googleapis.com/v4/users/me/dataTypes/'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
GIST_FILE = 'health.json'
env = os.environ.get
DAYS = int(env('DAYS', '14'))
today = date.today()
start = today - timedelta(days=DAYS)
errors = []


def http(url, method='GET', body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'{method} {url.split("?")[0]} -> {e.code}: {e.read().decode()[:300]}') from None


def access_token():
    form = urllib.parse.urlencode({
        'client_id': env('GHEALTH_CLIENT_ID'), 'client_secret': env('GHEALTH_CLIENT_SECRET'),
        'refresh_token': env('GHEALTH_REFRESH_TOKEN'), 'grant_type': 'refresh_token',
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=form, headers={'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)['access_token']
    except urllib.error.HTTPError as e:
        sys.exit(f'Token refresh failed ({e.code}): {e.read().decode()[:300]}\n'
                 'If this says invalid_grant the refresh token expired (Testing-mode apps expire after 7 days): run tools/health_auth.py again.')


class Health:
    def __init__(self, tok):
        self.h = {'Authorization': 'Bearer ' + tok}

    def list(self, dtype, flt):
        out, token = [], None
        for _ in range(20):
            q = {'filter': flt, 'pageSize': 1000}
            if token: q['pageToken'] = token
            r = http(API + dtype + '/dataPoints?' + urllib.parse.urlencode(q), headers=self.h)
            out += r.get('dataPoints', [])
            token = r.get('nextPageToken')
            if not token: break
        return out

    def daily(self, dtype, d0, d1):
        civil = lambda d: {'date': {'year': d.year, 'month': d.month, 'day': d.day}}
        body = {'range': {'start': civil(d0), 'end': civil(d1)}, 'windowSizeDays': 1, 'pageSize': 1000}
        r = http(API + dtype + '/dataPoints:dailyRollUp', 'POST', body, self.h)
        return r.get('rollupDataPoints', [])


def civil_date(c):
    """CivilDateTime {date:{year,month,day},time:{...}} | {year,month,day} | RFC3339 string -> 'YYYY-MM-DD'."""
    if isinstance(c, dict):
        d = c.get('date', c)
        return f"{d['year']:04d}-{d['month']:02d}-{d['day']:02d}"
    return str(c)[:10] if c else None


def num(x):
    if x is None: return None
    try: return int(x)
    except (TypeError, ValueError):
        try: return round(float(x), 1)
        except (TypeError, ValueError): return None


def secs(s):
    try: return float(str(s).rstrip('s'))
    except Exception: return 0.0


def ts_minutes(a, b):
    try: return (datetime.fromisoformat(b.replace('Z', '+00:00')) - datetime.fromisoformat(a.replace('Z', '+00:00'))).total_seconds() / 60
    except Exception: return 0.0


def pull(h):
    days = {}
    day = lambda d: days.setdefault(d, {})
    f = start.isoformat()

    def attempt(label, fn):
        try: fn()
        except Exception as e: errors.append(f'{label}: {e}')

    # ---- daily rollups (exact value fields from the discovery doc)
    def rollup(dtype, key, pick):
        d0, d1 = start, today + timedelta(days=1)
        if dtype in ('heart-rate', 'total-calories', 'active-minutes') and (d1 - d0).days > 14: d0 = d1 - timedelta(days=14)
        for p in h.daily(dtype, d0, d1):
            d = civil_date(p.get('civilStartTime'))
            v = pick(p)
            if d and v is not None: day(d)[key] = v
    attempt('steps', lambda: rollup('steps', 'steps', lambda p: num((p.get('steps') or {}).get('countSum'))))
    attempt('total-calories', lambda: rollup('total-calories', 'calories', lambda p: num((p.get('totalCalories') or {}).get('kcalSum'))))
    attempt('distance', lambda: rollup('distance', 'distanceKm', lambda p: None if (p.get('distance') or {}).get('millimetersSum') is None else round(int(p['distance']['millimetersSum']) / 1e6, 2)))
    def azm(p):
        z = p.get('activeZoneMinutes')
        if not z: return None
        return sum(int(z.get(k) or 0) for k in ('sumInFatBurnHeartZone', 'sumInCardioHeartZone', 'sumInPeakHeartZone'))
    attempt('active-zone-minutes', lambda: rollup('active-zone-minutes', 'azm', azm))
    def hr(p):
        v = p.get('heartRate')
        if not v: return None
        return {'min': num(v.get('beatsPerMinuteMin')), 'avg': num(v.get('beatsPerMinuteAvg')), 'max': num(v.get('beatsPerMinuteMax'))}
    attempt('heart-rate', lambda: rollup('heart-rate', 'hr', hr))

    # ---- daily summaries: list with the snake_case daily filter; rollup as fallback
    def daily_list(dtype, field, flt_field, key, pick):
        for p in h.list(dtype, f'{flt_field}.date >= "{f}"'):
            v = p.get(field) or {}
            d = civil_date(v.get('date'))
            if d: day(d)[key] = pick(v)
    def resting():
        try:
            daily_list('daily-resting-heart-rate', 'dailyRestingHeartRate', 'daily_resting_heart_rate', 'restingHr', lambda v: num(v.get('beatsPerMinute')))
        except Exception as e:
            errors.append(f'resting-hr list: {e}')
            rollup('daily-resting-heart-rate', 'restingHr', lambda p: num((p.get('restingHeartRatePersonalRange') or {}).get('beatsPerMinuteMin')))
    attempt('resting-hr', resting)
    def hrv():
        try:
            daily_list('daily-heart-rate-variability', 'dailyHeartRateVariability', 'daily_heart_rate_variability', 'hrv',
                       lambda v: {'rmssd': num(v.get('averageHeartRateVariabilityMilliseconds')), 'deepSleepRmssd': num(v.get('deepSleepRootMeanSquareOfSuccessiveDifferencesMilliseconds')), 'nonRemHr': num(v.get('nonRemHeartRateBeatsPerMinute'))})
        except Exception as e:
            errors.append(f'hrv list: {e}')
            rollup('daily-heart-rate-variability', 'hrv', lambda p: {'rmssd': num((p.get('heartRateVariabilityPersonalRange') or {}).get('averageHeartRateVariabilityMillisecondsMin'))})
    attempt('hrv', hrv)

    # ---- weight samples
    def weight():
        for p in h.list('weight', f'weight.sample_time.civil_time >= "{f}"'):
            w = p.get('weight') or {}
            st = w.get('sampleTime') or {}
            d = civil_date(st.get('civilTime') or st.get('physicalTime'))
            if d and w.get('weightGrams') is not None: day(d)['weightKg'] = round(float(w['weightGrams']) / 1000, 1)
    attempt('weight', weight)

    # ---- sleep: one record per night, keyed by the morning you woke up
    def sleep():
        for p in h.list('sleep', f'sleep.interval.civil_end_time >= "{f}"'):
            s = p.get('sleep') or {}
            iv = s.get('interval') or {}
            d = civil_date(iv.get('civilEndTime') or iv.get('endTime'))
            if not d: continue
            summ = s.get('summary') or {}
            stages = {}
            for sg in summ.get('stagesSummary') or []:
                stages[(sg.get('type') or '?').lower()] = num(sg.get('minutes'))
            if not stages:
                for sg in s.get('stages') or []:
                    t = (sg.get('type') or '?').lower()
                    stages[t] = round(stages.get(t, 0) + ts_minutes(sg.get('startTime', ''), sg.get('endTime', '')))
            in_bed = num(summ.get('minutesInSleepPeriod')) or round(ts_minutes(iv.get('startTime', ''), iv.get('endTime', '')))
            asleep = num(summ.get('minutesAsleep'))
            if asleep is None: asleep = in_bed - (stages.get('awake') or 0)
            rec = {'start': iv.get('startTime'), 'end': iv.get('endTime'), 'inBedMin': in_bed, 'asleepMin': asleep,
                   'awakeMin': num(summ.get('minutesAwake')), 'toFallAsleepMin': num(summ.get('minutesToFallAsleep')),
                   'stagesMin': stages, 'type': s.get('type')}
            prev = day(d).get('sleep')
            if not prev or (rec['inBedMin'] or 0) > (prev['inBedMin'] or 0): day(d)['sleep'] = rec   # keep the main sleep, not a nap
    attempt('sleep', sleep)

    # ---- exercise sessions
    def exercise():
        for p in h.list('exercise', f'exercise.interval.civil_start_time >= "{f}"'):
            e = p.get('exercise') or {}
            iv = e.get('interval') or {}
            d = civil_date(iv.get('civilStartTime') or iv.get('startTime'))
            if not d: continue
            m = e.get('metricsSummary') or {}
            z = m.get('heartRateZoneDurations') or {}
            rec = {'type': e.get('exerciseType'), 'name': e.get('displayName'), 'start': iv.get('startTime'), 'end': iv.get('endTime'),
                   'activeMin': round(secs(e.get('activeDuration')) / 60) if e.get('activeDuration') else None,
                   'avgHr': num(m.get('averageHeartRateBeatsPerMinute')), 'kcal': num(m.get('caloriesKcal')), 'steps': num(m.get('steps')),
                   'azm': num(m.get('activeZoneMinutes')),
                   'distanceKm': None if m.get('distanceMillimeters') is None else round(int(m['distanceMillimeters']) / 1e6, 2),
                   'zonesMin': {k.replace('Time', ''): round(secs(v) / 60) for k, v in z.items()} if z else None,
                   'source': (p.get('dataSource') or {}).get('recordingMethod')}
            day(d).setdefault('exercises', []).append(rec)
    attempt('exercise', exercise)
    return days


def gist_get(gid, tok):
    return http(f'https://api.github.com/gists/{gid}', headers={'Authorization': 'Bearer ' + tok, 'Accept': 'application/vnd.github+json'})


def main():
    for k in ('GHEALTH_CLIENT_ID', 'GHEALTH_CLIENT_SECRET', 'GHEALTH_REFRESH_TOKEN', 'GIST_TOKEN', 'GIST_ID'):
        if not env(k): sys.exit(f'missing {k}')
    h = Health(access_token())
    days = pull(h)
    gid, gtok = env('GIST_ID'), env('GIST_TOKEN')
    g = gist_get(gid, gtok)
    existing = {}
    if GIST_FILE in g['files']:
        try: existing = json.loads(g['files'][GIST_FILE]['content'] or '{}').get('days', {})
        except Exception: existing = {}
    for d, v in days.items():
        existing[d] = {**existing.get(d, {}), **v}
    keep = sorted(existing)[-400:]
    out = {'updated': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'source': 'Google Health API via GitHub Actions',
           'range': [start.isoformat(), today.isoformat()], 'errors': errors, 'days': {d: existing[d] for d in keep}}
    http(f'https://api.github.com/gists/{gid}', 'PATCH', {'files': {GIST_FILE: {'content': json.dumps(out, indent=1)}}},
         {'Authorization': 'Bearer ' + gtok, 'Accept': 'application/vnd.github+json'})
    got = sorted(days)
    print(f'pulled {len(days)} day(s): {got[0] if got else "-"} .. {got[-1] if got else "-"}; gist updated')
    for e in errors: print('warning:', e)
    if not days: sys.exit('nothing pulled — check scopes and the Google Health app sync')


if __name__ == '__main__':
    main()
