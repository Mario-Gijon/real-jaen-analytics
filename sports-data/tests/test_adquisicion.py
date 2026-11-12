"""Durable handoff key: SQL fixtures only, no network or real data."""
import unittest
import uuid
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import test_actualizacion as fixtures

@unittest.skipUnless(fixtures.CON_SQL,'SQL dependencies required')
class SolicitudDurable(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.Actualizacion()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_clave_durable_y_colision(self):
        from persistencia.actualizacion import Coordinador,CoordinacionRechazada
        ident=str(uuid.uuid4())
        with self.fixture.abrir() as c:
            first=c.solicitar(exportaciones=[(self.fixture.fixture,2841)],identificador=ident)
            self.assertEqual(c.esperar(first)['estado'],'completed')
            generation=c.capturar().id
            self.assertEqual(c.solicitar(exportaciones=[(self.fixture.fixture,2841)],identificador=ident),first)
            self.assertEqual(c.capturar().id,generation)
            with self.assertRaises(CoordinacionRechazada):c.solicitar(identificador=ident)
        with Coordinador(self.fixture.base,fixtures.AMBITO,reserva=0) as c:
            self.assertEqual(c.solicitar(exportaciones=[(self.fixture.fixture,2841)],identificador=ident),first)
            self.assertEqual(c.capturar().id,generation)


class LimitesYRestriccion(unittest.TestCase):
    """Lo que declara un bundle de grupo: límites nunca por encima de producción, lo hecho dentro de ellos, y la
    restricción de la instalación exactamente."""
    def plan(self, **cambios):
        return dict(dict(now='x', rounds={}, planned=[], deferred={'5': 'restricted', '6': 'budget'}, new_matches=[],
                         limits=dict(attempts=60, sends=70), detail_rounds=[9]), **cambios)

    def test_limites_y_jornadas_declarados(self):
        from persistencia.bundle import Rejected, check_plan
        self.assertEqual(check_plan(self.plan(), [{}] * 60), (dict(attempts=60, sends=70), [9]))
        for plan, intentos in ((self.plan(limits=dict(attempts=117, sends=120)), 1), (self.plan(limits=dict(attempts=60, sends=121)), 1),
                               (self.plan(limits=dict(attempts=71, sends=70)), 1), (self.plan(), 61),
                               (self.plan(detail_rounds=[]), 1), (self.plan(detail_rounds=[10, 9]), 1), (self.plan(detail_rounds=[39]), 1),
                               (self.plan(detail_rounds=None), 1), (self.plan(deferred={'5': 'later'}), 1),
                               ({k: v for k, v in self.plan().items() if k != 'limits'}, 1)):
            with self.subTest(plan=plan, intentos=intentos), self.assertRaises(Rejected):
                check_plan(plan, [{}] * intentos)

    def test_la_restriccion_configurada_se_exige_exacta(self):
        from persistencia.adquisicion import comprobar_restriccion
        from persistencia.bundle import Rejected
        exigida = dict(limits=dict(attempts=60, sends=70), detail_rounds=[9])
        comprobar_restriccion(dict(plan=self.plan()), exigida)
        comprobar_restriccion(dict(plan=self.plan(detail_rounds=None, limits=dict(attempts=116, sends=120))), None)
        for plan in (self.plan(detail_rounds=None), self.plan(detail_rounds=[9, 10]), self.plan(limits=dict(attempts=61, sends=70))):
            with self.subTest(plan=plan), self.assertRaises(Rejected):
                comprobar_restriccion(dict(plan=plan), exigida)

    def test_la_restriccion_de_grupo_no_se_aplica_al_piloto(self):
        from persistencia.adquisicion import comprobar_restriccion
        comprobar_restriccion(dict(contract='pilot-v2'),
                              dict(limits=dict(attempts=60, sends=70), detail_rounds=[9]))

    def test_control_lee_las_tres_variables_o_ninguna(self):
        import control
        self.assertIsNone(control.restriccion({}))
        self.assertEqual(control.restriccion(dict(RJ_ACQUISITION_MAX_ATTEMPTS='60', RJ_ACQUISITION_MAX_SENDS='70',
                                                  RJ_ACQUISITION_DETAIL_ROUNDS='9')),
                         dict(limits=dict(attempts=60, sends=70), detail_rounds=[9]))
        with self.assertRaises(SystemExit):
            control.restriccion(dict(RJ_ACQUISITION_DETAIL_ROUNDS='9'))


class Productores(unittest.TestCase):
    """Only reviewed producer fingerprints pass the trust boundary; the current producer is one of them."""
    def test_productor_actual_y_del_bundle_real_aprobados(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from acquisition.recipe import producer_rules
        from persistencia.adquisicion import productores_aprobados
        self.assertIn(producer_rules(), productores_aprobados())
        self.assertIn('e18eba33cfbfd11b6ac39c6046ddacfe4436bb8f0b40775ba6b770f8a8c4d65a', productores_aprobados())

    def test_productor_no_aprobado_rechazado_antes_de_verificar(self):
        from persistencia.adquisicion import verificar_sellado
        from persistencia.bundle import Rejected, encode
        body = encode(dict(manifest=dict(rules_sha256='0' * 64), files={}))
        with self.assertRaisesRegex(Rejected, 'Unapproved producer'):
            verificar_sellado(body, dict(bytes=len(body), sha256='0' * 64), {}, ('a', 'b'))
        with self.assertRaisesRegex(Rejected, 'Malformed'):
            verificar_sellado(b'{"manifest":{},"manifest":{}}', {}, {}, ('a', 'b'))

    def test_aprobacion_limitada_a_su_generacion(self):
        from persistencia.adquisicion import productores_aprobados, verificar_sellado
        from persistencia.bundle import Rejected, encode
        legacy = 'e18eba33cfbfd11b6ac39c6046ddacfe4436bb8f0b40775ba6b770f8a8c4d65a'
        self.assertEqual(productores_aprobados()[legacy], '0e80fdde-95d9-44b0-a2ea-8426bce14b03')
        body = encode(dict(manifest=dict(rules_sha256=legacy), files={}))
        with self.assertRaisesRegex(Rejected, 'another generation'):
            verificar_sellado(body, dict(bytes=len(body), sha256='0' * 64), dict(generation_id='otra'), ('a', 'b'))

    def test_aprobar_productores_no_cambia_la_huella_de_reglas(self):
        from persistencia import carga
        archivos = carga.archivos_de_codigo()
        self.assertIn('queries.py', archivos)
        self.assertNotIn('productores-adquisicion.json', archivos)
        self.assertFalse(any('productores' in a for a in archivos))


class Availability(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='acquisition-readiness-')
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.base = root / 'sports.sqlite'
        self.coordinator = SimpleNamespace(base=self.base)
        self.secret = root / 'token'
        self.secret.write_bytes(b't' * 48)
        from persistencia.adquisicion import Cliente
        self.Cliente = Cliente

    def test_real_mode_requires_live_sofascore_service_and_rejects_test_installation(self):
        client = self.Cliente('http://acquisition:8002', self.secret)
        with mock.patch.object(client, '_json', return_value={'status': 'ok', 'provider': 'sofascore'}) as health:
            self.assertTrue(client.disponible(self.coordinator))
            health.assert_called_once_with('GET', base='http://acquisition:8002/health', limit=1024, timeout=2)
        with mock.patch.object(client, '_json', return_value={'status': 'ok', 'provider': 'fixture-site'}):
            self.assertFalse(client.disponible(self.coordinator))

    def test_test_mode_requires_test_marker_and_fixture_provider(self):
        from persistencia.adquisicion import TEST_CONTENT, TEST_MARKER
        client = self.Cliente('http://acquisition:8002', self.secret, modo='prueba')
        with mock.patch.object(client, '_json', return_value={'status': 'ok', 'provider': 'fixture-site'}):
            self.assertFalse(client.disponible(self.coordinator))
            marker = self.base.with_name(self.base.name + TEST_MARKER)
            marker.write_text(TEST_CONTENT)
            self.assertTrue(client.disponible(self.coordinator))
            marker.write_text('unrecognized\n')
            self.assertFalse(client.disponible(self.coordinator))


class PublicationWait(unittest.TestCase):
    def test_timeout_termina_una_ventana_y_devuelve_pendiente(self):
        from persistencia.adquisicion import _esperar_publicacion

        class Coordinator:
            def __init__(self):
                self.calls = []

            def esperar(self, ident, timeout):
                self.calls.append((ident, timeout))
                raise TimeoutError(ident)

        coordinator = Coordinator()
        result = _esperar_publicacion(coordinator, 'durable-id', timeout=7)
        self.assertEqual(result, dict(estado='pending', identificador='durable-id'))
        self.assertEqual(coordinator.calls, [('durable-id', 7)])

    def test_publicacion_completada_despues_del_timeout_reusa_el_mismo_id(self):
        from persistencia.adquisicion import Cliente

        with tempfile.TemporaryDirectory(prefix='acquisition-publication-timeout-') as tmp:
            root = Path(tmp)
            secret = root / 'secret'
            secret.write_bytes(b't' * 48)
            request_id = str(uuid.uuid4())
            acquisition_id = str(uuid.uuid4())
            baseline = dict(generation_id=str(uuid.uuid4()), sha256='baseline')
            descriptor = dict(bytes=14, sha256='descriptor')
            publication_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
                                            f'urn:rj:acquisition:{acquisition_id}:descriptor:1'))
            folder = root / 'adquisiciones' / request_id
            folder.mkdir(parents=True)
            (folder / 'bundle.json').write_bytes(b'fixture bundle')
            record = dict(bundle=descriptor, acquisition_id=acquisition_id, request_id=request_id,
                          recipe_id='sofascore-group2-2627-v1', intentos=[])
            from persistencia.bundle import encode
            (folder / 'solicitud.json').write_bytes(encode(record))
            (folder / 'inventory.json').write_bytes(encode(baseline))

            class Coordinator:
                def __init__(self):
                    self.directory = root
                    self.directorio = root
                    self.status = 'queued'
                    self.waits = []
                    self.requested = []
                    self.generation = baseline['generation_id']

                def estado(self, ident):
                    if ident != publication_id:
                        raise KeyError(ident)
                    return dict(estado=self.status, generacion=self.generation)

                def capturar(self):
                    return SimpleNamespace(id=self.generation)

                def solicitar(self, **kwargs):
                    self.requested.append(kwargs['identificador'])

                def esperar(self, ident, timeout):
                    self.waits.append((ident, timeout))
                    if self.status != 'completed':
                        raise TimeoutError(ident)
                    return dict(estado='completed', generacion=self.generation)

            coordinator = Coordinator()
            client = Cliente('http://acquisition:8002', secret, modo='prueba')
            manifest = dict(recipe_id=record['recipe_id'], contract='pilot-v2')
            with mock.patch('persistencia.adquisicion.verificar_sellado', return_value=(manifest, {})), \
                    mock.patch('persistencia.adquisicion.comprobar_restriccion'), \
                    mock.patch('persistencia.adquisicion.inventory', return_value=baseline):
                pending = client.recibir(coordinator, folder, record, baseline)
                self.assertEqual(pending['state'], 'pending')
                self.assertEqual(pending['job_id'], publication_id)

                coordinator.status = 'completed'
                coordinator.generation = 'published-generation'
                result = client.reconciliar_publicacion(coordinator, request_id, record['recipe_id'], publication_id)

            self.assertEqual(coordinator.requested, [publication_id])
            self.assertEqual([ident for ident, _ in coordinator.waits], [publication_id, publication_id])
            self.assertEqual(result['state'], 'published')
            self.assertEqual(result['generation_id'], 'published-generation')

    def test_reconciliacion_permanentemente_pendiente_no_reenvia_solicitud(self):
        from persistencia.adquisicion import Cliente
        from persistencia.bundle import encode

        with tempfile.TemporaryDirectory(prefix='acquisition-permanent-pending-') as tmp:
            root = Path(tmp)
            secret = root / 'secret'
            secret.write_bytes(b't' * 48)
            request_id, acquisition_id = str(uuid.uuid4()), str(uuid.uuid4())
            coordinator_id = str(uuid.uuid4())
            descriptor = dict(bytes=14, sha256='descriptor')
            baseline = dict(generation_id='base-generation', sha256='baseline')
            folder = root / 'adquisiciones' / request_id
            folder.mkdir(parents=True)
            (folder / 'solicitud.json').write_bytes(encode(dict(request_id=request_id,
                recipe_id='sofascore-group2-2627-v1', bundle=descriptor, acquisition_id=acquisition_id,
                intentos=[coordinator_id])))
            (folder / 'inventory.json').write_bytes(encode(baseline))
            (folder / 'bundle.json').write_bytes(b'fixture bundle')

            class Coordinator:
                directorio = root

                def __init__(self):
                    self.requested = []

                def capturar(self):
                    return SimpleNamespace(id='base-generation')

                def estado(self, _ident):
                    return dict(estado='running')

                def solicitar(self, **kwargs):
                    self.requested.append(kwargs)

            coordinator = Coordinator()
            client = Cliente('http://acquisition:8002', secret, modo='prueba')
            result = client.reconciliar_publicacion(coordinator, request_id,
                'sofascore-group2-2627-v1', coordinator_id)
            self.assertEqual((result['state'], result['job_id']), ('pending', coordinator_id))
            self.assertEqual(coordinator.requested, [])
