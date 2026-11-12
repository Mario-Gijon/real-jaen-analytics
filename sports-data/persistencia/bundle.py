"""Acquisition bundle format: inventory of the current generation and bundle verification.

Shared by the backend (receiver) and the acquisition service (producer). Standard library and the existing API
rules only; no scraper/browser imports. The wire identifiers (contract 'pilot-v2', recipe
'sofascore-pilot-16599363-v1') come from the pilot that validated this format and stay unchanged, so sealed
evidence such as the real bundle of 2026-10-28 remains verifiable. See docs/contrato-adquisicion-interno.md.
"""
import base64
from datetime import datetime
import hashlib
import json
import re
import uuid
import api
import importar

SECTIONS=('lineups','statistics','incidents')
PILOT=16599363
PATHS=('/unique-tournament/17073/season/97382/events/round/1',
       '/event/16599363','/event/16599363/lineups','/event/16599363/statistics','/event/16599363/incidents')
MAX_WIRE=96*1024*1024
MAX_FILE=4*1024*1024
MAX_COMMENTS_FILE=8*1024*1024
MAX_TOTAL=64*1024*1024
MAX_LINE=2*1024*1024

class Rejected(ValueError): pass

def encode(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()
def fingerprint(value):return sha(encode(value))
def unique(pairs):
    obj={}
    for k,v in pairs:
        if k in obj:raise Rejected('Duplicate JSON key')
        obj[k]=v
    return obj
def parse(raw):return json.loads(raw,object_pairs_hook=unique,parse_constant=lambda x:(_ for _ in ()).throw(Rejected('Nonfinite JSON')))
def instant(value):
    at=datetime.fromisoformat(value)
    if not at.tzinfo:raise Rejected('Missing timezone')
    return at.timestamp()
def record(row):return {k:row.get(k) for k in ('event_id','key','sub','fetched_at','payload')}

def inventory(generation):
    """Copy immutable published inventory, not a second SQLite connection or a provider decision."""
    data=generation._datos
    matches={m['match_id']:m for m in data['partidos']}
    teams=sorted({e['team_id'] for e in api.equipos_con_exportacion(data)})
    if len(teams)!=20:raise Rejected('Pilot requires the verified scope of 20 own teams')
    rows=[]
    for (ident,key),value in sorted(data['originales'].items()):
        if ident in matches and key in ('event','listing',*SECTIONS):
            source=value['registro']
            rows.append(dict(record=record(source),source={k:source.get(k) for k in ('export','line','content_hash','fetched_at_valid')}))
    comments_enabled=bool(getattr(generation,'comments_enabled',True))
    comments=[]
    if comments_enabled:
        for public_id,snapshot in sorted(generation._comentarios.items()):
            match=next((m for m in matches.values() if f"sofascore-match-{m['match_id']}"==public_id),None)
            if match is not None and api.vigente(match):
                comments.append(dict(event_id=match['match_id'],availability=snapshot.get('availability'),
                                     source_path=snapshot.get('sourcePath'),response_sha256=snapshot.get('responseSha256'),
                                     spanish_complete=snapshot.get('spanishComplete') is True))
    result=dict(schema=1,generation_id=generation.id,teams=teams,
                events=[dict(id=i,current=api.vigente(m),replaced_by=(m.get('replaced_by') or {}).get('match_id'),
                             home=m['home_team_id'],away=m['away_team_id'],status=m['status'],
                             tournament_id=m['tournament_id'],season_id=m['season_id']) for i,m in sorted(matches.items())],
                records=rows,comments=comments,comments_enabled=comments_enabled)
    return dict(result,sha256=fingerprint(result))

def check_inventory(value):
    if fingerprint({k:v for k,v in value.items() if k!='sha256'})!=value['sha256']:raise Rejected('Inventory hash mismatch')
    events={e['id']:e for e in value['events']}
    if len(events)!=len(value['events']) or len(set(value['teams']))!=20:raise Rejected('Invalid inventory')
    for e in events.values():
        if e['current'] is not (e['replaced_by'] is None):raise Rejected('Invalid lifecycle')
        if not e['current'] and (e['replaced_by'] not in events or not events[e['replaced_by']]['current']):raise Rejected('Invalid replacement')
        if (e['tournament_id'],e['season_id'])!=(94191,97382):raise Rejected('Inventory outside pilot scope')
    comments=value.get('comments',[])
    if 'comments_enabled' in value and type(value['comments_enabled']) is not bool:
        raise Rejected('Invalid comments capability')
    if value.get('comments_enabled') is False and comments:
        raise Rejected('Comments inventory supplied without migrated storage')
    if not isinstance(comments,list):raise Rejected('Invalid comments inventory')
    seen_comments=set()
    for item in comments:
        if (not isinstance(item,dict) or set(item)!={'event_id','availability','source_path','response_sha256','spanish_complete'}
                or type(item['event_id']) is not int or item['event_id'] not in events or not events[item['event_id']]['current']
                or item['event_id'] in seen_comments or item['availability'] not in ('available','empty','not_collected','error')
                or type(item['spanish_complete']) is not bool
                or item['source_path'] is not None and not isinstance(item['source_path'],str)
                or item['response_sha256'] is not None and (not isinstance(item['response_sha256'],str)
                                                             or len(item['response_sha256'])!=64
                                                             or any(c not in '0123456789abcdef' for c in item['response_sha256']))):
            raise Rejected('Invalid comments inventory row')
        seen_comments.add(item['event_id'])
    return events

def verify(body,descriptor,baseline,*,context,expected_rules,test_transport=False):
    """Dispatch on the bundle's contract: 'pilot-v2' (the pilot's rules, unchanged) or 'group-v1' (the Group 2
    recipe with partial coverage). Returns (manifest, files)."""
    try:
        contract=parse(body).get('manifest',{}).get('contract') if len(body)<=MAX_WIRE else None
        check=_verify_group if contract in ('group-v1','group-v2') else _verify
        return check(body,descriptor,baseline,context=context,expected_rules=expected_rules,test_transport=test_transport)
    except Rejected:raise
    except (ValueError,TypeError,KeyError,AttributeError,IndexError,UnicodeError,OverflowError):raise Rejected('Malformed v2 bundle') from None

def _verify(body,descriptor,baseline,*,context,expected_rules,test_transport):
    expected=check_inventory(baseline)
    if set(descriptor)!={'bytes','sha256'} or type(descriptor['bytes']) is not int or len(body)!=descriptor['bytes'] or len(body)>MAX_WIRE or sha(body)!=descriptor['sha256']:raise Rejected('Bundle descriptor mismatch')
    obj=parse(body)
    if set(obj)!={'manifest','files'}:raise Rejected('Unknown envelope')
    m=obj['manifest']
    fields={'schema','contract','job_id','request_id','recipe_id','provider_revision','transport','inventory_sha256','generation_id','rules_sha256','policy','started_at','finished_at','files','coverage','retained','attempts','errors'}
    if set(m)!=fields or type(m['schema']) is not int or m['schema']!=2 or m['contract']!='pilot-v2':raise Rejected('Incompatible contract')
    if any(str(uuid.UUID(m[k]))!=m[k] for k in ('job_id','request_id')) or tuple(m[k] for k in ('job_id','request_id'))!=tuple(context):raise Rejected('Wrong job context')
    if m['recipe_id']!='sofascore-pilot-16599363-v1' or m['provider_revision']!='611e436a1528b0f487aa56a045f023e8ae8ae098':raise Rejected('Wrong recipe/revision')
    if m['transport']!=('loopback-fixture' if test_transport else 'sofascore-https'):raise Rejected('Wrong transport provenance')
    if not expected_rules or m['rules_sha256']!=expected_rules:raise Rejected('Unapproved producer rules')
    if m['inventory_sha256']!=baseline['sha256'] or m['generation_id']!=baseline['generation_id']:raise Rejected('Inventory generation mismatch')
    if m['errors'] or m['policy']!={'window_s':259200,'interval_s':21600}:raise Rejected('Incomplete acquisition or unexpected policy')
    begin,end=instant(m['started_at']),instant(m['finished_at'])
    if end<begin:raise Rejected('Invalid clock')
    retained=sorted(e['id'] for e in expected.values() if not e['current'])
    if m['retained']!=retained:raise Rejected('Local history changed')
    attempts=m['attempts'];latest={};counts={}
    if not 5<=len(attempts)<=16:raise Rejected('Attempt budget mismatch')
    for n,a in enumerate(attempts,1):
        if set(a)!={'number','path','sent_at','finished_at','status','payload_sha256'} or a['number']!=n or a['path'] not in PATHS:raise Rejected('Unplanned attempt')
        counts[a['path']]=counts.get(a['path'],0)+1
        if counts[a['path']]>2:raise Rejected('Per-resource budget exceeded')
        if not begin<=instant(a['sent_at'])<=instant(a['finished_at'])<=end:raise Rejected('Invalid attempt clock')
        latest[a['path']]=a
    if set(latest)!=set(PATHS) or any(a['status']!=200 for a in latest.values()):raise Rejected('Pilot did not finish every planned resource')
    wanted={f'team_{i}.jsonl':i for i in baseline['teams']};wanted['calendar.jsonl']=None;wanted['provenance.jsonl']=None
    if set(obj['files'])!=set(wanted) or len(m['files'])!=len(wanted):raise Rejected('Own exports/calendar missing')
    entries={e['name']:e for e in m['files']}
    if len(entries)!=len(wanted) or set(entries)!=set(wanted):raise Rejected('Duplicate file descriptor')
    decoded={};rows={};total=0
    for name,owner in wanted.items():
        d=entries[name]
        if set(d)!={'name','kind','team_external_id','bytes','sha256'} or d['team_external_id']!=owner or d['kind']!=(('calendar' if name=='calendar.jsonl' else 'provenance') if owner is None else 'own_export'):raise Rejected('Invalid file owner')
        raw=base64.b64decode(obj['files'][name],validate=True);total+=len(raw)
        if len(raw)>MAX_FILE or total>MAX_TOTAL or type(d['bytes']) is not int or d['bytes']!=len(raw) or sha(raw)!=d['sha256']:raise Rejected('Invalid file size/hash')
        lines=raw.splitlines()
        if any(not l or len(l)>MAX_LINE for l in lines):raise Rejected('Invalid JSONL')
        decoded[name]=raw;rows[name]=[parse(l) for l in lines]
    provenance={}
    for e in rows['provenance.jsonl']:
        if set(e)!={'number','path','received_at','body_b64'} or type(e['number']) is not int or e['number'] in provenance or not 1<=e['number']<=len(attempts):raise Rejected('Invalid original response')
        a=attempts[e['number']-1];original=base64.b64decode(e['body_b64'],validate=True);payload=parse(original)
        selected=importar.evento_de(payload) if a['path']==PATHS[1] else payload
        if a['status']!=200 or e['path']!=a['path'] or e['received_at']!=a['finished_at'] or fingerprint(selected)!=a['payload_sha256']:raise Rejected('Original response/attempt mismatch')
        provenance[e['number']]=payload
    if not {a['number'] for a in latest.values()}<=set(provenance):raise Rejected('Original successful responses missing')
    calendar={}
    for row in rows['calendar.jsonl']:
        ident=row['id']
        if type(ident) is not int or ident in calendar:raise Rejected('Duplicate calendar ID')
        parsed=importar.partido_de_listado(row)
        if (row['tournament_id'],parsed['tournament_id'],parsed['season_id'])!=(17073,94191,97382):raise Rejected('Calendar scope mismatch')
        if not {parsed['home_team_id'],parsed['away_team_id']}<=set(baseline['teams']):raise Rejected('Calendar participants outside scope')
        if ident in expected and (parsed['home_team_id'],parsed['away_team_id'])!=(expected[ident]['home'],expected[ident]['away']):raise Rejected('Calendar changed identity')
        if ident in retained and parsed['status']!=expected[ident]['status']:raise Rejected('Historical event modified')
        calendar[ident]=row
    if not set(expected)<=set(calendar):raise Rejected('Inventory events missing from calendar')
    # The pilot never silently expands its sporting selection; new IDs need another preflight.
    if set(calendar)!=set(expected):raise Rejected('New event requires preflight revision')
    raw_records={};owned={};prior={(v['record']['event_id'],v['record']['key']):v['record'] for v in baseline['records']}
    for name,owner in wanted.items():
        if owner is None:continue
        seen=set()
        for row in rows[name]:
            ident,key=row['event_id'],row['key'];pair=(ident,key)
            if ident not in expected or not expected[ident]['current'] or key not in ('event',*SECTIONS) or row.get('sub')!='' or pair in seen:raise Rejected('Unexpected raw resource')
            seen.add(pair)
            if owner not in (expected[ident]['home'],expected[ident]['away']):raise Rejected('Wrong own export')
            if pair in raw_records and record(row)!=raw_records[pair]:raise Rejected('Shared resource conflict')
            raw_records[pair]=record(row);owned.setdefault(pair,set()).add(owner)
            if key=='event':
                event=importar.evento_de(row['payload'])
                if (event['id'],event['tournament']['id'],event['tournament']['uniqueTournament']['id'],event['season']['id'])!=(ident,94191,17073,97382):raise Rejected('Raw scope mismatch')
                if (event['homeTeam']['id'],event['awayTeam']['id'])!=(expected[ident]['home'],expected[ident]['away']):raise Rejected('Raw identity mismatch')
    for pair,owners in owned.items():
        e=expected[pair[0]]
        if owners!={e['home'],e['away']}:raise Rejected('Shared event missing own export')
    coverage=m['coverage'];seen=set();required={(i,k) for i,e in expected.items() if e['current'] for k in ('calendar','event',*SECTIONS)}
    if len(encode(coverage))>4*1024*1024:raise Rejected('Coverage too large')
    for c in coverage:
        if set(c)!={'event_id','resource','status','origin','evidence','checked_at'}:raise Rejected('Invalid coverage')
        pair=c['event_id'],c['resource']
        if pair not in required or pair in seen:raise Rejected('Unexpected coverage')
        seen.add(pair);ident,key=pair
        if c['status']=='confirmed_absence':raise Rejected('Absence rule absent; contradictions cannot publish')
        if c['status']=='not_applicable':
            ev=raw_records.get((ident,'event'));status=(importar.evento_de(ev['payload']).get('status') or {}).get('type') if ev else calendar[ident]['status']['type']
            if key not in SECTIONS and not (key=='event' and ev is None):raise Rejected('Not applicable invalid resource')
            if status not in api.PENDIENTES and status!='inprogress':raise Rejected('Not applicable invalid phase')
            if c['evidence'] is not None or c['origin']!='phase':raise Rejected('Invalid phase proof')
            continue
        if c['status']!='data':raise Rejected('Incomplete sporting coverage')
        ref=c['evidence']
        if set(ref)!={'file','line'} or type(ref['line']) is not int or ref['file'] not in rows or not 1<=ref['line']<=len(rows[ref['file']]):raise Rejected('Invalid evidence reference')
        row=rows[ref['file']][ref['line']-1]
        if key=='calendar':
            if ref['file']!='calendar.jsonl' or row['id']!=ident:raise Rejected('Calendar evidence mismatch')
            at=(row.get('quality') or {}).get('observed_at_utc')
            if at is None:
                old=prior.get((ident,'listing'))
                if c['origin']!='inventory' or old is None or old['payload']!=row:raise Rejected('Unknown calendar provenance not preserved')
        else:
            if ref['file']=='calendar.jsonl' or (row['event_id'],row['key'])!=pair:raise Rejected('Raw evidence mismatch')
            at=row['fetched_at']
            if key in SECTIONS and (not isinstance(row['payload'],(dict,list)) or api.vacia(key,row['payload'])):raise Rejected('Empty resource cannot mean data')
        if ident!=PILOT and c['checked_at']!=at:raise Rejected('Invented observation timestamp')
        if at is not None and instant(at)>end:raise Rejected('Future evidence')
        if key!='calendar' and ident!=PILOT:
            if c['origin']!='inventory' or record(row)!=prior.get(pair):raise Rejected('Pilot altered an unqueried resource')
        if ident==PILOT:
            path=(PATHS[0] if row.get('quality',{}).get('source')=='listing' else f'/event/{PILOT}') if key=='calendar' else f'/event/{PILOT}'+('' if key=='event' else '/'+key)
            a=latest[path]
            if c['origin']!='attempt' or c['checked_at']!=a['finished_at'] or (at is not None and instant(at)>instant(c['checked_at'])):raise Rejected('Pilot resource not newly checked')
            if key!='calendar' and fingerprint(row['payload'])!=a['payload_sha256']:raise Rejected('Payload/attempt mismatch')
    if seen!=required:raise Rejected('Resource coverage missing')
    return m,decoded


# --- Group 2 recipe (sofascore-group2-2627-v1): partial coverage, see docs/diseno-receta-grupo2.md -------------

GROUP_RECIPE='sofascore-group2-2627-v1'
UNIQUE_TOURNAMENT,TOURNAMENT,SEASON=17073,94191,97382
ROUND_NUMBERS=range(1,39)
MAX_GROUP_ATTEMPTS=120  # production maximum of physical sends; logical attempts stop four below it
MAX_GROUP_LOGICAL=MAX_GROUP_ATTEMPTS-4
MAX_GROUP_TOTAL_SENDS=50
MAX_GROUP_RECENT_ROUNDS=3
MAX_GROUP_HISTORY_RECOVERIES=3
MAX_PROVENANCE=24*1024*1024
PENDING_REASONS=frozenset({'empty','not_found','parse','error','budget','not_requested','restricted'})
PLAN_FIELDS=frozenset({'now','rounds','planned','deferred','new_matches','limits','detail_rounds'})
GROUP_V2_PLAN_FIELDS=PLAN_FIELDS|{'comments'}
GROUP_V3_PLAN_FIELDS=GROUP_V2_PLAN_FIELDS|{'recent_round_count','reference_round','recent_rounds','max_total_sends',
                                              'operation_sends_used','physical_sends','operation_id'}
GROUP_FIELDS=frozenset({'schema','contract','job_id','request_id','recipe_id','provider_revision','transport','inventory_sha256',
                        'generation_id','rules_sha256','policy','started_at','finished_at','files','coverage','retained',
                        'attempts','errors','plan'})

# Terminal SofaScore statuses used by the pinned scraper. Keep this small, conservative mirror here because the
# backend verifier does not install the scraper package. Unknown/contradictory statuses are never treated as final.
_COMMENT_POST_CODES=frozenset({100,110,120,91,92})
_COMMENT_POST_DESCRIPTIONS=frozenset({'ended','aet','after extra time','ap','penalties','walkover','retired'})

def _comment_payload_is_post(payload):
    """The final-phase subset of the pinned scraper's status classifier, without importing it into Sports Data."""
    event=importar.evento_de(payload)
    status=event.get('status') or {}
    if not isinstance(status,dict):return False
    status_type=status.get('type');code=status.get('code')
    if status_type:
        if status_type!='finished':return False
        if code in _COMMENT_POST_CODES:return True
        return code is None and str(status.get('description') or '').strip().lower() in _COMMENT_POST_DESCRIPTIONS
    if code is not None:return code in _COMMENT_POST_CODES
    return str(status.get('description') or '').strip().lower() in _COMMENT_POST_DESCRIPTIONS

def _listed_event_matches_inventory(ident,row,expected):
    """A listing may update comment eligibility only for the same Group 2 event and teams in this inventory."""
    tournament=row.get('tournament') or {};season=(row.get('season') or {}).get('id')
    home=(row.get('homeTeam') or {}).get('id');away=(row.get('awayTeam') or {}).get('id')
    event=expected.get(ident)
    return (event is not None and row.get('id')==ident and tournament.get('id')==TOURNAMENT
            and (tournament.get('uniqueTournament') or {}).get('id')==UNIQUE_TOURNAMENT
            and season in (None,SEASON) and (home,away)==(event['home'],event['away']))

def _group_comment_finished(current,expected,raw_records,listed,latest,planned):
    """Match the producer's status-source priority using only evidence verified in this bundle.

    A successful event-detail response wins; otherwise the latest successful round listing wins over a stale cached
    event; otherwise the cached event, then the published inventory status is used.
    """
    planned_ids={item['id'] for item in planned}
    finished=set()
    for ident in current:
        event_attempt=latest.get(event_path(ident))
        event_raw=raw_records.get((ident,'event'))
        fresh=(ident in planned_ids and event_attempt is not None and event_attempt['status']==200
               and event_attempt['payload_sha256'] is not None)
        if fresh:
            is_post=event_raw is not None and _comment_payload_is_post(event_raw['payload'])
        elif ident in listed:
            row=listed[ident][0]
            if not _listed_event_matches_inventory(ident,row,expected):
                raise Rejected('Comments listing identity mismatch')
            is_post=_comment_payload_is_post(row)
        elif event_raw is not None:
            is_post=_comment_payload_is_post(event_raw['payload'])
        else:
            is_post=expected[ident]['status']=='finished'
        if is_post:finished.add(ident)
    return finished

def round_path(number):return f'/unique-tournament/{UNIQUE_TOURNAMENT}/season/{SEASON}/events/round/{number}'
def event_path(ident,key='event'):return f'/event/{ident}'+('' if key=='event' else '/'+key)
ROUND_PATHS={round_path(n):n for n in ROUND_NUMBERS}

def pending_reason(attempt_reason):
    """Transport reason of a failed or empty answer as a pending reason. Never absence, never zero."""
    return {'empty':'empty','404':'not_found','parse':'parse','budget':'budget'}.get(attempt_reason,'error')

def coverage_summary(manifest):
    """New data, previous data kept and pending resources of a group bundle (calendar excluded), by match."""
    new=kept=0;pending={}
    for c in manifest['coverage']:
        if c['resource']=='calendar':continue
        if c['status']=='data':
            if c['origin']=='attempt':new+=1
            else:kept+=1
        elif c['status']=='pending':
            pending.setdefault(c['event_id'],[]).append(dict(resource=c['resource'],reason=c['reason'],kept=c['origin']=='inventory'))
    comments=(manifest.get('plan') or {}).get('comments') or dict(new=[],kept=[],pending=[],failed=[])
    return dict(new=new,kept=kept,pending=[dict(event_id=i,resources=r) for i,r in sorted(pending.items())],
                comments=comments)

def check_plan(plan,attempts,*,with_comments=False,dynamic_policy=False):
    """The job's declared limits and restriction: never above the production maxima, and what it did fits them."""
    fields=GROUP_V3_PLAN_FIELDS if dynamic_policy else GROUP_V2_PLAN_FIELDS if with_comments else PLAN_FIELDS
    if not isinstance(plan,dict) or set(plan)!=fields:raise Rejected('Invalid plan')
    limits,rounds=plan['limits'],plan['detail_rounds']
    if not isinstance(limits,dict) or set(limits)!={'attempts','sends'} or any(type(v) is not int for v in limits.values()):raise Rejected('Invalid limits')
    if not 1<=limits['attempts']<=min(MAX_GROUP_LOGICAL,limits['sends']) or not limits['sends']<=MAX_GROUP_ATTEMPTS:raise Rejected('Limits above production')
    if len(attempts)>limits['attempts']:raise Rejected('Attempt budget mismatch')
    if rounds is not None and (not isinstance(rounds,list) or not rounds or rounds!=sorted(set(rounds))
                               or any(type(r) is not int or r not in ROUND_NUMBERS for r in rounds)):raise Rejected('Invalid detail rounds')
    if not isinstance(plan['deferred'],dict) or any(v not in ('budget','restricted') for v in plan['deferred'].values()):raise Rejected('Invalid deferral')
    if rounds is None and 'restricted' in plan['deferred'].values():raise Rejected('Restriction without declared rounds')
    if with_comments:
        comments=plan['comments']
        if not isinstance(comments,dict) or set(comments)!={'new','kept','pending','failed'}:
            raise Rejected('Invalid comments coverage')
        if any(not isinstance(comments[k],list) for k in comments):raise Rejected('Invalid comments coverage')
    if dynamic_policy:
        if (plan['detail_rounds'] is not None or type(plan['recent_round_count']) is not int
                or not 1<=plan['recent_round_count']<=MAX_GROUP_RECENT_ROUNDS
                or plan['reference_round'] is not None and (type(plan['reference_round']) is not int
                                                             or plan['reference_round'] not in ROUND_NUMBERS)
                or not isinstance(plan['recent_rounds'],list)
                or plan['recent_rounds']!=sorted(set(plan['recent_rounds']))
                or any(type(r) is not int or r not in ROUND_NUMBERS for r in plan['recent_rounds'])):
            raise Rejected('Invalid rolling round window')
        expected_recent=([] if plan['reference_round'] is None else
                         list(range(max(1,plan['reference_round']-plan['recent_round_count']+1),
                                    plan['reference_round']+1)))
        if plan['recent_rounds']!=expected_recent:
            raise Rejected('Rolling window does not match its reference round')
        if (type(plan['max_total_sends']) is not int or not 1<=plan['max_total_sends']<=MAX_GROUP_TOTAL_SENDS
                or type(plan['operation_sends_used']) is not int
                or not 0<=plan['operation_sends_used']<=plan['max_total_sends']
                or type(plan['physical_sends']) is not int
                or not 0<=plan['physical_sends']<=limits['sends']
                or plan['physical_sends']>plan['operation_sends_used']
                or type(plan['operation_id']) is not str):
            raise Rejected('Invalid operation send accounting')
        try:
            if str(uuid.UUID(plan['operation_id']))!=plan['operation_id']:
                raise ValueError()
        except (ValueError,TypeError,AttributeError):
            raise Rejected('Invalid operation id') from None
        if limits['sends']>plan['max_total_sends']:
            raise Rejected('Per-batch sends exceed the operation allowance')
        planned=plan['planned']
        if (not isinstance(planned,list) or any(not isinstance(item,dict)
                or set(item)!={'id','reason','keys'} or type(item['id']) is not int
                or item['reason'] not in ('finished','changed','pending','refresh','history')
                or not isinstance(item['keys'],list) or len(set(item['keys']))!=len(item['keys'])
                or any(key not in SECTIONS for key in item['keys']) for item in planned)
                or len({item['id'] for item in planned})!=len(planned)
                or sum(item['reason']=='history' for item in planned)>MAX_GROUP_HISTORY_RECOVERIES):
            raise Rejected('Invalid planned work')
    return limits,rounds

def _verify_group_file(name, descriptor, raw, total, *, with_comments):
    """Verify one Group bundle file at its trust boundary, preserving its per-kind and aggregate limits."""
    limit = MAX_PROVENANCE if name == 'provenance.jsonl' else (
        MAX_COMMENTS_FILE if with_comments and name == 'comments.jsonl' else MAX_FILE)
    if (len(raw) > limit or total + len(raw) > MAX_TOTAL or type(descriptor.get('bytes')) is not int
            or descriptor['bytes'] != len(raw) or sha(raw) != descriptor.get('sha256')):
        raise Rejected('Invalid file size/hash')
    lines=raw.splitlines()
    if any(not line or len(line)>MAX_LINE for line in lines):raise Rejected('Invalid JSONL')
    try:
        rows = [parse(line) for line in lines]
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
        raise Rejected('Invalid JSONL') from None
    return rows, total + len(raw)

def listed_candidate(row,teams):
    """Same fixed-scope predicate as the producer, repeated at the receiving trust boundary."""
    tournament=row.get('tournament') or {}
    season=(row.get('season') or {}).get('id')
    home=(row.get('homeTeam') or {}).get('id');away=(row.get('awayTeam') or {}).get('id')
    return (tournament.get('id')==TOURNAMENT and (tournament.get('uniqueTournament') or {}).get('id')==UNIQUE_TOURNAMENT
            and season in (None,SEASON) and {home,away}<=set(teams) and home!=away)

def _verify_group(body,descriptor,baseline,*,context,expected_rules,test_transport):
    """Like _verify, generalized from the pilot match to the set the job consulted. What was not consulted equals
    the inventory; what was, matches its attempt; a resource with a previous original is never dropped: it is new
    data, the same original, or pending with that original kept. Empty, 404, unreadable or not asked: pending with
    its reason, never data and never absence. New IDs from listed rounds are review-only: no detail request or
    calendar publication is accepted until an operator makes a local replacement decision."""
    expected=check_inventory(baseline)
    if set(descriptor)!={'bytes','sha256'} or type(descriptor['bytes']) is not int or len(body)!=descriptor['bytes'] or len(body)>MAX_WIRE or sha(body)!=descriptor['sha256']:raise Rejected('Bundle descriptor mismatch')
    obj=parse(body)
    if set(obj)!={'manifest','files'}:raise Rejected('Unknown envelope')
    m=obj['manifest']
    if set(m)!=GROUP_FIELDS or type(m['schema']) is not int or m['schema']!=2 or m['contract'] not in ('group-v1','group-v2','group-v3'):raise Rejected('Incompatible contract')
    dynamic_policy=m['contract']=='group-v3'
    with_comments=m['contract'] in ('group-v2','group-v3')
    if any(str(uuid.UUID(m[k]))!=m[k] for k in ('job_id','request_id')) or tuple(m[k] for k in ('job_id','request_id'))!=tuple(context):raise Rejected('Wrong job context')
    if m['recipe_id']!=GROUP_RECIPE or m['provider_revision']!='611e436a1528b0f487aa56a045f023e8ae8ae098':raise Rejected('Wrong recipe/revision')
    if m['transport']!=('loopback-fixture' if test_transport else 'sofascore-https'):raise Rejected('Wrong transport provenance')
    if not expected_rules or m['rules_sha256']!=expected_rules:raise Rejected('Unapproved producer rules')
    if m['inventory_sha256']!=baseline['sha256'] or m['generation_id']!=baseline['generation_id']:raise Rejected('Inventory generation mismatch')
    if m['errors'] or m['policy']!={'window_s':259200,'interval_s':21600}:raise Rejected('Incomplete acquisition or unexpected policy')
    begin,end=instant(m['started_at']),instant(m['finished_at'])
    if end<begin:raise Rejected('Invalid clock')
    retained=sorted(e['id'] for e in expected.values() if not e['current'])
    if m['retained']!=retained:raise Rejected('Local history changed')
    attempts=m['attempts'];latest={};counts={}
    if not 1<=len(attempts)<=MAX_GROUP_ATTEMPTS:raise Rejected('Attempt budget mismatch')
    _,detail_rounds=check_plan(m['plan'],attempts,with_comments=with_comments,dynamic_policy=dynamic_policy)
    for n,a in enumerate(attempts,1):
        if set(a)!={'number','path','sent_at','finished_at','status','payload_sha256'} or a['number']!=n:raise Rejected('Unplanned attempt')
        counts[a['path']]=counts.get(a['path'],0)+1
        if counts[a['path']]>2:raise Rejected('Per-resource budget exceeded')
        if not begin<=instant(a['sent_at'])<=instant(a['finished_at'])<=end:raise Rejected('Invalid attempt clock')
        latest[a['path']]=a
    wanted={f'team_{i}.jsonl':i for i in baseline['teams']};wanted['calendar.jsonl']=None;wanted['provenance.jsonl']=None
    if with_comments:wanted['comments.jsonl']=None
    if set(obj['files'])!=set(wanted) or len(m['files'])!=len(wanted):raise Rejected('Own exports/calendar missing')
    entries={e['name']:e for e in m['files']}
    if len(entries)!=len(wanted) or set(entries)!=set(wanted):raise Rejected('Duplicate file descriptor')
    rows={};total=0;decoded={}
    for name,owner in wanted.items():
        d=entries[name]
        kind=('calendar' if name=='calendar.jsonl' else 'provenance' if name=='provenance.jsonl' else 'comments') if owner is None else 'own_export'
        if set(d)!={'name','kind','team_external_id','bytes','sha256'} or d['team_external_id']!=owner or d['kind']!=kind:raise Rejected('Invalid file owner')
        raw=base64.b64decode(obj['files'][name],validate=True)
        rows[name],total=_verify_group_file(name,d,raw,total,with_comments=with_comments)
        decoded[name]=raw
    # Original sporting answers: every successful attempt, byte for byte. Comment responses have a separate
    # evidence record so an invalid comment payload can remain a comment failure without rejecting valid sports data.
    provenance={};listed={}
    for e in rows['provenance.jsonl']:
        if set(e)!={'number','path','received_at','body_b64'} or type(e['number']) is not int or e['number'] in provenance or not 1<=e['number']<=len(attempts):raise Rejected('Invalid original response')
        a=attempts[e['number']-1]
        if with_comments and re.fullmatch(r'/event/[1-9][0-9]{0,11}/comments/es-la',a['path']):
            raise Rejected('Comment response belongs in comments evidence')
        payload=parse(base64.b64decode(e['body_b64'],validate=True))
        selected=importar.evento_de(payload) if a['path'] not in ROUND_PATHS and a['path'].count('/')==2 else payload
        if a['status']!=200 or e['path']!=a['path'] or e['received_at']!=a['finished_at'] or fingerprint(selected)!=a['payload_sha256']:raise Rejected('Original response/attempt mismatch')
        provenance[e['number']]=payload
        if a['path'] in ROUND_PATHS and latest[a['path']] is a:
            for row in payload.get('events') or []:
                if isinstance(row,dict) and (row.get('tournament') or {}).get('id')==TOURNAMENT:listed[row.get('id')]=(row,a)
    required_provenance={a['number'] for a in attempts if a['status']==200 and not (
        with_comments and re.fullmatch(r'/event/[1-9][0-9]{0,11}/comments/es-la',a['path']))}
    if not required_provenance<=set(provenance):raise Rejected('Original successful responses missing')
    reported=[]
    for ident,(row,_attempt) in sorted(listed.items()):
        if ident in expected or type(ident) is not int or not listed_candidate(row,baseline['teams']):continue
        pair={(row.get('homeTeam') or {}).get('id'),(row.get('awayTeam') or {}).get('id')}
        replaces=sorted(i for i,e in expected.items() if e['current'] and e['status']!='finished' and
                        {e['home'],e['away']}==pair)
        reported.append(dict(id=ident,replaces=replaces))
    if m['plan']['new_matches']!=reported:raise Rejected('Detected new IDs missing or changed in review report')
    calendar={}
    for row in rows['calendar.jsonl']:
        ident=row['id']
        if type(ident) is not int or ident in calendar:raise Rejected('Duplicate calendar ID')
        parsed=importar.partido_de_listado(row)
        if (row['tournament_id'],parsed['tournament_id'],parsed['season_id'])!=(UNIQUE_TOURNAMENT,TOURNAMENT,SEASON):raise Rejected('Calendar scope mismatch')
        if not {parsed['home_team_id'],parsed['away_team_id']}<=set(baseline['teams']) or parsed['home_team_id']==parsed['away_team_id']:raise Rejected('Calendar participants outside scope')
        if ident in expected and (parsed['home_team_id'],parsed['away_team_id'])!=(expected[ident]['home'],expected[ident]['away']):raise Rejected('Calendar changed identity')
        if ident in retained and parsed['status']!=expected[ident]['status']:raise Rejected('Historical event modified')
        if ident not in expected:raise Rejected('New event requires explicit review before import')
        calendar[ident]=(row,parsed)
    if not set(expected)<=set(calendar):raise Rejected('Inventory events missing from calendar')
    order=list(calendar)
    current={i for i in expected if expected[i]['current']}
    planned_by_id={item['id']:item for item in m['plan']['planned']}
    if dynamic_policy:
        from acquisition.group_plan import reference_round as group_reference_round
        actual_reference=group_reference_round(baseline,{i:row for i,(row,_attempt) in listed.items()})
        if actual_reference!=m['plan']['reference_round']:
            raise Rejected('Reference round is not supported by the inventory and listing')
    participants={i:((expected[i]['home'],expected[i]['away']) if i in expected else (p['home_team_id'],p['away_team_id'])) for i,(_,p) in calendar.items()}
    for path in latest:
        if path in ROUND_PATHS:continue
        if with_comments and re.fullmatch(r'/event/[1-9][0-9]{0,11}/comments/es-la',path):
            if int(path.split('/')[2]) not in current:raise Rejected('Comments event outside inventory')
            continue
        parts=path.split('/')
        if len(parts) not in (3,4) or parts[1]!='event' or not parts[2].isdigit() or int(parts[2]) not in current or (len(parts)==4 and parts[3] not in SECTIONS):raise Rejected('Unplanned attempt')
        ident=int(parts[2]);round_number=calendar[ident][1]['round']
        if dynamic_policy:
            candidate=planned_by_id.get(ident)
            if candidate is None or (len(parts)==4 and parts[3] not in candidate['keys']):
                raise Rejected('Detailed request is missing from the rolling plan')
            reason=candidate['reason']
            from acquisition.group_plan import matches as group_matches
            old=group_matches(baseline).get(ident)
            row=listed.get(ident,(None,None))[0]
            missing=set(SECTIONS)-(old['sections'] if old else set())
            if reason=='finished' and not (row and (row.get('status') or {}).get('type')=='finished'
                                           and expected[ident]['status']!='finished'):
                raise Rejected('Finished-match exception is not supported by the listing')
            if reason=='changed':
                from importar import rasgos_evento
                if not (row and (old is None or old['traits'] is None or rasgos_evento(row)!=old['traits'])):
                    raise Rejected('Calendar-change exception is not supported by the listing')
            if reason in ('pending','history') and not (old and old['status']=='finished' and missing):
                raise Rejected('Incomplete-resource recovery is not supported by the inventory')
            if reason=='pending' and round_number not in m['plan']['recent_rounds']:
                raise Rejected('Out-of-window pending work is not an approved exception')
            if reason=='history' and round_number in m['plan']['recent_rounds']:
                raise Rejected('Recent match misclassified as historical recovery')
            if round_number not in m['plan']['recent_rounds']:
                if reason not in ('finished','changed','history'):
                    raise Rejected('Out-of-window detail lacks a verified exception')
            elif reason=='refresh' and round_number not in m['plan']['recent_rounds']:
                raise Rejected('Routine refresh outside the rolling window')
        elif detail_rounds is not None and round_number not in detail_rounds:
            raise Rejected('Detail outside the declared rounds')
    prior={(v['record']['event_id'],v['record']['key']):v['record'] for v in baseline['records']}
    raw_records={};owned={};refs={}
    for name,owner in wanted.items():
        if owner is None:continue
        seen=set()
        for line,row in enumerate(rows[name],1):
            ident,key=row['event_id'],row['key'];pair=(ident,key)
            if ident not in current or key not in ('event',*SECTIONS) or row.get('sub')!='' or pair in seen:raise Rejected('Unexpected raw resource')
            seen.add(pair)
            if owner not in participants[ident]:raise Rejected('Wrong own export')
            if pair in raw_records and record(row)!=raw_records[pair]:raise Rejected('Shared resource conflict')
            raw_records[pair]=record(row);owned.setdefault(pair,set()).add(owner);refs.setdefault(pair,dict(file=name,line=line))
            if key=='event':
                event=importar.evento_de(row['payload'])
                if (event['id'],event['tournament']['id'],event['tournament']['uniqueTournament']['id'],event['season']['id'])!=(ident,TOURNAMENT,UNIQUE_TOURNAMENT,SEASON):raise Rejected('Raw scope mismatch')
                if (event['homeTeam']['id'],event['awayTeam']['id'])!=participants[ident]:raise Rejected('Raw identity mismatch')
    for pair,owners in owned.items():
        if owners!=set(participants[pair[0]]):raise Rejected('Shared event missing own export')
    required={(i,k) for i in current for k in ('calendar','event',*SECTIONS)}
    coverage=m['coverage'];seen=set()
    if len(encode(coverage))>8*1024*1024:raise Rejected('Coverage too large')
    for c in coverage:
        if set(c)!={'event_id','resource','status','origin','evidence','checked_at','reason'}:raise Rejected('Invalid coverage')
        pair=c['event_id'],c['resource']
        if pair not in required or pair in seen:raise Rejected('Unexpected coverage')
        seen.add(pair);ident,key=pair
        if key=='calendar':
            row,_=calendar[ident]
            if c['status']!='data' or c['reason'] is not None or c['evidence']!=dict(file='calendar.jsonl',line=order.index(ident)+1):raise Rejected('Calendar evidence mismatch')
            if c['origin']=='attempt':
                if ident not in listed or c['checked_at']!=listed[ident][1]['finished_at']:raise Rejected('Calendar not newly checked')
            elif c['origin']=='inventory':
                if ident not in expected:raise Rejected('New event without its listing')
                at=(row.get('quality') or {}).get('observed_at_utc');old=prior.get((ident,'listing'))
                if at is None and (old is None or old['payload']!=row):raise Rejected('Unknown calendar provenance not preserved')
                if c['checked_at']!=at:raise Rejected('Invented observation timestamp')
            else:raise Rejected('Invalid calendar origin')
            continue
        row=raw_records.get(pair);old=prior.get(pair);a=latest.get(event_path(ident,key))
        if c['status']=='data':
            if c['reason'] is not None or row is None or c['evidence']!=refs[pair]:raise Rejected('Invalid data coverage')
            if key in SECTIONS and (not isinstance(row['payload'],(dict,list)) or api.vacia(key,row['payload'])):raise Rejected('Empty resource cannot mean data')
            if c['origin']=='attempt':
                if a is None or a['status']!=200 or fingerprint(row['payload'])!=a['payload_sha256'] or c['checked_at']!=a['finished_at']:raise Rejected('Resource not newly checked')
                if row['fetched_at'] is not None and instant(row['fetched_at'])>instant(c['checked_at']):raise Rejected('Future evidence')
            elif c['origin']=='inventory':
                if old is None or row!=record(old) or c['checked_at']!=old['fetched_at']:raise Rejected('Unqueried resource altered')
            else:raise Rejected('Invalid data origin')
        elif c['status']=='pending':
            if c['reason'] not in PENDING_REASONS:raise Rejected('Invalid pending reason')
            if c['reason']=='restricted' and m['plan']['deferred'].get(str(ident))!='restricted':raise Rejected('Restriction not declared in the plan')
            if c['origin']=='inventory':
                if old is None or row!=record(old) or c['evidence']!=refs[pair]:raise Rejected('Pending must keep the previous original')
            elif c['origin']=='none':
                if old is not None:raise Rejected('A previous original was dropped')
                if row is not None or c['evidence'] is not None:raise Rejected('Pending resource without data carries data')
            else:raise Rejected('Invalid pending origin')
            if c['checked_at'] is not None and (a is None or c['checked_at']!=a['finished_at']):raise Rejected('Invalid pending check time')
        elif c['status']=='not_applicable':
            ev=raw_records.get((ident,'event'));status=(importar.evento_de(ev['payload']).get('status') or {}).get('type') if ev else calendar[ident][1]['status']
            if key not in SECTIONS and not (key=='event' and ev is None):raise Rejected('Not applicable invalid resource')
            if status not in api.PENDIENTES and status!='inprogress':raise Rejected('Not applicable invalid phase')
            if c['evidence'] is not None or c['origin']!='phase' or c['reason'] is not None or row is not None or old is not None:raise Rejected('Invalid phase proof')
        else:raise Rejected('Unknown coverage status')
    if seen!=required:raise Rejected('Resource coverage missing')
    # Nothing outside the coverage: every raw row is covered by a data or a kept pending entry.
    covered={(c['event_id'],c['resource']) for c in coverage if c['evidence'] is not None and c['resource']!='calendar'}
    if set(raw_records)!=covered:raise Rejected('Raw resource outside coverage')
    if with_comments:
        _verify_group_comments(m['plan']['comments'],rows['comments.jsonl'],baseline,expected,current,calendar,
                               attempts,latest,m['plan']['planned'],detail_rounds,listed,raw_records,
                               dynamic_policy=dynamic_policy)
    return m,decoded


def _verify_group_comments(summary,records,baseline,expected,current,calendar,attempts,latest,planned,detail_rounds,
                           listed,raw_records,*,dynamic_policy=False):
    """Verify the optional Spanish narrative backfill against the same job's scope and budget."""
    from persistencia.comentarios import validar_respuesta
    from acquisition.group_plan import matches as planned_matches
    previous={item['event_id']:item for item in baseline.get('comments',[])}
    # Do not infer comment eligibility solely from the normalized calendar: it can still show the old event status
    # when the latest round listing has already marked a match finished but the detail request was deferred by budget.
    finished=_group_comment_finished(current,expected,raw_records,listed,latest,planned)
    round_by_event={ident:item['round'] for ident,item in planned_matches(baseline).items()}
    valid_prior={ident for ident in finished if ident in previous and previous[ident]['spanish_complete'] is True}
    review_due={item['id'] for item in planned if item['reason'] in ('finished','changed')}
    eligible=(finished-valid_prior)|(finished&review_due)
    by_event={}
    attempts_by_path={}
    for attempt in attempts:
        if re.fullmatch(r'/event/[1-9][0-9]{0,11}/comments/es-la',attempt['path']):
            attempts_by_path.setdefault(attempt['path'],[]).append(attempt)
    for row in records:
        if (not isinstance(row,dict) or set(row)!={'event_id','status','attempt_numbers','raw_b64','raw_sha256',
                                                   'captured_at','reason','transport_report'}
                or type(row['event_id']) is not int or row['event_id'] in by_event):
            raise Rejected('Invalid comments evidence row')
        by_event[row['event_id']]=row
    if set(by_event)!={int(path.split('/')[2]) for path in attempts_by_path}:
        raise Rejected('Comments attempts/evidence mismatch')
    if baseline.get('comments_enabled') is not True and by_event:
        raise Rejected('Comments requested without migrated storage')
    captured=[];failed=[]
    for ident,row in sorted(by_event.items()):
        path=f'/event/{ident}/comments/es-la'
        attempts_for_event=attempts_by_path[path]
        if ident not in eligible:raise Rejected('Comments attempt is not due or outside finished Group 2')
        if ident not in expected or not expected[ident]['current'] or ident not in finished:
            raise Rejected('Comments event outside current finished inventory')
        if not dynamic_policy and detail_rounds is not None and round_by_event.get(ident) not in detail_rounds:
            raise Rejected('Comments event outside configured detail rounds')
        if row['attempt_numbers']!=[item['number'] for item in attempts_for_event]:
            raise Rejected('Comments attempt numbers mismatch')
        last=attempts_for_event[-1]
        if row['status']!=last['status'] or row['captured_at']!=last['finished_at']:
            raise Rejected('Comments last attempt mismatch')
        report=row['transport_report']
        if (not isinstance(report,dict) or set(report)!={'path','attempt_numbers','logical_requests','physical_sends'}
                or report['path']!=path or report['attempt_numbers']!=row['attempt_numbers']
                or type(report['logical_requests']) is not int or report['logical_requests']!=1
                or type(report['physical_sends']) is not int or report['physical_sends']<0):
            raise Rejected('Comments transport report mismatch')
        raw=None
        if row['raw_b64'] is not None:
            raw=base64.b64decode(row['raw_b64'],validate=True)
            if sha(raw)!=row['raw_sha256']:
                raise Rejected('Comments response hash mismatch')
        elif row['raw_sha256'] is not None:
            raise Rejected('Comments response hash without body')
        valid=False;reason=row['reason']
        if last['status']==200 and raw is not None:
            try:
                payload=parse(raw)
            except (Rejected,ValueError,UnicodeError):
                if last['payload_sha256'] is not None:
                    raise Rejected('Comments response/attempt mismatch')
                reason='parse'
            else:
                if not isinstance(payload,(dict,list)):
                    if last['payload_sha256'] is not None:
                        raise Rejected('Comments response/attempt mismatch')
                    reason='parse'
                elif fingerprint(payload)!=last['payload_sha256']:
                    raise Rejected('Comments response/attempt mismatch')
                else:
                    try:
                        validar_respuesta(raw)
                        valid=True
                    except (ValueError,UnicodeError):
                        reason='parse'
        if valid:
            if reason is not None:raise Rejected('Valid comments response marked failed')
            captured.append(ident)
        else:
            if not isinstance(reason,str) or reason not in ('empty','not_found','parse','error','budget'):
                raise Rejected('Invalid comments failure reason')
            failed.append(dict(id=ident,reason=reason))
    expected_summary=dict(new=sorted(captured),kept=sorted(valid_prior-review_due),
                          pending=sorted(eligible-set(by_event)),failed=sorted(failed,key=lambda item:item['id']))
    if summary!=expected_summary:
        raise Rejected('Comments coverage does not match attempts and inventory')
