"""Cobertura de la huella del coordinador para reglas de enrichment."""

from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from persistencia import carga


class HuellaEnrichment(unittest.TestCase):
    def _arbol_temporal(self, destino: Path) -> None:
        """Copia solo los archivos que actualmente participan en la huella."""
        for relativo in carga.archivos_de_codigo():
            origen = carga.RAIZ / relativo
            copia = destino / relativo
            copia.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origen, copia)

        # Estos archivos generados o de transporte no deben participar en la huella.
        (destino / 'persistencia' / 'compilado.pyc').write_bytes(b'compiled')
        (destino / 'persistencia' / 'temporal.tmp').write_text('temporary', encoding='utf-8')
        cache = destino / 'persistencia' / '__pycache__' / 'generado.py'
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text('generated', encoding='utf-8')
        (destino / 'enrichment' / 'router.py').write_text('route-v1', encoding='utf-8')
        (destino / 'enrichment' / 'queries.py').write_text('query-v1', encoding='utf-8')

    def test_incluye_reglas_y_es_determinista_sin_archivos_generados_ni_http(self):
        first = carga.archivos_de_codigo()
        second = carga.archivos_de_codigo()

        self.assertEqual(first, second)
        self.assertEqual(first, sorted(set(first)))
        self.assertTrue(set(carga.ARCHIVOS_ENRIQUECIMIENTO).issubset(first))
        self.assertIn('persistencia/migraciones/versions/0008_anotaciones_enrichment.py', first)
        self.assertNotIn('enrichment/router.py', first)
        self.assertNotIn('enrichment/queries.py', first)
        self.assertFalse(any('__pycache__' in path or path.endswith('.pyc') for path in first))
        self.assertFalse(any(path.endswith('.tmp') for path in first))

    def test_huella_temporal_es_estable_y_refleja_cambios_de_un_extractor(self):
        with tempfile.TemporaryDirectory(prefix='rj-enrichment-fingerprint-') as directory:
            root = Path(directory)
            self._arbol_temporal(root)
            with mock.patch.object(carga, 'RAIZ', root):
                original = carga.referencia_del_codigo()['sha256_agregada']
                self.assertEqual(original, carga.referencia_del_codigo()['sha256_agregada'])

                extractor = root / 'enrichment' / 'shot_attributes.py'
                source = extractor.read_bytes()
                extractor.write_bytes(source + b'\n# fingerprint fixture change\n')
                changed = carga.referencia_del_codigo()['sha256_agregada']
                self.assertNotEqual(original, changed)

                extractor.write_bytes(source)
                self.assertEqual(original, carga.referencia_del_codigo()['sha256_agregada'])

    def test_cambios_solo_en_router_y_queries_de_enrichment_no_alteran_huella(self):
        with tempfile.TemporaryDirectory(prefix='rj-enrichment-http-fingerprint-') as directory:
            root = Path(directory)
            self._arbol_temporal(root)
            with mock.patch.object(carga, 'RAIZ', root):
                original = carga.referencia_del_codigo()['sha256_agregada']
                (root / 'enrichment' / 'router.py').write_text('route-v2', encoding='utf-8')
                (root / 'enrichment' / 'queries.py').write_text('query-v2', encoding='utf-8')
                self.assertEqual(original, carga.referencia_del_codigo()['sha256_agregada'])


if __name__ == '__main__':
    unittest.main()
