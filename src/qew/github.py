"""Narrow GitHub CLI boundary for work-graph queries."""

import json
import os
import re
import subprocess
from functools import lru_cache
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from qew.errors import QewError
from qew.config import load_config
from qew.model import APPROVAL_LABEL, AUTHOR_LABEL_PREFIX, normalize_labels
from qew.moments import format_instant, parse_github_time
from qew.vcs import workspace_git_dir


DEFAULT_GH_TIMEOUT_SECONDS = 120
_REPOSITORY_PART = re.compile(r"[A-Za-z0-9._-]+")
_LOGIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
AUTHOR_LABEL_COLOR = "EDEDED"
LABEL_NAME_LIMIT = 50

VIEWER_QUERY = "query { viewer { login } }"

LABEL_LOOKUP_QUERY = """
query($owner: String!, $name: String!, $label: String!) {
  repository(owner: $owner, name: $name) {
    label(name: $label) { name }
  }
}
""".strip()

READY_QUERY = """
query($owner: String!, $name: String!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    issues(first: 100, after: $endCursor, states: OPEN, labels: ["qew:approved"]) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        url
        assignees(first: 1) { totalCount }
        labels(first: 100) {
          nodes { name }
          pageInfo { hasNextPage endCursor }
        }
        blockedBy(first: 100) {
          nodes { number state stateReason }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
  }
}
""".strip()

PRIME_QUERY = """
query($owner: String!, $name: String!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    issues(first: 100, after: $endCursor, states: OPEN, labels: ["qew:approved"]) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title state
        assignees(first: 100) {
          nodes { login }
          pageInfo { hasNextPage endCursor }
        }
        labels(first: 100) {
          nodes { name }
          pageInfo { hasNextPage endCursor }
        }
        blockedBy(first: 100) {
          nodes { number state stateReason }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
  }
}
""".strip()

PLAN_STATUS_QUERY = """
query($owner: String!, $name: String!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    issues(first: 20, after: $endCursor, states: OPEN, labels: ["epic"]) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title state url
        labels(first: 20) {
          nodes { name }
          pageInfo { hasNextPage endCursor }
        }
        subIssues(first: 25) {
          nodes {
            number title state url
            assignees(first: 1) { totalCount }
            labels(first: 20) {
              nodes { name }
              pageInfo { hasNextPage endCursor }
            }
            blockedBy(first: 20) {
              nodes { number state stateReason }
              pageInfo { hasNextPage endCursor }
            }
          }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
  }
}
""".strip()

PLAN_CHILDREN_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      subIssues(first: 25, after: $endCursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          number title state url
          assignees(first: 1) { totalCount }
          labels(first: 20) {
            nodes { name }
            pageInfo { hasNextPage endCursor }
          }
          blockedBy(first: 20) {
            nodes { number state stateReason }
            pageInfo { hasNextPage endCursor }
          }
        }
      }
    }
  }
}
""".strip()

ISSUE_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  viewer { login }
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      number
      title
      state
      stateReason
      parent { number }
      assignees(first: 100) {
        nodes { login }
        pageInfo { hasNextPage endCursor }
      }
      labels(first: 100) {
        nodes { name }
        pageInfo { hasNextPage endCursor }
      }
      blockedBy(first: 100) {
        nodes { number state stateReason }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
""".strip()

PULL_REQUEST_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      number
      state
      baseRefName
      mergeCommit { oid }
      closingIssuesReferences(first: 100) {
        nodes { number }
        pageInfo { hasNextPage }
      }
    }
  }
}
""".strip()

PULL_REQUEST_STATE_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  viewer { login }
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      number state isDraft body
      author { login }
      labels(first: 50) { nodes { name } pageInfo { hasNextPage } }
      closingIssuesReferences(first: 50) {
        nodes { number assignees(first: 20) { totalCount nodes { login } } }
        pageInfo { hasNextPage }
      }
    }
  }
}
""".strip()

PULL_REQUEST_MERGE_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  viewer { login }
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      number state isDraft body mergeable headRefOid baseRefName
      author { login }
      labels(first: 50) { nodes { name } pageInfo { hasNextPage } }
      autoMergeRequest { enabledAt }
      baseRef { branchProtectionRule { requiresStatusChecks } }
      commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
      closingIssuesReferences(first: 50) {
        nodes {
          number
          assignees(first: 20) { totalCount nodes { login } }
          labels(first: 100) {
            nodes { name }
            pageInfo { hasNextPage endCursor }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
""".strip()

EPIC_CLAIM_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      number state
      labels(first: 100) { nodes { name } pageInfo { hasNextPage } }
      assignees(first: 100) { nodes { login } pageInfo { hasNextPage } }
      subIssues(first: 100) {
        nodes {
          number state
          labels(first: 100) { nodes { name } pageInfo { hasNextPage } }
          assignees(first: 100) { nodes { login } pageInfo { hasNextPage } }
        }
        pageInfo { hasNextPage }
      }
    }
  }
}
""".strip()

GRAPH_QUERY = """
query($owner: String!, $name: String!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    issues(first: 100, after: $endCursor, states: [OPEN, CLOSED]) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        state
        parent { number }
        assignees(first: 100) {
          nodes { login }
          totalCount
          pageInfo { hasNextPage endCursor }
        }
        blockedBy(first: 100) {
          nodes { number title state }
          pageInfo { hasNextPage endCursor }
        }
        labels(first: 100) {
          nodes { name }
          pageInfo { hasNextPage endCursor }
        }
        blockedBy(first: 100) {
          nodes { number }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
  }
}
""".strip()

BLOCKERS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      blockedBy(first: 100, after: $endCursor) {
        nodes { number state stateReason }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
""".strip()

ASSIGNEES_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      assignees(first: 100, after: $endCursor) {
        nodes { login }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
""".strip()

LABELS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      labels(first: 100, after: $endCursor) {
        nodes { name }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
""".strip()

VETTED_LIST_FIELDS = """
        number
        title
        state
        url
        parent { number }
        assignees(first: 100) {
          nodes { login }
          totalCount
          pageInfo { hasNextPage endCursor }
        }
        blockedBy(first: 100) {
          nodes { number state }
          pageInfo { hasNextPage endCursor }
        }
        labels(first: 100) {
          nodes { name }
          pageInfo { hasNextPage endCursor }
        }
""".strip()

VETTED_LIST_QUERY = f"""
query($owner: String!, $name: String!, $endCursor: String) {{
  repository(owner: $owner, name: $name) {{
    issues(first: 100, after: $endCursor, states: [OPEN, CLOSED], labels: ["qew:approved"]) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{
        {VETTED_LIST_FIELDS}
      }}
    }}
  }}
}}
""".strip()

VETTED_LIST_WITH_BODIES_QUERY = f"""
query($owner: String!, $name: String!, $endCursor: String) {{
  repository(owner: $owner, name: $name) {{
    issues(first: 100, after: $endCursor, states: [OPEN, CLOSED], labels: ["qew:approved"]) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{
        {VETTED_LIST_FIELDS}
        body
      }}
    }}
  }}
}}
""".strip()

DRAFT_LIST_QUERY = f"""
query($owner: String!, $name: String!, $endCursor: String) {{
  repository(owner: $owner, name: $name) {{
    issues(first: 100, after: $endCursor, states: [OPEN], labels: ["qew:draft"]) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{
        {VETTED_LIST_FIELDS}
      }}
    }}
  }}
}}
""".strip()

DRAFT_NODE_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      number state
      labels(first: 100) {
        nodes { name }
        pageInfo { hasNextPage endCursor }
      }
      assignees(first: 10) { totalCount nodes { login } }
      closedByPullRequestsReferences(first: 50) {
        nodes { number state isDraft labels(first: 50) { nodes { name } } }
        pageInfo { hasNextPage endCursor }
      }
      subIssues(first: 100) {
        nodes { number state }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
""".strip()

TRIAGE_QUERY = """
query($owner: String!, $name: String!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    issues(first: 100, after: $endCursor, states: [OPEN, CLOSED]) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        body
        state
        url
        labels(first: 100) {
          nodes { name }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
  }
}
""".strip()

BLOCKED_QUERY = """
query($owner: String!, $name: String!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    issues(first: 100, after: $endCursor, states: OPEN, labels: ["qew:approved"]) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        state
        url
        labels(first: 100) {
          nodes { name }
          pageInfo { hasNextPage endCursor }
        }
        blockedBy(first: 100) {
          nodes { number title state }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
  }
}
""".strip()

SEARCH_QUERY = """
query($searchQuery: String!, $endCursor: String) {
  search(first: 100, after: $endCursor, type: ISSUE, query: $searchQuery) {
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on Issue {
        number
        title
        state
        url
        labels(first: 100) {
          nodes { name }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
  }
}
""".strip()

SHOW_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      number
      title
      body
      state
      url
      parent { number title state }
      assignees(first: 100) {
        nodes { login }
        pageInfo { hasNextPage endCursor }
      }
      labels(first: 100) {
        nodes { name }
        pageInfo { hasNextPage endCursor }
      }
      blockedBy(first: 100) {
        nodes { number title state }
        pageInfo { hasNextPage endCursor }
      }
      blocking(first: 100) {
        nodes { number title state }
        pageInfo { hasNextPage endCursor }
      }
      subIssues(first: 100) {
        nodes {
          number title state url
          assignees(first: 1) { totalCount }
          labels(first: 100) {
            nodes { name }
            pageInfo { hasNextPage endCursor }
          }
          blockedBy(first: 100) {
            nodes { number state }
            pageInfo { hasNextPage endCursor }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
      comments(last: 10) {
        nodes { author { login } body createdAt }
        pageInfo { hasPreviousPage startCursor }
      }
    }
  }
}
""".strip()

CLAIM_COMMENTS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $before: String) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      comments(last: 100, before: $before) {
        nodes { author { login } body }
        pageInfo { hasPreviousPage startCursor }
      }
    }
  }
}
""".strip()

INBOX_SEARCH_LIMIT = 1000

INBOX_REPLIES_QUERY = """
query($searchQuery: String!, $endCursor: String) {
  search(first: 50, after: $endCursor, type: ISSUE, query: $searchQuery) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on Issue {
        number
        title
        url
        comments(last: 50) {
          nodes { author { __typename login } body createdAt }
          pageInfo { hasPreviousPage startCursor }
        }
      }
    }
  }
}
""".strip()

INBOX_ISSUES_QUERY = """
query($searchQuery: String!, $endCursor: String) {
  search(first: 100, after: $endCursor, type: ISSUE, query: $searchQuery) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on Issue {
        number
        title
        url
      }
    }
  }
}
""".strip()

OLDER_COMMENTS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $before: String) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      comments(last: 100, before: $before) {
        nodes { author { __typename login } body createdAt }
        pageInfo { hasPreviousPage startCursor }
      }
    }
  }
}
""".strip()

HELD_CLAIMS_QUERY = """
query($owner: String!, $name: String!, $login: String!) {
  repository(owner: $owner, name: $name) {
    issues(states: OPEN, labels: ["in-progress"], filterBy: {assignee: $login}) {
      totalCount
    }
  }
}
""".strip()

BLOCKING_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      blocking(first: 100, after: $endCursor) {
        nodes { number title state }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
""".strip()

SUB_ISSUES_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      subIssues(first: 100, after: $endCursor) {
        nodes { number title state }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
""".strip()

LABEL_INVENTORY_QUERY = """
query($owner: String!, $name: String!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    labels(first: 100, after: $endCursor) {
      nodes { name color description }
      pageInfo { hasNextPage endCursor }
    }
  }
}
""".strip()

MUTATION_TARGET_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      number
      title
      body
      state
      parent { number }
      labels(first: 100) {
        nodes { name }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
""".strip()

ORGANIZATION_PROJECTS_QUERY = """
query($owner: String!, $endCursor: String) {
  organization(login: $owner) {
    id
    projectsV2(first: 100, after: $endCursor) {
      nodes { id number title url }
      pageInfo { hasNextPage endCursor }
    }
  }
}
""".strip()

PROJECT_STRUCTURE_QUERY = """
query($owner: String!, $number: Int!) {
  organization(login: $owner) {
    projectV2(number: $number) {
      id number title url
      fields(first: 100) {
        nodes {
          __typename
          ... on ProjectV2Field { id name }
          ... on ProjectV2SingleSelectField { id name options { id name color description } }
        }
        pageInfo { hasNextPage endCursor }
      }
      views(first: 100) {
        nodes { id number name layout filter }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
""".strip()

PROJECT_ITEMS_QUERY = """
query($owner: String!, $number: Int!, $endCursor: String) {
  organization(login: $owner) {
    projectV2(number: $number) {
      items(first: 100, after: $endCursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          content {
            __typename
            ... on Issue { id number repository { nameWithOwner } }
          }
          fieldValues(first: 100) {
            nodes {
              __typename
              ... on ProjectV2ItemFieldSingleSelectValue {
                name
                field { ... on ProjectV2SingleSelectField { name } }
              }
            }
            pageInfo { hasNextPage endCursor }
          }
        }
      }
    }
  }
}
""".strip()

AUTHORED_ISSUES_QUERY = """
query($owner: String!, $name: String!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    issues(first: 100, after: $endCursor, states: [OPEN, CLOSED]) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number state
        author { login }
        labels(first: 100) {
          nodes { name }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
  }
}
""".strip()

APPROVED_ISSUES_QUERY = """
query($owner: String!, $name: String!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    issues(first: 100, after: $endCursor, states: [OPEN, CLOSED], labels: ["qew:approved"]) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number state body
        author { login }
        labels(first: 100) {
          nodes { name }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
  }
}
""".strip()

PROJECTION_ISSUES_QUERY = """
query($owner: String!, $name: String!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    issues(first: 100, after: $endCursor, states: [OPEN, CLOSED]) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id number state
        parent { number }
        labels(first: 100) {
          nodes { name }
          pageInfo { hasNextPage endCursor }
        }
        assignees(first: 100) { totalCount nodes { login } }
        blockedBy(first: 100) {
          nodes { number state stateReason }
          pageInfo { hasNextPage endCursor }
        }
        closedByPullRequestsReferences(first: 100) {
          nodes { number state }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
  }
}
""".strip()

PROJECTION_ISSUE_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      id number state
      parent { number }
      labels(first: 100) {
        nodes { name }
        pageInfo { hasNextPage endCursor }
      }
      assignees(first: 100) { totalCount nodes { login } }
      blockedBy(first: 100) {
        nodes { number state stateReason }
        pageInfo { hasNextPage endCursor }
      }
      blocking(first: 100) {
        nodes { number }
        pageInfo { hasNextPage endCursor }
      }
      closedByPullRequestsReferences(first: 100) {
        nodes { number state }
        pageInfo { hasNextPage endCursor }
      }
      projectItems(first: 50, includeArchived: false) {
        nodes {
          id
          project { number }
          fieldValues(first: 100) {
            nodes {
              __typename
              ... on ProjectV2ItemFieldSingleSelectValue {
                name
                field { ... on ProjectV2SingleSelectField { name } }
              }
            }
            pageInfo { hasNextPage endCursor }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
""".strip()

PULL_REQUESTS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      closedByPullRequestsReferences(first: 100, after: $endCursor) {
        nodes { number state }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
""".strip()


def resolve_repository(repository: Optional[str]) -> str:
    if repository is not None:
        _repository_parts(repository)
        return repository
    git_dir = workspace_git_dir()
    output = _run_gh(
        ["repo", "view", "--json", "nameWithOwner"],
        {} if git_dir is None else {"GIT_DIR": str(git_dir)},
    )
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as error:
        raise QewError("indeterminate: gh returned invalid repository JSON", 4) from error
    if not isinstance(payload, dict) or not isinstance(payload.get("nameWithOwner"), str):
        raise QewError("indeterminate: gh returned no repository identity", 4)
    discovered = payload["nameWithOwner"]
    _repository_parts(discovered)
    return discovered


def fetch_ready_graph(repository: str) -> Dict[str, Any]:
    owner, name = _repository_parts(repository)
    pages = _graphql_pages(
        _scoped_query(READY_QUERY),
        ["-F", f"owner={owner}", "-F", f"name={name}"],
    )
    issues = _merge_issue_pages(pages)
    for issue in issues["nodes"]:
        _complete_nested_connection(repository, issue, "labels", LABELS_QUERY)
        _complete_nested_connection(
            repository,
            issue,
            "blockedBy",
            BLOCKERS_QUERY,
        )
    return {"data": {"repository": {"issues": issues}}}


def fetch_prime_graph(repository: str) -> Dict[str, Any]:
    owner, name = _repository_parts(repository)
    pages = _graphql_pages(
        _scoped_query(PRIME_QUERY),
        ["-F", f"owner={owner}", "-F", f"name={name}"],
    )
    issues = _merge_issue_pages(pages)
    for issue in issues["nodes"]:
        _complete_nested_connection(repository, issue, "assignees", ASSIGNEES_QUERY)
        _complete_nested_connection(repository, issue, "labels", LABELS_QUERY)
        _complete_nested_connection(repository, issue, "blockedBy", BLOCKERS_QUERY)
    return {"data": {"repository": {"issues": issues}}}


def fetch_plan_status_graph(repository: str) -> Dict[str, Any]:
    owner, name = _repository_parts(repository)
    pages = _graphql_pages(
        _scoped_query(PLAN_STATUS_QUERY),
        ["-F", f"owner={owner}", "-F", f"name={name}"],
    )
    issues = _merge_issue_pages(pages)
    for plan in issues["nodes"]:
        _complete_nested_connection(repository, plan, "labels", LABELS_QUERY)
        _complete_nested_connection(repository, plan, "subIssues", PLAN_CHILDREN_QUERY)
        for child in plan["subIssues"]["nodes"]:
            _complete_nested_connection(repository, child, "labels", LABELS_QUERY)
            _complete_nested_connection(repository, child, "blockedBy", BLOCKERS_QUERY)
    return {"data": {"repository": {"issues": issues}}}


def fetch_issue(repository: str, issue: int) -> Dict[str, Any]:
    owner, name = _repository_parts(repository)
    payload = _graphql(
        ISSUE_QUERY,
        [
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"number={issue}",
        ],
    )
    issue_data = _issue_from_payload(payload)
    _complete_nested_connection(repository, issue_data, "assignees", ASSIGNEES_QUERY)
    _complete_nested_connection(repository, issue_data, "labels", LABELS_QUERY)
    _complete_nested_connection(repository, issue_data, "blockedBy", BLOCKERS_QUERY)
    return payload


def fetch_pull_request_state(repository: str, number: int) -> Dict[str, Any]:
    """The viewer and a pull request's state, draft flag and author."""
    owner, name = _repository_parts(repository)
    payload = _graphql(
        PULL_REQUEST_STATE_QUERY,
        ["-F", f"owner={owner}", "-F", f"name={name}", "-F", f"number={number}"],
    )
    data = _mapping(payload, "data", "response")
    viewer = _mapping(data, "viewer", "response data").get("login")
    pull = _mapping(_mapping(data, "repository", "response data"), "pullRequest", "repository")
    return {"viewer": viewer, "pull_request": pull}


def fetch_pull_request_for_merge(repository: str, number: int) -> Dict[str, Any]:
    """The viewer and everything a merge decision reads about one pull request."""
    owner, name = _repository_parts(repository)
    payload = _graphql(
        PULL_REQUEST_MERGE_QUERY,
        ["-F", f"owner={owner}", "-F", f"name={name}", "-F", f"number={number}"],
    )
    data = _mapping(payload, "data", "response")
    viewer = _mapping(data, "viewer", "response data").get("login")
    pull = _mapping(_mapping(data, "repository", "response data"), "pullRequest", "repository")
    return {"viewer": viewer, "pull_request": pull}


def fetch_branch_rules(repository: str, branch: str) -> List[Dict[str, Any]]:
    """The active rules (rulesets included) that apply to a branch."""
    owner, name = _repository_parts(repository)
    output = _run_gh(["api", f"repos/{owner}/{name}/rules/branches/{quote(branch, safe='')}"])
    try:
        rules = json.loads(output)
    except json.JSONDecodeError as error:
        raise QewError("indeterminate: invalid branch rules", 4) from error
    if not isinstance(rules, list) or any(not isinstance(rule, dict) for rule in rules):
        raise QewError("indeterminate: invalid branch rules", 4)
    return rules


def merge_pull_request(
    repository: str, number: int, head: str, *, auto: bool, admin: bool
) -> None:
    arguments = ["pr", "merge", str(number), "--repo", repository, "--merge", "--match-head-commit", head]
    if auto:
        arguments.append("--auto")
    if admin:
        arguments.append("--admin")
    _run_gh(arguments)


def edit_pull_request_label(repository: str, number: int, label: str, *, add: bool) -> None:
    flag = "--add-label" if add else "--remove-label"
    _run_gh(["pr", "edit", str(number), "--repo", repository, flag, label])


def edit_pull_request_body(repository: str, number: int, body: str) -> None:
    _run_gh(["pr", "edit", str(number), "--repo", repository, "--body", body])


def mark_pull_request_ready(repository: str, number: int) -> None:
    _run_gh(["pr", "ready", str(number), "--repo", repository])


def fetch_pull_request(repository: str, number: int) -> Dict[str, Any]:
    owner, name = _repository_parts(repository)
    payload = _graphql(
        PULL_REQUEST_QUERY,
        ["-F", f"owner={owner}", "-F", f"name={name}", "-F", f"number={number}"],
    )
    data = _mapping(payload, "data", "response")
    return _mapping(_mapping(data, "repository", "response data"), "pullRequest", "repository")


def fetch_epic_claim(repository: str, number: int) -> Dict[str, Any]:
    """An epic and its direct children with the labels and assignees a claim needs.

    Every connection must be complete: a truncated one would let a claim be guessed.
    """
    owner, name = _repository_parts(repository)
    payload = _graphql(
        EPIC_CLAIM_QUERY,
        ["-F", f"owner={owner}", "-F", f"name={name}", "-F", f"number={number}"],
    )
    epic = _issue_from_payload(payload)
    if epic.get("number") != number:
        raise QewError(f"indeterminate: GitHub returned #{epic.get('number')} for #{number}", 4)
    for issue in (epic, *_nodes(_mapping(epic, "subIssues", f"issue #{number}"), "subIssues")):
        for key in ("labels", "assignees"):
            _page_info_complete(_mapping(issue, key, f"issue #{number}"), key)
    _page_info_complete(_mapping(epic, "subIssues", f"issue #{number}"), "subIssues")
    return epic


def _page_info_complete(connection: Dict[str, Any], context: str) -> None:
    if _mapping(connection, "pageInfo", context).get("hasNextPage") is not False:
        raise QewError(f"indeterminate: incomplete pagination in {context}", 4)


def claim_issue(repository: str, issue: int) -> None:
    _run_gh(
        [
            "issue",
            "edit",
            str(issue),
            "--repo",
            repository,
            "--add-assignee",
            "@me",
            "--add-label",
            "in-progress",
        ]
    )


def create_issue(
    repository: str,
    *,
    title: str,
    issue_type: str,
    priority: str,
    areas: List[str],
    body: str,
    parent: Optional[int] = None,
    blocked_by: Optional[List[int]] = None,
    approved: bool = True,
    needs_approval: bool = False,
    draft: bool = False,
) -> Dict[str, Any]:
    arguments = [
        "issue",
        "create",
        "--repo",
        repository,
        "--title",
        title,
        "--label",
        issue_type,
        "--label",
        priority,
        "--body",
        body,
    ]
    if draft and (approved or needs_approval):
        raise AssertionError("a draft is neither approved nor gated")
    if draft:
        arguments.extend(("--label", "qew:draft"))
    if approved:
        arguments.extend(("--label", "qew:approved"))
    if needs_approval:
        arguments.extend(("--label", APPROVAL_LABEL))
    if load_config().managed_only:
        arguments.extend(("--label", "qew:managed"))
    arguments.extend(("--label", ensure_author_label(repository)))
    for area in areas:
        arguments.extend(("--label", area))
    if parent is not None:
        arguments.extend(("--parent", str(parent)))
    blockers = blocked_by or []
    if blockers:
        arguments.extend(("--blocked-by", ",".join(str(number) for number in blockers)))
    try:
        output = _run_gh(arguments).strip()
    except QewError as error:
        if draft and "qew:draft" in str(error):
            raise QewError(f"{error}; run qew init to create the qew:draft label", error.exit_code) from error
        raise
    match = re.search(r"/issues/(\d+)$", output)
    if match is None:
        raise QewError("indeterminate: GitHub did not return a created issue URL", 4)
    return {"number": int(match.group(1)), "url": output}


@lru_cache(maxsize=None)
def ensure_author_label(repository: str) -> str:
    """Return author:<viewer login>, creating the label in the repository if absent.

    Cached per process: one command that opens many issues resolves it once.
    """
    owner, name = _repository_parts(repository)
    login = fetch_viewer_login()
    label = f"{AUTHOR_LABEL_PREFIX}{login}"
    if len(label) > LABEL_NAME_LIMIT:
        raise QewError(f"indeterminate: author label exceeds {LABEL_NAME_LIMIT} characters", 4)
    payload = _graphql(
        LABEL_LOOKUP_QUERY,
        ["-F", f"owner={owner}", "-F", f"name={name}", "-f", f"label={label}"],
    )
    repository_data = _mapping(_mapping(payload, "data", "response"), "repository", "response data")
    if repository_data.get("label") is None:
        upsert_label(
            repository,
            name=label,
            color=AUTHOR_LABEL_COLOR,
            description=f"Opened through Qew by {login}",
            force=False,
        )
    return label


def create_pr(
    repository: str,
    *,
    branch: str,
    title: str,
    body: str,
    base: Optional[str],
    draft: bool,
    labels: Optional[List[str]] = None,
) -> Dict[str, Any]:
    arguments = [
        "pr",
        "create",
        "--repo",
        repository,
        "--head",
        branch,
        "--title",
        title,
        "--body",
        body,
        "--assignee",
        "@me",
    ]
    if base is not None:
        arguments.extend(("--base", base))
    if draft:
        arguments.append("--draft")
    for label in labels or []:
        arguments.extend(("--label", label))
    output = _run_gh(arguments).strip()
    match = re.search(r"/pull/(\d+)$", output)
    if match is None:
        raise QewError("indeterminate: GitHub did not return a created PR URL", 4)
    return {"number": int(match.group(1)), "url": output}


def set_parent(repository: str, issue: int, parent: int) -> None:
    _run_gh(
        [
            "issue",
            "edit",
            str(issue),
            "--repo",
            repository,
            "--parent",
            str(parent),
        ]
    )


def fetch_issue_body(repository: str, issue: int) -> str:
    output = _run_gh(
        [
            "issue",
            "view",
            str(issue),
            "--repo",
            repository,
            "--json",
            "number,body",
        ]
    )
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as error:
        raise QewError("indeterminate: GitHub returned invalid issue JSON", 4) from error
    if not isinstance(payload, dict) or payload.get("number") != issue:
        raise QewError(f"indeterminate: GitHub returned the wrong issue for #{issue}", 4)
    body = payload.get("body")
    if not isinstance(body, str):
        raise QewError(f"indeterminate: GitHub returned an invalid body for #{issue}", 4)
    return body


MARKER_SEARCH_LIMIT = 100


def find_issues_by_marker(repository: str, marker: str) -> List[Dict[str, Any]]:
    """Return issues whose body contains an exact provenance marker."""
    output = _run_gh(
        [
            "issue",
            "list",
            "--repo",
            repository,
            "--state",
            "all",
            "--search",
            f"{json.dumps(marker)} in:body",
            "--limit",
            str(MARKER_SEARCH_LIMIT),
            "--json",
            "number,title,body,url",
        ]
    )
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as error:
        raise QewError("indeterminate: GitHub returned invalid issue search JSON", 4) from error
    if not isinstance(payload, list) or len(payload) >= MARKER_SEARCH_LIMIT:
        raise QewError("indeterminate: GitHub returned an invalid or truncated issue search", 4)
    matches = []
    for issue in payload:
        if (
            not isinstance(issue, dict)
            or not isinstance(issue.get("number"), int)
            or isinstance(issue.get("number"), bool)
            or not isinstance(issue.get("title"), str)
            or not isinstance(issue.get("body"), str)
            or not isinstance(issue.get("url"), str)
        ):
            raise QewError("indeterminate: GitHub returned an invalid issue in search", 4)
        if marker in issue["body"]:
            matches.append(issue)
    return matches


def fetch_dependency_graph(repository: str) -> Dict[str, Any]:
    owner, name = _repository_parts(repository)
    pages = _graphql_pages(
        _scoped_query(GRAPH_QUERY),
        ["-F", f"owner={owner}", "-F", f"name={name}"],
    )
    issues = _merge_issue_pages(pages)
    for issue in issues["nodes"]:
        _complete_nested_connection(repository, issue, "labels", LABELS_QUERY)
        _complete_nested_connection(
            repository,
            issue,
            "blockedBy",
            BLOCKERS_QUERY,
        )
    return {"data": {"repository": {"issues": issues}}}


def fetch_vetted_issues(repository: str, *, bodies: bool = False) -> Dict[str, Any]:
    query = VETTED_LIST_WITH_BODIES_QUERY if bodies else VETTED_LIST_QUERY
    return _fetch_issue_list(
        repository,
        _scoped_query(query),
        complete_blockers=True,
        complete_assignees=True,
    )


def fetch_draft_issues(repository: str) -> Dict[str, Any]:
    return _fetch_issue_list(repository, _scoped_query(DRAFT_LIST_QUERY), complete_blockers=False)


def fetch_draft_node(repository: str, issue: int) -> Dict[str, Any]:
    """One issue with its complete labels and direct sub-issues (state only)."""
    owner, name = _repository_parts(repository)
    payload = _graphql(
        DRAFT_NODE_QUERY,
        ["-F", f"owner={owner}", "-F", f"name={name}", "-F", f"number={issue}"],
    )
    node = _issue_from_payload(payload)
    if node.get("number") != issue:
        raise QewError(f"indeterminate: GitHub returned #{node.get('number')} for #{issue}", 4)
    _page_info_complete(_mapping(node, "labels", f"issue #{issue}"), "labels")
    return node


def fetch_triage_issues(repository: str) -> Dict[str, Any]:
    return _fetch_issue_list(repository, _scoped_query(TRIAGE_QUERY), complete_blockers=False)


def fetch_blocked_issues(repository: str) -> Dict[str, Any]:
    return _fetch_issue_list(repository, _scoped_query(BLOCKED_QUERY), complete_blockers=True)


def search_vetted_issues(repository: str, terms: str) -> Dict[str, Any]:
    managed = " label:qew:managed" if load_config().managed_only else ""
    query_value = (
        f'repo:{repository} is:issue label:qew:approved{managed} '
        f'{json.dumps(terms)}'
    )
    payload = _graphql(SEARCH_QUERY, ["-F", f"searchQuery={query_value}"])
    search = _mapping(_mapping(payload, "data", "response"), "search", "response data")
    for issue in search["nodes"]:
        _complete_nested_connection(repository, issue, "labels", LABELS_QUERY)
    return payload


def fetch_show_issue(repository: str, issue: int) -> Dict[str, Any]:
    owner, name = _repository_parts(repository)
    payload = _graphql(
        SHOW_QUERY,
        ["-F", f"owner={owner}", "-F", f"name={name}", "-F", f"number={issue}"],
    )
    issue_data = _issue_from_payload(payload)
    _complete_nested_connection(repository, issue_data, "assignees", ASSIGNEES_QUERY)
    _complete_nested_connection(repository, issue_data, "labels", LABELS_QUERY)
    _complete_nested_connection(repository, issue_data, "blockedBy", BLOCKERS_QUERY)
    _complete_nested_connection(repository, issue_data, "blocking", BLOCKING_QUERY)
    _complete_nested_connection(repository, issue_data, "subIssues", SUB_ISSUES_QUERY)
    comments = _mapping(issue_data, "comments", f"issue #{issue}")
    page_info = _mapping(comments, "pageInfo", f"comments for #{issue}")
    has_previous = page_info.get("hasPreviousPage")
    if not isinstance(has_previous, bool):
        raise QewError(f"indeterminate: invalid pagination in comments for #{issue}", 4)
    return payload


def fetch_claim_comments(repository: str, issue: int) -> List[Dict[str, Any]]:
    """Every comment page, newest first, for deriving the current lane claim."""
    owner, name = _repository_parts(repository)
    pages = []
    cursor: Optional[str] = None
    while True:
        fields = ["-F", f"owner={owner}", "-F", f"name={name}", "-F", f"number={issue}"]
        if cursor is not None:
            fields.extend(("-f", f"before={cursor}"))
        page = _graphql(CLAIM_COMMENTS_QUERY, fields)
        pages.append(page)
        comments = _mapping(_issue_from_payload(page), "comments", f"issue #{issue}")
        page_info = _mapping(comments, "pageInfo", f"comments for #{issue}")
        has_previous = page_info.get("hasPreviousPage")
        if not isinstance(has_previous, bool):
            raise QewError(f"indeterminate: invalid pagination in comments for #{issue}", 4)
        if not has_previous:
            return pages
        start = page_info.get("startCursor")
        if not isinstance(start, str) or not start or start == cursor:
            raise QewError(f"indeterminate: invalid comment cursor for #{issue}", 4)
        cursor = start


def fetch_viewer_login() -> str:
    """The login gh is authenticated as."""
    viewer_data = _mapping(_graphql(VIEWER_QUERY, []), "data", "response")
    login = _mapping(viewer_data, "viewer", "response data").get("login")
    if not isinstance(login, str) or not _LOGIN.fullmatch(login):
        raise QewError("indeterminate: invalid viewer login", 4)
    return login


def fetch_inbox_replies(repository: str, login: str, since: datetime) -> List[Dict[str, Any]]:
    """Open issues involving LOGIN updated since SINCE, with every comment back to SINCE.

    Each issue carries its comments oldest first: author (login or None), the
    author's GraphQL type, body and creation time. Search pages are read to the
    end, and an issue's older comment pages are read until one predates SINCE.
    """
    terms = _inbox_terms(repository, f"involves:{_search_login(login)} updated:>={format_instant(since)}")
    issues = []
    for node in _search_nodes(INBOX_REPLIES_QUERY, terms):
        number = _integer(node, "number", "issue")
        context = f"issue #{number}"
        comments = _mapping(node, "comments", context)
        collected = _comment_nodes(comments, context)
        info = _mapping(comments, "pageInfo", f"comments for #{number}")
        while _has_previous(info, number) and _oldest_in_window(collected, since, number):
            cursor = info.get("startCursor")
            if not isinstance(cursor, str) or not cursor:
                raise QewError(f"indeterminate: invalid comment cursor for #{number}", 4)
            owner, name = _repository_parts(repository)
            page = _graphql(
                OLDER_COMMENTS_QUERY,
                ["-F", f"owner={owner}", "-F", f"name={name}", "-F", f"number={number}", "-f", f"before={cursor}"],
            )
            older = _mapping(_issue_from_payload(page), "comments", context)
            info = _mapping(older, "pageInfo", f"comments for #{number}")
            if info.get("startCursor") == cursor:
                raise QewError(f"indeterminate: invalid comment cursor for #{number}", 4)
            collected = _comment_nodes(older, context) + collected
        issues.append(
            {
                "comments": collected,
                "number": number,
                "title": _string(node, "title", context),
                "url": _string(node, "url", context),
            }
        )
    return issues


def fetch_inbox_waiting(repository: str, login: str) -> List[Dict[str, Any]]:
    """Open issues LOGIN opened through Qew that wait for a human's approval."""
    label = f"{AUTHOR_LABEL_PREFIX}{_search_login(login)}"
    return _fetch_inbox_issues(repository, f'label:"{APPROVAL_LABEL}" label:"{label}"')


def fetch_inbox_assigned(repository: str, login: str) -> List[Dict[str, Any]]:
    """Open issues assigned to LOGIN."""
    return _fetch_inbox_issues(repository, f"assignee:{_search_login(login)}")


def _fetch_inbox_issues(repository: str, qualifiers: str) -> List[Dict[str, Any]]:
    issues = []
    for node in _search_nodes(INBOX_ISSUES_QUERY, _inbox_terms(repository, qualifiers)):
        number = _integer(node, "number", "issue")
        issues.append(
            {
                "number": number,
                "title": _string(node, "title", f"issue #{number}"),
                "url": _string(node, "url", f"issue #{number}"),
            }
        )
    return issues


def _search_login(login: str) -> str:
    if not _LOGIN.fullmatch(login):
        raise QewError("--login must be a GitHub login", 2)
    return login


def _inbox_terms(repository: str, qualifiers: str) -> str:
    _repository_parts(repository)
    managed = " label:qew:managed" if load_config().managed_only else ""
    return f"repo:{repository} is:issue is:open{managed} {qualifiers}"


def _search_nodes(query: str, terms: str) -> List[Dict[str, Any]]:
    """Every node of a search, paged to the end; GitHub's 1000-result cap is a failure."""
    nodes: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    while True:
        fields = ["-f", f"searchQuery={terms}"]
        if cursor is not None:
            fields.extend(("-f", f"endCursor={cursor}"))
        payload = _graphql(query, fields)
        search = _mapping(_mapping(payload, "data", "response"), "search", "response data")
        if _integer(search, "issueCount", "search") > INBOX_SEARCH_LIMIT:
            raise QewError(
                f"indeterminate: search matches more than {INBOX_SEARCH_LIMIT} issues; narrow --since",
                4,
            )
        nodes.extend(_nodes(search, "search"))
        info = _page_info(search, "search")
        if not info["hasNextPage"]:
            return nodes
        if info["endCursor"] == cursor:
            raise QewError("indeterminate: search cursor did not advance", 4)
        cursor = info["endCursor"]


def _comment_nodes(comments: Dict[str, Any], context: str) -> List[Dict[str, Any]]:
    rendered = []
    for node in _nodes(comments, f"comments of {context}"):
        author = node.get("author")
        login = None
        typename = None
        if author is not None:
            if not isinstance(author, dict):
                raise QewError(f"indeterminate: invalid comment author in {context}", 4)
            login = _string(author, "login", f"comment author in {context}")
            typename = _string(author, "__typename", f"comment author in {context}")
        rendered.append(
            {
                "author": login,
                "author_type": typename,
                "body": _string(node, "body", f"comment in {context}"),
                "created_at": parse_github_time(node.get("createdAt"), f"comment in {context}"),
            }
        )
    return rendered


def _has_previous(info: Dict[str, Any], number: int) -> bool:
    value = info.get("hasPreviousPage")
    if not isinstance(value, bool):
        raise QewError(f"indeterminate: invalid pagination in comments for #{number}", 4)
    return value


def _oldest_in_window(comments: List[Dict[str, Any]], since: datetime, number: int) -> bool:
    if not comments:
        raise QewError(f"indeterminate: empty comment page before older comments of #{number}", 4)
    return comments[0]["created_at"] >= since


def _string(value: Dict[str, Any], key: str, context: str) -> str:
    child = value.get(key)
    if not isinstance(child, str):
        raise QewError(f"indeterminate: invalid {key} in {context}", 4)
    return child


def count_held_claims(repository: str, login: str) -> int:
    """Open in-progress issues assigned to LOGIN in the repository."""
    owner, name = _repository_parts(repository)
    payload = _graphql(
        HELD_CLAIMS_QUERY,
        ["-F", f"owner={owner}", "-F", f"name={name}", "-F", f"login={login}"],
    )
    data = _mapping(payload, "data", "response")
    issues = _mapping(_mapping(data, "repository", "response data"), "issues", "repository")
    return _integer(issues, "totalCount", "held claims")


def fetch_repository_labels(repository: str) -> List[Dict[str, Any]]:
    owner, name = _repository_parts(repository)
    pages = _graphql_pages(
        LABEL_INVENTORY_QUERY,
        ["-F", f"owner={owner}", "-F", f"name={name}"],
    )
    labels = []
    page_infos = []
    for page in pages:
        data = _mapping(page, "data", "response")
        repository_data = _mapping(data, "repository", "response data")
        connection = _mapping(repository_data, "labels", "repository")
        labels.extend(_nodes(connection, "labels"))
        page_infos.append(_page_info(connection, "labels"))
    _require_complete_page_sequence(page_infos, "labels")
    return labels


def upsert_label(
    repository: str,
    *,
    name: str,
    color: str,
    description: str,
    force: bool = True,
) -> None:
    arguments = [
            "label",
            "create",
            name,
            "--repo",
            repository,
            "--color",
            color,
            "--description",
            description,
        ]
    if force:
        arguments.append("--force")
    _run_gh(arguments)


def fetch_mutation_target(repository: str, issue: int) -> Dict[str, Any]:
    owner, name = _repository_parts(repository)
    payload = _graphql(
        MUTATION_TARGET_QUERY,
        ["-F", f"owner={owner}", "-F", f"name={name}", "-F", f"number={issue}"],
    )
    issue_data = _issue_from_payload(payload)
    _complete_nested_connection(repository, issue_data, "labels", LABELS_QUERY)
    return payload


def fetch_issue_parent(repository: str, issue: int) -> Optional[int]:
    issue_data = _issue_from_payload(fetch_mutation_target(repository, issue))
    parent = issue_data.get("parent")
    if parent is None:
        return None
    if not isinstance(parent, dict):
        raise QewError(f"indeterminate: invalid parent for #{issue}", 4)
    number = parent.get("number")
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        raise QewError(f"indeterminate: invalid parent for #{issue}", 4)
    return number


def edit_issue(repository: str, issue: int, arguments: List[str]) -> None:
    _run_gh(["issue", "edit", str(issue), "--repo", repository, *arguments])


def fetch_milestones(repository: str) -> List[Dict[str, Any]]:
    output = _run_gh(
        [
            "api",
            f"repos/{repository}/milestones?state=all&per_page=100",
            "--paginate",
            "--slurp",
        ]
    )
    try:
        pages = json.loads(output)
    except json.JSONDecodeError as error:
        raise QewError("indeterminate: GitHub returned invalid milestone JSON", 4) from error
    if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
        raise QewError("indeterminate: GitHub returned invalid milestones", 4)
    milestones = []
    for page in pages:
        if any(not isinstance(item, dict) for item in page):
            raise QewError("indeterminate: GitHub returned invalid milestones", 4)
        milestones.extend(page)
    return milestones


def create_milestone(
    repository: str,
    *,
    title: str,
    description: Optional[str],
    due_on: Optional[str],
) -> Dict[str, Any]:
    arguments = ["api", "--method", "POST", f"repos/{repository}/milestones", "-f", f"title={title}"]
    if description is not None:
        arguments.extend(("-f", f"description={description}"))
    if due_on is not None:
        arguments.extend(("-f", f"due_on={due_on}"))
    return _decode_json_object(_run_gh(arguments), "milestone")


def update_milestone(
    repository: str,
    number: int,
    *,
    description: Optional[str],
    due_on: Optional[str],
    state: Optional[str] = None,
) -> Dict[str, Any]:
    arguments = ["api", "--method", "PATCH", f"repos/{repository}/milestones/{number}"]
    if description is not None:
        arguments.extend(("-f", f"description={description}"))
    if due_on is not None:
        arguments.extend(("-f", f"due_on={due_on}"))
    if state is not None:
        arguments.extend(("-f", f"state={state}"))
    return _decode_json_object(_run_gh(arguments), "milestone")


def assign_milestone(repository: str, issue: int, title: str) -> None:
    _run_gh(["issue", "edit", str(issue), "--repo", repository, "--milestone", title])


def fetch_issue_milestone(repository: str, issue: int) -> Dict[str, Any]:
    output = _run_gh(
        ["issue", "view", str(issue), "--repo", repository, "--json", "number,milestone"]
    )
    return _decode_json_object(output, f"milestone for #{issue}")


def fetch_organization_projects(repository: str) -> Dict[str, Any]:
    owner, _ = _repository_parts(repository)
    pages = _graphql_pages(ORGANIZATION_PROJECTS_QUERY, ["-F", f"owner={owner}"])
    projects = []
    page_infos = []
    owner_id = None
    for page in pages:
        data = _mapping(page, "data", "response")
        organization = _mapping(data, "organization", "response data")
        current_owner_id = organization.get("id")
        if not isinstance(current_owner_id, str):
            raise QewError("indeterminate: invalid organization identity", 4)
        if owner_id is not None and owner_id != current_owner_id:
            raise QewError("indeterminate: organization changed during pagination", 4)
        owner_id = current_owner_id
        connection = _mapping(organization, "projectsV2", "organization")
        projects.extend(_nodes(connection, "projects"))
        page_infos.append(_page_info(connection, "projects"))
    _require_complete_page_sequence(page_infos, "projects")
    return {"owner_id": owner_id, "projects": projects}


def create_organization_project(owner_id: str, title: str) -> Dict[str, Any]:
    payload = _graphql(
        """mutation($ownerId: ID!, $title: String!) {
          createProjectV2(input: {ownerId: $ownerId, title: $title}) {
            projectV2 { id number title url }
          }
        }""",
        ["-F", f"ownerId={owner_id}", "-f", f"title={title}"],
    )
    mutation = _mapping(_mapping(payload, "data", "response"), "createProjectV2", "response data")
    return _mapping(mutation, "projectV2", "createProjectV2")


def fetch_project_structure(repository: str, number: int) -> Dict[str, Any]:
    owner, _ = _repository_parts(repository)
    payload = _graphql(
        PROJECT_STRUCTURE_QUERY,
        ["-F", f"owner={owner}", "-F", f"number={number}"],
    )
    organization = _mapping(_mapping(payload, "data", "response"), "organization", "response data")
    project = _mapping(organization, "projectV2", "organization")
    for key in ("fields", "views"):
        connection = _mapping(project, key, "project")
        if _page_info(connection, key)["hasNextPage"]:
            raise QewError(f"indeterminate: incomplete pagination in project {key}", 4)
        _nodes(connection, key)
    return project


def fetch_project_items(repository: str, number: int) -> List[Dict[str, Any]]:
    owner, _ = _repository_parts(repository)
    pages = _graphql_pages(
        PROJECT_ITEMS_QUERY,
        ["-F", f"owner={owner}", "-F", f"number={number}"],
    )
    items = []
    page_infos = []
    for page in pages:
        organization = _mapping(
            _mapping(page, "data", "response"), "organization", "response data"
        )
        project = _mapping(organization, "projectV2", "organization")
        connection = _mapping(project, "items", "project")
        page_items = _nodes(connection, "project items")
        for item in page_items:
            values = _mapping(item, "fieldValues", "project item")
            if _page_info(values, "project item field values")["hasNextPage"]:
                raise QewError("indeterminate: incomplete project item field values", 4)
            _nodes(values, "project item field values")
        items.extend(page_items)
        page_infos.append(_page_info(connection, "project items"))
    _require_complete_page_sequence(page_infos, "project items")
    return items


def fetch_projection_issues(repository: str) -> List[Dict[str, Any]]:
    owner, name = _repository_parts(repository)
    pages = _graphql_pages(
        _scoped_query(PROJECTION_ISSUES_QUERY),
        ["-F", f"owner={owner}", "-F", f"name={name}"],
    )
    issues = _merge_issue_pages(pages)
    for issue in issues["nodes"]:
        _complete_nested_connection(repository, issue, "labels", LABELS_QUERY)
        _complete_nested_connection(repository, issue, "blockedBy", BLOCKERS_QUERY)
        _complete_nested_connection(
            repository,
            issue,
            "closedByPullRequestsReferences",
            PULL_REQUESTS_QUERY,
        )
    nodes = issues["nodes"]
    if load_config().managed_only:
        # An epic is in scope when it parents a managed issue, so its derived claim
        # and card follow the work, without pulling any unadopted epic into scope.
        present = {issue.get("number") for issue in nodes}
        parents = sorted(
            {
                number
                for number in (_parent_number(issue) for issue in nodes)
                if number is not None and number not in present
            }
        )
        for number in parents:
            parent = fetch_projection_issue(repository, number)
            labels = [label.get("name", "") for label in parent["labels"]["nodes"]]
            if normalize_labels(labels).epic:
                nodes.append(parent)
    return nodes


def fetch_approved_issues(repository: str) -> List[Dict[str, Any]]:
    """Every approved issue (open and closed) with its body, opener and complete labels.

    Deliberately not scoped to managed issues: adoption looks for the unmanaged ones.
    """
    owner, name = _repository_parts(repository)
    pages = _graphql_pages(
        APPROVED_ISSUES_QUERY, ["-F", f"owner={owner}", "-F", f"name={name}"]
    )
    issues = _merge_issue_pages(pages)
    for issue in issues["nodes"]:
        _complete_nested_connection(repository, issue, "labels", LABELS_QUERY)
    return issues["nodes"]


def fetch_authored_issues(repository: str) -> List[Dict[str, Any]]:
    """Every issue (open and closed) with its recorded opener and complete labels."""
    owner, name = _repository_parts(repository)
    pages = _graphql_pages(
        _scoped_query(AUTHORED_ISSUES_QUERY),
        ["-F", f"owner={owner}", "-F", f"name={name}"],
    )
    issues = _merge_issue_pages(pages)
    for issue in issues["nodes"]:
        _complete_nested_connection(repository, issue, "labels", LABELS_QUERY)
    return issues["nodes"]


def _parent_number(issue: Dict[str, Any]) -> Optional[int]:
    parent = issue.get("parent")
    number = parent.get("number") if isinstance(parent, dict) else None
    return number if isinstance(number, int) and not isinstance(number, bool) else None


def fetch_projection_issue(repository: str, number: int) -> Dict[str, Any]:
    """Fetch one issue with everything its Project card projection needs."""
    owner, name = _repository_parts(repository)
    payload = _graphql(
        PROJECTION_ISSUE_QUERY,
        ["-F", f"owner={owner}", "-F", f"name={name}", "-F", f"number={number}"],
    )
    issue = _issue_from_payload(payload)
    _complete_nested_connection(repository, issue, "labels", LABELS_QUERY)
    _complete_nested_connection(repository, issue, "blockedBy", BLOCKERS_QUERY)
    _complete_nested_connection(
        repository, issue, "closedByPullRequestsReferences", PULL_REQUESTS_QUERY
    )
    for key in ("blocking", "projectItems"):
        connection = _mapping(issue, key, f"issue #{number}")
        if _page_info(connection, f"{key} for #{number}")["hasNextPage"]:
            raise QewError(f"indeterminate: GitHub truncated {key} for #{number}", 4)
    return issue


def create_project_field(
    project_id: str,
    name: str,
    options: List[Dict[str, str]],
) -> Dict[str, Any]:
    rendered = _render_project_options(options, include_ids=False)
    query = f"""mutation($projectId: ID!, $name: String!) {{
      createProjectV2Field(input: {{
        projectId: $projectId
        dataType: SINGLE_SELECT
        name: $name
        singleSelectOptions: [{rendered}]
      }}) {{
        projectV2Field {{
          ... on ProjectV2SingleSelectField {{ id name options {{ id name }} }}
        }}
      }}
    }}"""
    payload = _graphql(query, ["-F", f"projectId={project_id}", "-f", f"name={name}"])
    mutation = _mapping(
        _mapping(payload, "data", "response"), "createProjectV2Field", "response data"
    )
    return _mapping(mutation, "projectV2Field", "createProjectV2Field")


def update_project_field(field_id: str, options: List[Dict[str, str]]) -> Dict[str, Any]:
    rendered = _render_project_options(options, include_ids=True)
    query = f"""mutation($fieldId: ID!) {{
      updateProjectV2Field(input: {{fieldId: $fieldId, singleSelectOptions: [{rendered}]}}) {{
        projectV2Field {{
          ... on ProjectV2SingleSelectField {{ id name options {{ id name }} }}
        }}
      }}
    }}"""
    payload = _graphql(query, ["-F", f"fieldId={field_id}"])
    mutation = _mapping(
        _mapping(payload, "data", "response"), "updateProjectV2Field", "response data"
    )
    return _mapping(mutation, "projectV2Field", "updateProjectV2Field")


def create_project_view(project_id: str, name: str, layout: str) -> Dict[str, Any]:
    payload = _graphql(
        """mutation($projectId: ID!, $name: String!, $layout: ProjectV2ViewLayout!) {
          createProjectV2View(input: {projectId: $projectId, name: $name, layout: $layout}) {
            projectV2View { id number name layout filter }
          }
        }""",
        ["-F", f"projectId={project_id}", "-f", f"name={name}", "-f", f"layout={layout}"],
    )
    mutation = _mapping(
        _mapping(payload, "data", "response"), "createProjectV2View", "response data"
    )
    return _mapping(mutation, "projectV2View", "createProjectV2View")


def update_project_view(
    view_id: str,
    *,
    name: Optional[str] = None,
    layout: Optional[str] = None,
    filter_value: Optional[str] = None,
) -> Dict[str, Any]:
    assignments = []
    fields = ["-F", f"viewId={view_id}"]
    if name is not None:
        assignments.append("name: $name")
        fields.extend(("-f", f"name={name}"))
    if layout is not None:
        assignments.append("layout: $layout")
        fields.extend(("-f", f"layout={layout}"))
    if filter_value is not None:
        assignments.append("filter: $filter")
        fields.extend(("-f", f"filter={filter_value}"))
    declarations = ["$viewId: ID!"]
    if name is not None:
        declarations.append("$name: String!")
    if layout is not None:
        declarations.append("$layout: ProjectV2ViewLayout!")
    if filter_value is not None:
        declarations.append("$filter: String!")
    query = f"""mutation({', '.join(declarations)}) {{
      updateProjectV2View(input: {{viewId: $viewId, {', '.join(assignments)}}}) {{
        projectV2View {{ id number name layout filter }}
      }}
    }}"""
    payload = _graphql(query, fields)
    mutation = _mapping(
        _mapping(payload, "data", "response"), "updateProjectV2View", "response data"
    )
    return _mapping(mutation, "projectV2View", "updateProjectV2View")


def add_project_item(project_id: str, content_id: str) -> str:
    payload = _graphql(
        """mutation($projectId: ID!, $contentId: ID!) {
          addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
            item { id }
          }
        }""",
        ["-F", f"projectId={project_id}", "-F", f"contentId={content_id}"],
    )
    mutation = _mapping(
        _mapping(payload, "data", "response"), "addProjectV2ItemById", "response data"
    )
    item = _mapping(mutation, "item", "addProjectV2ItemById")
    item_id = item.get("id")
    if not isinstance(item_id, str):
        raise QewError("indeterminate: invalid project item identity", 4)
    return item_id


def delete_project_item(project_id: str, item_id: str) -> None:
    """Delete one card from a Project. The issue it points to is never touched."""
    payload = _graphql(
        """mutation($projectId: ID!, $itemId: ID!) {
          deleteProjectV2Item(input: {projectId: $projectId, itemId: $itemId}) {
            deletedItemId
          }
        }""",
        ["-F", f"projectId={project_id}", "-F", f"itemId={item_id}"],
    )
    mutation = _mapping(_mapping(payload, "data", "response"), "deleteProjectV2Item", "response data")
    if not isinstance(mutation.get("deletedItemId"), str):
        raise QewError("indeterminate: GitHub did not confirm the project item deletion", 4)


def update_project_item_field(
    project_id: str,
    item_id: str,
    field_id: str,
    option_id: str,
) -> None:
    _graphql(
        """mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
          updateProjectV2ItemFieldValue(input: {
            projectId: $projectId
            itemId: $itemId
            fieldId: $fieldId
            value: {singleSelectOptionId: $optionId}
          }) { projectV2Item { id } }
        }""",
        [
            "-F",
            f"projectId={project_id}",
            "-F",
            f"itemId={item_id}",
            "-F",
            f"fieldId={field_id}",
            "-f",
            f"optionId={option_id}",
        ],
    )


def clear_project_item_field(
    project_id: str,
    item_id: str,
    field_id: str,
) -> None:
    _graphql(
        """mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!) {
          clearProjectV2ItemFieldValue(input: {
            projectId: $projectId
            itemId: $itemId
            fieldId: $fieldId
          }) { projectV2Item { id } }
        }""",
        [
            "-F",
            f"projectId={project_id}",
            "-F",
            f"itemId={item_id}",
            "-F",
            f"fieldId={field_id}",
        ],
    )


def _render_project_options(options: List[Dict[str, str]], *, include_ids: bool) -> str:
    rendered = []
    for option in options:
        parts = []
        if include_ids and option.get("id") is not None:
            parts.append(f"id: {json.dumps(option['id'])}")
        parts.extend(
            (
                f"name: {json.dumps(option['name'])}",
                f"color: {option['color']}",
                f"description: {json.dumps(option['description'])}",
            )
        )
        rendered.append("{" + ", ".join(parts) + "}")
    return ", ".join(rendered)


def _decode_json_object(output: str, context: str) -> Dict[str, Any]:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as error:
        raise QewError(f"indeterminate: GitHub returned invalid {context} JSON", 4) from error
    if not isinstance(payload, dict):
        raise QewError(f"indeterminate: GitHub returned invalid {context}", 4)
    return payload


def _fetch_issue_list(
    repository: str,
    query: str,
    *,
    complete_blockers: bool,
    complete_assignees: bool = False,
) -> Dict[str, Any]:
    owner, name = _repository_parts(repository)
    pages = _graphql_pages(query, ["-F", f"owner={owner}", "-F", f"name={name}"])
    issues = _merge_issue_pages(pages)
    for issue in issues["nodes"]:
        _complete_nested_connection(repository, issue, "labels", LABELS_QUERY)
        if complete_blockers:
            _complete_nested_connection(repository, issue, "blockedBy", BLOCKERS_QUERY)
        if complete_assignees:
            _complete_nested_connection(repository, issue, "assignees", ASSIGNEES_QUERY)
    return {"data": {"repository": {"issues": issues}}}


def _scoped_query(query: str) -> str:
    """Restrict repository issue collections at GitHub in overlay mode."""
    if not load_config().managed_only:
        return query
    replacements = (
        ('labels: ["qew:approved"]', 'labels: ["qew:approved", "qew:managed"]'),
        ('labels: ["epic"]', 'labels: ["epic", "qew:managed"]'),
        ('labels: ["qew:draft"]', 'labels: ["qew:draft", "qew:managed"]'),
        (
            "states: [OPEN, CLOSED]) {",
            'states: [OPEN, CLOSED], labels: ["qew:managed"]) {',
        ),
    )
    for source, destination in replacements:
        if source in query:
            return query.replace(source, destination, 1)
    raise QewError("internal error: issue query has no repository scope", 4)


def add_blocked_by(repository: str, issue: int, blocker: int) -> None:
    _run_gh(
        [
            "issue",
            "edit",
            str(issue),
            "--repo",
            repository,
            "--add-blocked-by",
            str(blocker),
        ]
    )


def remove_blocked_by(repository: str, issue: int, blocker: int) -> None:
    _run_gh(
        [
            "issue",
            "edit",
            str(issue),
            "--repo",
            repository,
            "--remove-blocked-by",
            str(blocker),
        ]
    )


def close_issue(
    repository: str,
    issue: int,
    *,
    reason: str,
    comment: str,
    assignees: List[str],
    duplicate_of: Optional[int] = None,
) -> None:
    arguments = [
        "issue",
        "close",
        str(issue),
        "--repo",
        repository,
        "--reason",
        reason,
        "--comment",
        comment,
    ]
    if duplicate_of is not None:
        arguments.extend(("--duplicate-of", str(duplicate_of)))
    _run_gh(arguments)
    release_claim(repository, issue, assignees)


def reopen_issue(
    repository: str,
    issue: int,
    *,
    reason: str,
    assignees: List[str],
) -> None:
    _run_gh(
        [
            "issue",
            "reopen",
            str(issue),
            "--repo",
            repository,
            "--comment",
            reason,
        ]
    )
    release_claim(repository, issue, assignees)


def compare_commit_to_branch(repository: str, commit: str, branch: str) -> Dict[str, Any]:
    """Ask GitHub how COMMIT relates to the remote tip of BRANCH (compare base...head)."""
    owner, name = _repository_parts(repository)
    try:
        output = _run_gh(["api", f"repos/{owner}/{name}/compare/{commit}...{branch}"])
    except QewError as error:
        if "HTTP 404" in str(error):
            raise QewError(
                f"GitHub does not know commit {commit} or branch {branch} in {repository}",
                2,
            ) from error
        raise
    return _decode_json_object(output, f"comparison of {commit} with {branch}")


def fetch_commit_pulls(repository: str, commit: str) -> List[Dict[str, Any]]:
    """Pull requests GitHub associates with COMMIT."""
    owner, name = _repository_parts(repository)
    output = _run_gh(["api", f"repos/{owner}/{name}/commits/{commit}/pulls"])
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as error:
        raise QewError(f"indeterminate: invalid pull requests for commit {commit}", 4) from error
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise QewError(f"indeterminate: invalid pull requests for commit {commit}", 4)
    return payload


def comment_issue(repository: str, issue: int, body: str) -> None:
    _run_gh(
        [
            "issue",
            "comment",
            str(issue),
            "--repo",
            repository,
            "--body",
            body,
        ]
    )


def release_claim(repository: str, issue: int, assignees: List[str]) -> None:
    arguments = ["issue", "edit", str(issue), "--repo", repository]
    if assignees:
        arguments.extend(("--remove-assignee", ",".join(sorted(assignees))))
    arguments.extend(("--remove-label", "in-progress"))
    _run_gh(arguments)


def _graphql(query: str, fields: List[str]) -> Dict[str, Any]:
    output = _run_gh(["api", "graphql", "-f", f"query={query}", *fields])
    return _decode_graphql_page(output)


def _graphql_pages(query: str, fields: List[str]) -> List[Dict[str, Any]]:
    output = _run_gh(
        ["api", "graphql", "--paginate", "--slurp", "-f", f"query={query}", *fields]
    )
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as error:
        raise QewError("indeterminate: GitHub returned invalid JSON", 4) from error
    pages = payload if isinstance(payload, list) else [payload]
    if not pages or any(not isinstance(page, dict) for page in pages):
        raise QewError("indeterminate: GitHub returned an invalid response", 4)
    for page in pages:
        _raise_graphql_errors(page)
    return pages


def _decode_graphql_page(output: str) -> Dict[str, Any]:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as error:
        raise QewError("indeterminate: GitHub returned invalid JSON", 4) from error
    if not isinstance(payload, dict):
        raise QewError("indeterminate: GitHub returned an invalid response", 4)
    _raise_graphql_errors(payload)
    return payload


def _raise_graphql_errors(payload: Dict[str, Any]) -> None:
    errors = payload.get("errors")
    if errors is None:
        return
    if not isinstance(errors, list) or not errors:
        raise QewError("indeterminate: GitHub returned invalid GraphQL errors", 4)
    messages = []
    for error in errors:
        if not isinstance(error, dict) or not isinstance(error.get("message"), str):
            raise QewError("indeterminate: GitHub returned invalid GraphQL errors", 4)
        messages.append(error["message"])
    raise QewError(f"GraphQL: {'; '.join(messages)}", 4)


def _merge_issue_pages(pages: List[Dict[str, Any]]) -> Dict[str, Any]:
    merged = []
    page_infos = []
    for page in pages:
        data = _mapping(page, "data", "response")
        repository = _mapping(data, "repository", "response data")
        issues = _mapping(repository, "issues", "repository")
        merged.extend(_nodes(issues, "issues"))
        page_infos.append(_page_info(issues, "issues"))
    _require_complete_page_sequence(page_infos, "issues")
    return {"nodes": merged, "pageInfo": {"hasNextPage": False, "endCursor": None}}


def _merge_connection_pages(
    pages: List[Dict[str, Any]], key: str
) -> Dict[str, Any]:
    merged = []
    page_infos = []
    for page in pages:
        data = _mapping(page, "data", "response")
        connection = _mapping(data, key, "response data")
        merged.extend(_nodes(connection, key))
        page_infos.append(_page_info(connection, key))
    _require_complete_page_sequence(page_infos, key)
    return {"nodes": merged, "pageInfo": {"hasNextPage": False, "endCursor": None}}


def _complete_nested_connection(
    repository: str,
    issue: Dict[str, Any],
    key: str,
    query: str,
) -> None:
    number = _integer(issue, "number", "issue")
    connection = _mapping(issue, key, f"issue #{number}")
    page_info = _page_info(connection, f"{key} for #{number}")
    if not page_info["hasNextPage"]:
        return
    cursor = page_info["endCursor"]
    owner, name = _repository_parts(repository)
    pages = _graphql_pages(
        query,
        [
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"number={number}",
            "-F",
            f"endCursor={cursor}",
        ],
    )
    nodes = list(_nodes(connection, f"{key} for #{number}"))
    page_infos = []
    for page in pages:
        nested_issue = _issue_from_payload(page)
        nested = _mapping(nested_issue, key, f"issue #{number}")
        nodes.extend(_nodes(nested, f"{key} for #{number}"))
        page_infos.append(_page_info(nested, f"{key} for #{number}"))
    _require_complete_page_sequence(page_infos, f"{key} for #{number}")
    connection["nodes"] = nodes
    connection["pageInfo"] = {"hasNextPage": False, "endCursor": None}


def _issue_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = _mapping(payload, "data", "response")
    repository = _mapping(data, "repository", "response data")
    return _mapping(repository, "issue", "repository")


def _mapping(value: Dict[str, Any], key: str, context: str) -> Dict[str, Any]:
    child = value.get(key)
    if not isinstance(child, dict):
        raise QewError(f"indeterminate: missing {key} in {context}", 4)
    return child


def _nodes(connection: Dict[str, Any], context: str) -> List[Dict[str, Any]]:
    nodes = connection.get("nodes")
    if not isinstance(nodes, list) or any(not isinstance(node, dict) for node in nodes):
        raise QewError(f"indeterminate: invalid nodes in {context}", 4)
    return nodes


def _page_info(connection: Dict[str, Any], context: str) -> Dict[str, Any]:
    page_info = _mapping(connection, "pageInfo", context)
    has_next = page_info.get("hasNextPage")
    if not isinstance(has_next, bool):
        raise QewError(f"indeterminate: invalid pagination in {context}", 4)
    cursor = page_info.get("endCursor")
    if has_next and (not isinstance(cursor, str) or not cursor):
        raise QewError(f"indeterminate: missing cursor in {context}", 4)
    return page_info


def _require_complete_page_sequence(
    page_infos: List[Dict[str, Any]], context: str
) -> None:
    if not page_infos or page_infos[-1]["hasNextPage"]:
        raise QewError(f"indeterminate: incomplete pagination in {context}", 4)
    if any(not page_info["hasNextPage"] for page_info in page_infos[:-1]):
        raise QewError(f"indeterminate: invalid pagination sequence in {context}", 4)


def _integer(value: Dict[str, Any], key: str, context: str) -> int:
    child = value.get(key)
    if not isinstance(child, int) or isinstance(child, bool):
        raise QewError(f"indeterminate: invalid {key} in {context}", 4)
    return child


def _run_gh(arguments: List[str], extra_environment: Optional[Dict[str, str]] = None) -> str:
    environment = os.environ.copy()
    environment.update(extra_environment or {})
    environment["GH_PROMPT_DISABLED"] = "1"
    try:
        result = subprocess.run(
            ["gh", *arguments],
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            env=environment,
            timeout=_gh_timeout(environment),
        )
    except FileNotFoundError as error:
        raise QewError("GitHub CLI (gh) was not found on PATH", 3) from error
    except subprocess.TimeoutExpired as error:
        raise QewError("GitHub CLI (gh) timed out", 3) from error
    except OSError as error:
        raise QewError(f"GitHub CLI (gh) could not be started: {error.strerror}", 3) from error
    if result.returncode != 0:
        detail = result.stderr.strip() or "GitHub CLI request failed"
        raise QewError(detail, 3)
    return result.stdout


def _gh_timeout(environment: Dict[str, str]) -> float:
    raw = environment.get("QEW_GH_TIMEOUT_SECONDS", str(DEFAULT_GH_TIMEOUT_SECONDS))
    try:
        seconds = float(raw)
    except ValueError as error:
        raise QewError("QEW_GH_TIMEOUT_SECONDS must be a positive number", 2) from error
    if not 0 < seconds < float("inf"):
        raise QewError("QEW_GH_TIMEOUT_SECONDS must be a positive number", 2)
    return seconds


def _repository_parts(repository: str) -> List[str]:
    parts = repository.split("/")
    if len(parts) != 2 or not all(_REPOSITORY_PART.fullmatch(part) for part in parts):
        raise QewError("repository must be OWNER/REPO", 2)
    return parts
