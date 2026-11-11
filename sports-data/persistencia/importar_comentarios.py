"""Explicitly import the reviewed local comments response into an isolated migrated SQLite copy.

The command never contacts a provider and refuses any response whose SHA-256 is not the approved capture.
"""
import argparse
import json
import sys
from pathlib import Path

from persistencia.comentarios import (EXPECTED_RESPONSE_SHA256, NarrativeCommentImportRejected,
                                      import_saved_response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True, help='SQLite de prueba ya migrada, fuera del servicio activo')
    parser.add_argument('--response', type=Path, required=True, help='Respuesta HTTP 200 guardada localmente')
    parser.add_argument('--sha256', required=True, help='Huella autorizada de la respuesta original')
    parser.add_argument('--captured-at', help='Instante ISO-8601 con zona; por omisión usa el mtime de la evidencia')
    args = parser.parse_args()
    if args.sha256 != EXPECTED_RESPONSE_SHA256:
        parser.error('la respuesta debe ser la captura HTTP 200 aprobada para el evento 16600011')
    try:
        result = import_saved_response(args.base, args.response, args.sha256, captured_at=args.captured_at)
    except (NarrativeCommentImportRejected, OSError, RuntimeError, ValueError) as error:
        raise SystemExit(f'Importación rechazada: {error}') from error
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
