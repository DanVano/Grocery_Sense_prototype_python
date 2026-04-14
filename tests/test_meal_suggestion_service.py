"""
Smoke tests for MealSuggestionService and explain_suggested_meal.

Run with:
    python -m Grocery_Sense.tests.test_meal_suggestion_service_smoke
"""

from Grocery_Sense.services.meal_suggestion_service import (
    MealSuggestionService,
    explain_suggested_meal,
)


def main():
    print("=== MealSuggestionService smoke test ===")

    # For now we don't wire a real PriceHistoryService; we just check
    # that the engine runs and returns scored suggestions.
    svc = MealSuggestionService(price_history_service=None)

    # Example 1: no explicit target ingredients
    print("\n[1] Suggestions with no explicit ingredient targets:")
    suggestions = svc.suggest_meals_for_week(
        target_ingredients=None,
        max_recipes=5,
        recently_used_recipe_ids=None,
    )

    if not suggestions:
        print("No suggestions returned. Check that recipes.json is present and loadable.")
    else:
        for i, s in enumerate(suggestions, start=1):
            name = s.recipe.get("name", "Unnamed recipe")
            print(f"\nSuggestion #{i}: {name}")
            print(f"  total_score:      {s.total_score:.3f}")
            print(f"  price_score:      {s.price_score:.3f}")
            print(f"  preference_score: {s.preference_score:.3f}")
            print(f"  variety_score:    {s.variety_score:.3f}")
            if s.reasons:
                print("  reasons:")
                for r in s.reasons:
                    print(f"    - {r}")

            explanation = explain_suggested_meal(s)
            print("\n  Explanation:")
            print(explanation)

    # Example 2: explicitly bias toward chicken & rice
    print("\n[2] Suggestions targeting ['chicken', 'rice']:")
    suggestions2 = svc.suggest_meals_for_week(
        target_ingredients=["chicken", "rice"],
        max_recipes=5,
        recently_used_recipe_ids=None,
    )

    if not suggestions2:
        print("No suggestions for ['chicken', 'rice'].")
    else:
        for i, s in enumerate(suggestions2, start=1):
            name = s.recipe.get("name", "Unnamed recipe")
            print(f"\nSuggestion #{i} (chicken/rice): {name}")
            print(f"  total_score:      {s.total_score:.3f}")
            print(f"  price_score:      {s.price_score:.3f}")
            print(f"  preference_score: {s.preference_score:.3f}")

    print("\n=== MealSuggestionService smoke test complete ===")


def test_meal_suggestion_smoke():
    main()


if __name__ == "__main__":
    main()
