"""Local smoke test for the arxiv_search MCP tool.

Run AFTER `skaffold dev` is up and port-forward 23024 is live:

    # default — quick sanity check
    uv run python scripts/test_arxiv_local.py

    # custom query + category filter
    uv run python scripts/test_arxiv_local.py "deep agents" 10 cs.LG cs.AI

Or from inside the running pod (no fastmcp install needed on the host):

    kubectl exec -it -n coelhonexus-dev deploy/coelhonexus-fastmcp -- \\
        python scripts/test_arxiv_local.py "agentic RAG" 5

The script connects to the MCP endpoint, lists registered tools, calls
arxiv_search, and pretty-prints the first few hits — exercising the full
JSON-RPC over Streamable-HTTP round-trip plus Pydantic schema validation.
"""
import asyncio
import sys

import fastmcp


URL = "http://localhost:23024/mcp/"


async def main() -> None:
    query = sys.argv[1] if len(sys.argv) > 1 else "deep agents context engineering"
    n_max = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    categories = sys.argv[3:] or None

    async with fastmcp.Client(URL) as client:
        tools = await client.list_tools()
        print(f"\nTools on {URL}: {[t.name for t in tools]}\n")

        args = {"input": {"query": query, "n_max": n_max}}
        if categories:
            args["input"]["categories"] = list(categories)

        print(f"Calling arxiv_search(query={query!r}, n_max={n_max}, "
              f"categories={categories})\n")

        result = await client.call_tool("arxiv_search", args)
        # FastMCP returns a CallToolResult; .data carries the parsed list.
        papers = getattr(result, "data", None) or result

        if not papers:
            print("  (no hits)\n")
            return

        for i, p in enumerate(papers, 1):
            # `papers` items are dicts from JSON; tolerate either form.
            get = (lambda k, _p=p: _p[k] if isinstance(_p, dict) else getattr(_p, k))
            print(f"{i}. {get('title')}")
            print(f"   {get('arxiv_id')}  ·  {get('primary_category')}  "
                  f"·  {str(get('published'))[:10]}")
            print(f"   {get('abs_url')}")
            print()


if __name__ == "__main__":
    asyncio.run(main())
