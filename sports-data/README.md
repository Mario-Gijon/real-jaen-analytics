# Sports Data

Backend FastAPI del dashboard. Contiene las consultas y contratos deportivos, el importador, la persistencia SQLite, comentarios, enrichment y el coordinador de actualizaciones.

El servicio escucha en 8002 y guarda su base, registros de actualización y procedencia en `/state`. El control se habilita en Compose con un worker; usa el contrato HTTP de Acquisition descrito en [contrato-adquisición](docs/contrato-adquisicion-interno.md). Las actualizaciones siguen solicitándose desde el mecanismo autenticado del Dashboard.

Las instrucciones de ejecución con Compose están en el [README raíz](../README.md). Las pruebas unitarias e integración de consulta están en `tests/`; pueden ejecutarse desde esta carpeta con los requisitos Python instalados. Las operaciones de base y migración siguen siendo explícitas.
