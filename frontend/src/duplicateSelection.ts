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
        && (member.review_eligible || member.decision === 'approved_exclude')
      ) {
        selected.add(member.sample_id)
      }
    }
  }

  return selected
}

function findDuplicateRepresentative(
  members: readonly DuplicateSelectionMember[],
): DuplicateSelectionMember | null {
  let representative: DuplicateSelectionMember | null = null

  for (const member of members) {
    if (representative === null || comparesBefore(member, representative)) {
      representative = member
    }
  }

  return representative
}

function comparesBefore(left: DuplicateSelectionMember, right: DuplicateSelectionMember): boolean {
  const leftArea = left.pixel_area ?? -1
  const rightArea = right.pixel_area ?? -1
  if (leftArea !== rightArea) return leftArea > rightArea
  if (left.relative_path !== right.relative_path) return left.relative_path < right.relative_path
  return left.sample_id < right.sample_id
}
