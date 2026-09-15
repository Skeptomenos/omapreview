# Batch I — a progressively disclosed PDF skill

Goal: an agent loads a small entry skill, chooses the available CLI or MCP
interface, and loads only the guidance needed to complete and verify the
user's PDF task.

This is the implementation brief. Live status belongs only to `batch-i` in
the [shared plan](/home/david/.local/state/omapreview/coordination/review-remediation.md).
Start after Batch H is integrated and its verification gates pass. The skill
must describe the implemented CLI/MCP contracts, not H's proposed API.

## Scope and ownership

Own `skill/SKILL.md`, its disclosed references, small reusable examples where
needed, and tests/evidence for skill routing and instructions. Keep the product
skill canonical in this repository. Preserve its existing invocation name
unless a deliberate migration is agreed; use `omapreview` in visitor-facing
commands. Do not install or relocate personal framework skills as part of I.

H keeps its required corrections to the current playbook and verification
loop. I then restructures and expands that accurate foundation. I may run in
parallel with D/E/F after H because it owns skill files, not their source.
Any later API change must trigger a recheck of affected examples before the
skill's final integration. Keep the entry path stable for README links.

## Required steps

### 1. Inventory supported scenarios and the shipped interfaces

Read the post-H operation catalog, CLI help, actual MCP tool schemas and the
verification guide. Map existing recipes to supported behavior. Remove stale
claims such as universal capabilities or unconditional atomicity. Separate
application features from reasoning an agent performs with extracted content.

### 2. Build a small entry skill with explicit routes

Aim for a body under about 100 lines. Keep only these common decisions inline:

- Resolve the source/destination and intended result from the request and
  available facts. Ask only for information that blocks the task.
- Select the interface from explicit user choice and available capabilities.
- Read the selected mode guide and scenario guide using their exact paths.
- Preserve authorization, source data and coordinate conventions.
- Verify the saved output before reporting a mutation complete; report an
  unavailable or failed required check explicitly.

Write a concise discovery description with distinct task branches. Each
reference pointer must state **when to load it and what it supplies**.
Do not tell agents to read the entire reference directory at startup.

Suggested layout (adjust grouping to avoid small, repetitive files):

```text
skill/
  SKILL.md
  references/
    cli.md
    mcp.md
    verification.md
    read-review.md
    markup.md
    forms-signatures.md
    redaction.md
    pages-export.md
```

The entry skill owns routing and common completion rules. Mode guides own
transport mechanics. Scenario guides own task decisions and examples. One
verification guide owns detailed checks and failure handling. Link to the
live operation catalog for exhaustive schemas instead of copying them.

### 3. Support CLI and MCP as independent execution modes

| Situation | Required behavior |
|---|---|
| User specifies CLI or MCP | Use that mode; report a missing capability if it blocks completion. |
| Only one interface is available | Load that guide and complete supported tasks through it. |
| Both are available, no preference | Prefer MCP's structured tools and page images; use CLI for an identified capability gap where available. |
| A mode fails or lacks a feature | Establish whether any write occurred before recovery. Explain a required mode change; never repeat an uncertain mutation blindly or bypass confirmation. |

CLI guidance covers discovery, structured output, op files/stdin, dry-run and
confirmation, safe destinations, page snapshots and command error handling.
MCP guidance covers tool discovery, argument schemas, image content blocks,
structured results, dry-run/confirmation, and rereading/rerendering saved output.
An MCP-only recipe must not depend on hidden shell access or an inaccessible
local screenshot path. GUI handoff is an explicit optional route when available.

### 4. Add scenario guidance with clear completion checks

| Scenario | Decisions and checks the guide must cover |
|---|---|
| Read, summarize, answer or review | Relevant/full-document coverage based on the request; page citations; scanned/image-only limitations; no invented text or claims of built-in OCR/diff. |
| Annotate, redline, mark up or stamp | Match versus rectangle targeting; repeated text; text boxes, styles, shapes and ink; inspect saved placement and neighboring content. |
| Fill forms | Discover actual fields; avoid invented values; handle ambiguity and non-form text placement; reread the intended saved field. |
| Sign or initial | Use an authorized saved signature; identify placement; handle absent signatures; verify appearance and date; distinguish visual from certificate signing. |
| Redact | Identify exact targets and preserved content; preview/confirmation; verify saved content and payload removal plus rendering; distinguish ink coverage and flattening from redaction. |
| Reorganize, crop or extract pages | Page identity/order and coordinate effects; preserve source as required; inspect saved page count, content and geometry. |
| Finalize or hand off | Verify flatten output and retained appearance; refuse pending redactions; identify the verified artifact. Sharing needs separately available tools and user authorization. |

Each route needs initial facts, the mode-specific commands/tools, stop or
recovery conditions, and observable completion criteria. Include partial-task
reporting when one requested action is unsupported. Keep essential verification
mandatory even though its detailed procedure is progressively disclosed.

### 5. Validate the skill as instructions an agent can follow

- Check frontmatter, reference reachability, name/path compatibility and
  command/tool examples against the integrated application. Keep all required
  references with the skill bundle; a clean copy of `skill/` must not depend
  on this machine's workspace paths or missing sibling documentation.
- Run bounded fresh-context trials for each scenario above. Record selected
  mode, loaded references, tool calls, output artifacts and verification.
  Include a CLI-only and MCP-only route for every supported scenario.
- Include a both-interfaces routing trial and failures: missing signature,
  unsupported scanned-text task, ambiguous field, denied consequential action,
  stale/wrong output and unavailable verification. Safe refusal or an explicit
  partial result is the correct outcome where the capability is absent.
- Use synthetic PDFs and signature assets. Execute real CLI commands and real
  MCP calls for the recipes. Reuse H's fixtures and protocol harness; build no
  general evaluation platform. Observe the shared GUI lease for native trials.
- Test the skill from its intended discovery/loading route in a fresh context.
  Show that only relevant mode/scenario references are loaded and that an
  agent cannot claim success without the required saved-result checks.
- Run appropriate repository checks and the required full test suite; distinguish
  instruction failures from product defects still assigned to D/E/F. Refresh
  affected evidence after their interface changes or integration conflicts.

## Completion and handback

Provide the final skill tree and description, routing/recipe coverage, real
CLI/MCP evidence, reference-loading observations, tested commit and remaining
limits. A short entry file alone is not proof of progressive disclosure.
The skill is complete only when its supported routes work and its unsupported
routes end truthfully. Record all live progress in `batch-i`; no implementation
task is launched by preparing this brief.
