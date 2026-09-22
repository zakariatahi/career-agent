"""Print Tavily result metadata without invoking the job classifier."""

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.tools.tavily_search import search_web
from src.tools.linkedin_post_search import get_linkedin_post_datetime


results = search_web(
    query="site:linkedin.com/posts AI Engineer Morocco",
    max_results=10,
    days=1,
    include_domains=["linkedin.com/posts"],
)

for result in results:
    url = result.get("url", "")
    print(url)
    print("published_date:", result.get("published_date"))
    print("activity_date:", get_linkedin_post_datetime(url))
    print()
