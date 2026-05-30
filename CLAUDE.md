# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**NotebookLM MCP Server & CLI** - Provides programmatic access to NotebookLM (notebooklm.google.com) via both a Model Context Protocol server and a comprehensive command-line interface.

Tested with personal/free tier accounts. May work with Google Workspace accounts but has not been tested.

## Development Commands

```bash
# Install dependencies
uv tool install .

# Reinstall after code changes (ALWAYS clean cache first)
uv cache clean && uv tool install --force .

# Run the MCP server (stdio)
notebooklm-mcp

# Run with Debug logging
notebooklm-mcp --debug

# Run as HTTP server
notebooklm-mcp --transport http --port 8000

# Run tests
uv run pytest

# Run a single test
uv run pytest tests/test_file.py::test_function -v
```

**Python requirement:** >=3.11

## Authentication (SIMPLIFIED!)

**You only need to provide COOKIES!** The CSRF token and session ID are now **automatically extracted** when needed.

### Method 1: Chrome DevTools MCP (Recommended)

**Option A - Fast (Recommended):**
Extract CSRF token and session ID directly from network request - **no page fetch needed!**

```python
# 1. Navigate to NotebookLM page
navigate_page(url="https://notebooklm.google.com/")

# 2. Get a batchexecute request (any NotebookLM API call)
get_network_request(reqid=<any_batchexecute_request>)

# 3. Save with all three fields from the network request:
save_auth_tokens(
    cookies=<cookie_header>,
    request_body=<request_body>,  # Contains CSRF token
    request_url=<request_url>      # Contains session ID
)
```

**Option B - Minimal (slower first call):**
Save only cookies, tokens extracted from page on first API call

```python
save_auth_tokens(cookies=<cookie_header>)
```

### Method 2: Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `NOTEBOOKLM_COOKIES` | Yes | Full cookie header from Chrome DevTools |
| `NOTEBOOKLM_CSRF_TOKEN` | No | (DEPRECATED - auto-extracted) |
| `NOTEBOOKLM_SESSION_ID` | No | (DEPRECATED - auto-extracted) |
| `NOTEBOOKLM_BL` | No | Override for build label / bl URL param (auto-extracted from page) |
| `NOTEBOOKLM_HL` | No | Interface language and default artifact language (default: `en`) |

### Token Expiration

- **Cookies**: Stable for weeks, but some rotate on each request
- **CSRF token**: Auto-refreshed on each client initialization
- **Session ID**: Auto-refreshed on each client initialization
- **Build label (bl)**: Auto-extracted during login and CSRF refresh; stays current with Google's build

When API calls fail with auth errors, re-extract fresh cookies from Chrome DevTools.

## Architecture

```
src/notebooklm_tools/
├── __init__.py          # Package version
├── services/            # Shared service layer (v0.3.0+)
│   ├── errors.py        # ServiceError, ValidationError, NotFoundError, etc.
│   ├── chat.py          # Chat/query logic
│   ├── downloads.py     # Artifact downloading
│   ├── exports.py       # Google Docs/Sheets export
│   ├── notebooks.py     # Notebook CRUD + describe
│   ├── notes.py         # Note CRUD
│   ├── research.py      # Research start/poll/import
│   ├── sharing.py       # Public link, invite, status
│   ├── sources.py       # Source add/list/sync/delete
│   └── studio.py        # Artifact creation, status, rename, delete
├── cli/                 # CLI commands and formatting (thin wrapper)
├── mcp/                 # MCP server + tools (thin wrapper)
│   ├── server.py        # FastMCP server facade
│   └── tools/           # Modular tool definitions per domain
├── core/                # Low-level API client (no business logic)
│   ├── client.py        # Internal batchexecute API calls
│   ├── constants.py     # Code-name mappings (CodeMapper class)
│   └── auth.py          # AuthManager for profile-based token caching
└── utils/
    ├── config.py        # Configuration and storage paths
    └── cdp.py           # Chrome DevTools Protocol for cookie extraction
```

**Layering Rules (v0.3.0+):**
- `cli/` and `mcp/` are thin wrappers: they handle UX concerns (prompts, spinners, JSON responses) and delegate to `services/`
- `services/` contains all business logic, validation, and error handling. Returns typed dicts.
- `cli/` and `mcp/` must NOT import from `core/` directly — always go through `services/`
- `services/` raises `ServiceError`/`ValidationError` — never raw exceptions

**Storage Structure (`~/.notebooklm-mcp-cli/`):**
```
├── config.toml                    # CLI settings (default_profile, output format)
├── aliases.json                   # Notebook aliases
├── profiles/<name>/auth.json      # Per-profile credentials and email
├── chrome-profile/                # Chrome session (single-profile/legacy)
└── chrome-profiles/<name>/        # Chrome sessions (multi-profile)
```

**Executables:**
- `nlm` - Command-line interface
- `notebooklm-mcp` - The MCP server

## MCP Tools Provided

| Tool | Purpose |
|------|---------|
| `notebook_list` | List all notebooks |
| `notebook_create` | Create new notebook |
| `notebook_get` | Get notebook details |
| `notebook_describe` | Get AI-generated summary of notebook content with keywords |
| `source_describe` | Get AI-generated summary and keyword chips for a source |
| `source_get_content` | Get raw text content from a source (no AI processing) |
| `notebook_rename` | Rename a notebook |
| `chat_configure` | Configure chat goal/style and response length |
| `notebook_delete` | Delete a notebook (REQUIRES confirmation) |
| `source_add` | Add source (url, text, drive, file) |
| `notebook_query` | Ask questions (AI answers!) |
| `source_list_drive` | List sources with types, check Drive freshness |
| `source_sync_drive` | Sync stale Drive sources (REQUIRES confirmation) |
| `source_rename` | Rename a source in a notebook |
| `source_delete` | Delete a source from notebook (REQUIRES confirmation) |
| `research_start` | Start Web or Drive research to discover sources |
| `research_status` | Check research progress and get results |
| `research_import` | Import discovered sources into notebook |
| `studio_create` | Generate unified content (audio, video, infographic, slides, etc.) |
| `download_artifact` | Download any artifact (audio, video, pdf, markdown, json) |
| `export_artifact` | Export Data Tables to Google Sheets or Reports to Google Docs |
| `studio_status` | Check studio artifact generation status |
| `studio_delete` | Delete studio artifacts (REQUIRES confirmation) |
| `studio_revise` | Revise slides in an existing slide deck (creates new artifact, REQUIRES confirmation) |
| `notebook_share_status` | Get sharing settings and collaborators |
| `notebook_share_public` | Enable/disable public link access |
| `notebook_share_invite` | Invite collaborator by email |
| `save_auth_tokens` | Save tokens extracted via Chrome DevTools MCP |
| `refresh_auth` | Reload auth tokens or run headless auth |
| `note_create` | Create a note in a notebook |
| `note_list` | List all notes in a notebook |
| `note_update` | Update a note's content or title |
| `note_delete` | Delete a note (REQUIRES confirmation) |

**IMPORTANT - Operations Requiring Confirmation:**
- `notebook_delete` requires `confirm=True` - deletion is IRREVERSIBLE
- `source_delete` requires `confirm=True` - deletion is IRREVERSIBLE
- `source_sync_drive` requires `confirm=True` - always show stale sources first via `source_list_drive`
- All studio creation tools require `confirm=True` - show settings and get user approval first
- `studio_delete` requires `confirm=True` - list artifacts first via `studio_status`, deletion is IRREVERSIBLE
- `studio_revise` requires `confirm=True` - creates a new artifact with revisions applied
- `note_delete` requires `confirm=True` - deletion is IRREVERSIBLE

## Features NOT Yet Implemented

None - all NotebookLM features that can be accessed programmatically are implemented.

## Troubleshooting

### "401 Unauthorized" or "403 Forbidden"
- Cookies or CSRF token expired
- Re-extract from Chrome DevTools

### "Invalid CSRF token"
- The `at=` value expired
- Must match the current session

### Empty notebook list
- Session might be for a different Google account
- Verify you're logged into the correct account

### Rate limit errors
- Free tier: ~50 queries/day
- Wait until the next day or upgrade to Plus

## Documentation

### API Reference

**For detailed API documentation** (RPC IDs, parameter structures, response formats), see:

**[docs/API_REFERENCE.md](./docs/API_REFERENCE.md)**

This includes:
- All discovered RPC endpoints and their parameters
- Source type structures (URL, text, Drive)
- Studio content creation (audio, video, reports, etc.)
- Research workflow details
- Mind map generation process
- Source metadata structures

Only read API_REFERENCE.md when:
- Debugging API issues
- Adding new features
- Understanding internal API behavior

### MCP Test Plan

**For comprehensive MCP tool testing**, see:

**[docs/MCP_CLI_TEST_PLAN.md](./docs/MCP_CLI_TEST_PLAN.md)**

This includes:
- Step-by-step test cases for all 29 MCP tools and CLI commands
- Authentication and basic operations tests
- Source management and Drive sync tests
- Studio content generation tests (audio, video, infographics, etc.)
- Quick copy-paste test prompts for validation

Use this test plan when:
- Validating MCP server functionality after code changes
- Testing new tool implementations
- Debugging MCP tool issues

## Contributing

When adding new features:

1. Use Chrome DevTools MCP to capture the network request
2. Document the RPC ID in docs/API_REFERENCE.md
3. Add the param structure with comments
4. Add the low-level API method in `core/client.py`
5. Add business logic in the appropriate `services/*.py` module
6. Add a thin wrapper in `mcp/tools/*.py` (for MCP) and `cli/commands/*.py` (for CLI)
7. Write unit tests for the service function in `tests/services/`
8. Update the "Features NOT Yet Implemented" checklist
9. Add test case to docs/MCP_TEST_PLAN.md

## License

MIT License

<!-- dgc-policy-v11 -->
# Dual-Graph Context Policy

This project uses a local dual-graph MCP server for efficient context retrieval.

## MANDATORY: Always follow this order

1. **Call `graph_continue` first** — before any file exploration, grep, or code reading.

2. **If `graph_continue` returns `needs_project=true`**: call `graph_scan` with the
   current project directory (`pwd`). Do NOT ask the user.

3. **If `graph_continue` returns `skip=true`**: project has fewer than 5 files.
   Do NOT do broad or recursive exploration. Read only specific files if their names
   are mentioned, or ask the user what to work on.

4. **Read `recommended_files`** using `graph_read` — **one call per file**.
   - `graph_read` accepts a single `file` parameter (string). Call it separately for each
     recommended file. Do NOT pass an array or batch multiple files into one call.
   - `recommended_files` may contain `file::symbol` entries (e.g. `src/auth.ts::handleLogin`).
     Pass them verbatim to `graph_read(file: "src/auth.ts::handleLogin")` — it reads only
     that symbol's lines, not the full file.
   - Example: if `recommended_files` is `["src/auth.ts::handleLogin", "src/db.ts"]`,
     call `graph_read(file: "src/auth.ts::handleLogin")` and `graph_read(file: "src/db.ts")`
     as two separate calls (they can be parallel).

5. **Check `confidence` and obey the caps strictly:**
   - `confidence=high` -> Stop. Do NOT grep or explore further.
   - `confidence=medium` -> If recommended files are insufficient, call `fallback_rg`
     at most `max_supplementary_greps` time(s) with specific terms, then `graph_read`
     at most `max_supplementary_files` additional file(s). Then stop.
   - `confidence=low` -> Call `fallback_rg` at most `max_supplementary_greps` time(s),
     then `graph_read` at most `max_supplementary_files` file(s). Then stop.

## Token Usage

A `token-counter` MCP is available for tracking live token usage.

- To check how many tokens a large file or text will cost **before** reading it:
  `count_tokens({text: "<content>"})`
- To log actual usage after a task completes (if the user asks):
  `log_usage({input_tokens: <est>, output_tokens: <est>, description: "<task>"})`
- To show the user their running session cost:
  `get_session_stats()`

Live dashboard URL is printed at startup next to "Token usage".

## Rules

- Do NOT use `rg`, `grep`, or bash file exploration before calling `graph_continue`.
- Do NOT do broad/recursive exploration at any confidence level.
- `max_supplementary_greps` and `max_supplementary_files` are hard caps - never exceed them.
- Do NOT dump full chat history.
- Do NOT call `graph_retrieve` more than once per turn.
- After edits, call `graph_register_edit` with the changed files. Use `file::symbol` notation (e.g. `src/auth.ts::handleLogin`) when the edit targets a specific function, class, or hook.

## Context Store

Whenever you make a decision, identify a task, note a next step, fact, or blocker during a conversation, call `graph_add_memory`.

**To add an entry:**
```
graph_add_memory(type="decision|task|next|fact|blocker", content="one sentence max 15 words", tags=["topic"], files=["relevant/file.ts"])
```

**Do NOT write context-store.json directly** — always use `graph_add_memory`. It applies pruning and keeps the store healthy.

**Rules:**
- Only log things worth remembering across sessions (not every minor detail)
- `content` must be under 15 words
- `files` lists the files this decision/task relates to (can be empty)
- Log immediately when the item arises — not at session end

## Session End

When the user signals they are done (e.g. "bye", "done", "wrap up", "end session"), proactively update `CONTEXT.md` in the project root with:
- **Current Task**: one sentence on what was being worked on
- **Key Decisions**: bullet list, max 3 items
- **Next Steps**: bullet list, max 3 items

Keep `CONTEXT.md` under 20 lines total. Do NOT summarize the full conversation — only what's needed to resume next session.
