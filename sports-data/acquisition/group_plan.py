"""Plan of the Primera Federación Group 2 recipe (sofascore-group2-2627-v1): pure functions of the published
inventory, the rows of the round pages just listed and the time. No network, no SDK, no store.

Design and decisions: docs/diseno-receta-grupo2.md. In short:

1. Rounds to list. Near rounds (a current match not finished whose start has passed or is within 7 days, a
   postponed match, or a finished match still inside the 72 h window) and, in rotation, the FAR_ROUNDS_PER_RUN
   other rounds with matches still to play whose calendar was checked longest ago (far calendar changes).
2. Routine detailed review is the contiguous window ending at the highest current round with a final score in the
   published inventory or this update's validated listings (at most the last three rounds).
3. Matches to consult, in priority order, within the budget:
   finished   the listing shows newly finished, including a postponed match from an old round
   changed    the listing differs from the inventory (status, score, start): a correction or calendar change
   pending    essential sections missing from an earlier update
   refresh    complete, finished matches whose SDK 72 h / 6 h refresh is due, only inside the routine window
   comments   pending narrative comments for finished current matches
   history    at most three unique old incomplete matches per manual operation
   Whatever does not fit stays pending with reason "budget" for the next manual update.

One manual operation has one durable physical-send counter shared across its batches and service restarts. It includes
curl calls, browser API fetches and every SofaScore page request made for a challenge. Six sends remain reserved to
keep attempts and browser traffic conservative; the transport ledger is the hard ceiling.

An empty answer, a 404 or an error never means "no data" here: the section stays pending, never zero.
"""
from datetime import datetime

from importar import evento_de, rasgos_evento
from queries import vacia
from persistencia.bundle import (GROUP_RECIPE as RECIPE, ROUND_NUMBERS as ROUNDS, SECTIONS, SEASON, TOURNAMENT,
                                 UNIQUE_TOURNAMENT, event_path, round_path)

# The SDK's RefreshSettings defaults (window_hours=72, min_interval_hours=6); parity checked against refresh_due.
WINDOW_S = 72 * 3600
MIN_INTERVAL_S = 6 * 3600
UPCOMING_S = 7 * 86400
FAR_ROUNDS_PER_RUN = 6
# Manual policy: routine queries use the last rounds that have actually started producing final scores.
DEFAULT_RECENT_ROUNDS = 3
MAX_RECENT_ROUNDS = 3
MAX_HISTORICAL_RECOVERIES = 3
# Physical sends include curl, browser fetches and provider-page requests used by a challenge.
MAX_SENDS = 120          # physical sends of the adapter (curl and the browser's fetch()), production maximum
MAX_ATTEMPTS = MAX_SENDS - 4  # logical attempts of the transport (each GET of a route, retries included)
PLANNED_ATTEMPTS = 100   # attempts the plan may schedule (listings included)
SECONDS = 900
# Conservative headroom for a challenge, its browser pages and an API fetch. The adapter remains the hard cap.
SEND_RESERVE = 6
FINISHED = 'finished'
OPEN_LATER = ('postponed', 'suspended', 'interrupted', 'delayed')
PRIORITY = ('finished', 'changed', 'pending', 'refresh', 'history')


def instant(value):
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (AttributeError, OverflowError, TypeError, ValueError):
        # Invalid legacy observation times are unknown for scheduling, not a reason to fail the whole scope read.
        return None


def matches(baseline):
    """Current and historical matches of the inventory with what the plan needs: round, start, status, the event's
    fetched_at, the sections stored and when the calendar was last checked."""
    records = {}
    for source in baseline['records']:
        row = source['record']
        records[(row['event_id'], row['key'])] = row
    out = {}
    for e in baseline['events']:
        ident = e['id']
        event = records.get((ident, 'event'))
        listing = records.get((ident, 'listing'))
        if event is not None:
            payload = evento_de(event['payload'])
            number = (payload.get('roundInfo') or {}).get('round')
            start = payload.get('startTimestamp')
            status = (payload.get('status') or {}).get('type') or e['status']
            # Last calendar check: the event's observation or a later listing of its round (far rounds are listed
            # without asking their events, so the rotation must see that listing to move on).
            seen = instant(((listing or {}).get('payload') or {}).get('quality', {}).get('observed_at_utc')) if listing else None
            checked = max(filter(None, (instant(event['fetched_at']), seen)), default=None)
        else:
            payload = listing['payload'] if listing else {}
            number = (payload.get('round') or {}).get('number')
            start = instant(payload.get('start_utc'))
            status = (payload.get('status') or {}).get('type') or e['status']
            checked = instant((payload.get('quality') or {}).get('observed_at_utc'))
        out[ident] = dict(id=ident, current=e['current'], round=number, start=start, status=status,
                          event_fetched=instant(event['fetched_at']) if event else None, checked=checked,
                          sections={k for k in SECTIONS if (ident, k) in records and
                                    not vacia(k, records[(ident, k)]['payload'])},
                          traits=rasgos_evento(event['payload']) if event else None,
                          home=e['home'], away=e['away'])
    return out


def refresh_due(match, now):
    """The SDK's rule for a closed match: observed before WINDOW_S after kick-off and at least MIN_INTERVAL_S ago."""
    if match['status'] != FINISHED or match['event_fetched'] is None or match['start'] is None:
        return False
    return match['event_fetched'] - match['start'] < WINDOW_S and match['event_fetched'] <= now - MIN_INTERVAL_S


def reference_round(baseline, listed=None):
    """Highest current round with a finished match in local evidence or this update's listing."""
    played = []
    for match in matches(baseline).values():
        if match['current'] and match['status'] == FINISHED and type(match['round']) is int and match['round'] in ROUNDS:
            played.append(match['round'])
    for row in (listed or {}).values():
        number = round_of(row)
        if ((row.get('status') or {}).get('type') == FINISHED and type(number) is int and number in ROUNDS):
            played.append(number)
    return max(played) if played else None


def recent_rounds(reference, count=DEFAULT_RECENT_ROUNDS):
    """The contiguous round-number window; no reference means no fixture has been completed yet."""
    if type(count) is not int or not 1 <= count <= MAX_RECENT_ROUNDS:
        raise ValueError('Recent round count is outside the approved policy')
    if reference is None:
        return []
    if type(reference) is not int or reference not in ROUNDS:
        raise ValueError('Invalid reference round')
    return list(range(max(1, reference - count + 1), reference + 1))


def rounds_to_list(baseline, now, *, far_per_run=FAR_ROUNDS_PER_RUN, listed_at=None):
    """(near, far) round numbers, each sorted. Far rounds rotate: the ones checked longest ago first. listed_at:
    {round: last successful listing}, kept by the acquisition service itself (round_listings), because a listing
    that changes nothing leaves no new trace in the published inventory."""
    listed_at = listed_at or {}
    by_round = {}
    for m in matches(baseline).values():
        if m['current'] and m['round'] in ROUNDS:
            by_round.setdefault(m['round'], []).append(m)
    near, far = set(), []
    for number, ms in by_round.items():
        pending_start = any(m['status'] != FINISHED and m['start'] is not None and m['start'] <= now + UPCOMING_S for m in ms)
        postponed = any(m['status'] in OPEN_LATER for m in ms)
        window = any(m['status'] == FINISHED and m['start'] is not None and now - m['start'] < WINDOW_S for m in ms)
        unfinished = any(m['status'] != FINISHED for m in ms)
        if pending_start or postponed or window:
            near.add(number)
        elif unfinished:
            oldest = min((m['checked'] if m['checked'] is not None else float('-inf')) for m in ms)
            far.append((max(oldest, listed_at.get(number, float('-inf'))), number))
    far.sort()
    return sorted(near), sorted(n for _, n in far[:far_per_run])


def round_listings(transport_root):
    """{round: instant of its last successful listing} from the job records of the acquisition service's transport
    (one JSON per job with its attempts). Operational memory for the rotation only, never published data."""
    import json
    from pathlib import Path
    from persistencia.bundle import ROUND_PATHS
    seen = {}
    for path in Path(transport_root).glob('*.json'):
        if path.name == 'retry.json':
            continue
        try:
            attempts = json.loads(path.read_bytes())['attempts']
        except (OSError, ValueError, KeyError, TypeError):
            continue
        for a in attempts:
            number = ROUND_PATHS.get(a.get('path'))
            if number is not None and a.get('status') == 200 and a.get('finished_at'):
                seen[number] = max(seen.get(number, float('-inf')), instant(a['finished_at']))
    return seen


def in_scope(row, teams):
    """A listed row of this competition between two of the 20 own teams."""
    t = row.get('tournament') or {}
    return (t.get('id') == TOURNAMENT and (t.get('uniqueTournament') or {}).get('id') == UNIQUE_TOURNAMENT
            and (row.get('season') or {}).get('id') in (None, SEASON)
            and {(row.get('homeTeam') or {}).get('id'), (row.get('awayTeam') or {}).get('id')} <= set(teams)
            and (row.get('homeTeam') or {}).get('id') != (row.get('awayTeam') or {}).get('id'))


def round_of(match_or_row):
    """The round of an inventory match (`round`) or of a listed row (`roundInfo.round`); None if unknown."""
    if 'round' in match_or_row:
        return match_or_row['round']
    return (match_or_row.get('roundInfo') or {}).get('round')


def plan_matches(baseline, listed, now, *, budget=PLANNED_ATTEMPTS, detail_rounds=None, recent_rounds=None,
                 history_limit=MAX_HISTORICAL_RECOVERIES, history_ids=(), reasons=None):
    """Matches to consult. `listed`: {match id: row of a round page just listed}. Returns (planned, deferred):
    planned is a list of dict(id, reason, keys, cost) in consultation order; deferred the ones left for the next
    update ({id: 'budget' | 'restricted'}). keys: the sections to ask besides the event (empty: event only).
    detail_rounds: legacy restriction for already sealed group-v1/v2 jobs. recent_rounds is the rolling routine window.
    Newly finished matches and detected calendar changes remain exceptions; older incomplete resources are considered
    as bounded history work. `reasons` lets the recipe preserve stages while sharing this planner."""
    known = matches(baseline)
    candidates = []
    for ident, m in known.items():
        if not m['current']:
            continue  # retained history is never consulted nor changed
        row = listed.get(ident)
        listed_status = ((row or {}).get('status') or {}).get('type')
        finished_now = listed_status == FINISHED or (row is None and m['status'] == FINISHED)
        number = round_of(m)
        if row is not None and listed_status == FINISHED and m['status'] != FINISHED:
            candidates.append(('finished', m['start'] or 0, ident, SECTIONS))
        elif row is not None and m['traits'] is not None and rasgos_evento(row) != m['traits']:
            candidates.append(('changed', m['start'] or 0, ident, SECTIONS if finished_now else ()))
        elif row is not None and m['traits'] is None:
            candidates.append(('changed', m['start'] or 0, ident, SECTIONS if finished_now else ()))
        elif m['status'] == FINISHED and m['sections'] != set(SECTIONS):
            missing = tuple(k for k in SECTIONS if k not in m['sections'])
            candidates.append(('pending' if recent_rounds is None or number in recent_rounds else 'history',
                               m['start'] or 0, ident, missing))
        elif refresh_due(m, now):
            candidates.append(('refresh', m['start'] or 0, ident, SECTIONS))
    # New provider IDs are reported separately by new_matches(). They are not eligible for detail requests or
    # publication; a possible replacement is a local operator decision, never a source-side inference.
    if reasons is not None:
        selected_reasons = set(reasons)
        if not selected_reasons <= set(PRIORITY):
            raise ValueError('Unknown planning reason')
        candidates = [candidate for candidate in candidates if candidate[0] in selected_reasons]
    candidates.sort(key=lambda c: (PRIORITY.index(c[0]), c[1], c[2]))
    planned, deferred, used, histories = [], {}, 0, 0
    history_ids = set(history_ids)
    for reason, _start, ident, keys in candidates:
        if detail_rounds is not None:
            number = round_of(known[ident]) if ident in known else round_of(listed[ident])
            if number not in detail_rounds:
                deferred[ident] = 'restricted'
                continue
        number = round_of(known[ident]) if ident in known else round_of(listed[ident])
        if reason == 'refresh' and recent_rounds is not None and number not in recent_rounds:
            deferred[ident] = 'restricted'
            continue
        if (reason == 'history' and ident not in history_ids
                and (type(history_limit) is not int or history_limit < 0 or histories >= history_limit)):
            deferred[ident] = 'restricted'
            continue
        cost = 1 + len(keys)
        if used + cost > budget:
            deferred[ident] = 'budget'
            continue
        used += cost
        if reason == 'history' and ident not in history_ids:
            histories += 1
        planned.append(dict(id=ident, reason=reason, keys=tuple(keys), cost=cost))
    return planned, deferred


def new_matches(baseline, listed):
    """Listed matches in scope that the inventory does not know, with the current unfinished inventory matches that
    have the same pairing: possible local replacements for the operator, never linked automatically."""
    known = matches(baseline)
    out = []
    for ident, row in sorted(listed.items()):
        if ident in known or not in_scope(row, baseline['teams']):
            continue
        pair = {(row.get('homeTeam') or {}).get('id'), (row.get('awayTeam') or {}).get('id')}
        same = sorted(k for k, m in known.items() if m['current'] and m['status'] != FINISHED and {m['home'], m['away']} == pair)
        out.append(dict(id=ident, replaces=same))
    return out
