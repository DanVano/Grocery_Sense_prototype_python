from Grocery_Sense.data.schema import initialize_database


def main():
    # First-time run or any startup: ensure DB tables exist
    initialize_database()
    print("Database initialized. You can now start building services & UI.")


def test_db_creation():
    main()


if __name__ == "__main__":
    main()