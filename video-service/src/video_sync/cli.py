"""Commands: info, sample, read, calibrate, analyze-samples, auto-calibrate, locate."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

from .clock import ROI, clock_text, preprocess, read_clock, read_clock_crop
from .detector import ClockDetector
from .auto_calibrate import run_auto_calibration, video_report_paths
from .media import MediaError, extract_frame, probe, save_jpg
from .sports_events import load_sports_events
from .sofascore_adapter import convert_capture_file, write_contract_new
from .sync import (build_segments, event_minute_to_clock, locate,
                   observations_from_scans, pick_second_mode)
from .samples import parse_sample_files, analyze_samples


def parse_time(value: str) -> float:
    try:
        bits = [float(p) for p in value.split(":")]
        if len(bits) == 3:
            h, m, s = bits
            if m >= 60 or s >= 60:
                raise ValueError
            result = h * 3600 + m * 60 + s
        elif len(bits) == 2:
            m, s = bits
            if s >= 60:
                raise ValueError
            result = m * 60 + s
        elif len(bits) == 1:
            result = bits[0]
        else:
            raise ValueError
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Tiempo no válido: {value}; usa HH:MM:SS") from exc
    if result < 0:
        raise argparse.ArgumentTypeError("El tiempo no puede ser negativo.")
    return result


def parse_window(value: str) -> tuple[float, float]:
    try:
        a, b = (parse_time(x.strip()) for x in value.split(","))
    except (ValueError, argparse.ArgumentTypeError) as exc:
        raise argparse.ArgumentTypeError("Ventana inválida: HH:MM:SS,HH:MM:SS") from exc
    if b <= a:
        raise argparse.ArgumentTypeError("El final de la ventana debe ser posterior al inicio.")
    return a, b


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def video_position_text(seconds: float) -> str:
    whole = int(round(seconds))
    if whole >= 3600:
        return f"{whole // 3600:02d}:{(whole // 60) % 60:02d}:{whole % 60:02d}"
    return clock_text(whole)


def do_sample(args: argparse.Namespace) -> None:
    info = probe(args.video)
    duration = info["duration_seconds"]
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    images = []
    for index in range(args.count):
        sec = duration * (index + 1) / (args.count + 1)
        frame = extract_frame(args.video, sec)
        name = f"frame_{index+1:02d}_{int(sec):06d}s.jpg"
        save_jpg(output / name, frame)
        thumb = cv2.resize(frame, (480, round(frame.shape[0] * 480 / frame.shape[1])), interpolation=cv2.INTER_AREA)
        thumb = cv2.copyMakeBorder(thumb, 0, 35, 0, 0, cv2.BORDER_CONSTANT, value=(30, 30, 30))
        cv2.putText(thumb, f"{index + 1:02d} | video {clock_text(sec)}", (12, thumb.shape[0] - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, .65, (255, 255, 255), 2, cv2.LINE_AA)
        images.append(thumb)
        print(f"{name} -> video {clock_text(sec)}")
    # All thumbs are the same shape because the source resolution is fixed.
    row_count = 3
    import numpy as np
    rows = []
    for i in range(0, len(images), row_count):
        cells = images[i:i+row_count]
        while len(cells) < row_count:
            cells.append(np.full_like(images[0], 40))
        rows.append(cv2.hconcat(cells))
    overview = cv2.vconcat(rows)
    save_jpg(output / "contact_sheet.jpg", overview)
    print(f"\nMosaico: {output / 'contact_sheet.jpg'}")
    print("Los fotogramas originales están en ese mismo directorio para ver el reloj a tamaño real.")


def do_read(args: argparse.Namespace) -> None:
    roi = ROI.parse(args.roi)
    frame = extract_frame(args.video, args.at)
    result = read_clock(frame, roi)
    if args.debug_dir:
        args.debug_dir.mkdir(parents=True, exist_ok=True)
        save_jpg(args.debug_dir / "full.jpg", frame)
        cropped = roi.crop(frame)
        save_jpg(args.debug_dir / "crop.jpg", cropped)
        for name, variant in preprocess(cropped).items():
            cv2.imwrite(str(args.debug_dir / f"{name}.png"), variant)
    print(json.dumps({"video_second": args.at, "roi": roi.as_dict(), **result},
                     indent=2, ensure_ascii=False))


def scan_window(video: Path, roi: ROI, period: str, window: tuple[float, float],
                step: float, min_votes: int = 2) -> list[dict]:
    raw = []
    start, end = window
    point = start
    while point <= end + 0.001:
        try:
            frame = extract_frame(video, point)
            reading = read_clock(frame, roi, min_votes=min_votes)
            raw.append({"period": period, "video_second": round(point, 3), **reading})
            indicator = reading["clock"] or "sin lectura"
            print(f"[{period:6}] video {clock_text(point)} -> reloj {indicator} "
                  f"(votos {reading['votes']}/3)")
        except (MediaError, ValueError, RuntimeError) as exc:
            print(f"[{period}] video {clock_text(point)}: error: {exc}")
            raw.append({"period": period, "video_second": round(point, 3), "clock_seconds": None,
                        "clock": None, "votes": 0, "status": "ambiguous_invalid",
                        "status_reason": str(exc), "error": str(exc)})
        point += step
    return raw


def do_calibrate(args: argparse.Namespace) -> None:
    if not args.first_window and not args.second_window:
        raise ValueError("Indica al menos --first-window o --second-window.")
    info = probe(args.video)
    scans = []
    modes = {}
    clock_detection = {"status": "manual_roi", "roi": None}
    if args.roi:
        roi = ROI.parse(args.roi)
        clock_detection["roi"] = roi.as_dict()
        for period, window in (("first", args.first_window), ("second", args.second_window)):
            if not window:
                continue
            if window[1] >= info["duration_seconds"]:
                raise ValueError(f"Ventana '{period}' fuera del vídeo ({clock_text(info['duration_seconds'])}).")
            scans.extend(scan_window(args.video, roi, period, window, args.step, args.min_votes))
    else:
        # Extract only the already requested sparse calibration instants. The
        # detector sees a small, evenly spaced subset; OCR then reuses the same
        # frames at the confirmed region, so the MP4 is never scanned densely.
        sampled_frames = []
        extraction_errors = []
        for period, window in (("first", args.first_window), ("second", args.second_window)):
            if not window:
                continue
            if window[1] >= info["duration_seconds"]:
                raise ValueError(f"Ventana '{period}' fuera del vídeo ({clock_text(info['duration_seconds'])}).")
            point = window[0]
            while point <= window[1] + 0.001:
                try:
                    sampled_frames.append({"period": period, "video_second": round(point, 3),
                                          "frame": extract_frame(args.video, point)})
                except (MediaError, ValueError, RuntimeError) as exc:
                    extraction_errors.append({"period": period, "video_second": round(point, 3),
                                              "clock_seconds": None, "clock": None, "votes": 0,
                                              "status": "ambiguous_invalid", "error": str(exc)})
                point += args.step
        if not sampled_frames:
            raise ValueError("No se pudieron extraer fotogramas en las ventanas indicadas.")
        count = min(12, len(sampled_frames))
        detector_indices = sorted(set(round(i * (len(sampled_frames) - 1) / max(1, count - 1))
                                      for i in range(count)))
        detector_samples = [(sampled_frames[i]["video_second"], sampled_frames[i]["frame"])
                            for i in detector_indices]
        detector = ClockDetector()
        clock_detection = detector.detect(detector_samples)
        detector.write_visual_report(clock_detection, detector_samples, args.detection_report)
        print(f"Detección automática: {clock_detection['status']} "
              f"(confianza {clock_detection['confidence']:.2f}, "
              f"{clock_detection['processing_seconds']:.1f}s)")
        if clock_detection["status"] != "confirmed":
            scans = extraction_errors + [
                {"period": item["period"], "video_second": item["video_second"],
                 "clock_seconds": None, "clock": None, "votes": 0,
                 "status": "ambiguous_invalid",
                 "status_reason": "clock_region_not_confirmed_automatically"}
                for item in sampled_frames
            ]
            payload = {
                "version": 3, "video": info, "roi": None,
                "clock_detection": clock_detection,
                "parameters": {"step_seconds": args.step, "offset_tolerance_seconds": args.tolerance,
                               "first_window": args.first_window, "second_window": args.second_window,
                               "min_votes": args.min_votes, "second_clock_mode": None},
                "segments": [], "scans": scans,
                "status_counts": {"ambiguous_invalid": len(scans)},
                "warnings": ["La calibración automática no se confirmó; no se asignó ninguna región ni offset."],
            }
            write_json(args.output, payload)
            print(f"Calibración sin confirmar: {args.output}; informe visual: {args.detection_report}")
            return
        roi = ROI(**clock_detection["roi"])
        detector_evidence = {
            round(sampled_frames[detector_indices[i]]["video_second"], 3): item
            for i, item in enumerate(clock_detection.get("sample_evidence", []))
        }
        for item in sampled_frames:
            reading = read_clock_crop(roi.crop(item["frame"]), min_votes=args.min_votes,
                                      require_marker_evidence=False)
            evidence = detector_evidence.get(round(item["video_second"], 3))
            if (evidence and evidence.get("status") == "marker_not_visible" and
                    reading["status"] != "valid_reading"):
                reading["status"] = "marker_not_visible"
                reading["status_reason"] = evidence.get("reason")
            scans.append({"period": item["period"], "video_second": item["video_second"], **reading})
            print(f"[{item['period']:6}] video {clock_text(item['video_second'])} -> reloj "
                  f"{reading['clock'] or 'sin lectura'} (votos {reading['votes']}/3)")
        scans.extend(extraction_errors)

    for period in ("first", "second"):
        result = [r for r in scans if r.get("period") == period]
        valid = [r for r in result if r["status"] == "valid_reading"]
        if period == "second":
            if valid:
                mode = args.second_mode if args.second_mode != "auto" else pick_second_mode(
                    [r["clock_seconds"] for r in valid])
                modes[period] = mode
                print(f"Segunda parte: modo reloj = {mode} "
                      f"{'(estimado; REVÍSALO)' if args.second_mode == 'auto' else '(manual)'}")
            else:
                modes[period] = args.second_mode
    observations = observations_from_scans(
        scans, second_mode=modes.get("second", args.second_mode),
        tolerance_seconds=args.tolerance, max_gap_seconds=args.step * 3,
    )
    segments = build_segments(observations, tolerance_seconds=args.tolerance,
                              max_gap_seconds=args.step * 3)
    status_counts = {}
    for item in scans:
        status_counts[item["status"]] = status_counts.get(item["status"], 0) + 1
    output = {
        "version": 3, "video": info, "roi": roi.as_dict(), "clock_detection": clock_detection,
        "parameters": {"step_seconds": args.step, "offset_tolerance_seconds": args.tolerance,
                       "first_window": args.first_window, "second_window": args.second_window,
                       "min_votes": args.min_votes,
                       "second_clock_mode": modes.get("second")},
        "segments": segments, "scans": scans, "status_counts": status_counts,
        "warnings": [
            "Las referencias OCR y sus offsets requieren revisión manual; no garantizan precisión al segundo.",
            "Entre fotogramas muestreados pueden existir cortes o repeticiones no detectados.",
            "La ausencia del marcador no crea por sí sola una discontinuidad.",
            "Los minutos de SofaScore no contienen el segundo exacto de la acción.",
        ],
    }
    write_json(args.output, output)
    print(f"\nCalibración: {args.output}")
    print(f"Referencias candidatas: {len(observations)} | Tramos validados (mín. 2 puntos): {len(segments)}")
    for index, seg in enumerate(segments, 1):
        print(f"  #{index} {seg['period']}  reloj {clock_text(seg['game_clock_start'])}-"
              f"{clock_text(seg['game_clock_end'])} => vídeo {clock_text(seg['video_start'])}-"
              f"{clock_text(seg['video_end'])}  offset={seg['offset_seconds']:.1f}s  "
              f"ref={seg['reference_count']}  residuo_max={seg['max_residual_seconds']}s")
    if not segments:
        print("No hay tramos suficientemente consistentes. Revisa el ROI, el reloj y las ventanas.")


def do_analyze_samples(args: argparse.Namespace) -> None:
    info = probe(args.video)
    images = parse_sample_files(args.samples_dir, duration=info["duration_seconds"])
    clock_detection = None
    if args.roi:
        roi = ROI.parse(args.roi)
    else:
        detector_samples = []
        for path, video_second in images:
            frame = cv2.imread(str(path))
            if frame is None:
                raise ValueError(f"No se pudo abrir: {path}")
            detector_samples.append((video_second, frame))
        detector = ClockDetector()
        clock_detection = detector.detect(detector_samples)
        detector.write_visual_report(clock_detection, detector_samples, args.detection_report)
        print(f"Detección automática: {clock_detection['status']} "
              f"(confianza {clock_detection['confidence']:.2f}, "
              f"{clock_detection['processing_seconds']:.1f}s)")
        if clock_detection["status"] != "confirmed":
            unresolved_scans = [{
                "source": path.name, "video_second": round(video_second, 3),
                "clock_seconds": None, "clock": None, "votes": 0,
                "status": "ambiguous_invalid",
                "status_reason": "clock_region_not_confirmed_automatically",
            } for path, video_second in images]
            payload = {
                "version": 3, "source": "Existing JPEG samples",
                "roi": None, "clock_detection": clock_detection,
                "parameters": {"first_window": args.first_window, "second_window": args.second_window,
                               "second_clock_mode": args.second_mode, "min_votes": args.min_votes,
                               "offset_tolerance_seconds": args.tolerance,
                               "max_gap_seconds": args.max_gap},
                "segments": [], "scans": unresolved_scans,
                "status_counts": {"ambiguous_invalid": len(unresolved_scans)},
                "warnings": ["La región del reloj no se confirmó automáticamente; no se fabricaron lecturas."],
            }
            payload["video"] = info
            write_json(args.output, payload)
            print(f"Sin calibración: detector {clock_detection['status']}; informe {args.detection_report}")
            return
        roi = ROI(**clock_detection["roi"])
    payload = analyze_samples(images, roi, args.first_window, args.second_window,
                              second_mode=args.second_mode, min_votes=args.min_votes,
                              tolerance=args.tolerance, max_gap=args.max_gap,
                              debug_dir=args.debug_dir, clock_detection=clock_detection,
                              require_marker_evidence=clock_detection is None)
    payload["video"] = info
    write_json(args.output, payload)
    print(f"Muestras analizadas: {len(payload['scans'])}")
    for item in payload["scans"]:
        print(f" {item['source']} | vídeo {clock_text(item['video_second'])} "
              f"| reloj {item['clock'] or 'SIN LECTURA'} | "
              f"{item['votes']}/3 | {item['period']} | {item['status']}")
    print(f"Estados: {json.dumps(payload['status_counts'], ensure_ascii=False)}")
    print(f"\nTramos fiables: {len(payload['segments'])}")
    for seg in payload["segments"]:
        print(f" {seg['period']}: offset={seg['offset_seconds']:.3f}s "
              f"reloj {clock_text(seg['game_clock_start'])}-"
              f"{clock_text(seg['game_clock_end'])}; "
              f"{seg['reference_count']} referencias, "
              f"residuo máximo {seg['max_residual_seconds']}s")
    print(f"Informe JSON: {args.output}")
    print(f"Recortes visuales: {args.debug_dir}")


def do_locate(args: argparse.Namespace) -> None:
    payload = json.loads(args.calibration.read_text(encoding="utf-8"))
    result = locate(payload["segments"], period=args.period, minute=args.minute,
                    lead_seconds=args.lead, max_extrapolate_seconds=args.max_extrapolate)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result["status"] == "ambiguous":
        print("Posición no confirmada: el minuto aparece en varios segmentos.")
        return
    print(f"\nPosición del vídeo para reproducir: {video_position_text(result['video_second'])}")
    if result["extrapolated_seconds"] > 0:
        print("ADVERTENCIA: esta posición extrapola fuera de los intervalos OCR verificados.")


def do_auto_calibrate(args: argparse.Namespace) -> None:
    sports_events = load_sports_events(args.events_json) if args.events_json else None
    if args.output:
        output_path = args.output
        default_report_dir = output_path.parent / f"{output_path.stem}_report"
    else:
        output_path, default_report_dir, _ = video_report_paths(
            args.video, sports_events=sports_events)
    report_dir = args.report_dir or default_report_dir
    report = run_auto_calibration(
        args.video, output_path=output_path, report_dir=report_dir,
        initial_samples=args.initial_samples, sample_interval=args.sample_interval,
        max_samples=args.max_samples, max_processing_seconds=args.max_processing_seconds,
        adaptive_min_gap_seconds=args.adaptive_min_gap,
        tolerance_seconds=args.tolerance,
        max_interpolation_gap_seconds=args.max_interpolation_gap,
        sports_events=sports_events,
        sports_events_source=str(args.events_json) if args.events_json else None,
    )
    print(f"Auto-calibración: {report['status']} | ROI {report['roi'] or 'sin confirmar'}")
    print(f"Muestras: {report['coverage']['sampled_frames']} | "
          f"referencias compatibles: {report['coverage']['supported_references']} | "
          f"cobertura interpolable: {report['coverage']['percent']:.2f}%")
    dimensions = report["confidence_dimensions"]
    print(f"Confianza OCR={dimensions['ocr']['level']} | "
          f"periodo={dimensions['period']['level']} | "
          f"localización={dimensions['location']['level']} | "
          f"tiempo {report['processing_seconds']:.1f}s")
    for segment in report["segments"]:
        print(f"  {segment['segment_id']} {segment['period']} "
              f"reloj {clock_text(segment['game_clock_start'])}-{clock_text(segment['game_clock_end'])} "
              f"vídeo {clock_text(segment['video_start'])}-{clock_text(segment['video_end'])} "
              f"offset={segment['offset_seconds']:.3f}s refs={segment['reference_count']} "
              f"confianza={segment['confidence_level']}")
    print(f"JSON: {output_path}")
    print(f"Evolución OCR/desfase: {report_dir / 'clock_sync_timeline.jpg'}")


def do_adapt_sofascore(args: argparse.Namespace) -> None:
    document = convert_capture_file(
        args.input,
        expected_match_id=args.match_id,
        language=args.language,
        selection_inventory=args.selection_inventory,
        newest_first=not args.already_chronological,
    )
    write_contract_new(args.output, document)
    metadata = document["source_metadata"]
    print(f"Eventos SofaScore normalizados: {len(document['events'])}")
    print(f"Partido: {metadata['match_id']} | idioma: {metadata.get('language') or 'no indicado'}")
    if metadata.get("capture_response_sha256"):
        print(f"SHA-256 de la respuesta: {metadata['capture_response_sha256']}")
    print(f"JSON V5: {args.output}")


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m video_sync", description="Laboratorio de OCR de relojes futbolísticos")
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("adapt-sofascore", help="Convertir comentarios SofaScore adquiridos a sports-events v1")
    p.add_argument("--input", required=True, type=Path,
                   help="Respuesta JSON o comments.jsonl adquirido; solo lectura")
    p.add_argument("--match-id", required=True,
                   help="ID del partido que debe contener la captura")
    p.add_argument("--language", help="Etiqueta de idioma de origen, p. ej. es-la")
    p.add_argument("--selection-inventory", type=Path,
                   help="Inventario opcional que acredita la captura española seleccionada y su hash")
    p.add_argument("--already-chronological", action="store_true",
                   help="La lista JSON de entrada ya está en orden cronológico ascendente")
    p.add_argument("--output", required=True, type=Path,
                   help="Destino nuevo; el comando rechaza sobrescribirlo")
    p.set_defaults(func=do_adapt_sofascore)

    p = commands.add_parser("info", help="Metadatos del MP4 sin modificarlo")
    p.add_argument("video", type=Path)
    p.set_defaults(func=lambda args: print(json.dumps(probe(args.video), indent=2, ensure_ascii=False)))

    p = commands.add_parser("sample", help="Fotogramas aislados + mosaico")
    p.add_argument("video", type=Path)
    p.add_argument("--count", type=int, default=12)
    p.add_argument("--output", type=Path, default=Path("output/samples"))
    p.set_defaults(func=do_sample)

    p = commands.add_parser("read", help="Leer un reloj en un instante y guardar imágenes debug")
    p.add_argument("video", type=Path)
    p.add_argument("--at", type=parse_time, required=True, help="Instante del VÍDEO: HH:MM:SS")
    p.add_argument("--roi", required=True,
                   help="x,y,ancho,alto del reloj (píxeles); consulta la detección automática de calibrate")
    p.add_argument("--debug-dir", type=Path, default=Path("output/ocr_debug"))
    p.set_defaults(func=do_read)

    p = commands.add_parser("calibrate", help="Calibrar las partes a partir de muestras OCR")
    p.add_argument("video", type=Path)
    p.add_argument("--roi", help="ROI manual opcional x,y,ancho,alto; si falta se detecta en fotogramas dispersos")
    p.add_argument("--first-window", type=parse_window, help="Inicio,fin del VÍDEO para 1ª parte")
    p.add_argument("--second-window", type=parse_window, help="Inicio,fin del VÍDEO para 2ª parte")
    p.add_argument("--second-mode", choices=["auto", "continuous", "reset"], default="auto")
    p.add_argument("--step", type=int, default=120, help="Muestrear cada N segundos (por defecto 120)")
    p.add_argument("--tolerance", type=float, default=4.0, help="Tolerancia de offset en segundos")
    p.add_argument("--min-votes", type=int, choices=[1, 2, 3], default=2, help="Votos OCR mínimos (por defecto 2 de 3)")
    p.add_argument("--detection-report", type=Path, default=Path("output/clock_detection"))
    p.add_argument("--output", type=Path, default=Path("output/calibration.json"))
    p.set_defaults(func=do_calibrate)

    p = commands.add_parser("analyze-samples", help="OCR y calibración usando JPG existentes (sin extraer más vídeo)")
    p.add_argument("video", type=Path, help="El MP4 original, solo para obtener duración")
    p.add_argument("--samples-dir", type=Path, default=Path("output/samples"))
    p.add_argument("--roi", help="ROI manual opcional x,y,ancho,alto; si falta se detecta automáticamente")
    p.add_argument("--first-window", type=parse_window, required=True)
    p.add_argument("--second-window", type=parse_window, required=True)
    p.add_argument("--second-mode", choices=["auto", "continuous", "reset"], default="auto")
    p.add_argument("--min-votes", type=int, choices=[1, 2, 3], default=2)
    p.add_argument("--tolerance", type=float, default=4.0)
    p.add_argument("--max-gap", type=float, default=2400.0,
                   help="Separación máxima entre referencias que se consideran del mismo tramo (por defecto 2400 s)")
    p.add_argument("--debug-dir", type=Path, default=Path("output/clock_strips"))
    p.add_argument("--detection-report", type=Path, default=Path("output/clock_detection"))
    p.add_argument("--output", type=Path, default=Path("output/sample_calibration.json"))
    p.set_defaults(func=do_analyze_samples)

    p = commands.add_parser("auto-calibrate", help="Detectar reloj y calibrar el MP4 con muestras dispersas")
    p.add_argument("video", type=Path, help="Ruta al MP4; no requiere ROI ni ventanas manuales")
    p.add_argument("--initial-samples", type=int, default=12,
                   help="Muestras distribuidas para detectar el reloj (por defecto 12)")
    p.add_argument("--sample-interval", type=float, default=300,
                   help="Intervalo base disperso en segundos (por defecto 300)")
    p.add_argument("--max-samples", type=int, default=80,
                   help="Límite total de fotogramas (por defecto 80)")
    p.add_argument("--max-processing-seconds", type=float, default=180,
                   help="Presupuesto total de análisis en segundos")
    p.add_argument("--adaptive-min-gap", type=float, default=30,
                   help="Dejar de refinar un límite cuando el intervalo sea menor (segundos)")
    p.add_argument("--tolerance", type=float, default=8,
                   help="Tolerancia de compatibilidad entre offsets (segundos)")
    p.add_argument("--max-interpolation-gap", type=float, default=600,
                   help="Separación máxima entre referencias del mismo segmento")
    p.add_argument("--output", type=Path,
                   help="Ruta JSON explícita; por defecto se crea una carpeta única por ruta de vídeo")
    p.add_argument("--report-dir", type=Path,
                   help="Directorio para informes de detección y gráfica temporal")
    p.add_argument("--events-json", type=Path,
                   help="Eventos normalizados según contracts/sports_events.v1.schema.json")
    p.set_defaults(func=do_auto_calibrate)

    p = commands.add_parser("locate", help="Minuto de un evento → segundo del vídeo")
    p.add_argument("calibration", type=Path, help="JSON generado por calibrate")
    p.add_argument("--period", required=True, choices=["first", "second"])
    p.add_argument("--minute", required=True, help="Minuto de SofaScore (p. ej. 58 o 90+4)")
    p.add_argument("--lead", type=int, default=15, help="Segundos previos al inicio del minuto")
    p.add_argument("--max-extrapolate", type=float, default=0, help="Permitir extrapolar hasta N segundos (0=seguro)")
    p.set_defaults(func=do_locate)
    return parser


def main() -> int:
    parser = create_parser()
    args = parser.parse_args()
    try:
        if getattr(args, "count", 1) < 1:
            raise ValueError("--count debe ser al menos 1")
        if getattr(args, "step", 1) < 1:
            raise ValueError("--step debe ser al menos 1")
        if getattr(args, "tolerance", 0) < 0:
            raise ValueError("--tolerance debe ser >= 0")
        args.func(args)
    except (MediaError, OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0
