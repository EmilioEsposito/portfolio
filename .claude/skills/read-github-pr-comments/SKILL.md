The Github CLI is a powerful tool for managing pull requests. Here are some commands you can use to list, view, and read the comments on a pull request. The CLI should already be installed and authenticated for you.

# LIST ALL OPEN PRs

```bash
gh pr list
```

# LIST OPEN PRs filtered by HEAD (source) branch

```bash
# Replace <branch> with the branch name
gh pr list --head <branch>
```

# LIST OPEN PRs filtered by BASE (target) branch

```bash
gh pr list --base <branch>
```

# VIEW SIMPLE PR for current branch
```bash
gh pr view
```

# VIEW REVIEW THREADS (inline comments) for PR #<number>

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