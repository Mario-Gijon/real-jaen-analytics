"""Image content checks from the bytes themselves: never from a file name, an extension or a declared MIME type."""
from __future__ import annotations

MAX_SIDE = 4096


class ImageError(ValueError):
    pass


def webp_dimensions(data: bytes) -> tuple[int, int]:
    """(width, height) of a simple (VP8, VP8L) or extended (VP8X) WebP. The RIFF container must account for every
    byte of the file and every chunk must fit in it, so truncated files and trailing data are rejected. The image is
    not decoded."""
    if len(data) < 20 or data[:4] != b'RIFF' or data[8:12] != b'WEBP':
        raise ImageError('no es un WebP')
    if int.from_bytes(data[4:8], 'little') + 8 != len(data):
        raise ImageError('el tamaño RIFF no coincide con el archivo')
    position = 12
    while position < len(data):
        if position + 8 > len(data):
            raise ImageError('chunk RIFF incompleto')
        size = int.from_bytes(data[position + 4:position + 8], 'little')
        position += 8 + size + (size & 1)
        if position > len(data):
            raise ImageError('chunk RIFF incompleto')
    kind, body = data[12:16], data[20:]
    if kind == b'VP8 ':
        if len(body) < 10 or body[0] & 1 or body[3:6] != b'\x9d\x01\x2a':
            raise ImageError('trama VP8 no válida')
        width = int.from_bytes(body[6:8], 'little') & 0x3FFF
        height = int.from_bytes(body[8:10], 'little') & 0x3FFF
    elif kind == b'VP8L':
        if len(body) < 5 or body[0] != 0x2F:
            raise ImageError('trama VP8L no válida')
        bits = int.from_bytes(body[1:5], 'little')
        width, height = (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    elif kind == b'VP8X':
        if len(body) < 10:
            raise ImageError('cabecera VP8X no válida')
        width, height = int.from_bytes(body[4:7], 'little') + 1, int.from_bytes(body[7:10], 'little') + 1
    else:
        raise ImageError('tipo de WebP desconocido')
    if not (0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE):
        raise ImageError(f'dimensiones no admitidas ({width}x{height})')
    return width, height


# media type -> (format name, file extension, dimension reader). Only WebP for now; another format is one entry here.
FORMATS = {'image/webp': ('webp', 'webp', webp_dimensions)}


def inspect(data: bytes, media_type: str) -> tuple[int, int]:
    """Dimensions of `data`, which must really be an image of `media_type`."""
    if media_type not in FORMATS:
        raise ImageError(f'tipo de imagen no admitido: {media_type!r}')
    return FORMATS[media_type][2](data)
