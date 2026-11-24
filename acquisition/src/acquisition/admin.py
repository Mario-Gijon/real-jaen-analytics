"""Explicit administrative profile commands and a separate manual photo import."""
import argparse
import asyncio
import json
import os
import sys

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
    photos = commands.add_parser('import-photos')
    photos.add_argument('--collection', required=True, help='Selector seguro, por ejemplo df11/1')
    photos.add_argument('--confirm-usage-rights', action='store_true',
                        help='Confirma que tienes permisos para usar y redistribuir todas las imágenes de la colección')
    return result


def _import_photos(args):
    if not args.confirm_usage_rights:
        print(json.dumps({'status': 'photos_import_rejected',
                          'message': 'Confirma los permisos de uso con --confirm-usage-rights antes de importar.'},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    from .photo_import import PhotoImportError, import_collection
    try:
        result = import_collection(args.collection, confirm_usage_rights=True,
                                   collections_root=os.environ.get('ACQ_COLLECTIONS_ROOT', '/collections'),
                                   secret_file=os.environ.get('ACQ_SECRET_FILE', '/run/secrets/acquisition-token'))
    except PhotoImportError as error:
        print(json.dumps({'status': 'photos_import_failed', 'message': str(error)}, ensure_ascii=False),
              file=sys.stderr)
        return 1
    except OSError:
        print(json.dumps({'status': 'photos_import_failed',
                          'message': 'No se pudo leer la colección o el secreto de Acquisition.'},
                         ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == 'import-photos':
        return _import_photos(args)
    from .profile import Profile, ProfileError
    from .browser import require_offline, validate_candidate
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
