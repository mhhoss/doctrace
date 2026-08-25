import type { ExtractedItem } from '../types'

interface RequirementsTableProps {
  items: ExtractedItem[]
  selectedItemId: string | null
  onSelect: (item: ExtractedItem) => void
}

const CATEGORY_LABEL: Record<string, string> = {
  obligation: 'Obligation',
  requirement: 'Requirement',
  deadline: 'Deadline',
  prohibition: 'Prohibition',
  definition: 'Definition',
  other: 'Other',
}

const VERIFICATION_LABEL: Record<string, string> = {
  verified: '✓ Verified',
  failed: '⚠ Unsupported',
  not_run: '— Not checked',
  unverified: '— Not checked',
}

export function RequirementsTable({
  items,
  selectedItemId,
  onSelect,
}: RequirementsTableProps) {
  if (items.length === 0) {
    return (
      <div className="empty-state">
        No requirements extracted — either this document has none, or extraction
        hasn't run yet.
      </div>
    )
  }

  return (
    <table className="requirements-table">
      <thead>
        <tr>
          <th>Category</th>
          <th>Requirement</th>
          <th>Page</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <tr
            key={item.item_id}
            className={item.item_id === selectedItemId ? 'selected' : ''}
            onClick={() => onSelect(item)}
          >
            <td>
              <span className={`category-chip category-${item.category}`}>
                {CATEGORY_LABEL[item.category] ?? item.category}
              </span>
            </td>
            <td className="requirement-text">{item.text}</td>
            <td className="page-cell">
              {item.page_start !== null
                ? item.page_start === item.page_end
                  ? `p. ${item.page_start}`
                  : `p. ${item.page_start}–${item.page_end}`
                : '—'}
            </td>
            <td>
              <span className={`verification-chip verification-${item.verification_status}`}>
                {VERIFICATION_LABEL[item.verification_status] ?? item.verification_status}
              </span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
