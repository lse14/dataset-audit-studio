export type DuplicateSelectionMember = {
  sample_id: string
  relative_path: string
  pixel_area: number | null
  review_eligible: boolean
  decision: string | null
}

export type DuplicateSelectionGroup = {
  members: readonly DuplicateSelectionMember[]
}

export function selectDuplicateMembersForExclusion(
  groups: readonly DuplicateSelectionGroup[],
): Set<string> {
  const selected = new Set<string>()

  for (const group of groups) {
    const representative = findDuplicateRepresentative(group.members)
    for (const member of group.members) {
      if (
        member.sample_id !== representative?.sample_id
        && member.decision !== 'approved_keep'
        && (member.review_eligible || member.decision === 'approved_exclude')
      ) {
        selected.add(member.sample_id)
      }
    }
  }

  return selected
}

// A member a reviewer explicitly kept should stand in as the representative rather than having the
// largest pending member highlighted next to it. Only keeps inside the current review scope count,
// because an out-of-scope keep is not a candidate for this page's selection anyway.
function findDuplicateRepresentative(
  members: readonly DuplicateSelectionMember[],
): DuplicateSelectionMember | null {
  const kept = members.filter((member) => member.decision === 'approved_keep' && member.review_eligible)
  return bestMember(kept.length > 0 ? kept : members)
}

function bestMember(members: readonly DuplicateSelectionMember[]): DuplicateSelectionMember | null {
  let representative: DuplicateSelectionMember | null = null

  for (const member of members) {
    if (representative === null || comparesBefore(member, representative)) {
      representative = member
    }
  }

  return representative
}

// `toLowerCase` stands in for Python's `casefold` so the frontend highlight matches the backend's
// tie-break on ASCII and CJK paths, where the two agree.
function comparesBefore(left: DuplicateSelectionMember, right: DuplicateSelectionMember): boolean {
  const leftArea = left.pixel_area ?? -1
  const rightArea = right.pixel_area ?? -1
  if (leftArea !== rightArea) return leftArea > rightArea
  const leftFolded = left.relative_path.toLowerCase()
  const rightFolded = right.relative_path.toLowerCase()
  if (leftFolded !== rightFolded) return leftFolded < rightFolded
  if (left.relative_path !== right.relative_path) return left.relative_path < right.relative_path
  return left.sample_id < right.sample_id
}
