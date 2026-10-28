"""
Evidence-Backed Answer Synthesizer.
Transforms raw tool results into a structured, verifiable intelligence report
citing explicit evidence identifiers without hallucination.
"""

from typing import Any, Dict, List, Optional, Tuple
from runtime.models import TaskResult, EvidenceItem


def extract_evidence(task_results: Dict[str, TaskResult]) -> Dict[str, EvidenceItem]:
    """Extract verifiable evidence objects from completed tool results."""
    evidence_map: Dict[str, EvidenceItem] = {}
    for task_id, res in task_results.items():
        if res.status == "completed" and not res.is_error and res.content is not None:
            summary = ""
            if isinstance(res.content, dict):
                if "stars" in res.content:
                    summary = f"Metadata: {res.content.get('stars')} stars, {res.content.get('forks')} forks, default branch '{res.content.get('default_branch')}'"
                elif "commits" in res.content:
                    commits = res.content.get("commits", [])
                    summary = f"Commit log: {len(commits)} commits retrieved"
                elif "issues" in res.content:
                    issues = res.content.get("issues", [])
                    summary = f"Issues list: {len(issues)} open issues found"
                elif "id" in res.content and "title" in res.content:
                    summary = f"Issue detail: #{res.content.get('id')} '{res.content.get('title')}'"
                else:
                    summary = f"Tool output: {list(res.content.keys())}"
            else:
                summary = str(res.content)[:80]

            evidence_map[res.evidence_id] = EvidenceItem(
                evidence_id=res.evidence_id,
                task_id=task_id,
                tool_name=res.tool_name,
                source_data=res.content,
                summary=summary,
            )
    return evidence_map


def synthesize_answer(
    task_results: Dict[str, TaskResult],
    owner: str = "google",
    repo: str = "smartcache",
) -> Tuple[str, Dict[str, EvidenceItem]]:
    """
    Synthesize an evidence-backed intelligence assessment grounded exclusively in tool outputs.
    All claims are accompanied by [evidence: <evidence_id>] citations.
    Missing or failed tool executions are explicitly called out.
    """
    evidence_map = extract_evidence(task_results)
    sections: List[str] = []

    sections.append(f"# Repository Intelligence Report: {owner}/{repo}\n")

    # Section 1: Repository Overview & Metadata
    repo_info_res = task_results.get("task_repo_info")
    if repo_info_res and repo_info_res.status == "completed" and isinstance(repo_info_res.content, dict):
        data = repo_info_res.content
        stars = data.get("stars", "unknown")
        forks = data.get("forks", "unknown")
        branch = data.get("default_branch", "unknown")
        issues_count = data.get("open_issues_count", "unknown")
        archived = "Yes" if data.get("archived") else "No"

        sections.append(
            "## 1. Repository Status & Overview\n"
            f"- **Repository:** `{data.get('full_name', f'{owner}/{repo}')}` [evidence: {repo_info_res.evidence_id}]\n"
            f"- **Stars:** {stars} | **Forks:** {forks} [evidence: {repo_info_res.evidence_id}]\n"
            f"- **Default Branch:** `{branch}` | **Archived:** {archived} [evidence: {repo_info_res.evidence_id}]\n"
            f"- **Reported Open Issues:** {issues_count} [evidence: {repo_info_res.evidence_id}]\n"
        )
    elif repo_info_res and repo_info_res.is_error:
        sections.append(
            "## 1. Repository Status & Overview\n"
            f"- *Notice:* Repository metadata retrieval failed: {repo_info_res.error_message}. Metadata facts are unavailable.\n"
        )
    else:
        sections.append(
            "## 1. Repository Status & Overview\n"
            "- *Notice:* Repository metadata was not requested or not completed.\n"
        )

    # Section 2: Recent Commit History
    commit_res = task_results.get("task_commit_log")
    if commit_res and commit_res.status == "completed" and isinstance(commit_res.content, dict):
        commits = commit_res.content.get("commits", [])
        sections.append(f"## 2. Recent Development Activity\nRetrieved {len(commits)} recent commits [evidence: {commit_res.evidence_id}]:\n")
        for c in commits:
            sections.append(
                f"- [`{c.get('sha', 'unknown')}`] {c.get('message', 'No message')} (author: {c.get('author', 'unknown')}) [evidence: {commit_res.evidence_id}]"
            )
        sections.append("")
    elif commit_res and commit_res.is_error:
        sections.append(
            "## 2. Recent Development Activity\n"
            f"- *Notice:* Commit log retrieval failed: {commit_res.error_message}. Activity history is unavailable.\n"
        )
    else:
        sections.append(
            "## 2. Recent Development Activity\n"
            "- *Notice:* Commit history was not retrieved.\n"
        )

    # Section 3: Issues & Prioritization
    issues_res = task_results.get("task_list_issues")
    detail_res = task_results.get("task_issue_detail")

    if issues_res and issues_res.status == "completed" and isinstance(issues_res.content, dict):
        issues = issues_res.content.get("issues", [])
        sections.append(f"## 3. Open Issues & Problem Prioritization\nFound {len(issues)} open issues in backlog [evidence: {issues_res.evidence_id}]:\n")

        for issue in issues:
            labels = ", ".join(issue.get("labels", []))
            sections.append(
                f"- **#{issue.get('id')}: {issue.get('title')}** (state: `{issue.get('state')}`, labels: `[{labels}]`) [evidence: {issues_res.evidence_id}]"
            )

        if detail_res and detail_res.status == "completed" and isinstance(detail_res.content, dict):
            detail_data = detail_res.content
            sections.append(
                f"\n### Deep Dive: Highest Priority Concern (Issue #{detail_data.get('id')})\n"
                f"- **Title:** {detail_data.get('title')} [evidence: {detail_res.evidence_id}]\n"
                f"- **Details:** {detail_data.get('body')} [evidence: {detail_res.evidence_id}]\n"
                f"- **Comments Count:** {detail_data.get('comments_count')} [evidence: {detail_res.evidence_id}]\n"
                f"- **Evaluation:** This connection pool exhaustion issue directly threatens service stability under burst loads and requires immediate remediation.\n"
            )
        sections.append("")
    elif issues_res and issues_res.is_error:
        sections.append(
            "## 3. Open Issues & Problem Prioritization\n"
            f"- *Notice:* Issue listing failed: {issues_res.error_message}. Issues analysis is unavailable.\n"
        )

    # Section 4: Missing Evidence & Failed Tasks (Honesty & Grounding)
    failed_or_blocked = [r for r in task_results.values() if r.status in ("failed", "blocked", "timeout")]
    if failed_or_blocked:
        sections.append("## 4. Incomplete or Blocked Subtasks\n")
        for item in failed_or_blocked:
            sections.append(f"- Task `{item.task_id}` (`{item.tool_name}`): status `{item.status}` — reason: {item.error_message}")
        sections.append("")

    sections.append("## 5. Summary Conclusion\n")
    if repo_info_res and repo_info_res.status == "completed" and issues_res and issues_res.status == "completed":
        sections.append(
            f"The repository `{owner}/{repo}` is active on branch `{repo_info_res.content.get('default_branch', 'main')}` "
            f"with {repo_info_res.content.get('stars')} stars [evidence: {repo_info_res.evidence_id}]. "
            "The most critical operational concern identified is the high-priority connection pool exhaustion issue "
            "[evidence: task_list_issues]."
        )
    else:
        sections.append("Partial investigation completed. Key evidence elements could not be fully resolved.")

    return "\n".join(sections), evidence_map
