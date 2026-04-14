"""
Manual smoke test for IngredientMappingService.
"""

from Grocery_Sense.data.schema import initialize_database
import Grocery_Sense.data.repositories.items_repo as items_repo_module
from Grocery_Sense.services.ingredient_mapping_service import IngredientMappingService


def main():
    initialize_database()

    mapper = IngredientMappingService(items_repo=items_repo_module)

    samples = [
        "CHK THG BP SKLS",
        "Chicken Thighs Value Pack",
        "chicken thighs bulk",
        "GRND BF",
        "Fresh basil",
    ]

    for s in samples:
        res = mapper.map_to_item(s)
        print("\nINPUT:", s)
        print(" ->", res)


def test_item_mapping_smoke():
    main()


if __name__ == "__main__":
    main()
