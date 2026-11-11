"""Transport and recipe guarantees: durable budget before each send, retries and waits, persisted refusals, redirects,
cancellation, ownership, evidence, no absence inferred from 404/empty, no sealing after a partial or failed step.
Loopback HTTP only. Runs in the acquisition test image (it needs the SDK's breaker, store and pipeline)."""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import tempfile
import unittest
import uuid

from acquisition.transport import Transport, Stopped
from persistencia.bundle import PATHS, encode, parse

class Clock:
    def __init__(self):self.value=datetime(2026,10,28,12,tzinfo=timezone.utc).timestamp()
    def __call__(self):return self.value
    async def sleep(self,seconds):self.value+=seconds;await asyncio.sleep(0)

@contextmanager
def server(routes):
    hits=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_GET(self):
            path=self.path.removeprefix('/api/v1');hits.append(path)
            actions=routes.get(path,[(404,{}, {})])
            status,headers,body=actions.pop(0) if len(actions)>1 else actions[0]
            raw=body if isinstance(body,bytes) else encode(body)
            self.send_response(status)
            for k,v in headers.items():self.send_header(k,v)
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    http=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{http.server_port}/api/v1',hits
    finally:http.shutdown();http.server_close();thread.join()

class TransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.clock=Clock()
        self.context=dict(job_id=str(uuid.uuid4()),request_id=str(uuid.uuid4()),inventory_sha256='a'*64)
    def transport(self,url,**kwargs):return Transport(self.root,loopback_url=url,clock=self.clock,sleep=self.clock.sleep,**self.context,**kwargs)
    def get(self,t,path=PATHS[1]):
        async def work():
            async with t:return await t.get(path)
        return asyncio.run(work())
    def test_default_disabled_and_unauthorized_urls(self):
        with self.assertRaises(Stopped):Transport(self.root,**self.context)
        with self.assertRaises(ValueError):self.transport('http://example.invalid/api/v1')
    def test_success_counts_before_socket_and_dedup(self):
        with server({PATHS[1]:[(200,{}, {'event':{'id':16599363}})]}) as (url,hits),self.transport(url) as t:
            original=t._request
            def proof(path):
                saved=parse(t.path.read_bytes());self.assertEqual(saved['attempts'][-1]['status'],None)
                self.assertEqual(saved['attempts'][-1]['path'],path);return original(path)
            t._request=proof
            self.get(t);self.get(t)
            self.assertEqual(hits,[PATHS[1]]);self.assertEqual(len(t.state['attempts']),1)
    def test_retry_after_503_counts_every_attempt_and_waits(self):
        with server({PATHS[1]:[(503,{'Retry-After':'60'},{}),(200,{}, {'event':{'id':16599363}})]}) as (url,hits),self.transport(url) as t:
            self.assertEqual(self.get(t).status,'ok');self.assertEqual(len(hits),2)
            a,b=t.state['attempts'];self.assertGreaterEqual(datetime.fromisoformat(b['sent_at']).timestamp()-datetime.fromisoformat(a['finished_at']).timestamp(),60)
    def test_budget_exhausted_before_next_attempt(self):
        with server({PATHS[1]:[(503,{}, {})]}) as (url,hits),self.transport(url,max_attempts=1) as t:
            with self.assertRaises(Stopped):self.get(t)
            self.assertEqual(len(hits),1)
    def test_refusal_persisted_across_new_jobs(self):
        for code in (403,429):
            with self.subTest(code=code),tempfile.TemporaryDirectory() as root:
                self.root=Path(root)
                with server({PATHS[1]:[(code,{'Retry-After':'3600'}, {})]}) as (url,hits):
                    with self.transport(url) as t:
                        with self.assertRaises(Stopped):self.get(t)
                    self.context['job_id']=str(uuid.uuid4())
                    with self.transport(url) as t:
                        with self.assertRaises(Stopped):self.get(t)
                    self.assertEqual(len(hits),1)
    def test_redirect_does_not_contact_target_or_retry(self):
        with server({PATHS[1]:[(302,{'Location':'http://example.invalid/never'}, {})]}) as (url,hits),self.transport(url) as t:
            self.assertEqual(self.get(t).status,'failed');self.assertEqual(len(hits),1)
            with self.assertRaises(Stopped):self.get(t)
            self.assertEqual(len(hits),1)
    def test_cancel_and_unplanned_path_have_zero_requests(self):
        with server({}) as (url,hits),self.transport(url,cancelled=lambda:True) as t:
            with self.assertRaises(InterruptedError):self.get(t)
            self.assertEqual(hits,[])
        with server({}) as (url,hits),self.transport(url) as t:
            with self.assertRaises(Stopped):self.get(t,'/event/1')
            self.assertEqual(hits,[])
    def test_long_retry_after_does_not_get_shortened(self):
        with server({PATHS[1]:[(503,{'Retry-After':'3600'}, {})]}) as (url,hits),self.transport(url) as t:
            with self.assertRaises(Stopped):self.get(t)
            self.assertEqual(len(hits),1)
    def test_lock_concurrency_and_interrupted_attempt_budget(self):
        with server({PATHS[1]:[(200,{}, {'event':{'id':16599363}})]}) as (url,hits):
            with self.transport(url) as t:
                with self.assertRaises(BlockingIOError):
                    with self.transport(url):pass
                t.state['attempts']=[dict(number=1,path=PATHS[1],sent_at=t.state['started_at'],finished_at=None,status=None,payload_sha256=None)]
                from acquisition.profile import atomic_json
                atomic_json(t.path,t.state)
            with self.transport(url) as t:
                self.assertEqual(t.state['attempts'][0]['status'],0)
                self.get(t);self.assertEqual(len(t.state['attempts']),2)
                self.assertEqual(len(hits),1)
    def test_journal_full_stops_before_socket_and_releases_owner(self):
        from unittest.mock import patch
        with server({}) as (url,hits):
            t=self.transport(url)
            with patch('acquisition.transport.atomic_json',side_effect=OSError('ENOSPC')):
                with self.assertRaises(OSError):
                    with t:pass
            self.assertIsNone(t.owner)
            with self.transport(url):pass
            self.assertEqual(hits,[])

    def test_response_evidence_failure_does_not_retry(self):
        from unittest.mock import patch
        real_open=Path.open
        def fail(path,*args,**kwargs):
            if path.suffix=='.body':raise OSError('ENOSPC')
            return real_open(path,*args,**kwargs)
        with server({PATHS[1]:[(200,{}, {'event':{'id':16599363}})]}) as (url,hits),self.transport(url) as t:
            with patch.object(Path,'open',fail):
                with self.assertRaises(Stopped):self.get(t)
            self.assertEqual(len(hits),1)

    def test_existing_success_is_not_queried_again_after_restart(self):
        with server({PATHS[1]:[(200,{}, {'event':{'id':16599363}})]}) as (url,hits):
            with self.transport(url) as t:self.get(t)
            with self.transport(url) as t:
                with self.assertRaises(Stopped):self.get(t)
            self.assertEqual(len(hits),1)

    def test_empty_404_and_parse_never_confirm_absence(self):
        for code,body in ((404,{}),(200,{'incidents':[]}),(200,b'invalid-json')):
            with self.subTest(code=code),tempfile.TemporaryDirectory() as root:
                self.root=Path(root)
                with server({PATHS[-1]:[(code,{},body)]}) as (url,hits),self.transport(url) as t:
                    result=self.get(t,PATHS[-1]);self.assertNotEqual(result.status,'ok')
                    with self.assertRaises(Stopped):self.get(t,PATHS[-1])
                    self.assertEqual(len(hits),1)

class SendReserve(unittest.TestCase):
    """Below the reserve of physical sends the transport schedules nothing more: in the group, an exhausted budget
    (pending 'budget', partial publication), never a refusal; nothing is sent."""
    def test_reserve_is_budget_not_refusal(self):
        class Few(Transport):
            left = 3
            def remaining_sends(self):return self.left
        clock=Clock()
        with tempfile.TemporaryDirectory() as root,server({PATHS[1]:[(200,{}, {'event':{'id':16599363}})]}) as (url,hits):
            context=dict(job_id=str(uuid.uuid4()),request_id=str(uuid.uuid4()),inventory_sha256='a'*64)
            with Few(Path(root),loopback_url=url,clock=clock,sleep=clock.sleep,limits='group',max_attempts=60,send_reserve=4,
                     allowed=lambda path:True,**context) as t:
                async def work():
                    async with t:return await t.get(PATHS[1])
                result=asyncio.run(work())
                self.assertEqual((result.status,result.reason,hits,t.state['attempts']),('failed','budget',[],[]))
                self.assertNotIn('_blocked',t.retry)
                Few.left=4
                self.assertEqual(asyncio.run(work()).status,'ok')
                self.assertEqual(hits,[PATHS[1]])


class ArtifactFailures(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.clock=Clock()
        from sofascore_scraper.store import Store
        self.store=Store(self.root/'cache');self.addCleanup(self.store.close)
        from persistencia.bundle import fingerprint
        self.event=dict(id=16599363,tournament=dict(id=94191,uniqueTournament=dict(id=17073),category=dict(sport=dict(slug='football',id=1))),
                        season=dict(id=97382),homeTeam=dict(id=2841,name='Fixture home'),awayTeam=dict(id=135672,name='Fixture away'),
                        status=dict(type='finished',code=100),startTimestamp=int(self.clock()-86400),roundInfo=dict(round=1))
        b=dict(schema=1,generation_id=str(uuid.uuid4()),teams=[2841,135672,*range(90001,90019)],
               events=[dict(id=16599363,current=True,replaced_by=None,home=2841,away=135672,status='finished',tournament_id=94191,season_id=97382)],records=[])
        self.baseline=dict(b,sha256=fingerprint(b))
    def test_backup_failure_prevents_all_requests_and_sealing(self):
        from acquisition.recipe import collect
        from unittest.mock import patch
        with server({}) as (url,hits),Transport(self.root/'jobs',job_id=str(uuid.uuid4()),request_id=str(uuid.uuid4()),
                    inventory_sha256=self.baseline['sha256'],loopback_url=url,clock=self.clock,sleep=self.clock.sleep) as t:
            with patch.object(self.store.backup,'create',side_effect=OSError('ENOSPC')):
                with self.assertRaises(OSError):collect(self.store,t,self.baseline,self.root/'bundle.json')
            self.assertEqual(hits,[]);self.assertFalse((self.root/'bundle.json').exists())
    def test_correction_outside_fixed_pilot_stops_before_more_queries(self):
        from acquisition.recipe import collect
        from persistencia.bundle import fingerprint
        import copy
        old=copy.deepcopy(self.event);old['id']=16599359
        self.baseline['events'].append(dict(self.baseline['events'][0],id=old['id']))
        self.baseline['records'].append(dict(record=dict(event_id=old['id'],key='event',sub='',fetched_at='2026-10-21T12:00:00+00:00',payload=old),source={}))
        self.baseline['sha256']=fingerprint({k:v for k,v in self.baseline.items() if k!='sha256'})
        corrected=copy.deepcopy(old);corrected['status']['type']='postponed'
        with server({PATHS[0]:[(200,{}, {'events':[self.event,corrected]})]}) as (url,hits),Transport(
                    self.root/'jobs',job_id=str(uuid.uuid4()),request_id=str(uuid.uuid4()),inventory_sha256=self.baseline['sha256'],
                    loopback_url=url,clock=self.clock,sleep=self.clock.sleep) as t:
            with self.assertRaisesRegex(Stopped,'outside fixed pilot'):
                collect(self.store,t,self.baseline,self.root/'bundle.json')
            self.assertEqual(hits,[PATHS[0]]);self.assertFalse((self.root/'bundle.json').exists())

    def test_partial_sporting_acquisition_never_seals(self):
        from acquisition.recipe import collect
        routes={PATHS[0]:[(200,{}, {'events':[self.event]})],PATHS[1]:[(200,{}, {'event':self.event})]}
        # No section has an approved absence rule. A 404 must leave the whole acquisition incomplete.
        with server(routes) as (url,hits),Transport(self.root/'jobs',job_id=str(uuid.uuid4()),request_id=str(uuid.uuid4()),
                    inventory_sha256=self.baseline['sha256'],loopback_url=url,clock=self.clock,sleep=self.clock.sleep) as t:
            with self.assertRaises(Stopped):collect(self.store,t,self.baseline,self.root/'bundle.json')
            self.assertFalse((self.root/'bundle.json').exists());self.assertTrue(hits)



if __name__ == '__main__':
    unittest.main(verbosity=2)
