#!/usr/bin/env python3
"""Pull the last N days from the Google Health API and merge them into health.json in the workout gist.

Env: GHEALTH_CLIENT_ID, GHEALTH_CLIENT_SECRET, GHEALTH_REFRESH_TOKEN, GIST_TOKEN, GIST_ID, [DAYS=14]
Stdlib only, so it runs anywhere. Every data type is pulled independently: one failing never blocks the rest.
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
        civil = lambda d: {'year': d.year, 'month': d.month, 'day': d.day}
        body = {'range': {'start': civil(d0), 'end': civil(d1)}, 'windowSizeDays': 1, 'pageSize': 1000}
        r = http(API + dtype + '/dataPoints:dailyRollUp', 'POST', body, self.h)
        return r.get('rollupDataPoints', [])


def civil_date(c):
    """CivilDateTime {year,month,day,...} or an RFC3339/civil string -> 'YYYY-MM-DD'."""
    if isinstance(c, dict):
        return f"{c['year']:04d}-{c['month']:02d}-{c['day']:02d}"
    return str(c)[:10]


def secs(s):
    try: return float(str(s).rstrip('s'))
    except Exception: return 0.0


def iso(ts):
    return ts if ts else None


def pull(h):
    days = {}
    day = lambda d: days.setdefault(d, {})
    f = start.isoformat()

    def attempt(label, fn):
        try: fn()
        except Exception as e: errors.append(f'{label}: {e}')

    # ---- daily rollups
    def rollup(dtype, key, pick):
        d0, d1 = start, today + timedelta(days=1)
        if dtype == 'heart-rate' and (d1 - d0).days > 14: d0 = d1 - timedelta(days=14)
        for p in h.daily(dtype, d0, d1):
            d = civil_date(p.get('civilStartTime'))
            v = pick(p)
            if v is not None: day(d)[key] = v
    num = lambda x: None if x is None else (int(x) if str(x).lstrip('-').isdigit() else float(x))
    first = lambda o: next(iter(o.values())) if isinstance(o, dict) and o else None
    attempt('steps', lambda: rollup('steps', 'steps', lambda p: num(first(p.get('steps')))))
    attempt('total-calories', lambda: rollup('total-calories', 'calories', lambda p: num(first(p.get('totalCalories')))))
    attempt('active-zone-minutes', lambda: rollup('active-zone-minutes', 'azm', lambda p: num(first(p.get('activeZoneMinutes')))))
    attempt('distance', lambda: rollup('distance', 'distance', lambda p: num(first(p.get('distance')))))
    attempt('heart-rate', lambda: rollup('heart-rate', 'hr', lambda p: p.get('heartRate')))

    # ---- daily summaries via list
    def daily_list(dtype, field, key, pick):
        for p in h.list(dtype, f'{field}.date >= "{f}"'):
            v = p.get(field) or {}
            d = civil_date(v.get('date'))
            if d: day(d)[key] = pick(v)
    attempt('resting-hr', lambda: daily_list('daily-resting-heart-rate', 'dailyRestingHeartRate', 'restingHr', lambda v: num(v.get('beatsPerMinute'))))
    attempt('hrv', lambda: daily_list('daily-heart-rate-variability', 'dailyHeartRateVariability', 'hrv', lambda v: {k: v[k] for k in v if k != 'date'}))

    # ---- weight samples
    def weight():
        for p in h.list('weight', f'weight.sample_time.civil_time >= "{f}T00:00:00"'):
            w = p.get('weight') or {}
            st = w.get('sampleTime') or {}
            d = civil_date(st.get('civilTime') or st.get('physicalTime'))
            if d: day(d)['weight'] = {k: v for k, v in w.items() if k != 'sampleTime'}
    attempt('weight', weight)

    # ---- sleep: one record per night, keyed by the morning you woke up
    def sleep():
        for p in h.list('sleep', f'sleep.interval.civil_end_time >= "{f}"'):
            s = p.get('sleep') or {}
            iv = s.get('interval') or {}
            end = iv.get('endTime'); st = iv.get('startTime')
            d = civil_date(iv.get('civilEndTime') or end)
            if not d: continue
            stages = {}
            for sg in s.get('stages') or []:
                t = (sg.get('type') or '?').lower()
                si = sg.get('interval') or {}
                dur = secs(sg.get('duration')) if sg.get('duration') else 0
                if not dur and si.get('startTime') and si.get('endTime'):
                    dur = (datetime.fromisoformat(si['endTime'].replace('Z', '+00:00')) - datetime.fromisoformat(si['startTime'].replace('Z', '+00:00'))).total_seconds()
                stages[t] = stages.get(t, 0) + dur / 60
            total = 0
            if st and end:
                total = (datetime.fromisoformat(end.replace('Z', '+00:00')) - datetime.fromisoformat(st.replace('Z', '+00:00'))).total_seconds() / 60
            asleep = total - stages.get('awake', 0) if stages else total
            rec = {'start': iso(st), 'end': iso(end), 'inBedMin': round(total), 'asleepMin': round(asleep),
                   'stagesMin': {k: round(v) for k, v in stages.items()}, 'summary': s.get('summary'), 'type': s.get('type')}
            prev = day(d).get('sleep')
            if not prev or rec['inBedMin'] > prev['inBedMin']: day(d)['sleep'] = rec   # keep the main sleep, not a nap
    attempt('sleep', sleep)

    # ---- exercise sessions
    def exercise():
        for p in h.list('exercise', f'exercise.interval.civil_start_time >= "{f}"'):
            e = p.get('exercise') or {}
            iv = e.get('interval') or {}
            d = civil_date(iv.get('civilStartTime') or iv.get('startTime'))
            if not d: continue
            rec = {'type': e.get('exerciseType'), 'name': e.get('displayName'), 'start': iv.get('startTime'), 'end': iv.get('endTime'),
                   'activeMin': round(secs(e.get('activeDuration')) / 60) if e.get('activeDuration') else None,
                   'metrics': e.get('metricsSummary'), 'source': (p.get('dataSource') or {}).get('recordingMethod')}
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
    if days and not errors: return
    if not days: sys.exit('nothing pulled — check scopes and the Google Health app sync')


if __name__ == '__main__':
    main()
