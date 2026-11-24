"""Minimal event projections needed by Acquisition and its copied bundle verifier."""


def evento_de(payload):
    return payload.get('event', payload) if isinstance(payload, dict) else {}


def rasgos_evento(payload):
    """The identity, phase and score fields used to detect a corrected calendar event."""
    event = evento_de(payload)
    tournament = event.get('tournament') or {}
    return {
        'home_team_id': (event.get('homeTeam') or {}).get('id'),
        'away_team_id': (event.get('awayTeam') or {}).get('id'),
        'tournament_id': tournament.get('id'),
        'unique_tournament_id': (tournament.get('uniqueTournament') or {}).get('id'),
        'season_id': (event.get('season') or {}).get('id'),
        'status': (event.get('status') or {}).get('type'),
        'home_score': (event.get('homeScore') or {}).get('current'),
        'away_score': (event.get('awayScore') or {}).get('current'),
        'start': event.get('startTimestamp'),
    }


def partido_de_listado(row):
    """Normalize the calendar fields used by Sports Data's bundle verifier."""
    start = row.get('start_utc')
    return {
        'kickoff_utc': start.replace('Z', '+00:00') if isinstance(start, str) else None,
        'status': row['status']['type'],
        'tournament_id': row['stage']['id'],
        'competition': row['stage'].get('name'),
        'season_id': row['season_id'],
        'round': (row.get('round') or {}).get('number'),
        'home_team_id': row['participants']['home']['id'],
        'away_team_id': row['participants']['away']['id'],
        'home_score': (row.get('score') or {}).get('home'),
        'away_score': (row.get('score') or {}).get('away'),
    }
