"""Modelos SQLAlchemy de la persistencia (docs/persistencia-sqlite.md, sección 2). Solo esquema: ni carga ni lectura.

Capas:
- Identidad: entidades con uid estable, identificadores externos por proveedor y decisiones de identidad.
- Evidencia: importaciones, ficheros, contenidos y observaciones; inmutable.
- Decisiones: declaraciones de equipo propio y sustituciones locales; con alta y baja auditadas.
- Estado operativo: lo derivado de la evidencia con una versión de reglas (`rule_set`); reconstruible.

Valores de la fuente cuyo tipo importa (true frente a 1, null frente a ausente) van como literal JSON en texto:
NULL de SQL = clave ausente; 'null' = null explícito; 'true', '1', '"4"', '-1', '1.5' se conservan tal cual. Las
restricciones protegen la forma del estado operativo, nunca el contenido de la evidencia: un valor inválido o
contradictorio de la fuente se puede guardar.
"""
from sqlalchemy import (DDL, Boolean, CheckConstraint, Date, ForeignKey, ForeignKeyConstraint, Index, Integer, MetaData,
                        event,
                        String, Text, UniqueConstraint, text)
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

from persistencia.sqlite import hexadecimal, indice_parcial, json_valido, texto_no_vacio

CONVENCION = {
    'ix': 'ix_%(table_name)s_%(column_0_N_name)s',
    'uq': 'uq_%(table_name)s_%(column_0_N_name)s',
    'ck': 'ck_%(table_name)s_%(constraint_name)s',
    'fk': 'fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s',
    'pk': 'pk_%(table_name)s',
}

PROVEEDORES_ID = ('team', 'player', 'match', 'competition', 'season', 'stage')
CAMPOS_FICHA = ('name', 'usual_position', 'country', 'birth_date', 'height_cm', 'preferred_foot')


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=CONVENCION)


def booleano(nombre, nullable=False):
    """Booleano con CHECK 0/1: SQLite admitiría cualquier entero."""
    return mapped_column(Boolean(create_constraint=True, name=nombre), nullable=nullable)


def en(columna, valores):
    return f"{columna} IN ({', '.join(repr(v) for v in valores)})"


def no_nulos(columnas):
    """Número de columnas no nulas, en SQL portable."""
    return ' + '.join(f'(CASE WHEN {c} IS NOT NULL THEN 1 ELSE 0 END)' for c in columnas)


def json(*columnas, nulo=True):
    return [CheckConstraint(json_valido(c, nulo), name=f'{c}_json') for c in columnas]


# ─── Evidencia: ejecuciones ─────────────────────────────────────────────────────────────────────────────────────

class ImportRun(Base):
    """Cada ejecución que escribe en la base."""
    __tablename__ = 'import_run'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    started_at: Mapped[str] = mapped_column(String)
    finished_at: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    tool_version: Mapped[str | None] = mapped_column(String)
    command: Mapped[str | None] = mapped_column(Text)
    rule_set_ids: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        CheckConstraint(en('kind', ('initial_load', 'import', 'rebuild', 'decisions', 'identity_decisions')), name='kind'),
        CheckConstraint(en('status', ('running', 'committed', 'failed', 'no_changes')), name='status'),
        CheckConstraint("(status = 'running') = (finished_at IS NULL)", name='finished'),
        CheckConstraint("status <> 'failed' OR error IS NOT NULL", name='failed_error'),
        *json('rule_set_ids', 'summary'),
    )


# ─── Identidad ──────────────────────────────────────────────────────────────────────────────────────────────────

class Entidad:
    """Entidad con identificador interno (uniones), uid estable (servicios) y fusión sin pérdida: una entidad
    fusionada sigue existiendo y apunta a la que queda."""
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uid: Mapped[str] = mapped_column(String(36), unique=True)
    status: Mapped[str] = mapped_column(String, server_default=text("'active'"))

    @declared_attr
    def merged_into_id(cls) -> Mapped[int | None]:
        return mapped_column(ForeignKey(f'{cls.__tablename__}.id'))

    @declared_attr
    def created_in_run(cls) -> Mapped[int]:
        return mapped_column(ForeignKey('import_run.id'))

    @classmethod
    def restricciones(cls):
        return (
            CheckConstraint('length(uid) = 36', name='uid'),
            CheckConstraint(en('status', ('active', 'merged')), name='status'),
            CheckConstraint("(status = 'merged') = (merged_into_id IS NOT NULL)", name='merged_into'),
            CheckConstraint('merged_into_id IS NULL OR merged_into_id <> id', name='not_self'),
        )


class Competition(Entidad, Base):
    """Competición general (SofaScore: uniqueTournament)."""
    __tablename__ = 'competition'
    __table_args__ = Entidad.restricciones()


class Season(Entidad, Base):
    """Temporada de una competición (SofaScore: season). Competición desconocida: NULL, nunca deducida."""
    __tablename__ = 'season'
    competition_id: Mapped[int | None] = mapped_column(ForeignKey('competition.id'))
    label: Mapped[str | None] = mapped_column(String)
    __table_args__ = Entidad.restricciones()


class Stage(Entidad, Base):
    """Etapa o grupo (SofaScore: tournament). No se ata a una temporada: un id de copa puede repetirse."""
    __tablename__ = 'stage'
    competition_id: Mapped[int | None] = mapped_column(ForeignKey('competition.id'))
    __table_args__ = Entidad.restricciones()


class Team(Entidad, Base):
    __tablename__ = 'team'
    __table_args__ = Entidad.restricciones()


class Player(Entidad, Base):
    __tablename__ = 'player'
    __table_args__ = Entidad.restricciones()


class Match(Entidad, Base):
    __tablename__ = 'match'
    __table_args__ = Entidad.restricciones()


class IdentityDecision(Base):
    """Decisión de identidad (fusión, división, reasignación o rechazo de un id externo, reversión). Es un dato:
    una reconstrucción nunca la recalcula. Sin herramienta todavía."""
    __tablename__ = 'identity_decision'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uid: Mapped[str] = mapped_column(String(36), unique=True)
    kind: Mapped[str] = mapped_column(String)
    entity_type: Mapped[str] = mapped_column(String)
    reason: Mapped[str] = mapped_column(Text)
    decided_by: Mapped[str] = mapped_column(String)
    decided_on = mapped_column(Date, nullable=False)
    evidence: Mapped[str] = mapped_column(Text)
    run_id: Mapped[int] = mapped_column(ForeignKey('import_run.id'))
    reverts_decision_id: Mapped[int | None] = mapped_column(ForeignKey('identity_decision.id'))
    __table_args__ = (
        CheckConstraint('length(uid) = 36', name='uid'),
        CheckConstraint(en('kind', ('merge', 'split', 'reassign_external_id', 'reject_external_id', 'revert')), name='kind'),
        CheckConstraint(en('entity_type', PROVEEDORES_ID), name='entity_type'),
        CheckConstraint(texto_no_vacio('reason'), name='reason'),
        CheckConstraint(texto_no_vacio('decided_by'), name='decided_by'),
        CheckConstraint(texto_no_vacio('evidence'), name='evidence'),
        CheckConstraint("(kind = 'revert') = (reverts_decision_id IS NOT NULL)", name='revert'),
    )


class ExternalId:
    """Identificador de una entidad en un proveedor, como texto, único por (proveedor, espacio de nombres, id)
    entre los vivos. `origin`: la entidad nació de él; `verified`: asociación decidida; `rejected`: anulada y
    conservada."""
    __entidad__: str
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String)
    namespace: Mapped[str] = mapped_column(String, server_default=text("''"))
    external_id: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)

    @declared_attr
    def entity_id(cls) -> Mapped[int]:
        return mapped_column(ForeignKey(f'{cls.__entidad__}.id'))

    @declared_attr
    def decision_id(cls) -> Mapped[int | None]:
        return mapped_column(ForeignKey('identity_decision.id'))

    @declared_attr
    def created_in_run(cls) -> Mapped[int]:
        return mapped_column(ForeignKey('import_run.id'))

    @staticmethod
    def restricciones(tabla):
        return (
            CheckConstraint(texto_no_vacio('provider'), name='provider'),
            CheckConstraint(texto_no_vacio('external_id'), name='external_id'),
            CheckConstraint(en('status', ('origin', 'verified', 'rejected')), name='status'),
            CheckConstraint("status = 'origin' OR decision_id IS NOT NULL", name='decision'),
            Index(f'uq_{tabla}_live', 'provider', 'namespace', 'external_id', unique=True,
                  **indice_parcial("status <> 'rejected'")),
            Index(f'uq_{tabla}_origin', 'entity_id', unique=True, **indice_parcial("status = 'origin'")),
            Index(f'ix_{tabla}_entity_id', 'entity_id'),
        )


class CompetitionExternalId(ExternalId, Base):
    __tablename__, __entidad__ = 'competition_external_id', 'competition'
    __table_args__ = ExternalId.restricciones('competition_external_id')


class SeasonExternalId(ExternalId, Base):
    __tablename__, __entidad__ = 'season_external_id', 'season'
    __table_args__ = ExternalId.restricciones('season_external_id')


class StageExternalId(ExternalId, Base):
    __tablename__, __entidad__ = 'stage_external_id', 'stage'
    __table_args__ = ExternalId.restricciones('stage_external_id')


class TeamExternalId(ExternalId, Base):
    __tablename__, __entidad__ = 'team_external_id', 'team'
    __table_args__ = ExternalId.restricciones('team_external_id')


class PlayerExternalId(ExternalId, Base):
    __tablename__, __entidad__ = 'player_external_id', 'player'
    __table_args__ = ExternalId.restricciones('player_external_id')


class MatchExternalId(ExternalId, Base):
    __tablename__, __entidad__ = 'match_external_id', 'match'
    __table_args__ = ExternalId.restricciones('match_external_id')


SUJETO_ENTIDAD = tuple(f'{t}_id' for t in PROVEEDORES_ID)
SUJETO_EXTERNO = tuple(f'{t}_external_id_id' for t in PROVEEDORES_ID)


class IdentityDecisionSubject(Base):
    """A quién afecta una decisión: una entidad o una fila de identificador externo, con FK reales por tipo."""
    __tablename__ = 'identity_decision_subject'
    decision_id: Mapped[int] = mapped_column(ForeignKey('identity_decision.id'), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    role: Mapped[str] = mapped_column(String)
    team_id: Mapped[int | None] = mapped_column(ForeignKey('team.id'))
    player_id: Mapped[int | None] = mapped_column(ForeignKey('player.id'))
    match_id: Mapped[int | None] = mapped_column(ForeignKey('match.id'))
    competition_id: Mapped[int | None] = mapped_column(ForeignKey('competition.id'))
    season_id: Mapped[int | None] = mapped_column(ForeignKey('season.id'))
    stage_id: Mapped[int | None] = mapped_column(ForeignKey('stage.id'))
    team_external_id_id: Mapped[int | None] = mapped_column(ForeignKey('team_external_id.id'))
    player_external_id_id: Mapped[int | None] = mapped_column(ForeignKey('player_external_id.id'))
    match_external_id_id: Mapped[int | None] = mapped_column(ForeignKey('match_external_id.id'))
    competition_external_id_id: Mapped[int | None] = mapped_column(ForeignKey('competition_external_id.id'))
    season_external_id_id: Mapped[int | None] = mapped_column(ForeignKey('season_external_id.id'))
    stage_external_id_id: Mapped[int | None] = mapped_column(ForeignKey('stage_external_id.id'))
    __table_args__ = (
        CheckConstraint(en('role', ('source', 'target', 'created', 'external_id')), name='role'),
        CheckConstraint(f'{no_nulos(SUJETO_ENTIDAD)} <= 1', name='one_entity'),
        CheckConstraint(f'{no_nulos(SUJETO_EXTERNO)} <= 1', name='one_external_id'),
        CheckConstraint(f'{no_nulos(SUJETO_ENTIDAD + SUJETO_EXTERNO)} >= 1', name='some_subject'),
    )


# ─── Evidencia: ficheros, contenidos y observaciones ─────────────────────────────────────────────────────────────

class SourceFile(Base):
    """Un fichero de entrada identificado por su contenido (sha256): presentarlo otra vez no añade evidencia. El
    nombre y la ruta con que se presentó son metadatos de cada ejecución (import_run_file)."""
    __tablename__ = 'source_file'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String)
    label: Mapped[str] = mapped_column(String, unique=True)
    records: Mapped[int] = mapped_column(Integer)
    events: Mapped[int | None] = mapped_column(Integer)
    players: Mapped[int | None] = mapped_column(Integer)
    invalid_fetched_at: Mapped[int | None] = mapped_column(Integer)
    without_profile: Mapped[int | None] = mapped_column(Integer)
    fetched_from: Mapped[str | None] = mapped_column(String)
    fetched_to: Mapped[str | None] = mapped_column(String)
    first_run_id: Mapped[int] = mapped_column(ForeignKey('import_run.id'))
    __table_args__ = (
        CheckConstraint(hexadecimal('sha256', 64), name='sha256'),
        CheckConstraint(en('kind', ('raw', 'listing', 'player_profile', 'local_decisions', 'identity_decisions')), name='kind'),
        CheckConstraint(texto_no_vacio('label'), name='label'),
        CheckConstraint('records >= 0', name='records'),
    )


class ImportRunFile(Base):
    """Cómo se presentó un fichero en una ejecución: orden, nombre, ruta y si era nuevo o ya conocido."""
    __tablename__ = 'import_run_file'
    run_id: Mapped[int] = mapped_column(ForeignKey('import_run.id'), primary_key=True)
    source_file_id: Mapped[int] = mapped_column(ForeignKey('source_file.id'), primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    presented_name: Mapped[str] = mapped_column(String)
    presented_path: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String)
    __table_args__ = (
        UniqueConstraint('run_id', 'position'),
        CheckConstraint('position >= 1', name='position'),
        CheckConstraint(en('outcome', ('ingested', 'already_known')), name='outcome'),
    )


class FileTeamDeclaration(Base):
    """«Exportación propia del equipo», separada del contenido: un fichero conocido puede recibir declaraciones
    nuevas sin duplicar evidencia; retirar una es otra decisión auditada. Varios equipos por fichero, cada uno
    validado al declararlo."""
    __tablename__ = 'file_team_declaration'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_file_id: Mapped[int] = mapped_column(ForeignKey('source_file.id'))
    team_id: Mapped[int] = mapped_column(ForeignKey('team.id'))
    external_team_id: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    declared_in_run: Mapped[int] = mapped_column(ForeignKey('import_run.id'))
    declared_by: Mapped[str | None] = mapped_column(String)
    withdrawn_in_run: Mapped[int | None] = mapped_column(ForeignKey('import_run.id'))
    withdrawn_by: Mapped[str | None] = mapped_column(String)
    withdrawn_reason: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        CheckConstraint(en('status', ('active', 'withdrawn')), name='status'),
        CheckConstraint("(status = 'withdrawn') = (withdrawn_in_run IS NOT NULL AND withdrawn_by IS NOT NULL "
                        "AND withdrawn_reason IS NOT NULL)", name='withdrawn'),
        Index('uq_file_team_declaration_active', 'source_file_id', 'team_id', unique=True,
              **indice_parcial("status = 'active'")),
    )


class Content(Base):
    """Payload original, deduplicado por su huella canónica. `payload_text` conserva el orden de claves del original
    y es lo que se sirve en las secciones."""
    __tablename__ = 'content'
    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload_text: Mapped[str] = mapped_column(Text)
    text_sha256: Mapped[str] = mapped_column(String(64))
    bytes: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        CheckConstraint(hexadecimal('hash', 64), name='hash'),
        CheckConstraint(hexadecimal('text_sha256', 64), name='text_sha256'),
        CheckConstraint('bytes >= 0', name='bytes'),
        *json('payload_text', nulo=False),
    )


class Observation(Base):
    """Cada línea de entrada, con su procedencia. `id` es el orden de ingestión (desempates). Los ids externos se
    guardan como texto tal cual; los resueltos (match_id, player_id) van al lado y pueden faltar."""
    __tablename__ = 'observation'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_file_id: Mapped[int] = mapped_column(ForeignKey('source_file.id'))
    line: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String)
    provider: Mapped[str] = mapped_column(String)
    external_event_id: Mapped[str | None] = mapped_column(String)
    external_player_id: Mapped[str | None] = mapped_column(String)
    key: Mapped[str] = mapped_column(String)
    sub: Mapped[str] = mapped_column(String, server_default=text("''"))
    endpoint: Mapped[str | None] = mapped_column(String)
    fetched_at_raw: Mapped[str | None] = mapped_column(String)
    fetched_instant: Mapped[str | None] = mapped_column(String)
    fetched_at_valid: Mapped[bool] = booleano('fetched_at_valid')
    content_hash: Mapped[str] = mapped_column(ForeignKey('content.hash'))
    envelope_extra: Mapped[str | None] = mapped_column(Text)
    match_id: Mapped[int | None] = mapped_column(ForeignKey('match.id'))
    player_id: Mapped[int | None] = mapped_column(ForeignKey('player.id'))
    __table_args__ = (
        UniqueConstraint('source_file_id', 'line'),
        # Destinos de claves foráneas compuestas: una selección o una convocatoria sólo pueden apuntar a una
        # observación del mismo partido y sección.
        UniqueConstraint('id', 'match_id', 'key', 'sub'),
        CheckConstraint('line >= 1', name='line'),
        CheckConstraint(en('kind', ('section', 'listing', 'player_profile')), name='kind'),
        CheckConstraint(texto_no_vacio('provider'), name='provider'),
        CheckConstraint(texto_no_vacio('key'), name='key'),
        CheckConstraint("kind = 'player_profile' OR external_event_id IS NOT NULL", name='event_id'),
        CheckConstraint("kind <> 'player_profile' OR (external_player_id IS NOT NULL AND key = 'player_profile')",
                        name='profile'),
        CheckConstraint("(kind = 'listing') = (key = 'listing')", name='listing'),
        CheckConstraint('fetched_at_valid = (fetched_instant IS NOT NULL)', name='fetched'),
        *json('envelope_extra'),
        Index('ix_observation_section', 'match_id', 'key', 'sub'),
        Index('ix_observation_external_section', 'provider', 'external_event_id', 'key', 'sub'),
        Index('ix_observation_player_id', 'player_id'),
        Index('ix_observation_content_hash', 'content_hash'),
    )


# ─── Estado operativo (reconstruible) ────────────────────────────────────────────────────────────────────────────

class RuleSet(Base):
    """Versión de una regla materializada (p. ej. importar.seleccionar@2), con el código que la implementa."""
    __tablename__ = 'rule_set'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    version: Mapped[str] = mapped_column(String)
    code_ref: Mapped[str] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint('name', 'version'),)


class Derivado:
    """Fila del estado operativo: qué regla y qué ejecución la produjeron."""

    @declared_attr
    def rule_set_id(cls) -> Mapped[int]:
        return mapped_column(ForeignKey('rule_set.id'))

    @declared_attr
    def run_id(cls) -> Mapped[int]:
        return mapped_column(ForeignKey('import_run.id'))


def seleccion_de(columnas, nombre):
    """FK compuesta a la sección seleccionada (partido, clave, sub, observación): la fila deriva de la versión
    operativa. Diferida: una reconstrucción puede cambiar la selección y sus derivados en la misma transacción."""
    return ForeignKeyConstraint(columnas, ['section_selection.match_id', 'section_selection.key', 'section_selection.sub',
                                           'section_selection.observation_id'],
                                name=nombre, deferrable=True, initially='DEFERRED')


def observacion_de(columnas, nombre):
    """FK compuesta a una observación del mismo partido y sección."""
    return ForeignKeyConstraint(columnas, ['observation.id', 'observation.match_id', 'observation.key', 'observation.sub'],
                                name=nombre)


class SectionSelection(Derivado, Base):
    """La versión operativa de cada sección (partido, clave, sub)."""
    __tablename__ = 'section_selection'
    match_id: Mapped[int] = mapped_column(ForeignKey('match.id'), primary_key=True)
    key: Mapped[str] = mapped_column(String, primary_key=True)
    sub: Mapped[str] = mapped_column(String, primary_key=True, server_default=text("''"))
    observation_id: Mapped[int] = mapped_column(Integer)
    versions: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        UniqueConstraint('match_id', 'key', 'sub', 'observation_id'),
        observacion_de(['observation_id', 'match_id', 'key', 'sub'], 'fk_section_selection_observation'),
        CheckConstraint('versions >= 1', name='versions'),
    )


class SectionSelectionHistory(Base):
    """Selecciones anteriores con su vigencia. No basta por sí sola para reproducir respuestas pasadas (3.5)."""
    __tablename__ = 'section_selection_history'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey('match.id'))
    key: Mapped[str] = mapped_column(String)
    sub: Mapped[str] = mapped_column(String, server_default=text("''"))
    observation_id: Mapped[int] = mapped_column(Integer)
    versions: Mapped[int] = mapped_column(Integer)
    rule_set_id: Mapped[int] = mapped_column(ForeignKey('rule_set.id'))
    valid_from_run: Mapped[int] = mapped_column(ForeignKey('import_run.id'))
    valid_to_run: Mapped[int | None] = mapped_column(ForeignKey('import_run.id'))
    __table_args__ = (
        observacion_de(['observation_id', 'match_id', 'key', 'sub'], 'fk_section_selection_history_observation'),
        CheckConstraint('versions >= 1', name='versions'),
        Index('ix_section_selection_history_section', 'match_id', 'key', 'sub'),
    )


class Discrepancy(Derivado, Base):
    """Versiones que no coinciden (lo que hoy es discrepancias.json). Nunca se resuelven a mano."""
    __tablename__ = 'discrepancy'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    match_id: Mapped[int | None] = mapped_column(ForeignKey('match.id'))
    player_id: Mapped[int | None] = mapped_column(ForeignKey('player.id'))
    key: Mapped[str] = mapped_column(String)
    sub: Mapped[str] = mapped_column(String, server_default=text("''"))
    integrity: Mapped[bool] = booleano('integrity')
    selected_observation_id: Mapped[int | None] = mapped_column(ForeignKey('observation.id'))
    changed_fields: Mapped[str | None] = mapped_column(Text)
    superseded_in_run: Mapped[int | None] = mapped_column(ForeignKey('import_run.id'))
    __table_args__ = (
        CheckConstraint(en('kind', ('section', 'listing_vs_event', 'player_attribute')), name='kind'),
        CheckConstraint("(kind = 'player_attribute') = (player_id IS NOT NULL AND match_id IS NULL)", name='subject_player'),
        CheckConstraint("kind = 'player_attribute' OR (match_id IS NOT NULL AND player_id IS NULL)", name='subject_match'),
        *json('changed_fields'),
        Index('ix_discrepancy_match_id', 'match_id'),
        Index('ix_discrepancy_player_id', 'player_id'),
    )


class DiscrepancyReason(Base):
    """Motivos de una discrepancia, en el orden en que los da la regla (forma parte de lo publicado)."""
    __tablename__ = 'discrepancy_reason'
    discrepancy_id: Mapped[int] = mapped_column(ForeignKey('discrepancy.id'), primary_key=True)
    reason: Mapped[str] = mapped_column(String, primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint('discrepancy_id', 'ordinal'), CheckConstraint('ordinal >= 0', name='ordinal'))


class DiscrepancyObservation(Base):
    """Todas las versiones que intervienen en una discrepancia."""
    __tablename__ = 'discrepancy_observation'
    discrepancy_id: Mapped[int] = mapped_column(ForeignKey('discrepancy.id'), primary_key=True)
    observation_id: Mapped[int] = mapped_column(ForeignKey('observation.id'), primary_key=True)


class DiscrepancyValue(Base):
    """Valores distintos observados de un atributo de jugador, en su orden."""
    __tablename__ = 'discrepancy_value'
    discrepancy_id: Mapped[int] = mapped_column(ForeignKey('discrepancy.id'), primary_key=True)
    field: Mapped[str] = mapped_column(String, primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    value_json: Mapped[str] = mapped_column(Text)
    __table_args__ = (*json('value_json', nulo=False),)


class MatchState(Derivado, Base):
    """El partido operativo (lo que hoy es una fila de partidos.json), desde el `event` o `listing` seleccionado.
    Ids externos y nombres tal como llegaron; marcador y jornada con su tipo original."""
    __tablename__ = 'match_state'
    match_id: Mapped[int] = mapped_column(ForeignKey('match.id'), primary_key=True)
    source_key: Mapped[str] = mapped_column(String)
    source_sub: Mapped[str] = mapped_column(String, server_default=text("''"))
    source_observation_id: Mapped[int] = mapped_column(Integer)
    stage_id: Mapped[int | None] = mapped_column(ForeignKey('stage.id'))
    season_id: Mapped[int | None] = mapped_column(ForeignKey('season.id'))
    competition_id: Mapped[int | None] = mapped_column(ForeignKey('competition.id'))
    external_stage_id: Mapped[str | None] = mapped_column(String)
    external_season_id: Mapped[str | None] = mapped_column(String)
    external_competition_id: Mapped[str | None] = mapped_column(String)
    external_home_team_id: Mapped[str] = mapped_column(String)
    external_away_team_id: Mapped[str] = mapped_column(String)
    competition_name: Mapped[str | None] = mapped_column(String)
    season_label: Mapped[str | None] = mapped_column(String)
    status: Mapped[str | None] = mapped_column(String)
    kickoff_utc: Mapped[str | None] = mapped_column(String)
    round: Mapped[str | None] = mapped_column(Text)
    home_team_id: Mapped[int] = mapped_column(ForeignKey('team.id'))
    away_team_id: Mapped[int] = mapped_column(ForeignKey('team.id'))
    home_score: Mapped[str | None] = mapped_column(Text)
    away_score: Mapped[str | None] = mapped_column(Text)
    calendar_source: Mapped[str] = mapped_column(String)
    version_conflict: Mapped[bool] = booleano('version_conflict')
    __table_args__ = (
        seleccion_de(['match_id', 'source_key', 'source_sub', 'source_observation_id'], 'fk_match_state_selection'),
        CheckConstraint(en('source_key', ('event', 'listing')), name='source_key'),
        CheckConstraint('source_key = calendar_source', name='calendar_source'),
        *json('round', 'home_score', 'away_score'),
        Index('ix_match_state_home_team_id', 'home_team_id'),
        Index('ix_match_state_away_team_id', 'away_team_id'),
        Index('ix_match_state_stage_season', 'stage_id', 'season_id'),
    )


class MatchVersionIssue(Base):
    __tablename__ = 'match_version_issue'
    match_id: Mapped[int] = mapped_column(ForeignKey('match_state.match_id'), primary_key=True)
    reason: Mapped[str] = mapped_column(String, primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint('match_id', 'ordinal'), CheckConstraint('ordinal >= 0', name='ordinal'))


class TeamName(Derivado, Base):
    """Nombre publicado de cada equipo (regla actual: el primero visto en orden de ingestión)."""
    __tablename__ = 'team_name'
    team_id: Mapped[int] = mapped_column(ForeignKey('team.id'), primary_key=True)
    name: Mapped[str | None] = mapped_column(String)
    source_observation_id: Mapped[int] = mapped_column(ForeignKey('observation.id'))


class CallUp(Derivado, Base):
    """Cada convocatoria de un jugador en un partido y un lado (lo que hoy es jugador_partido), extraída con
    importar.extraer_convocatorias de la alineación seleccionada. Su identidad es (partido, lado, jugador): única, y su
    id se conserva aunque cambie la versión de la alineación.

    Vigencia: `active` mientras la alineación seleccionada la incluye. Si una versión posterior deja de incluirla pasa
    a `retired` (no se borra ni se reutiliza su id) y conserva la última alineación que la incluía y sus estadísticas:
    es solo su último estado, no su historial, que está completo en las observaciones. Si reaparece, se reactiva la
    misma fila. Las transiciones quedan en call_up_change. Que toda convocatoria activa salga de la alineación
    seleccionada se comprueba antes de confirmar cada escritura (persistencia.sincronizacion.problemas_de_estado):
    SQLite no admite claves foráneas condicionales. Las lecturas usan la vista call_up_vigente."""
    __tablename__ = 'call_up'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey('match.id'))
    lineup_key: Mapped[str] = mapped_column(String, server_default=text("'lineups'"))
    lineup_sub: Mapped[str] = mapped_column(String, server_default=text("''"))
    lineup_observation_id: Mapped[int] = mapped_column(Integer)
    side: Mapped[str] = mapped_column(String)
    entry_index: Mapped[int] = mapped_column(Integer)
    team_id: Mapped[int] = mapped_column(ForeignKey('team.id'))
    external_player_id: Mapped[str] = mapped_column(String)
    player_id: Mapped[int | None] = mapped_column(ForeignKey('player.id'))
    external_team_id: Mapped[str | None] = mapped_column(String)
    substitute: Mapped[str | None] = mapped_column(Text)
    shirt_number: Mapped[str | None] = mapped_column(Text)
    minutes_played: Mapped[str | None] = mapped_column(Text)
    match_position: Mapped[str | None] = mapped_column(Text)
    played: Mapped[bool | None] = booleano('played', nullable=True)
    statistics_kind: Mapped[str] = mapped_column(String)
    # Al final, como las añade la migración 0004.
    status: Mapped[str] = mapped_column(String, server_default=text("'active'"), sort_order=10)
    retired_in_run: Mapped[int | None] = mapped_column(ForeignKey('import_run.id'), sort_order=11)
    __table_args__ = (
        observacion_de(['lineup_observation_id', 'match_id', 'lineup_key', 'lineup_sub'], 'fk_call_up_lineup_observation'),
        UniqueConstraint('lineup_observation_id', 'side', 'entry_index'),
        UniqueConstraint('match_id', 'side', 'external_player_id'),
        CheckConstraint(en('status', ('active', 'retired')), name='status'),
        CheckConstraint("(status = 'retired') = (retired_in_run IS NOT NULL)", name='retired'),
        CheckConstraint("lineup_key = 'lineups'", name='lineup_key'),
        CheckConstraint(en('side', ('home', 'away')), name='side'),
        CheckConstraint('entry_index >= 0', name='entry_index'),
        CheckConstraint(texto_no_vacio('external_player_id'), name='external_player_id'),
        # statistics de la entrada: objeto (sus claves en call_up_stat), null explícito, ausente u otro tipo (el
        # original sigue en content).
        CheckConstraint(en('statistics_kind', ('object', 'null', 'absent', 'other')), name='statistics_kind'),
        *json('substitute', 'shirt_number', 'minutes_played', 'match_position'),
        Index('ix_call_up_match_player', 'match_id', 'player_id'),
        Index('ix_call_up_player_id', 'player_id'),
    )


class CallUpStat(Base):
    """Cada clave presente de `statistics` de una convocatoria, con su literal JSON original y su posición.
    Ausente = sin fila; null = 'null'; un valor inválido se guarda tal cual."""
    __tablename__ = 'call_up_stat'
    call_up_id: Mapped[int] = mapped_column(ForeignKey('call_up.id'), primary_key=True)
    key: Mapped[str] = mapped_column(String, primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    value_json: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint('call_up_id', 'ordinal'),
        CheckConstraint('ordinal >= 0', name='ordinal'),
        *json('value_json', nulo=False),
        Index('ix_call_up_stat_key', 'key'),
    )


class CallUpChange(Base):
    """Cada transición de vigencia de una convocatoria (retirada o reactivación), con la ejecución y la alineación
    seleccionada que la provocan (NULL si el partido ya no tiene alineación seleccionada)."""
    __tablename__ = 'call_up_change'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_up_id: Mapped[int] = mapped_column(ForeignKey('call_up.id'))
    run_id: Mapped[int] = mapped_column(ForeignKey('import_run.id'))
    change: Mapped[str] = mapped_column(String)
    lineup_observation_id: Mapped[int | None] = mapped_column(ForeignKey('observation.id'))
    __table_args__ = (
        CheckConstraint(en('change', ('retired', 'reactivated')), name='change'),
        Index('ix_call_up_change_call_up_id', 'call_up_id'),
    )


# Única puerta de lectura de convocatorias: solo las vigentes. La reconstrucción (y con ella todas las rutas y
# agregados de la API) lee de aquí; las retiradas solo las ven la sincronización y la verificación.
VISTA_CONVOCATORIAS_VIGENTES = "CREATE VIEW call_up_vigente AS SELECT * FROM call_up WHERE status = 'active'"
event.listen(Base.metadata, 'after_create', DDL(VISTA_CONVOCATORIAS_VIGENTES))
event.listen(Base.metadata, 'before_drop', DDL('DROP VIEW IF EXISTS call_up_vigente'))


class PlayerAttributeObservation(Base):
    """Cada atributo personal conocido de cada observación (entradas de alineación de todas las versiones y fichas).
    La ficha se combina al consultar porque depende del ámbito."""
    __tablename__ = 'player_attribute_observation'
    observation_id: Mapped[int] = mapped_column(ForeignKey('observation.id'), primary_key=True)
    entry_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    field: Mapped[str] = mapped_column(String, primary_key=True)
    external_player_id: Mapped[str] = mapped_column(String)
    player_id: Mapped[int | None] = mapped_column(ForeignKey('player.id'))
    value_json: Mapped[str] = mapped_column(Text)
    fetched_instant: Mapped[str | None] = mapped_column(String)
    __table_args__ = (
        CheckConstraint(en('field', CAMPOS_FICHA), name='field'),
        CheckConstraint('entry_index >= 0', name='entry_index'),
        *json('value_json', nulo=False),
        Index('ix_player_attribute_observation_player_id', 'player_id'),
    )


# ─── Decisiones locales ──────────────────────────────────────────────────────────────────────────────────────────

class LocalReplacement(Base):
    """«El evento sustituido queda reemplazado por el vigente»: decisión de esta instalación, no de la fuente.
    Revertir la marca como revertida, con su motivo; nunca se borra."""
    __tablename__ = 'local_replacement'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    replaced_match_id: Mapped[int] = mapped_column(ForeignKey('match.id'))
    current_match_id: Mapped[int] = mapped_column(ForeignKey('match.id'))
    external_replaced_id: Mapped[str] = mapped_column(String)
    external_current_id: Mapped[str] = mapped_column(String)
    reason: Mapped[str] = mapped_column(Text)
    decided_by: Mapped[str] = mapped_column(String)
    decided_on = mapped_column(Date, nullable=False)
    evidence: Mapped[str | None] = mapped_column(Text)
    source_file_id: Mapped[int | None] = mapped_column(ForeignKey('source_file.id'))
    created_in_run: Mapped[int] = mapped_column(ForeignKey('import_run.id'))
    status: Mapped[str] = mapped_column(String)
    reverted_in_run: Mapped[int | None] = mapped_column(ForeignKey('import_run.id'))
    reverted_by: Mapped[str | None] = mapped_column(String)
    reverted_on = mapped_column(Date)
    revert_reason: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        CheckConstraint('replaced_match_id <> current_match_id', name='distinct'),
        CheckConstraint(texto_no_vacio('reason'), name='reason'),
        CheckConstraint(texto_no_vacio('decided_by'), name='decided_by'),
        CheckConstraint(en('status', ('active', 'reverted')), name='status'),
        CheckConstraint("(status = 'reverted') = (reverted_in_run IS NOT NULL AND reverted_by IS NOT NULL "
                        "AND reverted_on IS NOT NULL AND revert_reason IS NOT NULL)", name='reverted'),
        Index('uq_local_replacement_active', 'replaced_match_id', unique=True, **indice_parcial("status = 'active'")),
    )


# ─── Capturas narrativas independientes del estado deportivo ───────────────────────────────────────────────────

class NarrativeCommentCapture(Base):
    """Respuesta de comentarios capturada tal cual. Una misma respuesta por partido se conserva una sola vez."""
    __tablename__ = 'narrative_comment_capture'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey('match.id'))
    provider: Mapped[str] = mapped_column(String)
    source_event_id: Mapped[str] = mapped_column(String)
    source_path: Mapped[str | None] = mapped_column(String)
    captured_at: Mapped[str] = mapped_column(String)
    http_status: Mapped[int] = mapped_column(Integer)
    availability: Mapped[str] = mapped_column(String)
    comment_count: Mapped[int] = mapped_column(Integer)
    response_sha256: Mapped[str] = mapped_column(String(64))
    response_json: Mapped[str] = mapped_column(Text)
    transport_report_json: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint('match_id', 'response_sha256'),
        UniqueConstraint('id', 'match_id'),
        CheckConstraint("provider = 'sofascore'", name='provider'),
        CheckConstraint("http_status = 200", name='http_status'),
        CheckConstraint(en('availability', ('available', 'empty')), name='availability'),
        CheckConstraint('comment_count >= 0', name='comment_count'),
        CheckConstraint("(availability = 'empty') = (comment_count = 0)", name='empty'),
        CheckConstraint(hexadecimal('response_sha256', 64), name='response_sha256'),
        *json('response_json', nulo=False),
        Index('ix_narrative_comment_capture_match_id', 'match_id'),
    )


class NarrativeComment(Base):
    """Comentario normalizado; participantes conservan sus IDs de SofaScore sin resolverlos a identidad local."""
    __tablename__ = 'narrative_comment'
    capture_id: Mapped[int] = mapped_column(ForeignKey('narrative_comment_capture.id'), primary_key=True)
    source_comment_id: Mapped[str] = mapped_column(String, primary_key=True)
    source_position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String)
    minute: Mapped[int | None] = mapped_column(Integer)
    period_name: Mapped[str | None] = mapped_column(String)
    is_home: Mapped[bool | None] = booleano('is_home', nullable=True)
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    incident_id: Mapped[str | None] = mapped_column(String)
    participants_json: Mapped[str] = mapped_column(Text)
    context_json: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint('capture_id', 'source_position'),
        CheckConstraint('source_position >= 0', name='source_position'),
        CheckConstraint("length(trim(text)) > 0", name='text'),
        CheckConstraint("length(trim(type)) > 0", name='type'),
        CheckConstraint('minute IS NULL OR minute >= 0', name='minute'),
        CheckConstraint('home_score IS NULL OR home_score >= 0', name='home_score'),
        CheckConstraint('away_score IS NULL OR away_score >= 0', name='away_score'),
        *json('participants_json', 'context_json', nulo=False),
        Index('ix_narrative_comment_order', 'capture_id', 'source_position'),
    )


class NarrativeCommentSelection(Base):
    """Captura española vigente, su hash de procedencia y el último intento."""
    __tablename__ = 'narrative_comment_selection'
    match_id: Mapped[int] = mapped_column(ForeignKey('match.id'), primary_key=True)
    capture_id: Mapped[int | None] = mapped_column(Integer)
    last_successful_source_path: Mapped[str | None] = mapped_column(String)
    selected_spanish_sha256: Mapped[str | None] = mapped_column(String(64))
    last_attempt_state: Mapped[str] = mapped_column(String)
    last_attempt_at: Mapped[str] = mapped_column(String)
    last_http_status: Mapped[int | None] = mapped_column(Integer)
    last_error_code: Mapped[str | None] = mapped_column(String)
    __table_args__ = (
        ForeignKeyConstraint(['capture_id', 'match_id'],
                             ['narrative_comment_capture.id', 'narrative_comment_capture.match_id']),
        CheckConstraint(en('last_attempt_state', ('available', 'empty', 'not_collected', 'error')),
                        name='last_attempt_state'),
        CheckConstraint("selected_spanish_sha256 IS NULL OR " + hexadecimal('selected_spanish_sha256', 64),
                        name='selected_spanish_sha256'),
        CheckConstraint('last_http_status IS NULL OR last_http_status BETWEEN 100 AND 599', name='last_http_status'),
        CheckConstraint("last_error_code IS NULL OR length(trim(last_error_code)) > 0", name='last_error_code'),
        Index('ix_narrative_comment_selection_capture_id', 'capture_id'),
    )


class NarrativeCommentEnrichment(Base):
    """Resultado derivado y versionado de un comentario narrativo inmutable.

    Una nueva versión del enriquecedor crea una fila paralela para el mismo comentario; no reemplaza la anotación
    previa ni modifica el texto o los participantes originales.
    """
    __tablename__ = 'narrative_comment_enrichment'
    capture_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_comment_id: Mapped[str] = mapped_column(String, primary_key=True)
    enrichment_version: Mapped[str] = mapped_column(String, primary_key=True)
    category: Mapped[str | None] = mapped_column(String)
    attributes_json: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        ForeignKeyConstraint(['capture_id', 'source_comment_id'],
                             ['narrative_comment.capture_id', 'narrative_comment.source_comment_id']),
        CheckConstraint(texto_no_vacio('enrichment_version'), name='enrichment_version'),
        CheckConstraint('category IS NULL OR ' + texto_no_vacio('category'), name='category'),
        CheckConstraint("json_valid(attributes_json) AND json_type(attributes_json) = 'object'",
                        name='attributes_object_json'),
        Index('ix_narrative_comment_enrichment_category_version', 'category', 'enrichment_version'),
        Index('ix_narrative_comment_enrichment_version', 'enrichment_version'),
    )


class NarrativeCommentEnrichmentParticipant(Base):
    """Participante derivado; el nombre original se conserva en narrative_comment.participants_json."""
    __tablename__ = 'narrative_comment_enrichment_participant'
    capture_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_comment_id: Mapped[str] = mapped_column(String, primary_key=True)
    enrichment_version: Mapped[str] = mapped_column(String, primary_key=True)
    participant_position: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_role: Mapped[str] = mapped_column(String)
    role: Mapped[str | None] = mapped_column(String)
    source_id_json: Mapped[str | None] = mapped_column(Text)
    normalizer_role: Mapped[str | None] = mapped_column(String)
    __table_args__ = (
        ForeignKeyConstraint(['capture_id', 'source_comment_id', 'enrichment_version'],
                             ['narrative_comment_enrichment.capture_id',
                              'narrative_comment_enrichment.source_comment_id',
                              'narrative_comment_enrichment.enrichment_version']),
        CheckConstraint('participant_position >= 0', name='participant_position'),
        CheckConstraint(texto_no_vacio('source_role'), name='source_role'),
        CheckConstraint('role IS NULL OR ' + texto_no_vacio('role'), name='role'),
        CheckConstraint('normalizer_role IS NULL OR ' + texto_no_vacio('normalizer_role'),
                        name='normalizer_role'),
        CheckConstraint("source_id_json IS NULL OR (json_valid(source_id_json) AND "
                        "json_type(source_id_json) IN ('integer', 'text'))", name='source_id_json'),
        UniqueConstraint('capture_id', 'source_comment_id', 'enrichment_version', 'source_role'),
        Index('ix_narrative_comment_enrichment_participant_player_version', 'source_id_json', 'enrichment_version'),
        Index('ix_narrative_comment_enrichment_participant_role_version', 'role', 'enrichment_version'),
    )
