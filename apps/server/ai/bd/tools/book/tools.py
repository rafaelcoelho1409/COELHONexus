from langchain.tools import tool, ToolRuntime


@tool
async def book_term_frequency(
    variants: list[str], 
    runtime: ToolRuntime) -> str:
    """Count how often each spelling occurs in the whole book (whole words, ignoring case and soft hyphens).

    Use it to choose between spellings of one word, such as `cyberattack`, `cyber-attack` and `cyber attack`: the book's
    own usage is the evidence. Pass the exact spellings you want compared, at most eight.
    """
    return await runtime.context.term_counts(variants)


@tool
async def adjacent_page_edges(
    page_number: int, 
    lines: int, 
    runtime: ToolRuntime) -> str:
    """Show the last lines of the page before `page_number` and the first lines of the page after it.

    Use it when a defect may continue across the page break, such as a paragraph cut mid-sentence or a footnote number
    fused to the text. `lines` is how many lines to show from each page (1 to 8).
    """
    return await runtime.context.page_edges(page_number, lines)
