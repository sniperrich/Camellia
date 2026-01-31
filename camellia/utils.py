from thefuzz import fuzz, process
from typing import List
from .models.entities import NetGameItem

from thefuzz import fuzz
from typing import List
from .models.entities import NetGameItem


def fuzzy_search_games(query, game_list: List["NetGameItem"], limit=-1) -> List["NetGameItem"]:
    if not query:
        return game_list[:limit] if limit > 0 else game_list

    scored_results = []

    for item in game_list:
        search_text = f"{item.name} {item.brief_summary if item.brief_summary else ''}"

        score = fuzz.token_set_ratio(query, search_text)

        if score > 0:
            scored_results.append((item, score))

    scored_results.sort(key=lambda x: x[1], reverse=True)

    final_results = [res[0] for res in scored_results]

    if limit > 0:
        return final_results[:limit]
    return final_results