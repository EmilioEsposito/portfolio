---
name: github-cli
description: Use the GitHub CLI (gh) for PRs, issues, reviews, and repo operations. Authenticated via GH_TOKEN env var or local gh auth.
---

The GitHub CLI (`gh`) is available and authenticated. Use it for GitHub operations.

**Auth**: Works via `GH_TOKEN` env var (Claude Code Web) or local `gh auth login` (local CLI).

# PULL REQUESTS

## List open PRs
```bash
gh pr list
```

## List PRs by branch
```bash
gh pr list --head <branch>   # by source branch
gh pr list --base <branch>   # by target branch
```

## View PR details
```bash
gh pr view              # current branch
gh pr view <number>     # specific PR
gh pr view <number> --comments  # with comments
```

## View review threads (inline comments)
```bash
gh api graphql \
  -F owner='EmilioEsposito' -F name='portfolio' -F number=<number> \
  -f query='
    query($owner:String!,$name:String!,$number:Int!){
      repository(owner:$owner,name:$name){
        pullRequest(number:$number){
          reviewThreads(first:100){
            nodes{
              path
              line
              isResolved
              comments(first:50){
                nodes{
                  author{login}
                  createdAt
                  body
                }
              }
            }
          }
        }
      }
    }' \
  --jq '
    .data.repository.pullRequest.reviewThreads.nodes[]
    | "FILE: " + (.path // "unknown")
      + "  LINE: " + ( (.line|tostring) // "unknown" )
      + "  RESOLVED: " + (.isResolved|tostring)
      + "\n"
      + ( .comments.nodes[]
          | "  ["+.author.login+"] " + .body )
      + "\n"
  '
```

## Check PR CI status
```bash
gh pr checks <number>
```

# ISSUES

## List issues
```bash
gh issue list
gh issue list --label "bug"
gh issue list --assignee @me
```

## View issue
```bash
gh issue view <number>
```

# REPO & MISC

## View recent releases
```bash
gh release list
```

## View repo info
```bash
gh repo view
```

## Search code
```bash
gh search code "pattern" --repo EmilioEsposito/portfolio
```

## API calls
```bash
gh api repos/EmilioEsposito/portfolio/pulls/<number>/comments
```
