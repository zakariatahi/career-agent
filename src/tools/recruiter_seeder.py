from data.target_companies import TARGET_COMPANIES


from src.database.db import (
    count_recruiters,
    count_recruiters_for_company,
    prune_unqualified_recruiters,
    save_recruiters,
)

from src.tools.linkedin_profiles import (
    find_verified_recruiters,
)


def seed_recruiters(
    target_count: int = 50,
    recruiters_per_company: int = 4,
    minimum_per_company: int = 2,
):

    removed = prune_unqualified_recruiters(minimum_network=500)
    if removed:
        print(
            f"Removed {removed} cached recruiter(s) that were outside "
            "Morocco or below 500 followers/connections."
        )

    current_total = count_recruiters()

    print(
        f"Recruiters already stored: {current_total}"
    )

    if current_total >= target_count:
        print(
            "Target already reached."
        )

        return current_total

    for company in TARGET_COMPANIES:

        current_total = count_recruiters()

        if current_total >= target_count:
            break

        existing = count_recruiters_for_company(
            company
        )

        if existing >= minimum_per_company:
            print(
                f"\nSkipping {company}: "
                f"{existing} recruiters already stored."
            )
            continue

        print(
            f"\n{'=' * 70}"
        )

        print(
            f"Searching recruiters for: {company}"
        )

        try:
            recruiters = find_verified_recruiters(
                company=company,
                top_k=recruiters_per_company,
            )

        except Exception as error:
            print(
                f"[Seeder error] {company}: {error}"
            )
            continue

        if not recruiters:
            print(
                "No verified recruiters found."
            )
            continue

        save_recruiters(
            recruiters
        )

        new_total = count_recruiters()

        print(
            f"Verified recruiters found: "
            f"{len(recruiters)}"
        )

        print(
            f"Total recruiters stored: "
            f"{new_total}"
        )

    final_total = count_recruiters()

    print(
        f"\n{'=' * 70}"
    )

    print(
        f"Recruiter seeding finished."
    )

    print(
        f"Final recruiter count: {final_total}"
    )

    return final_total
