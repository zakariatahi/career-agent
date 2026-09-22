import argparse
import sys
from datetime import date


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run all configured job-search methods and show their results.")
    parser.add_argument("--query", default="AI Engineer", help="Role or keywords")
    parser.add_argument("--location", default="Morocco")
    parser.add_argument("--country", default="MA")
    parser.add_argument("--limit", type=int, default=5, help="Maximum jobs kept per source")
    parser.add_argument("--date", type=date.fromisoformat, help="Published on or after this date (YYYY-MM-DD, UTC)")
    parser.add_argument("--search-only", action="store_true", help="Show results from every source, then stop before ranking or applications")
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be positive")
    if not args.query.strip():
        parser.error("--query must not be empty")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("Search methods: LinkedIn, ReKrute, Dreamjob, Remotive, Tavily", flush=True)
    print(f"Query: {args.query} | Location: {args.location} | Limit per source: {args.limit}")
    print(f"Published on or after: {args.date or 'Source defaults (Tavily: last day)'}", flush=True)

    if args.search_only:
        from src.tools.job_collector import search_all_jobs

        jobs = search_all_jobs(
            query=args.query, location=args.location, country=args.country,
            limit_per_source=args.limit, post_date=args.date, verbose=True,
        )
        print(f"Search finished: {len(jobs)} unique job(s).", flush=True)
        return 0

    from langgraph.types import Command
    from src.database.db import init_db
    from src.graph.workflow import build_career_graph

    init_db()

    graph = build_career_graph()


    config = {
        "configurable": {
            "thread_id":
                "career-agent-session-1"
        }
    }


    result = graph.invoke(
        {
            "query": args.query,
            "location": args.location,
            "country": args.country,
            "post_date": args.date.isoformat() if args.date else None,
            "verbose_search": True,

            "limit_per_source": args.limit,
            "prefilter_top_k": 10,
            "minimum_score": 50,

            "cv_path": "data/base_cv.tex",
            "tailored_cv_path": "data/tailored_cv.tex",


        },
        config=config,
    )


    if result.get("no_jobs_message"):
        print(f"\n{result['no_jobs_message']}")
        return 0

    while "__interrupt__" in result:

        interrupt_data = (
            result["__interrupt__"][0].value
        )

        interrupt_type = (
            interrupt_data["type"]
        )

        # =====================================================
        # JOB SELECTION
        # =====================================================

        if interrupt_type == "job_selection":

            print(
                "\nTOP JOBS\n"
            )

            for job in interrupt_data["jobs"]:

                print("=" * 70)

                print(
                    f"{job['index'] + 1}. "
                    f"{job['title']}"
                )

                print(
                    "Company:",
                    job["company"],
                )

                print(
                    "Location:",
                    job["location"],
                )

                print(
                    "Source:",
                    job["source"],
                )

                print(
                    "Match:",
                    job["score"],
                )

                print(
                    "URL:",
                    job["url"],
                )

            selection = int(
                input(
                    "\nSelect job: "
                )
            )

            result = graph.invoke(
                Command(
                    resume={
                        "selected_index":
                            selection - 1
                    }
                ),
                config=config,
            )


        # =====================================================
        # CV APPROVAL
        # =====================================================

        elif interrupt_type == "cv_approval":

            approved = []

            edited = {}

            for change in interrupt_data[
                "changes"
            ]:

                print(
                    "\n" + "=" * 70
                )

                index = change["index"]

                print(
                    f"CHANGE {index + 1}"
                )

                print(
                    "\nORIGINAL:"
                )

                print(
                    change["original"]
                )

                print(
                    "\nPROPOSED:"
                )

                print(
                    change["proposed"]
                )

                print(
                    "\nREASON:"
                )

                print(
                    change["reason"]
                )

                choice = input(
                    "\n"
                    "[y] approve "
                    "[n] reject "
                    "[e] edit: "
                ).strip().lower()

                if choice == "y":

                    approved.append(
                        index
                    )

                elif choice == "e":

                    new_text = input(
                        "\nNew text:\n"
                    )

                    approved.append(
                        index
                    )

                    edited[
                        str(index)
                    ] = new_text

            result = graph.invoke(
                Command(
                    resume={
                        "approved_indices":
                            approved,

                        "edited_changes":
                            edited,
                    }
                ),
                config=config,
            )


        # =====================================================
        # EMAIL APPROVAL
        # =====================================================

        elif interrupt_type == "email_approval":

            email = interrupt_data[
                "email"
            ]

            print(
                "\n" + "=" * 70
            )

            print(
                "EMAIL DRAFT"
            )

            print(
                "\nSUBJECT:"
            )

            print(
                email["subject"]
            )

            print(
                "\nBODY:\n"
            )

            print(
                email["body"]
            )

            choice = input(
                "\n"
                "[y] approve "
                "[n] reject "
                "[e] edit: "
            ).strip().lower()

            if choice == "y":

                resume_data = {
                    "approved": True
                }

            elif choice == "e":

                subject = input(
                    "\nSubject "
                    "(leave empty to keep): "
                ).strip()

                print(
                    "\nEnter new email body."
                )

                body = input(
                    "Body: "
                ).strip()

                resume_data = {
                    "approved": True,

                    "subject":
                        subject
                        or email["subject"],

                    "body":
                        body
                        or email["body"],
                }

            else:

                resume_data = {
                    "approved": False
                }

            result = graph.invoke(
                Command(
                    resume=resume_data
                ),
                config=config,
            )


    print(
        "\nCareer Agent workflow finished."
    )


    if result.get("email_approved"):

        email = result[
            "email_draft"
        ]

        print(
            "\nFINAL EMAIL"
        )

        print(
            "Subject:",
            email.subject,
        )

        print(
            email.body
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
