"""Command line interface for the mouse simulation package.

The CLI is deliberately dependency-free and deterministic: artifacts never
contain timestamps and identical inputs produce identical outputs.
"""

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

from .cache import ArtifactCache
from .canonical import canonical_json
from .errors import ValidationError
from .reports import render_html_report, render_json_report

VERSION = "0.1.0"
PROGRAM = "mouse-sim"

EXIT_OK = 0
EXIT_NOT_QUALIFIED = 10
EXIT_INVALID_INPUT = 20
EXIT_UNSUPPORTED_FORMAT = 30
EXIT_INTERNAL = 40
EXIT_USAGE = 64

_VALIDATION_ERROR_CODES = frozenset(
    ("E_INVALID_INPUT", "E_PARSE", "E_VALIDATION", "E_INVALID_DOCUMENT", "E_DOCUMENT_INVALID")
)


class _Parser(argparse.ArgumentParser):
    """Argument parser that reports usage problems with exit code 64."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self._print_message("{}: error: {}\n".format(self.prog, message), sys.stderr)
        raise SystemExit(EXIT_USAGE)


def _error_format(args):
    return getattr(args, "error_format", "text") or "text"


def _emit_error(error_format, code, message, severity="error", phase="cli"):
    if error_format == "json":
        payload = {
            "schema": "gms.error/1",
            "error": {"code": code, "severity": severity, "phase": phase, "message": message},
        }
        sys.stderr.write(canonical_json(payload) + "\n")
    else:
        sys.stderr.write("{}: error: {}: {}\n".format(PROGRAM, code, message))


def _exit_code_for_error_code(code):
    if "UNSUPPORTED" in code:
        return EXIT_UNSUPPORTED_FORMAT
    if code in _VALIDATION_ERROR_CODES or code.startswith(("E_PARSE", "E_INVALID", "E_VALIDATION")):
        return EXIT_INVALID_INPUT
    if code.startswith("GEOMETRY_") or code == "MATERIAL_CATALOG_INVALID":
        return EXIT_INVALID_INPUT
    return EXIT_INTERNAL


def _write_atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".{}.tmp-".format(path.name), dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, str(path))
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _load_json_document(path, error_format):
    try:
        with Path(path).open("r", encoding="utf-8") as stream:
            return json.load(stream), None
    except (OSError, ValueError) as exc:
        _emit_error(error_format, "E_INVALID_INPUT", "unable to load project JSON: {}".format(exc))
        return None, EXIT_INVALID_INPUT


def _cmd_run(args):
    error_format = _error_format(args)
    document, status = _load_json_document(args.input, error_format)
    if status is not None:
        return status
    if not isinstance(document, dict):
        _emit_error(error_format, "E_INVALID_INPUT", "project JSON must contain an object")
        return EXIT_INVALID_INPUT
    emit_set = {token.strip() for token in args.emit.split(",") if token.strip()}
    if not emit_set or not emit_set.issubset({"json", "html"}):
        _emit_error(error_format, "E_USAGE", "unsupported --emit values: {!r}".format(args.emit))
        return EXIT_USAGE
    mode = args.mode
    if mode is None:
        document_mode = document.get("mode")
        if isinstance(document_mode, str) and document_mode.strip().casefold() in ("exploration", "qualification"):
            mode = document_mode.strip().casefold()
        else:
            project = document.get("project") if isinstance(document.get("project"), dict) else {}
            mode = project.get("default_mode", "exploration")
    try:
        from .pipeline import run_pipeline
    except Exception as exc:
        _emit_error(error_format, "E_UNAVAILABLE", "analysis pipeline is unavailable: {}".format(exc))
        return EXIT_INTERNAL
    request = dict(document)
    request["mode"] = mode
    document_options = document.get("options")
    options = dict(document_options) if isinstance(document_options, Mapping) else {}
    options["strict"] = bool(options.get("strict", False) or args.strict)
    # The cache directory is an execution detail, not a request input:
    # injecting it into options would change the run id for the same
    # analysis across cache locations and defeat cross-dir cache reuse.
    request["options"] = options
    cache = ArtifactCache(args.cache_dir) if args.cache_dir else None
    try:
        bundle = run_pipeline(request, cache=cache, use_cache=not args.no_cache)
    except Exception as exc:
        if args.debug:
            raise
        _emit_error(error_format, "E_INTERNAL", "pipeline failed: {}".format(exc))
        return EXIT_INTERNAL
    if not isinstance(bundle, dict):
        _emit_error(error_format, "E_INTERNAL", "pipeline returned a non-object result")
        return EXIT_INTERNAL
    bundle_errors = bundle.get("errors") or []
    if not isinstance(bundle_errors, (list, tuple)):
        bundle_errors = []
    if bundle_errors:
        first = bundle_errors[0] if isinstance(bundle_errors[0], dict) else {}
        code = str(first.get("code", "E_INTERNAL"))
        message = str(first.get("message", "")) or "pipeline reported errors"
        exit_code = _exit_code_for_error_code(code)
        _emit_error(
            error_format,
            code,
            message,
            severity=str(first.get("severity", "error")),
            phase=str(first.get("phase", "pipeline")),
        )
        return exit_code
    output_dir = Path(args.output)
    try:
        _write_atomic(output_dir / "report.json", render_json_report(bundle))
        if "html" in emit_set:
            _write_atomic(output_dir / "report.html", render_html_report(bundle))
        if isinstance(bundle.get("manifest"), dict):
            _write_atomic(output_dir / "manifest.json", canonical_json(bundle["manifest"]))
    except (OSError, ValueError) as exc:
        _emit_error(error_format, "E_INTERNAL", "unable to write reports: {}".format(exc))
        return EXIT_INTERNAL
    mode = bundle.get("mode") or mode
    qualification = bundle.get("qualification") if isinstance(bundle.get("qualification"), dict) else {}
    qualified = bool(qualification.get("qualified", False))
    if args.stdout == "json":
        sys.stdout.write(render_json_report(bundle) + "\n")
    elif args.stdout == "summary":
        if qualified:
            decision = "qualified"
        elif mode == "qualification":
            decision = "not_qualified"
        else:
            decision = "completed"
        sys.stdout.write(
            "mode={} decision={} run_id={} artifacts={}\n".format(mode, decision, bundle.get("run_id", ""), output_dir)
        )
    if mode == "qualification" and not qualified:
        return EXIT_NOT_QUALIFIED
    return EXIT_OK


KNOWN_IMPORT_FORMATS = frozenset(("auto", "json", "obj", "stl", "ascii", "step", "stp", "cad"))


def _cmd_import(args):
    error_format = _error_format(args)
    if args.format not in KNOWN_IMPORT_FORMATS:
        _emit_error(error_format, "E_UNSUPPORTED_FORMAT", "unsupported geometry format {!r}".format(args.format))
        return EXIT_UNSUPPORTED_FORMAT
    try:
        from .importers import load_geometry
    except Exception as exc:
        _emit_error(error_format, "E_UNAVAILABLE", "geometry importer is unavailable: {}".format(exc))
        return EXIT_INTERNAL
    fmt = "stl" if args.format == "ascii" else args.format
    try:
        from .step_kernel import StepKernelFailure, StepKernelUnavailable
    except Exception:
        StepKernelFailure = StepKernelUnavailable = RuntimeError
    try:
        result = load_geometry(args.input, fmt=fmt, units=args.units, stl_backend=args.backend)
    except (StepKernelUnavailable, StepKernelFailure) as exc:
        _emit_error(error_format, "E_KERNEL_UNAVAILABLE", "kernel import failed: {}".format(exc))
        return EXIT_INVALID_INPUT
    except ValueError as exc:
        _emit_error(error_format, "E_PARSE", "geometry import failed: {}".format(exc))
        return EXIT_INVALID_INPUT
    except (OSError, TypeError) as exc:
        _emit_error(error_format, "E_INVALID_INPUT", "unable to read geometry: {}".format(exc))
        return EXIT_INVALID_INPUT
    if result is None or not result.is_supported:
        diagnostic = result.diagnostic if result is not None else None
        message = diagnostic.message if diagnostic is not None else "geometry format is unsupported"
        _emit_error(error_format, "E_UNSUPPORTED_FORMAT", message)
        return EXIT_UNSUPPORTED_FORMAT
    payload = {
        "schema": "gms.normalized-geometry/1",
        "format": result.format,
        "source_units": result.source_units,
        "source_name": result.source_name,
        "geometry": result.geometry.to_dict(),
        "diagnostics": [diagnostic.to_dict() for diagnostic in result.diagnostics],
    }
    try:
        text = canonical_json(payload)
        if args.out:
            _write_atomic(args.out, text)
        else:
            sys.stdout.write(text + "\n")
    except (OSError, ValueError) as exc:
        _emit_error(error_format, "E_INTERNAL", "unable to write normalized geometry: {}".format(exc))
        return EXIT_INTERNAL
    return EXIT_OK


def _cmd_material_validate(args):
    try:
        from .materials import load_material_catalog

        load_material_catalog(args.input, validate=True)
    except ValidationError as exc:
        errors = list(exc.errors) if exc.errors else [str(exc)]
        sys.stdout.write(canonical_json({"valid": False, "errors": errors}) + "\n")
        return EXIT_INVALID_INPUT
    except Exception as exc:
        sys.stdout.write(canonical_json({"valid": False, "errors": [str(exc)]}) + "\n")
        return EXIT_INVALID_INPUT
    sys.stdout.write(canonical_json({"valid": True, "errors": []}) + "\n")
    return EXIT_OK


def _cmd_validate(args):
    error_format = _error_format(args)
    document, status = _load_json_document(args.input, error_format)
    if status is not None:
        return status
    try:
        from .pipeline import run_pipeline
    except Exception as exc:
        error = {
            "code": "E_UNAVAILABLE",
            "severity": "error",
            "phase": "validation",
            "message": "analysis pipeline is unavailable: {}".format(exc),
        }
        sys.stdout.write(canonical_json({"schema": "gms.error/1", "error": error}) + "\n")
        return EXIT_INTERNAL
    try:
        bundle = run_pipeline(
            dict(document, mode="exploration"),
            cache=None,
            use_cache=False,
        )
    except Exception as exc:
        if args.debug:
            raise
        _emit_error(error_format, "E_INTERNAL", "validation failed: {}".format(exc))
        return EXIT_INTERNAL
    if not isinstance(bundle, dict):
        _emit_error(error_format, "E_INTERNAL", "pipeline returned a non-object result")
        return EXIT_INTERNAL
    validation = bundle.get("validation") if isinstance(bundle.get("validation"), dict) else {}
    pipeline_issues = bundle.get("issues") or []
    pipeline_errors = bundle.get("errors") or []
    validation_findings = validation.get("findings") or []
    if not isinstance(pipeline_issues, (list, tuple)):
        pipeline_issues = []
    if not isinstance(pipeline_errors, (list, tuple)):
        pipeline_errors = []
    if not isinstance(validation_findings, (list, tuple)):
        validation_findings = []
    issues = list(validation_findings) + list(pipeline_issues) + list(pipeline_errors)
    valid = validation["status"] != "fail" and not pipeline_issues and not pipeline_errors
    sys.stdout.write(
        canonical_json(
            {"schema": "gms.validation/1", "valid": valid, "issues": list(issues), "validation": validation}
        )
        + "\n"
    )
    return EXIT_OK if valid else EXIT_INVALID_INPUT


def _cmd_serve(args):
    error_format = _error_format(args)
    try:
        from .web_api import WebConfig, build_server, serve
    except Exception as exc:
        _emit_error(error_format, "E_UNAVAILABLE", "web API server is unavailable: {}".format(exc))
        return EXIT_INTERNAL
    web_dist = Path(args.web_dist) if args.web_dist else None
    project_root = Path(args.project_root) if args.project_root else None
    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    if cache_dir is not None:
        ArtifactCache(cache_dir)
    config_kwargs = {
        "host": args.host,
        "port": args.port,
        "web_dist": web_dist,
        "project_root": project_root,
        "cache_dir": cache_dir,
        "cors_origins": tuple(args.cors_origin) if args.cors_origin else (),
        "log_requests": not args.quiet,
    }
    if args.max_json_bytes is not None:
        config_kwargs["max_json_bytes"] = args.max_json_bytes
    if args.max_geometry_bytes is not None:
        config_kwargs["max_geometry_bytes"] = args.max_geometry_bytes
    config = WebConfig(**config_kwargs)
    try:
        server = build_server(config)
    except Exception as exc:
        _emit_error(
            error_format,
            "E_INTERNAL",
            "unable to bind {}:{}: {}".format(config.host, config.port, exc),
        )
        return EXIT_INTERNAL
    return serve(config, server=server)


def build_parser():
    parser = _Parser(prog=PROGRAM, description="mouse-sim: deterministic mouse simulation analysis CLI")
    parser.add_argument("--version", action="version", version="{} {}".format(PROGRAM, VERSION))
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    run_parser = subparsers.add_parser("run", help="run the analysis pipeline over a project document")
    run_parser.add_argument("--input", required=True, metavar="PATH", help="project document JSON")
    run_parser.add_argument("--output", default="reports", metavar="DIR", help="output directory (default: reports)")
    run_parser.add_argument("--emit", default="json,html", metavar="LIST", help="artifacts to emit: json,html")
    run_parser.add_argument("--stdout", choices=("json", "summary", "none"), default="summary", help="stdout content")
    run_parser.add_argument("--mode", choices=("exploration", "qualification"), help="analysis mode")
    run_parser.add_argument("--cache-dir", metavar="PATH", help="analysis cache directory")
    run_parser.add_argument("--no-cache", action="store_true", help="disable the analysis cache")
    run_parser.add_argument("--strict", action="store_true", help="strict validation")
    run_parser.add_argument("--debug", action="store_true", help="show tracebacks on failure")
    run_parser.add_argument("--error-format", choices=("text", "json"), default="text", help="error format")
    run_parser.set_defaults(handler=_cmd_run)

    import_parser = subparsers.add_parser("import", help="normalize geometry to JSON")
    import_parser.add_argument("--input", required=True, metavar="PATH")
    import_parser.add_argument("--format", default="auto", metavar="FMT", help="auto, json, obj, stl, or ascii")
    import_parser.add_argument("--units", default=None, metavar="UNIT", help="source length units: mm, cm, m, or in")
    import_parser.add_argument("--backend", choices=("auto", "stdlib", "kernel"), default=None, metavar="BACKEND", help="STL import backend: kernel uses FreeCAD/OCCT when available (default: stdlib)")
    import_parser.add_argument("--out", default=None, metavar="PATH", help="write normalized JSON to PATH")
    import_parser.add_argument("--debug", action="store_true")
    import_parser.add_argument("--error-format", choices=("text", "json"), default="text")
    import_parser.set_defaults(handler=_cmd_import)

    material_parser = subparsers.add_parser("material", help="material catalog operations")
    material_sub = material_parser.add_subparsers(dest="material_command", metavar="<command>")
    validate_parser = material_sub.add_parser("validate", help="validate a material catalog JSON file")
    validate_parser.add_argument("--input", required=True, metavar="PATH")
    validate_parser.set_defaults(handler=_cmd_material_validate)

    document_parser = subparsers.add_parser("validate", help="validate a full project document")
    document_parser.add_argument("--input", required=True, metavar="PATH")
    document_parser.add_argument("--emit", choices=("json",), default="json", help="output format (default: json)")
    document_parser.add_argument("--debug", action="store_true")
    document_parser.add_argument("--error-format", choices=("text", "json"), default="text")
    document_parser.set_defaults(handler=_cmd_validate)

    serve_parser = subparsers.add_parser("serve", help="serve the deterministic web API")
    serve_parser.add_argument("--host", default="127.0.0.1", metavar="HOST")
    serve_parser.add_argument("--port", type=int, default=8000, metavar="PORT")
    serve_parser.add_argument("--web-dist", default=None, metavar="PATH", help="static web distribution root")
    serve_parser.add_argument("--project-root", default=None, metavar="PATH", help="project root for baseline assets")
    serve_parser.add_argument("--cache-dir", default=None, metavar="PATH", help="analysis cache directory")
    serve_parser.add_argument("--cors-origin", action="append", default=None, metavar="ORIGIN", help="allowed CORS origin (repeatable)")
    serve_parser.add_argument("--max-json-bytes", type=int, default=None, metavar="INT", help="maximum JSON request body size")
    serve_parser.add_argument("--max-geometry-bytes", type=int, default=None, metavar="INT", help="maximum geometry request body size")
    serve_parser.add_argument("--quiet", action="store_true", help="suppress request logging")
    serve_parser.add_argument("--error-format", choices=("text", "json"), default="text", help="error format")
    serve_parser.set_defaults(handler=_cmd_serve)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return EXIT_USAGE
    try:
        return handler(args)
    except KeyboardInterrupt:
        return 130
    except SystemExit:
        raise
    except Exception as exc:
        if getattr(args, "debug", False):
            raise
        _emit_error(_error_format(args), "E_INTERNAL", "unexpected error: {}".format(exc))
        return EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
