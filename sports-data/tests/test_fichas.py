"""Fichas complementarias: validación, versiones, aislamiento deportivo y ámbito."""
import copy
import json
import subprocess
import sys
from test_importar import Base, partido_basico, linea, evento, alineacion, entrada, JAEN, RIVAL, FIJO
import importar


def ficha(pie='Left', fecha='2026-10-23T12:00:00+02:00', **campos):
    return {'player_id': 1, 'endpoint': '/player/1', 'fetched_at': fecha,
            'payload': {'player': dict(id=1, preferredFoot=pie, **campos)}}


class Fichas(Base):
    def preparar(self, registros=None):
        raw = self.exportacion('raw.jsonl', registros or partido_basico())
        return [(raw, JAEN)]

    def test_enriquecimiento_sin_relaciones_ni_estadisticas_y_reimportacion(self):
        entradas = self.preparar()
        base = importar.transformar(entradas, FIJO)
        suplemento = self.exportacion('fichas.jsonl', [ficha(team={'id': 999}, jerseyNumber='999',
                                                       statistics={'goals': 999}, country={'name': 'Spain'})])
        datos = importar.transformar(entradas, FIJO, fichas=[suplemento])
        repetidos = importar.transformar(entradas, FIJO, fichas=[suplemento, suplemento])
        for clave in ('equipos', 'partidos', 'jugador_partido', 'originales'):
            self.assertEqual(datos[clave], base[clave])
            self.assertEqual(repetidos[clave], base[clave])
        self.assertEqual(datos['jugadores'], repetidos['jugadores'])
        self.assertEqual(datos['jugadores'][0]['preferred_foot'], 'Left')
        self.assertEqual(len(datos['jugadores']), len(base['jugadores']))
        self.assertEqual(datos['fichas_originales'][0]['payload'], ficha(team={'id': 999}, jerseyNumber='999',
                              statistics={'goals': 999}, country={'name': 'Spain'})['payload'])
        self.assertEqual(len(repetidos['fichas_originales']), 2)  # procedencia repetida, no entidades duplicadas
        self.assertEqual(datos['exportaciones']['exports'][-1]['kind'], 'player_profile')
        self.assertEqual(self.servir(datos)['jugadores'][0]['preferred_foot'], 'Left')

    def test_versiones_nulos_ausencias_conflictos_y_zonas_horarias(self):
        entradas = self.preparar()
        registros = [ficha('Left', '2026-10-23T12:00:00+02:00'),
                     ficha('Right', '2026-10-23T10:30:00Z'),
                     ficha(None, '2026-10-23T11:00:00Z'),
                     ficha('Both', '2026-10-23T09:00:00Z')]
        ausente = ficha(fecha='2026-10-23T12:00:00Z')
        del ausente['payload']['player']['preferredFoot']
        registros.append(ausente)
        ruta = self.exportacion('versiones.jsonl', registros)
        datos = importar.transformar(entradas, FIJO, fichas=[ruta])
        self.assertEqual(datos['jugadores'][0]['preferred_foot'], 'Right')
        d = next(d for d in datos['discrepancias'] if d.get('player_id') == 1)
        self.assertEqual(set(d['values']['preferred_foot']), {'Left', 'Right', 'Both'})
        self.assertEqual(d['reasons'], ['player_attribute_values_differ'])
        self.assertEqual(len(d['versions']), 6)
        self.assertEqual(len(datos['fichas_originales']), 5)

    def test_ambito_reconstruye_ficha_con_suplemento_sin_usar_alineacion_excluida(self):
        fuera = evento(200, JAEN, RIVAL, tournament={'id': 999, 'uniqueTournament': {'id': 999}})
        registros = partido_basico() + [linea(200, 'event', FIJO, fuera),
            linea(200, 'lineups', FIJO, alineacion([entrada(1, JAEN, altura=199)], []))]
        entradas = self.preparar(registros)
        ruta = self.exportacion('ficha.jsonl', [ficha()])
        datos = importar.transformar(entradas, FIJO, fichas=[ruta])
        servido = self.servir(datos)
        jugador = next(p for p in servido['jugadores'] if p['player_id'] == 1)
        self.assertEqual(jugador['preferred_foot'], 'Left')
        self.assertEqual(jugador['height_cm'], 180)
        self.assertEqual({m['match_id'] for m in servido['partidos']}, {100})

    def test_ficha_no_observada_se_conserva_sin_crear_jugador(self):
        registro = ficha()
        registro.update(player_id=999, endpoint='/player/999', payload={'player': {'id': 999, 'preferredFoot': 'Right'}})
        ruta = self.exportacion('otro.jsonl', [registro])
        datos = importar.transformar(self.preparar(), FIJO, fichas=[ruta])
        self.assertNotIn(999, [p['player_id'] for p in datos['jugadores']])
        self.assertEqual(datos['fichas_originales'][0]['player_id'], 999)

    def test_rechaza_estructura_identidad_fecha_y_atributos_invalidos(self):
        entradas = self.preparar()
        invalidos = [None, [], {}, dict(ficha(), player_id=True), dict(ficha(), endpoint='/player/2'),
                     dict(ficha(), fetched_at='2026-10-23T10:00:00'), dict(ficha(), fetched_at='invalida'),
                     dict(ficha(), payload=[]), dict(ficha(), payload={'player': {'id': 2}})]
        for campo, valor in [('height', '180'), ('height', True), ('height', -1), ('preferredFoot', 1),
                             ('country', 'Spain'), ('dateOfBirthTimestamp', True), ('dateOfBirthTimestamp', float('inf'))]:
            r = copy.deepcopy(ficha())
            r['payload']['player'][campo] = valor
            invalidos.append(r)
        for i, r in enumerate(invalidos):
            with self.subTest(r=r):
                ruta = self.exportacion(f'invalida-{i}.jsonl', [r])
                with self.assertRaises(ValueError):
                    importar.transformar(entradas, FIJO, fichas=[ruta])

    def test_valor_no_reconocido_se_conserva_sin_inferencia(self):
        ruta = self.exportacion('desconocido.jsonl', [ficha('Unknown')])
        datos = importar.transformar(self.preparar(), FIJO, fichas=[ruta])
        self.assertEqual(datos['jugadores'][0]['preferred_foot'], 'Unknown')

    def test_opcion_cli_y_error_atomico(self):
        entradas = self.preparar()
        ruta = self.exportacion('ficha.jsonl', [ficha()])
        salida = self.carpeta / 'cli'
        command = [sys.executable, str(importar.Path(importar.__file__)), str(entradas[0][0]),
                   '--fichas-jugadores', str(ruta), '--salida', str(salida)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        jugadores = json.loads((salida / 'jugadores.json').read_text(encoding='utf-8'))
        self.assertEqual(jugadores[0]['preferred_foot'], 'Left')
        self.assertTrue((salida / 'fichas_originales.json').exists())
        rechazo = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(rechazo.returncode, 0)  # nunca sobrescribe
        ruta.write_text(json.dumps(dict(ficha(), endpoint='/player/2')), encoding='utf-8')
        nueva = self.carpeta / 'invalida'
        command[-1] = str(nueva)
        rechazo = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(rechazo.returncode, 0)
        self.assertFalse(nueva.exists())
        self.assertFalse(nueva.with_name('invalida.parcial').exists())

    def test_admite_registros_slice_del_scraper_junto_al_sobre_complementario(self):
        entradas = self.preparar()
        sobre = self.exportacion('david.jsonl', [ficha('Left', '2026-10-23T10:00:00Z')])
        scraper = self.exportacion('scraper.jsonl', [
            slice_ficha('Right', '2026-10-23T11:00:00Z'),
            dict(slice_ficha(), owner_id=2, state='empty', has_payload=False, fetched_at_utc=None, payload=None)])
        datos = importar.transformar(entradas, FIJO, fichas=[sobre, scraper])
        self.assertEqual(datos['jugadores'][0]['preferred_foot'], 'Right')  # la versión más reciente
        self.assertEqual([f['player_id'] for f in datos['fichas_originales']], [1, 1])
        self.assertEqual(datos['fichas_originales'][1]['endpoint'], '/player/1')
        info = datos['exportaciones']['exports'][-1]
        self.assertEqual((info['kind'], info['records'], info['without_profile']), ('player_profile', 1, 1))

    def test_rechaza_slices_de_otro_propietario_clave_o_identidad(self):
        entradas = self.preparar()
        invalidos = [dict(slice_ficha(), owner_kind='team'), dict(slice_ficha(), key='player_statistics'),
                     dict(slice_ficha(), sub='x'), dict(slice_ficha(), owner_id=True), dict(slice_ficha(), owner_id=2),
                     dict(slice_ficha(), fetched_at_utc=None), dict(slice_ficha(), has_payload=False)]
        for i, r in enumerate(invalidos):
            with self.subTest(r=r):
                ruta = self.exportacion(f'slice-invalido-{i}.jsonl', [r])
                with self.assertRaises(ValueError):
                    importar.transformar(entradas, FIJO, fichas=[ruta])


def slice_ficha(pie='Left', fecha='2026-10-23T10:00:00Z'):
    """Registro de `ssc export --dataset player_profiles` (modelo Slice del scraper, con su payload)."""
    return {'owner_kind': 'player', 'owner_id': 1, 'key': 'player_profile', 'sub': None, 'state': 'ok',
            'has_payload': True, 'fetched_at_utc': fecha, 'checked_at_utc': fecha, 'error': None,
            'payload': {'player': {'id': 1, 'preferredFoot': pie}}}
