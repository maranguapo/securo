import { describe, it, expect } from 'vitest'
import { navItems, visibleNavItems, type NavItem } from './nav-items'
import { MODULE_IDS, type ModuleId } from './modules'

const all = () => true
const none = () => false
const allExcept =
  (...off: ModuleId[]) =>
  (id: ModuleId) =>
    !off.includes(id)

// Mirrors the backend's PersonalPolicy result. Spelled out rather than
// derived, so a change that takes a link away from existing users has
// to be a deliberate edit here.
const PERSONAL_MODULES: ModuleId[] = [
  'transactions',
  'accounts',
  'import',
  'reports',
  'assets',
  'budgets',
  'goals',
  'recurring',
  'categories',
  'payees',
  'split_groups',
  'rules',
]

const linkKeys = (items: NavItem[]) =>
  items.filter((i) => i.type === 'link').map((i) => i.key)

describe('nav catalog', () => {
  it('maps every link to a known module', () => {
    for (const item of navItems) {
      if (item.type !== 'link') continue
      expect(MODULE_IDS).toContain(item.module)
    }
  })

  it('has a link for every module in the catalog', () => {
    const covered = navItems.filter((i) => i.type === 'link').map((i) => i.module)
    expect([...new Set(covered)].sort()).toEqual([...MODULE_IDS].sort())
  })

  it('gives every link a distinct module, except sub-pages gated by a feature', () => {
    // Hiding a module should hide exactly one link. A link that also needs an
    // optional feature (`requires`) is a sub-page of its module's destination,
    // not a module of its own, so it may share the module it belongs to.
    const links = navItems.flatMap((i) => (i.type === 'link' ? [i] : []))
    const owners = links.filter((i) => i.requires === undefined).map((i) => i.module)
    expect(new Set(owners).size).toBe(owners.length)
    for (const sub of links.filter((i) => i.requires !== undefined)) {
      expect(owners).toContain(sub.module)
    }
  })
})

describe('feature-gated links', () => {
  const review = () => navItems.find((i) => i.type === 'link' && i.key === 'categorization')

  it('catalogues the AI categorization review under the categories module', () => {
    expect(review()).toMatchObject({ module: 'categories', requires: 'agents' })
  })

  it('shows it only when its module is on and the agents feature is available', () => {
    const keys = (features?: { agents?: boolean }, hasModule: (id: ModuleId) => boolean = all) =>
      linkKeys(visibleNavItems(navItems, hasModule, features))

    expect(keys({ agents: true })).toContain('categorization')
    expect(keys()).not.toContain('categorization')
    expect(keys({ agents: false })).not.toContain('categorization')
    expect(keys({ agents: true }, allExcept('categories'))).not.toContain('categorization')
  })

  it('sits next to the categories link', () => {
    const keys = linkKeys(visibleNavItems(navItems, all, { agents: true }))
    expect(keys.indexOf('categorization')).toBe(keys.indexOf('categories') + 1)
  })
})

describe('visibleNavItems', () => {
  it('renders a personal workspace exactly as before modules existed', () => {
    const personal = visibleNavItems(navItems, (id) => PERSONAL_MODULES.includes(id))
    expect(linkKeys(personal)).toEqual([
      'transactions',
      'accounts',
      'import',
      'reports',
      'assets',
      'budgets',
      'goals',
      'recurring',
      'categories',
      'payees',
      'splitGroups',
      'rules',
    ])
    // All three section headers survive.
    expect(personal.filter((i) => i.type === 'separator')).toHaveLength(3)
  })

  it('adds invoices for a workspace that has it', () => {
    const business = visibleNavItems(navItems, all)
    expect(linkKeys(business)).toContain('invoices')
    // And it does not disturb the rest of the order.
    expect(linkKeys(business).indexOf('invoices')).toBe(1)
  })

  it('hides a section header once its last link goes', () => {
    const items: NavItem[] = [
      { type: 'separator', labelKey: 'a' },
      { type: 'link', key: 'one', path: '/1', icon: () => null, module: 'reports' },
      { type: 'separator', labelKey: 'b' },
      { type: 'link', key: 'two', path: '/2', icon: () => null, module: 'assets' },
    ]
    const result = visibleNavItems(items, allExcept('reports'))
    expect(result.map((i) => (i.type === 'separator' ? i.labelKey : i.key))).toEqual([
      'b',
      'two',
    ])
  })

  it('keeps a section header while any of its links remain', () => {
    const result = visibleNavItems(navItems, allExcept('transactions', 'invoices'))
    const labels = result.filter((i) => i.type === 'separator').map((i) => i.labelKey)
    expect(labels).toContain('nav.groupAccounts')
    expect(linkKeys(result)).toContain('accounts')
  })

  it('drops every header when no module is on', () => {
    expect(visibleNavItems(navItems, none)).toEqual([])
  })

  it('leaves a trailing header out rather than dangling', () => {
    const items: NavItem[] = [
      { type: 'link', key: 'one', path: '/1', icon: () => null, module: 'reports' },
      { type: 'separator', labelKey: 'trailing' },
      { type: 'link', key: 'two', path: '/2', icon: () => null, module: 'assets' },
    ]
    const result = visibleNavItems(items, allExcept('assets'))
    expect(result.map((i) => (i.type === 'separator' ? i.labelKey : i.key))).toEqual(['one'])
  })
})
