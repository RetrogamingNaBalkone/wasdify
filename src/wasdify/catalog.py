"""Packaged metadata; game executables are never package resources."""
import json
from importlib.resources import files

from .models import AdditionalFile, Game, KnownVersion, PatchDefinition, PatchExtension, WasdifyError


class Catalog:
    def __init__(self, games: tuple[Game, ...]):
        self.games = games
        self._games = {game.id: game for game in games}
        if len(self._games) != len(games):
            raise ValueError("Duplicate game ID")
        for game in games:
            extension_ids = set()
            if len({patch.id for patch in game.patches}) != len(game.patches):
                raise ValueError("Duplicate patch ID")
            if not game.roles or len(set(game.roles)) != len(game.roles):
                raise ValueError("Invalid target roles")
            for text in (game.title, *(p.title for p in game.patches),
                         *(p.description for p in game.patches)):
                if not all(text.get(lang) for lang in ("ru", "en")):
                    raise ValueError("Missing catalog translation")
            for version in game.versions:
                if (len(version.sha256) != 64 or version.size < 0
                        or any(c not in "0123456789abcdef" for c in version.sha256)):
                    raise ValueError("Invalid known version")
            for patch in game.patches:
                if not patch.roles or not set(patch.roles) <= set(game.roles):
                    raise ValueError("Invalid patch roles")
                if not set(patch.dependencies + patch.conflicts) <= {p.id for p in game.patches}:
                    raise ValueError("Unknown patch reference")
                for extension in patch.extensions:
                    if (not extension.id or extension.id in extension_ids
                            or extension.target == patch.id
                            or extension.target not in {p.id for p in game.patches}):
                        raise ValueError("Invalid patch extension")
                    if not all(extension.description.get(lang) for lang in ("ru", "en")):
                        raise ValueError("Missing extension translation")
                    extension_ids.add(extension.id)

    def game(self, game_id: str) -> Game:
        try:
            return self._games[game_id]
        except KeyError:
            raise WasdifyError("unknown_game", game=game_id) from None


def load_catalog() -> Catalog:
    data = json.loads(files("wasdify").joinpath("data", "games.json").read_text("utf-8"))
    games = []
    for item in data["games"]:
        patches = tuple(PatchDefinition(
            id=p["id"], title=p["title"], description=p["description"],
            roles=tuple(p["roles"]), dependencies=tuple(p["dependencies"]),
            conflicts=tuple(p["conflicts"]), implemented=p["implemented"],
            files=tuple(AdditionalFile(**f) for f in p.get("files", ())),
            extensions=tuple(PatchExtension(**e) for e in p.get("extensions", ())),
        ) for p in item["patches"])
        games.append(Game(item["id"], item["title"], tuple(item["roles"]),
                          tuple(KnownVersion(**v) for v in item["versions"]), patches))
    return Catalog(tuple(games))

