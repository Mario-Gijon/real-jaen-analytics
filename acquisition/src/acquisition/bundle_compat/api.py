"""Minimal producer-side subset used by Acquisition's copied bundle verifier."""

PENDIENTES = ('notstarted', 'postponed')


def vacia(seccion, payload):
    """Match Sports Data's empty-section rule used when validating a sealed bundle."""
    if not payload:
        return True
    if not isinstance(payload, dict):
        return False
    if seccion == 'lineups':
        return not any(isinstance(payload.get(side), dict) and payload[side].get('players')
                       for side in ('home', 'away'))
    return not payload.get(seccion)
