# GitHub Integration

Manage your GitHub repositories directly from Discord. Create issues, review
pull requests, check workflow status, and manage labels -- all through natural
language.

---

## Setting Up

### Prerequisites

You need a GitHub personal access token (PAT) with access to the repositories
you want to manage. Create one in your
[GitHub Developer Settings](https://github.com/settings/tokens).

Add the following to your `.env` file:

```env
GITHUB_TOKEN=ghp_your_token_here
```

Optionally, set a default repository so you do not have to specify it in every
command:

```env
GITHUB_DEFAULT_REPO=owner/repo
```

### Required Token Scopes

Your PAT needs the following scopes depending on what you want to do:

| Scope | Required For |
|---|---|
| `repo` | Issues, PRs, repository info |
| `workflow` | Listing and re-running CI/CD workflows |

### Verifying Connection

The bot verifies your token on startup and logs your authenticated GitHub
username. If the token is invalid or missing required scopes, the bot will
report the error in the startup logs.

---

## Managing Issues

### List Issues

Ask the bot to show issues from a repository. If you have set a default
repository, you do not need to specify one.

```
@Zetherion AI list open issues
@Zetherion AI show issues in owner/repo
@Zetherion AI list issues with label "bug"
```

### View an Issue

Reference an issue by number to see its details, including title, body, labels,
assignees, and status.

```
@Zetherion AI show issue #42
```

### Create an Issue

Describe the issue you want to create. The bot will parse a title from your
message.

```
@Zetherion AI create issue: Fix login page redirect
```

By default, this requires confirmation before the bot executes the action. See
the [Autonomy Levels](#autonomy-levels) section for details on changing this
behavior.

### Close and Reopen Issues

```
@Zetherion AI close issue #42
@Zetherion AI reopen issue #42
```

Both actions require confirmation by default.

### Labels

Add or remove labels from issues. Label operations run autonomously by default
and do not require confirmation.

```
@Zetherion AI add label "bug" to issue #42
@Zetherion AI remove label "wontfix" from issue #42
```

### Comments

Add a comment to any issue by referencing its number.

```
@Zetherion AI add comment to #42: Looks good, merging soon
```

Comment operations run autonomously by default.

---

## Pull Requests

### List PRs

View open pull requests in your repository.

```
@Zetherion AI list pull requests
@Zetherion AI show open PRs
```

### View a PR

See the details of a specific pull request, including its description, review
status, and CI checks.

```
@Zetherion AI show PR #10
```

### View a PR Diff

Review the code changes in a pull request without leaving Discord.

```
@Zetherion AI show PR diff #10
```

### Merge a PR

Merge a pull request into its target branch.

```
@Zetherion AI merge PR #10
```

Merging always requires confirmation. This is a safety measure that cannot be
overridden through autonomy settings.

---

## Workflows (CI/CD)

### Check Status

View the status of your repository's GitHub Actions workflows.

```
@Zetherion AI list workflows
@Zetherion AI show CI status
```

### Re-run a Workflow

Trigger a re-run of a specific workflow by its run ID.

```
@Zetherion AI rerun workflow 12345
```

---

## Repository Info

Get a summary of your repository's key details.

```
@Zetherion AI repo info
```

This returns the repository description, default branch, open issues count,
star count, fork count, and whether the repository is private.

---

## Autonomy Levels

The GitHub integration uses a tiered autonomy system to control which actions
the bot can perform immediately and which require your explicit confirmation.

| Level | Behavior | Default For |
|---|---|---|
| Autonomous | Executes immediately without asking. | Labels, comments, listing, repo info |
| Ask | Asks for confirmation before executing. | Create, close, and reopen issues |
| Always Ask | Requires confirmation. Cannot be overridden. | Merge PRs |

### View Current Settings

Check which autonomy level is assigned to each action.

```
@Zetherion AI show autonomy settings
@Zetherion AI get autonomy
```

### Change Settings

Adjust the autonomy level for any action that is not locked to "Always Ask".

```
@Zetherion AI set autonomy for create_issue to autonomous
```

You can set an action to `autonomous`, `ask`, or `always_ask`. Actions locked
to "Always Ask" (such as merging PRs) cannot be changed.

### Confirming Actions

When the bot asks for confirmation, it displays the details of what it is about
to do along with an action ID. Follow the instructions in the confirmation
message to approve or cancel the action.

---

## Supported Intents

The GitHub skill recognizes the following intents:

| Intent | Description |
|---|---|
| `list_issues` | List issues, optionally filtered by state or label. |
| `get_issue` | View details of a specific issue. |
| `create_issue` | Create a new issue. |
| `update_issue` | Update an existing issue's title or body. |
| `close_issue` | Close an open issue. |
| `reopen_issue` | Reopen a closed issue. |
| `add_label` | Add a label to an issue. |
| `remove_label` | Remove a label from an issue. |
| `add_comment` | Add a comment to an issue. |
| `list_prs` | List pull requests. |
| `get_pr` | View details of a specific pull request. |
| `get_pr_diff` | View the diff of a pull request. |
| `merge_pr` | Merge a pull request. |
| `list_workflows` | List GitHub Actions workflows and their status. |
| `rerun_workflow` | Re-run a specific workflow. |
| `get_repo_info` | View repository metadata. |
| `set_autonomy` | Change the autonomy level for an action. |
| `get_autonomy` | View current autonomy settings. |

---

## Troubleshooting

### "GitHub client not initialized"

The bot could not create a GitHub client on startup. Check that `GITHUB_TOKEN`
is set in your `.env` file and that the value is a valid personal access token.

### "No repository specified"

You issued a command without specifying a repository, and no default is
configured. Either set `GITHUB_DEFAULT_REPO` in your `.env` file or include the
repository in your command (e.g., `list issues in owner/repo`).

### "GitHub authentication failed"

Your token may be expired, revoked, or missing the required scopes. Generate a
new token with the `repo` and `workflow` scopes and update your `.env` file.

### Actions not executing

If the bot responds with a confirmation prompt but you expected it to run
immediately, check the autonomy settings. The action may be configured to
require confirmation. Use `show autonomy settings` to review.

---

## Related Guides

- [Getting Started](getting-started.md) -- installation and initial setup.
- [Commands](commands.md) -- full list of available commands.
- [Configuration](../technical/configuration.md) -- environment variables and
  advanced settings.
- [Security](../technical/security.md) -- token storage and access control.
