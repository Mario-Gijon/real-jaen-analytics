"""Focused verification of incremental comments coverage against current event/listing evidence."""
import base64
import unittest

from persistencia.bundle import (
    Rejected,
    _group_comment_finished,
    _verify_group_comments,
    encode,
    fingerprint,
    sha,
)


class GroupCommentCoverageTests(unittest.TestCase):
    def setUp(self):
        self.ids = (101, 102, 103, 104, 105, 106, 107)
        self.teams = (10, 20)
        self.baseline = {
            'comments_enabled': True,
            'events': [dict(id=i, current=True, replaced_by=None, home=10, away=20, status='notstarted',
                            tournament_id=94191, season_id=97382) for i in self.ids],
            'records': [],
            'comments': [dict(event_id=102, spanish_complete=True)],
        }
        self.expected = {
            i: dict(id=i, current=True, home=10, away=20, status='notstarted') for i in self.ids
        }
        self.current = set(self.ids)
        self.calendar = {
            i: ({'id': i, 'status': {'type': 'finished' if i != 106 else 'notstarted'}},
                {'status': 'finished' if i != 106 else 'notstarted', 'round': 1})
            for i in self.ids
        }
        self.planned = [dict(id=i, reason='finished', keys=[]) for i in (101, 103, 104, 105, 106, 107)]
        self.raw_records = {
            (i, 'event'): dict(event_id=i, key='event', payload={'id': i, 'status': {'type':
                'notstarted' if i in (106, 107) else 'finished', 'code': 0 if i in (106, 107) else 100}})
            for i in self.ids
        }
        self.listed_row = {
            'id': 106,
            'tournament': {'id': 94191, 'uniqueTournament': {'id': 17073}},
            'season': {'id': 97382},
            'homeTeam': {'id': 10},
            'awayTeam': {'id': 20},
            'status': {'type': 'finished', 'code': 100, 'description': 'Ended'},
        }
        self.listed = {106: (self.listed_row, dict(number=1, path='/unique-tournament/17073/season/97382/events/round/1',
                                                   status=200))}
        self.attempts = []
        self.latest = {}
        self.comment_records = []
        self._comment_attempt(101, 200, {'comments': [self.comment('new')]})
        self._comment_attempt(103, 404, None, reason='not_found')
        self._comment_attempt(104, 200, {'comments': []})  # Valid empty capture is still a successful response.
        # An event request with a successful but non-final detail takes precedence over a stale finished listing.
        self._event_attempt(107, {'id': 107, 'status': {'type': 'notstarted', 'code': 0}})
        self.listed[107] = ({**self.listed_row, 'id': 107}, dict(number=2, status=200))

    @staticmethod
    def comment(ident):
        return dict(id=ident, text='Acción de partido', type='shotOffTarget')

    def _comment_attempt(self, event_id, status, payload, *, reason=None):
        number = len(self.attempts) + 1
        finished_at = f'2026-11-09T00:00:{number:02d}+00:00'
        raw = None if payload is None else encode(payload)
        attempt = dict(number=number, path=f'/event/{event_id}/comments/es-la', sent_at=finished_at,
                       finished_at=finished_at, status=status,
                       payload_sha256=None if payload is None else fingerprint(payload))
        self.attempts.append(attempt)
        self.latest[attempt['path']] = attempt
        self.comment_records.append(dict(
            event_id=event_id, status=status, attempt_numbers=[number],
            raw_b64=None if raw is None else base64.b64encode(raw).decode('ascii'),
            raw_sha256=None if raw is None else sha(raw), captured_at=finished_at, reason=reason,
            transport_report=dict(path=attempt['path'], attempt_numbers=[number], logical_requests=1, physical_sends=1),
        ))

    def _event_attempt(self, event_id, payload):
        number = len(self.attempts) + 1
        at = f'2026-11-09T00:01:{number:02d}+00:00'
        attempt = dict(number=number, path=f'/event/{event_id}', sent_at=at, finished_at=at, status=200,
                       payload_sha256=fingerprint(payload))
        self.attempts.append(attempt)
        self.latest[attempt['path']] = attempt
        self.raw_records[(event_id, 'event')] = dict(event_id=event_id, key='event', payload=payload)

    def valid_summary(self):
        return dict(new=[101, 104], kept=[102], pending=[105, 106],
                    failed=[dict(id=103, reason='not_found')])

    def verify(self, summary=None):
        return _verify_group_comments(
            self.valid_summary() if summary is None else summary,
            self.comment_records,
            self.baseline,
            self.expected,
            self.current,
            self.calendar,
            self.attempts,
            self.latest,
            self.planned,
            None,
            self.listed,
            self.raw_records,
        )

    def test_new_kept_empty_404_and_partial_batch_summary_is_accepted(self):
        # id=106 is pending because the verified round page has "finished", although its cached calendar row
        # remains "notstarted"; id=107 is excluded because its successful event detail takes precedence.
        self.assertEqual(_group_comment_finished(self.current, self.expected, self.raw_records, self.listed,
                                                 self.latest, self.planned), {101, 102, 103, 104, 105, 106})
        self.verify()

    def test_reported_4_new_97_kept_9_pending_and_no_failures_is_accepted(self):
        ids = tuple(range(1000, 1110))
        self.baseline = {
            'comments_enabled': True,
            'events': [dict(id=i, current=True, replaced_by=None, home=10, away=20, status='finished',
                            tournament_id=94191, season_id=97382) for i in ids],
            'records': [],
            'comments': [dict(event_id=i, spanish_complete=True) for i in ids[:97]],
        }
        self.expected = {i: dict(id=i, current=True, home=10, away=20, status='finished') for i in ids}
        self.current = set(ids)
        self.calendar = {}
        self.planned = []
        self.raw_records = {}
        self.listed = {}
        self.attempts = []
        self.latest = {}
        self.comment_records = []
        for ident in ids[97:101]:
            self._comment_attempt(ident, 200, {'comments': [self.comment(f'new-{ident}')]})

        self.verify(dict(new=list(ids[97:101]), kept=list(ids[:97]), pending=list(ids[101:]), failed=[]))

    def test_inconsistent_pending_summary_is_still_rejected(self):
        wrong = self.valid_summary()
        wrong['pending'] = [105]
        with self.assertRaisesRegex(Rejected, 'Comments coverage does not match attempts and inventory'):
            self.verify(wrong)

    def test_listing_from_different_match_identity_is_not_accepted_as_comment_scope(self):
        self.listed[106] = ({**self.listed_row, 'homeTeam': {'id': 999}}, self.listed[106][1])
        with self.assertRaisesRegex(Rejected, 'Comments listing identity mismatch'):
            self.verify()

    def test_three_old_pending_comments_remain_eligible_under_dynamic_window(self):
        ids=(201,202,203)
        baseline=dict(comments_enabled=True,events=[dict(id=i,current=True,replaced_by=None,home=10,away=20,
                      status='finished',tournament_id=94191,season_id=97382) for i in ids],records=[],comments=[])
        expected={i:dict(id=i,current=True,home=10,away=20,status='finished') for i in ids}
        calendar={i:({'id':i,'status':{'type':'finished'}},{'status':'finished','round':4}) for i in ids}
        result=_verify_group_comments(dict(new=[],kept=[],pending=list(ids),failed=[]),[],baseline,expected,set(ids),
                                      calendar,[],{},[],[1],{}, {},dynamic_policy=True)
        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()
