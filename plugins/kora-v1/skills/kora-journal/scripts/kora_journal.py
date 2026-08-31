#!/usr/bin/env python3
"""
Kora Journal — Continuous improvement journal for Kora skills.

Storage: ~/.kora-journal/<project>/<module>/<YYYY-MM-DD>_<slug>.md

Each entry is a separate file for easy management, export, and integration.

Usage:
    python kora_journal.py add "Title" --files file1.md --context "..." --problem "..." --solution "..."
    python kora_journal.py list --limit 10
    python kora_journal.py export --since 2026-05-01
    python kora_journal.py status
    python kora_journal.py integrate <entry-file.md>
"""

import argparse
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Emit UTF-8 regardless of the platform console codepage (Windows defaults to
# cp1252, which cannot encode the checkmark/arrow/emoji this CLI prints and would
# otherwise crash every command). Safe no-op where the stream can't be reconfigured.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def get_project_name():
    """
    Get project name from current directory or git remote.
    
    Priority:
    1. Git remote name (e.g., 'kora-skills' from 'github:user/kora-skills.git')
    2. Parent directory name
    3. Current directory name
    """
    current = Path.cwd()
    project_name = current.name
    
    # Try to get from git remote
    try:
        import subprocess
        result = subprocess.run(
            ['git', 'config', '--get', 'remote.origin.url'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            match = re.search(r'/([^/]+?)(?:\.git)?$', url)
            if match:
                project_name = match.group(1)
    except Exception:
        pass
    
    # Sanitize
    project_name = re.sub(r'[^a-zA-Z0-9]+', '-', project_name).strip('-').lower()
    return project_name or 'kora-project'


def get_gradle_module():
    """
    Get Gradle module name from current directory.
    
    Priority:
    1. settings.gradle.kts / settings.gradle rootProject.name
    2. Current directory name
    """
    current = Path.cwd()
    
    for settings_file in ['settings.gradle.kts', 'settings.gradle']:
        settings_path = current / settings_file
        if settings_path.exists():
            content = settings_path.read_text()
            match = re.search(r'rootProject\.name\s*=\s*["\']([^"\']+)["\']', content)
            if match:
                return match.group(1)
    
    module_name = current.name
    module_name = re.sub(r'[^a-zA-Z0-9]+', '-', module_name).strip('-').lower()
    return module_name or 'default'


def get_journal_dir():
    """
    Get journal directory: ~/.kora-journal/<project>/<module>/
    
    Each entry is stored as a separate file: YYYY-MM-DD_slug.md
    """
    home = Path.home()
    project = get_project_name()
    module = get_gradle_module()
    
    journal_dir = home / '.kora-journal' / project / module
    journal_dir.mkdir(parents=True, exist_ok=True)
    
    return journal_dir


def slugify(title):
    """Convert title to URL-safe slug."""
    slug = title.lower()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    # A title made only of punctuation would otherwise yield "<date>_.md"
    return slug[:50] or 'entry'


def get_entry_filename(slug, date, counter=None):
    """Generate filename for entry: YYYY-MM-DD_slug.md, _slug_N.md on collision"""
    suffix = f"_{counter}" if counter else ""
    return f"{date}_{slug}{suffix}.md"


def yaml_quote(value):
    """
    Quote a scalar for the entry frontmatter.

    Titles are free-form user text, so a bare quote or newline would truncate
    the frontmatter and every later `title:` read would return a partial value.
    """
    flat = ' '.join(str(value).split())
    return '"' + flat.replace('\\', '\\\\').replace('"', '\\"') + '"'


def yaml_unquote(value):
    """Reverse yaml_quote — drop the backslash from any escaped character."""
    return re.sub(r'\\(.)', r'\1', value)


def normalize_tag(tag):
    """Tags are stored comma-separated inside [ ], so keep them to one token."""
    return re.sub(r'[^\w.-]+', '-', str(tag).strip().lower()).strip('-')


# One place for every frontmatter/metadata read, so the commands cannot drift
# apart. The title pattern has to tolerate the \" escapes yaml_quote writes.
TITLE_RE = re.compile(r'^title:\s*"((?:[^"\\]|\\.)*)"', re.MULTILINE)
DATE_RE = re.compile(r'^date:\s*(\d{4}-\d{2}-\d{2})', re.MULTILINE)
TAGS_RE = re.compile(r'^tags:\s*\[([^\]]*)\]', re.MULTILINE)
STATUS_RE = re.compile(r'(Status:\**[ \t]*)(\w+)')


def read_title(content, default=''):
    match = TITLE_RE.search(content)
    return yaml_unquote(match.group(1)) if match else default


def read_date(content, default='unknown'):
    match = DATE_RE.search(content)
    return match.group(1) if match else default


def read_status(content, default='pending'):
    match = STATUS_RE.search(content)
    return match.group(2) if match else default


def read_tags(content):
    match = TAGS_RE.search(content)
    if not match:
        return []
    return [yaml_unquote(t.strip().strip('"').strip("'"))
            for t in match.group(1).split(',') if t.strip()]


def add_entry(title, context, problem, solution, files, author, tags=None):
    """Add a new entry as a separate file."""
    journal_dir = get_journal_dir()
    date = datetime.now().strftime('%Y-%m-%d')
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    
    slug = slugify(title)
    entry_path = journal_dir / get_entry_filename(slug, date)
    
    # Handle duplicate filenames (same title on same day)
    counter = 0
    while entry_path.exists():
        counter += 1
        entry_path = journal_dir / get_entry_filename(slug, date, counter)
    
    project = get_project_name()
    module = get_gradle_module()
    
    # Auto-generate tags from title and problem if not provided
    if not tags:
        tags = []
        # Extract keywords from title
        title_words = re.findall(r'\b[a-zA-Z]{4,}\b', title.lower())
        tags.extend([w for w in title_words if w not in ['fixed', 'added', 'updated', 'changed', 'with', 'from', 'that', 'this', 'what', 'when', 'were']])
        # Extract Kora-specific tags from problem/solution
        kora_tags = {
            'http': ['http', 'controller', 'route', 'interceptor', 'request', 'response'],
            'client': ['client', 'httpclient'],
            'database': ['database', 'jdbc', 'repository', 'query', 'sql'],
            'di': ['component', 'module', 'injection', 'dependency', 'graph'],
            'aop': ['cache', 'cacheable', 'retry', 'circuit', 'schedule', 'log', 'valid'],
            'config': ['config', 'hocon', 'yaml', 'source'],
            'openapi': ['openapi', 'delegate', 'controller', 'model', 'schema'],
            'kafka': ['kafka', 'listener', 'publisher', 'consumer', 'producer'],
            'grpc': ['grpc', 'server', 'client', 'stub'],
            'auth': ['auth', 'principal', 'token', 'bearer', 'apikey'],
            'test': ['test', 'koraapptest', 'testcontainers'],
            'json': ['json', 'dto', 'serialization'],
        }
        problem_lower = (problem + ' ' + solution).lower()
        for tag, keywords in kora_tags.items():
            if any(kw in problem_lower for kw in keywords):
                tags.append(tag)
        tags = list(set(tags))[:10]  # Limit to 10 tags
    
    tags = list(dict.fromkeys(t for t in map(normalize_tag, tags) if t))
    tags_yaml = ', '.join(yaml_quote(t) for t in tags)
    
    content = f"""---
title: {yaml_quote(title)}
date: {date}
project: {project}
module: {module}
author: {author}
tags: [{tags_yaml}]
---

# {title}

**Date:** {date}  
**Project:** {project}  
**Module:** {module}  
**Author:** {author}

---

## Context

{context}

## Problem

{problem}

## Solution

{solution}

## Files Affected

"""
    
    for f in files:
        content += f"- `{f}`\n"
    
    content += f"""
---

## Metadata

- **Created:** {timestamp}
- **Status:** pending  # pending → integrated → archived
- **Integrated:** 

"""
    
    entry_path.write_text(content, encoding='utf-8')
    print(f"✓ Entry added: {entry_path}")


def list_entries(limit=10, status=None):
    """Print the most recent entries."""
    journal_dir = get_journal_dir()
    
    if not journal_dir.exists():
        print("Journal not found. Add the first entry.")
        return
    
    # Find all entry files (newest first)
    entries = sorted(journal_dir.glob('*.md'), reverse=True)

    if not entries:
        print("No entries yet.")
        return

    # Parse and apply the status filter BEFORE limiting, so the count, numbering,
    # and the limit all reflect the entries actually shown.
    rows = []
    for entry_path in entries:
        content = entry_path.read_text(encoding='utf-8')
        entry_status = read_status(content)

        if status and entry_status != status:
            continue

        rows.append((
            entry_path,
            read_title(content, entry_path.stem),
            read_date(content),
            entry_status,
        ))

    if not rows:
        print(f"\nNo entries with status '{status}'.\n" if status else "\nNo entries yet.\n")
        return

    shown = rows[:limit]
    scope = f" [{status}]" if status else ""
    print(f"\nLast {len(shown)} of {len(rows)}{scope} entries:\n")

    for i, (entry_path, title, date, entry_status) in enumerate(shown, 1):
        print(f"{i}. [{entry_status}] {date} — {title}")
        print(f"   File: {entry_path.name}")


def export_entries(since_date, status=None):
    """Export entries from the specified date."""
    journal_dir = get_journal_dir()
    
    if not journal_dir.exists():
        print("Journal not found.")
        return
    
    entries = sorted(journal_dir.glob('*.md'), reverse=True)
    exported = []
    
    for entry_path in entries:
        content = entry_path.read_text(encoding='utf-8')
        
        entry_date = read_date(content, default='')
        if not entry_date or entry_date < since_date:
            continue
        
        # Filter by status if specified
        if status and read_status(content) != status:
            continue
        
        exported.append((entry_path, content))
    
    if exported:
        print(f"\nExporting {len(exported)} entries since {since_date}:\n")
        print('=' * 80)
        
        for entry_path, content in exported:
            print(f"\n## File: {entry_path.name}\n")
            # Print content without frontmatter
            body = re.sub(r'^---\n.*?\n---\n\n', '', content, flags=re.DOTALL)
            print(body)
            print('=' * 80)
        
        print(f"\nTotal: {len(exported)} entries")
    else:
        print(f"No entries found since {since_date}.")


def integrate_entry(entry_file, new_status='integrated'):
    """Mark an entry as integrated."""
    entry_path = Path(entry_file)
    
    if not entry_path.exists():
        # Try in journal dir
        journal_dir = get_journal_dir()
        entry_path = journal_dir / entry_file
        
        if not entry_path.exists():
            print(f"Entry not found: {entry_file}")
            return
    
    content = entry_path.read_text(encoding='utf-8')
    old_status = read_status(content)
    
    if old_status == new_status:
        print(f"Entry is already {new_status}: {entry_path.name}")
        return
    
    # Match whatever status is written, not just "pending", so that the
    # integrated -> archived transition works too.
    content, replaced = STATUS_RE.subn(
        lambda m: m.group(1) + new_status,
        content,
        count=1
    )
    if not replaced:
        print(f"No Status field found, entry left unchanged: {entry_path.name}")
        return
    
    # "Integrated" records when the entry reached the skills; archiving later
    # must not overwrite that date.
    if new_status == 'integrated':
        today = datetime.now().strftime('%Y-%m-%d')
        content = re.sub(
            r'(\*\*Integrated:\*\*)[^\n]*',
            rf'\g<1> {today}',
            content
        )
    
    entry_path.write_text(content, encoding='utf-8')
    print(f"✓ Entry marked as {new_status} (was {old_status}): {entry_path.name}")


def search_entries(query, limit=10, status=None, by_tags=False):
    """Search entries by keywords in title, context, problem, solution, or tags."""
    journal_dir = get_journal_dir()
    
    if not journal_dir.exists():
        print("Journal not found.")
        return
    
    entries = list(journal_dir.glob('*.md'))
    keywords = query.lower().split()
    results = []
    
    for entry_path in entries:
        content = entry_path.read_text(encoding='utf-8')
        content_lower = content.lower()
        
        entry_tags = read_tags(content)
        
        # Search by tags if --by-tags flag is set
        if by_tags:
            # Match if any keyword matches any tag
            if not any(kw in entry_tags for kw in keywords):
                continue
        else:
            # Check if all keywords match in content (title, context, problem, solution, tags)
            if not all(kw in content_lower for kw in keywords):
                continue

        # Filter by status if specified
        if status and status != 'all' and read_status(content) != status:
            continue

        # Calculate relevance
        if by_tags:
            relevance = sum(1 for kw in keywords if kw in entry_tags)
        else:
            relevance = sum(1 for kw in keywords if kw in content_lower)
            # Boost relevance if keywords match tags
            relevance += sum(1 for kw in keywords if kw in entry_tags) * 2

        results.append({
            'path': entry_path,
            'title': read_title(content, entry_path.stem),
            'date': read_date(content),
            'relevance': relevance,
            'tags': entry_tags
        })
    
    # Sort by relevance (keyword matches) and date
    results.sort(key=lambda x: (x['relevance'], x['date']), reverse=True)
    
    if results:
        print(f"\nFound {len(results)} entries matching '{query}':\n")
        for i, result in enumerate(results[:limit], 1):
            tags_str = ', '.join(result['tags'][:5]) if result['tags'] else 'no tags'
            print(f"{i}. [{result['date']}] {result['title']}")
            print(f"   File: {result['path'].name}")
            print(f"   Tags: {tags_str}")
            print(f"   Relevance: {result['relevance']} points")
            print()
        
        if len(results) > limit:
            print(f"... and {len(results) - limit} more. Use --limit to see all.")
    else:
        print(f"No entries found matching '{query}'.")


def status():
    """Show journal status."""
    journal_dir = get_journal_dir()
    project = get_project_name()
    module = get_gradle_module()
    
    print("\n📊 Kora Journal Status\n")
    print(f"Project: {project}")
    print(f"Module:  {module}")
    print(f"Journal: {journal_dir}")
    
    if journal_dir.exists():
        entries = list(journal_dir.glob('*.md'))
        
        # Count by status
        status_counts = {'pending': 0, 'integrated': 0, 'archived': 0}
        for entry in entries:
            entry_status = read_status(entry.read_text(encoding='utf-8'))
            status_counts[entry_status] = status_counts.get(entry_status, 0) + 1
        
        print(f"Total entries: {len(entries)}")
        print(f"  - Pending:    {status_counts.get('pending', 0)}")
        print(f"  - Integrated: {status_counts.get('integrated', 0)}")
        print(f"  - Archived:   {status_counts.get('archived', 0)}")
        
        # Recent entries
        if entries:
            print("\nRecent entries:")
            for entry in sorted(entries, reverse=True)[:5]:
                title = read_title(entry.read_text(encoding='utf-8'), entry.stem)
                print(f"  - {entry.name}: {title}")
    else:
        print("Status: No journal yet (add first entry)")


def main():
    parser = argparse.ArgumentParser(description='Kora Journal — continuous improvement for Kora skills')
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    
    # add
    add_parser = subparsers.add_parser('add', help='Add journal entry as separate file')
    add_parser.add_argument('title', help='Entry title')
    add_parser.add_argument('--context', required=True, help='What was being done')
    add_parser.add_argument('--problem', required=True, help='What went wrong')
    add_parser.add_argument('--solution', required=True, help='How it was fixed')
    add_parser.add_argument('--files', nargs='+', required=True, help='Affected files')
    add_parser.add_argument('--author', default=os.getenv('USER', 'anonymous'), help='Author')
    add_parser.add_argument('--tags', nargs='+', help='Keywords/tags for search (auto-generated if not provided)')
    
    # list
    list_parser = subparsers.add_parser('list', help='List entries')
    list_parser.add_argument('--limit', type=int, default=10, help='Max entries')
    list_parser.add_argument('--status', choices=['pending', 'integrated', 'archived'], help='Filter by status')
    
    # export
    export_parser = subparsers.add_parser('export', help='Export entries')
    export_parser.add_argument('--since', required=True, help='Date YYYY-MM-DD')
    export_parser.add_argument('--status', choices=['pending', 'integrated', 'archived'], default='pending', help='Filter by status')
    
    # integrate
    integrate_parser = subparsers.add_parser('integrate', help='Mark entry as integrated')
    integrate_parser.add_argument('entry', help='Entry filename or path')
    integrate_parser.add_argument('--status', default='integrated', choices=['integrated', 'archived'], help='New status')
    
    # search
    search_parser = subparsers.add_parser('search', help='Search entries by keywords or tags')
    search_parser.add_argument('query', help='Search query (keywords or tags)')
    search_parser.add_argument('--limit', type=int, default=10, help='Max results')
    search_parser.add_argument('--status', choices=['pending', 'integrated', 'archived', 'all'], default='all', help='Filter by status')
    search_parser.add_argument('--by-tags', action='store_true', help='Search only in tags (not in content)')
    
    # status
    subparsers.add_parser('status', help='Show status')
    
    args = parser.parse_args()
    
    if args.command == 'add':
        add_entry(args.title, args.context, args.problem, args.solution, args.files, args.author, args.tags)
    elif args.command == 'list':
        list_entries(args.limit, args.status)
    elif args.command == 'export':
        export_entries(args.since, args.status)
    elif args.command == 'integrate':
        integrate_entry(args.entry, args.status)
    elif args.command == 'search':
        search_entries(args.query, args.limit, args.status if args.status != 'all' else None, args.by_tags)
    elif args.command == 'status':
        status()
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
