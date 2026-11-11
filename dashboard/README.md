# Dashboard

Aplicación Next.js del analista, con BFF en `/api/v1`, control de acceso y clientes para Sports Data y Video Service. La interfaz no conecta directamente con las fuentes deportivas ni con SofaScore.

La carpeta forma parte del monorepositorio. Los comandos Compose de desarrollo y producción, sus redes, volúmenes y rutas de secretos están descritos en el [README raíz](../README.md). El servicio usa internamente `LOCAL_DATA_API_URL=http://sports-data:8002` y `VIDEO_SERVICE_URL=http://video-service:8010`.

El modo `real` requiere Sports Data y nunca sustituye una respuesta fallida por datos inventados. El modo `demo` usa solo los datos sintéticos incluidos para desarrollo y pruebas; producción fuerza `DATA_MODE=real`.

El contrato del botón «Actualizar» está en [contrato-actualizaciones](docs/contrato-actualizaciones.md). Las pruebas de unidad se ejecutan con `npm test`; las pruebas de navegador requieren su configuración de test y no usan credenciales de una instalación.
