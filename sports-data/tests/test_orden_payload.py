"""Regresión: un payload con el mismo contenido y otro orden de claves respeta la representación ya guardada en
`content` (docs/persistencia-sqlite.md, 2.7). Ejecutar desde la carpeta de la API:
python -m unittest tests.test_orden_payload
"""
import json
import sys
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_carga import CON_SQL, Base  # noqa: E402
from test_importar import linea  # noqa: E402


def invertir(valor):
    """Las claves de cada objeto en orden inverso; listas, tipos y valores intactos."""
    if isinstance(valor, dict):
        return {k: invertir(valor[k]) for k in reversed(valor)}
    if isinstance(valor, list):
        return [invertir(v) for v in valor]
    return valor


@unittest.skipUnless(CON_SQL, 'requiere las dependencias SQL')
class OrdenDeClaves(Base):
    def test_un_orden_equivalente_no_cambia_ordinales_ni_rompe_el_commit(self):
        from persistencia import carga, sincronizacion, sqlite
        carga.cargar(self.importacion(), self.base)
        consulta = 'SELECT call_up_id, key, ordinal, value_json FROM call_up_stat ORDER BY call_up_id, key'
        with closing(sqlite._lectura(self.base)) as c:
            antes = c.execute(consulta).fetchall()
            [texto] = c.execute("""SELECT ct.payload_text FROM section_selection s
                JOIN observation o ON o.id = s.observation_id JOIN content ct ON ct.hash = o.content_hash
                JOIN match_external_id e ON e.entity_id = s.match_id
                WHERE e.external_id = '200' AND s.key = 'lineups'""").fetchone()
        ruta = self.tmp / 'equivalente.jsonl'
        fila = linea(200, 'lineups', '2026-10-29T12:00:00+00:00', invertir(json.loads(texto)))
        ruta.write_text(json.dumps(fila) + '\n', encoding='utf-8')
        resultado = sincronizacion.importar_evidencia(self.base, exportaciones=[(ruta, None)])
        self.assertEqual(resultado['estado'], 'committed')
        self.assertTrue(sincronizacion.verificar(self.base)['ok'])
        with closing(sqlite._lectura(self.base)) as c:
            self.assertEqual(c.execute(consulta).fetchall(), antes)
            nuevas = "SELECT count(*) FROM observation WHERE fetched_at_raw = '2026-10-29T12:00:00+00:00'"
            self.assertEqual(c.execute(nuevas).fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
