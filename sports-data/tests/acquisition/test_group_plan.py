"""Plan of the Group 2 recipe (acquisition/group_plan.py) with simulated times, and its temporal parity with the SDK.

Pure functions: no network, no store. The parity tests need the SDK (acquisition test image) and are skipped
without it."""
from datetime import datetime, timezone
import importlib.util
from types import SimpleNamespace
import unittest

from acquisition import group_plan as g
from persistencia.bundle import fingerprint

H = 3600
NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc).timestamp()
TEAMS = [2841, 135672, *range(90001, 90019)]


def iso(at):
    return datetime.fromtimestamp(at, timezone.utc).isoformat()


def event(ident, number, start, status='notstarted', home=2841, away=135672, score=None):
    e = dict(id=ident, startTimestamp=int(start), roundInfo=dict(round=number), status=dict(type=status),
             tournament=dict(id=g.TOURNAMENT, uniqueTournament=dict(id=g.UNIQUE_TOURNAMENT)), season=dict(id=g.SEASON),
             homeTeam=dict(id=home), awayTeam=dict(id=away), homeScore={}, awayScore={})
    if score:
        e['homeScore'], e['awayScore'] = dict(current=score[0]), dict(current=score[1])
    return e


class World:
    """An inventory built match by match: the event as stored, its fetched_at and its sections."""
    def __init__(self):
        self.events, self.records = [], []

    def add(self, payload, *, fetched, sections=(), current=True, replaced_by=None):
        ident = payload['id']
        self.events.append(dict(id=ident, current=current, replaced_by=replaced_by, home=payload['homeTeam']['id'],
                                away=payload['awayTeam']['id'], status=payload['status']['type'],
                                tournament_id=g.TOURNAMENT, season_id=g.SEASON))
        self.records.append(dict(record=dict(event_id=ident, key='event', sub='', fetched_at=iso(fetched), payload=payload), source={}))
        for key in sections:
            section_payload = {
                'lineups': {'home': {'players': [{'player': {'id': 1}}]}, 'away': {'players': []}},
                'statistics': {'statistics': [{'period': 'ALL', 'groups': [{'statisticsItems': [{'name': 'Total shots'}]}]}]},
                'incidents': {'incidents': [{'incidentType': 'period'}]},
            }[key]
            self.records.append(dict(record=dict(event_id=ident, key=key, sub='', fetched_at=iso(fetched), payload=section_payload), source={}))
        return payload

    def baseline(self):
        value = dict(schema=1, generation_id='g', teams=TEAMS, events=self.events, records=self.records)
        return dict(value, sha256=fingerprint(value))


def season(now=NOW, checked=None):
    """Rounds 1-8 played long ago with every section, round 9 started 4 days ago (not finished in the inventory),
    round 10 in 3 days, rounds 11-38 later, one match per round."""
    w = World()
    week = 7 * 86400
    for n in range(1, 39):
        start = now - (9 - n) * week - 4 * 86400
        if n <= 8:
            w.add(event(1000 + n, n, start, 'finished', score=(1, 0)), fetched=start + 5 * 86400, sections=g.SECTIONS)
        else:
            w.add(event(1000 + n, n, start), fetched=(checked or {}).get(n, now - 30 * 86400))
    return w


class Rounds(unittest.TestCase):
    def test_rolling_window_uses_the_highest_round_with_a_final_score(self):
        w=World()
        for number in (9,10,11):
            w.add(event(2000+number,number,NOW-86400,'finished',score=(1,0)),fetched=NOW-3600,sections=g.SECTIONS)
        reference=g.reference_round(w.baseline())
        self.assertEqual((reference,g.recent_rounds(reference,3)),(11,[9,10,11]))
        w.add(event(2012,12,NOW-3600,'finished',home=90001,away=90002,score=(0,0)),
              fetched=NOW-1800,sections=g.SECTIONS)
        reference=g.reference_round(w.baseline())
        self.assertEqual((reference,g.recent_rounds(reference,3)),(12,[10,11,12]))

    def test_simultaneous_and_out_of_order_results_do_not_move_window_backwards(self):
        w=World()
        for ident,number in ((1,10),(2,11),(3,12),(4,12)):
            w.add(event(ident,number,NOW-86400,'finished',home=TEAMS[ident],away=TEAMS[ident+4],score=(1,0)),
                  fetched=NOW-3600,sections=g.SECTIONS)
        self.assertEqual(g.reference_round(w.baseline()),12)
        old=event(5,4,NOW-30*86400,'finished',home=TEAMS[9],away=TEAMS[10],score=(2,1))
        self.assertEqual(g.reference_round(w.baseline(),{5:old}),12)

    def test_no_completed_fixture_has_no_dynamic_window(self):
        w=World();w.add(event(1,1,NOW+86400),fetched=NOW)
        self.assertIsNone(g.reference_round(w.baseline()))
        self.assertEqual(g.recent_rounds(None,3),[])

    def test_invalid_local_observation_timestamp_is_unknown_not_a_scope_failure(self):
        w=World()
        w.add(event(2011,11,NOW-86400,'finished',score=(1,0)),fetched=NOW-3600,sections=g.SECTIONS)
        next(row['record'] for row in w.records if row['record']['key']=='event')['fetched_at']='sin-fecha'
        baseline=w.baseline()
        self.assertEqual((g.reference_round(baseline),g.recent_rounds(g.reference_round(baseline),3)),
                         (11,[9,10,11]))
        self.assertTrue(g.rounds_to_list(baseline,NOW)[0])

    def test_near_rounds_and_far_rotation(self):
        near, far = g.rounds_to_list(season().baseline(), NOW)
        self.assertEqual(near, [9, 10])  # started and not finished; within 7 days
        self.assertEqual(far, [11, 12, 13, 14, 15, 16])  # equally old checks: lowest rounds first

    def test_a_far_round_checked_longest_ago_is_listed_first(self):
        checked = {n: NOW - 86400 for n in range(11, 39)}
        checked[30] = NOW - 60 * 86400
        _, far = g.rounds_to_list(season(checked=checked).baseline(), NOW)
        self.assertIn(30, far)  # chosen first by age; the result itself is sorted by round number

    def test_rotation_reaches_every_far_round(self):
        checked, seen = {}, set()
        for press in range(5):
            now = NOW + press * 3600
            _, far = g.rounds_to_list(season(now=NOW, checked=checked).baseline(), now)
            seen.update(far)
            checked.update({n: now for n in far})  # what an update records as checked
        self.assertEqual(seen, set(range(11, 39)))  # 28 far rounds in ceil(28 / 6) = 5 updates

    def test_rotation_moves_on_with_the_services_own_listings(self):
        # Listings that change nothing leave the inventory as it was: the rotation follows the service's record.
        baseline, listed_at, seen, previous = season().baseline(), {}, [], set()
        for press in range(5):
            now = NOW + press * 3600
            _, far = g.rounds_to_list(baseline, now, listed_at=listed_at)
            self.assertFalse(previous & set(far) and press < 4, (press, far))  # a new set every update
            seen.extend(far)
            listed_at.update({n: now for n in far})
            previous = set(far)
        self.assertEqual(set(seen), set(range(11, 39)))

    def test_round_listings_from_the_job_records(self):
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as root:
            ok = dict(path=g.round_path(12), status=200, finished_at=iso(NOW))
            failed = dict(path=g.round_path(13), status=500, finished_at=iso(NOW))
            event = dict(path='/event/1', status=200, finished_at=iso(NOW))
            (Path(root) / 'job.json').write_text(json.dumps(dict(attempts=[ok, failed, event])))
            (Path(root) / 'retry.json').write_text('{}')
            self.assertEqual(g.round_listings(root), {12: NOW})

    def test_finished_and_complete_rounds_outside_the_window_are_not_listed(self):
        near, far = g.rounds_to_list(season().baseline(), NOW)
        self.assertFalse(set(range(1, 9)) & set(near + far))


class Matches(unittest.TestCase):
    def listed(self, *rows):
        return {r['id']: r for r in rows}

    def test_finished_outside_the_72h_window_is_recovered(self):
        w = season()
        stale = next(e for e in w.records if e['record']['event_id'] == 1009)['record']['payload']
        row = dict(stale, status=dict(type='finished'), homeScore=dict(current=2), awayScore=dict(current=2))
        self.assertGreater(NOW - stale['startTimestamp'], g.WINDOW_S)
        planned, deferred = g.plan_matches(w.baseline(), self.listed(row), NOW)
        self.assertEqual([(p['id'], p['reason'], p['keys']) for p in planned], [(1009, 'finished', g.SECTIONS)])
        self.assertEqual(deferred, {})

    def test_old_postponed_match_that_finishes_is_a_window_exception(self):
        w=World()
        old=w.add(event(10,4,NOW-30*86400,'postponed'),fetched=NOW-20*86400)
        finished=dict(old,status=dict(type='finished'),homeScore=dict(current=1),awayScore=dict(current=0))
        planned,deferred=g.plan_matches(w.baseline(),{10:finished},NOW,recent_rounds=[10,11,12])
        self.assertEqual([(item['id'],item['reason']) for item in planned],[(10,'finished')])
        self.assertEqual(deferred,{})

    def test_old_complete_unchanged_and_recent_not_due_are_not_refreshed(self):
        w=World()
        w.add(event(1,2,NOW-30*86400,'finished',score=(1,0)),fetched=NOW-20*86400,sections=g.SECTIONS)
        w.add(event(2,11,NOW-30*H,'finished',home=90001,away=90002,score=(0,0)),
              fetched=NOW-H,sections=g.SECTIONS)
        planned,deferred=g.plan_matches(w.baseline(),{},NOW,recent_rounds=[9,10,11],reasons=('refresh',))
        self.assertEqual((planned,deferred),([],{}))

    def test_old_incomplete_matches_are_bounded_historical_recovery(self):
        w=World()
        for ident,number in ((1,2),(2,3),(3,4),(4,5)):
            w.add(event(ident,number,NOW-30*86400,'finished',home=TEAMS[ident],away=TEAMS[ident+4],score=(1,0)),
                  fetched=NOW-20*86400,sections=('lineups',))
        planned,deferred=g.plan_matches(w.baseline(),{},NOW,recent_rounds=[9,10,11],history_limit=2,
                                        reasons=('history',))
        self.assertEqual([(item['id'],item['reason']) for item in planned],[(1,'history'),(2,'history')])
        self.assertEqual(deferred,{3:'restricted',4:'restricted'})

    def test_empty_stored_section_is_pending_and_retried(self):
        w = World()
        w.add(event(30, 5, NOW - 40 * 86400, 'finished', score=(1, 0)), fetched=NOW - 39 * 86400,
              sections=g.SECTIONS)
        next(row['record'] for row in w.records if row['record']['event_id'] == 30 and
             row['record']['key'] == 'statistics')['payload'] = {'statistics': []}
        planned, deferred = g.plan_matches(w.baseline(), {}, NOW)
        self.assertEqual([(p['id'], p['reason'], p['keys']) for p in planned], [(30, 'pending', ('statistics',))])
        self.assertEqual(deferred, {})

    def test_far_calendar_change_asks_only_the_event(self):
        w = season()
        far = next(e for e in w.records if e['record']['event_id'] == 1030)['record']['payload']
        moved = dict(far, startTimestamp=far['startTimestamp'] + 86400)
        planned, _ = g.plan_matches(w.baseline(), self.listed(moved), NOW)
        self.assertEqual([(p['id'], p['reason'], p['keys'], p['cost']) for p in planned], [(1030, 'changed', (), 1)])

    def test_unchanged_listing_asks_nothing(self):
        w = season()
        rows = [e['record']['payload'] for e in w.records if e['record']['event_id'] in (1010, 1011)]
        self.assertEqual(g.plan_matches(w.baseline(), self.listed(*rows), NOW), ([], {}))

    def test_refresh_follows_72h_and_6h(self):
        w = World()
        start = NOW - 30 * H
        w.add(event(1, 9, start, 'finished', score=(1, 1)), fetched=NOW - 20 * H, sections=g.SECTIONS)  # due
        w.add(event(2, 9, start, 'finished', score=(1, 1), home=90001, away=90002), fetched=NOW - 2 * H, sections=g.SECTIONS)  # < 6 h
        w.add(event(3, 9, NOW - 100 * H, 'finished', score=(0, 0), home=90003, away=90004), fetched=NOW - 20 * H,
              sections=g.SECTIONS)  # observed 80 h after kick-off: outside the window
        planned, _ = g.plan_matches(w.baseline(), {}, NOW)
        self.assertEqual([(p['id'], p['reason']) for p in planned], [(1, 'refresh')])

    def test_pending_first_then_finished_changed_refresh_and_the_budget(self):
        w = World()
        w.add(event(1, 5, NOW - 40 * 86400, 'finished', score=(1, 0)), fetched=NOW - 39 * 86400, sections=('lineups',))  # pending
        nine = w.add(event(2, 9, NOW - 4 * 86400, home=90001, away=90002), fetched=NOW - 5 * 86400)
        far = w.add(event(3, 20, NOW + 60 * 86400, home=90003, away=90004), fetched=NOW - 5 * 86400)
        w.add(event(4, 9, NOW - 30 * H, 'finished', score=(2, 2), home=90005, away=90006), fetched=NOW - 20 * H, sections=g.SECTIONS)
        listed = self.listed(dict(nine, status=dict(type='finished'), homeScore=dict(current=1), awayScore=dict(current=0)),
                             dict(far, startTimestamp=far['startTimestamp'] + 3600))
        planned, deferred = g.plan_matches(w.baseline(), listed, NOW)
        self.assertEqual([(p['id'], p['reason'], p['cost']) for p in planned],
                         [(2, 'finished', 4), (3, 'changed', 1), (1, 'pending', 3), (4, 'refresh', 4)])
        planned, deferred = g.plan_matches(w.baseline(), listed, NOW, budget=7)
        self.assertEqual([p['id'] for p in planned], [2, 3])
        self.assertEqual(deferred, {1: 'budget', 4: 'budget'})
        # Next update: what was deferred is still due; pending of the previous one goes first again.
        planned, _ = g.plan_matches(w.baseline(), listed, NOW, budget=7)
        self.assertEqual(planned[0]['reason'], 'finished')

    def test_reason_filter_reserves_routine_refresh_for_a_later_pass(self):
        w = World()
        w.add(event(1, 5, NOW - 40 * 86400, 'finished', score=(1, 0)), fetched=NOW - 39 * 86400,
              sections=('lineups',))
        nine = w.add(event(2, 9, NOW - 4 * 86400, home=90001, away=90002), fetched=NOW - 5 * 86400)
        far = w.add(event(3, 20, NOW + 60 * 86400, home=90003, away=90004), fetched=NOW - 5 * 86400)
        w.add(event(4, 9, NOW - 30 * H, 'finished', score=(2, 2), home=90005, away=90006),
              fetched=NOW - 20 * H, sections=g.SECTIONS)
        listed = self.listed(dict(nine, status=dict(type='finished'), homeScore=dict(current=1), awayScore=dict(current=0)),
                             dict(far, startTimestamp=far['startTimestamp'] + 3600))

        essential, deferred = g.plan_matches(w.baseline(), listed, NOW, budget=8,
                                             reasons=('pending', 'finished', 'changed'))
        self.assertEqual([(item['id'], item['reason']) for item in essential],
                         [(2, 'finished'), (3, 'changed'), (1, 'pending')])
        self.assertEqual(deferred, {})
        refresh, deferred = g.plan_matches(w.baseline(), listed, NOW, budget=4, reasons=('refresh',))
        self.assertEqual([(item['id'], item['reason']) for item in refresh], [(4, 'refresh')])
        self.assertEqual(deferred, {})

    def test_new_match_is_reported_for_review_but_never_planned(self):
        w = season()
        postponed = w.add(event(2000, 12, NOW - 10 * 86400, 'postponed', home=90007, away=90008), fetched=NOW - 9 * 86400)
        new = event(3000, 12, NOW - 3 * 86400, 'finished', home=90007, away=90008, score=(0, 1))
        outside = dict(event(4000, 12, NOW, 'finished', home=90007, away=777), tournament=dict(id=1, uniqueTournament=dict(id=17073)))
        listed = self.listed(new, outside)
        planned, _ = g.plan_matches(w.baseline(), listed, NOW)
        self.assertNotIn(3000, [p['id'] for p in planned])
        self.assertNotIn(4000, [p['id'] for p in planned])
        self.assertEqual(g.new_matches(w.baseline(), listed), [dict(id=3000, replaces=[postponed['id']])])

    def test_history_is_never_consulted(self):
        w = World()
        w.add(event(5, 3, NOW - 10 * 86400, 'postponed'), fetched=NOW - 9 * 86400, current=False, replaced_by=6)
        w.add(event(6, 3, NOW - 4 * 86400, 'finished', score=(1, 0)), fetched=NOW - 3 * 86400, sections=g.SECTIONS)
        row = event(5, 3, NOW - 10 * 86400, 'finished', score=(3, 3))
        planned, _ = g.plan_matches(w.baseline(), {5: row}, NOW)
        self.assertNotIn(5, [p['id'] for p in planned])


class Restriction(unittest.TestCase):
    """An explicit restriction of the installation: only the matches of the given rounds are consulted; whatever the
    listings detect elsewhere is deferred as 'restricted', whatever the date or the budget."""
    def setUp(self):
        self.w = World()
        nine = self.w.add(event(1, 9, NOW - 4 * 86400), fetched=NOW - 5 * 86400)
        ten = self.w.add(event(2, 10, NOW + 3 * 86400, home=90001, away=90002), fetched=NOW - 5 * 86400)
        far = self.w.add(event(3, 20, NOW + 60 * 86400, home=90003, away=90004), fetched=NOW - 5 * 86400)
        self.w.add(event(4, 8, NOW - 30 * H, 'finished', score=(2, 2), home=90005, away=90006), fetched=NOW - 20 * H,
                   sections=g.SECTIONS)  # a refresh due by the 72 h / 6 h rule, in round 8
        self.listed = {r['id']: r for r in (
            dict(nine, status=dict(type='finished'), homeScore=dict(current=1), awayScore=dict(current=0)),
            dict(ten, startTimestamp=ten['startTimestamp'] + 3600), dict(far, startTimestamp=far['startTimestamp'] + 3600),
            event(5, 11, NOW + 10 * 86400, home=90007, away=90008),  # new match in scope, round 11
            dict(event(6, 9, NOW - 4 * 86400, 'finished', home=90009, away=90010, score=(0, 0)), roundInfo=None))}

    def test_only_the_declared_rounds_are_consulted(self):
        planned, deferred = g.plan_matches(self.w.baseline(), self.listed, NOW, detail_rounds=[9])
        self.assertEqual([(p['id'], p['reason']) for p in planned], [(1, 'finished')])
        # Detected elsewhere (calendar changes, a due refresh, a row without round): deferred, flagged.
        # A new ID is reported separately and never becomes a detail candidate.
        self.assertEqual(deferred, {2: 'restricted', 3: 'restricted', 4: 'restricted'})

    def test_restriction_before_budget_and_without_it_everything_is_planned(self):
        planned, deferred = g.plan_matches(self.w.baseline(), self.listed, NOW, budget=2, detail_rounds=[9])
        self.assertEqual((planned, deferred[1]), ([], 'budget'))
        self.assertEqual({k: v for k, v in deferred.items() if k != 1}, {i: 'restricted' for i in (2, 3, 4)})
        planned, deferred = g.plan_matches(self.w.baseline(), self.listed, NOW)
        self.assertEqual(({p['id'] for p in planned}, deferred), ({1, 2, 3, 4}, {}))


@unittest.skipUnless(importlib.util.find_spec('fcntl'), 'acquisition service (Linux image)')
class Reductions(unittest.TestCase):
    """The installation can only lower the production maxima."""
    def test_lowering_is_accepted_and_raising_refused(self):
        from acquisition.service import reductions
        self.assertEqual(reductions({}), dict(recent_round_count=3, max_total_sends=50))
        self.assertEqual(reductions(dict(ACQ_MAX_ATTEMPTS='60', ACQ_MAX_SENDS='70',
                                        ACQ_UPDATE_RECENT_ROUNDS='2', ACQ_UPDATE_MAX_TOTAL_SENDS='40')),
                         dict(max_attempts=60, max_sends=70, recent_round_count=2, max_total_sends=40))
        for bad in (dict(ACQ_MAX_ATTEMPTS='117'), dict(ACQ_MAX_SENDS='121'), dict(ACQ_MAX_SENDS='0'),
                    dict(ACQ_MAX_ATTEMPTS='-1'), dict(ACQ_MAX_ATTEMPTS='60.5'), dict(ACQ_MAX_ATTEMPTS='71', ACQ_MAX_SENDS='70'),
                    dict(ACQ_DETAIL_ROUNDS='10'), dict(ACQ_UPDATE_RECENT_ROUNDS='4'),
                    dict(ACQ_UPDATE_MAX_TOTAL_SENDS='51')):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                reductions(bad)

    def test_both_providers_receive_them(self):
        from acquisition.service import configuration
        provider, extra = configuration(dict(ACQ_PROVIDER='sofascore', ACQ_ENABLE_REAL='1', ACQ_MAX_ATTEMPTS='60',
                                             ACQ_MAX_SENDS='70', ACQ_UPDATE_RECENT_ROUNDS='3',
                                             ACQ_UPDATE_MAX_TOTAL_SENDS='50'))
        self.assertEqual((provider, extra), ('sofascore', dict(max_attempts=60, max_sends=70,
                                                               recent_round_count=3, max_total_sends=50)))


@unittest.skipUnless(importlib.util.find_spec('sofascore_scraper'), 'SDK required (acquisition test image)')
class SdkParity(unittest.TestCase):
    """The same instants through the SDK's own refresh_due and through the plan's: identical decisions."""
    def test_constants_are_the_sdk_defaults(self):
        from sofascore_scraper.config.settings import RefreshSettings
        settings = RefreshSettings()
        self.assertEqual((g.WINDOW_S, g.MIN_INTERVAL_S), (settings.window_hours * 3600, settings.min_interval_hours * 3600))

    def test_refresh_due_parity(self):
        from sofascore_scraper.services import planning
        from sofascore_scraper.status import StatusClass
        policy = lambda now: SimpleNamespace(now=now, window_s=g.WINDOW_S, min_interval_s=g.MIN_INTERVAL_S,  # noqa: E731
                                             include_unobserved=False, confirm_after_s=0.0)
        checked = 0
        for start_offset in (-200, -80, -72, -71.99, -30, -6, -1):
            for observed_after in (0.5, 5, 6, 30, 71.99, 72, 73, 100):
                for now_after in (0, 5.99, 6, 6.01, 50, 200):
                    start = NOW + start_offset * H
                    observed = start + observed_after * H
                    now = observed + now_after * H
                    match = dict(status='finished', start=start, event_fetched=observed)
                    row = SimpleNamespace(has_event_payload=True, status_class=StatusClass.COMPLETED.value,
                                          observed_at=observed, observed_gap=observed - start)
                    self.assertEqual(g.refresh_due(match, now), planning.refresh_due(row, policy(now)),
                                     (start_offset, observed_after, now_after))
                    checked += 1
        self.assertEqual(checked, 7 * 8 * 6)

    def test_not_finished_never_refreshed_in_either(self):
        from sofascore_scraper.services import planning
        from sofascore_scraper.status import StatusClass
        row = SimpleNamespace(has_event_payload=True, status_class=StatusClass.NOT_STARTED.value, observed_at=NOW - 10 * H,
                              observed_gap=1 * H)
        policy = SimpleNamespace(now=NOW, window_s=g.WINDOW_S, min_interval_s=g.MIN_INTERVAL_S, include_unobserved=False)
        self.assertFalse(planning.refresh_due(row, policy))
        self.assertFalse(g.refresh_due(dict(status='notstarted', start=NOW - 11 * H, event_fetched=NOW - 10 * H), NOW))


if __name__ == '__main__':
    unittest.main(verbosity=2)
