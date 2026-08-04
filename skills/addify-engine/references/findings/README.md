# findings/

Where this skill grows. When agents come back with something durable that is not already in
a reference file, condense it to one file here and add a pointer from `SKILL.md`.

Write a finding when the thing is **reusable and was expensive to learn**: a data source
that works (or is provably dead, with the response that proves it), a measured budget, a
failure mode and its signature, a technique that beat the obvious approach.

Do not write a finding for a one-off bug fix — that belongs in the git message.

## Format

```markdown
# <topic>
Verified: <date> · Method: <how you checked>

## What is true
<claims, each with a URL you actually fetched or a command you actually ran>

## What failed
<avenues tried and their actual responses - this is half the value>

## How to use it
<the command, the endpoint, the constant>

## Fragility
<what will break this and what the fallback is>
```

Every claim carries its evidence. A finding without a source URL or a command output is an
opinion, and opinions are what this directory exists to replace.
