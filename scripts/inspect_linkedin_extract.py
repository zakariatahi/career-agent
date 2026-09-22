"""Compare focused and full Tavily extraction for one LinkedIn post."""

import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.tools.tavily_search import client


URL = (
    "https://www.linkedin.com/posts/emploi-express-maroc_"
    "offre-demploi-artificial-intelligence-"
    "activity-7495766589087232001-Aco8"
)


def summarize(label: str, response: dict) -> None:
    results = response.get("results", [])
    content = results[0].get("raw_content", "") if results else ""
    links = re.findall(r"https?://[^\s)>\]]+", content)
    emails = re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", content)
    marker = content.casefold().find("pour postuler")
    excerpt = content[max(0, marker - 100):marker + 500] if marker >= 0 else ""

    print("\n" + "=" * 80)
    print(label)
    print("Characters:", len(content))
    print("URLs:", links)
    print("Emails:", emails)
    print("Around application section:")
    print(excerpt or "Marker not found")


focused = client.extract(
    urls=[URL],
    extract_depth="advanced",
    format="markdown",
    query="job title company location requirements application email application link",
    chunks_per_source=5,
)
full = client.extract(
    urls=[URL],
    extract_depth="advanced",
    format="markdown",
)

summarize("FOCUSED EXTRACTION", focused)
summarize("FULL EXTRACTION", full)
