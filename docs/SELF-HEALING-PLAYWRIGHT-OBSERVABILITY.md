# Self-Healing Playwright with Observability

> **Status**: Future Implementation
> **Priority**: Medium
> **Complexity**: High
> **Prerequisites**: Loki logging, Alertmanager, Claude Code API access

## Overview

A self-healing system that automatically fixes Playwright scraping logic when YouTube changes their DOM structure or blocks access patterns.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         SELF-HEALING LOOP                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│   ┌──────────┐    ┌──────────┐    ┌──────────────┐    ┌─────────────┐  │
│   │ Playwright│───▶│  Error   │───▶│    Loki      │───▶│ Alertmanager│  │
│   │  Crawler  │    │  Occurs  │    │   Logging    │    │   Alert     │  │
│   └──────────┘    └──────────┘    └──────────────┘    └──────┬──────┘  │
│        ▲                                                      │         │
│        │                                                      ▼         │
│   ┌────┴─────┐    ┌──────────┐    ┌──────────────┐    ┌─────────────┐  │
│   │  Deploy  │◀───│   PR     │◀───│  Claude Code │◀───│   Webhook   │  │
│   │  Fix     │    │  Merge   │    │   Analysis   │    │   Trigger   │  │
│   └──────────┘    └──────────┘    └──────────────┘    └─────────────┘  │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

## Problem Statement

YouTube frequently changes:
- DOM selectors (button labels, element IDs, class names)
- Page structure (layout A/B tests)
- Anti-bot measures (new detection patterns)

Current impact:
- Manual investigation required when errors occur
- Downtime until developer fixes the issue
- No automatic recovery

## Proposed Solution

### 1. Structured Error Logging

```python
# In youtube_transcript.py
import structlog

logger = structlog.get_logger()

async def extract_via_dom(page: Page, timeout_ms: int) -> str:
    try:
        # ... extraction logic ...
    except Exception as e:
        # Capture full context for Claude Code analysis
        page_state = await page.evaluate('''
            () => ({
                url: window.location.href,
                title: document.title,
                html_snapshot: document.documentElement.outerHTML.slice(0, 50000),
                selectors_found: {
                    expand_btns: document.querySelectorAll('tp-yt-paper-button#expand').length,
                    transcript_btns: document.querySelectorAll('button[aria-label*="transcript"]').length,
                    panels: document.querySelectorAll('ytd-engagement-panel-section-list-renderer').length,
                },
                aria_labels: Array.from(document.querySelectorAll('button[aria-label]'))
                    .map(b => b.getAttribute('aria-label'))
            })
        ''')

        logger.error(
            "playwright_transcript_extraction_failed",
            error_type=type(e).__name__,
            error_message=str(e),
            video_id=video_id,
            page_state=page_state,
            current_selectors=TRANSCRIPT_SELECTORS,
            extraction_method="dom_scrape",
        )
        raise
```

### 2. Alertmanager Rule

```yaml
# alertmanager/rules/playwright.yaml
groups:
  - name: playwright-self-healing
    rules:
      - alert: PlaywrightTranscriptExtractionFailed
        expr: |
          sum(rate(playwright_transcript_errors_total[5m])) > 0.1
        for: 2m
        labels:
          severity: warning
          auto_heal: "true"
        annotations:
          summary: "YouTube transcript extraction failing"
          description: "Error rate {{ $value }} in last 5 minutes"
          runbook_url: "https://github.com/coelhonexus/runbooks/playwright-healing"
```

### 3. Webhook Handler

```python
# apps/fastapi/routers/v1/webhooks/alertmanager.py
from fastapi import APIRouter, BackgroundTasks
import httpx

router = APIRouter()

@router.post("/alertmanager/self-heal")
async def handle_alert(alert: AlertManagerPayload, background_tasks: BackgroundTasks):
    if alert.labels.get("auto_heal") != "true":
        return {"status": "skipped", "reason": "auto_heal not enabled"}

    if alert.labels.get("alertname") == "PlaywrightTranscriptExtractionFailed":
        background_tasks.add_task(
            trigger_claude_code_fix,
            error_type="playwright_transcript",
            time_range="5m",
        )

    return {"status": "healing_triggered"}


async def trigger_claude_code_fix(error_type: str, time_range: str):
    """Query Loki for errors and trigger Claude Code to fix them."""

    # 1. Query Loki for recent errors with full context
    loki_query = f'''
        {{app="fastapi"}} |= "playwright_transcript_extraction_failed"
        | json
        | line_format "{{.page_state}}"
    '''

    errors = await query_loki(loki_query, time_range)

    # 2. Prepare prompt for Claude Code
    prompt = f"""
    YouTube transcript extraction is failing. Analyze these errors and fix the code.

    Recent errors:
    {json.dumps(errors, indent=2)}

    Current extraction code:
    {read_file('apps/fastapi/scripts/youtube_transcript.py')}

    Instructions:
    1. Analyze the page_state HTML snapshots to identify working selectors
    2. Update TRANSCRIPT_SELECTORS with new selectors
    3. Test the fix with: python -m scripts.youtube_transcript dQw4w9WgXcQ
    4. If successful, create a PR with the fix
    5. If unsuccessful after 3 attempts, escalate to human
    """

    # 3. Trigger Claude Code via API or CLI
    result = await run_claude_code(prompt, max_attempts=3)

    # 4. Track outcome
    if result.success:
        logger.info("self_healing_success", error_type=error_type, pr_url=result.pr_url)
    else:
        logger.error("self_healing_failed", error_type=error_type, attempts=result.attempts)
        await send_slack_notification("Self-healing failed, manual intervention needed")
```

### 4. Claude Code Execution

```python
# apps/fastapi/services/claude_code_runner.py
import subprocess
import asyncio

async def run_claude_code(prompt: str, max_attempts: int = 3) -> HealingResult:
    """Run Claude Code with the healing prompt."""

    for attempt in range(max_attempts):
        # Run Claude Code in non-interactive mode
        process = await asyncio.create_subprocess_exec(
            "claude",
            "--print",  # Non-interactive
            "--allowedTools", "Read,Edit,Bash,Grep",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate(prompt.encode())

        # Check if Claude Code made changes
        git_status = subprocess.run(["git", "status", "--porcelain"], capture_output=True)

        if git_status.stdout:
            # Changes were made, test them
            test_result = subprocess.run([
                "python", "-m", "scripts.youtube_transcript", "dQw4w9WgXcQ"
            ], capture_output=True, timeout=60)

            if test_result.returncode == 0:
                # Success! Create PR
                pr_url = await create_pr(
                    branch=f"auto-heal/playwright-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
                    title="fix(playwright): auto-heal YouTube transcript extraction",
                    body=f"Automated fix by self-healing system.\n\nAttempt: {attempt + 1}",
                )
                return HealingResult(success=True, pr_url=pr_url, attempts=attempt + 1)
            else:
                # Test failed, revert and try again
                subprocess.run(["git", "checkout", "."])

    return HealingResult(success=False, attempts=max_attempts)
```

## Implementation Phases

### Phase 1: Structured Logging (1-2 days)
- Add structlog with JSON output
- Capture page state on errors
- Ship logs to Loki

### Phase 2: Alerting (1 day)
- Create Alertmanager rules
- Set up webhook endpoint
- Test alert triggering

### Phase 3: Claude Code Integration (2-3 days)
- Implement Claude Code runner
- Create healing prompts
- Add PR creation logic

### Phase 4: Testing & Tuning (1-2 days)
- Simulate YouTube changes
- Tune alert thresholds
- Add circuit breakers

## Safety Guardrails

1. **Rate limiting**: Max 3 healing attempts per hour
2. **Human review**: PRs require approval before merge
3. **Rollback**: Auto-revert if error rate increases after deploy
4. **Scope limits**: Claude Code can only edit specific files
5. **Test validation**: Changes must pass tests before PR

## Metrics to Track

```promql
# Self-healing success rate
sum(rate(self_healing_success_total[1d])) / sum(rate(self_healing_attempts_total[1d]))

# Time to heal (from error to fix deployed)
histogram_quantile(0.95, rate(self_healing_duration_seconds_bucket[1d]))

# Manual intervention rate
sum(rate(self_healing_escalated_total[1d])) / sum(rate(self_healing_attempts_total[1d]))
```

## Alternative Approaches

### Option A: Scheduled Selector Refresh
Run Claude Code weekly to proactively update selectors based on YouTube's current DOM.

### Option B: Multiple Selector Strategies
Maintain 3-4 different extraction strategies, automatically switch when one fails.

### Option C: Visual AI Detection
Use Claude's vision capabilities to identify UI elements from screenshots instead of DOM selectors.

## References

- [Claude Code CLI Documentation](https://docs.anthropic.com/claude-code)
- [Loki LogQL](https://grafana.com/docs/loki/latest/query/)
- [Alertmanager Webhooks](https://prometheus.io/docs/alerting/latest/configuration/#webhook_config)
