"""Deployment admission fence without the optional HTTP test dependencies."""
import tempfile
import unittest
import uuid

from acquisition.service import Conflict, Jobs
from persistencia.bundle import fingerprint


def inventory():
    value = dict(
        schema=1,
        generation_id=str(uuid.uuid4()),
        teams=[2841, 135672, *range(90001, 90019)],
        events=[dict(id=16599363, current=True, replaced_by=None, home=2841, away=135672,
                     status='finished', tournament_id=94191, season_id=97382)],
        records=[],
    )
    return dict(value, sha256=fingerprint(value))


class MaintenanceFenceTests(unittest.TestCase):
    def test_durable_deployment_fence_rejects_new_job_before_persisting_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            calls = []
            jobs = Jobs(temporary, provider='fixture-site', runner=lambda *args: calls.append(args))
            try:
                jobs.maintenance_path.write_text('release-under-deployment\n', encoding='ascii')
                request = str(uuid.uuid4())
                with self.assertRaises(Conflict):
                    jobs.submit(request, 'sofascore-pilot-16599363-v1', inventory(), request)
                with jobs.db() as connection:
                    self.assertEqual(connection.execute('SELECT COUNT(*) FROM job').fetchone()[0], 0)
                self.assertEqual(calls, [])
            finally:
                jobs.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
