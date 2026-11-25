"""Sports Data startup refuses incomplete restores before opening or seeding its state."""
import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    import app as sports_app
except ImportError:
    sports_app = None


@unittest.skipIf(sports_app is None, 'instala requirements-fastapi.txt para probar la barrera de arranque')
class RestoreGuard(unittest.TestCase):
    def test_marker_blocks_startup_and_no_marker_allows_the_guard(self):
        with tempfile.TemporaryDirectory(prefix='rja-restore-marker-') as temporary:
            marker = Path(temporary) / '.real-jaen-restore-incomplete'
            sports_app.require_complete_restore(marker)
            marker.write_text('project=restore-test\n', encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'Restauración incompleta'):
                sports_app.require_complete_restore(marker)

    def test_lifespan_checks_the_external_marker_before_loading_data(self):
        with tempfile.TemporaryDirectory(prefix='rja-restore-marker-') as temporary:
            marker = Path(temporary) / '.real-jaen-restore-incomplete'
            marker.write_text('project=restore-test\n', encoding='utf-8')

            async def enter():
                application = sports_app.create_app(data={})
                async with application.router.lifespan_context(application):
                    self.fail('No debe entrar en el lifespan con una restauración incompleta')

            with patch.dict('os.environ', {'RJ_RESTORE_MARKER_FILE': str(marker),
                                           'SPORTS_DATA_CONTROL': '0',
                                           'SPORTS_DATA_ENRICHMENT_ENABLED': '0'}):
                with self.assertRaisesRegex(RuntimeError, 'Restauración incompleta'):
                    asyncio.run(enter())


if __name__ == '__main__':
    unittest.main()
