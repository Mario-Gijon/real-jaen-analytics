"""Sustituciones locales de eventos, con la muestra de tests/fixtures/datos.

En la muestra, 1012 (aplazado, Real Jaén - 9001, J13) y 1006 / 1014 (finalizados, mismo local y visitante) hacen de
evento antiguo y de evento vigente; 1001 tiene los equipos al revés y 1004 está fuera del ámbito.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(Path(__file__).resolve().parent / 'fixtures'))
import api  # noqa: E402
import crear  # noqa: E402

MUESTRA = Path(__file__).resolve().parent / 'fixtures' / 'datos'
SIN = api.cargar(MUESTRA)
RIVAL = 9001


def decision(sustituido=1012, vigente=1006, **cambios):
    s = {'sustituido': sustituido, 'vigente': vigente, 'motivo': 'Aplazado y jugado en otra fecha con otro id',
         'procedencia': {'decidido_por': 'Analista (prueba)', 'fecha': '2026-10-22', 'evidencia': 'Mismo cruce y jornada'}}
    s.update(cambios)
    return s


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def archivo(self, *sustituciones, contenido=None):
        ruta = Path(self.tmp.name) / 'sustituciones.json'
        ruta.write_text(json.dumps(contenido if contenido is not None else {'sustituciones': list(sustituciones)}), encoding='utf-8')
        return ruta

    def cargar(self, *sustituciones):
        return api.cargar(MUESTRA, sustituciones=self.archivo(*sustituciones))


class Aplicada(Base):
    def test_el_evento_sustituido_deja_de_ser_encuentro_vigente_y_pendiente(self):
        con = self.cargar(decision())
        for equipo in (crear.JAEN, RIVAL):  # la misma regla para los dos equipos del partido
            antes = api.consultar(SIN, f'/api/v1/teams/{equipo}/performance')[1]['data']['competitions'][0]
            despues = api.consultar(con, f'/api/v1/teams/{equipo}/performance')[1]['data']['competitions'][0]
            self.assertEqual(despues['matches'], dict(antes['matches'], in_scope=antes['matches']['in_scope'] - 1,
                                                       pending=antes['matches']['pending'] - 1, replaced_locally=1))
            # Balance, resultados y producción no cambian.
            for clave in ('record', 'venues', 'output'):
                self.assertEqual(despues[clave], antes[clave])
            self.assertEqual([r['match']['match_id'] for r in despues['results']], [r['match']['match_id'] for r in antes['results']])
            self.assertEqual(api.consultar(con, f'/api/v1/teams/{equipo}/player-stats'), api.consultar(SIN, f'/api/v1/teams/{equipo}/player-stats'))
        calendario = api.consultar(con, f'/api/v1/teams/{crear.JAEN}/matches')[1]
        self.assertNotIn(1012, [m['match_id'] for m in calendario['data']])
        self.assertEqual(calendario['meta'], {'count': len(calendario['data']), 'replaced_locally': [1012]})

    def test_los_dos_eventos_se_conservan_y_el_detalle_explica_la_decision_local(self):
        con = self.cargar(decision())
        estado, antiguo = api.consultar(con, '/api/v1/matches/1012')
        self.assertEqual(estado, 200)
        sustitucion = antiguo['data']['replaced_by']
        self.assertEqual((sustitucion['match_id'], sustitucion['decision'], sustitucion['decided_by'], sustitucion['decided_on']),
                         (1006, 'local', 'Analista (prueba)', '2026-10-22'))
        self.assertIn('no un dato de SofaScore', sustitucion['note'])
        self.assertEqual(antiguo['meta'], api.consultar(SIN, '/api/v1/matches/1012')[1]['meta'])
        vigente = api.consultar(con, '/api/v1/matches/1006')[1]
        self.assertEqual([r['match_id'] for r in vigente['data']['replaces']], [1012])
        for seccion in ('', '/statistics', '/lineups', '/incidents'):
            for ident in (1006, 1012):
                self.assertEqual(api.consultar(con, f'/api/v1/matches/{ident}{seccion}')[1]['meta'],
                                 api.consultar(SIN, f'/api/v1/matches/{ident}{seccion}')[1]['meta'])
        self.assertEqual(con['originales'], SIN['originales'])
        self.assertEqual(con['jugador_partido'], SIN['jugador_partido'])
        alcance = api.consultar(con, '/api/v1/scope')[1]['data']
        self.assertEqual(alcance['matches'], {'events': len(SIN['partidos']), 'current': len(SIN['partidos']) - 1, 'replaced_locally': 1})
        self.assertEqual([(s['replaced'], s['current']) for s in alcance['local_replacements']], [(1012, 1006)])

    def test_sin_archivo_o_con_una_lista_vacia_nada_cambia(self):
        vacia = api.cargar(MUESTRA, sustituciones=self.archivo())
        for datos in (SIN, vacia):
            alcance = api.consultar(datos, '/api/v1/scope')[1]['data']
            self.assertEqual((alcance['local_replacements'], alcance['matches']['replaced_locally']), ([], 0))
            self.assertEqual(datos['partidos'], api.cargar(MUESTRA)['partidos'])
            self.assertTrue(all('replaced_by' not in m and 'replaces' not in m for m in datos['partidos']))
        self.assertEqual(api.consultar(vacia, f'/api/v1/teams/{crear.JAEN}/performance'),
                         api.consultar(SIN, f'/api/v1/teams/{crear.JAEN}/performance'))


class Rechazada(Base):
    def rechaza(self, *sustituciones, contenido=None, texto=''):
        with self.assertRaises(ValueError) as error:
            api.cargar(MUESTRA, sustituciones=self.archivo(*sustituciones, contenido=contenido))
        self.assertIn(texto, str(error.exception))

    def test_archivo_y_campos(self):
        self.rechaza(contenido=[], texto='lista "sustituciones"')
        self.rechaza(decision(sustituido='1012'), texto='identificadores enteros')
        self.rechaza(decision(motivo=' '), texto='motivo')
        self.rechaza(decision(procedencia={'fecha': '2026-10-22'}), texto='decidido_por')
        self.rechaza(decision(procedencia={'decidido_por': 'x', 'fecha': '22/10/2026'}), texto='fecha')

    def test_autorreferencias_contradicciones_cadenas_y_ciclos(self):
        self.rechaza(decision(vigente=1012), texto='a sí mismo')
        self.rechaza(decision(), decision(vigente=1014), texto='más de una vez')
        self.rechaza(decision(), decision(sustituido=1006, vigente=1014), texto='cadena o ciclo')
        self.rechaza(decision(), decision(sustituido=1006, vigente=1012), texto='cadena o ciclo')

    def test_eventos_que_no_cumplen_las_condiciones(self):
        self.rechaza(decision(vigente=999999), texto='no existe')
        self.rechaza(decision(vigente=1004), texto='fuera del ámbito')
        self.rechaza(decision(vigente=1001), texto='no coincide equipo local')
        self.rechaza(decision(vigente=1002), texto='no coincide equipo visitante')
        self.rechaza(decision(sustituido=1014, vigente=1006), texto='está finalizado')

    def test_un_archivo_invalido_se_rechaza_al_componer_datos(self):
        with self.assertRaisesRegex(ValueError, 'a sí mismo'):
            api.cargar(MUESTRA, sustituciones=self.archivo(decision(vigente=1012)))


if __name__ == '__main__':
    unittest.main()
