"""
Smoke tests for WeeklyPlannerService.
"""

from Grocery_Sense.data.schema import initialize_database
from Grocery_Sense.services.meal_suggestion_service import MealSuggestionService
from Grocery_Sense.services.shopping_list_service import ShoppingListService
from Grocery_Sense.services.weekly_planner_service import (
    WeeklyPlannerService,
    summarize_weekly_plan,
)


def main():
    print("=== WeeklyPlannerService smoke test ===")

    initialize_database()

    meal_svc = MealSuggestionService(price_history_service=None)
    sl_service = ShoppingListService()
    planner = WeeklyPlannerService(
        meal_suggestion_service=meal_svc,
        shopping_list_service=sl_service,
    )

    plan = planner.build_weekly_plan(
        num_recipes=6,
        map_ingredients=False,  # skip fuzzy matching against empty DB
        persist_to_shopping_list=False,
    )

    print(f"\nSuggestions: {len(plan.suggestions)}")
    print(f"Planned ingredients: {len(plan.planned_ingredients)}")

    summary = summarize_weekly_plan(plan)
    for line in summary:
        print(" ", line)

    print("\n=== WeeklyPlannerService smoke test complete ===")


if __name__ == "__main__":
    main()
