"""Approved photo producers: productores-fotos.json, separate from the sporting productores-adquisicion.json.

A photos-v1 bundle is accepted only if its `rules_sha256` is listed here for the same contract and recipe. The two
registries are independent on purpose: a sporting producer approval never authorizes a photo bundle (it is not in this
file) and a photo approval never authorizes a sporting bundle (persistencia.adquisicion reads only the other file).
The file lives outside persistencia/, so approving a producer does not change the coordinator's rule fingerprint.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

REGISTRY = Path(__file__).resolve().parents[1] / 'productores-fotos.json'
SHA256 = re.compile(r'^[0-9a-f]{64}$')
ENTRY_FIELDS = {'rules_sha256', 'contract', 'recipe_id', 'productor', 'uso'}


class RegistryError(ValueError):
    pass


def load(path: Path | None = None) -> dict[str, dict]:
    """{rules_sha256: entry}. A malformed registry is an error, never an empty approval list."""
    path = Path(path) if path is not None else REGISTRY
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RegistryError(f'Registro de productores de fotos ilegible: {type(error).__name__}') from None
    if not isinstance(value, dict) or set(value) != {'descripcion', 'aprobados'} or not isinstance(value['aprobados'], list):
        raise RegistryError('Registro de productores de fotos no válido')
    approved = {}
    for entry in value['aprobados']:
        if (not isinstance(entry, dict) or set(entry) != ENTRY_FIELDS or not isinstance(entry['rules_sha256'], str)
                or not SHA256.match(entry['rules_sha256']) or entry['rules_sha256'] in approved
                or not all(isinstance(entry[k], str) and entry[k] for k in ENTRY_FIELDS)):
            raise RegistryError('Entrada no válida en el registro de productores de fotos')
        approved[entry['rules_sha256']] = entry
    return approved
