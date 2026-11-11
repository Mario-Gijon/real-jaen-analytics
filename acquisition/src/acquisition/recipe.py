"""Fixed recipes sealed from the SDK cache/export: sofascore-pilot-16599363-v1 (round 1 calendar and match 16599363)
and sofascore-group2-2627-v1 (the whole Group 2 with partial coverage; plan in acquisition/group_plan.py).

Runs inside the acquisition service (acquisition/job.py). No sporting SQLite access: the inventory comes from the
backend with the request. producer_rules() is the fingerprint the backend must have approved
(productores-adquisicion.json).
"""
from datetime import datetime
import base64
import json
import os
from pathlib import Path
import re
import shutil

from .transport import Stopped, iso
from .profile import sync_dir
from persistencia.bundle import (MAX_COMMENTS_FILE, MAX_LINE, PATHS, PILOT, SECTIONS, check_inventory, encode,
                                 fingerprint, record, sha, verify)

REVISION='611e436a1528b0f487aa56a045f023e8ae8ae098'

def producer_rules():
    names=('recipe.py','group_plan.py','transport.py','sdk_adapter.py','job.py',
           '../persistencia/bundle.py','../persistencia/comentarios.py')
    sources={n:sha((Path(__file__).parent/n).resolve().read_bytes()) for n in names}
    return fingerprint(dict(sources=sources,scraper=REVISION,policy=dict(window_s=259200,interval_s=21600)))

def seed_cache(store,baseline):
    """Explicit initialization from backend-selected evidence, not an empty-profile fallback."""
    from sofascore_scraper.slices import Outcome
    expected=check_inventory(baseline);grouped={}
    for source in baseline['records']:
        row=source['record']
        if row['event_id'] not in expected or row['key']=='listing':continue
        if row['fetched_at'] is None:raise Stopped('Raw evidence without provenance needs explicit initialization')
        grouped.setdefault(row['event_id'],{})[row['key']]=Outcome('ok',data=row['payload'],fetched_at=datetime.fromisoformat(row['fetched_at']))
    with store.lease('writer',purpose='explicit-test-cache-initialization'):
        for ident,outcomes in grouped.items():store.events.put(ident,outcomes)

class RoundListing:
    def __init__(self,store,baseline):self.store,self.baseline=store,baseline
    async def run(self,item,get,write):
        from sofascore_scraper.services.pipeline import ItemResult
        from sofascore_scraper.services.listing import ScheduleLister, round_is_complete
        response=await get(PATHS[0],retries=1)
        if response.status!='ok':return ItemResult(item,'failed',response.reason,event=response)
        data=response.data
        if not isinstance(data,dict) or not isinstance(data.get('events'),list):raise Stopped('Malformed round')
        expected=check_inventory(self.baseline)
        scoped=[e for e in data['events'] if e.get('tournament',{}).get('id')==94191]
        if PILOT not in {e.get('id') for e in scoped}:raise Stopped('Pilot missing from its verified round')
        if any(e.get('id') not in expected for e in scoped):raise Stopped('New event: another preflight required')
        import importar
        prior={(s['record']['event_id'],s['record']['key']):s['record'] for s in self.baseline['records']}
        for event in scoped:
            if event['id']==PILOT:continue
            original=prior.get((event['id'],'event'))
            if original is None or fingerprint(importar.rasgos_evento(original['payload']))!=fingerprint(importar.rasgos_evento(event)):
                raise Stopped('Round correction outside fixed pilot: another preflight required')
        lister=ScheduleLister(self.store,only_finished=False,concurrency=1)
        await write(lambda:lister.save_page(17073,97382,'round_1',data,meta={'complete':round_is_complete(data)},fetched_at=response.fetched_at))
        return ItemResult(item,'ok')

def collect(store,transport,baseline,output,*,cancelled=lambda:False):
    """One targeted pilot, not a historical rotation. Caller explicitly owns the transport."""
    from sofascore_scraper.services.pipeline import FetchPipeline
    from sofascore_scraper.services.planning import WorkItem
    from sofascore_scraper.store import Ref
    expected=check_inventory(baseline)
    if PILOT not in expected or not expected[PILOT]['current']:raise Stopped('Pilot is not current in this inventory')
    output=Path(output)
    if output.exists():
        body=output.read_bytes();descriptor=dict(bytes=len(body),sha256=sha(body))
        verify(body,descriptor,baseline,context=(transport.context['job_id'],transport.context['request_id']),expected_rules=producer_rules(),test_transport=transport.mode=='loopback-fixture')
        return descriptor # Exact repeat of a sealed job, no network or writes.
    required=3*len(encode(baseline))+64*1024*1024
    if shutil.disk_usage(output.parent).free<required:raise Stopped('Insufficient space before backup/download')
    results=[]
    with store.lease('writer',purpose='fixed-pilot-download-and-export'):
        backup=store.backup.create(scope='all')
        if not store.backup.verify(backup.name).ok:raise Stopped('Cache backup verification failed')
        # Preserve even partial new evidence before any later run can overwrite cache files.
        async def run():
            pipeline=FetchPipeline(store,client=transport,concurrency=1,selection=SECTIONS,listing=RoundListing(store,baseline))
            listing=WorkItem(Ref.season(17073,97382),'listing',(('pilot_round_1',''),),None,'fixed pilot round')
            first=await pipeline.run([listing],cancelled=cancelled)
            if first.failed or first.cancelled or first.skipped or cancelled():raise Stopped('Calendar acquisition incomplete')
            detail=WorkItem(Ref.event(PILOT),'refill',tuple((k,'') for k in SECTIONS),'football','explicit pilot authorization')
            second=await pipeline.run([detail],cancelled=cancelled)
            results.extend(second.results)
            if second.failed or second.cancelled or second.skipped or cancelled():raise Stopped('Pilot acquisition incomplete')
            if any(o.status!='ok' for result in second.results for o in result.slices.values()):raise Stopped('Sporting resource incomplete; no bundle publication')
        import asyncio
        try:
            asyncio.run(run())
            body=assemble(store,baseline,transport,output.parent)
            descriptor=dict(bytes=len(body),sha256=sha(body))
            verify(body,descriptor,baseline,context=(transport.context['job_id'],transport.context['request_id']),
                   expected_rules=producer_rules(),test_transport=transport.mode=='loopback-fixture')
        except BaseException:
            # Preserve partial evidence; never turn it into a publishable bundle.
            partial=store.backup.create(scope='all')
            if not store.backup.verify(partial.name).ok:raise Stopped('Partial cache backup failed; acquisition stopped')
            raise
        # Seal only after verified post-acquisition backup. A failed maintenance step stops here.
        complete=store.backup.create(scope='all')
        if not store.backup.verify(complete.name).ok:raise Stopped('Post-acquisition cache backup failed')
        with output.open('xb') as f:f.write(body);f.flush();os.fsync(f.fileno())
        output.chmod(0o400);sync_dir(output.parent)
        return descriptor


def assemble(store,baseline,transport,workspace):
    from sofascore_scraper.services.export import ExportService,DatasetSpec,DatasetFilter
    from sofascore_scraper.store import EventQuery,Scope
    import tempfile
    expected=check_inventory(baseline)
    ids=tuple(sorted(expected));active={i for i,e in expected.items() if e['current']}
    prior={(s['record']['event_id'],s['record']['key']):s['record'] for s in baseline['records']}
    records={};calendar={};files={};coverage=[]
    with tempfile.TemporaryDirectory(prefix='pilot-export-',dir=workspace) as tmp:
        tmp=Path(tmp)
        normalized=tmp/'events.jsonl'
        report=ExportService(store).export_dataset(DatasetSpec(dataset='events',schema='normalized',format='jsonl',filter=DatasetFilter(event_ids=ids)),normalized)
        if report.skipped:raise Stopped('Calendar export omitted evidence')
        calendar={row['id']:row for row in (json.loads(line) for line in normalized.read_bytes().splitlines())}
        raw=tmp/'raw.jsonl'
        report=store.export.raw(EventQuery(scope=Scope(event_ids=ids)),raw,keys=('event',*SECTIONS),fmt='jsonl')
        if report.skipped:raise Stopped('Raw export omitted evidence')
        for row in (json.loads(line) for line in raw.read_bytes().splitlines()):
            pair=row['event_id'],row['key']
            if row['event_id'] not in active:continue
            if row['event_id']!=PILOT:
                original=prior.get(pair)
                if original is None or original['payload']!=row['payload']:raise Stopped('Unqueried cache differs from its inventory')
                row=original # Keep exact original timestamp/spelling, never regenerate its freshness.
            records[pair]=row
    for ident in ids:
        if ident not in calendar:
            old=prior.get((ident,'listing'))
            if old is None:raise Stopped('Inventory event missing from cache/calendar')
            calendar[ident]=old['payload'] # Null observed_at remains null with exact provenance.
    if set(calendar)!=set(ids):raise Stopped('Unexpected calendar expansion')
    successful=[a for a in transport.state['attempts'] if a['status']==200]
    latest={a['path']:a for a in successful}
    files['provenance.jsonl']=b''.join(encode(dict(number=a['number'],path=a['path'],received_at=a['finished_at'],body_b64=base64.b64encode((transport.root/(transport.context['job_id']+'-responses')/(str(a['number'])+'.body')).read_bytes()).decode()))+b'\n' for a in successful)
    files['calendar.jsonl']=b''.join(encode(calendar[i])+b'\n' for i in ids)
    cal_lines={i:n for n,i in enumerate(ids,1)}
    refs={}
    for team in baseline['teams']:
        name=f'team_{team}.jsonl';rows=[]
        for pair,row in sorted(records.items()):
            e=expected[pair[0]]
            if team in (e['home'],e['away']):
                rows.append(row);refs.setdefault(pair,dict(file=name,line=len(rows)))
        files[name]=b''.join(encode(row)+b'\n' for row in rows)
    for ident in sorted(active):
        event=records.get((ident,'event'))
        from sofascore_scraper.status import classify_status
        from sofascore_scraper.services.planning import phase_of
        from sofascore_scraper.services.pipeline import body_state
        phase=phase_of(classify_status(event['payload'] if event else calendar[ident]).value)
        for key in ('calendar','event',*SECTIONS):
            if key=='calendar':
                at=(calendar[ident].get('quality') or {}).get('observed_at_utc')
                checked=latest[PATHS[1]]['finished_at'] if ident==PILOT else at
                coverage.append(dict(event_id=ident,resource=key,status='data',origin='attempt' if ident==PILOT else 'inventory',
                                     evidence=dict(file='calendar.jsonl',line=cal_lines[ident]),checked_at=checked))
                continue
            row=records.get((ident,key))
            if (key in SECTIONS and phase in ('pre','live')) or (key=='event' and row is None and phase=='pre'):
                coverage.append(dict(event_id=ident,resource=key,status='not_applicable',origin='phase',evidence=None,checked_at=None));continue
            if row is None or (key in SECTIONS and body_state(key,row['payload'])!='data'):raise Stopped('Resource coverage incomplete')
            coverage.append(dict(event_id=ident,resource=key,status='data',origin='attempt' if ident==PILOT else 'inventory',
                                 evidence=refs[ident,key],checked_at=latest[f'/event/{PILOT}'+('' if key=='event' else '/'+key)]['finished_at'] if ident==PILOT else row['fetched_at']))
    m=dict(schema=2,contract='pilot-v2',**{k:transport.context[k] for k in ('job_id','request_id','inventory_sha256')},
           recipe_id='sofascore-pilot-16599363-v1',provider_revision=REVISION,transport=transport.mode,
           generation_id=baseline['generation_id'],rules_sha256=producer_rules(),policy=dict(window_s=259200,interval_s=21600),
           started_at=transport.state['started_at'],finished_at=iso(transport.clock()),
           retained=sorted(i for i,e in expected.items() if not e['current']),attempts=transport.state['attempts'],errors=[],coverage=coverage,
           files=[dict(name=n,kind=('calendar' if n=='calendar.jsonl' else 'provenance') if not n.startswith('team_') else 'own_export',team_external_id=int(n[5:-6]) if n.startswith('team_') else None,bytes=len(raw),sha256=sha(raw)) for n,raw in sorted(files.items())])
    return encode(dict(manifest=m,files={n:base64.b64encode(raw).decode() for n,raw in files.items()}))


# --- Group 2 recipe (sofascore-group2-2627-v1) -----------------------------------------------------------------
# Same collection as the pilot (seeded cache, backups, sealing, re-verification without network); the plan decides
# what to ask (acquisition/group_plan.py) and the bundle carries partial coverage (persistencia/bundle.py,
# _verify_group). Design: docs/diseno-receta-grupo2.md.

class GroupScope:
    """Allowlist of one group job: the rounds of its plan, then the event paths of the matches it decided to consult
    (inventory matches or matches of the scope seen in one of its listed rounds). Nothing else is ever sent."""
    def __init__(self,rounds):
        from persistencia.bundle import round_path
        self.paths={round_path(n) for n in rounds}
    def admit(self,ident,keys):
        from persistencia.bundle import event_path
        self.paths.update(event_path(ident,k) for k in ('event',*keys))
    def admit_comments(self, ident, known_ids):
        if type(ident) is not int or ident not in known_ids:
            raise Stopped('Comments route outside the published Group 2 inventory')
        self.paths.add(f'/event/{ident}/comments/es-la')
    def permits(self,path):
        return path in self.paths

class GroupListing:
    """Lists the planned rounds one by one. A round that fails is not fatal: its matches keep their published calendar
    and are not consulted because of it. Rows outside this group and season are kept in the page, never used."""
    def __init__(self,store,baseline,rounds):
        self.store,self.baseline,self.rounds=store,baseline,rounds
        self.listed={};self.failed={}
    async def run(self,item,get,write):
        from sofascore_scraper.services.pipeline import ItemResult
        from sofascore_scraper.services.listing import ScheduleLister, round_is_complete
        from persistencia.bundle import round_path
        from .group_plan import in_scope
        lister=ScheduleLister(self.store,only_finished=False,concurrency=1)
        for number in self.rounds:
            response=await get(round_path(number),retries=1)
            data=response.data
            if response.status!='ok' or not isinstance(data,dict) or not isinstance(data.get('events'),list):
                self.failed[number]=response.reason or 'parse';continue
            await write(lambda data=data,number=number,at=response.fetched_at:lister.save_page(
                17073,97382,f'round_{number}',data,meta={'complete':round_is_complete(data)},fetched_at=at))
            for row in data['events']:
                if isinstance(row,dict) and in_scope(row,self.baseline['teams']):self.listed[row['id']]=(row,number)
        return ItemResult(item,'ok')

def refused(transport):
    return bool(transport.retry.get('_blocked')) or transport.breaker.tripped


def comments_file_bytes(evidence):
    """Serialized comments JSONL size, including the newline required for every record."""
    return sum(len(encode(row)) + 1 for row in evidence)


def comments_capacity_for_next(evidence):
    """Reserve one full verifier-allowed line before sending, so a later response cannot overflow the file cap.

    A single response whose encoded evidence line exceeds MAX_LINE cannot be represented by the current bundle
    contract; its transport body remains in the durable Acquisition response store and that job must stop unsealed.
    """
    return comments_file_bytes(evidence) + MAX_LINE + 1 <= MAX_COMMENTS_FILE


def pending_comments(candidates, attempted):
    """Comment candidates not yet charged to the durable transport ledger."""
    return sorted(set(candidates) - set(attempted))


def _group_cached_events(store, ids, workspace):
    """Read the current SDK event cache for known inventory IDs; no provider request is made here."""
    import tempfile
    from sofascore_scraper.store import EventQuery, Scope
    with tempfile.TemporaryDirectory(prefix='group-comments-state-', dir=workspace) as tmp:
        path=Path(tmp)/'events.jsonl'
        report=store.export.raw(EventQuery(scope=Scope(event_ids=tuple(ids))),path,keys=('event',),fmt='jsonl')
        if report.skipped:raise Stopped('Could not determine final-match comment scope from the local cache')
        return {row['event_id']:row['payload'] for row in
                (json.loads(line) for line in path.read_bytes().splitlines()) if row.get('key')=='event'}


async def _collect_group_comments(store,transport,scope,baseline,output,*,listing,planned,attempt_limit,
                                 detail_rounds):
    """Backfill one request at a time inside the existing Group 2 job and its remaining shared budget."""
    from persistencia.bundle import sha
    from sofascore_scraper.status import classify_status
    from sofascore_scraper.services.planning import phase_of
    from .group_plan import SEND_RESERVE, matches as planned_matches, round_of

    expected=check_inventory(baseline)
    ids={ident for ident,event in expected.items() if event['current']}
    match_plan=planned_matches(baseline)
    latest_event_attempt={}
    for attempt in transport.state['attempts']:
        if attempt['path'].startswith('/event/') and attempt['path'].count('/')==2:
            latest_event_attempt[int(attempt['path'].split('/')[2])]=attempt
    cached=_group_cached_events(store,ids,output.parent)
    prior={item['event_id']:item for item in baseline.get('comments',[])}
    planned_by_id={item['id']:item for item in planned}
    planned_ids=set(planned_by_id)
    review_due={ident for ident,item in planned_by_id.items() if item['reason'] in ('finished','changed')}
    final=set()
    for ident in ids:
        payload=cached.get(ident)
        listed_row=listing.listed.get(ident)
        if (ident in planned_ids and payload is not None
                and (latest_event_attempt.get(ident) or {}).get('status')==200
                and (latest_event_attempt.get(ident) or {}).get('payload_sha256') is not None):
            is_final=phase_of(classify_status(payload).value)=='post'
        elif listed_row is not None:
            is_final=phase_of(classify_status(listed_row[0]).value)=='post'
        elif payload is not None:
            is_final=phase_of(classify_status(payload).value)=='post'
        else:
            is_final=expected[ident]['status']=='finished'
        if is_final:final.add(ident)
    complete={ident for ident in final if ident in prior and prior[ident]['spanish_complete'] is True}
    candidates=(final-complete)|(final&review_due)
    newly_finished={ident for ident in candidates if expected[ident]['status']!='finished'}
    candidates=sorted(candidates,key=lambda ident:(0 if ident in newly_finished else 1 if ident not in complete else 2,
                                                    expected[ident].get('start') or float('inf'),ident))
    if baseline.get('comments_enabled') is not True:
        return dict(new=[],kept=sorted(complete-review_due),pending=sorted(candidates),failed=[]),[]
    attempts_by_event={};captured={};failed={};evidence=[]
    for ident in candidates:
        round_number=round_of(match_plan[ident])
        if detail_rounds is not None and round_number not in detail_rounds:
            continue
        if not comments_capacity_for_next(evidence):
            # No request is charged. Remaining candidates stay pending in the sealed coverage for a later update.
            break
        if len(transport.state['attempts'])>=attempt_limit or transport.remaining_sends()<SEND_RESERVE:
            break
        route=f'/event/{ident}/comments/es-la'
        scope.admit_comments(ident,ids)
        prior_numbers={a['number'] for a in transport.state['attempts']}
        sends_mark=len(transport.adapter.sends)
        try:
            outcome=await transport.get(route)
        except Stopped:
            outcome=None
        new_attempts=[a for a in transport.state['attempts'] if a['number'] not in prior_numbers]
        if not new_attempts:
            if outcome is None:
                # A stopped transport can refuse before charging a logical attempt (for example after an earlier
                # provider refusal). Stop the comments pass and keep the already completed sporting work publishable.
                transport.comments_stop_after_sport=True
                transport.comments_stop_reason=transport.adapter.stopped
                break
            # A durable backoff or exhausted budget costs nothing in this update; keep the match for a later one.
            continue
        attempts_by_event[ident]=new_attempts
        latest=new_attempts[-1]
        response_path=transport.root/(transport.context['job_id']+'-responses')/(str(latest['number'])+'.body')
        raw=response_path.read_bytes() if response_path.is_file() else None
        valid=False;reason=None
        if latest['status']==200 and raw is not None:
            try:
                from persistencia.comentarios import validar_respuesta
                validar_respuesta(raw)
                valid=True
            except (ValueError,UnicodeError):
                reason='parse'
        if valid:
            captured[ident]=True
        else:
            stored_reason=(transport.retry.get(route) or {}).get('reason')
            reason=reason or {'404':'not_found','empty':'empty','parse':'parse','budget':'budget'}.get(stored_reason,'error')
            failed[ident]=reason
        physical=transport.adapter.sends[sends_mark:]
        evidence_row=dict(event_id=ident,status=latest['status'],
                          attempt_numbers=[item['number'] for item in new_attempts],
                          raw_b64=base64.b64encode(raw).decode('ascii') if raw is not None else None,
                          raw_sha256=sha(raw) if raw is not None else None,
                          captured_at=latest['finished_at'],reason=None if valid else reason,
                          transport_report=dict(path=route,attempt_numbers=[item['number'] for item in new_attempts],
                                               logical_requests=1,physical_sends=sum(1 for item in physical if item.get('sent'))))
        if len(encode(evidence_row)) > MAX_LINE:
            # The exact response is already durable under transport.root; don't discard it or emit a bundle that
            # cannot carry its evidence under the existing JSONL line contract.
            raise Stopped('A single comments response exceeds the Group JSONL line limit; durable evidence retained, bundle not sealed')
        evidence.append(evidence_row)
        if outcome is None:
            # The sporting work is complete. Preserve the adapter's refusal, stop every further send, and seal its
            # valid sporting evidence with this comments request marked failed.
            transport.comments_stop_after_sport=True
            transport.comments_stop_reason=transport.adapter.stopped
            break
    pending=pending_comments(candidates,attempts_by_event)
    summary=dict(new=sorted(captured),kept=sorted(complete-review_due),pending=pending,
                 failed=[dict(id=ident,reason=reason) for ident,reason in sorted(failed.items())])
    return summary,evidence

def collect_group(store,transport,scope,baseline,output,*,now,cancelled=lambda:False,budget=None,listed_at=None,
                  limits=None,detail_rounds=None):
    """One manual update of the group. A refusal or the breaker stops it without a bundle; an exhausted budget or an
    unanswered resource leaves that resource pending and the rest is sealed. limits: dict(attempts, sends) of this
    job (the production maxima unless the installation lowers them); detail_rounds: the only rounds whose matches may
    be consulted (None: any). Both are declared in the manifest and checked by the verifier."""
    from sofascore_scraper.services.pipeline import FetchPipeline
    from sofascore_scraper.services.planning import WorkItem
    from sofascore_scraper.store import Ref
    from persistencia.bundle import verify as verify_bundle
    from .group_plan import MAX_ATTEMPTS, MAX_SENDS, PLANNED_ATTEMPTS, SEND_RESERVE, plan_matches, rounds_to_list
    check_inventory(baseline)
    limits=dict(limits or dict(attempts=MAX_ATTEMPTS,sends=MAX_SENDS))
    output=Path(output)
    context=(transport.context['job_id'],transport.context['request_id']);test=transport.mode=='loopback-fixture'
    if output.exists():
        body=output.read_bytes();descriptor=dict(bytes=len(body),sha256=sha(body))
        verify_bundle(body,descriptor,baseline,context=context,expected_rules=producer_rules(),test_transport=test)
        return descriptor
    required=3*len(encode(baseline))+64*1024*1024
    if shutil.disk_usage(output.parent).free<required:raise Stopped('Insufficient space before backup/download')
    near,far=rounds_to_list(baseline,now,listed_at=listed_at)
    rounds=sorted(set(near)|set(far))
    state={}
    with store.lease('writer',purpose='group-download-and-export'):
        backup=store.backup.create(scope='all')
        if not store.backup.verify(backup.name).ok:raise Stopped('Cache backup verification failed')
        async def run():
            listing=GroupListing(store,baseline,rounds)
            pipeline=FetchPipeline(store,client=transport,concurrency=1,selection=SECTIONS,listing=listing)
            item=WorkItem(Ref.season(17073,97382),'listing',(('group_rounds',''),),None,'group rounds')
            await pipeline.run([item],cancelled=cancelled)
            if cancelled():raise InterruptedError('cancelled')
            if refused(transport):raise Stopped('Refusal/breaker while listing: nothing is published')
            listed={i:row for i,(row,_) in listing.listed.items()}
            attempt_limit=min(PLANNED_ATTEMPTS,limits['attempts'],
                              budget if budget is not None else PLANNED_ATTEMPTS)
            attempt_limit=max(0,attempt_limit)
            remaining_attempts=max(0,attempt_limit-len(transport.state['attempts']))
            remaining_sends=max(0,transport.remaining_sends()-SEND_RESERVE)
            essential_budget=min(remaining_attempts,remaining_sends)
            essential,deferred=plan_matches(
                baseline,listed,now,budget=essential_budget,detail_rounds=detail_rounds,
                reasons=('pending','finished','changed'))
            for p in essential:scope.admit(p['id'],p['keys'])
            items=[WorkItem(Ref.event(p['id']),'refill',tuple((k,'') for k in p['keys']),'football',p['reason']) for p in essential]
            if items:await pipeline.run(items,cancelled=cancelled)
            if cancelled():raise InterruptedError('cancelled')
            if refused(transport):raise Stopped('Refusal/breaker while consulting: nothing is published')

            comments_summary,comments_evidence=await _collect_group_comments(
                store,transport,scope,baseline,output,listing=listing,planned=essential,
                attempt_limit=attempt_limit,detail_rounds=detail_rounds)
            if cancelled():raise InterruptedError('cancelled')

            # Routine refreshes are deliberately last: comments for new finals and the historical backlog use the
            # shared remaining budget first. If a comments refusal stopped the adapter, preserve the valid sporting
            # work already completed and defer every routine refresh without making another request.
            refresh_candidates,refresh_capped=plan_matches(
                baseline,listed,now,budget=PLANNED_ATTEMPTS,detail_rounds=detail_rounds,reasons=('refresh',))
            deferred.update(refresh_capped)
            refresh=[]
            logical_left=max(0,attempt_limit-len(transport.state['attempts']))
            sends_left=max(0,transport.remaining_sends()-SEND_RESERVE)
            if transport.comments_stop_after_sport:
                deferred.update({p['id']:'budget' for p in refresh_candidates})
            else:
                for candidate in refresh_candidates:
                    if candidate['cost']>logical_left or candidate['cost']>sends_left:
                        deferred[candidate['id']]='budget'
                        continue
                    logical_left-=candidate['cost'];sends_left-=candidate['cost']
                    refresh.append(candidate)
                for p in refresh:scope.admit(p['id'],p['keys'])
                refresh_items=[WorkItem(Ref.event(p['id']),'refill',tuple((k,'') for k in p['keys']),'football',p['reason'])
                               for p in refresh]
                if refresh_items:await pipeline.run(refresh_items,cancelled=cancelled)
                if cancelled():raise InterruptedError('cancelled')
                if refused(transport):raise Stopped('Refusal/breaker while refreshing: nothing is published')

            planned=essential+refresh
            state.update(listing=listing,planned=planned,deferred=deferred,near=near,far=far,limits=limits,
                         detail_rounds=detail_rounds,comments_summary=comments_summary,
                         comments_evidence=comments_evidence)
        import asyncio
        try:
            asyncio.run(run())
            body=assemble_group(store,baseline,transport,output.parent,now=now,**state)
            descriptor=dict(bytes=len(body),sha256=sha(body))
            verify_bundle(body,descriptor,baseline,context=context,expected_rules=producer_rules(),test_transport=test)
        except BaseException:
            partial=store.backup.create(scope='all')
            if not store.backup.verify(partial.name).ok:raise Stopped('Partial cache backup failed; acquisition stopped')
            raise
        complete=store.backup.create(scope='all')
        if not store.backup.verify(complete.name).ok:raise Stopped('Post-acquisition cache backup failed')
        with output.open('xb') as f:f.write(body);f.flush();os.fsync(f.fileno())
        output.chmod(0o400);sync_dir(output.parent)
        return descriptor

def assemble_group(store,baseline,transport,workspace,*,now,listing,planned,deferred,near,far,limits,detail_rounds,
                   comments_summary,comments_evidence):
    """The bundle of a group job. Per resource: new data (a successful attempt with data), the previous original kept
    (not consulted, or consulted without data), pending (no data, with its reason) or not applicable (match not
    played yet). Never an empty answer as data, never a previous original dropped."""
    from sofascore_scraper.services.export import ExportService,DatasetSpec,DatasetFilter
    from sofascore_scraper.services.pipeline import body_state
    from sofascore_scraper.status import classify_status
    from sofascore_scraper.services.planning import phase_of
    from sofascore_scraper.store import EventQuery,Scope
    from persistencia.bundle import event_path, pending_reason, round_path
    from .group_plan import new_matches
    import tempfile
    expected=check_inventory(baseline)
    listed=listing.listed
    # The listing may reveal a new ID (often a postponed match's replacement). Keep it in plan.new_matches for
    # review, but only refresh the IDs already present in the published inventory.
    ids=tuple(sorted(expected))
    current=[i for i in ids if expected[i]['current']]
    prior={(s['record']['event_id'],s['record']['key']):s['record'] for s in baseline['records']}
    with tempfile.TemporaryDirectory(prefix='group-export-',dir=workspace) as tmp:
        tmp=Path(tmp)
        normalized=tmp/'events.jsonl'
        report=ExportService(store).export_dataset(DatasetSpec(dataset='events',schema='normalized',format='jsonl',filter=DatasetFilter(event_ids=ids)),normalized)
        if report.skipped:raise Stopped('Calendar export omitted evidence')
        calendar={row['id']:row for row in (json.loads(line) for line in normalized.read_bytes().splitlines())}
        raw=tmp/'raw.jsonl'
        report=store.export.raw(EventQuery(scope=Scope(event_ids=ids)),raw,keys=('event',*SECTIONS),fmt='jsonl')
        if report.skipped:raise Stopped('Raw export omitted evidence')
        stored={(r['event_id'],r['key']):r for r in (json.loads(line) for line in raw.read_bytes().splitlines())}
    for ident in ids:
        if ident not in calendar:
            old=prior.get((ident,'listing'))
            if old is None:raise Stopped('Match missing from cache/calendar')
            calendar[ident]=old['payload']
    attempts=transport.state['attempts'];latest={a['path']:a for a in attempts}
    planned_keys={p['id']:('event',*p['keys']) for p in planned}
    participants={i:((expected[i]['home'],expected[i]['away']) if i in expected else
                     ((listed[i][0].get('homeTeam') or {}).get('id'),(listed[i][0].get('awayTeam') or {}).get('id'))) for i in ids}
    rows={};drafts=[];refreshed=set()
    for ident in current:
        if ident in listed and (latest.get(round_path(listed[ident][1])) or {}).get('status')==200:
            drafts.append(dict(event_id=ident,resource='calendar',status='data',origin='attempt',checked_at=latest[round_path(listed[ident][1])]['finished_at'],reason=None))
        else:
            drafts.append(dict(event_id=ident,resource='calendar',status='data',origin='inventory',checked_at=(calendar[ident].get('quality') or {}).get('observed_at_utc'),reason=None))
        for key in ('event',*SECTIONS):
            pair=(ident,key);path=event_path(ident,key);a=latest.get(path);old=prior.get(pair);new=stored.get(pair)
            fresh=(a is not None and a['status']==200 and new is not None and fingerprint(new['payload'])==a['payload_sha256']
                   and (key=='event' or body_state(key,new['payload'])=='data'))
            if fresh:
                if key=='event':refreshed.add(ident)
                rows[pair]=record(new);drafts.append(dict(event_id=ident,resource=key,status='data',origin='attempt',checked_at=a['finished_at'],reason=None))
            elif a is not None:
                # Asked now without data: pending; a previous original, if any, is kept as it was.
                why=pending_reason((transport.retry.get(path) or {}).get('reason'))
                if a['status']==200 and why=='error':why='parse'
                if old is not None:rows[pair]=record(old)
                drafts.append(dict(event_id=ident,resource=key,status='pending',origin='inventory' if old is not None else 'none',
                                   checked_at=a['finished_at'],reason=why))
            elif old is not None:
                # Not asked in this update (not due, deferred by the budget or still in a backoff): previous data kept.
                rows[pair]=record(old);drafts.append(dict(event_id=ident,resource=key,status='data',origin='inventory',checked_at=old['fetched_at'],reason=None))
            else:
                # The phase of what this update knows best: the event read now, else this update's listing (a match it
                # shows finished is due even if the event kept from before says otherwise), else the kept event.
                event=rows.get((ident,'event'))
                basis=(event['payload'] if event and (ident in refreshed or ident not in listed)
                       else listed[ident][0] if ident in listed else calendar[ident])
                phase=phase_of(classify_status(basis).value)
                if (key in SECTIONS and phase in ('pre','live')) or (key=='event' and phase=='pre'):
                    drafts.append(dict(event_id=ident,resource=key,status='not_applicable',origin='phase',checked_at=None,reason=None))
                else:
                    if path in transport.retry:
                        why=pending_reason(transport.retry[path].get('reason'))  # still in the backoff of an earlier answer
                    elif ident in deferred:
                        why=deferred[ident]  # 'budget' (did not fit) or 'restricted' (outside this installation's rounds)
                    elif key in planned_keys.get(ident,()) and latest.get(event_path(ident)) is None:
                        why='budget'  # never reached: the attempts or the time of this update ran out
                    else:
                        why='not_requested'  # not due in this update, or its event failed so its sections were not asked
                    drafts.append(dict(event_id=ident,resource=key,status='pending',origin='none',checked_at=None,reason=why))
    files={}
    successful=[a for a in attempts if a['status']==200 and
                not re.fullmatch(r'/event/[1-9][0-9]{0,11}/comments/es-la',a['path'])]
    files['provenance.jsonl']=b''.join(encode(dict(number=a['number'],path=a['path'],received_at=a['finished_at'],body_b64=base64.b64encode((transport.root/(transport.context['job_id']+'-responses')/(str(a['number'])+'.body')).read_bytes()).decode()))+b'\n' for a in successful)
    files['comments.jsonl']=b''.join(encode(row)+b'\n' for row in comments_evidence)
    files['calendar.jsonl']=b''.join(encode(calendar[i])+b'\n' for i in ids)
    lines={i:n for n,i in enumerate(ids,1)}
    refs={}
    for team in baseline['teams']:
        name=f'team_{team}.jsonl';own=[]
        for pair,row in sorted(rows.items()):
            if team in participants[pair[0]]:
                own.append(row);refs.setdefault(pair,dict(file=name,line=len(own)))
        files[name]=b''.join(encode(row)+b'\n' for row in own)
    coverage=[]
    for d in drafts:
        pair=(d['event_id'],d['resource'])
        evidence=dict(file='calendar.jsonl',line=lines[d['event_id']]) if d['resource']=='calendar' else refs.get(pair)
        coverage.append(dict(d,evidence=evidence))
    plan=dict(now=iso(now),rounds=dict(near=near,far=far,failed=sorted(listing.failed)),
              planned=[dict(id=p['id'],reason=p['reason'],keys=list(p['keys'])) for p in planned],
              deferred={str(k):v for k,v in sorted(deferred.items())},
              new_matches=new_matches(baseline,{i:row for i,(row,_) in listed.items()}),
              limits=dict(limits),detail_rounds=detail_rounds,comments=comments_summary)
    m=dict(schema=2,contract='group-v2',**{k:transport.context[k] for k in ('job_id','request_id','inventory_sha256')},
           recipe_id='sofascore-group2-2627-v1',provider_revision=REVISION,transport=transport.mode,
           generation_id=baseline['generation_id'],rules_sha256=producer_rules(),policy=dict(window_s=259200,interval_s=21600),
           started_at=transport.state['started_at'],finished_at=iso(transport.clock()),
           retained=sorted(i for i,e in expected.items() if not e['current']),attempts=attempts,errors=[],coverage=coverage,plan=plan,
           files=[dict(name=n,kind=('calendar' if n=='calendar.jsonl' else 'provenance' if n=='provenance.jsonl' else 'comments') if not n.startswith('team_') else 'own_export',team_external_id=int(n[5:-6]) if n.startswith('team_') else None,bytes=len(raw),sha256=sha(raw)) for n,raw in sorted(files.items())])
    return encode(dict(manifest=m,files={n:base64.b64encode(raw).decode() for n,raw in files.items()}))
