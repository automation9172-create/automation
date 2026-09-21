from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import random
from typing import Iterable

from .catalog import Asset
from .history import known_signatures


THEME_POOLS = {
    "vehicles": {"bus", "car", "firetruck", "helicopter", "jeep", "motorcycle", "rocket", "scooter", "ship", "train", "van", "yellowbus"},
    "fruits": {"apple", "banana", "grapes", "lemon", "mango", "orange", "pineapple", "strawberry", "tomato", "watermelon"},
    "animals": {"cat", "dog", "dolphin", "elephant", "frog", "giraffe", "goat", "horse", "kangaroo", "koala", "lion", "monkey", "rabbit", "tiger", "turtle", "whale", "zebra"},
}


@dataclass
class ShortPlan:
    signature: str
    theme: str
    title: str
    description: str
    assets: list[Asset]
    style_index: int

    def manifest(self) -> dict:
        data = asdict(self)
        data["assets"] = [asset.name for asset in self.assets]
        return data


def _pool(assets: list[Asset], names: set[str] | None = None) -> list[Asset]:
    if not names:
        return list(assets)
    selected = [asset for asset in assets if asset.key in names]
    return selected or list(assets)


def _signature(theme: str, assets: Iterable[Asset], style: int) -> str:
    return f"{theme}|{','.join(asset.key for asset in assets)}|style-{style}"


def plan_batch(assets: list[Asset], history_path, count: int = 5, on_date: date | None = None, seed: int | None = None) -> list[ShortPlan]:
    if not assets:
        raise RuntimeError("No usable PNG assets were found in assets/real_png")
    known = known_signatures(history_path)
    day = on_date or date.today()
    seed_value = seed if seed is not None else int(hashlib.sha256(day.isoformat().encode()).hexdigest()[:12], 16)
    rng = random.Random(seed_value)
    result: list[ShortPlan] = []
    themes = ["quiz", "abc", "count", "vehicles", "animals", "colors"]
    for index in range(count):
        made = None
        for attempt in range(200):
            theme = themes[(index + attempt) % len(themes)]
            style = (seed_value + index + attempt) % 4
            if theme == "abc":
                start = (index * 4 + attempt + seed_value) % 23
                chosen = []
                for offset in range(4):
                    letter = chr(ord("a") + start + offset)
                    candidates = [asset for asset in assets if asset.letter == letter]
                    chosen.append(rng.choice(candidates or assets))
                title = f"Learn ABC with {chosen[0].name}, {chosen[1].name} & More | Sing Along #Shorts"
            elif theme == "count":
                pool = _pool(assets, THEME_POOLS["fruits"])
                chosen = rng.sample(pool, min(4, len(pool)))
                title = f"Count to 10 with {chosen[0].name}s! | Kids Counting Song #Shorts"
            elif theme == "vehicles":
                pool = _pool(assets, THEME_POOLS["vehicles"])
                chosen = rng.sample(pool, min(4, len(pool)))
                title = "Sing Along with Vehicles! | Kids Song #Shorts"
            elif theme == "animals":
                pool = _pool(assets, THEME_POOLS["animals"])
                chosen = rng.sample(pool, min(4, len(pool)))
                title = "What Animal Is This? | Kids Quiz #Shorts"
            elif theme == "colors":
                chosen = rng.sample(assets, min(4, len(assets)))
                title = f"Paint the {chosen[0].name}! | Learn Colors for Kids #Shorts"
            else:
                chosen = rng.sample(assets, min(4, len(assets)))
                title = "Can You Guess These? | Kids Quiz #Shorts"
            signature = _signature(theme, chosen, style)
            if signature in known or any(plan.signature == signature for plan in result):
                continue
            made = ShortPlan(signature, theme, title[:100], "Fun, original phonics practice for children. Learn, sing, and say each word aloud! #Shorts", chosen, style)
            break
        if made is None:
            raise RuntimeError("Could not find a non-repeating Shorts plan after 200 attempts")
        result.append(made)
    return result

