"""Funciones puras para proyectar alias SofaScore a una identidad pública canónica."""

from importar import CAMPOS_FICHA, instante


def jugador_canonico(aliases, player_id):
    """Devuelve el ID público canónico sin alterar el identificador recibido."""
    if type(player_id) is not int:
        return player_id
    return aliases.get(player_id, player_id)


def aliases_de(aliases, canonical_id):
    """IDs fuente que forman parte de una identidad canónica, incluidos los no fusionados."""
    return {canonical_id, *(alias for alias, target in aliases.items() if target == canonical_id)}


def combinar_fichas(canonical_id, fichas_por_id):
    """Combina fichas conservando la ficha canónica y evitando elegir valores alias contradictorios.

    Cada ficha de entrada ya está combinada por ID fuente mediante las reglas históricas. La ficha canónica gana
    cuando tiene un valor; un atributo ausente solo se completa desde los alias si todos coinciden. Las observaciones
    originales y sus discrepancias siguen almacenadas por separado.
    """
    canonical = fichas_por_id.get(canonical_id)
    candidates = list(fichas_por_id.values())
    if canonical is None:
        canonical = max(candidates, key=lambda ficha: instante(ficha.get('fetched_at')) or 0.0,
                        default={'player_id': canonical_id})
    salida = dict(canonical, player_id=canonical_id)
    for campo in CAMPOS_FICHA:
        valor = canonical.get(campo)
        if valor is not None:
            salida[campo] = valor
            continue
        valores = []
        for player_id, ficha in fichas_por_id.items():
            if player_id == canonical_id:
                continue
            candidate = ficha.get(campo)
            if candidate is not None and candidate not in valores:
                valores.append(candidate)
        salida[campo] = valores[0] if len(valores) == 1 else None
    return salida
