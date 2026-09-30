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
                title = (
                    f"{chosen[0].letter.upper()} for {chosen[0].name} | "
                    f"{chosen[1].letter.upper()} for {chosen[1].name} | "
                    f"abcd | phonics song"
                )
                desc = (
                    f"{chosen[0].letter.upper()} for {chosen[0].name} | "
                    f"{chosen[1].letter.upper()} for {chosen[1].name} | "
                    f"{chosen[2].letter.upper()} for {chosen[2].name} | "
                    f"{chosen[3].letter.upper()} for {chosen[3].name}\n\n"
                    "Watch, sing, and say it aloud! Perfect for toddlers and preschoolers.\n\n"
                    "#Shorts #ABCSong #PhonicsForKids #LearnABC #KidsEducation #AlphabetSong "
                    "#KidsSong #MadeForKids #Preschool #Toddlers #PhonicsLesson #ABCKids "
                    "#ChildrenSong #EnglishAlphabet #KindergartenLearning #abcd #phonicssong"
                )
            elif theme == "count":
                pool = _pool(assets, THEME_POOLS["fruits"])
                chosen = rng.sample(pool, min(4, len(pool)))
                title = (
                    f"1 for {chosen[0].name} | 2 for {chosen[1].name} | "
                    f"counting song | kids learning"
                )
                desc = (
                    f"1 for {chosen[0].name} | 2 for {chosen[1].name} | "
                    f"3 for {chosen[2].name} | 4 for {chosen[3].name}\n\n"
                    "Fun counting practice for kids and toddlers.\n\n"
                    "#Shorts #CountingSong #KidsLearning #NumbersForKids #MadeForKids "
                    "#Preschool #Toddlers #KidsEducation #LearnNumbers #123Kids #counting"
                )
            elif theme == "vehicles":
                pool = _pool(assets, THEME_POOLS["vehicles"])
                chosen = rng.sample(pool, min(4, len(pool)))
                title = (
                    f"{chosen[0].name} | {chosen[1].name} | "
                    f"vehicles song | kids phonics"
                )
                desc = (
                    f"{chosen[0].name} | {chosen[1].name} | "
                    f"{chosen[2].name} | {chosen[3].name}\n\n"
                    "Sing along and learn about vehicles!\n\n"
                    "#Shorts #VehicleSong #KidsLearning #MadeForKids #Preschool "
                    "#Toddlers #KidsEducation #CarSong #VehiclesForKids #phonicssong"
                )
            elif theme == "animals":
                pool = _pool(assets, THEME_POOLS["animals"])
                chosen = rng.sample(pool, min(4, len(pool)))
                title = (
                    f"{chosen[0].name} | {chosen[1].name} | "
                    f"animal sounds | kids phonics song"
                )
                desc = (
                    f"{chosen[0].name} | {chosen[1].name} | "
                    f"{chosen[2].name} | {chosen[3].name}\n\n"
                    "Learn animal names and sounds!\n\n"
                    "#Shorts #AnimalSong #KidsLearning #MadeForKids #Preschool "
                    "#Toddlers #KidsEducation #AnimalsForKids #animalsounds #phonicssong"
                )
            elif theme == "colors":
                chosen = rng.sample(assets, min(4, len(assets)))
                title = (
                    f"{chosen[0].name} | {chosen[1].name} | "
                    f"learn colors | kids song"
                )
                desc = (
                    f"{chosen[0].name} | {chosen[1].name} | "
                    f"{chosen[2].name} | {chosen[3].name}\n\n"
                    "Learn colors with fun!\n\n"
                    "#Shorts #ColorSong #KidsLearning #MadeForKids #Preschool "
                    "#Toddlers #KidsEducation #LearnColors #ColorsForKids #kidssong"
                )
            else:
                chosen = rng.sample(assets, min(4, len(assets)))
                title = (
                    f"{chosen[0].name} | {chosen[1].name} | "
                    f"phonics quiz | kids learning"
                )
                desc = (
                    f"{chosen[0].name} | {chosen[1].name} | "
                    f"{chosen[2].name} | {chosen[3].name}\n\n"
                    "Say each word aloud and learn!\n\n"
                    "#Shorts #PhonicsQuiz #KidsLearning #MadeForKids #Preschool "
                    "#Toddlers #KidsEducation #PhonicsForKids #KidsQuiz #phonicssong"
                )
            signature = _signature(theme, chosen, style)
            if signature in known or any(plan.signature == signature for plan in result):
                continue
            made = ShortPlan(signature, theme, title[:100], desc, chosen, style)
            break
        if made is None:
            raise RuntimeError("Could not find a non-repeating Shorts plan after 200 attempts")
        result.append(made)
    return result

