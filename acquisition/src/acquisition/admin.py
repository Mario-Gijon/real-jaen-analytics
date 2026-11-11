"""Explicit administrative profile commands. No daemon, downloads or automatic recovery."""
import argparse
import asyncio
import json
import sys
from .profile import Profile, ProfileError
from .browser import require_offline, validate_candidate

def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--root', default='/profiles')
    commands = result.add_subparsers(dest='command', required=True)
    init = commands.add_parser('initialize-profile')
    init.add_argument('--confirm-new-profile', action='store_true')
    commands.add_parser('verify-profile')
    recover = commands.add_parser('recover-profile')
    recover.add_argument('--confirm-service-stopped', action='store_true')
    recover.add_argument('--confirm-no-external-users', action='store_true')
    return result

def main(argv=None):
    args = parser().parse_args(argv)
    try:
        require_offline()  # Deployment must also set Docker network_mode:none.
        if args.command == 'recover-profile' and not (args.confirm_service_stopped and args.confirm_no_external_users):
            raise ProfileError('Confirme servicio detenido y ausencia de usuarios externos')
        if args.command == 'initialize-profile' and not args.confirm_new_profile:
            raise ProfileError('Confirme explícitamente la creación de un perfil nuevo')
        with Profile(args.root) as owner:
            if args.command == 'initialize-profile':
                result = owner.initialize(confirm_new=args.confirm_new_profile)
            elif args.command == 'verify-profile':
                result = owner.verify()
            else:
                result = owner.recover(lambda path: asyncio.run(validate_candidate(owner, path)),
                    confirm_stopped=args.confirm_service_stopped, confirm_external=args.confirm_no_external_users)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ProfileError, OSError) as error:
        # Browser internals/cookie-bearing diagnostics are not included in CLI responses.
        message = str(error) if isinstance(error, ProfileError) else 'Error de almacenamiento; perfil pendiente de recuperación'
        print(json.dumps({'status': 'profile_busy' if getattr(error, 'code', None) == 73 else 'profile_recovery_required', 'message': message}, ensure_ascii=False), file=sys.stderr)
        return getattr(error, 'code', 74)
    except Exception:
        print(json.dumps({'status': 'profile_recovery_required', 'message': 'Validación del navegador fallida; perfil pendiente de recuperación'}), file=sys.stderr)
        return 74

if __name__ == '__main__':
    raise SystemExit(main())
