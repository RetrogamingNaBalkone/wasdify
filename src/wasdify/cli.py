"""Command-line frontend. Importing it does not import Tkinter."""
import argparse
import json
import sys

from . import __version__
from .i18n import Translator, default_language
from .models import Issue, WasdifyError, exit_code
from .presentation import apply_text, detection_text, plan_text, to_wire
from .service import Service


class UsageError(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse's OS-localized diagnostics are not tied to --lang. Keep the
        # public error in our catalog, with localized help as the recovery path.
        raise UsageError()


def build_parser(tr: Translator) -> argparse.ArgumentParser:
    def configure(parser: argparse.ArgumentParser) -> None:
        parser._positionals.title = tr("positional")
        parser._optionals.title = tr("options")
        parser.add_argument("-h", "--help", action="help", help=tr("help"))

    def common(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--lang", choices=("ru", "en"), default=argparse.SUPPRESS,
                            help=tr("language_help"))
        parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                            help=tr("json_help"))

    class LocalFormatter(argparse.HelpFormatter):
        def add_usage(self, usage, actions, groups, prefix=None):
            super().add_usage(usage, actions, groups, prefix=tr("usage") if prefix is None else prefix)

    root = Parser(prog="wasdify", description="WASDify — " + tr("app_description"),
                  add_help=False, allow_abbrev=False, formatter_class=LocalFormatter)
    configure(root)
    common(root)
    root.add_argument("--version", action="version", version="WASDify " + __version__, help=tr("version_help"))
    sub = root.add_subparsers(dest="command", required=True, title=tr("commands"), metavar="COMMAND")
    for command in ("games", "detect", "patches", "plan", "apply", "gui"):
        parser = sub.add_parser(command, help=tr(command + "_help"),
                                description=tr(command + "_help"), add_help=False,
                                allow_abbrev=False, formatter_class=LocalFormatter)
        configure(parser)
        common(parser)
        if command in ("detect", "plan", "apply"):
            parser.add_argument("path", metavar="PATH", help=tr("path_help"))
        if command in ("detect", "patches", "plan", "apply"):
            parser.add_argument("--game", required=command == "patches", help=tr("game_help"))
        if command in ("plan", "apply"):
            parser.add_argument("--patch", action="append", required=True, help=tr("patch_help"))
        if command == "apply":
            parser.add_argument("--no-backup", action="store_true", help=tr("no_backup_help"))
    return root


def _language(argv: list[str]) -> str:
    language = default_language()
    for index, token in enumerate(argv):
        if token == "--lang" and index + 1 < len(argv):
            language = argv[index + 1]
        elif token.startswith("--lang="):
            language = token.partition("=")[2]
    return language if language in ("ru", "en") else default_language()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    tr = Translator(_language(argv))
    json_output = "--json" in argv

    def emit(data: object, text: str, *, error: bool = False) -> None:
        if json_output:
            print(json.dumps(to_wire(data), ensure_ascii=True))
        else:
            print(text, file=sys.stderr if error else sys.stdout)

    try:
        args = build_parser(tr).parse_args(argv)
        if args.command == "gui":
            if json_output:
                raise UsageError()
            try:
                from .gui import main as gui_main
            except ImportError as error:
                raise WasdifyError("gui_unavailable") from error
            return gui_main(language=tr.language)
        service = Service()
        if args.command == "games":
            data = [{"id": game.id, "title": game.title[tr.language]} for game in service.catalog.games]
            emit({"games": data}, "\n".join(item["id"] + " — " + item["title"] for item in data))
        elif args.command == "patches":
            patches = service.list_patches(args.game)
            lines = []
            for patch in patches:
                lines.extend([patch.id + " — " + patch.title[tr.language],
                              patch.description[tr.language],
                              tr("implemented" if patch.implemented else "placeholder"),
                              tr("dependencies") + ": " + (", ".join(patch.dependencies) or tr("none")),
                              tr("conflicts") + ": " + (", ".join(patch.conflicts) or tr("none")), ""])
            emit({"patches": patches}, "\n".join(lines))
        elif args.command == "detect":
            detection = service.detect(args.path, args.game)
            issues = () if detection.game_id else (Issue("unknown_file"),)
            emit({"detection": detection, "issues": issues},
                 detection_text(detection, service, tr) + ("\n" + tr("unknown_file") if issues else ""))
            return exit_code(issues)
        else:
            plan = service.plan({"main": args.path}, tuple(args.patch), args.game)
            if args.command == "plan" or not plan.ready:
                emit({"plan": plan, "ready": plan.ready}, plan_text(plan, service, tr))
                return exit_code(plan.issues)
            result = service.apply(plan, backup=not args.no_backup)
            emit({"result": result}, apply_text(result, tr))
        return 0
    except UsageError:
        issue = Issue("invalid_arguments")
        emit({"issues": [issue]}, tr.issue(issue), error=True)
        return 2
    except WasdifyError as error:
        emit({"issues": [error.issue]}, tr.issue(error.issue), error=True)
        return exit_code((error.issue,))


def gui_main() -> int:
    """Lazy Tk import and the same localized diagnostics as `wasdify gui`."""
    return main(["gui", *sys.argv[1:]])

